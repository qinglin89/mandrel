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
    RELEASE_REF,
    at_line,
    complete_task,
    experiment_decision,
    experiment_round,
    git_canonical_commit,
    git_commit,
    git_release_commit,
    git_repo,
    git_rev,
    git_unreadable_canonical_commit,
    git_update_ref,
    make_manifest_report,
    make_record,
    make_repo,
    releasing_from,
    snapshot,
    write_closure,
    write_experiment,
    write_feed,
    write_manifest,
    write_outcome,
)

from mandrel import evolution
from mandrel.evolution import analysis_task, batches, ledger, manifests, proposals, render, revisions, state

TARGET = 3
MINIMUM = 2

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
RELEASE_TAG = "v2.2.0"


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
    # Git-backed, because a freeze records where the configured source line
    # stands: without the line there is no revision to record, and every
    # manifest here would state the absence instead of the fact.
    return git_repo(root, tag=RELEASE_TAG)


@pytest.fixture
def config(repo: Path) -> evolution.EvolutionConfig:
    return evolution.load_config(repo)


def runner_revision(config: evolution.EvolutionConfig) -> str:
    """What a formation taken now records as this batch's base: where the
    configured source line stands, read the way a test states a fact rather than
    the way the code derives one. It is also every experiment of that batch's
    base, so a hand-written experiment record has to state it too
    (`lineage._require_one_base`)."""

    return git_rev(config.repo_root, RELEASE_REF)


@pytest.fixture
def feed_root(tmp_path: Path) -> Path:
    return tmp_path / "feed"


def fill_pool(
    config: evolution.EvolutionConfig,
    feed_root: Path,
    count: int,
    *,
    extra: list[dict] | None = None,
    at: str | None = None,
):
    """Import `count` distinct completed tasks, plus any extra records given.

    Every record states this repository's source-line tip as the revision it was
    produced at, so the pool is one a batch can be formed out of: membership is
    equality of deployable canonical identity against the base
    (`at_line`). A suite about the identity test itself says otherwise
    explicitly.
    """

    records = [
        make_record(key=f"r{index}", sequence=index, task_id=f"2026-07-{index:02d}-task")
        for index in range(1, count + 1)
    ]
    feed = write_feed(feed_root, at_line(config, records + (extra or []), revision=at))
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
    """Offer the batch, then fix it — the two commands a human runs.

    The formation is two operations, so a test that means "form a cohort out of
    this pool" has to do both. Where the split itself is what is under test, the
    two are called apart.
    """

    kwargs.setdefault("now", NOW)
    evolution.build_proposal(config, now=kwargs["now"])
    return evolution.freeze(config, **kwargs)


def freeze_only(config: evolution.EvolutionConfig, **kwargs) -> batches.FreezeResult:
    """The freeze command by itself, with no rebuild before it.

    What an operator runs to finish an interrupted formation or to fix an offer
    that already stands — and the call any test about repair or closure has to
    make, since building a proposal repairs and closes first and would take the
    step being measured.
    """

    kwargs.setdefault("now", NOW)
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
    """Finish the analysis task the way close-out does — the shared close-out
    shape, since the lifecycle arc reads the same completion for a round's
    seal."""

    complete_task(config, task_id)


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

    The record is written directly rather than through `conclude-no-change`,
    which does now write one: what the freeze reads is the record, and going
    through the operation would make these cases depend on its own guards. The
    two are exercised together in the lifecycle suite."""

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
    assert manifest["runner_protocol_revision"] == runner_revision(config)
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


def test_a_report_produced_at_the_same_protocol_is_a_member_whatever_its_commit(
    config: evolution.EvolutionConfig, repo: Path, feed_root: Path
) -> None:
    """Membership is the deployable canonical identity the base carries, never
    commit equality.

    The source line moves for work under `mandrel/`, `tests/` and `docs/` that no
    target ever receives, so a report produced one ordinary commit off the base
    was produced under the same protocol — and a test on commit strings would
    throw away evidence that belongs to the cohort.
    """
    produced = git_commit(repo, "work no target receives")
    assert produced != runner_revision(config)
    fill_pool(config, feed_root, TARGET, at=produced)

    offered = evolution.build_proposal(config, now=NOW).proposal

    assert offered is not None
    assert offered.task_count == TARGET
    assert offered.not_selected == ()
    assert {member.mode for member in offered.selected} == {manifests.MODE_AUTO}


def test_a_report_produced_at_another_protocol_is_left_out_with_that_reason(
    config: evolution.EvolutionConfig, repo: Path, feed_root: Path
) -> None:
    """A commit whose deployed canonical tree differs is a different protocol, so
    the reports produced under it are evidence about something else."""
    elsewhere = git_canonical_commit(repo, "a different dev contract")
    fill_pool(config, feed_root, 1)
    fill_pool(config, feed_root, 1, extra=[], at=elsewhere)
    other = make_record(key="other", sequence=90, task_id="2026-09-01-task")
    evolution.sync(config, write_feed(feed_root, at_line(config, [other], revision=elsewhere)))

    offered = evolution.build_proposal(config, now=NOW).proposal

    assert offered is not None
    assert [item.task_id for item in offered.not_selected] == ["2026-09-01-task"]
    assert offered.not_selected[0].reason == manifests.REASON_IDENTITY_DIFFERS
    assert offered.considered == offered.task_count + 1


def test_the_three_ways_a_revision_fails_are_three_different_records(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """An absent revision, a stated non-commit and a commit this clone cannot
    read are different facts with different next moves: nothing to derive from, a
    record every clone refuses identically, and a fetch away.
    """
    stated = {
        "2026-08-01-absent": None,
        "2026-08-02-malformed": "HEAD",
        "2026-08-03-unresolvable": "f" * 40,
    }
    records = [
        make_record(key=f"x{index}", sequence=50 + index, task_id=task_id)
        for index, task_id in enumerate(stated, start=1)
    ]
    fill_pool(config, feed_root, 1)
    evolution.sync(
        config,
        write_feed(
            feed_root,
            [
                {**record, "provenance": {**record["provenance"], "effective_revision": revision}}
                for record, revision in zip(records, stated.values())
            ],
        ),
    )

    offered = evolution.build_proposal(config, now=NOW).proposal

    assert offered is not None
    assert {item.task_id: item.reason for item in offered.not_selected} == {
        "2026-08-01-absent": manifests.REASON_REVISION_ABSENT,
        "2026-08-02-malformed": manifests.REASON_REVISION_MALFORMED,
        "2026-08-03-unresolvable": manifests.REASON_REVISION_UNRESOLVABLE,
    }
    assert [member.task_id for member in offered.selected] == ["2026-07-01-task"]


def two_evaluations(
    config: evolution.EvolutionConfig,
    feed_root: Path,
    *,
    task_id: str,
    first: str | None,
    second: str | None,
) -> None:
    """Import two evaluations of one completed task, produced at two revisions.

    Two syncs rather than one, in the given order: the pool takes the highest
    sequence it has seen as the entry's primary and the feed cursor never goes
    back, so this is the arrival order a real pool has. `second` ends up the
    primary and `first` its rerun. `None` is a report whose provenance states no
    revision at all.
    """

    for key, sequence, revision in (("first-pass", 10, first), ("second-pass", 20, second)):
        record = make_record(
            key=key,
            sequence=sequence,
            task_id=task_id,
            evaluation_id=f"eval-{key}",
            effective_revision=revision,
        )
        evolution.sync(config, write_feed(feed_root, [record]))


def test_an_off_base_rerun_keeps_its_task_out_of_the_batch(
    config: evolution.EvolutionConfig, repo: Path, feed_root: Path
) -> None:
    """A member's whole evidence set is frozen into the manifest under the task's
    one decision, so the decision has to be true of every report it owns.

    Deciding from the latest evaluation alone would stamp `auto` on a report
    produced under another protocol — a claim about that report nobody derived,
    inside the immutable record a cohort's coherence claim is read from.
    """
    elsewhere = git_canonical_commit(repo, "a different dev contract")
    fill_pool(config, feed_root, TARGET)
    two_evaluations(
        config, feed_root, task_id="2026-08-01-task", first=elsewhere, second=runner_revision(config)
    )

    manifest = read_manifest(freeze(config))

    assert manifest["selection"]["not_selected"] == [
        {
            "repo_id": "repo-alpha",
            "task_id": "2026-08-01-task",
            "report_keys": ["first-pass", "second-pass"],
            "reason": manifests.REASON_IDENTITY_DIFFERS,
            "actor": None,
            "moved_at": None,
        }
    ]
    assert {report["report_key"] for report in manifest["reports"]}.isdisjoint({"first-pass", "second-pass"})


def test_the_order_two_disagreeing_evaluations_arrived_in_does_not_decide_membership(
    config: evolution.EvolutionConfig, repo: Path, feed_root: Path
) -> None:
    """The same task and the same two revisions the other way round, and the same
    answer: whether a cohort holds this evidence cannot be a fact about which
    reading of it happened to land last."""
    elsewhere = git_canonical_commit(repo, "a different dev contract")
    fill_pool(config, feed_root, TARGET)
    two_evaluations(
        config, feed_root, task_id="2026-08-01-task", first=runner_revision(config), second=elsewhere
    )

    offered = evolution.build_proposal(config, now=NOW).proposal

    assert offered is not None
    assert [item.task_id for item in offered.not_selected] == ["2026-08-01-task"]
    assert offered.not_selected[0].reason == manifests.REASON_IDENTITY_DIFFERS
    assert offered.not_selected[0].report_keys == ("first-pass", "second-pass")


def test_the_reason_a_task_is_left_out_is_one_of_its_own_reports_reasons(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """One task gets one reason, and it has to be a reason some report of it
    actually gave.

    That is what keeps the denominator portable: `revision-absent` is settled
    identically in every clone while `revision-unresolvable` is a fact about this
    one, so a task whose rerun states nothing must not borrow the
    `identity-differs` its primary would never have given.
    """
    fill_pool(config, feed_root, TARGET)
    two_evaluations(config, feed_root, task_id="2026-08-01-task", first=None, second=runner_revision(config))

    offered = evolution.build_proposal(config, now=NOW).proposal

    assert offered is not None
    assert {item.task_id: item.reason for item in offered.not_selected} == {
        "2026-08-01-task": manifests.REASON_REVISION_ABSENT
    }


def test_a_staged_bundle_this_machine_cannot_read_stops_the_offer(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """The pool names evidence that is not there, which is damage rather than a
    smaller batch: recording it as a non-member would turn a broken workspace
    into a cohort nobody was told was short."""
    fill_pool(config, feed_root, TARGET)
    entry = evolution.load_state(config).pending[0]
    (config.repo_root / entry.primary.artifacts_path / "report.json").unlink()

    with pytest.raises(evolution.BatchError, match="cannot read the staged bundle"):
        evolution.build_proposal(config, now=NOW)


def test_a_base_whose_canonical_tree_cannot_be_read_forms_no_batch(
    config: evolution.EvolutionConfig, repo: Path, feed_root: Path
) -> None:
    """Every membership test compares against the base, so a base this checkout
    cannot read would answer "different protocol" for every pooled report — a
    batch of nothing, formed for a reason that is about the clone."""
    fill_pool(config, feed_root, TARGET)
    git_update_ref(config.repo_root, RELEASE_REF, git_unreadable_canonical_commit(repo, "a symlinked contract"))

    with pytest.raises(evolution.BatchError, match="cannot read that commit's canonical tree"):
        evolution.build_proposal(config, now=NOW)


def test_the_thresholds_measure_the_batch_on_offer_and_not_the_pool(
    config: evolution.EvolutionConfig, repo: Path, feed_root: Path
) -> None:
    """A pool of TARGET of which one qualifies does not reach the target.

    Measuring the pool instead would pass the threshold on evidence about another
    protocol and then freeze the one report that belongs — a cohort whose size
    was decided by reports it does not contain.
    """
    elsewhere = git_canonical_commit(repo, "a different dev contract")
    fill_pool(config, feed_root, 1)
    evolution.sync(
        config,
        write_feed(
            feed_root,
            at_line(
                config,
                [
                    make_record(key=f"o{index}", sequence=50 + index, task_id=f"2026-08-{index:02d}-task")
                    for index in range(1, TARGET)
                ],
                revision=elsewhere,
            ),
        ),
    )

    offer = evolution.build_proposal(config, now=NOW)

    assert offer.decision.pool_count == TARGET
    assert offer.decision.task_count == 1
    assert offer.decision.freeze is False
    assert offer.decision.reason == batches.REASON_BELOW_MINIMUM
    assert not freeze_only(config).frozen


def test_the_age_rule_is_about_the_members_and_not_the_pool(
    config: evolution.EvolutionConfig, repo: Path, feed_root: Path
) -> None:
    """A report this base does not select cannot age this batch out.

    Taken over the pool, one long-pooled off-base report would release every
    later cohort the day it arrived, whatever the evidence at the base.
    """
    elsewhere = git_canonical_commit(repo, "a different dev contract")
    evolution.sync(
        config,
        write_feed(
            feed_root,
            at_line(
                config,
                [make_record(key="old", sequence=1, task_id="2026-01-01-task")],
                revision=elsewhere,
            ),
        ),
    )
    backdate(config, "2026-01-01T00:00:00Z")
    fill_pool(config, feed_root, MINIMUM)

    offer = evolution.build_proposal(config, now=NOW)

    assert offer.decision.oldest_pending_at != "2026-01-01T00:00:00Z"
    assert offer.decision.freeze is False, "the aged report is not a member, so it releases nothing"
    assert offer.decision.reason == batches.REASON_BELOW_TARGET


def test_an_empty_pool_and_an_off_base_pool_are_different_answers(
    config: evolution.EvolutionConfig, repo: Path, feed_root: Path
) -> None:
    """One says there is no evidence and the other says the evidence is about
    another protocol; an operator's next move differs, so the reasons do."""
    evolution.sync(config, write_feed(feed_root, []))
    assert evolution.build_proposal(config, now=NOW).decision.reason == batches.REASON_POOL_EMPTY

    elsewhere = git_canonical_commit(repo, "a different dev contract")
    evolution.sync(
        config,
        write_feed(
            feed_root,
            at_line(
                config,
                [make_record(key="o1", sequence=50, task_id="2026-08-01-task")],
                revision=elsewhere,
            ),
        ),
    )

    offer = evolution.build_proposal(config, now=NOW)

    assert offer.decision.reason == batches.REASON_POOL_OFF_BASE
    assert offer.proposal is not None and offer.proposal.task_count == 0
    printed = render.format_proposal(offer, config.repo_root)
    assert "none of them is a member of a batch formed at this base" in printed
    assert f"1 not selected: {manifests.REASON_IDENTITY_DIFFERS}" in printed


def test_a_frozen_manifest_states_its_own_denominator(
    config: evolution.EvolutionConfig, repo: Path, feed_root: Path
) -> None:
    """What was considered, how the membership split, and why each considered
    task is not a member — readable from the manifest alone, because the pool it
    was selected out of is machine-local and moves."""
    elsewhere = git_canonical_commit(repo, "a different dev contract")
    fill_pool(config, feed_root, TARGET)
    evolution.sync(
        config,
        write_feed(
            feed_root,
            at_line(
                config,
                [make_record(key="o1", sequence=50, task_id="2026-08-01-task")],
                revision=elsewhere,
            ),
        ),
    )

    manifest = read_manifest(freeze(config))

    assert manifest["selection"] == {
        "considered": TARGET + 1,
        "auto_selected": TARGET,
        "human_included": 0,
        "not_selected": [
            {
                "repo_id": "repo-alpha",
                "task_id": "2026-08-01-task",
                "report_keys": ["o1"],
                "reason": manifests.REASON_IDENTITY_DIFFERS,
                "actor": None,
                "moved_at": None,
            }
        ],
    }
    assert {report["task_id"] for report in manifest["reports"]} == {
        f"2026-07-{index:02d}-task" for index in range(1, TARGET + 1)
    }
    assert all(report["selection"]["mode"] == manifests.MODE_AUTO for report in manifest["reports"])
    assert all(report["selection"]["actor"] is None for report in manifest["reports"])


def test_the_generated_task_states_the_membership_it_reads_recurrence_against(
    config: evolution.EvolutionConfig, repo: Path, feed_root: Path
) -> None:
    """The analysing session counts recurrence over a set, so it has to be told
    which set — and that the pool it can see today is not it."""
    elsewhere = git_canonical_commit(repo, "a different dev contract")
    fill_pool(config, feed_root, TARGET)
    evolution.sync(
        config,
        write_feed(
            feed_root,
            at_line(
                config,
                [make_record(key="o1", sequence=50, task_id="2026-08-01-task")],
                revision=elsewhere,
            ),
        ),
    )

    result = freeze(config)

    assert result.analysis_task_path is not None
    flat = " ".join(result.analysis_task_path.read_text(encoding="utf-8").split())
    assert f"Membership: {TARGET} of {TARGET + 1} considered task(s) selected automatically." in flat
    assert "equality of deployable canonical identity, never commit equality" in flat
    assert "the pending pool is machine-local, has moved since, and is not this batch's denominator" in flat




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
    evolution.sync(config, write_feed(feed_root, at_line(config, records), bodies=bodies))

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
    evolution.sync(config, write_feed(feed_root, at_line(config, records), bodies=bodies))

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
    again = evolution.sync(config, write_feed(feed_root, at_line(config, [late])))

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
    evolution.sync(config, write_feed(feed_root, at_line(config, [late])))

    persisted = evolution.load_state(config)
    assert [entry.dedup_key for entry in persisted.pending] == [("repo-alpha", "2026-07-01-task")]
    assert persisted.pending[0].primary.report_key == "late"
    assert read_manifest(result) == manifest_before


# --- idempotency and the open-batch guard ------------------------------------


def test_a_repeated_start_neither_freezes_again_nor_creates_a_second_task(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    feed = fill_pool(config, feed_root, TARGET)
    evolution.start(config, feed, now=NOW)
    first = evolution.freeze(config, now=NOW)
    assert first.frozen

    again = evolution.start(config, feed, now=NOW)

    assert again.proposal.proposal is None, "a batch is current, so there is nothing to offer"
    assert again.proposal.decision.reason == batches.REASON_CURRENT_BATCH
    assert again.proposal.current_batch_id == first.batch_id
    assert again.proposal.completed == ()
    assert not config.proposal_path.exists(), "the freeze dropped its own offer"
    assert [batch.batch_id for batch in evolution.load_batches(config)] == ["evolution-batch-0001"]
    index_rows = [
        line
        for line in analysis_task.index_path(config).read_text(encoding="utf-8").splitlines()
        if first.analysis_task_id and first.analysis_task_id in line
    ]
    assert len(index_rows) == 1
    assert sorted(path.name for path in analysis_task.tasks_root(config).iterdir()) == [
        f"{first.analysis_task_id}.md",
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

    resumed = freeze_only(config)

    assert not resumed.frozen
    assert resumed.current_batch_id == "evolution-batch-0001"
    assert set(resumed.completed) == {
        batches.COMPLETED_TASK,
        batches.COMPLETED_INDEX,
        # The crash was before the freeze dropped its own offer, and an offer of
        # a batch that now exists is exactly what a second freeze would read.
        batches.COMPLETED_PROPOSAL,
    }
    assert not config.proposal_path.exists()
    assert analysis_task.task_exists(config, frozen[0].analysis_task_id or "")
    assert evolution.load_state(config).pending == []
    assert freeze_only(config).completed == (), "a second restart has nothing left to complete"


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

    resumed = freeze_only(config)

    assert set(resumed.completed) >= {batches.COMPLETED_STATE, batches.COMPLETED_TASK}
    persisted = evolution.load_state(config)
    assert persisted.pending == []
    assert set(persisted.processed) == evolution.load_batches(config)[0].report_keys


def test_a_restart_keeps_a_report_that_arrived_after_the_manifest_pending(
    config: evolution.EvolutionConfig, feed_root: Path, monkeypatch
) -> None:
    """A fresh evaluation of an already-batched task can land in the pool
    between the manifest and the state commit — `start` syncs in the same run
    that repairs. The repair claims exactly the reports the manifest names, so
    the late one stays pending: it belongs to a later batch (invariant 3), not
    to the one that never named it."""
    feed = fill_pool(config, feed_root, TARGET)

    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt("crash before the pool transition")

    monkeypatch.setattr(batches, "save_state", interrupt)
    with pytest.raises(KeyboardInterrupt):
        freeze(config)
    monkeypatch.undo()

    late = make_record(key="late", sequence=200, task_id="2026-07-01-task", evaluation_id="eval-late")
    write_feed(feed_root, at_line(config, [late]))
    resumed = evolution.start(config, feed, now=NOW)

    assert batches.COMPLETED_STATE in resumed.proposal.completed
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
    feed = write_feed(feed_root, at_line(config, records))

    bounded = evolution.start(config, feed, now=NOW, page_size=TARGET, max_pages=1)

    assert not bounded.sync.exhausted
    assert bounded.proposal.decision.reason == batches.REASON_POOL_INCOMPLETE
    assert evolution.load_batches(config) == []
    assert not analysis_task.tasks_root(config).exists()
    # An offer stands over a prefix, and admission is what refuses it: the
    # proposal says which reports were selected, the policy says whether a
    # cohort may be formed out of them at all.
    refused = evolution.freeze(config, now=NOW)
    assert not refused.frozen
    assert refused.decision.reason == batches.REASON_POOL_INCOMPLETE

    # Nothing was lost: the cursor carries on, and the complete pool freezes.
    drained = evolution.start(config, feed, now=NOW)
    frozen = evolution.freeze(config, now=NOW)

    assert drained.sync.exhausted
    assert frozen.frozen
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
    feed = write_feed(feed_root, at_line(config, records))

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
        evolution.sync(config, ArtifactsUnreachable(write_feed(feed_root, at_line(config, records))))

    assert json.loads(config.state_path.read_text(encoding="utf-8"))["feed_exhausted"] is False

    blocked = freeze(config)

    assert not blocked.frozen
    assert blocked.decision.reason == batches.REASON_POOL_INCOMPLETE
    assert evolution.load_batches(config) == []

    # Nothing is stuck: the next healthy sync drains the feed, restores the
    # flag, and the batch forms over the whole eligible set.
    healthy = evolution.sync(config, write_feed(feed_root, at_line(config, records)))

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
        evolution.start(config, Unreachable(), now=NOW)

    assert analysis_task.task_exists(config, batch.analysis_task_id or "")
    assert evolution.load_state(config).pending == []
    assert set(evolution.load_state(config).processed) == set(batch.report_keys)

    # And the repair is reported once the feed comes back, not repeated.
    resumed = evolution.start(config, feed, now=NOW)
    assert resumed.proposal.completed == ()
    assert resumed.proposal.current_batch_id == batch.batch_id


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

    resumed = freeze_only(config)

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
    # A batch that also names the report, so the state below still loads, and
    # concluded, so this reaches the claim check rather than stopping at two
    # current batches (invariant 14).
    closed_elsewhere(config, "evolution-batch-0002", keys)
    write_outcome(config.batches_root, "evolution-batch-0002")
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
    evolution.sync(config, write_feed(feed_root, at_line(config, later)))

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

    published = freeze_only(config)

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
    evolution.sync(config, write_feed(feed_root, at_line(config, later)))
    second = freeze(config, now=NOW + timedelta(days=1))

    assert second.frozen
    assert second.batch_id == "evolution-batch-0002"
    assert [batch.batch_id for batch in evolution.load_batches(config)] == [
        "evolution-batch-0001",
        "evolution-batch-0002",
    ]
    assert second.analysis_task_id != first.analysis_task_id


def test_two_current_batches_are_refused_rather_than_arbitrated(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, TARGET)
    first = freeze(config)
    hand_made = config.batches_root / "evolution-batch-0007"
    hand_made.mkdir()
    (hand_made / batches.MANIFEST_FILENAME).write_text(
        json.dumps({**read_manifest(first), "batch_id": "evolution-batch-0007"}), encoding="utf-8"
    )

    with pytest.raises(evolution.BatchError, match="more than one current batch"):
        freeze(config)


def test_a_second_current_batch_stops_the_repair_before_it_is_selected(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Invariant 14 is asked before a repair is chosen, not after it has run.

    Asking instead which *current* batch still awaits its analysis has an answer
    whenever two of them stand at different stages: the analysis-complete one
    drops out of the question, the interrupted one is the only candidate left,
    and the repair completes its freeze — state transition, analysis task, index
    row — and reports an ordinary run while another cohort's change cycle is
    still open.
    """
    fill_pool(config, feed_root, TARGET)
    first = freeze(config)
    close_analysis(config, first.batch_id or "")
    later = [
        make_record(key=f"n{index}", sequence=100 + index, task_id=f"2026-08-{index:02d}-task")
        for index in range(1, TARGET + 1)
    ]
    evolution.sync(config, write_feed(feed_root, at_line(config, later)))
    # A second current batch whose freeze stopped before its analysis task
    # landed — exactly the state the repair exists to finish.
    interrupted = "evolution-batch-0002"
    task_id = f"2026-08-02-{interrupted}-analysis"
    write_manifest(
        config.batches_root,
        interrupted,
        [f"n{index}" for index in range(1, TARGET + 1)],
        analysis_task_id=task_id,
    )

    with pytest.raises(evolution.BatchError, match="more than one current batch"):
        freeze(config, now=NOW + timedelta(days=1))

    assert not analysis_task.task_exists(config, task_id)
    assert len(evolution.load_state(config).pending) == TARGET, "the second batch claimed nothing"


@pytest.mark.parametrize(
    "recorded, message",
    [
        (None, "has no such experiment"),
        ("e" * 40, "the promotion revision is one commit on the source line"),
    ],
    ids=["experiment-missing", "revision-mismatched"],
)
def test_a_conclusion_its_own_experiments_contradict_releases_no_cohort(
    config: evolution.EvolutionConfig, feed_root: Path, recorded: str | None, message: str
) -> None:
    """A terminal record is not a concluded batch on its own.

    `outcome.json` is what releases the next cohort, so a conclusion no
    experiment of the batch bears out ends the cycle on a trail leading nowhere:
    the promoted experiment does not exist, or it pinned another revision.
    `status` already refused to describe either state; the freeze read the
    record's own shape, called the batch concluded, and formed the cohort after
    it.
    """
    fill_pool(config, feed_root, TARGET)
    first = freeze(config)
    batch_id = first.batch_id or ""
    close_analysis(config, batch_id)
    if recorded is not None:
        write_experiment(
            config.experiments_root,
            f"{batch_id}-exp-01",
            base_revision=runner_revision(config),
            rounds=[experiment_round(1, candidate_revision=recorded)],
            decision=experiment_decision("promoted", promotion_revision=recorded),
        )
    write_outcome(
        config.batches_root,
        batch_id,
        outcome="promoted",
        reason="the candidate held across the replay cohort",
        experiment_id=f"{batch_id}-exp-01",
        promotion_revision="d" * 40,
    )
    later = [
        make_record(key=f"n{index}", sequence=100 + index, task_id=f"2026-08-{index:02d}-task")
        for index in range(1, TARGET + 1)
    ]
    evolution.sync(config, write_feed(feed_root, at_line(config, later)))

    with pytest.raises(evolution.BatchError, match=message):
        freeze(config, now=NOW + timedelta(days=1))

    assert [batch.batch_id for batch in evolution.load_batches(config)] == [batch_id]


def test_a_conclusion_over_a_successor_nobody_created_releases_no_cohort(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """The same rule for the state an interrupted supersession leaves.

    That state stays readable so the supersession can be redone — but only while
    the batch is current. An outcome recorded over it ends the cycle on an attempt
    that does not exist, and once the batch is no longer current nothing looks for
    that successor again: the freeze reading the record's own shape would form the
    next cohort over an unfinished operation nobody can now finish.
    """
    fill_pool(config, feed_root, TARGET)
    first = freeze(config)
    batch_id = first.batch_id or ""
    close_analysis(config, batch_id)
    write_experiment(
        config.experiments_root,
        f"{batch_id}-exp-01",
        base_revision=runner_revision(config),
        rounds=[experiment_round(1, candidate_revision="e" * 40)],
        decision=experiment_decision("superseded", superseded_by=f"{batch_id}-exp-02"),
    )
    write_outcome(config.batches_root, batch_id, reason="calling it here")
    later = [
        make_record(key=f"n{index}", sequence=100 + index, task_id=f"2026-08-{index:02d}-task")
        for index in range(1, TARGET + 1)
    ]
    evolution.sync(config, write_feed(feed_root, at_line(config, later)))

    with pytest.raises(evolution.BatchError, match="which does not exist"):
        freeze(config, now=NOW + timedelta(days=1))

    assert [batch.batch_id for batch in evolution.load_batches(config)] == [batch_id]


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
    evolution.sync(config, write_feed(feed_root, at_line(config, later)))

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


def test_a_batch_frozen_elsewhere_reads_back_and_a_new_one_is_written_at_this_version(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """A committed manifest is the whole of what another machine's freeze leaves
    behind, and it reads back without the ignored runtime state it was frozen
    from."""
    closed_elsewhere(config, "evolution-batch-0001", ["old1", "old2"])

    loaded = evolution.load_batches(config)

    assert [batch.schema_version for batch in loaded] == [2]
    assert loaded[0].task_count == 2
    assert loaded[0].report_keys == {"old1", "old2"}
    assert batches.batch_awaiting_analysis(config) is None

    write_outcome(config.batches_root, "evolution-batch-0001")
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)

    assert read_manifest(result)["schema_version"] == batches.BATCH_SCHEMA_VERSION == 2
    assert [batch.schema_version for batch in evolution.load_batches(config)] == [2, 2]


def test_a_manifest_from_another_machine_still_guards_the_open_batch(
    config: evolution.EvolutionConfig,
) -> None:
    """The fresh-clone shape: the committed manifest is here and the ignored
    runtime state it was frozen from is not, which is a batch this machine never
    staged rather than one whose evidence it lost."""
    write_manifest(
        config.batches_root,
        "evolution-batch-0001",
        ["old1"],
        analysis_task_id="2026-07-31-evolution-batch-0001-analysis",
    )
    assert not config.state_path.exists()

    result = freeze_only(config)

    assert not result.frozen
    assert result.decision.reason == batches.REASON_CURRENT_BATCH
    assert batches.COMPLETED_TASK in result.completed
    assert analysis_task.task_exists(config, "2026-07-31-evolution-batch-0001-analysis")
    # The old batch's report is recorded as claimed, so a later sync cannot pool
    # evidence a frozen cohort already owns.
    assert evolution.load_state(config).processed["old1"]["batch_id"] == "evolution-batch-0001"


@pytest.mark.parametrize("version", [0, 1, 3, "1", None])
def test_a_manifest_version_this_build_cannot_read_fails_closed(
    config: evolution.EvolutionConfig, version: object
) -> None:
    """Guessing at an unknown version would either misread a future manifest or
    silently drop it from the id allocation and the open-batch guard.

    Version 1 is in the list on purpose. It was dropped with its frozen schema
    rather than kept as a reader arm, because no batch has ever been frozen in
    this repository — so the arm would have been compatibility with a record that
    does not exist. That window closes at the first successful freeze."""
    directory = write_manifest(config.batches_root, "evolution-batch-0001", ["r1"])
    manifest = json.loads((directory / batches.MANIFEST_FILENAME).read_text(encoding="utf-8"))
    (directory / batches.MANIFEST_FILENAME).write_text(
        json.dumps({**manifest, "schema_version": version}), encoding="utf-8"
    )

    with pytest.raises(evolution.BatchError, match="unsupported batch manifest schema_version"):
        evolution.load_batches(config)


def excluded_task(**overrides) -> dict:
    """One non-selection record, in the shape a manifest's denominator states."""

    return {
        "repo_id": "repo-alpha",
        "task_id": "2026-08-01-task",
        "report_keys": ["x1"],
        "reason": manifests.REASON_IDENTITY_DIFFERS,
        "actor": None,
        "moved_at": None,
        **overrides,
    }


def human_selection(**overrides) -> dict:
    """A member a person argued in, as the report entry records it."""

    return {
        "mode": manifests.MODE_HUMAN_INCLUDED,
        "reason": "historical evidence, argued in",
        "actor": "operator",
        "moved_at": "2026-07-31T00:00:00Z",
        **overrides,
    }


@pytest.mark.parametrize(
    "overrides, message",
    [
        (
            {"selection": {"considered": 101, "auto_selected": 1, "human_included": 99, "not_selected": []}},
            "do not describe this manifest",
        ),
        (
            {
                "selection": {
                    "considered": 1,
                    "auto_selected": 1,
                    "human_included": 0,
                    "not_selected": [excluded_task(task_id="2026-07-01-task")],
                }
            },
            "is a member and is also listed as not selected",
        ),
        (
            {
                "selection": {
                    "considered": 3,
                    "auto_selected": 1,
                    "human_included": 0,
                    "not_selected": [excluded_task(), excluded_task()],
                }
            },
            "twice, counting one judgement as two",
        ),
        (
            {
                "reports": [
                    make_manifest_report(key="r1", task_id="2026-07-01-task", selection=human_selection(actor=None)),
                ],
                "selection": {"considered": 1, "auto_selected": 0, "human_included": 1, "not_selected": []},
            },
            "records no judgement",
        ),
        (
            {
                "selection": {
                    "considered": 2,
                    "auto_selected": 1,
                    "human_included": 0,
                    "not_selected": [excluded_task(reason=manifests.REASON_HUMAN_EXCLUDED)],
                }
            },
            "records no judgement",
        ),
        (
            {
                "reports": [
                    make_manifest_report(key="r1", sequence=1, task_id="2026-07-01-task"),
                    make_manifest_report(key="rerun", sequence=2, task_id="2026-07-01-task", selection=human_selection()),
                ],
                "selection": {"considered": 1, "auto_selected": 0, "human_included": 1, "not_selected": []},
            },
            "carries two different selections across its reports",
        ),
    ],
    ids=["counts", "both-lists", "counted-twice", "unnamed-inclusion", "unnamed-exclusion", "two-answers"],
)
def test_a_manifest_stating_a_cohort_that_cannot_exist_is_refused(
    config: evolution.EvolutionConfig, overrides: dict, message: str
) -> None:
    """A manifest is immutable and everything downstream reads its denominator as
    fact, so the shape check is not enough on its own.

    JSON Schema constrains each field alone: it will accept counts that no set of
    reports could produce, a task both selected and left out, one judgement
    counted twice, a human move nobody is named for, and one task answered two
    ways across its own reports. Every one of those is a cohort that never
    existed, and `Batch.considered`, `Batch.human_included`, `status` and a
    repaired analysis task would each report it as the batch's own account of
    what it was drawn from. The freeze publishes through this same reader, so a
    writer cannot commit one either.
    """
    write_manifest(config.batches_root, "evolution-batch-0001", ["r1"], **overrides)

    with pytest.raises(evolution.BatchError, match=message):
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
    assert runner_revision(config) in body
    assert config.sha256 in body
    assert "must not edit `canonical/`" in body
    assert ".ai-evolution/imported-artifacts" in body


def test_the_generated_task_keeps_the_base_apart_from_where_the_evidence_came_from(
    config: evolution.EvolutionConfig, repo: Path, feed_root: Path
) -> None:
    """The base governs the analysis and every experiment of the batch; it is not
    a claim about where the members were produced.

    The two are separate facts and the manifest carries both. A member's own
    `provenance.effective_revision` is where that report was produced; the base
    is where the batch was formed, and nothing holds the two to the same commit.
    A task calling the base "the protocol revision its evidence was produced
    under" would have the analysing session state cohort provenance no report
    actually says.
    """
    # A member produced one commit past the base, changing nothing a target
    # receives: the same protocol by the membership test, a different commit by
    # its sha. That is exactly the pair the two facts have to stay apart for.
    produced = git_commit(repo, "work no target receives")
    fill_pool(config, feed_root, TARGET, at=produced)

    result = freeze(config)
    manifest = read_manifest(result)
    assert result.analysis_task_path is not None
    body = result.analysis_task_path.read_text(encoding="utf-8")

    base = manifest["runner_protocol_revision"]
    produced_at = {report["provenance"]["effective_revision"] for report in manifest["reports"]}
    # Otherwise the distinction under test is vacuous here.
    assert base not in produced_at

    # Flattened, so the assertions are about the sentences and not about where
    # the renderer happened to wrap them.
    flat = " ".join(body.split())
    assert f"Base revision: {base}." in flat
    assert "it is the commit every experiment of this batch starts from" in flat
    assert "each member keeps its own production revision in the manifest (`provenance.effective_revision`)" in flat


def test_a_checkout_without_the_source_line_forms_no_batch(
    config: evolution.EvolutionConfig, repo: Path, feed_root: Path
) -> None:
    """The line is what the batch is formed at: it is the base every experiment
    starts from and the identity the membership test selects evidence by. A
    checkout that cannot resolve it cannot say what the batch is, so it refuses
    rather than recording the absence — the answer is a fetch.

    This is the reverse of what the field did while it was provenance alone,
    where a freeze went ahead and named the absence.
    """
    fill_pool(config, feed_root, TARGET)
    elsewhere = releasing_from(config, "refs/heads/never-fetched")
    before = snapshot(repo / "evolution")

    with pytest.raises(evolution.BatchError, match="does not hold refs/heads/never-fetched"):
        evolution.build_proposal(elsewhere, now=NOW)

    assert snapshot(repo / "evolution") == before
    assert not elsewhere.proposal_path.exists()
    assert len(evolution.load_state(config).pending) == TARGET


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

    evolution.start(config, feed, now=NOW)
    result = evolution.freeze(config, now=NOW)
    evolution.start(config, feed, now=NOW)

    frozen = [record for record in ledger.read_records(config) if record["record_type"] == batches.RECORD_BATCH_FROZEN]
    assert len(frozen) == 1
    assert frozen[0] == {
        "record_type": batches.RECORD_BATCH_FROZEN,
        "schema_version": 1,
        "recorded_at": "2026-08-01T12:00:00Z",
        "batch_id": result.batch_id,
        "task_id": result.analysis_task_id,
        "revision": runner_revision(config),
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


def test_the_runner_revision_is_the_source_line_not_the_checkout(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Invariant 8: the revision governing the cohort is where the line the
    project releases from stands, not where whoever ran the freeze was standing.

    The old reading was `git describe --tags HEAD`, which answered with a tag
    name and moved with the operator; this asserts both halves at once by
    committing on the checkout alone and requiring the manifest to ignore it.
    """
    fill_pool(config, feed_root, TARGET)
    line = runner_revision(config)
    moved = git_commit(config.repo_root, "work that reaches no release")
    assert moved != line

    manifest = read_manifest(freeze(config))

    assert manifest["runner_protocol_revision"] == line
    assert manifest["runner_protocol_revision"] != moved


def test_the_freeze_records_a_revision_rather_than_a_release_tag(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """`v2.2.0` is reachable here and is deliberately not the answer: every other
    entry in the contract's revisions table is a commit, and a tag names a tree
    that need not be the one the cohort ran under."""
    fill_pool(config, feed_root, TARGET)

    recorded = read_manifest(freeze(config))["runner_protocol_revision"]

    assert recorded != RELEASE_TAG
    assert revisions.release_ref(config.repo_root, recorded) == RELEASE_TAG


def test_a_directory_inside_another_repository_does_not_borrow_its_revision(tmp_path: Path) -> None:
    """Otherwise a temporary directory under someone's checkout silently
    acquires that checkout's source line."""
    outer = git_repo(make_repo(tmp_path), tag="v9.9.9")
    inner = outer / "nested"
    inner.mkdir()

    assert revisions.ref_tip(inner, RELEASE_REF) is None


def test_a_path_that_is_not_a_repository_resolves_no_source_line(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    assert revisions.ref_tip(plain, RELEASE_REF) is None


# --- the proposal, and the human window before the manifest -------------------


def test_building_a_proposal_writes_nothing_durable(
    config: evolution.EvolutionConfig, repo: Path, feed_root: Path
) -> None:
    """The whole point of splitting the formation: `start` offers a batch and
    fixes nothing, so a human stands between the pool and the analysis task.

    Nothing under `evolution/` moves and the pending pool is byte-identical —
    no manifest, no report claim, no analysis task, no batch ledger line."""
    feed = fill_pool(config, feed_root, TARGET)
    before = snapshot(repo / "evolution")
    pool_before = config.state_path.read_bytes()

    result = evolution.start(config, feed, now=NOW)

    assert result.proposal.proposed
    assert snapshot(repo / "evolution") == before
    assert config.state_path.read_bytes() == pool_before
    assert evolution.load_batches(config) == []
    assert not analysis_task.tasks_root(config).exists()
    assert [record["record_type"] for record in ledger.read_records(config)] == ["report-imported"] * TARGET


def test_the_proposal_records_the_base_and_what_it_selected(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """A batch is a base plus the reports selected against it, and both halves
    are decided here rather than at the manifest."""
    fill_pool(config, feed_root, TARGET)

    result = evolution.build_proposal(config, now=NOW)

    offered = result.proposal
    assert offered is not None
    assert offered.base_revision == runner_revision(config)
    assert offered.source_line_ref == RELEASE_REF
    assert offered.task_count == TARGET
    assert {member.mode for member in offered.selected} == {manifests.MODE_AUTO}
    assert offered.report_keys() == {f"r{index}" for index in range(1, TARGET + 1)}
    # And it reads back through the parser it was written by.
    assert proposals.load_proposal(config) == offered


def test_the_frozen_batch_takes_the_base_the_proposal_was_built_at(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Not re-resolved at the freeze: the base is a parameter of the batch, read
    once when it is created (invariants 8, 15)."""
    fill_pool(config, feed_root, TARGET)
    offered = evolution.build_proposal(config, now=NOW).proposal
    assert offered is not None

    result = evolution.freeze(config, now=NOW)

    assert read_manifest(result)["runner_protocol_revision"] == offered.base_revision


def test_a_freeze_with_no_proposal_refuses_and_names_the_offer(
    config: evolution.EvolutionConfig, repo: Path, feed_root: Path
) -> None:
    """There is no path from the pool straight to a manifest. A freeze fixes an
    offer somebody read, which is what makes the human window real."""
    fill_pool(config, feed_root, TARGET)
    before = snapshot(repo / "evolution")

    with pytest.raises(evolution.BatchError, match="no batch proposal stands"):
        evolution.freeze(config, now=NOW)

    assert snapshot(repo / "evolution") == before
    assert len(evolution.load_state(config).pending) == TARGET


def test_a_freeze_refuses_a_proposal_built_before_the_line_moved(
    config: evolution.EvolutionConfig, repo: Path, feed_root: Path
) -> None:
    """The lock does not span the human window, so canonical work can land on
    the source line between the offer and the freeze. The base selects the
    evidence and is what every experiment starts from, so the offer is refused
    rather than quietly re-based."""
    fill_pool(config, feed_root, TARGET)
    offered = evolution.build_proposal(config, now=NOW).proposal
    assert offered is not None
    moved = git_release_commit(repo, "canonical work between the offer and the freeze")
    assert moved != offered.base_revision
    before = snapshot(repo / "evolution")

    with pytest.raises(evolution.BatchError, match="rather than re-based"):
        evolution.freeze(config, now=NOW)

    assert snapshot(repo / "evolution") == before
    assert evolution.load_batches(config) == []
    # And the rebuild the refusal names is what forms the batch, at the new tip.
    rebuilt = evolution.build_proposal(config, now=NOW).proposal
    assert rebuilt is not None and rebuilt.base_revision == moved
    assert read_manifest(evolution.freeze(config, now=NOW))["runner_protocol_revision"] == moved


def test_a_freeze_refuses_a_proposal_the_pool_has_outgrown(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """A batch states what it was selected out of, so a task that entered the
    pool after the offer was built has to be considered before it is left out —
    otherwise the frozen denominator is wrong about its own population."""
    feed = fill_pool(config, feed_root, TARGET)
    evolution.build_proposal(config, now=NOW)
    write_feed(feed_root, at_line(config, [make_record(key="late", sequence=99, task_id="2026-07-99-task")]))
    evolution.sync(config, feed)

    with pytest.raises(evolution.BatchError, match="never considered"):
        evolution.freeze(config, now=NOW)

    assert evolution.load_batches(config) == []


def test_a_freeze_refuses_a_member_whose_reports_changed(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """A later evaluation of an offered task lands in the same pool entry, so
    freezing the stale offer would silently substitute one pack for another."""
    feed = fill_pool(config, feed_root, TARGET)
    evolution.build_proposal(config, now=NOW)
    write_feed(feed_root, at_line(config, [make_record(key="rerun", sequence=99, task_id="2026-07-01-task")]))
    evolution.sync(config, feed)

    with pytest.raises(evolution.BatchError, match="different reports than the proposal offered"):
        evolution.freeze(config, now=NOW)


def test_the_freeze_drops_the_offer_it_fixed(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """An offer of a batch that now exists is what a second freeze would read,
    and it would try to claim reports the first one already claimed."""
    fill_pool(config, feed_root, TARGET)
    evolution.build_proposal(config, now=NOW)

    evolution.freeze(config, now=NOW)

    assert not config.proposal_path.exists()
    assert proposals.load_proposal(config) is None


def test_a_current_batch_leaves_no_offer_standing(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """A proposal is an offer of the *next* cohort. One standing beside a
    running change cycle would be frozen the moment that cycle ended, out of a
    pool nobody read again."""
    feed = fill_pool(config, feed_root, TARGET)
    evolution.build_proposal(config, now=NOW)
    evolution.freeze(config, now=NOW)
    fill_pool(config, feed_root, TARGET, extra=[make_record(key="next", sequence=90, task_id="2026-09-01-task")])

    result = evolution.start(config, feed, now=NOW)

    assert result.proposal.proposal is None
    assert result.proposal.decision.reason == batches.REASON_CURRENT_BATCH
    assert not config.proposal_path.exists()


def test_an_empty_pool_leaves_no_offer_standing(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, TARGET)
    evolution.build_proposal(config, now=NOW)
    persisted = evolution.load_state(config)
    persisted.pending = []
    evolution.save_state(config, persisted)

    result = evolution.build_proposal(config, now=NOW)

    assert result.proposal is None
    assert result.completed == (batches.COMPLETED_PROPOSAL,)
    assert not config.proposal_path.exists()


def test_a_freeze_still_verifies_every_staged_record_behind_the_offer(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Re-verification stays at the freeze and does not move to the proposal:
    the proposal is not immutable and the freeze is the last moment a tampered
    bundle can be caught."""
    fill_pool(config, feed_root, TARGET)
    evolution.build_proposal(config, now=NOW)
    entry = evolution.load_state(config).pending[0]
    staged = config.repo_root / entry.primary.artifacts_path / state.REPORT_JSON_FILENAME
    staged.write_text(staged.read_text(encoding="utf-8").replace("repo-alpha", "repo-tampered"), encoding="utf-8")

    with pytest.raises(evolution.BatchError):
        evolution.freeze(config, now=NOW)

    assert evolution.load_batches(config) == []


# --- the proposal record itself ----------------------------------------------


def offer(**overrides) -> dict:
    """One well-formed proposal document, for tests that damage exactly one
    field of it."""

    document = {
        "schema_version": proposals.PROPOSAL_SCHEMA_VERSION,
        "built_at": "2026-08-01T12:00:00Z",
        "source_line_ref": RELEASE_REF,
        "base_revision": "b" * 40,
        "selected": [
            {
                "repo_id": "repo-alpha",
                "task_id": "2026-07-01-task",
                "report_keys": ["r1"],
                "mode": manifests.MODE_AUTO,
                "reason": None,
                "actor": None,
                "moved_at": None,
            }
        ],
        "not_selected": [],
    }
    document.update(overrides)
    return document


def read_offer(document: dict) -> proposals.BatchProposal:
    return proposals.BatchProposal.from_json(document, Path("batch-proposal.json"))


def test_a_human_move_carries_its_actor_reason_and_moment(tmp_path: Path) -> None:
    """A human inclusion is invariant 1's exception, so nothing else bounds it:
    the record has to say who made the judgement and why, or the exception is a
    silent one."""
    included = {
        "repo_id": "repo-alpha",
        "task_id": "2026-07-02-task",
        "report_keys": ["r2"],
        "mode": manifests.MODE_HUMAN_INCLUDED,
        "reason": "historical evidence, produced before the identity was published",
        "actor": "operator@example.com",
        "moved_at": "2026-08-01T13:00:00Z",
    }

    parsed = read_offer(offer(selected=[included]))

    assert len(parsed.human_included) == 1
    assert parsed.human_included[0].reason.startswith("historical evidence")


@pytest.mark.parametrize("missing", ["reason", "actor", "moved_at"])
def test_a_human_move_missing_any_of_the_three_is_refused(missing: str) -> None:
    included = {
        "repo_id": "repo-alpha",
        "task_id": "2026-07-02-task",
        "report_keys": ["r2"],
        "mode": manifests.MODE_HUMAN_INCLUDED,
        "reason": "historical evidence",
        "actor": "operator@example.com",
        "moved_at": "2026-08-01T13:00:00Z",
        missing: None,
    }

    with pytest.raises(evolution.ProposalError, match="records no judgement"):
        read_offer(offer(selected=[included]))


def test_an_automatic_member_naming_an_actor_is_refused() -> None:
    """A name on an automatic selection would read as a judgement nobody made,
    which is exactly what the mode is there to keep apart."""
    named = {**offer()["selected"][0], "actor": "operator@example.com"}

    with pytest.raises(evolution.ProposalError, match="nothing was moved here"):
        read_offer(offer(selected=[named]))


def test_a_human_exclusion_carries_its_actor_and_moment() -> None:
    excluded = {
        "repo_id": "repo-alpha",
        "task_id": "2026-07-03-task",
        "report_keys": ["r3"],
        "reason": manifests.REASON_HUMAN_EXCLUDED,
        "actor": "operator@example.com",
        "moved_at": "2026-08-01T13:00:00Z",
    }

    parsed = read_offer(offer(not_selected=[excluded]))

    assert parsed.considered == 2
    assert parsed.task_count == 1


def test_an_automatic_non_selection_names_nobody() -> None:
    """`identity-differs` is the test answering, not a person deciding."""
    excluded = {
        "repo_id": "repo-alpha",
        "task_id": "2026-07-03-task",
        "report_keys": ["r3"],
        "reason": manifests.REASON_IDENTITY_DIFFERS,
        "actor": None,
        "moved_at": None,
    }

    assert read_offer(offer(not_selected=[excluded])).considered == 2

    with pytest.raises(evolution.ProposalError, match="nothing was moved here"):
        read_offer(offer(not_selected=[{**excluded, "actor": "operator@example.com"}]))


def test_a_task_cannot_be_both_selected_and_left_out() -> None:
    """The two lists together are the batch's denominator, so a task in both
    would make the considered count disagree with itself."""
    both = {
        "repo_id": "repo-alpha",
        "task_id": "2026-07-01-task",
        "report_keys": ["r1"],
        "reason": manifests.REASON_IDENTITY_DIFFERS,
        "actor": None,
        "moved_at": None,
    }

    with pytest.raises(evolution.ProposalError, match="named in both"):
        read_offer(offer(not_selected=[both]))


@pytest.mark.parametrize("field", ["built_at", "source_line_ref", "base_revision", "selected", "not_selected"])
def test_an_incomplete_proposal_is_refused_rather_than_defaulted(field: str) -> None:
    """The same rule the pool is read by: a missing field is damage, and reading
    the gap as "nothing" is how a selection silently becomes a different one."""
    document = offer()
    del document[field]

    with pytest.raises(evolution.ProposalError, match="incomplete proposal"):
        read_offer(document)


@pytest.mark.parametrize("version", [0, 2, "1", None, True, [], {}])
def test_a_proposal_version_this_build_cannot_read_is_refused(version: object) -> None:
    """`True` and `[]` are in the list on purpose: `True == 1` in Python, so a
    version field of `true` would read as version 1 and parse, and an
    unhashable value raises out of the lookup rather than being refused by
    it."""

    with pytest.raises(evolution.ProposalError, match="unsupported proposal schema_version"):
        read_offer(offer(schema_version=version))


def test_an_unparseable_proposal_names_the_rebuild(config: evolution.EvolutionConfig) -> None:
    """Nothing durable stands on a proposal, so a damaged one is thrown away
    rather than repaired — unlike the pool behind it."""
    config.proposal_path.parent.mkdir(parents=True, exist_ok=True)
    config.proposal_path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(evolution.ProposalError, match="delete it and run `mandrel evolution start`"):
        proposals.load_proposal(config)


def test_a_writer_cannot_publish_a_proposal_its_own_reader_refuses(
    config: evolution.EvolutionConfig,
) -> None:
    """Writers publish through the reader, so the cross-field rules hold in both
    directions and an operator never meets one as a freeze that cannot start."""
    broken = proposals.BatchProposal(
        built_at="2026-08-01T12:00:00Z",
        source_line_ref=RELEASE_REF,
        base_revision="b" * 40,
        selected=(
            proposals.Member(
                repo_id="repo-alpha",
                task_id="2026-07-01-task",
                report_keys=("r1",),
                mode=manifests.MODE_HUMAN_INCLUDED,
                reason="historical evidence",
            ),
        ),
    )

    with pytest.raises(evolution.ProposalError, match="records no judgement"):
        proposals.save_proposal(config, broken)
    assert not config.proposal_path.exists()


def test_a_poll_that_changes_nothing_leaves_the_offer_and_the_token_alone(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """The proposal is in the state digest, so re-stamping an unchanged offer
    would refuse an operator's outstanding token over somebody else's no-op
    poll — the exact false positive the watched-ref set was trimmed to avoid."""
    feed = fill_pool(config, feed_root, TARGET)
    first = evolution.start(config, feed, now=NOW).proposal.proposal
    assert first is not None
    bytes_before = config.proposal_path.read_bytes()

    again = evolution.start(config, feed, now=NOW + timedelta(days=2)).proposal.proposal

    assert again == first
    assert config.proposal_path.read_bytes() == bytes_before


def test_a_pool_that_grew_gets_a_fresh_offer(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    feed = fill_pool(config, feed_root, TARGET)
    first = evolution.start(config, feed, now=NOW).proposal.proposal
    assert first is not None
    write_feed(feed_root, at_line(config, [make_record(key="late", sequence=99, task_id="2026-07-99-task")]))

    again = evolution.start(config, feed, now=NOW + timedelta(days=2)).proposal.proposal

    assert again is not None
    assert again.task_count == first.task_count + 1
    assert again.built_at != first.built_at


def test_a_damaged_offer_is_rebuilt_by_the_poll_and_refused_by_the_freeze(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Nothing durable stands on an offer, so the poll repairs it by replacing
    it. Everything that would act on one still refuses rather than guessing."""
    feed = fill_pool(config, feed_root, TARGET)
    evolution.build_proposal(config, now=NOW)
    config.proposal_path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(evolution.ProposalError):
        evolution.freeze(config, now=NOW)

    rebuilt = evolution.start(config, feed, now=NOW).proposal.proposal

    assert rebuilt is not None and rebuilt.task_count == TARGET
    assert proposals.load_proposal(config) == rebuilt
