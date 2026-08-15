"""The operator surface: CLI adapters, the orch-hub client, and the derived phase.

Everything runs against a temporary repository. The client is exercised through
an injected opener answering the wire contract orch-hub publishes — the catalog
entry it really serves, not the import record this repository would have
preferred — so the translation `hub.py` performs is under test rather than
assumed. The one thing no injected opener can prove is that the live service
still answers that way; `scripts/probe-orch-hub.sh` is the credentialed check
for that, and it stays off the required gate.

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
    HUB_ARTIFACT_NAMES,
    HUB_LOCK_HASH,
    HUB_PROTOCOL_LEGACY,
    HUB_REVISION,
    RELEASE_REF,
    FakeHarness,
    admitted_task,
    complete_task,
    completed_report,
    experiment_decision,
    experiment_round,
    git_checkout,
    git_commit,
    git_follow,
    git_repo,
    git_rev,
    git_unrelated_commit,
    git_update_ref,
    hub_page,
    hub_protocol,
    make_hub_entry,
    make_manifest_report,
    make_record,
    make_repo,
    promote_candidate,
    promotion_of,
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
    assessment,
    batches,
    hub,
    importer,
    lineage,
    phase,
    render,
    reports,
    replay,
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

# The two cohorts a release reading is about: the batch whose experiment was
# promoted, and the first one frozen after it. Batch ids are allocated, so these
# are what the allocator would produce rather than names of a test's choosing.
FIRST = "evolution-batch-0001"
SECOND = "evolution-batch-0002"


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


@pytest.mark.parametrize("waiting", [False, True], ids=["conclusion-pending", "proposals-pending"])
def test_a_batch_between_attempts_explains_its_revisions_by_what_is_open(
    config: evolution.EvolutionConfig, feed_root: Path, waiting: bool
) -> None:
    """The candidate and the tip belong to the open experiment, and a batch
    whose only attempt was abandoned has none.

    Both absences then have one reason — there is nothing open — and neither of
    the ordinary explanations is true: no round was left unsealed, and no ref
    was looked up at all, so reporting them describes an experiment that does
    not exist. The phase does not discriminate either, which is why both of the
    states this batch can be in are here.
    """
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    batch_id = result.batch_id or ""
    close_batch(config, batch_id, result.analysis_task_id or "")
    experiment(
        config,
        batch_id,
        rounds=[experiment_round(1, tasks=[admitted_task("loader-fallback")])],
        decision=experiment_decision("abandoned"),
    )
    if waiting:
        draft(config, batch_id, "widen-scan")

    status = phase.describe(config, now=NOW)
    rendered = render.format_status(status)

    assert status.phase == (phase.PHASE_PROPOSALS_PENDING if waiting else phase.PHASE_CONCLUSION_PENDING)
    assert status.experiment is None and status.ref is None
    assert status.to_json()["experiments"]["open"] is None, "what the rendering reads the absence off"
    assert status.revisions.base is not None and status.revisions.base.sha == BASE
    assert status.revisions.round_candidate is None and status.revisions.candidate_tip is None
    assert "no experiment is open" in rendered
    assert "the open round has not been sealed" not in rendered
    assert "the experiment ref is not in this checkout" not in rendered
    assert f"{batch_id}-exp-01 abandoned" in rendered


def test_a_concluded_batch_stops_being_current_and_reports_its_promotion(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    batch_id = result.batch_id or ""
    close_batch(config, batch_id, result.analysis_task_id or "")
    decision = experiment_decision(
        "promoted",
        reason="the candidate held across the replay cohort",
        promotion_revision="c" * 40,
    )
    experiment(
        config,
        batch_id,
        rounds=[experiment_round(1, candidate_revision=CANDIDATE)],
        decision=decision,
    )
    write_outcome(
        config.batches_root,
        batch_id,
        outcome="promoted",
        reason=decision["reason"],
        experiment_id=f"{batch_id}-exp-01",
        promotion_revision="c" * 40,
        # One event, two records: the outcome states the merge unit the
        # experiment was promoted as, down to the candidate its round pinned.
        promotion=promotion_of(candidate_revision=CANDIDATE),
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

    assert payload["schema_version"] == phase.SCHEMA_VERSION == 7
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
    assert payload["batches"] == {"total": 0, "current": None, "history": []}
    assert payload["gate"] is None
    assert payload["experiments"] == {"open": None, "history": [], "pending_successor": None}
    assert payload["revisions"] == {"base": None, "candidate_tip": None, "round_candidate": None}
    # No experiment is open, so there is no round for evidence to be about — a
    # different absence from a round nothing has replayed.
    assert payload["replay"] is None
    assert payload["last_promotion"] is None
    # Nothing has been promoted, so there is no release for any cohort to read.
    assert payload["release"] is None
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


def artifact_url(key: str, name: str) -> str:
    """The published artifact's URL — by wire filename, which is what orch-hub's
    four-value selector accepts."""

    return f"{BASE_URL}/api/evaluation/reports/{key}/artifacts/{HUB_ARTIFACT_NAMES[name]}"


def artifact_routes(key: str, bodies: dict[str, bytes] | None = None) -> dict[str, object]:
    return {artifact_url(key, name): body for name, body in (bodies or ARTIFACT_BODIES).items()}


def http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "", {}, None)


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
    feed = hub_feed({page_url(limit="10"): hub_page([])}, seen=seen)

    feed.fetch_page(None, 10)

    assert seen[0].get_header("Authorization") == f"Bearer {TOKEN}"
    assert TOKEN not in seen[0].full_url


def test_a_page_carries_its_entries_cursor_and_exhaustion(config: evolution.EvolutionConfig) -> None:
    """`has_more` is the feed's own statement; `exhausted` is its negation, never
    a guess from a short page."""
    entry = make_hub_entry(key="r1", seq=1)
    feed = hub_feed({page_url(limit="5"): hub_page([entry], next_cursor=1, has_more=True)})

    page = feed.fetch_page(None, 5)

    assert page.cursor == "1"
    assert page.exhausted is False
    assert page.items[0]["report_key"] == "r1"


def test_the_watermark_is_sent_as_after_and_stays_opaque_upward(config: evolution.EvolutionConfig) -> None:
    """orch-hub pages an append-only catalog by an integer watermark, but nothing
    above `ReportFeed` may learn that a cursor is a number."""
    seen: list = []
    feed = hub_feed({page_url(limit="5", after="7"): hub_page([], cursor=7, next_cursor=7)}, seen=seen)

    page = feed.fetch_page("7", 5)

    assert "after=7" in seen[0].full_url and "cursor=" not in seen[0].full_url
    assert page.cursor == "7"


def test_a_page_without_has_more_is_refused(config: evolution.EvolutionConfig) -> None:
    """It authorizes a later freeze to treat the pool as the whole eligible set,
    so it is read from the feed, never inferred."""
    body = json.dumps({"enabled": True, "reports": [], "cursor": 0, "next_cursor": 0}).encode("utf-8")
    feed = hub_feed({page_url(limit="5"): body})

    with pytest.raises(evolution.FeedError, match="has_more"):
        feed.fetch_page(None, 5)


def test_an_empty_page_echoes_the_watermark_and_does_not_rewind(config: evolution.EvolutionConfig) -> None:
    """A drained feed returns the cursor unchanged. Reading a missing one as
    "start over" would re-import the feed from the beginning on every run, so a
    page without a watermark is refused rather than interpreted."""
    echoed = hub_feed({page_url(limit="5", after="9"): hub_page([], cursor=9, next_cursor=9)})
    assert echoed.fetch_page("9", 5).cursor == "9"

    missing = json.dumps({"enabled": True, "reports": [], "has_more": False, "next_cursor": None}).encode("utf-8")
    with pytest.raises(evolution.FeedError, match="next_cursor"):
        hub_feed({page_url(limit="5"): missing}).fetch_page(None, 5)


def test_a_disabled_evaluation_subsystem_is_not_an_empty_feed(config: evolution.EvolutionConfig) -> None:
    """Treating it as a drained page would let a freeze call an empty pool the
    whole eligible set."""
    body = json.dumps({"enabled": False, "reports": [], "cursor": 0, "next_cursor": 0, "has_more": False}).encode(
        "utf-8"
    )
    feed = hub_feed({page_url(limit="5"): body})

    with pytest.raises(evolution.FeedError, match="disabled"):
        feed.fetch_page(None, 5)


def test_a_pruned_artifact_is_absent_rather_than_fatal(config: evolution.EvolutionConfig) -> None:
    """410 is the feed stating the body was published and is gone: the L1+L2 set
    is no longer durable, which the importer records as a rejection."""
    record = make_record(key="r1", sequence=1)
    routes = artifact_routes("r1")
    routes[artifact_url("r1", "report_markdown")] = http_error(artifact_url("r1", "report_markdown"), 410)
    feed = hub_feed(routes)

    blobs = feed.fetch_artifacts(record)

    assert set(blobs) == {"evidence", "static_metrics", "semantic_report"}


def test_an_unknown_key_or_name_raises_instead_of_reading_as_pruned(config: evolution.EvolutionConfig) -> None:
    """404 says the request addressed nothing — a defect in what was asked, not
    a fact about retention. Recording it as a missing body would bury a report
    that is fine."""
    record = make_record(key="r1", sequence=1)
    routes = artifact_routes("r1")
    routes[artifact_url("r1", "evidence")] = http_error(artifact_url("r1", "evidence"), 404)
    feed = hub_feed(routes)

    with pytest.raises(evolution.FeedError, match="HTTP 404"):
        feed.fetch_artifacts(record)


def test_an_incoherent_stored_identity_raises(config: evolution.EvolutionConfig) -> None:
    """409 is the hub saying its own entry cannot address its artifacts; no retry
    heals it and no rejection reason describes it."""
    record = make_record(key="r1", sequence=1)
    routes = artifact_routes("r1")
    routes[artifact_url("r1", "evidence")] = http_error(artifact_url("r1", "evidence"), 409)
    feed = hub_feed(routes)

    with pytest.raises(evolution.FeedError, match="HTTP 409"):
        feed.fetch_artifacts(record)


def test_a_transport_failure_fetching_an_artifact_raises(config: evolution.EvolutionConfig) -> None:
    """An unreachable feed says nothing about a report's eligibility, and
    recording it as rejected would bury a good report permanently."""
    record = make_record(key="r1", sequence=1)
    feed = hub_feed({artifact_url("r1", name): urllib.error.URLError("connection reset") for name in ARTIFACT_BODIES})

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

    # The canned feed answers 404 for a URL it does not know, which is now a
    # refusal rather than an absent body; the request it built is the subject.
    with pytest.raises(evolution.FeedError):
        feed.fetch_artifacts({"report_key": "a/../b", "artifacts": {"evidence": {"size_bytes": 1}}})

    assert seen[0].full_url.endswith("/reports/a%2F..%2Fb/artifacts/evidence.json")


def test_a_body_larger_than_declared_is_bounded_and_then_rejected(
    config: evolution.EvolutionConfig,
) -> None:
    """The client stops reading at one byte past the declared size; the
    importer's hash check is what turns that into a rejection."""
    oversized = b"x" * 5000
    record = make_record(key="r1", sequence=1)
    declared = record["artifacts"]["evidence"]["size_bytes"]
    feed = hub_feed({**artifact_routes("r1"), artifact_url("r1", "evidence"): oversized})

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
    routes: dict[str, object] = {page_url(limit="50"): hub_page([make_hub_entry(key="r1", seq=1, bodies=bodies)])}
    routes.update(artifact_routes("r1", bodies))

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
    drained = (200, {"Content-Type": "application/json"}, hub_page([]))
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
    test that proves the pair works together — a published catalog entry in, a
    pooled report with verified bytes out."""
    routes: dict[str, object] = {page_url(limit="50"): hub_page([make_hub_entry(key="r1", seq=1)])}
    routes.update(artifact_routes("r1"))

    result = importer.sync(config, hub_feed(routes))

    assert result.imported == ("r1",)
    assert result.exhausted is True
    assert result.cursor_after == "1"


def test_a_published_identity_reaches_the_frozen_cohort_unchanged(
    config: evolution.EvolutionConfig,
) -> None:
    """The identity is only worth publishing if it survives to where a release
    assessment reads it: the pool stages the record whole and the freeze copies
    its provenance into the immutable manifest, so what places a report in a
    cohort is the pair its own feed stated."""
    entries = [
        make_hub_entry(key=f"r{index}", seq=index, task_id=f"2026-07-0{index}-task", protocol=hub_protocol())
        for index in (1, 2)
    ]
    routes: dict[str, object] = {page_url(limit="50"): hub_page(entries)}
    for entry in entries:
        routes.update(artifact_routes(entry["report_key"]))

    assert importer.sync(config, hub_feed(routes)).imported == ("r1", "r2")
    result = freeze(config)

    assert result.manifest_path is not None
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    provenance = [report["provenance"] for report in manifest["reports"]]
    assert [item["effective_revision"] for item in provenance] == [HUB_REVISION, HUB_REVISION]
    assert [item["deploy_lock_hash"] for item in provenance] == [HUB_LOCK_HASH, HUB_LOCK_HASH]


def test_a_translated_entry_is_the_record_the_import_schema_describes(
    config: evolution.EvolutionConfig,
) -> None:
    """The feed serves its own catalog entry; everything above `ReportFeed` reads
    an import record, so the translation is what has to be right."""
    feed = hub_feed({page_url(limit="5"): hub_page([make_hub_entry(key="r1", seq=4)])})

    record = feed.fetch_page(None, 5).items[0]

    assert record["schema_version"] == 1
    assert record["sequence"] == 4
    assert record["generated_at"] == "2026-07-30T10:00:00Z"
    assert record["source"]["repo_id"] == "repo-alpha"
    assert record["evaluator"]["model"] == "claude-opus-5"
    assert record["artifacts"]["report_markdown"]["media_type"] == "text/markdown"
    assert record["artifacts"]["evidence"]["size_bytes"] == len(ARTIFACT_BODIES["evidence"])
    # Nothing orch-hub says about runs or git survives: the import schema closes
    # its objects, and the entry here states no protocol identity, which a release
    # assessment must see as absent rather than invented.
    assert record["provenance"]["effective_revision"] is None
    assert "repo_path" not in record and "target_source" not in record
    assert reports.normalize(record, config, reports.load_import_schema(config)).report_key == "r1"


def test_a_verified_protocol_identity_is_copied_onto_the_record(
    config: evolution.EvolutionConfig,
) -> None:
    """The pair orch-hub publishes is the one fact of this repository's provenance
    block the feed states, and it is what places a report in a release cohort."""
    entry = make_hub_entry(key="r1", seq=1, protocol=hub_protocol())
    feed = hub_feed({page_url(limit="5"): hub_page([entry])})

    provenance = feed.fetch_page(None, 5).items[0]["provenance"]

    assert provenance["effective_revision"] == HUB_REVISION
    assert provenance["deploy_lock_hash"] == HUB_LOCK_HASH
    # Copied, not reshaped: the record states exactly what the feed did.
    assert provenance["effective_revision"] == entry["provenance"]["protocol"]["effective_revision"]
    # The rest of the block orch-hub does not hold, and this side does not invent.
    assert provenance["runner_protocol_revision"] is None
    assert provenance["config_revision"] is None
    assert provenance["dev"] == {"agent": None, "model": None, "effort": None, "profile": None}


@pytest.mark.parametrize(
    "section",
    [
        HUB_PROTOCOL_LEGACY,
        {"available": False, "detail": "contributing runs verified different protocol revisions"},
        {"available": False, "effective_revision": HUB_REVISION, "deploy_lock_hash": HUB_LOCK_HASH},
        {"available": True, "detail": "an earlier hub published a revision on its own"},
        hub_protocol(lock_hash=None),
        hub_protocol(revision=None),
        hub_protocol(revision="   "),
        hub_protocol(revision=42),
        hub_protocol(available="true"),
        hub_protocol(available=1),
        None,
        "unavailable",
    ],
    ids=[
        "legacy-detail-only",
        "unavailable-mixed-run-set",
        "unavailable-but-carrying-a-pair",
        "available-carrying-neither-half",
        "revision-without-payload",
        "payload-without-revision",
        "blank-revision",
        "revision-that-is-not-a-string",
        "availability-as-a-string",
        "availability-as-a-number",
        "section-served-as-null",
        "section-that-is-not-an-object",
    ],
)
def test_anything_short_of_a_stated_pair_translates_to_two_nulls(
    config: evolution.EvolutionConfig, section: object
) -> None:
    """Half an identity is not a weaker fact, it is a different one: the commit
    names a source tree and the digest names the bytes taken from it, so neither
    stands in for the other. `available` is the flag orch-hub sets only for an
    identity every contributing run verified, and anything that is not exactly
    that flag over exactly both halves leaves the report unplaceable — which the
    assessment reports as `effective-revision-absent` rather than misplacing it."""
    feed = hub_feed({page_url(limit="5"): hub_page([make_hub_entry(key="r1", seq=1, protocol=section)])})

    record = feed.fetch_page(None, 5).items[0]

    assert record["provenance"]["effective_revision"] is None
    assert record["provenance"]["deploy_lock_hash"] is None
    # Still an admissible report: unplaceable costs a denominator, not a report.
    assert reports.normalize(record, config, reports.load_import_schema(config)).report_key == "r1"


def test_an_entry_predating_the_protocol_section_states_no_identity(
    config: evolution.EvolutionConfig,
) -> None:
    """The section is absent, not null, on everything an older hub published."""
    entry = make_hub_entry(key="r1", seq=1)
    del entry["provenance"]["protocol"]
    feed = hub_feed({page_url(limit="5"): hub_page([entry])})

    record = feed.fetch_page(None, 5).items[0]

    assert record["provenance"]["effective_revision"] is None
    assert record["provenance"]["deploy_lock_hash"] is None


def test_no_neighbouring_field_supplies_a_missing_identity(
    config: evolution.EvolutionConfig,
) -> None:
    """The entry carries other commit-shaped values — the newest commit of the
    evaluation's git window among them — and none of them is the revision the
    target's payload came from. Completing an unstated identity from one would
    place a report by a commit no run was shown to have run under."""
    entry = make_hub_entry(key="r1", seq=1, protocol=hub_protocol(revision=None))
    feed = hub_feed({page_url(limit="5"): hub_page([entry])})

    record = feed.fetch_page(None, 5).items[0]

    assert entry["provenance"]["git"]["newest_sha"]
    assert record["provenance"]["effective_revision"] is None
    assert record["provenance"]["deploy_lock_hash"] is None


def test_archived_is_how_this_feed_says_completed(config: evolution.EvolutionConfig) -> None:
    """orch-hub catalogs a report only once its task was archived at publication,
    and this protocol archives only at completion close-out — but it is mapped
    rather than asserted, so the importer's own gate still decides."""
    admissible = hub_feed({page_url(limit="5"): hub_page([make_hub_entry(key="r1", seq=1)])})
    assert admissible.fetch_page(None, 5).items[0]["source"]["completed"] is True

    unarchived = hub_feed({page_url(limit="5"): hub_page([make_hub_entry(key="r2", seq=2, archived=False)])})
    record = unarchived.fetch_page(None, 5).items[0]

    assert record["source"]["completed"] is False
    rejection = reports.normalize(record, config, reports.load_import_schema(config))
    assert rejection.reason == reports.REASON_NOT_ARCHIVED


def test_an_entry_the_feed_mangled_is_translated_and_then_rejected(
    config: evolution.EvolutionConfig,
) -> None:
    """The translation never raises and never drops: a malformed entry has to
    reach the importer, which records the rejection the ledger carries."""
    entry = make_hub_entry(key="r1", seq=1)
    entry["artifacts"] = [item for item in entry["artifacts"] if item["name"] != "semantic_report.json"]
    entry["seq"] = "not-a-sequence"
    feed = hub_feed({page_url(limit="5"): hub_page([entry], next_cursor=1)})

    record = feed.fetch_page(None, 5).items[0]

    assert "semantic_report" not in record["artifacts"]
    assert reports.normalize(record, config, reports.load_import_schema(config)).reason == (
        reports.REASON_MISSING_ARTIFACT
    )


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


# --- the change lineage, as commands -----------------------------------------
#
# The console's other half: `status` says what may be done next, and these are
# the verbs that do it. Two properties are under test here and neither is about
# argparse. The first is that the whole lineage of a batch is reachable from the
# command line alone — no operation exists only in a library caller or only in
# orch-hub. The second is what a run reports: every one of these operations is
# redoable by being run again, so a command that found the work already done has
# to say so rather than claim the decision it read.
#
# The expectation token is the third. A surface that is not the only writer acts
# on a reading somebody else may have moved, and `--expect` is what makes that
# safe: the operation compares it under its own lock, which is the only place the
# answer holds until the write.


@pytest.fixture
def lineage_repo(repo: Path) -> Path:
    """The workspace under Git, which a change lineage needs: an experiment
    freezes a base revision and creates a durable ref at it."""

    git_repo(repo, tag="v2.2.0")
    return repo


def gate(config: evolution.EvolutionConfig, feed_root: Path, *drafts: str) -> str:
    """A frozen batch whose analysis ended, with drafts waiting at the human
    admission gate — the state every verb below acts from."""

    fill_pool(config, feed_root, TARGET)
    frozen = freeze(config)
    batch_id = frozen.batch_id or ""
    close_batch(config, batch_id, frozen.analysis_task_id or "")
    for draft_id in drafts:
        draft(config, batch_id, draft_id)
    return batch_id


def token(config: evolution.EvolutionConfig) -> str:
    """What `status` publishes for a caller about to act on this reading."""

    return phase.describe(config, now=NOW).state_revision


def finish_admitted_tasks(config: evolution.EvolutionConfig) -> None:
    """Every task of the open round completed on this machine, and work
    committed on the experiment's ref — what a seal observes and pins."""

    experiment = lineage.describe(config).current.open_experiment
    for task in experiment.last_round.tasks:
        complete_task(config, task.task_id)
    git_update_ref(config.repo_root, experiment.ref, git_commit(config.repo_root, "candidate work"))


def test_the_whole_change_lineage_is_reachable_from_the_command_line(
    lineage_repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The acceptance for this half of the console: one batch driven from its
    admission gate to its outcome with `aii-2` and nothing else — a grouped
    admission, a round filled, sealed and revised, a draft declined, the attempt
    abandoned, and the batch concluded having changed nothing."""

    config = evolution.load_config(lineage_repo)
    batch_id = gate(config, feed_root, "loader-fallback", "prefetch-injection", "wider-rubric")
    where = ["--repo", str(lineage_repo)]

    code, out, _ = run(["evolution", "create", "loader-fallback", *where], capsys)
    assert code == 0
    assert f"{batch_id}-exp-01 created, round 1" in out
    assert phase.describe(config, now=NOW).phase == phase.PHASE_IMPLEMENTING

    code, out, _ = run(["evolution", "add-tasks", "prefetch-injection", *where], capsys)
    assert code == 0
    assert "already open, round 1" in out and "prefetch-injection" in out

    finish_admitted_tasks(config)
    code, out, _ = run(["evolution", "seal-round", *where], capsys)
    assert code == 0
    assert "round 1 is candidate-ready" in out
    assert "2 task(s) complete" in out
    assert phase.describe(config, now=NOW).phase == phase.PHASE_CANDIDATE_READY

    code, out, _ = run(["evolution", "revise", "--reason", "the loader fallback measured worse", *where], capsys)
    assert code == 0
    assert "round 2 open" in out
    assert phase.describe(config, now=NOW).phase == phase.PHASE_IMPLEMENTING

    code, out, _ = run(["evolution", "reject", "wider-rubric", "--reason", "not this cohort's evidence", *where], capsys)
    assert code == 0
    assert "1 draft(s) declined" in out

    code, out, _ = run(
        ["evolution", "abandon", "--reason", "the approach was wrong", "--experiment", f"{batch_id}-exp-01", *where],
        capsys,
    )
    assert code == 0
    assert f"abandoned: {batch_id}-exp-01, round 2 of {batch_id}" in out
    assert phase.describe(config, now=NOW).phase == phase.PHASE_CONCLUSION_PENDING

    code, out, _ = run(["evolution", "conclude-no-change", "--reason", "no protocol change justified", *where], capsys)
    assert code == 0
    assert f"{batch_id} ended with no change" in out
    assert lineage.describe(config).current is None


def test_supersede_creates_the_successor_and_the_command_names_it(
    lineage_repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The successor is the operation's to create — the CLI has none to allocate
    or name, and what it reports is the one that came back."""

    config = evolution.load_config(lineage_repo)
    batch_id = gate(config, feed_root, "loader-fallback")
    where = ["--repo", str(lineage_repo)]
    run(["evolution", "create", "loader-fallback", *where], capsys)

    code, out, _ = run(
        ["evolution", "supersede", "--reason", "a narrower change answers the same finding", *where], capsys
    )

    assert code == 0
    assert f"{batch_id}-exp-02 (created now)" in out
    assert "empty round 1" in out
    assert lineage.describe(config).current.open_experiment.experiment_id == f"{batch_id}-exp-02"


def test_a_verb_run_again_reports_the_record_rather_than_a_second_decision(
    lineage_repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every operation here is redoable, and the difference between doing a thing
    and finding it done is the whole of what these commands report. A seal run
    twice states the pin on record; a rejection run twice writes nothing."""

    config = evolution.load_config(lineage_repo)
    gate(config, feed_root, "loader-fallback", "wider-rubric")
    where = ["--repo", str(lineage_repo)]
    run(["evolution", "create", "loader-fallback", *where], capsys)
    finish_admitted_tasks(config)

    run(["evolution", "seal-round", *where], capsys)
    code, out, _ = run(["evolution", "seal-round", *where], capsys)
    assert code == 0
    assert "was already candidate-ready" in out
    assert "this run wrote nothing: the pin above is the one on record" in out

    run(["evolution", "reject", "wider-rubric", "--reason", "answered by the loader work", *where], capsys)
    before = snapshot(lineage_repo)
    code, out, _ = run(["evolution", "reject", "wider-rubric", "--reason", "answered by the loader work", *where], capsys)
    assert code == 0
    assert "already declined" in out
    assert "this run wrote nothing" in out
    assert snapshot(lineage_repo) == before


def test_admitting_further_drafts_is_not_the_redo_of_an_admission(
    lineage_repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both leave `created` False and only one of them wrote nothing, so that
    flag cannot be what the report is derived from: `add-tasks` never creates an
    experiment and admits into one every time it is not a redo. The result says
    `recorded` for exactly this, and an adapter that guessed instead would tell an
    operator their drafts were already admitted."""

    config = evolution.load_config(lineage_repo)
    gate(config, feed_root, "loader-fallback", "prefetch-injection")
    where = ["--repo", str(lineage_repo)]
    run(["evolution", "create", "loader-fallback", *where], capsys)

    _, redone, _ = run(["evolution", "create", "loader-fallback", *where], capsys)
    assert "this run wrote nothing" in redone

    _, added, _ = run(["evolution", "add-tasks", "prefetch-injection", *where], capsys)
    assert "this run wrote nothing" not in added
    assert "prefetch-injection" in added
    admitted = {task.draft_id for task in lineage.describe(config).current.open_experiment.last_round.tasks}
    assert admitted == {"loader-fallback", "prefetch-injection"}

    _, again, _ = run(["evolution", "add-tasks", "prefetch-injection", *where], capsys)
    assert "this run wrote nothing" in again


def test_a_verb_refuses_the_reading_another_writer_moved_under(
    lineage_repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """What `--expect` is for. The token describes the state a decision was made
    against; a lifecycle that moved since is a different one, and acting on it is
    what a surface reading a stale console would otherwise do."""

    config = evolution.load_config(lineage_repo)
    gate(config, feed_root, "loader-fallback", "wider-rubric")
    where = ["--repo", str(lineage_repo)]
    stale = token(config)

    # Another writer, between the reading and the verb.
    run(["evolution", "reject", "wider-rubric", "--reason", "answered elsewhere", *where], capsys)
    before = snapshot(lineage_repo)

    code, _, err = run(["evolution", "create", "loader-fallback", "--expect", stale, *where], capsys)

    assert code == 2
    assert f"the evolution records moved since {stale} was read" in err
    assert snapshot(lineage_repo) == before

    code, out, _ = run(["evolution", "create", "loader-fallback", "--expect", token(config), *where], capsys)
    assert code == 0
    assert "created, round 1" in out


def test_a_token_from_another_scheme_is_refused_rather_than_compared(
    lineage_repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """"This does not describe this repository" and "this repository moved" are
    different facts, and a caller reading them as one retries the wrong thing
    forever."""

    config = evolution.load_config(lineage_repo)
    gate(config, feed_root, "loader-fallback")
    where = ["--repo", str(lineage_repo)]

    code, _, err = run(["evolution", "create", "loader-fallback", "--expect", "99-deadbeefdeadbeef", *where], capsys)

    assert code == 2
    assert "is not a state revision this build computes" in err
    assert lineage.describe(config).current.open_experiment is None


def test_a_refused_expectation_stops_before_the_operation_writes_anything(
    lineage_repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The check is first inside the lock, before the preamble that publishes this
    machine's analysis closures — itself a write, and one that would change the
    very digest being compared. So a batch whose analysis finished and whose
    closure record is still owed stays exactly as it was."""

    config = evolution.load_config(lineage_repo)
    fill_pool(config, feed_root, TARGET)
    frozen = freeze(config)
    batch_id = frozen.batch_id or ""
    record_findings(config, batch_id)
    complete_analysis_task(config, frozen.analysis_task_id or "")
    draft(config, batch_id, "loader-fallback")
    closure = config.batches_root / batch_id / batches.CLOSURE_FILENAME
    assert not closure.exists()
    before = snapshot(lineage_repo)

    code, _, err = run(
        ["evolution", "create", "loader-fallback", "--expect", "1-0000000000000000", "--repo", str(lineage_repo)],
        capsys,
    )

    assert code == 2
    assert "the evolution records moved since" in err
    assert not closure.exists()
    assert snapshot(lineage_repo) == before


def test_the_verbs_this_cli_offers_are_the_ones_the_gate_names(
    lineage_repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A command the gate has no verb for is a lifecycle only the console
    believes in, so the names come from `phase.ACTION_*` on both sides and this
    is what holds them together. The other direction — a verb `status` emits that
    is not a command yet — is what remains of the console rather than a defect,
    so it is not asserted here.

    Every one of them also takes the state token and the workspace, which is what
    lets a surface pass the action it read from the gate straight to a command
    line.
    """

    config = evolution.load_config(lineage_repo)
    gate(config, feed_root, "loader-fallback")
    emitted = {item["action"] for item in phase.describe(config, now=NOW).to_json()["allowed_actions"]}

    wired = cli._wired_verbs(phase)

    assert wired
    assert wired <= emitted
    parser = cli.build_parser()
    for verb in sorted(wired):
        parsed = parser.parse_args(["evolution", verb, *_minimal(verb), "--expect", "1-abc", "--repo", "/tmp/x"])
        assert parsed.evolution_command == verb
        assert parsed.expect == "1-abc"


# --- the release, as commands ------------------------------------------------
#
# What the change lineage produces and what is done about it afterwards: a
# candidate carried onto the source line, a run that measured it ended or given
# up, the next cohort's reading of that release, and the gate the next base
# freeze waits on. The same two properties are under test as for the lineage
# verbs — every operation reachable, and a run that found the work already done
# saying so — with one more that belongs to this half alone: a `rolled-back`
# settlement performs the reversal itself, so the console offers one verb where
# an operator might otherwise compose two.


@pytest.fixture
def release(lineage_repo: Path) -> str:
    """The source line, where it stood before anything was promoted."""

    sha = git_rev(lineage_repo, "HEAD")
    git_update_ref(lineage_repo, RELEASE_REF, sha)
    return sha


def measured(config: evolution.EvolutionConfig, feed_root: Path, capsys: pytest.CaptureFixture[str]) -> str:
    """A batch driven to the state a promotion is argued from, by the commands
    that were wired for it: admitted, completed, sealed, and then measured.

    The run is started through the library because the two verbs that ask a
    harness are not wired yet — this console has no way to reach one.
    """

    batch_id = gate(config, feed_root, "loader-fallback")
    where = ["--repo", str(config.repo_root)]
    run(["evolution", "create", "loader-fallback", *where], capsys)
    finish_admitted_tasks(config)
    run(["evolution", "seal-round", *where], capsys)

    harness = FakeHarness(report=completed_report())
    replay.start(config, harness, source_ref=RELEASE_REF, expectation="fewer remediation rounds", now=NOW)
    replay.conclude(config, harness, now=NOW)
    return batch_id


def test_a_promotion_and_its_rollback_are_reachable_from_the_command_line(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The acceptance for this half: the measured candidate goes onto the source
    line and comes back off it with `aii-2` and nothing else. The rollback leaves
    the promotion exactly what it was — a new commit takes the change back out,
    and nothing about the experiment or the batch outcome is edited."""

    config = evolution.load_config(lineage_repo)
    batch_id = measured(config, feed_root, capsys)
    where = ["--repo", str(lineage_repo)]

    code, out, _ = run(
        ["evolution", "promote", "--reason", "the replay showed fewer rounds", "--target", "orch-hub", *where], capsys
    )

    assert code == 0
    assert f"{batch_id}-exp-01 round 1 is on {RELEASE_REF}, ending {batch_id}" in out
    assert "planned" in out and "orch-hub" in out
    assert "this deployed nothing" in out
    promoted = phase.describe(config, now=NOW).last_promotion
    assert git_rev(lineage_repo, RELEASE_REF) == promoted.revision
    assert lineage.describe(config).current is None

    code, out, _ = run(["evolution", "rollback", "--reason", "the next cohort measured worse", *where], capsys)

    assert code == 0
    assert f"is off {RELEASE_REF}" in out
    assert f"{batch_id}-exp-01 stays promoted and the batch stays concluded" in out
    assert lineage.describe(config).last_promoted.promotion_effective is False
    line = git_rev(lineage_repo, RELEASE_REF)
    assert line != promoted.revision

    code, out, _ = run(["evolution", "rollback", "--reason", "the next cohort measured worse", *where], capsys)

    assert code == 0
    assert "this run wrote nothing: the rollback above is the one on record" in out
    assert git_rev(lineage_repo, RELEASE_REF) == line


def test_a_promotion_run_again_reports_the_merge_rather_than_making_a_second(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The redo every operation here has, on the one verb whose Git half cannot
    be taken back by running it twice: what a second run reports is the promotion
    on record, and the source line does not move again."""

    config = evolution.load_config(lineage_repo)
    measured(config, feed_root, capsys)
    where = ["--repo", str(lineage_repo)]
    reason = ["--reason", "the replay showed fewer rounds"]
    run(["evolution", "promote", *reason, "--target", "orch-hub", *where], capsys)
    line = git_rev(lineage_repo, RELEASE_REF)

    code, out, _ = run(["evolution", "promote", *reason, "--target", "orch-hub", *where], capsys)

    assert code == 0
    assert "was already on" in out
    assert "this run wrote nothing: the promotion above is the one on record" in out
    assert git_rev(lineage_repo, RELEASE_REF) == line


def test_a_run_and_a_request_are_ended_from_the_command_line(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The two replay verbs that need no harness, which are the two that exist
    for a harness that cannot answer: a run going is recorded as failed, and a
    request that never became one is given up. Their redo is the difference —
    ending a run reports the failure on record, while a withdrawal over nothing
    outstanding is how it reports having already landed."""

    config = evolution.load_config(lineage_repo)
    gate(config, feed_root, "loader-fallback")
    where = ["--repo", str(lineage_repo)]
    run(["evolution", "create", "loader-fallback", *where], capsys)
    finish_admitted_tasks(config)
    run(["evolution", "seal-round", *where], capsys)
    replay.start(config, FakeHarness(report=None), source_ref=RELEASE_REF, expectation="fewer rounds", now=NOW)

    code, out, _ = run(["evolution", "replay-abandon", "--reason", "the harness host was reclaimed", *where], capsys)

    assert code == 0
    assert "round 1 attempt 1 ended failed" in out
    assert "the harness host was reclaimed" in out

    code, out, _ = run(["evolution", "replay-abandon", "--reason", "the harness host was reclaimed", *where], capsys)
    assert code == 0
    assert "this run wrote nothing: the same failure was already on record" in out

    with pytest.raises(RuntimeError):
        replay.start(config, DeadHarness(), source_ref=RELEASE_REF, expectation="fewer rounds", now=NOW)

    code, out, _ = run(["evolution", "replay-withdraw", *where], capsys)

    assert code == 0
    assert "round 1 attempt 2 given up" in out
    assert "the position stays allocated" in out
    assert "stopping it is done there" in out

    code, out, _ = run(["evolution", "replay-withdraw", *where], capsys)
    assert code == 0
    assert "nothing outstanding" in out


class DeadHarness:
    """A harness that never answers, which is what leaves a request outstanding:
    the request is written before the harness is asked anything, so a start that
    dies here leaves a run that may be going and no record naming it."""

    def start(self, request: object) -> object:
        raise RuntimeError("the harness host was reclaimed before it answered")

    def poll(self, handle: str) -> object | None:
        return None


def assessing(config: evolution.EvolutionConfig, feed_root: Path, capsys: pytest.CaptureFixture[str]) -> str:
    """A promotion on the line and the cohort that owes the reading of it.

    The promotion is made by the command under test in the tests above; here it
    is the state rather than the subject, so the shared cycle makes it — and the
    second cohort's reports state the promoted revision, which is what places
    them after the release.
    """

    line = git_rev(config.repo_root, RELEASE_REF)
    promotion = promote_candidate(
        config,
        batch_id=FIRST,
        drafts=("loader-fallback",),
        at=NOW,
        reports=[_report(f"b{index}", index, f"2026-07-0{index}-task", line) for index in (1, 2)],
    )
    git_follow(config.repo_root, promotion.promotion_revision)
    write_manifest(
        config.batches_root,
        SECOND,
        [],
        analysis_task_id=f"2026-08-10-{SECOND}",
        reports=[
            _report(f"a{index}", index, f"2026-08-0{index}-task", promotion.promotion_revision) for index in (1, 2)
        ],
    )
    return promotion.promotion_revision


def _report(key: str, sequence: int, task_id: str, effective_revision: str) -> dict:
    """One frozen membership entry, stating the revision its target held.

    Which side of the release a report falls on is read from that revision and
    nothing else, so a cohort is placed by what this states rather than by when
    the batch was frozen.
    """

    return make_manifest_report(
        key=key, sequence=sequence, task_id=task_id, effective_revision=effective_revision
    )


def test_the_release_reading_and_its_gate_are_one_verb_each(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """What the cohort after a promotion owes: a reading of that release, and an
    answer to the gate the next base freeze waits on. The quantities are the
    operator's — read off machine-local artifacts nothing here can re-derive —
    and everything else about the reading is the lineage's own."""

    config = evolution.load_config(lineage_repo)
    promoted = assessing(config, feed_root, capsys)
    where = ["--repo", str(lineage_repo)]

    code, out, _ = run(
        [
            "evolution",
            "assess",
            "--verdict",
            assessment.VERDICT_INCONCLUSIVE,
            "--confidence",
            assessment.CONFIDENCE_LOW,
            "--rationale",
            "no frozen manifest states what kind of work either cohort did",
            "--metric",
            "review-rounds:rounds:1.8:1.2:lower",
            *where,
        ],
        capsys,
    )

    assert code == 0
    assert f"reads {promoted[:12]} as {assessment.VERDICT_INCONCLUSIVE}" in out
    assert "review-rounds" in out
    assert "the gate is unanswered" in out
    reading = assessment.read(config, assessment.describe_current(config).batch)
    assert reading.verdict == assessment.VERDICT_INCONCLUSIVE
    assert reading.metrics[0].before == 1.8

    code, out, _ = run(
        ["evolution", "settle", "--settlement", assessment.SETTLEMENT_RETAIN, "--reason", "nothing measured against it", *where],
        capsys,
    )

    assert code == 0
    assert f"settled {assessment.SETTLEMENT_RETAIN}" in out
    assert "the release stays the line the first experiment of this batch freezes its base on" in out
    assert git_rev(lineage_repo, RELEASE_REF) == promoted


def test_a_rolled_back_settlement_runs_the_reversal_itself(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one place this console offers a single verb where an operator might
    compose two. `assessment.settle` sequences the reversal itself — the rollback
    lands before the decision is written, and never after it — so a
    rollback-then-settle sequence of the CLI's own would be a second ordering of
    the same two writes."""

    config = evolution.load_config(lineage_repo)
    promoted = assessing(config, feed_root, capsys)
    where = ["--repo", str(lineage_repo)]
    run(
        [
            "evolution",
            "assess",
            "--verdict",
            assessment.VERDICT_INCONCLUSIVE,
            "--confidence",
            assessment.CONFIDENCE_LOW,
            "--rationale",
            "the cohorts place nothing on either side",
            *where,
        ],
        capsys,
    )

    code, out, _ = run(
        [
            "evolution",
            "settle",
            "--settlement",
            assessment.SETTLEMENT_ROLLED_BACK,
            "--reason",
            "the release is not worth the risk it carries",
            *where,
        ],
        capsys,
    )

    assert code == 0
    assert f"settled {assessment.SETTLEMENT_ROLLED_BACK}" in out
    assert "committed by this settlement" in out
    line = git_rev(lineage_repo, RELEASE_REF)
    assert line != promoted
    assert lineage.describe(config).last_promoted.promotion_effective is False

    code, out, _ = run(
        [
            "evolution",
            "settle",
            "--settlement",
            assessment.SETTLEMENT_ROLLED_BACK,
            "--reason",
            "the release is not worth the risk it carries",
            *where,
        ],
        capsys,
    )
    assert code == 0
    assert "this run wrote nothing: the same answer was already on record" in out
    assert git_rev(lineage_repo, RELEASE_REF) == line


def test_the_counterfactual_is_ended_given_up_and_read_from_the_command_line(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The three verbs that act on the pinned run without asking a harness. Each
    answers a state the harness left: a request it never answered for, a run it
    stopped reporting, and a completed comparison the reading is revised on.

    The runs themselves are started through the library for `measured`'s reason.
    """

    config = evolution.load_config(lineage_repo)
    assessing(config, feed_root, capsys)
    where = ["--repo", str(lineage_repo)]
    reading = [
        "--verdict",
        assessment.VERDICT_INCONCLUSIVE,
        "--confidence",
        assessment.CONFIDENCE_LOW,
        "--rationale",
        "the cohorts place nothing on either side",
    ]
    run(["evolution", "assess", *reading, *where], capsys)

    with pytest.raises(RuntimeError):
        assessment.measure(config, DeadHarness(), expectation="the release converges in fewer rounds", now=NOW)

    code, out, _ = run(["evolution", "assess-withdraw", *where], capsys)
    assert code == 0
    assert "counterfactual attempt 1 given up" in out
    assert "the position stays allocated" in out

    code, out, _ = run(["evolution", "assess-withdraw", *where], capsys)
    assert code == 0
    assert "nothing outstanding" in out

    assessment.measure(config, FakeHarness(report=None), expectation="the release converges in fewer rounds", now=NOW)

    code, out, _ = run(["evolution", "assess-abandon", "--reason", "the harness lost the handle", *where], capsys)
    assert code == 0
    assert "counterfactual attempt 2 ended failed" in out
    assert "another run answers it, from `assess-measure`" in out

    harness = FakeHarness(report=completed_report())
    assessment.measure(config, harness, expectation="the release converges in fewer rounds", now=NOW)
    assessment.conclude(config, harness, now=NOW)

    code, out, _ = run(
        [
            "evolution",
            "assess-resolve",
            "--verdict",
            assessment.VERDICT_IMPROVED,
            "--confidence",
            assessment.CONFIDENCE_HIGH,
            "--rationale",
            "the pinned run measured fewer rounds over one case set",
            *where,
        ],
        capsys,
    )

    assert code == 0
    assert f"reads {git_rev(lineage_repo, RELEASE_REF)[:12]} as {assessment.VERDICT_IMPROVED}" in out
    assert assessment.read(config, assessment.describe_current(config).batch).verdict == assessment.VERDICT_IMPROVED


def test_a_release_verb_refuses_the_reading_another_writer_moved_under(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--expect` reaches this half too, and on the settlement it guards the one
    operation here that composes another: the check is the first statement inside
    the single hold the whole settlement — reversal included — runs under."""

    config = evolution.load_config(lineage_repo)
    assessing(config, feed_root, capsys)
    where = ["--repo", str(lineage_repo)]
    stale = token(config)

    run(
        [
            "evolution",
            "assess",
            "--verdict",
            assessment.VERDICT_INCONCLUSIVE,
            "--confidence",
            assessment.CONFIDENCE_LOW,
            "--rationale",
            "the cohorts place nothing on either side",
            *where,
        ],
        capsys,
    )
    before = snapshot(lineage_repo)

    code, _, err = run(
        [
            "evolution",
            "settle",
            "--settlement",
            assessment.SETTLEMENT_ROLLED_BACK,
            "--reason",
            "the release is not worth the risk it carries",
            "--expect",
            stale,
            *where,
        ],
        capsys,
    )

    assert code == 2
    assert f"the evolution records moved since {stale} was read" in err
    assert snapshot(lineage_repo) == before
    assert git_rev(lineage_repo, RELEASE_REF) == phase.describe(config, now=NOW).last_promotion.revision


def test_a_measurement_the_record_cannot_hold_is_refused_before_anything_runs(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one thing this adapter checks itself: the shape of a `--metric`, which
    is an argument rather than a policy. An empty side is a cohort that measured
    nothing — a different fact from zero, and the reason the fields are not
    simply required — while whether a one-sided quantity may be called better in
    some direction is the record's own rule, asked where every other reader of it
    is."""

    config = evolution.load_config(lineage_repo)
    assessing(config, feed_root, capsys)
    where = ["--repo", str(lineage_repo)]
    reading = [
        "--verdict",
        assessment.VERDICT_INCONCLUSIVE,
        "--confidence",
        assessment.CONFIDENCE_LOW,
        "--rationale",
        "the cohorts place nothing on either side",
    ]

    code, _, err = run(["evolution", "assess", *reading, "--metric", "review-rounds:rounds:1.8:lower", *where], capsys)
    assert code == 2
    assert "NAME:UNIT:BEFORE:AFTER:BETTER" in err

    code, _, err = run(
        ["evolution", "assess", *reading, "--metric", "review-rounds:rounds:some:1.2:lower", *where], capsys
    )
    assert code == 2
    assert "where a measurement holds a number" in err
    assert assessment.read(config, assessment.describe_current(config).batch) is None

    code, out, _ = run(
        ["evolution", "assess", *reading, "--metric", "review-rounds:rounds::1.2:neither", *where], capsys
    )
    assert code == 0
    reading_record = assessment.read(config, assessment.describe_current(config).batch)
    assert reading_record.metrics[0].before is None
    assert reading_record.metrics[0].after == 1.2


def _minimal(verb: str) -> list[str]:
    """The arguments a verb needs to parse — not to run."""

    drafts = ["a-draft"] if verb in ("create", "add-tasks", "reject") else []
    reason = (
        ["--reason", "why"]
        if verb
        in (
            "reject",
            "revise",
            "abandon",
            "supersede",
            "conclude-no-change",
            "promote",
            "rollback",
            "replay-abandon",
            "assess-abandon",
            "settle",
        )
        else []
    )
    reading = (
        ["--verdict", assessment.VERDICT_INCONCLUSIVE, "--confidence", assessment.CONFIDENCE_LOW, "--rationale", "why"]
        if verb in ("assess", "assess-resolve")
        else []
    )
    settlement = ["--settlement", assessment.SETTLEMENT_RETAIN] if verb == "settle" else []
    return drafts + reason + reading + settlement
