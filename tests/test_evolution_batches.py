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
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from evolution_fixtures import ARTIFACT_BODIES, make_record, make_repo, snapshot, write_feed

from ai_native_deployment import evolution
from ai_native_deployment.evolution import analysis_task, batches, ledger, revisions, state

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


def close_batch(config: evolution.EvolutionConfig, batch_id: str) -> None:
    """Record an analysis disposition, which is what closes a batch."""

    (config.batches_root / batch_id / batches.FINDINGS_FILENAME).write_text(
        "# Findings\n\nNo protocol change justified.\n", encoding="utf-8"
    )


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


def test_an_empty_pool_is_not_a_batch(config: evolution.EvolutionConfig) -> None:
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
    assert again.freeze.decision.reason == batches.REASON_OPEN_BATCH
    assert again.freeze.open_batch_id == first.freeze.batch_id
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
    assert resumed.open_batch_id == "evolution-batch-0001"
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


def test_a_new_batch_waits_for_the_open_one_to_record_findings(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, TARGET)
    first = freeze(config)
    close_batch(config, first.batch_id or "")

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
    manifest = read_manifest(first)
    close_batch(config, first.batch_id or "")
    (config.batches_root / (first.batch_id or "")).rename(config.batches_root / "evolution-batch-0009")
    (config.batches_root / "evolution-batch-0009" / batches.MANIFEST_FILENAME).write_text(
        json.dumps({**manifest, "batch_id": "evolution-batch-0009"}), encoding="utf-8"
    )
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


def git_repo(root: Path, *, tag: str | None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    run = ["-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    (root / "file.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), *run, "commit", "-q", "-m", "first"], check=True)
    if tag is not None:
        subprocess.run(["git", "-C", str(root), "tag", tag], check=True)
        (root / "file.txt").write_text("two\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), *run, "commit", "-q", "-m", "candidate work"], check=True)
    return root


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
