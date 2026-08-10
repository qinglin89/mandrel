"""The operator surface: CLI adapters, the orch-hub client, and the derived phase.

Everything runs against a temporary repository. The orch-hub feed does not exist
yet, so the client is exercised through an injected opener that answers the wire
contract `hub.py` states, which is the only way to test a client written against
an API that has not shipped.

Two tests are the exception and bind a loopback server: what `urllib` does with
a redirect — whose headers it copies, and to whom — is a property of the real
handler chain, and an injected opener replaces exactly the code under test. They
use `127.0.0.1` and an ephemeral port; nothing leaves the machine.

The temporary config lowers `target_task_count` to 2 and `minimum_task_count` to
1 so a full flow fits in two reports; `test_evolution_controller` asserts the
shipped file's real numbers load.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import threading
import urllib.error
import urllib.parse
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from evolution_fixtures import (
    ARTIFACT_BODIES,
    admitted_task,
    experiment_decision,
    experiment_round,
    git_checkout,
    git_commit,
    git_repo,
    git_rev,
    git_unrelated_commit,
    git_update_ref,
    make_record,
    make_repo,
    rejection,
    snapshot,
    write_closure,
    write_draft,
    write_experiment,
    write_feed,
    write_manifest,
    write_outcome,
    write_rejected_drafts,
)

from ai_native_deployment import cli, evolution
from ai_native_deployment.evolution import (
    analysis_task,
    batches,
    hub,
    importer,
    lineage,
    phase,
    render,
    reports,
)

TARGET = 2
MINIMUM = 1

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
BASE_URL = "https://orch-hub.example"
TOKEN = "s3cret-token"

# The revisions an experiment record pins. Opaque here on purpose: the lineage
# is read from the records, so nothing about it depends on this checkout holding
# these objects (contract: What is derived).
BASE = "a" * 40
CANDIDATE = "b" * 40


# --- fixtures ----------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = make_repo(tmp_path)
    path = root / "evolution" / "config.toml"
    text = path.read_text(encoding="utf-8")
    for old, new in (
        ("target_task_count = 20", f"target_task_count = {TARGET}"),
        ("minimum_task_count = 10", f"minimum_task_count = {MINIMUM}"),
    ):
        assert old in text
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")
    return root


@pytest.fixture
def config(repo: Path) -> evolution.EvolutionConfig:
    return evolution.load_config(repo)


@pytest.fixture
def feed_root(tmp_path: Path) -> Path:
    return tmp_path / "feed"


@pytest.fixture(autouse=True)
def no_ambient_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's own orch-hub credentials must not decide what these tests
    exercise."""

    monkeypatch.delenv("ORCH_HUB_URL", raising=False)
    monkeypatch.delenv("ORCH_HUB_TOKEN", raising=False)


def records(count: int) -> list[dict]:
    return [
        make_record(key=f"r{index}", sequence=index, task_id=f"2026-07-{index:02d}-task")
        for index in range(1, count + 1)
    ]


def fill_pool(config: evolution.EvolutionConfig, feed_root: Path, count: int):
    feed = write_feed(feed_root, records(count))
    evolution.sync(config, feed)
    return feed


def freeze(config: evolution.EvolutionConfig, **kwargs) -> batches.FreezeResult:
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("runner_revision", "v2.2.0")
    return evolution.freeze(config, **kwargs)


def record_findings(config: evolution.EvolutionConfig, batch_id: str) -> Path:
    path = config.batches_root / batch_id / batches.FINDINGS_FILENAME
    path.write_text("# Findings\n\nNo protocol change justified.\n", encoding="utf-8")
    return path


def complete_analysis_task(config: evolution.EvolutionConfig, task_id: str) -> None:
    path = analysis_task.task_path(config, task_id)
    text = path.read_text(encoding="utf-8").replace("status: pending", "status: completed", 1)
    path.write_text(text, encoding="utf-8")


def close_batch(config: evolution.EvolutionConfig, batch_id: str, task_id: str) -> None:
    """Close a batch the way the contract does: dispositions committed, the
    analysis task completed, and the next controller run publishing the closure
    record from that status."""

    record_findings(config, batch_id)
    complete_analysis_task(config, task_id)
    freeze(config)
    assert (config.batches_root / batch_id / batches.CLOSURE_FILENAME).is_file()


def draft(config: evolution.EvolutionConfig, batch_id: str, draft_id: str) -> Path:
    """A change-task draft, as an analysis session writes one: inert until a
    human admits it into an experiment (contract: Change admission)."""

    return write_draft(config.batches_root, batch_id, draft_id)


def experiment(
    config: evolution.EvolutionConfig,
    batch_id: str,
    *,
    ordinal: int = 1,
    rounds: list[dict] | None = None,
    decision: dict | None = None,
) -> Path:
    """One experiment record for `batch_id`, on that batch's frozen base."""

    return write_experiment(
        config.experiments_root,
        f"{batch_id}-exp-{ordinal:02d}",
        base_revision=BASE,
        rounds=rounds,
        decision=decision,
    )


def copy_into_tasks(config: evolution.EvolutionConfig, path: Path) -> Path:
    """A draft copied into the active pool, as grouped admission does — the file
    alone, with no experiment record behind it."""

    target = analysis_task.tasks_root(config) / path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --- lifecycle phase ---------------------------------------------------------


def test_an_untouched_workspace_is_idle(config: evolution.EvolutionConfig) -> None:
    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_IDLE
    assert status.summary == "idle"
    assert status.decision.task_count == 0
    assert status.pool_complete is False


def test_a_staged_pool_reports_its_count_against_the_target(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, 1)

    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_POOL
    assert status.summary == f"pool 1/{TARGET}"
    assert status.pool_complete is True


def test_a_pool_left_as_a_prefix_reports_completeness_unproven(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """A page bound makes the count a prefix of the feed, not a denominator
    (invariants 1 and 2) — and the phase has to say so, because the number alone
    looks the same."""
    feed = write_feed(feed_root, records(3))
    evolution.sync(config, feed, page_size=1, max_pages=1)

    status = phase.describe(config, now=NOW)

    assert status.pool_complete is False
    assert status.decision.reason == batches.REASON_POOL_INCOMPLETE
    assert "completeness unproven" in render.format_status(status)


def test_a_frozen_batch_holds_the_lifecycle_at_batch_frozen(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)

    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_BATCH_FROZEN
    assert status.current_batch is not None
    assert status.current_batch.batch_id == result.batch_id
    assert status.current_batch.findings_recorded is False
    assert status.current_batch.analysis_complete is False
    assert status.current_batch.evidence_local == status.current_batch.report_count
    assert status.decision.reason == batches.REASON_CURRENT_BATCH


def test_recorded_dispositions_move_the_phase_without_ending_the_analysis(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """`findings.md` is the disposition record and ends nothing on its own; the
    phase distinguishes the two so an operator can see analysis in progress."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    record_findings(config, result.batch_id or "")

    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_DISPOSITIONS_READY
    assert status.current_batch is not None and status.current_batch.batch_id == result.batch_id
    assert status.current_batch.analysis_complete is False


def test_a_completed_analysis_leaves_its_batch_current_at_the_admission_gate(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """The analysis stage ends; the batch does not (invariant 14). What is
    waiting is the human admission gate, and the batch goes on holding the next
    cohort back while it waits."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    close_batch(config, result.batch_id or "", result.analysis_task_id or "")
    draft(config, result.batch_id or "", "tighten-contract")

    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_PROPOSALS_PENDING
    assert status.summary == f"proposals-pending {result.batch_id} (1 draft)"
    assert status.current_batch is not None
    assert status.current_batch.analysis_complete is True
    assert status.gate is not None and status.gate.waiting == ("tighten-contract",)
    assert status.decision.reason == batches.REASON_CURRENT_BATCH


def test_an_admitted_draft_stops_waiting_at_the_gate(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Admission copies the draft and leaves it in place, so the directory keeps
    every proposal ever made. What makes it spent is the experiment record that
    took it — and a declined one is spent too."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    batch_id = result.batch_id or ""
    close_batch(config, batch_id, result.analysis_task_id or "")
    for draft_id in ("tighten-contract", "loader-fallback", "widen-scan"):
        draft(config, batch_id, draft_id)
    experiment(config, batch_id, rounds=[experiment_round(1, tasks=[admitted_task("tighten-contract")])])
    write_rejected_drafts(config.batches_root, batch_id, [rejection("widen-scan")])

    status = phase.describe(config, now=NOW)

    assert status.gate is not None
    assert status.gate.waiting == ("loader-fallback",)
    assert status.gate.consumed == {"tighten-contract": f"{batch_id}-exp-01"}
    assert status.gate.declined == ("widen-scan",)
    assert (config.batches_root / batch_id / analysis_task.PROPOSED_TASKS_DIRNAME / "tighten-contract.md").is_file()


def test_an_open_round_reports_the_tasks_it_is_still_waiting_on(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    batch_id = result.batch_id or ""
    close_batch(config, batch_id, result.analysis_task_id or "")
    draft(config, batch_id, "tighten-contract")
    experiment(
        config,
        batch_id,
        rounds=[
            experiment_round(
                1,
                tasks=[
                    admitted_task("tighten-contract", task_id="2026-08-02-tighten-contract", complete=False),
                    admitted_task("loader-fallback", complete=True),
                ],
            )
        ],
    )

    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_IMPLEMENTING
    assert status.implementation_tasks == ("2026-08-02-tighten-contract",)
    assert status.summary == f"implementing {batch_id}-exp-01 round 1 (1 task left)"
    assert "implementing 2026-08-02-tighten-contract" in render.format_status(status)


def test_a_round_whose_tasks_are_all_observed_complete_is_ready_to_seal(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Still an open round: what pins its candidate is the seal, and nothing may
    be measured before that (invariant 16)."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    batch_id = result.batch_id or ""
    close_batch(config, batch_id, result.analysis_task_id or "")
    experiment(config, batch_id, rounds=[experiment_round(1, tasks=[admitted_task("loader-fallback")])])

    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_IMPLEMENTING
    assert status.implementation_tasks == ()
    assert status.summary == f"implementing {batch_id}-exp-01 round 1 (ready to seal)"
    assert status.revisions.round_candidate is None


def test_a_sealed_round_is_candidate_ready_and_names_the_revision_it_pinned(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    batch_id = result.batch_id or ""
    close_batch(config, batch_id, result.analysis_task_id or "")
    experiment(config, batch_id, rounds=[experiment_round(1, candidate_revision=CANDIDATE)])

    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_CANDIDATE_READY
    assert status.summary == f"candidate-ready {batch_id}-exp-01 round 1"
    assert status.revisions.base is not None and status.revisions.base.sha == BASE
    assert status.revisions.round_candidate is not None
    assert status.revisions.round_candidate.sha == CANDIDATE
    # No ref in this checkout, which says nothing about the record's pins — and
    # is reported once, as the missing tip, rather than also as a finding.
    assert status.revisions.candidate_tip is None
    rendered = render.format_status(status)
    assert "tip          none — the experiment ref is not in this checkout" in rendered
    assert "cannot confirm the pinned history" not in rendered


def test_a_candidate_that_does_not_descend_from_the_frozen_base_is_named(
    tmp_path: Path
) -> None:
    """Invariant 15 read back: the whole chain is checked, so an attempt built
    on a history the batch never froze is reported with the pair that broke it
    rather than as a ref resting where its record says."""
    root = git_repo(make_repo(tmp_path), tag="v2.2.0")
    config = evolution.load_config(root)
    write_manifest(config.batches_root, "evolution-batch-0001", ["r1"], analysis_task_id="2026-07-31-analysis")
    record_findings(config, "evolution-batch-0001")
    write_closure(config.batches_root, "evolution-batch-0001", analysis_task_id="2026-07-31-analysis")
    stranded = git_unrelated_commit(root, "an attempt on a history of its own")
    write_experiment(
        config.experiments_root,
        "evolution-batch-0001-exp-01",
        base_revision=git_rev(root, "HEAD"),
        rounds=[experiment_round(1, candidate_revision=stranded)],
    )
    git_update_ref(root, "refs/evolution/experiments/evolution-batch-0001-exp-01", stranded)

    status = phase.describe(config, now=NOW)

    assert status.ref is not None
    assert status.ref.state == lineage.REF_AT_PIN
    assert status.ref.consistent is False
    assert status.ref.chain_break == (git_rev(root, "HEAD"), stranded)
    assert "the pinned history is broken" in render.format_status(status)


def test_terminal_experiments_are_history_and_block_no_alternative(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """A batch carrying two dropped attempts and one open alternative is an
    ordinary state, and the lifecycle reads from the open one."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    batch_id = result.batch_id or ""
    close_batch(config, batch_id, result.analysis_task_id or "")
    experiment(
        config,
        batch_id,
        ordinal=1,
        rounds=[experiment_round(1, tasks=[admitted_task("loader-fallback")], candidate_revision=CANDIDATE)],
        decision=experiment_decision("superseded", superseded_by=f"{batch_id}-exp-02"),
    )
    experiment(
        config,
        batch_id,
        ordinal=2,
        rounds=[experiment_round(1, tasks=[admitted_task("widen-scan")])],
        decision=experiment_decision("abandoned"),
    )
    experiment(
        config,
        batch_id,
        ordinal=3,
        rounds=[experiment_round(1, tasks=[admitted_task("tighten-contract", complete=False)])],
    )

    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_IMPLEMENTING
    assert status.experiment is not None and status.experiment.experiment_id == f"{batch_id}-exp-03"
    assert [item.experiment_id for item in status.history] == [f"{batch_id}-exp-01", f"{batch_id}-exp-02"]
    assert status.current_batch is not None and status.current_batch.experiment_count == 3
    assert f"{batch_id}-exp-01 superseded" in render.format_status(status)


def test_a_batch_with_nothing_open_and_nothing_waiting_awaits_its_conclusion(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """The `no-change` case: analysis justified no change, so there is no draft
    to admit and no experiment to run, and what the batch needs is the outcome
    that says so (invariant 7)."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    close_batch(config, result.batch_id or "", result.analysis_task_id or "")

    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_CONCLUSION_PENDING
    assert status.summary == f"conclusion-pending {result.batch_id}"
    assert status.gate is not None and status.gate.waiting == ()


def test_a_concluded_batch_stops_being_current_and_reports_its_promotion(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    batch_id = result.batch_id or ""
    close_batch(config, batch_id, result.analysis_task_id or "")
    experiment(
        config,
        batch_id,
        rounds=[experiment_round(1, candidate_revision=CANDIDATE)],
        decision=experiment_decision("promoted", promotion_revision="c" * 40),
    )
    write_outcome(
        config.batches_root,
        batch_id,
        outcome="promoted",
        reason="the candidate held across the replay cohort",
        experiment_id=f"{batch_id}-exp-01",
        promotion_revision="c" * 40,
    )

    status = phase.describe(config, now=NOW)

    assert status.current_batch is None
    assert status.phase == phase.PHASE_IDLE
    assert status.experiment is None and status.history == ()
    assert status.last_promotion is not None
    assert status.last_promotion.revision == "c" * 40
    assert status.last_promotion.experiment_id == f"{batch_id}-exp-01"
    assert status.decision.current_batch_id is None
    assert "promoted" in render.format_status(status)


def test_a_task_file_citing_the_batch_is_not_what_makes_it_admitted(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """`.ai-tasks/` is machine-local and close-out archives tasks away, so the
    old citation scan found nothing on a fresh clone and less as time passed.
    The experiment record names its own tasks; a file with no record behind it
    admits nothing (contract: What is derived)."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    batch_id = result.batch_id or ""
    close_batch(config, batch_id, result.analysis_task_id or "")
    copy_into_tasks(config, draft(config, batch_id, "tighten-contract"))

    status = phase.describe(config, now=NOW)

    assert status.implementation_tasks == ()
    assert status.phase == phase.PHASE_PROPOSALS_PENDING
    assert status.gate is not None and status.gate.waiting == ("tighten-contract",)


def test_the_lineage_outlives_a_checkout_and_a_lost_task_pool(
    tmp_path: Path, feed_root: Path
) -> None:
    """The acceptance the derivation exists for: another branch, another
    revision, and no `.ai-tasks/` at all still derive one lifecycle."""
    root = make_repo(tmp_path)
    git_repo(root, tag="v2.2.0")
    config = evolution.load_config(root)
    fill_pool(config, feed_root, 1)
    write_manifest(config.batches_root, "evolution-batch-0001", ["r1"], analysis_task_id="2026-07-31-analysis")
    record_findings(config, "evolution-batch-0001")
    write_closure(config.batches_root, "evolution-batch-0001", analysis_task_id="2026-07-31-analysis")
    experiment(config, "evolution-batch-0001", rounds=[experiment_round(1, candidate_revision=CANDIDATE)])
    before = phase.describe(config, now=NOW)

    first = git_rev(root, "HEAD~1")
    git_commit(root, "unrelated work")
    git_checkout(root, first)
    shutil.rmtree(analysis_task.tasks_root(config), ignore_errors=True)

    after = phase.describe(config, now=NOW)

    assert before.phase == after.phase == phase.PHASE_CANDIDATE_READY
    assert after.revisions.round_candidate is not None
    assert after.revisions.round_candidate.sha == CANDIDATE
    assert after.experiment is not None
    assert after.experiment.experiment_id == "evolution-batch-0001-exp-01"


def test_a_batch_whose_evidence_was_staged_elsewhere_is_shown_not_failed(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """A frozen cohort owns its reports wherever it was frozen; `.ai-evolution/`
    is machine-local, so a clone holds the manifest and none of the bundles."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    shutil.rmtree(config.artifacts_root)

    status = phase.describe(config, now=NOW)

    assert status.current_batch is not None
    assert status.current_batch.evidence_local == 0
    assert "evidence on this machine: 0/" in render.format_status(status)
    assert result.batch_id in render.format_status(status)


def test_an_experiment_ref_that_moved_past_its_seal_is_reported_not_hidden(
    tmp_path: Path, feed_root: Path
) -> None:
    """A reader names it; the guarded operations are what refuse (invariant 16).
    Refusing to describe the lifecycle is not how an operator finds out."""
    root = git_repo(make_repo(tmp_path), tag="v2.2.0")
    config = evolution.load_config(root)
    write_manifest(config.batches_root, "evolution-batch-0001", ["r1"], analysis_task_id="2026-07-31-analysis")
    record_findings(config, "evolution-batch-0001")
    write_closure(config.batches_root, "evolution-batch-0001", analysis_task_id="2026-07-31-analysis")
    sealed = git_rev(root, "HEAD")
    write_experiment(
        config.experiments_root,
        "evolution-batch-0001-exp-01",
        base_revision=git_rev(root, "HEAD~1"),
        rounds=[experiment_round(1, candidate_revision=sealed)],
    )
    git_update_ref(root, "refs/evolution/experiments/evolution-batch-0001-exp-01", git_commit(root, "late work"))

    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_CANDIDATE_READY
    assert status.ref is not None
    assert status.ref.state == lineage.REF_AHEAD
    assert status.ref.consistent is False
    assert status.revisions.round_candidate is not None and status.revisions.round_candidate.sha == sealed
    assert status.revisions.candidate_tip is not None and status.revisions.candidate_tip.sha != sealed
    assert "is not at the revision the record pins" in render.format_status(status)


def test_status_writes_nothing(config: evolution.EvolutionConfig, feed_root: Path, repo: Path) -> None:
    fill_pool(config, feed_root, TARGET)
    freeze(config)
    before = snapshot(repo)

    phase.describe(config, now=NOW)

    assert snapshot(repo) == before


def test_the_status_json_carries_the_phase_and_the_revisions_in_play(
    tmp_path: Path, feed_root: Path
) -> None:
    root = make_repo(tmp_path)
    git_repo(root, tag="v2.2.0")
    config = evolution.load_config(root)
    fill_pool(config, feed_root, 1)

    payload = phase.describe(config, now=NOW).to_json()

    assert payload["schema_version"] == phase.SCHEMA_VERSION == 2
    assert payload["phase"] == phase.PHASE_POOL
    assert payload["pool"] == {
        "task_count": 1,
        "target": 20,
        "minimum": 10,
        "complete": True,
        "oldest_pending_at": payload["pool"]["oldest_pending_at"],
        "waited_days": payload["pool"]["waited_days"],
        "max_wait_days": 30,
    }
    assert payload["batches"] == {"total": 0, "current": None}
    assert payload["gate"] is None
    assert payload["experiments"] == {"open": None, "history": []}
    assert payload["revisions"] == {"base": None, "candidate_tip": None, "round_candidate": None}
    assert payload["last_promotion"] is None
    assert json.loads(json.dumps(payload)) == payload


def test_the_status_json_names_the_five_revisions_apart(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Base, pinned candidate, and promotion are three different commits, and an
    evidence trail that substitutes one for another measures nothing (contract:
    Revisions in play). The deployed effective revision is per target and is not
    a property of this repository."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    batch_id = result.batch_id or ""
    close_batch(config, batch_id, result.analysis_task_id or "")
    experiment(config, batch_id, rounds=[experiment_round(1, candidate_revision=CANDIDATE)])

    payload = phase.describe(config, now=NOW).to_json()

    assert payload["revisions"]["base"] == {"sha": BASE, "ref": "v2.2.0"}
    assert payload["revisions"]["round_candidate"] == {"sha": CANDIDATE, "ref": None}
    assert payload["revisions"]["candidate_tip"] is None
    assert payload["experiments"]["open"]["round"] == {
        "number": 1,
        "state": phase.ROUND_CANDIDATE_READY,
        "opened_at": "2026-08-01T09:00:00Z",
        "candidate_revision": CANDIDATE,
        "tasks": [{"task_id": "2026-08-01-loader-fallback", "draft_id": "loader-fallback", "complete": True}],
    }
    assert payload["experiments"]["open"]["ref"]["state"] == lineage.REF_ABSENT
    assert json.loads(json.dumps(payload)) == payload


# --- orch-hub client ---------------------------------------------------------


class FakeResponse(io.BytesIO):
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def hub_feed(routes: dict[str, object], *, seen: list | None = None) -> hub.OrchHubFeed:
    """A client wired to canned responses, keyed by URL.

    A route value may be bytes (a 200 body) or an exception to raise, which is
    how the transport-failure paths are exercised without a socket.
    """

    def opener(request, timeout=None):
        if seen is not None:
            seen.append(request)
        answer = routes.get(request.full_url)
        if answer is None:
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(answer)

    return hub.OrchHubFeed(BASE_URL, TOKEN, "/api/evaluation/reports", opener=opener)


def page_url(**query: str) -> str:
    return f"{BASE_URL}/api/evaluation/reports?{urllib.parse.urlencode(query)}"


def test_an_unset_feed_url_or_token_is_reported_as_not_ready(
    config: evolution.EvolutionConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """orch-hub's global feed is a separate deliverable; until it lands the
    message has to name both variables and the offline path."""
    with pytest.raises(evolution.FeedError) as excinfo:
        hub.feed_from_config(config, environ={})

    message = str(excinfo.value)
    assert "ORCH_HUB_URL" in message and "ORCH_HUB_TOKEN" in message
    assert "--feed-dir" in message


def test_only_the_missing_variable_is_named(config: evolution.EvolutionConfig) -> None:
    with pytest.raises(evolution.FeedError) as excinfo:
        hub.feed_from_config(config, environ={"ORCH_HUB_URL": BASE_URL})

    assert "ORCH_HUB_TOKEN is unset" in str(excinfo.value)


def test_the_token_travels_in_a_header_and_never_in_the_url(config: evolution.EvolutionConfig) -> None:
    seen: list = []
    body = json.dumps({"items": [], "next_cursor": None, "exhausted": True}).encode("utf-8")
    feed = hub_feed({page_url(limit="10"): body}, seen=seen)

    feed.fetch_page(None, 10)

    assert seen[0].get_header("Authorization") == f"Bearer {TOKEN}"
    assert TOKEN not in seen[0].full_url


def test_a_page_carries_its_items_cursor_and_exhaustion(config: evolution.EvolutionConfig) -> None:
    record = make_record(key="r1", sequence=1)
    body = json.dumps({"items": [record], "next_cursor": "c1", "exhausted": False}).encode("utf-8")
    feed = hub_feed({page_url(limit="5"): body})

    page = feed.fetch_page(None, 5)

    assert page.items == (record,)
    assert page.cursor == "c1"
    assert page.exhausted is False


def test_a_page_without_exhaustion_is_refused(config: evolution.EvolutionConfig) -> None:
    """It authorizes a later freeze to treat the pool as the whole eligible set,
    so it is read from the feed, never inferred."""
    body = json.dumps({"items": [], "next_cursor": None}).encode("utf-8")
    feed = hub_feed({page_url(limit="5"): body})

    with pytest.raises(evolution.FeedError, match="exhausted"):
        feed.fetch_page(None, 5)


def test_a_null_next_cursor_leaves_discovery_where_it_was(config: evolution.EvolutionConfig) -> None:
    """Reading it as "start over" would re-import the feed from the beginning on
    every drained run."""
    body = json.dumps({"items": [], "next_cursor": None, "exhausted": True}).encode("utf-8")
    feed = hub_feed({page_url(limit="5", cursor="c9"): body})

    assert feed.fetch_page("c9", 5).cursor == "c9"


def test_an_artifact_the_feed_does_not_serve_is_absent_rather_than_fatal(
    config: evolution.EvolutionConfig,
) -> None:
    """A 404 is the feed stating the body is not there: the L1+L2 set is not
    durable, which the importer records as a rejection with a reason."""
    record = make_record(key="r1", sequence=1)
    routes = {
        f"{BASE_URL}/api/evaluation/reports/r1/artifacts/{name}": body
        for name, body in list(ARTIFACT_BODIES.items())[:3]
    }
    feed = hub_feed(routes)

    blobs = feed.fetch_artifacts(record)

    assert set(blobs) == set(list(ARTIFACT_BODIES)[:3])


def test_a_transport_failure_fetching_an_artifact_raises(config: evolution.EvolutionConfig) -> None:
    """An unreachable feed says nothing about a report's eligibility, and
    recording it as rejected would bury a good report permanently."""
    record = make_record(key="r1", sequence=1)
    feed = hub_feed(
        {
            f"{BASE_URL}/api/evaluation/reports/r1/artifacts/{name}": urllib.error.URLError("connection reset")
            for name in ARTIFACT_BODIES
        }
    )

    with pytest.raises(evolution.FeedError, match="unreachable"):
        feed.fetch_artifacts(record)


def test_rejected_credentials_name_the_token_variable(config: evolution.EvolutionConfig) -> None:
    feed = hub_feed({page_url(limit="5"): urllib.error.HTTPError(page_url(limit="5"), 401, "Unauthorized", {}, None)})

    with pytest.raises(evolution.FeedError, match="token"):
        feed.fetch_page(None, 5)


def test_a_report_key_with_a_slash_addresses_one_path_segment(config: evolution.EvolutionConfig) -> None:
    """Otherwise a foreign key escapes the endpoint, the way it must never
    become a path component locally either."""
    seen: list = []
    feed = hub_feed({}, seen=seen)

    feed.fetch_artifacts({"report_key": "a/../b", "artifacts": {"evidence": {"size_bytes": 1}}})

    assert seen[0].full_url.endswith("/reports/a%2F..%2Fb/artifacts/evidence")


def test_a_body_larger_than_declared_is_bounded_and_then_rejected(
    config: evolution.EvolutionConfig,
) -> None:
    """The client stops reading at one byte past the declared size; the
    importer's hash check is what turns that into a rejection."""
    oversized = b"x" * 5000
    record = make_record(key="r1", sequence=1)
    declared = record["artifacts"]["evidence"]["size_bytes"]
    feed = hub_feed({f"{BASE_URL}/api/evaluation/reports/r1/artifacts/evidence": oversized})

    blobs = feed.fetch_artifacts(record)

    assert len(blobs["evidence"]) == declared + 1


def test_a_declared_size_never_widens_the_clients_own_read_bound(config: evolution.EvolutionConfig) -> None:
    """`size_bytes` has a minimum and no maximum in the import schema, so a feed
    declaring a petabyte must not turn into a petabyte-sized read: the declaring
    side is the one that may be lying."""
    asked: list[int] = []

    class Recording(FakeResponse):
        def read(self, size: int = -1) -> bytes:
            asked.append(size)
            return super().read(size)

    def opener(request: object, timeout: float | None = None) -> Recording:
        return Recording(b"x" * 8)

    record = make_record(key="r1", sequence=1)
    record["artifacts"]["evidence"]["size_bytes"] = 10**15
    feed = hub.OrchHubFeed(BASE_URL, TOKEN, "/api/evaluation/reports", opener=opener)

    feed.fetch_artifacts(record)

    assert max(asked) == hub.MAX_RESPONSE_BYTES + 1


def test_a_body_over_the_clients_limit_is_rejected_not_quietly_shortened(
    config: evolution.EvolutionConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap stops the read; the importer's size check is what stops the
    truncated body reaching the pool as a short artifact nobody declared."""
    monkeypatch.setattr(hub, "MAX_RESPONSE_BYTES", 2048)
    bodies = dict(ARTIFACT_BODIES, evidence=b"L" * 4096)
    record = make_record(key="r1", sequence=1, bodies=bodies)
    routes: dict[str, object] = {
        page_url(limit="50"): json.dumps({"items": [record], "next_cursor": "c1", "exhausted": True}).encode("utf-8")
    }
    routes.update({f"{BASE_URL}/api/evaluation/reports/r1/artifacts/{name}": body for name, body in bodies.items()})

    result = importer.sync(config, hub_feed(routes))

    assert result.imported == ()
    assert result.rejected == (("r1", reports.REASON_ARTIFACT_HASH_MISMATCH),)


def test_a_plaintext_url_to_a_remote_host_is_refused(config: evolution.EvolutionConfig) -> None:
    """A bearer token on the wire in clear text is a leak no later care undoes."""
    with pytest.raises(evolution.FeedError, match="clear text"):
        hub.feed_from_config(config, environ={"ORCH_HUB_URL": "http://orch-hub.example", "ORCH_HUB_TOKEN": TOKEN})


def test_a_local_plaintext_feed_is_allowed(config: evolution.EvolutionConfig) -> None:
    feed = hub.feed_from_config(config, environ={"ORCH_HUB_URL": "http://localhost:8080/", "ORCH_HUB_TOKEN": TOKEN})

    assert feed.base_url == "http://localhost:8080"


def test_a_url_that_is_not_http_is_refused(config: evolution.EvolutionConfig) -> None:
    with pytest.raises(evolution.FeedError, match="http"):
        hub.feed_from_config(config, environ={"ORCH_HUB_URL": "file:///etc", "ORCH_HUB_TOKEN": TOKEN})


Responder = Callable[[str], tuple[int, dict[str, str], bytes]]


@contextlib.contextmanager
def loopback_server(responder: Responder) -> Iterator[tuple[str, list[tuple[str, dict[str, str]]]]]:
    """A throwaway server on 127.0.0.1, with the list of requests it received.

    The two redirect tests need the real `urllib` handler chain: an injected
    opener would replace the code that decides where the token goes.
    """

    received: list[tuple[str, dict[str, str]]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            received.append((self.path, {key.lower(): value for key, value in self.headers.items()}))
            status, headers, body = responder(self.path)
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            """Silence the default stderr logging; the assertions are the output."""

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        server.shutdown()
        server.server_close()


def test_a_cross_origin_redirect_never_receives_the_token() -> None:
    """`urllib`'s default handler copies the request headers — `Authorization`
    among them — onto whichever destination answered with a `Location`, so
    checking the configured URL proves nothing about where the token lands."""
    drained = (200, {"Content-Type": "application/json"}, b'{"items": [], "next_cursor": null, "exhausted": true}')
    with loopback_server(lambda path: drained) as (elsewhere, elsewhere_received):
        with loopback_server(lambda path: (302, {"Location": f"{elsewhere}/feed"}, b"")) as (base, _):
            feed = hub.OrchHubFeed(base, TOKEN, "/api/evaluation/reports")

            with pytest.raises(evolution.FeedError, match="redirected") as excinfo:
                feed.fetch_page(None, 10)

    assert elsewhere_received == []
    assert TOKEN not in str(excinfo.value)


def test_a_same_origin_redirect_is_refused_as_well() -> None:
    """The rule is "no redirects", not an origin comparison — there is no
    same-origin test to get subtly wrong, and a chain that is same-origin at
    every hop still ends wherever the last one points."""
    with loopback_server(lambda path: (302, {"Location": "/moved"}, b"")) as (base, received):
        feed = hub.OrchHubFeed(base, TOKEN, "/api/evaluation/reports")

        with pytest.raises(evolution.FeedError, match="redirected"):
            feed.fetch_page(None, 10)

    assert len(received) == 1


def test_the_hub_client_imports_a_report_end_to_end(config: evolution.EvolutionConfig) -> None:
    """The client and the importer meet only at `ReportFeed`, so this is the one
    test that proves the pair works together."""
    record = make_record(key="r1", sequence=1)
    routes: dict[str, object] = {
        page_url(limit="50"): json.dumps({"items": [record], "next_cursor": "c1", "exhausted": True}).encode("utf-8")
    }
    routes.update(
        {f"{BASE_URL}/api/evaluation/reports/r1/artifacts/{name}": body for name, body in ARTIFACT_BODIES.items()}
    )

    result = importer.sync(config, hub_feed(routes))

    assert result.imported == ("r1",)
    assert result.exhausted is True


# --- CLI ---------------------------------------------------------------------


def test_status_prints_the_phase(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_repo(tmp_path)
    git_repo(root, tag="v2.2.0")

    code, out, _ = run(["evolution", "status", "--repo", str(root)], capsys)

    assert code == 0
    assert out.startswith("evolution: idle")


def test_status_reports_no_revisions_in_play_before_any_experiment(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A checkout is not a candidate. Before an experiment freezes a base there
    is nothing in play, whatever branch the operator happens to be on."""
    code, out, _ = run(["evolution", "status", "--repo", str(repo)], capsys)

    assert code == 0
    assert "revisions    none in play — no experiment has frozen a base" in out
    assert "candidate" not in out


def test_status_json_is_the_same_shape(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(["evolution", "status", "--repo", str(repo), "--json"], capsys)

    assert code == 0
    assert json.loads(out)["phase"] == phase.PHASE_IDLE


def test_list_inspects_the_feed_and_writes_nothing(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_feed(feed_root, records(2))
    before = snapshot(repo)

    code, out, _ = run(["evolution", "list", "--repo", str(repo), "--feed-dir", str(feed_root)], capsys)

    assert code == 0
    assert "2 report(s), 2 unique completed task(s)" in out
    assert "nothing was written" in out
    assert snapshot(repo) == before


def test_sync_imports_and_reports_the_pool(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_feed(feed_root, records(2))

    code, out, _ = run(["evolution", "sync", "--repo", str(repo), "--feed-dir", str(feed_root)], capsys)

    assert code == 0
    assert "imported" in out and "2 unique completed task(s) pending" in out
    assert evolution.load_state(evolution.load_config(repo)).pending


def test_start_runs_the_whole_flow_to_one_pending_analysis_task(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The acceptance end to end: an empty repository, a fixture feed, and one
    command reaching a frozen cohort with its pending analysis task."""
    write_feed(feed_root, records(TARGET))

    code, out, _ = run(["evolution", "start", "--repo", str(repo), "--feed-dir", str(feed_root)], capsys)

    config = evolution.load_config(repo)
    frozen = evolution.load_batches(config)
    assert code == 0
    assert len(frozen) == 1
    assert "frozen" in out and frozen[0].batch_id in out

    task_id = frozen[0].analysis_task_id or ""
    task = analysis_task.task_path(config, task_id).read_text(encoding="utf-8")
    assert "status: pending" in task
    assert analysis_task.CONTRACT_PATH in task
    assert frozen[0].batch_id in task
    assert analysis_task.PROPOSED_TASKS_DIRNAME in task
    assert f"| {task_id} " in analysis_task.index_path(config).read_text(encoding="utf-8")


def test_a_second_start_creates_no_second_batch(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_feed(feed_root, records(TARGET))
    run(["evolution", "start", "--repo", str(repo), "--feed-dir", str(feed_root)], capsys)

    code, out, _ = run(["evolution", "start", "--repo", str(repo), "--feed-dir", str(feed_root)], capsys)

    assert code == 0
    assert "is still current" in out
    assert len(evolution.load_batches(evolution.load_config(repo))) == 1


def test_start_below_the_target_forms_no_batch_and_says_why(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Too little evidence is the contract's normal outcome, so the run
    succeeds and reports the reason."""
    write_feed(feed_root, records(1))

    code, out, _ = run(["evolution", "start", "--repo", str(repo), "--feed-dir", str(feed_root)], capsys)

    assert code == 0
    assert f"no batch — {batches.REASON_BELOW_TARGET}" in out
    assert evolution.load_batches(evolution.load_config(repo)) == []


def test_force_without_a_justification_is_refused(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_feed(feed_root, records(1))

    code, _, err = run(
        ["evolution", "start", "--repo", str(repo), "--feed-dir", str(feed_root), "--force"], capsys
    )

    assert code == 2
    assert "justification" in err
    assert evolution.load_batches(evolution.load_config(repo)) == []


def test_a_justified_force_freezes_a_below_target_batch(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_feed(feed_root, records(1))

    code, out, _ = run(
        [
            "evolution",
            "start",
            "--repo",
            str(repo),
            "--feed-dir",
            str(feed_root),
            "--force",
            "--justification",
            "Severe correctness failure; escalated by the maintainer.",
        ],
        capsys,
    )

    config = evolution.load_config(repo)
    manifest = json.loads(evolution.load_batches(config)[0].manifest_path.read_text(encoding="utf-8"))
    assert code == 0
    assert batches.TRIGGER_FORCED in out
    assert manifest["forced"] is True
    assert "escalated by the maintainer" in manifest["force_justification"].lower()


def test_a_run_without_a_feed_or_credentials_is_actionable(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run(["evolution", "sync", "--repo", str(repo)], capsys)

    assert code == 2
    assert "ORCH_HUB_URL" in err and "--feed-dir" in err


def test_a_missing_workspace_config_is_actionable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    empty = tmp_path / "elsewhere"
    empty.mkdir()

    code, _, err = run(["evolution", "status", "--repo", str(empty)], capsys)

    assert code == 2
    assert "missing evolution config" in err


def test_an_unavailable_feed_is_reported_without_touching_the_pool(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unreachable source says nothing about any report's eligibility, so the
    run stops instead of recording an empty discovery."""
    config = evolution.load_config(repo)
    before = snapshot(repo)

    code, _, err = run(
        ["evolution", "sync", "--repo", str(repo), "--feed-dir", str(tmp_path / "absent")], capsys
    )

    assert code == 2
    assert "reports/" in err
    assert snapshot(repo) == before
    assert not config.state_path.exists()


def test_a_cursor_the_feed_never_issued_is_refused(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reinterpreting an unrecognised cursor is how discovery silently skips
    reports it never inspected."""
    config = evolution.load_config(repo)
    fill_pool(config, feed_root, 1)
    persisted = evolution.load_state(config)
    persisted.cursor = "not-a-position"
    evolution.save_state(config, persisted)

    code, _, err = run(["evolution", "sync", "--repo", str(repo), "--feed-dir", str(feed_root)], capsys)

    assert code == 2
    assert "invalid cursor" in err


def test_corrupt_runtime_state_is_reported_not_reset(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = evolution.load_config(repo)
    fill_pool(config, feed_root, 1)
    config.state_path.write_text('{"schema_version": 2}', encoding="utf-8")

    code, _, err = run(["evolution", "status", "--repo", str(repo)], capsys)

    assert code == 2
    assert "incomplete state" in err


def test_lock_contention_is_reported_with_its_holder(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = evolution.load_config(repo)
    write_feed(feed_root, records(1))
    config.lock_path.parent.mkdir(parents=True, exist_ok=True)
    config.lock_path.write_text(
        json.dumps({"pid": 4242, "host": "another-host", "acquired_at": "2026-08-01T11:00:00Z"}), encoding="utf-8"
    )

    code, _, err = run(["evolution", "sync", "--repo", str(repo), "--feed-dir", str(feed_root)], capsys)

    assert code == 2
    assert "4242" in err and "another-host" in err
    assert "remove it if no run is active" in err
