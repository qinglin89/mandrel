"""Shared scaffolding for the evolution controller suites.

One source for the repository, report, and feed builders, so the import tests
and the admission tests cannot drift into describing two different contracts.
Not a test module: pytest collects nothing here.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from ai_native_deployment.evolution import analysis_task
from ai_native_deployment.evolution import assessment
from ai_native_deployment.evolution import experiments
from ai_native_deployment.evolution import feed as feed_module
from ai_native_deployment.evolution import replay

REPO_ROOT = Path(__file__).resolve().parents[1]

# The source line a candidate is promoted onto: a ref of its own rather than
# whichever branch a checkout is on, because that is what the merge input is a
# property of.
RELEASE_REF = "refs/heads/release"
PROMOTION_REASON = "replay showed fewer remediation rounds with no regression"
PROMOTION_EXPECTATION = "fewer remediation rounds, with quality and elapsed time unchanged"

ARTIFACT_BODIES = {
    "evidence": b'{"layer": "L1", "events": []}',
    "static_metrics": b'{"layer": "L1", "rounds": 2}',
    "semantic_report": b'{"layer": "L2", "findings": []}',
    "report_markdown": b"# Report\n\nNo findings.\n",
}


def make_repo(tmp_path: Path) -> Path:
    """A repository carrying the real versioned evolution contract files.

    The schemas and config are copied rather than re-invented: a test that
    validated against a hand-written schema would prove nothing about the
    contract this repository actually ships.
    """

    root = tmp_path / "repo"
    (root / "evolution").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "evolution" / "config.toml", root / "evolution" / "config.toml")
    shutil.copytree(REPO_ROOT / "evolution" / "schemas", root / "evolution" / "schemas")
    (root / "evolution" / "ledger.jsonl").write_text("", encoding="utf-8")
    return root


def make_record(
    *,
    key: str,
    sequence: int,
    repo_id: str = "repo-alpha",
    task_id: str = "2026-07-01-task",
    evaluation_id: str | None = None,
    bodies: dict[str, bytes] | None = None,
    generated_at: str = "2026-07-30T10:00:00Z",
    runner_protocol_revision: str = "v2.2.0",
    rubric_revision: str = "r7",
    dev_model: str = "claude-opus-5",
    effective_revision: str | None = "e1",
) -> dict:
    """One feed record. `effective_revision` is what the target that produced it
    actually held — the reading a release assessment places a report by, and null
    for a report whose provenance never said."""

    bodies = bodies or ARTIFACT_BODIES
    return {
        "schema_version": 1,
        "report_key": key,
        "sequence": sequence,
        "generated_at": generated_at,
        "source": {
            "repo_id": repo_id,
            "repo_name": repo_id.replace("-", " "),
            "task_id": task_id,
            "evaluation_id": evaluation_id or f"eval-{key}",
            "archived": True,
            "completed": True,
        },
        "evaluator": {
            "backend": "claude",
            "model": "claude-opus-5",
            "schema_version": 1,
            "rubric_revision": rubric_revision,
        },
        "artifacts": {
            name: {
                "sha256": hashlib.sha256(body).hexdigest(),
                "size_bytes": len(body),
                "media_type": "application/json" if name != "report_markdown" else "text/markdown",
            }
            for name, body in bodies.items()
        },
        "provenance": {
            "runner_protocol_revision": runner_protocol_revision,
            "deploy_lock_hash": "a" * 64,
            "config_revision": "c1",
            "effective_revision": effective_revision,
            "dev": {"agent": "claude", "model": dev_model, "effort": "high", "profile": "dev"},
            "review": {"agent": "codex", "model": "gpt-x", "effort": "high", "profile": "review"},
        },
    }


def make_manifest_report(
    *,
    key: str,
    sequence: int = 1,
    task_id: str = "2026-07-01-task",
    repo_id: str = "repo-alpha",
    version: int = 2,
    **record_overrides,
) -> dict:
    """One membership entry, in the shape the given manifest version requires.

    `record_overrides` reach `make_record`, which owns the evaluator and
    provenance a version-2 entry restates: what a release assessment reads off a
    frozen manifest is exactly this half of the imported report.
    """

    item = {
        "report_key": key,
        "sequence": sequence,
        "repo_id": repo_id,
        "task_id": task_id,
        "evaluation_id": f"eval-{key}",
        "bundle_sha256": "b" * 64,
    }
    if version == 1:
        return item
    record = make_record(key=key, sequence=sequence, task_id=task_id, repo_id=repo_id, **record_overrides)
    return {
        **item,
        "generated_at": record["generated_at"],
        "evaluator": record["evaluator"],
        "provenance": record["provenance"],
    }


def write_manifest(
    batches_root: Path,
    batch_id: str,
    report_keys: list[str],
    *,
    version: int = 2,
    **overrides,
) -> Path:
    """A schema-valid manifest on disk, for tests that need a frozen batch
    without freezing one — an older manifest version, or a batch whose reports
    were never imported here."""

    manifest = {
        "schema_version": version,
        "batch_id": batch_id,
        "created_at": "2026-07-31T00:00:00Z",
        "config_sha256": "c" * 64,
        "forced": False,
        "reports": [
            make_manifest_report(key=key, sequence=index + 1, task_id=f"2026-07-{index + 1:02d}-task", version=version)
            for index, key in enumerate(report_keys)
        ],
        **overrides,
    }
    directory = batches_root / batch_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return directory


def write_closure(
    batches_root: Path,
    batch_id: str,
    *,
    analysis_task_id: str,
    closed_at: str = "2026-07-31T12:00:00Z",
) -> Path:
    """The closure record the controller publishes from a completed analysis
    task, for tests that need a batch already analyzed elsewhere — the ordinary
    case on any machine that did not run the analysis."""

    path = batches_root / batch_id / "analysis-complete.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "batch_id": batch_id,
                "analysis_task_id": analysis_task_id,
                "closed_at": closed_at,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def write_outcome(
    batches_root: Path,
    batch_id: str,
    *,
    outcome: str = "no-change",
    reason: str = "no cluster reached the minimum unique-task count",
    experiment_id: str | None = None,
    promotion_revision: str | None = None,
    promotion: dict | None = None,
    decided_at: str = "2026-08-09T09:00:00Z",
) -> Path:
    """The terminal record that ends a batch, for tests that need a batch whose
    change cycle is over.

    A `promoted` outcome carries the merge unit as well as the revision, so one
    is supplied by default: a record stating the revision alone is refused as it
    is read, which is a rule of its own rather than something every caller
    wanting a concluded batch should have to restate.
    """

    if outcome == "promoted" and promotion is None:
        promotion = promotion_of()
    return _write_json(
        batches_root / batch_id / "outcome.json",
        {
            "schema_version": 1,
            "batch_id": batch_id,
            "outcome": outcome,
            "decided_at": decided_at,
            "reason": reason,
            "experiment_id": experiment_id,
            "promotion_revision": promotion_revision,
            "promotion": promotion,
        },
    )


def promotion_of(**overrides: Any) -> dict:
    """The merge unit a promoted outcome states: the round and candidate that
    were measured, the source line it went onto, and the tree that produced."""

    merge: dict[str, Any] = {
        "round": 1,
        "candidate_revision": "c" * 40,
        "merge_input_revision": "e" * 40,
        "merge_input_ref": "refs/heads/release",
        "tree": "7" * 40,
        "planned_targets": [],
    }
    merge.update(overrides)
    return merge


def prepared_promotion(**overrides: Any) -> dict:
    """The merge unit an experiment records its promotion as — the same one the
    outcome states, plus the commit that carries it and what it was prepared
    under. Written before the source line moves, which is what lets an
    interrupted run find its own work again."""

    promotion: dict[str, Any] = {
        "round": 1,
        "candidate_revision": "c" * 40,
        "merge_input_revision": "e" * 40,
        "merge_input_ref": "refs/heads/release",
        "tree": "7" * 40,
        "revision": "f" * 40,
        "reason": "replay showed fewer remediation rounds with no regression",
        "planned_targets": [],
        "prepared_at": "2026-08-05T09:00:00Z",
    }
    promotion.update(overrides)
    return promotion


def write_rollback(
    batches_root: Path,
    batch_id: str,
    *,
    experiment_id: str,
    promotion_revision: str,
    source_ref: str = "refs/heads/release",
    reverted_from: str = "e" * 40,
    revision: str = "a" * 40,
    tree: str = "8" * 40,
    reason: str = "the next cohort showed no improvement and two new regressions",
    prepared_at: str = "2026-08-10T09:00:00Z",
    reverted_at: str | None = "2026-08-10T09:00:01Z",
) -> Path:
    """The record taking a batch's promotion back off the source line.

    `reverted_at` of None is the operation in flight: the inverse commit exists
    and the line has not been recorded as carrying it.
    """

    return _write_json(
        batches_root / batch_id / "rollback.json",
        {
            "schema_version": 1,
            "batch_id": batch_id,
            "experiment_id": experiment_id,
            "promotion_revision": promotion_revision,
            "source_ref": source_ref,
            "reverted_from": reverted_from,
            "revision": revision,
            "tree": tree,
            "reason": reason,
            "prepared_at": prepared_at,
            "reverted_at": reverted_at,
        },
    )


def write_rejected_drafts(batches_root: Path, batch_id: str, rejected: list[dict]) -> Path:
    """The drafts a human declined at this batch's admission gate."""

    return _write_json(
        batches_root / batch_id / "rejected-drafts.json",
        {"schema_version": 1, "batch_id": batch_id, "rejected": rejected},
    )


def rejection(draft_id: str, *, reason: str = "one report is not recurrence", sha: str | None = None) -> dict:
    return {
        "draft_id": draft_id,
        "draft_sha256": sha or draft_sha256(draft_id),
        "rejected_at": "2026-08-02T09:00:00Z",
        "reason": reason,
    }


def complete_task(config, task_id: str) -> Path:
    """Finish a task on this machine the way close-out does: the terminal status,
    the file moved to the archive, and its row dropped from the active index.

    Shared, because two things the controller does read exactly this — the
    closure record published from a completed analysis task, and the completion
    observation that seals a round — and a suite with its own idea of "finished"
    would be testing against a lifecycle nothing else believes in.
    """

    source = analysis_task.task_path(config, task_id)
    text = source.read_text(encoding="utf-8")
    for status in ("pending", "in_progress", "final_review"):
        if f"status: {status}" in text:
            text = text.replace(f"status: {status}", "status: completed", 1)
            break
    source.write_text(text, encoding="utf-8")
    target = analysis_task.archived_task_path(config, task_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    source.rename(target)
    index = analysis_task.index_path(config)
    if index.is_file():
        kept = [line for line in index.read_text(encoding="utf-8").splitlines() if f"| {task_id} " not in line]
        index.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return target


def settle_release(config, *, at: datetime, settlement: str = assessment.SETTLEMENT_RETAIN) -> bool:
    """Answer the release gate a cohort frozen after a promotion owes.

    Every base freeze after the first promotion waits on this (invariant 17), so
    a fixture that wants a second change cycle has to go through it exactly as an
    operator does: the cohort reads the release, and a human says whether the
    line keeps it. `inconclusive` and `retain` because that is the ordinary
    answer — no manifest states the shape of the work, so the cohorts suspect
    rather than settle, and the release stays the commit the next base is frozen
    on.

    A no-op where nothing is owed, which is every first batch and every cohort
    whose predecessor changed nothing. Returns whether it answered anything.
    """

    frame = assessment.describe_current(config)
    if frame is None or not frame.owed:
        return False
    if assessment.read(config, frame.batch, frame=frame) is None:
        assessment.form(
            config,
            verdict=assessment.VERDICT_INCONCLUSIVE,
            confidence=assessment.CONFIDENCE_LOW,
            rationale="no frozen manifest states what kind of work either cohort did",
            now=at,
        )
    assessment.settle(
        config,
        settlement=settlement,
        reason="the reading found nothing measured against the release",
        now=at,
    )
    return True


def promote_candidate(
    config,
    *,
    batch_id: str,
    drafts: tuple[str, ...] = ("loader-fallback", "hook-side-loader"),
    source_ref: str = RELEASE_REF,
    reason: str = PROMOTION_REASON,
    targets: tuple[str, ...] = ("orch-hub",),
    base: str | None = None,
    reports: list[dict] | None = None,
    at: datetime,
) -> Any:
    """A whole change cycle, from a frozen cohort to a commit on the source line.

    Every step is the real operation — the freeze, the admission, the completion
    observation, the seal, a run measured by the shared harness double, and the
    promotion — because what needs a promotion here needs the commit, the tree
    and the records that a promotion actually produces. A fixture writing an
    `outcome.json` and a merge by hand would leave a rollback proved only against
    itself.

    Shared for the reason the harness double is: the promotion suite tests each
    of these steps and the rollback suite needs the state after all of them, and
    a second way of reaching it would be a second account of what a promotion
    leaves behind.

    `reports` replaces the frozen membership when a caller needs particular
    provenance in it — the release-assessment suite needs the cohort this batch
    was analyzed from to state the revision its targets actually held.

    A cycle following an earlier promotion answers that release's gate first
    (`settle_release`), because no base is frozen until somebody does — and it
    starts from the line that release is on, because that is what the answer
    selects (invariant 17). So this leaves the checkout on the promotion it made:
    an operator's next cycle is worked on a line carrying the release, and a
    fixture whose `HEAD` stayed behind the promotion would freeze its next base
    off the line the gate just decided on.
    """

    directory = write_manifest(
        config.batches_root,
        batch_id,
        ["r1", "r2"],
        analysis_task_id=f"2026-07-31-{batch_id}",
        **({} if reports is None else {"reports": reports}),
    )
    (directory / "findings.md").write_text("# Findings\n", encoding="utf-8")
    write_closure(config.batches_root, batch_id, analysis_task_id=f"2026-07-31-{batch_id}")
    for draft_id in drafts:
        write_draft(config.batches_root, batch_id, draft_id)

    settle_release(config, at=at)
    admission = experiments.create(config, list(drafts), base=base, now=at)
    for item in admission.admitted:
        complete_task(config, item.task_id)
    git_update_ref(config.repo_root, admission.ref, git_commit(config.repo_root, f"{batch_id} candidate work"))
    experiments.seal_round(config, now=at)

    harness = FakeHarness(report=completed_report())
    replay.start(config, harness, source_ref=source_ref, expectation=PROMOTION_EXPECTATION, now=at)
    replay.conclude(config, harness, now=at)
    promotion = experiments.promote(config, reason=reason, targets=targets, now=at)
    git_follow(config.repo_root, promotion.promotion_revision)
    return promotion


def write_draft(batches_root: Path, batch_id: str, draft_id: str, body: str | None = None) -> Path:
    """One change-task draft waiting in a batch's `proposed-tasks/` directory."""

    path = batches_root / batch_id / "proposed-tasks" / f"{draft_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if body is not None else _draft_body(draft_id), encoding="utf-8")
    return path


def draft_sha256(draft_id: str) -> str:
    return hashlib.sha256(_draft_body(draft_id).encode("utf-8")).hexdigest()


def admitted_task(draft_id: str, *, task_id: str | None = None, complete: bool = True) -> dict:
    return {
        "task_id": task_id or f"2026-08-01-{draft_id}",
        "draft_id": draft_id,
        "draft_sha256": draft_sha256(draft_id),
        "admitted_at": "2026-08-01T09:00:00Z",
        "completion_observed_at": "2026-08-03T08:00:00Z" if complete else None,
    }


def experiment_round(
    number: int,
    *,
    tasks: list[dict] | None = None,
    candidate_revision: str | None = None,
    reason: str = "grouped admission of the loader dispositions",
    opened_at: str = "2026-08-01T09:00:00Z",
    sealed_at: str = "2026-08-03T09:00:00Z",
) -> dict:
    """One round. `candidate_revision` seals it — which is what makes it
    candidate-ready, and the only state anything may measure."""

    return {
        "round": number,
        "opened_at": opened_at,
        "reason": reason,
        "tasks": tasks if tasks is not None else [admitted_task("loader-fallback")],
        "seal": None
        if candidate_revision is None
        else {"sealed_at": sealed_at, "candidate_revision": candidate_revision},
    }


def experiment_decision(
    outcome: str,
    *,
    reason: str = "the approach needs a loader change this batch cannot justify",
    superseded_by: str | None = None,
    promotion_revision: str | None = None,
    decided_at: str = "2026-08-05T09:00:00Z",
) -> dict:
    return {
        "outcome": outcome,
        "decided_at": decided_at,
        "reason": reason,
        "superseded_by": superseded_by,
        "promotion_revision": promotion_revision,
    }


def write_experiment(
    experiments_root: Path,
    experiment_id: str,
    *,
    batch_id: str | None = None,
    base_revision: str = "a" * 40,
    base_release_ref: str | None = "v2.2.0",
    rounds: list[dict] | None = None,
    decision: dict | None = None,
    created_at: str = "2026-08-01T09:00:00Z",
    ref: str | None = None,
    version: int = 2,
    **overrides,
) -> Path:
    """One experiment record on disk, in its own directory.

    `batch_id` defaults to the batch half of the id, so the ordinary case states
    the identity once and the mismatch cases state it twice on purpose.

    A `promoted` decision brings the rest of a promotion with it — the merge unit
    on the record, and the completed run that measured it beside the record —
    because those three are one history and a fixture stating only the decision
    describes a promotion nothing justified. Pass `promotion` or write a
    `replays.json` first to state any of them differently on purpose.

    `version=1` writes the shape this repository shipped before a promotion
    recorded its merge unit: the field does not exist there, so a version-1
    `promoted` decision states the revision alone and brings nothing with it.
    That is what a current build meets in a repository promoted under the
    previous one. Every later version states the field, null included — a record
    with no field at all is the version-1 shape, and writing one here would be
    writing a record no current build accepts.
    """

    record = {
        "schema_version": version,
        "experiment_id": experiment_id,
        "batch_id": batch_id if batch_id is not None else experiment_id.split("-exp-")[0],
        "created_at": created_at,
        "base_revision": base_revision,
        "base_release_ref": base_release_ref,
        "ref": ref if ref is not None else f"refs/evolution/experiments/{experiment_id}",
        "rounds": rounds if rounds is not None else [experiment_round(1)],
        "decision": decision,
        **({"promotion": None} if version > 1 else {}),
        **overrides,
    }
    if version > 1 and decision is not None and decision.get("outcome") == "promoted" and "promotion" not in overrides:
        record["promotion"] = _promotion_for(record, decision)
    _write_json(experiments_root / experiment_id / "experiment.json", record)
    merge = record.get("promotion")
    if merge and not (experiments_root / experiment_id / "replays.json").is_file():
        write_replays(
            experiments_root,
            experiment_id,
            [
                replay_entry(
                    round_number=merge["round"],
                    integration=replay_integration(
                        base_revision=base_revision,
                        candidate_revision=merge["candidate_revision"],
                        merge_input_revision=merge["merge_input_revision"],
                        merge_input_ref=merge["merge_input_ref"],
                        tree=merge["tree"],
                    ),
                )
            ],
        )
    return experiments_root / experiment_id


def _promotion_for(record: dict, decision: dict) -> dict:
    """The merge unit a hand-written `promoted` decision implies, taken from the
    round it was decided over so the record agrees with itself."""

    last = record["rounds"][-1]
    seal = last.get("seal") or {}
    return prepared_promotion(
        round=last["round"],
        candidate_revision=seal.get("candidate_revision", "c" * 40),
        revision=decision.get("promotion_revision") or "f" * 40,
        reason=decision["reason"],
    )


def measurement(
    metric: str = "remediation-rounds",
    *,
    unit: str = "rounds",
    baseline: float | None = 2.4,
    candidate: float = 1.6,
    better: str = "lower",
) -> dict:
    """One quantity on the base and on the candidate."""

    return {"metric": metric, "unit": unit, "baseline": baseline, "candidate": candidate, "better": better}


def replay_integration(
    *,
    base_revision: str = "a" * 40,
    candidate_revision: str = "b" * 40,
    merge_input_revision: str = "e" * 40,
    merge_input_ref: str | None = "refs/heads/release",
    tree: str = "f" * 40,
) -> dict:
    """The pinned tree one replay exercised: the round's candidate, the source
    line it was integrated onto, and what the two produced."""

    return {
        "base_revision": base_revision,
        "candidate_revision": candidate_revision,
        "merge_input_revision": merge_input_revision,
        "merge_input_ref": merge_input_ref,
        "tree": tree,
    }


def replay_result(
    outcome: str = "completed",
    *,
    detail: str = "convergence improved; no regression outside the excluded cases",
    concluded_at: str = "2026-08-04T11:00:00Z",
    elapsed_seconds: float | None = 1820,
    metrics: list[dict] | None = None,
    regressions: list[dict] | None = None,
    ambiguity: str | None = None,
) -> dict:
    return {
        "outcome": outcome,
        "concluded_at": concluded_at,
        "detail": detail,
        "elapsed_seconds": elapsed_seconds,
        "metrics": metrics if metrics is not None else [measurement()],
        "regressions": regressions if regressions is not None else [],
        "ambiguity": ambiguity,
    }


def replay_entry(
    round_number: int = 1,
    attempt: int = 1,
    *,
    started_at: str = "2026-08-04T09:00:00Z",
    integration: dict | None = None,
    cases: dict | None = None,
    evaluator: dict | None = None,
    harness: dict | None = None,
    expectation: str = "fewer remediation rounds, with quality and elapsed time unchanged",
    result: dict | None = None,
    running: bool = False,
) -> dict:
    """One recorded replay run. `running` is the run with no result yet, which is
    a state of its own rather than a missing field: the record is what a later
    process polls."""

    return {
        "round": round_number,
        "attempt": attempt,
        "started_at": started_at,
        "integration": integration if integration is not None else replay_integration(),
        "cases": cases
        if cases is not None
        else {
            "case_set_id": "loader-regressions",
            "case_set_sha256": "c" * 64,
            "count": 12,
            "excluded": [],
        },
        "evaluator": evaluator
        if evaluator is not None
        else {"backend": "claude", "model": "claude-opus-5", "rubric_revision": "r7"},
        # The default handle is derived from the position rather than shared: one
        # handle names one run, so two attempts under one name are a collision
        # the reader refuses, not a fixture detail.
        "harness": harness
        if harness is not None
        else {
            "id": "local-replay",
            "revision": "0.1.0",
            "config_sha256": "d" * 64,
            "handle": f"run-{round_number:02d}{attempt:02d}",
        },
        "expectation": expectation,
        "result": None if running else (result if result is not None else replay_result()),
    }


class FakeHarness:
    """A replay harness that answers, and remembers what it was asked.

    The controller may assume exactly two things about the real one — that it
    starts a run and names it, and that polling that name eventually reports — so
    this implements those two and records the requests, which is what a test
    checks the controller pinned.

    Shared because two suites need a run to exist for different reasons: the
    replay suite is about what the operations write, and the promotion tests need
    evidence that was actually measured rather than hand-written. A second copy
    would let the boundary drift into two harness contracts.
    """

    def __init__(self, *, report: Any = None, plan: Any = None) -> None:
        self.requests: list[Any] = []
        self.polled: list[str] = []
        self.report = report
        self.plan = plan

    def start(self, request: Any) -> Any:
        self.requests.append(request)
        if self.plan is not None:
            return self.plan
        return replay.ReplayPlan(
            cases=replay.CaseSet(
                case_set_id="loader-regressions",
                case_set_sha256="c" * 64,
                count=12,
                excluded=(replay.Exclusion(case_id="case-9", reason="needs a credentialed backend"),),
            ),
            evaluator=replay.Evaluator(backend="claude", model="claude-opus-5", rubric_revision="r7"),
            harness=replay.Harness(
                id="local-replay",
                revision="0.1.0",
                config_sha256="d" * 64,
                handle=f"run-{request.round_number:02d}{request.attempt:02d}",
            ),
        )

    def poll(self, handle: str) -> Any:
        self.polled.append(handle)
        return self.report


def completed_report(**overrides: Any) -> Any:
    """What a harness reports for a run that finished and measured something."""

    fields: dict[str, Any] = {
        "outcome": replay.RESULT_COMPLETED,
        "detail": "convergence improved; no regression outside the excluded case",
        "elapsed_seconds": 1820.5,
        "metrics": (
            replay.Measurement(metric="remediation-rounds", unit="rounds", baseline=2.4, candidate=1.6, better="lower"),
        ),
        "regressions": (),
        "ambiguity": None,
    }
    fields.update(overrides)
    return replay.ReplayReport(**fields)


def write_replays(
    experiments_root: Path,
    experiment_id: str,
    replays: list[dict],
    *,
    declares: str | None = None,
    **overrides,
) -> Path:
    """One experiment's replay evidence on disk, beside its record.

    `declares` is the id the record itself states, defaulting to the directory it
    sits in — so the ordinary case names the experiment once and the mismatch
    case names it twice on purpose.
    """

    record = {
        "schema_version": 1,
        "experiment_id": declares if declares is not None else experiment_id,
        "pending": None,
        "withdrawn": [],
        "replays": replays,
        **overrides,
    }
    return _write_json(experiments_root / experiment_id / "replays.json", record)


def replay_pending(
    round_number: int = 1,
    attempt: int = 1,
    *,
    requested_at: str = "2026-08-04T09:00:00Z",
    integration: dict | None = None,
    expectation: str = "fewer remediation rounds, with quality and elapsed time unchanged",
) -> dict:
    """A request outstanding on a replay record: the run this controller
    committed to before the harness answered for it."""

    return {
        "round": round_number,
        "attempt": attempt,
        "requested_at": requested_at,
        "integration": integration if integration is not None else replay_integration(),
        "expectation": expectation,
    }


def replay_withdrawal(
    round_number: int = 1,
    attempt: int = 1,
    *,
    requested_at: str = "2026-08-04T09:00:00Z",
    withdrawn_at: str = "2026-08-04T09:30:00Z",
    integration: dict | None = None,
) -> dict:
    """A position a request gave up without becoming a run. It keeps the position
    allocated: the harness is keyed on it, and a run may have been started under
    it that nothing here will ever name."""

    return {
        "round": round_number,
        "attempt": attempt,
        "requested_at": requested_at,
        "withdrawn_at": withdrawn_at,
        "integration": integration if integration is not None else replay_integration(),
    }


def _draft_body(draft_id: str) -> str:
    """The default draft, kept as its own name because `draft_sha256` hashes it:
    what a fixture writes and what a record says was admitted are one string."""

    return draft_body(draft_id)


DRAFT_SECTIONS = ("Goal", "Scope", "Acceptance", "Session log")

_SECTION_TEXT = {
    "Goal": "Implement the {draft_id} disposition of this batch.",
    "Scope": "The canonical files that disposition names, and nothing else.",
    "Acceptance": "The disposition is implemented and the suite passes.",
}


def draft_body(
    draft_id: str,
    *,
    task_id: str | None = None,
    frontmatter: str | None = None,
    sections: tuple[str, ...] = DRAFT_SECTIONS,
    section_text: dict[str, str] | None = None,
    closed: bool = True,
) -> str:
    """A draft as the analysis session writes it — a schema-conforming pending
    task file — or one field, section, delimiter, or section-body short of it,
    which is what the admission gate decides about.

    `sections` may repeat a name, and `section_text` may put content under one
    that is empty by default: two ways of writing a body that has a heading
    without having the section it names once and inert.
    """

    head = frontmatter
    if head is None:
        head = (
            f"id: {task_id or f'2026-08-01-{draft_id}'}\n"
            "status: pending\n"
            "session-est: 0/1\n"
            "blockers: []\n"
            "claimed-by:\n"
        )
    closer = "---\n" if closed else ""
    text_of = {**_SECTION_TEXT, **(section_text or {})}
    body = ""
    for name in sections:
        body += f"## {name}\n\n"
        text = text_of.get(name)
        if text:
            body += text.format(draft_id=draft_id) + "\n\n"
    return f"---\n{head}{closer}\n# Change {draft_id}\n\n{body}".rstrip("\n") + "\n"


def _write_json(path: Path, record: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_feed(root: Path, records: list[dict], *, bodies: dict[str, bytes] | None = None) -> feed_module.DirectoryFeed:
    (root / feed_module.REPORTS_DIRNAME).mkdir(parents=True, exist_ok=True)
    for record in records:
        key = record.get("report_key") or f"unkeyed-{record.get('sequence')}"
        (root / feed_module.REPORTS_DIRNAME / f"{key}.json").write_text(json.dumps(record), encoding="utf-8")
        artifacts = record.get("artifacts")
        if not isinstance(artifacts, dict):
            continue
        directory = root / feed_module.ARTIFACTS_DIRNAME / key
        directory.mkdir(parents=True, exist_ok=True)
        for name, body in (bodies or ARTIFACT_BODIES).items():
            if name in artifacts:
                (directory / name).write_bytes(body)
    return feed_module.DirectoryFeed(root)


GIT_IDENTITY = ["-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false"]


def git_repo(root: Path, *, tag: str | None) -> Path:
    """A work tree with one commit, and — when `tag` is given — a release tag
    plus one commit on top of it.

    That extra commit is the point of the tag case: it is the candidate
    revision, and it must never be mistaken for the release line (invariant 8).
    """

    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    # Written into the repository rather than passed per command, because the
    # package makes commits of its own: a promotion's merge unit carries whoever
    # promoted, so a checkout Git has no identity for is one it refuses to
    # promote from — and a machine whose global config happens to supply one is
    # not something a test may depend on.
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "commit.gpgsign", "false"], check=True)
    (root / "file.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), *GIT_IDENTITY, "commit", "-q", "-m", "first"], check=True)
    if tag is not None:
        subprocess.run(["git", "-C", str(root), "tag", tag], check=True)
        (root / "file.txt").write_text("two\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), *GIT_IDENTITY, "commit", "-q", "-m", "candidate work"], check=True)
    return root


def git_commit(root: Path, message: str) -> str:
    """One more commit on the current branch, and its sha.

    Stages only `file.txt`, never the whole tree: the evolution records a test
    writes are the untracked state under examination, and committing them would
    make a later `git checkout` delete them.
    """

    path = root / "file.txt"
    path.write_text(f"{path.read_text(encoding='utf-8')}{message}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "--", "file.txt"], check=True)
    subprocess.run(["git", "-C", str(root), *GIT_IDENTITY, "commit", "-q", "-m", message], check=True)
    return git_rev(root, "HEAD")


def git_unrelated_commit(root: Path, message: str) -> str:
    """A commit on a history of its own, sharing no ancestor with anything here.

    What a rewritten or mistakenly-based experiment ref looks like from the
    outside: a perfectly resolvable revision that nothing in the batch's line
    leads to. Built with `commit-tree` rather than by branching, because the
    point is that no ancestor is shared at all.
    """

    tree = subprocess.run(
        ["git", "-C", str(root), "mktree"],
        check=True,
        capture_output=True,
        text=True,
        input="",
    ).stdout.strip()
    return subprocess.run(
        ["git", "-C", str(root), *GIT_IDENTITY, "commit-tree", tree, "-m", message],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def git_sibling_commit(root: Path, parent: str, content: str, message: str) -> str:
    """One commit on a line of its own, off `parent`, rewriting `file.txt`.

    The source line as it moves for reasons an experiment knows nothing about.
    Built with plumbing rather than a checkout so the working tree — which holds
    the uncommitted evolution records under test — is never touched, and written
    to the same file the candidate commits touch so the two can be made to
    conflict on purpose.
    """

    blob = subprocess.run(
        ["git", "-C", str(root), "hash-object", "-w", "--stdin"],
        check=True,
        capture_output=True,
        text=True,
        input=content,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "-C", str(root), "mktree"],
        check=True,
        capture_output=True,
        text=True,
        input=f"100644 blob {blob}\tfile.txt\n",
    ).stdout.strip()
    return subprocess.run(
        ["git", "-C", str(root), *GIT_IDENTITY, "commit-tree", tree, "-p", parent, "-m", message],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def git_file_commit(root: Path, parent: str, name: str, content: str, message: str) -> str:
    """One commit setting `name` to `content` on top of `parent`, keeping the
    rest of that tree exactly as it was.

    Two things a source line does after a promotion, and neither of them is
    `git_sibling_commit`'s wholesale rewrite: unrelated work arriving (a file
    the promotion never touched, which a rollback has to keep), and somebody
    putting a promoted file back by hand (which leaves nothing for a rollback to
    take out). Built with plumbing so the working tree, which holds the evolution
    records under test, is never touched.
    """

    blob = subprocess.run(
        ["git", "-C", str(root), "hash-object", "-w", "--stdin"],
        check=True,
        capture_output=True,
        text=True,
        input=content,
    ).stdout.strip()
    listing = subprocess.run(
        ["git", "-C", str(root), "ls-tree", parent],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    kept = [line for line in listing.splitlines() if line.split("\t", 1)[-1] != name]
    tree = subprocess.run(
        ["git", "-C", str(root), "mktree"],
        check=True,
        capture_output=True,
        text=True,
        input="".join(f"{line}\n" for line in kept) + f"100644 blob {blob}\t{name}\n",
    ).stdout.strip()
    return subprocess.run(
        ["git", "-C", str(root), *GIT_IDENTITY, "commit-tree", tree, "-p", parent, "-m", message],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def git_file(root: Path, revision: str, path: str) -> str:
    """What one path holds at one revision, for checking what a tree kept and
    what it gave back."""

    result = subprocess.run(
        ["git", "-C", str(root), "show", f"{revision}:{path}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def git_rev(root: Path, revision: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", f"{revision}^{{commit}}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def git_worktree(root: Path, directory: Path, ref: str) -> Path:
    """A second working tree of the same repository, sitting on `ref`.

    What a `HEAD`-only reading of "is this branch checked out" cannot see: the
    branch belongs to a directory the process asking never looks at, while
    `update-ref` moves it there just the same.
    """

    subprocess.run(
        ["git", "-C", str(root), "worktree", "add", str(directory), ref.split("refs/heads/")[-1]],
        check=True,
        capture_output=True,
        text=True,
    )
    return directory.resolve()


def git_tree(root: Path, revision: str) -> str:
    """The tree one commit carries, for checking what an integration produced
    against Git rather than against the record that claims it."""

    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", f"{revision}^{{tree}}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def git_update_ref(root: Path, ref: str, revision: str) -> None:
    subprocess.run(["git", "-C", str(root), "update-ref", ref, revision], check=True)


def git_follow(root: Path, revision: str) -> None:
    """Move the checked-out branch to `revision`, as a checkout following the
    source line does.

    Only ever called with a commit that already has the branch tip in its
    history and carries the same tree — the promotion merge of a candidate this
    branch is on — so the index and the working tree keep describing what is
    checked out and the untracked evolution records under test are untouched.
    That is why it is `update-ref` rather than a checkout: `git checkout` would
    delete them.
    """

    branch = subprocess.run(
        ["git", "-C", str(root), "symbolic-ref", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    git_update_ref(root, branch, revision)


def git_try_update_ref(root: Path, ref: str, revision: str) -> str | None:
    """Move a ref the way anything outside this package would, reporting instead
    of raising: None when it moved, and Git's own complaint when it did not.

    What an operation holding that ref looks like from the other side — the side
    a fetch, a push, or an operator's `git update-ref` is on.
    """

    result = subprocess.run(
        ["git", "-C", str(root), "update-ref", ref, revision],
        check=False,
        capture_output=True,
        text=True,
    )
    return None if result.returncode == 0 else " ".join((result.stderr or result.stdout).split())


def git_delete_ref(root: Path, ref: str) -> None:
    """Drop a ref, which is what every clone that never fetched
    `refs/evolution/*` looks like from inside this package."""

    subprocess.run(["git", "-C", str(root), "update-ref", "-d", ref], check=True)


def git_checkout(root: Path, revision: str) -> None:
    subprocess.run(["git", "-C", str(root), "checkout", "-q", revision], check=True)


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
