"""Admission policy, the immutable batch freeze, and the analysis task it creates.

Everything runs against a temporary repository and a directory-backed feed: no
network, no orch-hub, and no evaluation call — freezing a cohort is a decision
about evidence that already exists.

The temporary config lowers `target_task_count` to 3 and `minimum_task_count` to
2. The policy under test is "target reached", "below minimum", "aged out", not
the specific numbers; `test_evolution_controller` asserts that the shipped
file's real numbers load.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from evolution_fixtures import (
    ARTIFACT_BODIES,
    git_repo,
    make_record,
    make_repo,
    snapshot,
    write_closure,
    write_feed,
    write_manifest,
    write_outcome,
)

from ai_native_deployment import evolution
from ai_native_deployment.evolution import analysis_task, batches, ledger, manifests, revisions, state

TARGET = 3
MINIMUM = 2

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
REVISION = "v2.2.0"


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


def fill_pool(config: evolution.EvolutionConfig, feed_root: Path, count: int, *, extra: list[dict] | None = None):
    """Import `count` distinct completed tasks, plus any extra records given."""

    records = [
        make_record(key=f"r{index}", sequence=index, task_id=f"2026-07-{index:02d}-task")
        for index in range(1, count + 1)
    ]
    feed = write_feed(feed_root, records + (extra or []))
    evolution.sync(config, feed)
    return feed


def backdate(config: evolution.EvolutionConfig, timestamp: str) -> None:
    """Age every pooled task, so the max-wait rule can be exercised without
    waiting for it."""

    persisted = evolution.load_state(config)
    for entry in persisted.pending:
        entry.first_imported_at = timestamp
    evolution.save_state(config, persisted)


def freeze(config: evolution.EvolutionConfig, **kwargs) -> batches.FreezeResult:
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("runner_revision", REVISION)
    return evolution.freeze(config, **kwargs)


def read_manifest(result: batches.FreezeResult) -> dict:
    assert result.manifest_path is not None
    return json.loads(result.manifest_path.read_text(encoding="utf-8"))


def record_findings(config: evolution.EvolutionConfig, batch_id: str) -> Path:
    """Write the disposition record, as an analysis session does — including
    while it is still being written, before the task has completed."""

    path = config.batches_root / batch_id / batches.FINDINGS_FILENAME
    path.write_text("# Findings\n\nNo protocol change justified.\n", encoding="utf-8")
    return path


def complete_analysis_task(config: evolution.EvolutionConfig, task_id: str) -> None:
    """Finish the analysis task the way close-out does: set the terminal status,
    archive the file, and drop its row from the active index."""

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


def set_task_status(config: evolution.EvolutionConfig, task_id: str, status: str) -> None:
    path = analysis_task.task_path(config, task_id)
    text = path.read_text(encoding="utf-8")
    assert "status: pending" in text
    path.write_text(text.replace("status: pending", f"status: {status}", 1), encoding="utf-8")


def relabel_batch(config: evolution.EvolutionConfig, old_id: str, new_id: str) -> None:
    """Move a frozen batch to another id, the way a hand-repair would have to:
    directory, manifest, closure and outcome records, the runtime claims naming
    it, and the generated analysis task. Everything that is resolved against the
    manifest has to come along — the claims, the two records, and the task whose
    identity and manifest path the controller checks before reading its
    lifecycle — so leaving any of them on the old id is corruption rather than a
    relabel."""

    manifest = json.loads((config.batches_root / old_id / batches.MANIFEST_FILENAME).read_text(encoding="utf-8"))
    task_id = manifest.get("analysis_task_id")
    renamed = task_id.replace(old_id, new_id) if task_id else task_id
    (config.batches_root / old_id).rename(config.batches_root / new_id)
    (config.batches_root / new_id / batches.MANIFEST_FILENAME).write_text(
        json.dumps({**manifest, "batch_id": new_id, "analysis_task_id": renamed}), encoding="utf-8"
    )
    closure = config.batches_root / new_id / batches.CLOSURE_FILENAME
    if closure.is_file():
        record = json.loads(closure.read_text(encoding="utf-8"))
        closure.write_text(
            json.dumps({**record, "batch_id": new_id, "analysis_task_id": renamed}), encoding="utf-8"
        )
    outcome = config.batches_root / new_id / manifests.OUTCOME_FILENAME
    if outcome.is_file():
        record = json.loads(outcome.read_text(encoding="utf-8"))
        outcome.write_text(json.dumps({**record, "batch_id": new_id}), encoding="utf-8")
    task = analysis_task.existing_task_path(config, task_id) if task_id else None
    if task is not None:
        text = task.read_text(encoding="utf-8").replace(old_id, new_id)
        task.rename(task.parent / f"{renamed}.md").write_text(text, encoding="utf-8")
    raw = json.loads(config.state_path.read_text(encoding="utf-8"))
    for claim in raw["processed"].values():
        if claim["batch_id"] == old_id:
            claim["batch_id"] = new_id
    config.state_path.write_text(json.dumps(raw), encoding="utf-8")


def close_analysis(config: evolution.EvolutionConfig, batch_id: str) -> None:
    """End a batch's analysis *stage* the way the contract does: the analysis
    task completes, its dispositions are committed as findings, and the next
    controller run publishes the closure record from that completed status.
    Draft findings beside a task that has not completed are not closure — the
    `test_draft_findings_...` cases cover that, here and on another machine.

    The batch stays current afterwards: what ends it is its outcome
    (`conclude_batch`), and everything between the two — the admission gate, the
    experiments — happens inside a batch that is still current (invariant 14)."""

    batch = next(item for item in evolution.load_batches(config) if item.batch_id == batch_id)
    record_findings(config, batch_id)
    if batch.analysis_task_id:
        complete_analysis_task(config, batch.analysis_task_id)
    freeze(config)
    assert (config.batches_root / batch_id / batches.CLOSURE_FILENAME).is_file()


def conclude_batch(config: evolution.EvolutionConfig, batch_id: str) -> None:
    """End the batch itself, which is what releases the next cohort.

    Written directly here: `conclude-no-change` is a guarded operation this
    controller does not implement yet, and the record it will write is what
    every reader already derives currency from."""

    close_analysis(config, batch_id)
    write_outcome(config.batches_root, batch_id)


def closed_elsewhere(config: evolution.EvolutionConfig, batch_id: str, report_keys: list[str], **overrides) -> str:
    """A batch analyzed on another machine: a committed manifest, its findings,
    and the closure record — and none of the machine-local task files."""

    task_id = f"2026-07-31-{batch_id}-analysis"
    write_manifest(config.batches_root, batch_id, report_keys, analysis_task_id=task_id, **overrides)
    record_findings(config, batch_id)
    write_closure(config.batches_root, batch_id, analysis_task_id=task_id)
    return task_id


# --- admission policy --------------------------------------------------------


def test_a_pool_below_the_minimum_creates_no_batch(
    config: evolution.EvolutionConfig, repo: Path, feed_root: Path
) -> None:
    fill_pool(config, feed_root, MINIMUM - 1)
    before = snapshot(repo / "evolution")

    result = freeze(config)

    assert not result.frozen
    assert result.decision.reason == batches.REASON_BELOW_MINIMUM
    assert snapshot(repo / "evolution") == before
    assert not analysis_task.tasks_root(config).exists()
    assert len(evolution.load_state(config).pending) == MINIMUM - 1


def test_an_empty_pool_is_not_a_batch(config: evolution.EvolutionConfig, feed_root: Path) -> None:
    """A drained feed that offered nothing is an empty pool. A repository that
    has never synced has not measured one at all, and says so instead."""
    unmeasured = freeze(config)

    assert not unmeasured.frozen
    assert unmeasured.decision.reason == batches.REASON_POOL_INCOMPLETE

    evolution.sync(config, write_feed(feed_root, []))
    result = freeze(config)

    assert not result.frozen
    assert result.decision.reason == batches.REASON_POOL_EMPTY
    assert result.decision.task_count == 0


def test_the_target_freezes_exactly_one_batch_and_one_analysis_task(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, TARGET)

    result = freeze(config)

    assert result.frozen
    assert result.batch_id == "evolution-batch-0001"
    assert result.decision.trigger == batches.TRIGGER_TARGET
    assert result.decision.forced is False

    manifest = read_manifest(result)
    assert manifest["batch_id"] == "evolution-batch-0001"
    assert manifest["created_at"] == "2026-08-01T12:00:00Z"
    assert manifest["config_sha256"] == config.sha256
    assert manifest["forced"] is False
    assert manifest["force_justification"] is None
    assert manifest["runner_protocol_revision"] == REVISION
    assert manifest["analysis_task_id"] == result.analysis_task_id
    assert len(manifest["reports"]) == TARGET

    assert result.analysis_task_path is not None and result.analysis_task_path.is_file()
    assert [batch.batch_id for batch in evolution.load_batches(config)] == ["evolution-batch-0001"]


def test_an_aged_pool_that_meets_the_minimum_freezes_without_a_force(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Evidence must not age out waiting for a target that may never arrive.
    The wait is configured, so reaching it is policy, not an override."""
    fill_pool(config, feed_root, MINIMUM)
    aged = NOW - timedelta(days=config.batch.max_wait_days + 1)
    backdate(config, aged.isoformat().replace("+00:00", "Z"))

    result = freeze(config)

    assert result.frozen
    assert result.decision.trigger == batches.TRIGGER_MAX_WAIT
    assert result.decision.waited_days == config.batch.max_wait_days + 1
    assert read_manifest(result)["forced"] is False


def test_a_young_pool_between_minimum_and_target_waits(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, MINIMUM)
    backdate(config, "2026-08-01T00:00:00Z")

    result = freeze(config)

    assert not result.frozen
    assert result.decision.reason == batches.REASON_BELOW_TARGET
    assert result.decision.waited_days == 0


@pytest.mark.parametrize(
    ("oldest", "freezes"),
    [
        ("2026-08-01T00:00:00Z", True),
        # A source clock ahead of this one. Refusing is fail-closed: the age rule
        # has no honest answer, and `waited_days` keeps the skew visible.
        ("2026-09-01T00:00:00Z", False),
    ],
)
def test_a_zero_day_wait_releases_the_minimum_but_not_a_future_timestamp(
    repo: Path, feed_root: Path, oldest: str, freezes: bool
) -> None:
    path = repo / "evolution" / "config.toml"
    path.write_text(path.read_text(encoding="utf-8").replace("max_wait_days = 30", "max_wait_days = 0"), encoding="utf-8")
    config = evolution.load_config(repo)
    fill_pool(config, feed_root, MINIMUM)
    backdate(config, oldest)

    result = freeze(config)

    assert result.frozen is freezes
    assert result.decision.trigger == (batches.TRIGGER_MAX_WAIT if freezes else None)


def test_a_forced_batch_records_the_human_justification(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, MINIMUM)

    result = freeze(config, forced=True, justification="Severe review-loop regression; escalated 2026-08-01.")

    assert result.frozen
    assert result.decision.trigger == batches.TRIGGER_FORCED
    manifest = read_manifest(result)
    assert manifest["forced"] is True
    assert manifest["force_justification"] == "Severe review-loop regression; escalated 2026-08-01."


@pytest.mark.parametrize("justification", [None, "", "   "])
def test_a_force_without_a_written_justification_is_refused(
    config: evolution.EvolutionConfig, feed_root: Path, justification: str | None
) -> None:
    """The force path waives the configured target, so it never runs silently."""
    fill_pool(config, feed_root, MINIMUM)

    with pytest.raises(evolution.BatchError):
        freeze(config, forced=True, justification=justification)

    assert evolution.load_batches(config) == []


def test_force_cannot_cross_the_configured_minimum(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """`--force` waives the target. The minimum is a floor no path crosses."""
    fill_pool(config, feed_root, MINIMUM - 1)

    result = freeze(config, forced=True, justification="Urgent.")

    assert not result.frozen
    assert result.decision.reason == batches.REASON_BELOW_MINIMUM
    assert evolution.load_batches(config) == []


def test_a_target_sized_pool_is_not_recorded_as_forced(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Nothing was waived, so the audit must not claim a human overrode anything."""
    fill_pool(config, feed_root, TARGET)

    result = freeze(config, forced=True, justification="Belt and braces.")

    assert result.decision.trigger == batches.TRIGGER_TARGET
    assert read_manifest(result)["forced"] is False
    assert read_manifest(result)["force_justification"] is None


# --- batch membership --------------------------------------------------------


def test_membership_counts_unique_tasks_and_keeps_reruns_as_provenance(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """A rerun is evidence about an already-counted task (invariants 1 and 4):
    it belongs in the manifest and must not raise the task count."""
    rerun = make_record(key="rerun", sequence=99, task_id="2026-07-01-task", evaluation_id="eval-second-pass")
    fill_pool(config, feed_root, TARGET, extra=[rerun])

    result = freeze(config)

    manifest = read_manifest(result)
    assert len(manifest["reports"]) == TARGET + 1
    batch = evolution.load_batches(config)[0]
    assert batch.task_count == TARGET
    assert "rerun" in [report["report_key"] for report in manifest["reports"]]
    # Both reports for the reruns task are present, under the one task identity.
    assert [report["report_key"] for report in manifest["reports"] if report["task_id"] == "2026-07-01-task"] == [
        "r1",
        "rerun",
    ]
    sequences = [report["sequence"] for report in manifest["reports"]]
    assert sequences == sorted(sequences), "membership order is deterministic"


def test_the_manifest_carries_the_provenance_a_cohort_comparison_needs(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Invariant 5 cannot be applied from an ignored runtime directory: the
    committed manifest must say which protocol, evaluator, rubric, and role
    models produced each report."""
    mixed = make_record(
        key="mixed",
        sequence=50,
        task_id="2026-07-20-task",
        runner_protocol_revision="v2.1.0",
        rubric_revision="r6",
        dev_model="claude-sonnet-5",
    )
    fill_pool(config, feed_root, MINIMUM, extra=[mixed])
    hashes = {
        ref.report_key: ref.bundle_sha256
        for entry in evolution.load_state(config).pending
        for ref in (entry.primary, *entry.reruns)
    }

    manifest = read_manifest(freeze(config))

    by_key = {report["report_key"]: report for report in manifest["reports"]}
    assert by_key["mixed"]["provenance"]["runner_protocol_revision"] == "v2.1.0"
    assert by_key["mixed"]["provenance"]["dev"]["model"] == "claude-sonnet-5"
    assert by_key["mixed"]["evaluator"]["rubric_revision"] == "r6"
    assert by_key["r1"]["provenance"]["runner_protocol_revision"] == "v2.2.0"
    assert by_key["r1"]["evaluator"]["rubric_revision"] == "r7"
    assert by_key["r1"]["generated_at"] == "2026-07-30T10:00:00Z"
    # The hash the pool recorded at import, carried through unchanged: it is what
    # ties this membership entry to the bytes that were evaluated.
    assert {key: report["bundle_sha256"] for key, report in by_key.items()} == hashes


def test_a_report_with_no_findings_still_joins_the_batch(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Invariant 2: without the clean reports, no recurrence rate is knowable."""
    bodies = {**ARTIFACT_BODIES, "semantic_report": b'{"layer": "L2", "findings": []}'}
    records = [
        make_record(key=f"clean{index}", sequence=index, task_id=f"2026-07-{index:02d}-task", bodies=bodies)
        for index in range(1, TARGET + 1)
    ]
    evolution.sync(config, write_feed(feed_root, records, bodies=bodies))

    manifest = read_manifest(freeze(config))

    assert len(manifest["reports"]) == TARGET


def test_no_report_body_reaches_a_frozen_manifest(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """The manifest is committed (invariant 11). It names and hashes evidence;
    it never carries it."""
    secret = b'{"layer": "L2", "findings": ["SECRET-TOKEN-abc123"]}'
    bodies = {**ARTIFACT_BODIES, "semantic_report": secret}
    records = [
        make_record(key=f"s{index}", sequence=index, task_id=f"2026-07-{index:02d}-task", bodies=bodies)
        for index in range(1, TARGET + 1)
    ]
    evolution.sync(config, write_feed(feed_root, records, bodies=bodies))

    result = freeze(config)

    assert result.manifest_path is not None
    assert b"SECRET-TOKEN" not in result.manifest_path.read_bytes()
    assert result.analysis_task_path is not None
    assert b"SECRET-TOKEN" not in result.analysis_task_path.read_bytes()


def test_a_staged_bundle_that_no_longer_matches_its_hash_stops_the_freeze(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """The freeze pins content immutably, so it re-reads what it is about to
    pin. A manifest whose hashes describe content nobody has is worse than no
    batch."""
    fill_pool(config, feed_root, TARGET)
    entry = evolution.load_state(config).pending[0]
    staged = config.repo_root / entry.primary.artifacts_path / state.REPORT_JSON_FILENAME
    tampered = json.loads(staged.read_text(encoding="utf-8"))
    tampered["evaluator"]["rubric_revision"] = "r-tampered"
    staged.write_text(json.dumps(tampered), encoding="utf-8")

    with pytest.raises(evolution.BatchError, match="no longer matches"):
        freeze(config)

    assert evolution.load_batches(config) == []
    assert len(evolution.load_state(config).pending) == TARGET


def test_a_missing_staged_bundle_stops_the_freeze(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, TARGET)
    entry = evolution.load_state(config).pending[0]
    (config.repo_root / entry.primary.artifacts_path / state.REPORT_JSON_FILENAME).unlink()

    with pytest.raises(evolution.BatchError, match="staged bundle"):
        freeze(config)

    assert evolution.load_batches(config) == []


@pytest.mark.parametrize("artifact", ["evidence", "static_metrics", "semantic_report", "report_markdown"])
def test_a_changed_artifact_body_stops_the_freeze(
    config: evolution.EvolutionConfig, feed_root: Path, artifact: str
) -> None:
    """The record is a table of hashes, so an artifact edited after import
    leaves it byte-identical while the evidence itself is gone. Both L1 layers
    and both L2 layers are re-verified, because a batch pins all four."""
    fill_pool(config, feed_root, TARGET)
    entry = evolution.load_state(config).pending[0]
    body = config.repo_root / entry.primary.artifacts_path / state.ARTIFACTS_SUBDIR / artifact
    body.write_bytes(body.read_bytes() + b" tampered")

    with pytest.raises(evolution.BatchError, match="no longer match the record"):
        freeze(config)

    assert evolution.load_batches(config) == []
    assert len(evolution.load_state(config).pending) == TARGET


def test_a_deleted_artifact_body_stops_the_freeze(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, TARGET)
    entry = evolution.load_state(config).pending[0]
    (config.repo_root / entry.primary.artifacts_path / state.ARTIFACTS_SUBDIR / "semantic_report").unlink()

    with pytest.raises(evolution.BatchError, match="missing artifact semantic_report"):
        freeze(config)

    assert evolution.load_batches(config) == []


@pytest.mark.parametrize(
    "damage",
    [
        pytest.param({"evaluation_id": "eval-someone-else"}, id="evaluation"),
        pytest.param({"generated_at": "2020-01-01T00:00:00Z"}, id="timestamp"),
        pytest.param({"sequence": 99}, id="sequence"),
    ],
)
def test_a_pool_entry_that_disagrees_with_its_bundle_stops_the_freeze(
    config: evolution.EvolutionConfig, feed_root: Path, damage: dict
) -> None:
    """The bundle hash proves the bundle is intact; it says nothing about
    whether the entry pointing at it still describes the same report. A manifest
    that named one report and pinned another's content would misattribute every
    claim the analysis makes from it."""
    fill_pool(config, feed_root, TARGET)
    raw = json.loads(config.state_path.read_text(encoding="utf-8"))
    raw["pending"][0]["primary"].update(damage)
    config.state_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(evolution.BatchError, match="disagrees with its staged bundle"):
        freeze(config)

    assert evolution.load_batches(config) == []


def test_a_source_task_that_disagrees_with_its_bundle_stops_the_freeze(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, TARGET)
    raw = json.loads(config.state_path.read_text(encoding="utf-8"))
    raw["pending"][0]["task_id"] = "2026-07-99-some-other-task"
    config.state_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(evolution.BatchError, match="disagrees with its staged bundle"):
        freeze(config)


# --- pool transition ---------------------------------------------------------


def test_batched_reports_leave_the_pool_and_load_back_as_claimed(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """A report holds exactly one decision, so this is a move. The round trip is
    what keeps the writer and the state loader in step."""
    fill_pool(config, feed_root, TARGET)
    before = {key for entry in evolution.load_state(config).pending for key in entry.report_keys()}

    result = freeze(config)

    persisted = evolution.load_state(config)
    assert persisted.pending == []
    assert set(persisted.processed) == before
    assert all(claim["batch_id"] == result.batch_id for claim in persisted.processed.values())
    assert all(claim["recorded_at"] == "2026-08-01T12:00:00Z" for claim in persisted.processed.values())
    assert persisted.known_report_keys() == before


def test_a_batched_report_is_never_re_imported(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    feed = fill_pool(config, feed_root, TARGET)
    freeze(config)

    rewound = evolution.load_state(config)
    rewound.cursor = None
    evolution.save_state(config, rewound)
    again = evolution.sync(config, feed)

    assert again.imported == ()
    assert sorted(again.skipped) == ["r1", "r2", "r3"]
    assert evolution.load_state(config).pending == []


def test_a_machine_with_no_runtime_state_does_not_re_import_a_frozen_cohort(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """The state that records the claim is gitignored; the manifest that records
    the membership is committed. So a fresh clone — or this machine after
    `.ai-evolution/` is deleted — must read the manifests, or it re-imports a
    cohort already analyzed and freezes the same unique completed tasks a second
    time (invariants 1-3)."""
    feed = fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    close_analysis(config, result.batch_id or "")
    shutil.rmtree(config.runtime_root)
    shutil.rmtree(analysis_task.tasks_root(config))

    again = evolution.sync(config, feed)

    assert again.imported == ()
    assert sorted(again.skipped) == ["r1", "r2", "r3"]
    assert freeze(config).batch_id is None
    assert [batch.batch_id for batch in evolution.load_batches(config)] == [result.batch_id]


def test_a_clone_still_imports_a_later_evaluation_of_an_already_batched_task(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """The manifests suppress report keys, not tasks: a new evaluation is
    evidence for a later batch (invariant 3), and losing it would silently cap
    what a second cohort can ever measure."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    close_analysis(config, result.batch_id or "")
    shutil.rmtree(config.runtime_root)
    shutil.rmtree(analysis_task.tasks_root(config))

    late = make_record(key="late", sequence=200, task_id="2026-07-01-task", evaluation_id="eval-late")
    again = evolution.sync(config, write_feed(feed_root, [late]))

    assert again.imported == ("late",)


def test_list_reports_a_frozen_cohorts_reports_as_already_decided(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    feed = fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    close_analysis(config, result.batch_id or "")
    shutil.rmtree(config.runtime_root)
    shutil.rmtree(analysis_task.tasks_root(config))

    listed = evolution.list_candidates(config, feed)

    assert {candidate.status for candidate in listed.candidates} == {"known"}
    assert listed.new_task_count == 0


def test_a_later_report_for_a_batched_task_starts_a_new_pool_entry(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Invariant 3: the frozen batch keeps what it named, and a report that
    arrives afterwards is evidence for a later one — not a change to this one."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    manifest_before = read_manifest(result)

    late = make_record(key="late", sequence=200, task_id="2026-07-01-task", evaluation_id="eval-late")
    evolution.sync(config, write_feed(feed_root, [late]))

    persisted = evolution.load_state(config)
    assert [entry.dedup_key for entry in persisted.pending] == [("repo-alpha", "2026-07-01-task")]
    assert persisted.pending[0].primary.report_key == "late"
    assert read_manifest(result) == manifest_before


# --- idempotency and the open-batch guard ------------------------------------


def test_a_repeated_start_neither_freezes_again_nor_creates_a_second_task(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    feed = fill_pool(config, feed_root, TARGET)
    first = evolution.start(config, feed, now=NOW, runner_revision=REVISION)
    assert first.freeze.frozen

    again = evolution.start(config, feed, now=NOW, runner_revision=REVISION)

    assert not again.freeze.frozen
    assert again.freeze.decision.reason == batches.REASON_CURRENT_BATCH
    assert again.freeze.current_batch_id == first.freeze.batch_id
    assert again.freeze.completed == ()
    assert [batch.batch_id for batch in evolution.load_batches(config)] == ["evolution-batch-0001"]
    index_rows = [
        line
        for line in analysis_task.index_path(config).read_text(encoding="utf-8").splitlines()
        if first.freeze.analysis_task_id and first.freeze.analysis_task_id in line
    ]
    assert len(index_rows) == 1
    assert sorted(path.name for path in analysis_task.tasks_root(config).iterdir()) == [
        f"{first.freeze.analysis_task_id}.md",
        analysis_task.INDEX_FILENAME,
    ]


def test_a_restart_completes_a_freeze_interrupted_after_the_manifest(
    config: evolution.EvolutionConfig, feed_root: Path, monkeypatch
) -> None:
    """The manifest lands first because it is the durable membership statement.
    A restart must finish the rest rather than need a repair by hand."""
    fill_pool(config, feed_root, TARGET)

    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt("crash after the manifest")

    monkeypatch.setattr(batches.analysis_task, "write_task", interrupt)
    with pytest.raises(KeyboardInterrupt):
        freeze(config)
    monkeypatch.undo()

    frozen = evolution.load_batches(config)
    assert [batch.batch_id for batch in frozen] == ["evolution-batch-0001"]
    assert not analysis_task.task_exists(config, frozen[0].analysis_task_id or "")

    resumed = freeze(config)

    assert not resumed.frozen
    assert resumed.current_batch_id == "evolution-batch-0001"
    assert set(resumed.completed) == {batches.COMPLETED_TASK, batches.COMPLETED_INDEX}
    assert analysis_task.task_exists(config, frozen[0].analysis_task_id or "")
    assert evolution.load_state(config).pending == []
    assert freeze(config).completed == (), "a second restart has nothing left to complete"


def test_a_restart_completes_a_freeze_interrupted_before_the_state_write(
    config: evolution.EvolutionConfig, feed_root: Path, monkeypatch
) -> None:
    fill_pool(config, feed_root, TARGET)

    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt("crash before the pool transition")

    monkeypatch.setattr(batches, "save_state", interrupt)
    with pytest.raises(KeyboardInterrupt):
        freeze(config)
    monkeypatch.undo()

    assert len(evolution.load_state(config).pending) == TARGET

    resumed = freeze(config)

    assert set(resumed.completed) >= {batches.COMPLETED_STATE, batches.COMPLETED_TASK}
    persisted = evolution.load_state(config)
    assert persisted.pending == []
    assert set(persisted.processed) == evolution.load_batches(config)[0].report_keys


def test_a_restart_keeps_a_report_that_arrived_after_the_manifest_pending(
    config: evolution.EvolutionConfig, feed_root: Path, monkeypatch
) -> None:
    """`start` syncs before it resumes, so a fresh evaluation of an
    already-batched task can land in the pool between the manifest and the state
    commit. It belongs to a later batch (invariant 3), not to the one that never
    named it."""
    feed = fill_pool(config, feed_root, TARGET)

    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt("crash before the pool transition")

    monkeypatch.setattr(batches, "save_state", interrupt)
    with pytest.raises(KeyboardInterrupt):
        freeze(config)
    monkeypatch.undo()

    late = make_record(key="late", sequence=200, task_id="2026-07-01-task", evaluation_id="eval-late")
    write_feed(feed_root, [late])
    resumed = evolution.start(config, feed, now=NOW, runner_revision=REVISION)

    assert batches.COMPLETED_STATE in resumed.freeze.completed
    persisted = evolution.load_state(config)
    assert [entry.dedup_key for entry in persisted.pending] == [("repo-alpha", "2026-07-01-task")]
    assert persisted.pending[0].primary.report_key == "late"
    assert persisted.pending[0].reruns == []
    batch = evolution.load_batches(config)[0]
    assert set(persisted.processed) == set(batch.report_keys)
    assert "late" not in batch.report_keys


def test_a_bounded_partial_drain_freezes_nothing(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """The page bound is drain safety, not policy. Reaching it leaves the pool a
    prefix of the feed, so a batch frozen from it would take its denominator
    from a local pagination limit rather than from the evidence."""
    records = [
        make_record(key=f"r{index}", sequence=index, task_id=f"2026-07-{index:02d}-task")
        for index in range(1, TARGET + 2)
    ]
    feed = write_feed(feed_root, records)

    bounded = evolution.start(config, feed, now=NOW, runner_revision=REVISION, page_size=TARGET, max_pages=1)

    assert not bounded.sync.exhausted
    assert not bounded.freeze.frozen
    assert bounded.freeze.decision.reason == batches.REASON_POOL_INCOMPLETE
    assert evolution.load_batches(config) == []
    assert not analysis_task.tasks_root(config).exists()

    # Nothing was lost: the cursor carries on, and the complete pool freezes.
    drained = evolution.start(config, feed, now=NOW, runner_revision=REVISION)

    assert drained.sync.exhausted
    assert drained.freeze.frozen
    assert evolution.load_batches(config)[0].task_count == TARGET + 1


def test_a_bounded_sync_still_blocks_a_separate_freeze(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """`sync` and `freeze` are separate commands, so the drain bound has to
    outlive the run that hit it. A cursor alone does not say why discovery
    stopped where it did, and a freeze told nothing would take its denominator
    from the page limit — here, exactly the target, which is the case that
    freezes silently."""
    records = [
        make_record(key=f"r{index}", sequence=index, task_id=f"2026-07-{index:02d}-task")
        for index in range(1, TARGET + 2)
    ]
    feed = write_feed(feed_root, records)

    bounded = evolution.sync(config, feed, page_size=TARGET, max_pages=1)

    assert not bounded.exhausted
    assert bounded.pool_size == TARGET, "the pool has reached the target, but only a prefix of the feed was seen"
    assert evolution.load_state(config).feed_exhausted is False

    blocked = freeze(config)

    assert not blocked.frozen
    assert blocked.decision.reason == batches.REASON_POOL_INCOMPLETE
    assert evolution.load_batches(config) == []

    # Draining the feed is what makes the count a denominator.
    evolution.sync(config, feed)

    assert evolution.load_state(config).feed_exhausted is True
    assert freeze(config).frozen
    assert evolution.load_batches(config)[0].task_count == TARGET + 1


def test_a_sync_that_fails_after_a_drain_leaves_no_stale_exhaustion(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """`feed_exhausted` describes the last *completed* discovery, so a run that
    fetched a page and then failed may not leave the previous drain's answer
    standing. That sync saw a report the earlier one never did — it proved the
    pool is a prefix — and a separate freeze reading the stale flag would
    publish exactly the cohort the failure had just invalidated."""
    fill_pool(config, feed_root, TARGET)
    assert evolution.load_state(config).feed_exhausted is True

    records = [
        make_record(key=f"r{index}", sequence=index, task_id=f"2026-07-{index:02d}-task")
        for index in range(1, TARGET + 2)
    ]

    class ArtifactsUnreachable:
        """Serves the listing, fails on the bodies — a transport failure partway
        through a page, which is where an import spends most of its time."""

        def __init__(self, inner) -> None:
            self.inner = inner

        def fetch_page(self, cursor, limit):
            return self.inner.fetch_page(cursor, limit)

        def fetch_artifacts(self, record):
            if record.get("report_key") == f"r{TARGET + 1}":
                raise evolution.FeedError("orch-hub unreachable")
            return self.inner.fetch_artifacts(record)

    with pytest.raises(evolution.FeedError):
        evolution.sync(config, ArtifactsUnreachable(write_feed(feed_root, records)))

    assert json.loads(config.state_path.read_text(encoding="utf-8"))["feed_exhausted"] is False

    blocked = freeze(config)

    assert not blocked.frozen
    assert blocked.decision.reason == batches.REASON_POOL_INCOMPLETE
    assert evolution.load_batches(config) == []

    # Nothing is stuck: the next healthy sync drains the feed, restores the
    # flag, and the batch forms over the whole eligible set.
    healthy = evolution.sync(config, write_feed(feed_root, records))

    assert healthy.exhausted and evolution.load_state(config).feed_exhausted is True
    admitted = freeze(config)
    assert admitted.frozen
    assert evolution.load_batches(config)[0].task_count == TARGET + 1


def test_a_feed_that_cannot_be_reached_at_all_keeps_the_last_drain(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """The other side of the rule. A fetch that fails observed nothing, so the
    pool is still exactly what the last drain measured; retracting there would
    let an unreachable feed block freezing evidence that is already complete."""
    fill_pool(config, feed_root, TARGET)

    class Unreachable:
        def fetch_page(self, cursor, limit):
            raise evolution.FeedError("orch-hub unreachable")

        def fetch_artifacts(self, record):
            raise evolution.FeedError("orch-hub unreachable")

    with pytest.raises(evolution.FeedError):
        evolution.sync(config, Unreachable())

    assert evolution.load_state(config).feed_exhausted is True
    assert freeze(config).frozen


def test_an_open_batch_is_reconciled_before_the_feed_is_contacted(
    config: evolution.EvolutionConfig, feed_root: Path, monkeypatch
) -> None:
    """A frozen manifest whose task never landed is repaired from disk alone.
    Making that wait for a reachable feed would strand the repository on an
    outage that has nothing to do with the repair."""
    feed = fill_pool(config, feed_root, TARGET)

    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt("crash after the manifest")

    monkeypatch.setattr(batches.analysis_task, "write_task", interrupt)
    with pytest.raises(KeyboardInterrupt):
        freeze(config)
    monkeypatch.undo()
    batch = evolution.load_batches(config)[0]
    assert not analysis_task.task_exists(config, batch.analysis_task_id or "")

    class Unreachable:
        def fetch_page(self, cursor, limit):
            raise evolution.FeedError("orch-hub unreachable")

        def fetch_artifacts(self, record):
            raise evolution.FeedError("orch-hub unreachable")

    with pytest.raises(evolution.FeedError):
        evolution.start(config, Unreachable(), now=NOW, runner_revision=REVISION)

    assert analysis_task.task_exists(config, batch.analysis_task_id or "")
    assert evolution.load_state(config).pending == []
    assert set(evolution.load_state(config).processed) == set(batch.report_keys)

    # And the repair is reported once the feed comes back, not repeated.
    resumed = evolution.start(config, feed, now=NOW, runner_revision=REVISION)
    assert resumed.freeze.completed == ()
    assert resumed.freeze.current_batch_id == batch.batch_id


@pytest.mark.parametrize("replacement", ["# partial", "", "---\nid: something-else\n---\n"])
def test_a_generated_task_that_is_not_the_expected_one_is_reported(
    config: evolution.EvolutionConfig, feed_root: Path, replacement: str
) -> None:
    """Whatever sits at the task's path decides whether the batch has an
    analysis task at all, so a freeze may not declare the step complete over a
    truncated or unrelated file."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    path = analysis_task.task_path(config, result.analysis_task_id or "")
    path.write_text(replacement, encoding="utf-8")

    with pytest.raises(evolution.EvolutionError, match="is not the analysis task generated"):
        freeze(config)

    assert path.read_text(encoding="utf-8") == replacement, "a file that may hold a session log is never rewritten"


def test_an_interrupted_index_publish_leaves_the_other_rows_intact(
    config: evolution.EvolutionConfig, feed_root: Path, monkeypatch
) -> None:
    """The active index lists every task, not just this one, so a truncated
    rewrite loses rows belonging to work that has nothing to do with the
    freeze. It is published whole or not at all."""
    fill_pool(config, feed_root, TARGET)
    index = analysis_task.index_path(config)
    index.parent.mkdir(parents=True, exist_ok=True)
    before = "# Active tasks\n\n| Task | Status | Summary |\n|---|---|---|\n| 2026-07-01-other | pending | Unrelated. |\n"
    index.write_text(before, encoding="utf-8")

    real_replace = os.replace

    def crash_on_the_index(source, target):
        if str(target).endswith(analysis_task.INDEX_FILENAME):
            raise KeyboardInterrupt("crash mid-publish")
        return real_replace(source, target)

    monkeypatch.setattr(state.os, "replace", crash_on_the_index)
    with pytest.raises(KeyboardInterrupt):
        freeze(config)
    monkeypatch.undo()

    assert index.read_text(encoding="utf-8") == before
    assert [path.name for path in sorted(index.parent.iterdir()) if path.name.startswith(".")] == []

    resumed = freeze(config)

    assert batches.COMPLETED_INDEX in resumed.completed
    assert "| 2026-07-01-other |" in index.read_text(encoding="utf-8")


def test_a_claimed_and_logged_task_still_counts_as_the_generated_one(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Guards the check above from being so strict that ordinary work on the
    task breaks the next `start`."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    path = analysis_task.task_path(config, result.analysis_task_id or "")
    worked = path.read_text(encoding="utf-8").replace("status: pending", "status: in_progress")
    path.write_text(worked + "\n### 2026-08-02 / session / (in_progress -> in_progress)\n- Done: read the batch.\n", encoding="utf-8")

    resumed = freeze(config)

    assert resumed.completed == ()
    assert resumed.current_batch_id == result.batch_id


def test_a_manifest_member_claimed_by_another_batch_stops_the_reconcile(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Two cohorts believing they own one report is a contradiction this
    controller cannot arbitrate."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    keys = sorted(evolution.load_batches(config)[0].report_keys)
    # A closed batch that also names the report, so the state below still loads.
    closed_elsewhere(config, "evolution-batch-0002", keys)
    raw = json.loads(config.state_path.read_text(encoding="utf-8"))
    raw["processed"][keys[0]]["batch_id"] = "evolution-batch-0002"
    config.state_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(evolution.BatchError, match="claimed by 'evolution-batch-0002'"):
        freeze(config)

    assert read_manifest(result)["batch_id"] == "evolution-batch-0001"


@pytest.mark.parametrize("lost", ["one", "all"])
def test_a_manifest_member_missing_from_the_pool_stops_the_reconcile(
    config: evolution.EvolutionConfig, feed_root: Path, lost: str
) -> None:
    """Claiming it anyway would turn lost evidence into a state that looks
    consistent with the manifest.

    Total loss is loss too. Judging the gap against the rest of the batch made
    the largest hole the one that passed: a state file that kept its cursor and
    lost every claim read as a machine that had never staged the batch, and the
    reconcile recreated the whole membership as if the history were intact."""
    fill_pool(config, feed_root, TARGET)
    freeze(config)
    raw = json.loads(config.state_path.read_text(encoding="utf-8"))
    claimed = sorted(raw["processed"])
    for report_key in claimed[:1] if lost == "one" else claimed:
        del raw["processed"][report_key]
    config.state_path.write_text(json.dumps(raw), encoding="utf-8")
    assert raw["cursor"] is not None, "the state is this machine's own record, not an absent one"

    with pytest.raises(evolution.BatchError, match="neither pending nor claimed"):
        freeze(config)

    assert json.loads(config.state_path.read_text(encoding="utf-8"))["processed"] == raw["processed"]


@pytest.mark.parametrize("status", ["pending", "in_progress", "final_review"])
def test_draft_findings_do_not_close_a_batch_still_being_analyzed(
    config: evolution.EvolutionConfig, feed_root: Path, status: str
) -> None:
    """The analysis task writes its dispositions while it is still being
    developed and reviewed. Reading that draft as completion would release
    unreviewed dispositions to the admission gate (invariant 9), and the batch
    would go on blocking the next cohort with nobody able to act on it."""
    fill_pool(config, feed_root, TARGET)
    first = freeze(config)
    if status != "pending":
        set_task_status(config, first.analysis_task_id or "", status)
    record_findings(config, first.batch_id or "")
    later = [
        make_record(key=f"n{index}", sequence=100 + index, task_id=f"2026-08-{index:02d}-task")
        for index in range(1, TARGET + 1)
    ]
    evolution.sync(config, write_feed(feed_root, later))

    blocked = freeze(config, now=NOW + timedelta(days=1))

    assert not blocked.frozen
    assert blocked.decision.reason == batches.REASON_CURRENT_BATCH
    assert blocked.current_batch_id == first.batch_id
    assert [batch.batch_id for batch in evolution.load_batches(config)] == ["evolution-batch-0001"]

    # Completing the analysis ends the stage, not the batch: the pool keeps
    # accumulating until the batch records an outcome (invariant 14).
    complete_analysis_task(config, first.analysis_task_id or "")
    assert freeze(config, now=NOW + timedelta(days=1)).batch_id is None
    assert batches.batch_awaiting_analysis(config) is None

    write_outcome(config.batches_root, first.batch_id or "")
    assert freeze(config, now=NOW + timedelta(days=1)).batch_id == "evolution-batch-0002"


def test_a_completed_analysis_task_closes_its_batch_before_it_is_archived(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Close-out archives the file, but the terminal status is already the
    answer, so the window between the two is not a false open batch."""
    fill_pool(config, feed_root, TARGET)
    first = freeze(config)
    set_task_status(config, first.analysis_task_id or "", "completed")
    record_findings(config, first.batch_id or "")

    assert batches.batch_awaiting_analysis(config) is None


@pytest.mark.parametrize("status", ["pending", "in_progress", "final_review"])
def test_draft_findings_do_not_close_a_batch_on_another_machine_either(
    config: evolution.EvolutionConfig, feed_root: Path, status: str
) -> None:
    """`.ai-tasks/` is machine-local and ignored, so on every machine but one
    the analysis lifecycle cannot be read at all. Reading that absence as
    completion — as this controller once did — let committed draft findings
    release the next cohort from any fresh clone, while the analysis they came
    from was still pending review."""
    fill_pool(config, feed_root, TARGET)
    first = freeze(config)
    if status != "pending":
        set_task_status(config, first.analysis_task_id or "", status)
    record_findings(config, first.batch_id or "")
    shutil.rmtree(analysis_task.tasks_root(config))

    unfinished = batches.batch_awaiting_analysis(config)

    assert unfinished is not None and unfinished.batch_id == first.batch_id
    assert not (config.batches_root / (first.batch_id or "") / batches.CLOSURE_FILENAME).exists()


def test_a_completed_analysis_closes_its_batch_on_every_machine(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """The other half: closure has to travel with the repository, or every past
    batch would read as open elsewhere and the guard would deadlock. What
    travels is the record the controller publishes from the completed task, not
    the findings the task wrote while it was still being reviewed."""
    fill_pool(config, feed_root, TARGET)
    first = freeze(config)
    record_findings(config, first.batch_id or "")
    complete_analysis_task(config, first.analysis_task_id or "")

    published = freeze(config)

    assert published.closed_batch_ids == (first.batch_id,)
    closure = json.loads((config.batches_root / (first.batch_id or "") / batches.CLOSURE_FILENAME).read_text())
    assert closure == {
        "schema_version": 1,
        "batch_id": first.batch_id,
        "analysis_task_id": first.analysis_task_id,
        "closed_at": "2026-08-01T12:00:00Z",
    }
    assert batches.batch_awaiting_analysis(config) is None
    analyzed = [record for record in ledger.read_records(config) if record["record_type"] == "batch-analyzed"]
    assert analyzed == [
        {
            "record_type": "batch-analyzed",
            "schema_version": 1,
            "recorded_at": "2026-08-01T12:00:00Z",
            "batch_id": first.batch_id,
            "task_id": first.analysis_task_id,
        }
    ]

    # And the next machine, which has none of the task files, agrees.
    shutil.rmtree(analysis_task.tasks_root(config))
    assert batches.batch_awaiting_analysis(config) is None
    assert freeze(config).closed_batch_ids == (), "the record is published once, not on every run"


@pytest.mark.parametrize("location", ["active", "archived"])
@pytest.mark.parametrize(
    "replacement",
    ["---\nid: 2026-01-01-something-else\nstatus: completed\n---\n\n# Not this batch\n", "# partial"],
    ids=["replaced", "truncated"],
)
def test_a_file_that_is_not_the_analysis_task_cannot_close_its_batch(
    config: evolution.EvolutionConfig, feed_root: Path, location: str, replacement: str
) -> None:
    """Closure is read off the analysis task's lifecycle, so the file has to be
    that task before its status means anything. Otherwise any frontmatter saying
    `status: completed` at the path — or, in the archive, any file at all, since
    location alone is completion there — releases the guard and gets attested
    into the committed record every other machine trusts."""
    fill_pool(config, feed_root, TARGET)
    first = freeze(config)
    record_findings(config, first.batch_id or "")
    path = analysis_task.task_path(config, first.analysis_task_id or "")
    if location == "archived":
        archived = analysis_task.archived_task_path(config, first.analysis_task_id or "")
        archived.parent.mkdir(parents=True, exist_ok=True)
        path.rename(archived)
        path = archived
    path.write_text(replacement, encoding="utf-8")

    with pytest.raises(evolution.EvolutionError, match="is not the analysis task generated"):
        batches.batch_awaiting_analysis(config)
    with pytest.raises(evolution.EvolutionError, match="is not the analysis task generated"):
        freeze(config)

    assert not (config.batches_root / (first.batch_id or "") / batches.CLOSURE_FILENAME).exists()
    assert path.read_text(encoding="utf-8") == replacement, "a file that may hold a session log is never rewritten"


def test_an_unfinished_local_task_holds_a_batch_open_against_its_closure_record(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Local lifecycle may contradict the committed record only by being more
    conservative than it: every machine agrees a batch is closed, or is the one
    still working on it."""
    fill_pool(config, feed_root, TARGET)
    first = freeze(config)
    record_findings(config, first.batch_id or "")
    write_closure(
        config.batches_root,
        first.batch_id or "",
        analysis_task_id=first.analysis_task_id or "",
    )
    set_task_status(config, first.analysis_task_id or "", "in_progress")

    unfinished = batches.batch_awaiting_analysis(config)

    assert unfinished is not None and unfinished.batch_id == first.batch_id


def test_a_closure_record_that_contradicts_its_manifest_is_refused(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """The record is what every other machine trusts, so one that names another
    batch or another task releases the next cohort on evidence of something
    else entirely."""
    fill_pool(config, feed_root, TARGET)
    first = freeze(config)
    shutil.rmtree(analysis_task.tasks_root(config))
    record_findings(config, first.batch_id or "")
    path = config.batches_root / (first.batch_id or "") / batches.CLOSURE_FILENAME

    for record, message in (
        ({"batch_id": "evolution-batch-0009"}, "cannot close another"),
        ({"analysis_task_id": "2026-01-01-something-else"}, "names"),
        ({"closed_at": "1 August 2026"}, "does not match its schema"),
        ({"schema_version": 2}, "does not match its schema"),
    ):
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "batch_id": first.batch_id,
                    "analysis_task_id": first.analysis_task_id,
                    "closed_at": "2026-08-01T12:00:00Z",
                    **record,
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(evolution.EvolutionError, match=message):
            batches.batch_awaiting_analysis(config)


@pytest.mark.parametrize("named", [{}, {"analysis_task_id": None}], ids=["absent", "null"])
def test_a_closure_record_needs_a_manifest_that_names_its_task(
    config: evolution.EvolutionConfig, named: dict
) -> None:
    """A closure attests that one named task completed. Beside a manifest that
    names no analysis task there is nothing to attest to, and the controller
    could not have written the record — so accepting it means trusting an
    unverifiable file to release the next cohort, which is what skipping the
    identity check on a null `analysis_task_id` did."""
    write_manifest(config.batches_root, "evolution-batch-0001", ["old1"], **named)
    record_findings(config, "evolution-batch-0001")
    write_closure(config.batches_root, "evolution-batch-0001", analysis_task_id="2026-07-31-unrelated-analysis")

    with pytest.raises(evolution.BatchError, match="names no analysis task"):
        batches.batch_awaiting_analysis(config)


def test_an_open_batch_that_names_no_analysis_task_is_reported(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Otherwise the batch blocks every later freeze while nothing says which
    task is supposed to close it."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    assert result.manifest_path is not None
    manifest = {key: value for key, value in read_manifest(result).items() if key != "analysis_task_id"}
    result.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(evolution.BatchError, match="names no analysis_task_id"):
        freeze(config)


def test_a_new_batch_waits_for_the_current_one_to_record_its_outcome(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Invariant 14: the next cohort forms once the batch before it has
    concluded — not once its analysis finished, which is a stage inside it."""
    fill_pool(config, feed_root, TARGET)
    first = freeze(config)
    conclude_batch(config, first.batch_id or "")

    later = [
        make_record(key=f"n{index}", sequence=100 + index, task_id=f"2026-08-{index:02d}-task")
        for index in range(1, TARGET + 1)
    ]
    evolution.sync(config, write_feed(feed_root, later))
    second = freeze(config, now=NOW + timedelta(days=1))

    assert second.frozen
    assert second.batch_id == "evolution-batch-0002"
    assert [batch.batch_id for batch in evolution.load_batches(config)] == [
        "evolution-batch-0001",
        "evolution-batch-0002",
    ]
    assert second.analysis_task_id != first.analysis_task_id


def test_two_open_batches_are_refused_rather_than_arbitrated(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, TARGET)
    first = freeze(config)
    hand_made = config.batches_root / "evolution-batch-0007"
    hand_made.mkdir()
    (hand_made / batches.MANIFEST_FILENAME).write_text(
        json.dumps({**read_manifest(first), "batch_id": "evolution-batch-0007"}), encoding="utf-8"
    )

    with pytest.raises(evolution.BatchError, match="more than one open"):
        freeze(config)


def test_allocation_counts_from_the_highest_id_ever_used(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Counting existing directories instead would reuse the id of a batch that
    was moved away, attaching new evidence to an old cohort's name."""
    fill_pool(config, feed_root, TARGET)
    first = freeze(config)
    conclude_batch(config, first.batch_id or "")
    relabel_batch(config, first.batch_id or "", "evolution-batch-0009")
    later = [
        make_record(key=f"n{index}", sequence=100 + index, task_id=f"2026-08-{index:02d}-task")
        for index in range(1, TARGET + 1)
    ]
    evolution.sync(config, write_feed(feed_root, later))

    second = freeze(config, now=NOW + timedelta(days=1))

    assert second.batch_id == "evolution-batch-0010"


@pytest.mark.parametrize(
    ("name", "contents"),
    [
        ("not-a-batch", {batches.MANIFEST_FILENAME: "{}"}),
        ("evolution-batch-0001", {}),
        ("evolution-batch-0001", {batches.MANIFEST_FILENAME: "{ not json"}),
        ("evolution-batch-0001", {batches.MANIFEST_FILENAME: '{"schema_version": 1}'}),
    ],
)
def test_an_unreadable_batch_directory_fails_closed(
    config: evolution.EvolutionConfig, name: str, contents: dict[str, str]
) -> None:
    """Skipping one would let the next allocation reuse an id a manifest already
    claims, and silently drop an open batch from the guard."""
    directory = config.batches_root / name
    directory.mkdir(parents=True)
    for filename, text in contents.items():
        (directory / filename).write_text(text, encoding="utf-8")

    with pytest.raises(evolution.EvolutionError):
        evolution.load_batches(config)


def test_a_manifest_that_claims_another_batchs_id_is_refused(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    assert result.manifest_path is not None
    result.manifest_path.write_text(
        json.dumps({**read_manifest(result), "batch_id": "evolution-batch-0042"}), encoding="utf-8"
    )

    with pytest.raises(evolution.BatchError, match="cannot name another batch"):
        evolution.load_batches(config)


def test_staging_residue_from_an_interrupted_freeze_is_inert(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    config.batches_root.mkdir(parents=True, exist_ok=True)
    (config.batches_root / ".staging-abc123").mkdir()
    fill_pool(config, feed_root, TARGET)

    result = freeze(config)

    assert result.batch_id == "evolution-batch-0001"


# --- manifest versions -------------------------------------------------------


def test_a_version_1_manifest_keeps_its_read_path(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """A frozen manifest is immutable, so the schema it was written against is
    too. Version 2 added the cohort provenance invariants 4 and 5 need; a
    version-1 manifest cannot grow those fields, so it keeps its own schema and
    stays readable rather than becoming a batch nothing can load."""
    closed_elsewhere(config, "evolution-batch-0001", ["old1", "old2"], version=1)

    loaded = evolution.load_batches(config)

    assert [batch.schema_version for batch in loaded] == [1]
    assert loaded[0].task_count == 2
    assert loaded[0].report_keys == {"old1", "old2"}
    assert batches.batch_awaiting_analysis(config) is None

    # And a new batch is written at the current version, beside the old one.
    write_outcome(config.batches_root, "evolution-batch-0001")
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)

    assert read_manifest(result)["schema_version"] == batches.BATCH_SCHEMA_VERSION == 2
    assert [batch.schema_version for batch in evolution.load_batches(config)] == [1, 2]


def test_a_version_1_manifest_still_guards_the_open_batch(
    config: evolution.EvolutionConfig,
) -> None:
    """Also the fresh-clone shape: the committed manifest is here and the
    ignored runtime state it was frozen from is not, which is a batch this
    machine never staged rather than one whose evidence it lost."""
    write_manifest(
        config.batches_root,
        "evolution-batch-0001",
        ["old1"],
        version=1,
        analysis_task_id="2026-07-31-evolution-batch-0001-analysis",
    )
    assert not config.state_path.exists()

    result = freeze(config)

    assert not result.frozen
    assert result.decision.reason == batches.REASON_CURRENT_BATCH
    assert batches.COMPLETED_TASK in result.completed
    assert analysis_task.task_exists(config, "2026-07-31-evolution-batch-0001-analysis")
    # The old batch's report is recorded as claimed, so a later sync cannot pool
    # evidence a frozen cohort already owns.
    assert evolution.load_state(config).processed["old1"]["batch_id"] == "evolution-batch-0001"


@pytest.mark.parametrize("version", [0, 3, "1", None])
def test_a_manifest_version_this_build_cannot_read_fails_closed(
    config: evolution.EvolutionConfig, version: object
) -> None:
    """Guessing at an unknown version would either misread a future manifest or
    silently drop it from the id allocation and the open-batch guard."""
    directory = write_manifest(config.batches_root, "evolution-batch-0001", ["r1"])
    manifest = json.loads((directory / batches.MANIFEST_FILENAME).read_text(encoding="utf-8"))
    (directory / batches.MANIFEST_FILENAME).write_text(
        json.dumps({**manifest, "schema_version": version}), encoding="utf-8"
    )

    with pytest.raises(evolution.BatchError, match="unsupported batch manifest schema_version"):
        evolution.load_batches(config)


# --- the generated analysis task ---------------------------------------------


def frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "---"
    end = lines.index("---", 1)
    fields = {}
    for line in lines[1:end]:
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def test_the_generated_task_conforms_to_the_taskfile_schema(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, TARGET)

    result = freeze(config)

    path = result.analysis_task_path
    assert path is not None
    fields = frontmatter(path)
    assert fields["id"] == result.analysis_task_id == f"2026-08-01-{result.batch_id}-analysis"
    assert fields["status"] == "pending"
    assert fields["session-est"] == "0/1"
    assert fields["blockers"] == "[]"
    assert fields["claimed-by"] == ""
    assert fields["prefetch"] == "[.ai/features.md, .ai/modules.md]"

    body = path.read_text(encoding="utf-8")
    for heading in ("## Goal", "## Scope", "## Acceptance", "## Session log"):
        assert heading in body


def test_the_generated_task_cites_the_contract_batch_revision_and_boundaries(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """A cold analysis session learns all of this from the task file or not at
    all."""
    fill_pool(config, feed_root, TARGET)

    result = freeze(config)
    assert result.analysis_task_path is not None
    body = result.analysis_task_path.read_text(encoding="utf-8")

    assert analysis_task.CONTRACT_PATH in body
    assert result.batch_id is not None and result.batch_id in body
    assert f"evolution/batches/{result.batch_id}/manifest.json" in body
    assert f"evolution/batches/{result.batch_id}/{analysis_task.PROPOSED_TASKS_DIRNAME}" in body
    assert f"evolution/batches/{result.batch_id}/findings.md" in body
    assert REVISION in body
    assert config.sha256 in body
    assert "must not edit `canonical/`" in body
    assert ".ai-evolution/imported-artifacts" in body


def test_the_generated_task_states_an_unknown_runner_revision_explicitly(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """A checkout with no release tag has no stable revision to name; the task
    says so rather than borrowing the candidate it happens to sit on."""
    fill_pool(config, feed_root, TARGET)

    result = evolution.freeze(config, now=NOW)

    assert result.analysis_task_path is not None
    assert "unknown — no release tag" in result.analysis_task_path.read_text(encoding="utf-8")
    assert read_manifest(result)["runner_protocol_revision"] is None


def test_a_forced_batch_carries_its_justification_into_the_task(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, MINIMUM)

    result = freeze(config, forced=True, justification="Escalated safety regression.")

    assert result.analysis_task_path is not None
    assert "Escalated safety regression." in result.analysis_task_path.read_text(encoding="utf-8")


def test_the_task_estimate_grows_with_the_batch(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, analysis_task.TASKS_PER_SESSION + 1)

    result = freeze(config)

    assert result.analysis_task_path is not None
    assert frontmatter(result.analysis_task_path)["session-est"] == "0/2"


def test_the_index_row_names_the_task_and_its_batch(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, TARGET)

    result = freeze(config)

    assert result.analysis_task_id is not None and result.batch_id is not None
    rows = [
        line
        for line in analysis_task.index_path(config).read_text(encoding="utf-8").splitlines()
        if line.startswith("|") and result.analysis_task_id in line
    ]
    assert len(rows) == 1
    assert rows[0].startswith(f"| {result.analysis_task_id} | pending |")
    assert result.batch_id in rows[0]


def test_the_index_row_joins_an_existing_active_table(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """The row is appended to the operator's real index, so an existing active
    task must survive it."""
    existing = "# Active tasks\n\n| Task | Status | Summary |\n|---|---|---|\n| 2026-01-01-other | pending | Other. |\n"
    analysis_task.tasks_root(config).mkdir(parents=True)
    analysis_task.index_path(config).write_text(existing, encoding="utf-8")
    fill_pool(config, feed_root, TARGET)

    result = freeze(config)

    text = analysis_task.index_path(config).read_text(encoding="utf-8")
    assert "| 2026-01-01-other | pending | Other. |" in text
    assert text.startswith("# Active tasks")
    assert text.splitlines()[-1].startswith(f"| {result.analysis_task_id} |")


def test_the_index_row_replaces_a_none_placeholder(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    analysis_task.tasks_root(config).mkdir(parents=True)
    analysis_task.index_path(config).write_text("# Active tasks\n\n(none)\n", encoding="utf-8")
    fill_pool(config, feed_root, TARGET)

    result = freeze(config)

    text = analysis_task.index_path(config).read_text(encoding="utf-8")
    assert "(none)" not in text
    assert "| Task | Status | Summary |" in text
    assert f"| {result.analysis_task_id} | pending |" in text


def test_the_freeze_writes_no_draft_change_task_into_the_active_pool(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Invariant 9: only the analysis task — which cannot edit `canonical/` — is
    created by automation. Change proposals wait as drafts for a human."""
    fill_pool(config, feed_root, TARGET)

    result = freeze(config)

    written = sorted(path.name for path in analysis_task.tasks_root(config).rglob("*") if path.is_file())
    assert written == sorted([analysis_task.INDEX_FILENAME, f"{result.analysis_task_id}.md"])
    assert not (config.batches_root / (result.batch_id or "") / analysis_task.PROPOSED_TASKS_DIRNAME).exists()


def test_an_existing_task_id_stops_the_freeze_instead_of_overwriting_it(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """A task file may already carry a session log."""
    fill_pool(config, feed_root, TARGET)
    taken = analysis_task.task_path(config, "2026-08-01-evolution-batch-0001-analysis")
    taken.parent.mkdir(parents=True)
    taken.write_text("# Not mine\n", encoding="utf-8")

    with pytest.raises(evolution.BatchError, match="already exists"):
        freeze(config)

    assert taken.read_text(encoding="utf-8") == "# Not mine\n"
    assert evolution.load_batches(config) == []


def test_an_archived_analysis_task_is_never_recreated(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Recreating a completed analysis as pending would reopen a closed
    decision."""
    fill_pool(config, feed_root, TARGET)
    archived = analysis_task.archived_task_path(config, "2026-08-01-evolution-batch-0001-analysis")
    archived.parent.mkdir(parents=True)
    archived.write_text("# Completed\n", encoding="utf-8")

    with pytest.raises(evolution.BatchError, match="already exists"):
        freeze(config)


# --- audit -------------------------------------------------------------------


def test_the_freeze_appends_one_bounded_audit_record(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    feed = fill_pool(config, feed_root, TARGET)

    result = evolution.start(config, feed, now=NOW, runner_revision=REVISION)
    evolution.start(config, feed, now=NOW, runner_revision=REVISION)

    frozen = [record for record in ledger.read_records(config) if record["record_type"] == batches.RECORD_BATCH_FROZEN]
    assert len(frozen) == 1
    assert frozen[0] == {
        "record_type": batches.RECORD_BATCH_FROZEN,
        "schema_version": 1,
        "recorded_at": "2026-08-01T12:00:00Z",
        "batch_id": result.freeze.batch_id,
        "task_id": result.freeze.analysis_task_id,
        "revision": REVISION,
        "detail": batches.TRIGGER_TARGET,
    }


def test_the_audit_names_the_trigger_that_formed_the_batch(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, MINIMUM)

    freeze(config, forced=True, justification="Escalated.")

    frozen = [record for record in ledger.read_records(config) if record["record_type"] == batches.RECORD_BATCH_FROZEN]
    assert frozen[-1]["detail"] == batches.TRIGGER_FORCED
    assert "Escalated." not in json.dumps(frozen[-1])


# --- runner revision ---------------------------------------------------------


def test_the_runner_revision_is_the_release_tag_not_the_branch_tip(tmp_path: Path) -> None:
    """Invariant 8: a candidate revision never governs the run that creates it,
    so the commit on top of the release must not become the runner revision."""
    root = git_repo(tmp_path / "tagged", tag="v2.2.0")

    assert revisions.release_line_revision(root) == "v2.2.0"


def test_a_checkout_with_no_release_tag_has_no_runner_revision(tmp_path: Path) -> None:
    assert revisions.release_line_revision(git_repo(tmp_path / "untagged", tag=None)) is None


def test_a_directory_inside_another_repository_does_not_borrow_its_revision(tmp_path: Path) -> None:
    """Otherwise a temporary directory under someone's checkout silently
    acquires that checkout's release line."""
    outer = git_repo(tmp_path / "outer", tag="v9.9.9")
    inner = outer / "nested"
    inner.mkdir()

    assert revisions.release_line_revision(inner) is None


def test_a_path_that_is_not_a_repository_has_no_runner_revision(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    assert revisions.release_line_revision(plain) is None
