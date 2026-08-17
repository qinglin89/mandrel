"""One batch's whole change cycle, driven only through the public operations.

The other evolution suites each hold one seam still: admission policy against a
fixed pool, the readers against hand-written records, the operations against a
batch that was set up by hand. This one runs the arc the contract describes —
freeze, analysis, admission, rounds, decisions, outcome, the next cohort — with
every step performed by the operation that owns it and every fact read back
through the derivation `status` uses.

What that catches and a per-operation test cannot: two operations whose writes
are individually valid and jointly unreadable, a derived phase that names an
operation the writers refuse, and history that stops being answerable once the
attempt producing it has ended. The evidence a batch leaves behind has to
outlive the machine it was produced on, so the arc finishes by taking away
`.ai-tasks/` and the checkout and asking the same questions again.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from evolution_fixtures import (
    complete_task,
    git_checkout,
    git_commit,
    git_repo,
    git_rev,
    git_update_ref,
    make_record,
    make_repo,
    write_draft,
    write_feed,
)

from mandrel import evolution
from mandrel.evolution import analysis_task, batches, experiments, lineage, phase, render

TARGET = 3
MINIMUM = 2
REVISION = "v2.2.0"

BATCH_ID = "evolution-batch-0001"
NEXT_BATCH = "evolution-batch-0002"
EXP_01 = f"{BATCH_ID}-exp-01"
EXP_02 = f"{BATCH_ID}-exp-02"

DRAFTS = ("loader-fallback", "hook-side-loader", "prompt-budget", "not-worth-it")


def at(day: int) -> datetime:
    """One moment per step of the arc, so every record says when it happened."""

    return datetime(2026, 8, day, 9, 0, 0, tzinfo=timezone.utc)


# --- fixtures ----------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository with the real contract files and a pool target small enough
    to reach: the policy numbers are `test_evolution_controller`'s subject, and
    this suite is about what happens after a cohort is frozen."""

    root = git_repo(make_repo(tmp_path), tag=REVISION)
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


def fill_pool(config: evolution.EvolutionConfig, feed_root: Path, *, prefix: str, sequence: int) -> None:
    """Import one cohort's worth of distinct completed tasks."""

    evolution.sync(
        config,
        write_feed(
            feed_root,
            [
                make_record(
                    key=f"{prefix}{index}",
                    sequence=sequence + index,
                    task_id=f"2026-07-{sequence + index:02d}-task",
                )
                for index in range(1, TARGET + 1)
            ],
        ),
    )


def analyze(config: evolution.EvolutionConfig, batch_id: str, task_id: str, *, drafts: tuple[str, ...]) -> None:
    """The analysis stage, as the contract ends it: dispositions committed, the
    analysis task completed on this machine, and the change-task drafts left
    waiting in the batch directory for a human to decide about."""

    (config.batches_root / batch_id / batches.FINDINGS_FILENAME).write_text("# Findings\n", encoding="utf-8")
    complete_task(config, task_id)
    for draft_id in drafts:
        write_draft(config.batches_root, batch_id, draft_id)


def work(config: evolution.EvolutionConfig, ref: str, task_id: str, *, message: str) -> str:
    """One admitted task carried out: a commit on the experiment's ref, and the
    task finished the way close-out finishes it."""

    revision = git_commit(config.repo_root, message)
    git_update_ref(config.repo_root, ref, revision)
    complete_task(config, task_id)
    return revision


def ledger_types(config: evolution.EvolutionConfig) -> list[str]:
    return [record["record_type"] for record in evolution.read_records(config)]


# --- the arc -----------------------------------------------------------------


def test_a_batch_runs_from_its_freeze_to_its_outcome(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Admit, seal, revise, admit, seal, supersede, admit, seal, abandon,
    conclude — every step through the operation that owns it, and the phase read
    back after each one from nothing but what those operations wrote."""

    fill_pool(config, feed_root, prefix="r", sequence=0)
    frozen = evolution.freeze(config, now=at(1), runner_revision=REVISION)
    assert frozen.batch_id == BATCH_ID
    assert phase.describe(config, now=at(1)).phase == phase.PHASE_BATCH_FROZEN

    analyze(config, BATCH_ID, frozen.analysis_task_id or "", drafts=DRAFTS)
    assert phase.describe(config, now=at(2)).summary == f"proposals-pending {BATCH_ID} (4 drafts)"

    # Round 1 of the first attempt: two proposals admitted together, worked, and
    # the round sealed once both are observed complete.
    first = experiments.create(config, ["loader-fallback", "hook-side-loader"], now=at(2))
    assert first.experiment_id == EXP_01
    base = first.base_revision
    for item in first.admitted:
        work(config, first.ref, item.task_id, message=f"round 1: {item.draft_id}")
    round_one = experiments.seal_round(config, now=at(3)).candidate_revision
    assert phase.describe(config, now=at(3)).summary == f"candidate-ready {EXP_01} round 1"

    # Replay says the candidate is not good enough: the next round opens empty,
    # takes a proposal of its own, and pins a candidate of its own.
    experiments.revise(config, reason="replay lost two runs to the loader order", now=at(4))
    assert phase.describe(config, now=at(4)).summary == f"implementing {EXP_01} round 2 (no tasks admitted)"
    second = experiments.add_tasks(config, ["prompt-budget"], now=at(4))
    work(config, first.ref, second.admitted[0].task_id, message="round 2: prompt-budget")
    round_two = experiments.seal_round(config, now=at(5)).candidate_revision
    assert round_two != round_one

    # The approach is replaced rather than revised again: the attempt ends and
    # the same operation creates the next one, at the base and not at the tip.
    replaced = experiments.supersede(config, reason="the whole approach belongs in the hook", now=at(6))
    assert replaced.successor_id == EXP_02
    assert git_rev(config.repo_root, replaced.successor_ref or "") == base
    assert phase.describe(config, now=at(6)).summary == f"implementing {EXP_02} round 1 (no tasks admitted)"

    # The last proposal is declined rather than admitted, which is the other
    # terminal answer a draft can get and the one that empties the gate.
    experiments.reject(config, ["not-worth-it"], reason="one report is not recurrence", now=at(6))

    # The alternative has nothing left to admit — every draft is spent — so it is
    # abandoned from an open round, which records no candidate for it.
    experiments.abandon(config, reason="no proposal left that the hook could carry", now=at(7))
    assert phase.describe(config, now=at(7)).phase == phase.PHASE_CONCLUSION_PENDING

    outcome = experiments.conclude_no_change(config, reason="both approaches failed replay", now=at(8))

    assert outcome.batch_id == BATCH_ID
    assert evolution.current_batch(config) is None
    assert phase.describe(config, now=at(8)).phase == phase.PHASE_IDLE
    assert ledger_types(config)[TARGET:] == [
        "batch-frozen",
        "batch-analyzed",
        "experiment-created",
        "tasks-admitted",
        "tasks-admitted",
        "round-sealed",
        "experiment-revised",
        "tasks-admitted",
        "round-sealed",
        "experiment-superseded",
        "experiment-created",
        "draft-rejected",
        "experiment-abandoned",
        "batch-concluded",
    ]

    derived = lineage.describe(config).batches[0]
    assert derived.current is False
    assert [item.experiment_id for item in derived.experiments] == [EXP_01, EXP_02]
    assert [round_.candidate_revision for round_ in derived.experiments[0].rounds] == [round_one, round_two]
    assert derived.experiments[0].decision.outcome == "superseded"
    assert derived.experiments[1].decision.outcome == "abandoned"
    assert derived.experiments[1].last_round.seal is None
    assert derived.gate.waiting == ()
    assert sorted(derived.gate.consumed) == ["hook-side-loader", "loader-fallback", "prompt-budget"]
    assert derived.gate.declined == ("not-worth-it",)


def test_the_history_outlives_the_machine_that_produced_it(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """The point of recording task selections and candidate revisions rather
    than pointing at files: `.ai-tasks/` is machine-local and close-out archives
    it away, and a checkout is one command from being somewhere else."""

    fill_pool(config, feed_root, prefix="r", sequence=0)
    frozen = evolution.freeze(config, now=at(1), runner_revision=REVISION)
    analyze(config, BATCH_ID, frozen.analysis_task_id or "", drafts=DRAFTS)
    admitted = experiments.create(config, ["loader-fallback"], now=at(2))
    work(config, admitted.ref, admitted.admitted[0].task_id, message="round 1: loader-fallback")
    pinned = experiments.seal_round(config, now=at(3)).candidate_revision
    experiments.abandon(config, reason="the loader order cannot be fixed inside the hook", now=at(4))

    shutil.rmtree(analysis_task.tasks_root(config))
    git_checkout(config.repo_root, admitted.base_revision)

    derived = lineage.describe(config).current
    assert derived is not None and derived.batch_id == BATCH_ID
    experiment = derived.experiments[0]
    assert experiment.rounds[0].candidate_revision == pinned
    assert [task.task_id for task in experiment.admitted_tasks] == ["2026-08-01-loader-fallback"]
    assert experiment.admitted_tasks[0].complete is True
    assert experiment.decision.reason == "the loader order cannot be fixed inside the hook"
    assert derived.gate.consumed == {"loader-fallback": EXP_01}


def test_a_concluded_batch_releases_the_next_cohort(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """The sequencing rule the widened currency created: reports keep
    accumulating while a batch is current, and nothing frees them until its
    outcome is recorded (invariant 14). `conclude-no-change` is what does that
    for a batch that changed nothing."""

    fill_pool(config, feed_root, prefix="r", sequence=0)
    frozen = evolution.freeze(config, now=at(1), runner_revision=REVISION)
    analyze(config, BATCH_ID, frozen.analysis_task_id or "", drafts=("loader-fallback",))
    experiments.reject(config, ["loader-fallback"], reason="one report is not recurrence", now=at(2))

    fill_pool(config, feed_root, prefix="n", sequence=100)
    blocked = evolution.freeze(config, now=at(3), runner_revision=REVISION)
    assert blocked.frozen is False
    assert blocked.current_batch_id == BATCH_ID

    experiments.conclude_no_change(config, reason="no cluster reached recurrence", now=at(4))
    released = evolution.freeze(config, now=at(5), runner_revision=REVISION)

    assert released.frozen is True
    assert released.batch_id == NEXT_BATCH
    assert [batch.batch_id for batch in evolution.load_batches(config)] == [BATCH_ID, NEXT_BATCH]
    assert phase.describe(config, now=at(5)).summary == f"batch-frozen {NEXT_BATCH}"


def test_status_names_what_the_operator_does_next_at_every_step(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """The rendered status is the surface an operator acts on, so what it says is
    holding the lifecycle has to be the thing the next operation actually needs
    — not a fact that happens to be true."""

    fill_pool(config, feed_root, prefix="r", sequence=0)
    frozen = evolution.freeze(config, now=at(1), runner_revision=REVISION)
    analyze(config, BATCH_ID, frozen.analysis_task_id or "", drafts=("loader-fallback", "hook-side-loader"))

    waiting = render.format_status(phase.describe(config, now=at(2)))
    assert "hook-side-loader, loader-fallback — awaiting human admission" in waiting

    admitted = experiments.create(config, ["loader-fallback"], now=at(2))
    implementing = render.format_status(phase.describe(config, now=at(2)))
    assert f"{EXP_01}, round 1 (open)" in implementing
    assert "2026-08-01-loader-fallback" in implementing
    assert "none pinned — the open round has not been sealed" in implementing

    work(config, admitted.ref, admitted.admitted[0].task_id, message="round 1: loader-fallback")
    experiments.seal_round(config, now=at(3))
    sealed = render.format_status(phase.describe(config, now=at(3)))
    assert f"{EXP_01}, round 1 (candidate-ready)" in sealed

    experiments.abandon(config, reason="the loader order cannot be fixed inside the hook", now=at(4))
    ended = render.format_status(phase.describe(config, now=at(4)))
    assert f"history      {EXP_01} abandoned" in ended
    assert "no experiment is open — nothing is pinned and no ref was read" in ended
    assert "hook-side-loader — awaiting human admission" in ended


def test_nothing_in_the_arc_reads_the_ledger_back_as_state(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """The audit records that events happened and is never the state store, so
    losing it costs the audit and nothing else: the same lineage, the same phase,
    the same next operation."""

    fill_pool(config, feed_root, prefix="r", sequence=0)
    frozen = evolution.freeze(config, now=at(1), runner_revision=REVISION)
    analyze(config, BATCH_ID, frozen.analysis_task_id or "", drafts=("loader-fallback",))
    admitted = experiments.create(config, ["loader-fallback"], now=at(2))
    work(config, admitted.ref, admitted.admitted[0].task_id, message="round 1: loader-fallback")
    experiments.seal_round(config, now=at(3))
    before = phase.describe(config, now=at(3)).to_json()

    config.ledger_path.write_text("", encoding="utf-8")

    assert phase.describe(config, now=at(3)).to_json() == before
    assert experiments.revise(config, reason="replay lost two runs", now=at(4)).round_number == 2


def test_the_next_cohort_is_a_batch_of_its_own_with_its_own_experiments(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Two cycles in one repository: the second batch freezes its own base when
    its first experiment is created, and the first batch's history stays exactly
    where it was."""

    fill_pool(config, feed_root, prefix="r", sequence=0)
    first = evolution.freeze(config, now=at(1), runner_revision=REVISION)
    analyze(config, BATCH_ID, first.analysis_task_id or "", drafts=("loader-fallback",))
    admitted = experiments.create(config, ["loader-fallback"], now=at(2))
    work(config, admitted.ref, admitted.admitted[0].task_id, message="round 1: loader-fallback")
    pinned = experiments.seal_round(config, now=at(3)).candidate_revision
    experiments.abandon(config, reason="the loader order cannot be fixed", now=at(4))
    experiments.conclude_no_change(config, reason="the one approach failed replay", now=at(4))

    fill_pool(config, feed_root, prefix="n", sequence=100)
    second = evolution.freeze(config, now=at(5) + timedelta(days=1), runner_revision=REVISION)
    analyze(config, NEXT_BATCH, second.analysis_task_id or "", drafts=("second-loader-fix",))
    moved = git_commit(config.repo_root, "canonical work between the two cohorts")
    later = experiments.create(config, ["second-loader-fix"], now=at(7))

    assert later.experiment_id == f"{NEXT_BATCH}-exp-01"
    assert later.base_revision == moved
    assert later.base_revision != admitted.base_revision

    derived = {item.batch_id: item for item in lineage.describe(config).batches}
    assert derived[BATCH_ID].current is False
    assert derived[BATCH_ID].experiments[0].rounds[0].candidate_revision == pinned
    assert derived[NEXT_BATCH].current is True
    assert derived[NEXT_BATCH].base_revision == moved
