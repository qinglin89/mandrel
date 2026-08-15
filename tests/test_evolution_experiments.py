"""The human admission gate: what admitting, adding to, and declining a proposal
actually writes.

Everything runs against a real temporary Git repository, because the operations
here create a ref and record the commit it stands at — a fake would prove the
package agrees with itself about the one part an operator later has to check out.

Two properties get most of the attention, since they are the ones an interruption
threatens:

- **Nothing is admitted twice.** A draft, a task id, an experiment ordinal, a
  base revision: each is claimed once, and the refusals say which record already
  claims it.
- **Nothing is left orphaned.** The task copies are written last and are
  derivable from the record, so a crash can leave a task missing but never leave
  one in the active pool that no experiment accounts for. Redoing the same
  selection is what finishes it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from evolution_fixtures import (
    FakeHarness,
    admitted_task,
    completed_report,
    draft_body,
    draft_sha256,
    experiment_round,
    experiment_decision,
    git_commit,
    git_delete_ref,
    git_repo,
    git_rev,
    git_sibling_commit,
    git_tree,
    git_try_update_ref,
    git_unrelated_commit,
    git_update_ref,
    git_worktree,
    make_repo,
    prepared_promotion,
    rejection,
    snapshot,
    write_closure,
    write_draft,
    write_experiment,
    write_manifest,
    write_outcome,
    write_rejected_drafts,
)

from ai_native_deployment import evolution
from ai_native_deployment.evolution import (
    analysis_task,
    experiments,
    guards,
    lineage,
    phase,
    render,
    replay,
    revisions,
    state,
)

BATCH_ID = "evolution-batch-0001"
SECOND_BATCH = "evolution-batch-0002"
EXP_01 = f"{BATCH_ID}-exp-01"
EXP_02 = f"{BATCH_ID}-exp-02"
ANALYSIS_TASK = "2026-07-31-evolution-batch-0001-analysis"

NOW = datetime(2026, 8, 5, 9, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 8, 6, 9, 0, 0, tzinfo=timezone.utc)
LATEST = datetime(2026, 8, 7, 9, 0, 0, tzinfo=timezone.utc)
SEALED_AT = "2026-08-06T09:00:00Z"
REVISED_AT = "2026-08-07T09:00:00Z"
DRAFTS = ("loader-fallback", "hook-side-loader", "not-worth-it")


# --- fixtures ----------------------------------------------------------------


@pytest.fixture
def config(tmp_path: Path) -> evolution.EvolutionConfig:
    return evolution.load_config(git_repo(make_repo(tmp_path), tag="v2.2.0"))


@pytest.fixture
def batch(config: evolution.EvolutionConfig) -> Path:
    """A current batch whose analysis has closed and whose drafts are waiting —
    the only state the admission gate acts in."""

    directory = write_manifest(
        config.batches_root,
        BATCH_ID,
        ["r1", "r2"],
        analysis_task_id=ANALYSIS_TASK,
        runner_protocol_revision="v2.2.0",
    )
    (directory / "findings.md").write_text("# Findings\n", encoding="utf-8")
    write_closure(config.batches_root, BATCH_ID, analysis_task_id=ANALYSIS_TASK)
    for draft_id in DRAFTS:
        write_draft(config.batches_root, BATCH_ID, draft_id)
    return directory


def record(config: evolution.EvolutionConfig, experiment_id: str) -> dict:
    return json.loads((config.experiments_root / experiment_id / "experiment.json").read_text(encoding="utf-8"))


def candidate_of(config: evolution.EvolutionConfig, experiment_id: str) -> str:
    """The revision the experiment's last round pinned — what a promotion of it
    carries, and what any record naming that round has to agree with."""

    return record(config, experiment_id)["rounds"][-1]["seal"]["candidate_revision"]


def rewrite(config: evolution.EvolutionConfig, experiment_id: str, **changes) -> None:
    """Edit an experiment record in place, for the states no operation writes yet
    — a sealed round, a terminal decision."""

    path = config.experiments_root / experiment_id / "experiment.json"
    current = json.loads(path.read_text(encoding="utf-8"))
    current.update(changes)
    path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def index(config: evolution.EvolutionConfig) -> str:
    path = analysis_task.index_path(config)
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def ledger_types(config: evolution.EvolutionConfig) -> list[str]:
    return [record["record_type"] for record in evolution.read_records(config)]


def finish(config: evolution.EvolutionConfig, task_id: str) -> Path:
    """Take one admitted copy to `completed`, the way the session working it
    does: the lifecycle above the provenance changes, the provenance does not."""

    path = analysis_task.task_path(config, task_id)
    path.write_text(
        path.read_text(encoding="utf-8").replace("status: pending", "status: completed"),
        encoding="utf-8",
    )
    return path


def advance(config: evolution.EvolutionConfig, ref: str) -> str:
    """One commit of the work an admitted task does, landed on the experiment
    ref — a fast-forward, which is the only way that ref ever moves."""

    revision = git_commit(config.repo_root, "candidate work")
    git_update_ref(config.repo_root, ref, revision)
    return revision


def seal(config: evolution.EvolutionConfig, draft_ids: list[str]) -> str:
    """Admit, work, and seal round 1: the candidate-ready state every revision
    and every terminal decision starts from."""

    admission = experiments.create(config, draft_ids, now=NOW)
    for item in admission.admitted:
        finish(config, item.task_id)
    advance(config, admission.ref)
    return experiments.seal_round(config, now=LATER).candidate_revision


# --- grouped admission -------------------------------------------------------


def test_a_grouped_admission_creates_the_ref_the_record_and_the_tasks(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """One operation, four writes, in the order that makes them recoverable: the
    ref, the record that makes the admission real, the task copies, the audit."""

    result = experiments.create(config, ["loader-fallback", "hook-side-loader"], now=NOW)

    assert result.experiment_id == EXP_01
    assert result.created is True
    assert result.base_revision == git_rev(config.repo_root, "HEAD")
    assert git_rev(config.repo_root, result.ref) == result.base_revision

    written = record(config, EXP_01)
    assert written["batch_id"] == BATCH_ID
    assert written["base_release_ref"] == "v2.2.0"
    assert written["decision"] is None
    assert [task["draft_id"] for task in written["rounds"][0]["tasks"]] == [
        "hook-side-loader",
        "loader-fallback",
    ]
    assert written["rounds"][0]["seal"] is None
    assert all(task["completion_observed_at"] is None for task in written["rounds"][0]["tasks"])

    for item in result.admitted:
        assert item.task_path.is_file()
        assert item.draft_sha256 == draft_sha256(item.draft_id)
        assert f"| {item.task_id} | pending |" in index(config)
    assert ledger_types(config) == ["experiment-created", "tasks-admitted", "tasks-admitted"]


def test_the_copy_states_what_only_the_admission_knows(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """A change task must name the base revision and the experiment and draft it
    came from (contract: Evolution task requirements) — none of which exists when
    the draft is written. Without the ref it would also have nowhere to commit."""

    result = experiments.create(config, ["loader-fallback"], now=NOW)
    text = result.admitted[0].task_path.read_text(encoding="utf-8")

    assert text.startswith("---\nid: 2026-08-01-loader-fallback\n")
    assert "## Admission" in text
    assert f"`{EXP_01}`, round 1" in text
    assert result.base_revision in text
    assert draft_sha256("loader-fallback") in text
    assert lineage.experiment_ref(EXP_01) in text
    assert "v2.2.0" in text
    # The draft's own body survives whole, and the block sits above its first
    # section rather than inside one.
    assert text.index("## Admission") < text.index("## Goal") < text.index("## Session log")
    assert "Implement the loader-fallback disposition" in text


def test_an_admitted_draft_stays_in_the_batch_that_proposed_it(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Admission copies rather than moves: `.ai-tasks/` is machine-local and
    close-out archives a finished task away, so a draft that had moved out would
    leave nothing saying what was proposed or which experiment took it."""

    experiments.create(config, ["loader-fallback"], now=NOW)

    assert (batch / "proposed-tasks" / "loader-fallback.md").is_file()
    gate = lineage.describe(config).current.gate
    assert gate.consumed == {"loader-fallback": EXP_01}
    assert set(gate.waiting) == {"hook-side-loader", "not-worth-it"}
    assert gate.missing == ()


def test_the_first_experiment_freezes_the_base_every_later_one_starts_from(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Invariant 15. The batch freeze deliberately pins nothing — it happens
    before anyone knows a change is warranted — and alternatives built on
    different sources would not be alternatives to each other."""

    first = experiments.create(config, ["loader-fallback"], now=NOW)
    rewrite(config, EXP_01, decision=experiment_decision("abandoned"))
    moved = git_commit(config.repo_root, "unrelated source work")
    assert moved != first.base_revision

    second = experiments.create(config, ["hook-side-loader"], now=NOW)

    assert second.experiment_id == EXP_02
    assert second.base_revision == first.base_revision
    assert git_rev(config.repo_root, second.ref) == first.base_revision
    assert record(config, EXP_02)["base_release_ref"] == "v2.2.0"


def test_a_base_that_is_not_the_batchs_is_refused_rather_than_reconciled(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    experiments.create(config, ["loader-fallback"], now=NOW)
    rewrite(config, EXP_01, decision=experiment_decision("abandoned"))
    git_commit(config.repo_root, "unrelated source work")

    with pytest.raises(evolution.BatchError, match="froze its base at"):
        experiments.create(config, ["hook-side-loader"], base="HEAD", now=NOW)


def test_the_base_is_recorded_as_the_commit_not_the_name_that_was_typed(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """A tag or a branch means a different commit tomorrow; the record has to keep
    naming this one."""

    result = experiments.create(config, ["loader-fallback"], base="v2.2.0", now=NOW)

    assert result.base_revision == git_rev(config.repo_root, "v2.2.0")
    assert result.base_revision != git_rev(config.repo_root, "HEAD")


def test_a_redo_naming_a_different_base_is_refused_rather_than_ignored(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """A resumed admission still has a base, and it is not going to change; an
    operator who named a revision expecting it to be used has to be told."""

    experiments.create(config, ["loader-fallback"], base="v2.2.0", now=NOW)

    with pytest.raises(evolution.BatchError, match="froze its base at"):
        experiments.create(config, ["loader-fallback"], base="HEAD", now=NOW)


def test_a_base_this_repository_does_not_hold_is_refused(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    with pytest.raises(evolution.BatchError, match="cannot resolve"):
        experiments.create(config, ["loader-fallback"], base="v9.9.9", now=NOW)


# --- the gate ----------------------------------------------------------------


def test_a_draft_is_consumed_once(config: evolution.EvolutionConfig, batch: Path) -> None:
    """Admitting is terminal for a proposal. Re-proposing the idea means a new
    draft under a new id, whose own bytes state what the second proposal was."""

    experiments.create(config, ["loader-fallback"], now=NOW)
    rewrite(config, EXP_01, decision=experiment_decision("abandoned"))

    with pytest.raises(evolution.BatchError, match=f"already admitted by {EXP_01}"):
        experiments.create(config, ["loader-fallback"], now=NOW)


def test_a_declined_draft_is_never_admitted(config: evolution.EvolutionConfig, batch: Path) -> None:
    write_rejected_drafts(config.batches_root, BATCH_ID, [rejection("not-worth-it")])

    with pytest.raises(evolution.BatchError, match="was declined at"):
        experiments.create(config, ["not-worth-it"], now=NOW)


def test_an_admitted_draft_is_never_declined(config: evolution.EvolutionConfig, batch: Path) -> None:
    experiments.create(config, ["loader-fallback"], now=NOW)

    with pytest.raises(evolution.BatchError, match="already admitted"):
        experiments.reject(config, ["loader-fallback"], reason="second thoughts", now=NOW)


def test_a_draft_nobody_proposed_is_refused(config: evolution.EvolutionConfig, batch: Path) -> None:
    with pytest.raises(evolution.BatchError, match="does not exist"):
        experiments.create(config, ["never-proposed"], now=NOW)


@pytest.mark.parametrize(
    "draft_id",
    ["../../../etc/passwd", ".hidden", "loader fallback", "Loader-Fallback", "loader-fallback.md", ""],
    ids=["traversal", "dot-file", "space", "upper-case", "extension", "empty"],
)
def test_an_unsafe_draft_id_never_reaches_a_path(
    config: evolution.EvolutionConfig, batch: Path, draft_id: str
) -> None:
    """Checked as an id before anything joins it to a directory: a draft id is a
    kebab slug, which is what makes it one path segment under `proposed-tasks/`
    rather than a name that could reach anywhere else."""

    with pytest.raises(evolution.BatchError, match="cannot be draft ids"):
        experiments.create(config, [draft_id], now=NOW)


def test_a_draft_named_twice_in_one_selection_is_refused(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    with pytest.raises(evolution.BatchError, match="named twice"):
        experiments.create(config, ["loader-fallback", "loader-fallback"], now=NOW)


def test_an_empty_selection_is_refused(config: evolution.EvolutionConfig, batch: Path) -> None:
    with pytest.raises(evolution.BatchError, match="no draft was named"):
        experiments.create(config, [], now=NOW)


def test_an_admission_records_an_unambiguous_moment(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Every timestamp here goes into a committed record that another machine
    reads; a naive one says nothing about when the admission happened."""

    with pytest.raises(evolution.BatchError, match="timezone-aware"):
        experiments.create(config, ["loader-fallback"], now=datetime(2026, 8, 5, 9, 0, 0))


# --- what the batch has to be ------------------------------------------------


def test_admission_needs_a_current_batch(config: evolution.EvolutionConfig) -> None:
    with pytest.raises(evolution.BatchError, match="no batch is current"):
        experiments.create(config, ["loader-fallback"], now=NOW)


def test_a_concluded_batch_has_no_gate_left(config: evolution.EvolutionConfig, batch: Path) -> None:
    write_outcome(config.batches_root, BATCH_ID)

    with pytest.raises(evolution.BatchError, match="no batch is current"):
        experiments.create(config, ["loader-fallback"], now=NOW)


def test_two_current_batches_stop_the_admission(config: evolution.EvolutionConfig, batch: Path) -> None:
    """The same whole-lineage reading the freeze and `status` use: whichever of
    the two a writer picked, the drafts it went on to admit belong to the other."""

    write_manifest(config.batches_root, SECOND_BATCH, ["r3"], analysis_task_id="2026-08-04-second-analysis")

    with pytest.raises(evolution.BatchError, match="more than one current batch"):
        experiments.create(config, ["loader-fallback"], now=NOW)


def test_admission_waits_for_the_analysis_stage_to_end(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """A draft admitted before the analysis task completes implements
    dispositions nobody has reviewed — `findings.md` is written while that task
    is still being developed."""

    batch.joinpath("analysis-complete.json").unlink()

    with pytest.raises(evolution.BatchError, match="still in its analysis stage"):
        experiments.create(config, ["loader-fallback"], now=NOW)


def test_a_completed_analysis_task_closes_the_stage_before_the_gate_is_read(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The operation publishes the closure record first, exactly as the freeze
    does, so the machine holding the finished analysis task does not have to run
    a second command before it can admit anything."""

    batch.joinpath("analysis-complete.json").unlink()
    spec_text = (
        f"---\nid: {ANALYSIS_TASK}\nstatus: completed\n---\n\n"
        f"{analysis_task.task_heading(BATCH_ID)}\n\n"
        f"evolution/batches/{BATCH_ID}/manifest.json\n"
    )
    analysis_task.publish_task(config, ANALYSIS_TASK, spec_text, description="analysis task")

    result = experiments.create(config, ["loader-fallback"], now=NOW)

    assert result.created is True
    assert batch.joinpath("analysis-complete.json").is_file()


def test_a_second_experiment_while_one_is_open_is_refused(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Invariant 14. Terminal history never blocks an alternative — an open
    attempt does, and ending it keeps it as evidence."""

    experiments.create(config, ["loader-fallback"], now=NOW)

    with pytest.raises(evolution.BatchError, match="already has an open experiment"):
        experiments.create(config, ["hook-side-loader"], now=NOW)


def unreadable_replays(config: evolution.EvolutionConfig, experiment_id: str) -> Path:
    """One experiment's replay evidence, as bytes no reader will accept."""

    path = config.experiments_root / experiment_id / "replays.json"
    path.write_text("{ this was a replay record\n", encoding="utf-8")
    return path


def test_a_replay_record_nobody_can_read_stops_the_lifecycle(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The preamble's rule applied to the one persisted state these operations do
    not otherwise touch. Replay's own writes read that file anyway; the
    lifecycle's never do, so without this an attempt can be added to, revised, or
    ended over a record nobody can read."""

    seal(config, ["loader-fallback"])
    unreadable_replays(config, EXP_01)

    for operation in (
        lambda: experiments.add_tasks(config, ["hook-side-loader"], now=LATEST),
        lambda: experiments.revise(config, reason="the candidate regressed", now=LATEST),
        lambda: experiments.abandon(config, reason="the approach does not hold", now=LATEST),
    ):
        with pytest.raises(evolution.BatchError, match="unreadable replay record"):
            operation()

    assert record(config, EXP_01)["decision"] is None
    assert len(record(config, EXP_01)["rounds"]) == 1


def test_ending_an_attempt_cannot_hide_a_replay_record_nobody_can_read(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The reason the reading covers every experiment of the batch rather than the
    open one. Evidence is derived for the open experiment only, so a decision
    recorded over an attempt whose replay record is malformed would retire the
    finding along with it — the file staying on disk, saying something no reader
    accepts, with nothing left to report it."""

    seal(config, ["loader-fallback"])
    experiments.abandon(config, reason="the approach does not hold", now=LATEST)
    unreadable_replays(config, EXP_01)

    # Nothing derives evidence for it any more: this is what would have hidden it.
    assert phase.describe(config, now=LATEST).evidence is None

    # And it still stops the batch, from the next attempt through the conclusion
    # that would end the batch over it.
    with pytest.raises(evolution.BatchError, match="unreadable replay record"):
        experiments.create(config, ["hook-side-loader"], now=LATEST)
    with pytest.raises(evolution.BatchError, match="unreadable replay record"):
        experiments.conclude_no_change(config, reason="the evidence justified no change", now=LATEST)


# --- add-tasks ---------------------------------------------------------------


def test_further_drafts_join_the_open_round(config: evolution.EvolutionConfig, batch: Path) -> None:
    experiments.create(config, ["loader-fallback"], now=NOW)

    result = experiments.add_tasks(config, ["hook-side-loader"], now=NOW)

    assert result.created is False
    assert result.round_number == 1
    rounds = record(config, EXP_01)["rounds"]
    assert len(rounds) == 1
    assert [task["draft_id"] for task in rounds[0]["tasks"]] == ["loader-fallback", "hook-side-loader"]
    assert result.admitted[0].task_path.is_file()
    assert ledger_types(config).count("tasks-admitted") == 2


def test_add_tasks_needs_an_open_experiment(config: evolution.EvolutionConfig, batch: Path) -> None:
    with pytest.raises(evolution.BatchError, match="no open experiment to admit into"):
        experiments.add_tasks(config, ["loader-fallback"], now=NOW)


def test_a_candidate_ready_round_takes_no_further_work(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Invariant 16: its candidate is pinned and its evidence names it, so
    admitting into it would change what that evidence measured after the fact."""

    experiments.create(config, ["loader-fallback"], now=NOW)
    sealed = record(config, EXP_01)["rounds"]
    sealed[0]["tasks"][0]["completion_observed_at"] = "2026-08-06T09:00:00Z"
    sealed[0]["seal"] = {"sealed_at": "2026-08-06T10:00:00Z", "candidate_revision": git_rev(config.repo_root, "HEAD")}
    rewrite(config, EXP_01, rounds=sealed)

    with pytest.raises(evolution.BatchError, match="is candidate-ready"):
        experiments.add_tasks(config, ["hook-side-loader"], now=NOW)


def test_admitting_one_new_draft_beside_a_consumed_one_is_refused(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """A partial repeat says two things at once — redo this admission, and admit
    something new — and the record cannot hold both readings."""

    experiments.create(config, ["loader-fallback"], now=NOW)

    with pytest.raises(evolution.BatchError, match="already admitted into round 1"):
        experiments.add_tasks(config, ["loader-fallback", "hook-side-loader"], now=NOW)


# --- task provenance ---------------------------------------------------------


def malformed(**fields: str | None) -> str:
    """A draft one frontmatter field away from admissible. `None` drops a field."""

    head = {
        "id": "2026-08-01-malformed",
        "status": "pending",
        "session-est": "0/1",
        "blockers": "[]",
        "claimed-by": "",
    }
    head.update(fields)
    return draft_body(
        "malformed",
        frontmatter="".join(f"{key}: {value}\n" for key, value in head.items() if value is not None),
    )


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (malformed(id="not-a-date"), "not a date-prefixed task slug"),
        (malformed(status="in_progress"), "carries status 'pending'"),
        (malformed(**{"session-est": "1/3"}), r"session-est '1/3' is not '0/<total>'"),
        (malformed(**{"session-est": "0/0"}), r"session-est '0/0' is not '0/<total>'"),
        (malformed(blockers="[external:awaiting the spec]"), r"is not '\[\]'"),
        (malformed(**{"claimed-by": "019feb-a-session@2026-08-04T09:00:00Z"}), "names a session"),
        (malformed(**{"session-est": None}), "carries no 'session-est'"),
        (malformed(blockers=None), "carries no 'blockers'"),
        (malformed(**{"claimed-by": None}), "carries no 'claimed-by'"),
        (malformed(id=None), "carries no 'id'"),
        (draft_body("malformed", closed=False), "never closed by a '---' line"),
        ("# Malformed\n\n## Session log\n", "no frontmatter block"),
        (draft_body("malformed", sections=("Goal", "Session log")), r"\['## Scope', '## Acceptance'\] section"),
        (draft_body("malformed", sections=("Goal", "Scope", "Acceptance")), r"\['## Session log'\] section"),
        (
            draft_body("malformed", sections=("Goal", "Scope", "Acceptance", "Session logs")),
            r"no \['## Session log'\] section",
        ),
        (
            draft_body("malformed", sections=("Goal", "Scope", "Acceptance", "Session log", "Session log")),
            r"\['## Session log'\] section\(s\) declared more than once",
        ),
        (
            draft_body("malformed", sections=("Goal", "Scope", "Scope", "Acceptance", "Session log")),
            r"\['## Scope'\] section\(s\) declared more than once",
        ),
        (
            draft_body(
                "malformed",
                section_text={"Session log": "### 2026-08-02 / 019feb-a-session / (pending → in_progress)"},
            ),
            "already carries '### 2026-08-02",
        ),
        (
            draft_body("malformed", section_text={"Session log": "- Done: nothing yet, but this is a log entry"}),
            "already carries '- Done: nothing yet",
        ),
        (
            draft_body("malformed", sections=("Goal", "Scope", "Acceptance", "Admission", "Session log")),
            "already carries an '## Admission' section",
        ),
        (
            draft_body(
                "malformed",
                section_text={"Goal": "    ## Admission\n\n    - Base revision `deadbeef`: work from that one."},
            ),
            "'## Admission' is indented",
        ),
    ],
    ids=[
        "unsafe-id",
        "already-worked-on",
        "session-already-consumed",
        "estimate-of-no-work",
        "already-blocked",
        "already-claimed",
        "no-estimate",
        "no-blockers",
        "no-claim-field",
        "no-id",
        "unterminated-frontmatter",
        "no-frontmatter",
        "no-scope-or-acceptance",
        "no-session-log",
        "session-log-lookalike-heading",
        "two-session-logs",
        "two-scopes",
        "session-log-with-an-entry",
        "session-log-with-a-line-under-it",
        "admission-section-of-its-own",
        "indented-heading-lookalike",
    ],
)
def test_a_draft_that_is_not_an_inert_task_file_is_refused(
    config: evolution.EvolutionConfig, batch: Path, body: str, message: str
) -> None:
    """Admission is a copy, so what the gate checks is what the copy becomes: a
    pending task in the pool turn selection dispatches from, claimed by a session
    that increments its estimate, worked from its scope, reviewed against its
    acceptance, and recording itself in its session log. Nothing downstream ever
    reads it as a proposal again, so a proposal that is a task file in name only
    is one here or nowhere.

    The body is checked as sections and not as text for the same reason the
    frontmatter is: a heading that occurs somewhere says nothing about whether
    the section is there once and inert. A log with an entry under it is a task
    somebody has already worked — the state the other half of the check
    (`pending`, `0/<total>`, unclaimed) refuses from the frontmatter side. A line
    that only looks like a heading is refused outright, because it is a section to
    the session working the copy and text to everything that checks one: an
    indented `## Admission` would ride into the pool as provenance no record
    accounts for, standing beside the provenance that admission writes."""

    write_draft(config.batches_root, BATCH_ID, "malformed", body=body)

    with pytest.raises(evolution.BatchError, match=message):
        experiments.create(config, ["malformed"], now=NOW)

    assert not analysis_task.tasks_root(config).exists()


def test_a_field_declared_twice_never_reaches_the_active_pool(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """A file saying `pending` at the top and `completed` further down says two
    things about one task, and every reader takes whichever it reaches first."""

    write_draft(
        config.batches_root,
        BATCH_ID,
        "malformed",
        body=malformed().replace("blockers: []\n", "blockers: []\nstatus: completed\n"),
    )

    with pytest.raises(evolution.BatchError, match="declares \\['status'\\] more than once"):
        experiments.create(config, ["malformed"], now=NOW)


def test_an_unterminated_frontmatter_never_takes_the_admission_block_into_itself(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """What the shape check is protecting, stated as the failure it prevents: the
    provenance goes above the first `## ` line, and in a file whose block is
    never closed, that line is inside the frontmatter."""

    write_draft(config.batches_root, BATCH_ID, "malformed", body=draft_body("malformed", closed=False))

    with pytest.raises(evolution.BatchError, match="has no body to admit"):
        experiments.create(config, ["malformed"], now=NOW)

    assert not (config.experiments_root / EXP_01).exists()
    assert analysis_task.existing_task_path(config, "2026-08-01-malformed") is None


def test_the_analysis_task_this_controller_generates_would_pass_its_own_gate(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """One shape, checked from both ends. The gate's idea of an inert pending task
    is meant to be the taskfile schema's, and the nearest thing to a second
    opinion about that is the task this same package writes for a freeze — if the
    two ever disagree, one of them is wrong about the schema."""

    generated = analysis_task.render(
        analysis_task.AnalysisTaskSpec(
            task_id="2026-08-05-evolution-batch-0001-analysis",
            batch_id=BATCH_ID,
            manifest_relative_path=f"evolution/batches/{BATCH_ID}/manifest.json",
            proposed_tasks_relative_path=f"evolution/batches/{BATCH_ID}/proposed-tasks",
            findings_relative_path=f"evolution/batches/{BATCH_ID}/findings.md",
            closure_relative_path=f"evolution/batches/{BATCH_ID}/analysis-complete.json",
            artifacts_root=".ai-evolution/artifacts",
            task_count=3,
            report_count=4,
            runner_protocol_revision="v2.2.0",
            config_sha256="0" * 64,
            forced=False,
            force_justification=None,
        )
    )
    write_draft(config.batches_root, BATCH_ID, "generated-shape", body=generated)

    result = experiments.create(config, ["generated-shape"], now=NOW)

    assert result.admitted[0].task_id == "2026-08-05-evolution-batch-0001-analysis"


def test_a_task_already_in_flight_is_never_overwritten(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """That file may already carry a session log, and destroying one to satisfy
    an admission is not a trade this controller makes."""

    analysis_task.publish_task(config, "2026-08-01-loader-fallback", "---\nid: x\n---\n", description="task")

    with pytest.raises(evolution.BatchError, match="already exists at"):
        experiments.create(config, ["loader-fallback"], now=NOW)


def test_a_task_id_the_archive_already_holds_is_refused(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """An archived id is a decision that was closed; a new task under it would
    make the completed work unfindable from its own name."""

    archived = analysis_task.archived_task_path(config, "2026-08-01-loader-fallback")
    archived.parent.mkdir(parents=True, exist_ok=True)
    archived.write_text("---\nid: 2026-08-01-loader-fallback\nstatus: completed\n---\n", encoding="utf-8")

    with pytest.raises(evolution.BatchError, match="already exists at"):
        experiments.create(config, ["loader-fallback"], now=NOW)


def test_two_drafts_declaring_one_task_id_are_refused(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """One of them would take the other's file, and the record could name
    neither: which bytes did that task implement?"""

    write_draft(config.batches_root, BATCH_ID, "first-idea", body=draft_body("shared", task_id="2026-08-01-shared"))
    write_draft(config.batches_root, BATCH_ID, "second-idea", body=draft_body("shared", task_id="2026-08-01-shared"))

    with pytest.raises(evolution.BatchError, match="both declare task id"):
        experiments.create(config, ["first-idea", "second-idea"], now=NOW)


def test_a_task_id_this_batch_already_admitted_is_refused(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Across the batch's whole history, not just the open round: the completion
    observation that seals a round has to belong to one admission."""

    experiments.create(config, ["loader-fallback"], now=NOW)
    rewrite(config, EXP_01, decision=experiment_decision("abandoned"))
    write_draft(
        config.batches_root,
        BATCH_ID,
        "loader-fallback-v2",
        body=draft_body("loader-fallback-v2", task_id="2026-08-01-loader-fallback"),
    )

    with pytest.raises(evolution.BatchError, match="already admitted by"):
        experiments.create(config, ["loader-fallback-v2"], now=NOW)


# --- interruption and recovery -----------------------------------------------


def test_an_interrupted_create_leaves_no_task_nothing_accounts_for(
    config: evolution.EvolutionConfig, batch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one direction that matters. An orphaned active task is work a turn
    selection dispatches with no experiment behind it, so the record is written
    before any copy and a failure between the two leaves the copies missing."""

    monkeypatch.setattr(
        experiments,
        "_write_tasks",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("interrupted")),
    )
    with pytest.raises(OSError):
        experiments.create(config, ["loader-fallback", "hook-side-loader"], now=NOW)

    assert not analysis_task.tasks_root(config).exists()
    assert record(config, EXP_01)["rounds"][0]["tasks"]


def test_the_same_selection_again_finishes_an_interrupted_admission(
    config: evolution.EvolutionConfig, batch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redoing the command is the resume path: the record made the admission
    real, so what is left of it is the copies."""

    monkeypatch.setattr(
        experiments,
        "_write_tasks",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("interrupted")),
    )
    with pytest.raises(OSError):
        experiments.create(config, ["loader-fallback", "hook-side-loader"], now=NOW)
    monkeypatch.undo()

    result = experiments.create(config, ["loader-fallback", "hook-side-loader"], now=NOW)

    assert result.created is False
    assert sorted(result.restored) == ["2026-08-01-hook-side-loader", "2026-08-01-loader-fallback"]
    assert len(record(config, EXP_01)["rounds"]) == 1
    for item in result.admitted:
        assert item.task_path.is_file()
        assert f"| {item.task_id} | pending |" in index(config)
    # The audit line is written last, so this interruption cost every line the
    # operation would have appended — and the redo appends none of them rather
    # than guessing which the first run got to. Nothing derives state from the
    # ledger, which is what makes that the cheap failure it is.
    assert ledger_types(config) == []


def test_a_redo_finishes_the_copies_an_admission_got_halfway_through(
    config: evolution.EvolutionConfig, batch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The realistic interruption: one copy written, the next not. Both are named
    by the record, so the redo writes what is missing and leaves what is there —
    that file may already carry a session log."""

    published = analysis_task.publish_task

    def once(config_, task_id, text, *, description):
        if task_id == "2026-08-01-loader-fallback":
            raise OSError("interrupted")
        return published(config_, task_id, text, description=description)

    monkeypatch.setattr(analysis_task, "publish_task", once)
    with pytest.raises(OSError):
        experiments.create(config, ["loader-fallback", "hook-side-loader"], now=NOW)
    monkeypatch.undo()
    started = analysis_task.task_path(config, "2026-08-01-hook-side-loader")
    started.write_text(started.read_text(encoding="utf-8") + "\n### claimed already\n", encoding="utf-8")

    result = experiments.create(config, ["loader-fallback", "hook-side-loader"], now=NOW)

    assert result.restored == ("2026-08-01-loader-fallback",)
    assert analysis_task.task_path(config, "2026-08-01-loader-fallback").is_file()
    assert "### claimed already" in started.read_text(encoding="utf-8")
    assert index(config).count("| 2026-08-01-hook-side-loader |") == 1


def test_a_completed_admission_run_again_changes_nothing(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    experiments.create(config, ["loader-fallback"], now=NOW)
    before = snapshot(config.repo_root)

    result = experiments.create(config, ["loader-fallback"], now=NOW)

    assert result.created is False
    assert result.restored == ()
    assert snapshot(config.repo_root) == before


def test_add_tasks_run_again_finishes_its_own_admission(
    config: evolution.EvolutionConfig, batch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    experiments.create(config, ["loader-fallback"], now=NOW)
    monkeypatch.setattr(
        experiments,
        "_write_tasks",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("interrupted")),
    )
    with pytest.raises(OSError):
        experiments.add_tasks(config, ["hook-side-loader"], now=NOW)
    monkeypatch.undo()

    result = experiments.add_tasks(config, ["hook-side-loader"], now=NOW)

    assert result.restored == ("2026-08-01-hook-side-loader",)
    assert analysis_task.task_path(config, "2026-08-01-hook-side-loader").is_file()
    assert [task["draft_id"] for task in record(config, EXP_01)["rounds"][0]["tasks"]] == [
        "loader-fallback",
        "hook-side-loader",
    ]


def test_a_ref_an_interrupted_create_left_at_the_base_is_adopted(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The ref is created first because it is the one thing that must never be
    created twice; finding it already at the base is that step already done."""

    base = git_rev(config.repo_root, "HEAD")
    git_update_ref(config.repo_root, lineage.experiment_ref(EXP_01), base)

    result = experiments.create(config, ["loader-fallback"], now=NOW)

    assert result.base_revision == base
    assert git_rev(config.repo_root, result.ref) == base


def test_a_ref_standing_anywhere_else_stops_the_creation(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """An experiment id is never reused, so a ref already holding commits under
    the id about to be created belongs to work this controller cannot account
    for — and moving it would make those trees unreachable."""

    other = git_commit(config.repo_root, "someone else's work")
    git_update_ref(config.repo_root, lineage.experiment_ref(EXP_01), other)

    with pytest.raises(evolution.BatchError, match="already exists at"):
        experiments.create(config, ["loader-fallback"], base="v2.2.0", now=NOW)


def test_the_ref_is_never_moved_by_a_later_operation(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Work accumulates on the ref while a round is open, and admitting more
    drafts into that round must not reset it to the base."""

    experiments.create(config, ["loader-fallback"], base="v2.2.0", now=NOW)
    tip = git_commit(config.repo_root, "round-1 work")
    git_update_ref(config.repo_root, lineage.experiment_ref(EXP_01), tip)

    experiments.add_tasks(config, ["hook-side-loader"], now=NOW)

    assert git_rev(config.repo_root, lineage.experiment_ref(EXP_01)) == tip


def test_a_ref_off_the_history_its_record_pins_stops_further_admission(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """`lineage.py` reports a ref disagreement as data so a status can still be
    read; this is the other half of that decision. Work admitted onto a ref
    standing off the pinned history would be sealed as part of a candidate the
    record cannot identify."""

    experiments.create(config, ["loader-fallback"], now=NOW)
    git_update_ref(
        config.repo_root,
        lineage.experiment_ref(EXP_01),
        git_unrelated_commit(config.repo_root, "a history of its own"),
    )

    with pytest.raises(evolution.BatchError, match="not on the history of"):
        experiments.add_tasks(config, ["hook-side-loader"], now=NOW)


def test_a_round_that_does_not_build_on_the_one_before_it_stops_further_admission(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The pin chain, not only the tip: an earlier round's candidate off the
    frozen base leaves that round's evidence describing a tree the batch never
    started from, however well-placed the ref itself is."""

    write_experiment(
        config.experiments_root,
        EXP_01,
        base_revision=git_rev(config.repo_root, "HEAD"),
        rounds=[
            experiment_round(
                1,
                tasks=[admitted_task("loader-fallback")],
                candidate_revision=git_unrelated_commit(config.repo_root, "off the base"),
            ),
            experiment_round(2, tasks=[admitted_task("not-worth-it", complete=False)], reason="replay regressed"),
        ],
    )

    with pytest.raises(evolution.BatchError, match="does not descend from"):
        experiments.add_tasks(config, ["hook-side-loader"], now=NOW)


def test_a_clone_without_the_ref_still_admits(config: evolution.EvolutionConfig, batch: Path) -> None:
    """`refs/evolution/experiments/*` is outside the default fetch refspec, so an
    absent ref is the ordinary state of every clone but one — "cannot tell" is
    not a refusal."""

    write_experiment(
        config.experiments_root,
        EXP_01,
        base_revision=git_rev(config.repo_root, "HEAD"),
        rounds=[experiment_round(1, tasks=[admitted_task("loader-fallback", complete=False)])],
    )

    result = experiments.add_tasks(config, ["hook-side-loader"], now=NOW)

    assert result.admitted[0].task_path.is_file()


def test_a_draft_edited_after_admission_stops_the_restore(
    config: evolution.EvolutionConfig, batch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The copy owed to a task has to be the proposal that was admitted; the
    record's hash is what says which bytes those were — so the edit here is one
    that leaves a perfectly admissible draft behind, and is caught for being
    different rather than for being malformed."""

    monkeypatch.setattr(
        experiments,
        "_write_tasks",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("interrupted")),
    )
    with pytest.raises(OSError):
        experiments.create(config, ["loader-fallback"], now=NOW)
    monkeypatch.undo()
    draft = batch / "proposed-tasks" / "loader-fallback.md"
    draft.write_text(
        draft.read_text(encoding="utf-8").replace("and nothing else.", "and the loader test beside them."),
        encoding="utf-8",
    )

    with pytest.raises(evolution.BatchError, match="no longer matches the bytes"):
        experiments.create(config, ["loader-fallback"], now=NOW)


def test_a_task_already_seen_through_is_never_recreated(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Close-out archives a finished task away, so its absence from the active
    pool is the ordinary end state — recreating it as pending would reopen work
    that finished."""

    experiments.create(config, ["loader-fallback"], now=NOW)
    analysis_task.task_path(config, "2026-08-01-loader-fallback").unlink()
    rounds = record(config, EXP_01)["rounds"]
    rounds[0]["tasks"][0]["completion_observed_at"] = "2026-08-06T09:00:00Z"
    rewrite(config, EXP_01, rounds=rounds)

    result = experiments.create(config, ["loader-fallback"], now=NOW)

    assert result.restored == ()
    assert analysis_task.existing_task_path(config, "2026-08-01-loader-fallback") is None


def test_a_task_the_record_observed_complete_is_never_relisted(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The observation is enough on its own. The copy is not owed, so nothing
    about it goes back into the active list — whatever the file left behind on
    this machine happens to say about itself."""

    experiments.create(config, ["loader-fallback"], now=NOW)
    rounds = record(config, EXP_01)["rounds"]
    rounds[0]["tasks"][0]["completion_observed_at"] = "2026-08-06T09:00:00Z"
    rewrite(config, EXP_01, rounds=rounds)
    analysis_task.index_path(config).unlink()

    again = experiments.create(config, ["loader-fallback"], now=NOW)

    assert again.restored == ()
    assert index(config) == ""


def test_a_ref_off_its_recorded_history_stops_the_redo_of_a_create(
    config: evolution.EvolutionConfig, batch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A redo writes — the copies the interrupted run never made — so it is
    guarded exactly as the admission it is finishing. Those copies tell their
    sessions which ref to commit on, and a ref standing off the history the record
    pins is not one to send work to."""

    monkeypatch.setattr(
        experiments,
        "_write_tasks",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("interrupted")),
    )
    with pytest.raises(OSError):
        experiments.create(config, ["loader-fallback"], now=NOW)
    monkeypatch.undo()
    git_update_ref(
        config.repo_root,
        lineage.experiment_ref(EXP_01),
        git_unrelated_commit(config.repo_root, "a history of its own"),
    )

    with pytest.raises(evolution.BatchError, match="not on the history of"):
        experiments.create(config, ["loader-fallback"], now=NOW)

    assert analysis_task.existing_task_path(config, "2026-08-01-loader-fallback") is None


def test_an_unrelated_task_at_the_admitted_id_is_never_adopted(
    config: evolution.EvolutionConfig, batch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding the file present is what makes a redo declare that copy done, so
    what is at the path decides whether the admission has its task at all.
    Adopting whatever is there lists somebody else's work as this experiment's,
    puts a `pending` row on it, and hands the record a task implementing bytes it
    never admitted."""

    monkeypatch.setattr(
        experiments,
        "_write_tasks",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("interrupted")),
    )
    with pytest.raises(OSError):
        experiments.create(config, ["loader-fallback"], now=NOW)
    monkeypatch.undo()
    unrelated = "---\nid: 2026-08-01-loader-fallback\nstatus: in_progress\n---\n\n# Someone else's work\n"
    analysis_task.publish_task(config, "2026-08-01-loader-fallback", unrelated, description="task")

    with pytest.raises(evolution.BatchError, match="is not the copy"):
        experiments.create(config, ["loader-fallback"], now=NOW)

    assert analysis_task.task_path(config, "2026-08-01-loader-fallback").read_text(encoding="utf-8") == unrelated
    assert "| 2026-08-01-loader-fallback |" not in index(config)


def test_a_file_wearing_the_admitted_copy_s_markers_is_never_adopted(
    config: evolution.EvolutionConfig, batch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Identity is read from the structures that own those values, not from the
    text containing them. This file declares `not-id:` and mentions the heading,
    the experiment, and the digest in prose — every marker present as a string,
    and not one of them said by the file about itself."""

    monkeypatch.setattr(
        experiments,
        "_write_tasks",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("interrupted")),
    )
    with pytest.raises(OSError):
        experiments.create(config, ["loader-fallback"], now=NOW)
    monkeypatch.undo()
    lookalike = (
        "---\n"
        "not-id: 2026-08-01-loader-fallback\n"
        "status: in_progress\n"
        "---\n"
        "\n"
        "# Someone else's work\n"
        "\n"
        f"Discussed under `## Admission` with `{EXP_01}` and the digest\n"
        f"`{draft_sha256('loader-fallback')}` in passing.\n"
    )
    analysis_task.publish_task(config, "2026-08-01-loader-fallback", lookalike, description="task")

    with pytest.raises(evolution.BatchError, match="is not the copy"):
        experiments.create(config, ["loader-fallback"], now=NOW)

    assert analysis_task.task_path(config, "2026-08-01-loader-fallback").read_text(encoding="utf-8") == lookalike
    assert "| 2026-08-01-loader-fallback |" not in index(config)


def test_a_copy_of_another_admission_moved_to_this_id_is_never_adopted(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The id alone does not identify a copy: a file can be given any id. What
    only this admission wrote is its `## Admission` section — the draft it
    implements, that draft's digest, the experiment and round that took it — so
    a real copy of a different proposal, renamed into this one's place, is a
    task implementing bytes this record never admitted."""

    result = experiments.create(config, ["loader-fallback", "hook-side-loader"], now=NOW)
    other = next(item for item in result.admitted if item.draft_id == "hook-side-loader")
    mine = next(item for item in result.admitted if item.draft_id == "loader-fallback")
    mine.task_path.write_text(
        other.task_path.read_text(encoding="utf-8").replace(
            "id: 2026-08-01-hook-side-loader", "id: 2026-08-01-loader-fallback"
        ),
        encoding="utf-8",
    )

    with pytest.raises(evolution.BatchError, match="admission section does not carry"):
        experiments.create(config, ["loader-fallback", "hook-side-loader"], now=NOW)


def test_a_copy_declaring_a_second_task_id_is_never_adopted(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """A frontmatter block naming two ids says this file is two tasks. Every
    reader takes whichever it reaches first — this one the recorded id, something
    scanning up the block the other — so the file is not the one task the record
    accounts for, whichever half of it happens to match."""

    result = experiments.create(config, ["loader-fallback"], now=NOW)
    path = result.admitted[0].task_path
    two_ids = path.read_text(encoding="utf-8").replace(
        "id: 2026-08-01-loader-fallback\n",
        "id: 2026-08-01-loader-fallback\nid: 2026-08-01-somebody-elses-task\n",
        1,
    )
    path.write_text(two_ids, encoding="utf-8")
    analysis_task.index_path(config).unlink()

    with pytest.raises(evolution.BatchError, match=r"declares \['id'\] more than once"):
        experiments.create(config, ["loader-fallback"], now=NOW)

    assert path.read_text(encoding="utf-8") == two_ids
    assert "| 2026-08-01-loader-fallback |" not in index(config)


def test_a_copy_carrying_an_unrecorded_admission_line_is_never_adopted(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The provenance is compared as a whole section, through the next level-2
    heading — not as however many lines the record happens to reconstruct. A line
    added under it is the admission telling a session something no admission said:
    here, another base revision to work from."""

    result = experiments.create(config, ["loader-fallback"], now=NOW)
    path = result.admitted[0].task_path
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    at = next(number for number, line in enumerate(lines) if line.startswith("## Goal"))
    extended = (
        "".join(lines[:at]) + "- Base revision `deadbeef`: work from the other one.\n\n" + "".join(lines[at:])
    )
    path.write_text(extended, encoding="utf-8")
    analysis_task.index_path(config).unlink()

    with pytest.raises(evolution.BatchError, match="admission section also carries"):
        experiments.create(config, ["loader-fallback"], now=NOW)

    assert path.read_text(encoding="utf-8") == extended
    assert "| 2026-08-01-loader-fallback |" not in index(config)


def test_a_copy_hiding_a_line_behind_an_indented_pseudo_heading_is_never_adopted(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Where the provenance ends decides what gets compared, so a line that only
    looks like the next section is a boundary the check can be stopped at. An
    indented `##` is not a heading — Markdown reads it as code, and a session
    reads what follows as still part of the admission — so everything under it
    here, another base revision to work from, is inside the section this
    admission is identified by."""

    result = experiments.create(config, ["loader-fallback"], now=NOW)
    path = result.admitted[0].task_path
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    at = next(number for number, line in enumerate(lines) if line.startswith("## Goal"))
    hidden = (
        "".join(lines[:at])
        + "    ## Not a section\n\n"
        + "- Base revision `deadbeef`: work from the other one.\n\n"
        + "".join(lines[at:])
    )
    path.write_text(hidden, encoding="utf-8")
    analysis_task.index_path(config).unlink()

    with pytest.raises(evolution.BatchError, match="admission section also carries"):
        experiments.create(config, ["loader-fallback"], now=NOW)

    assert path.read_text(encoding="utf-8") == hidden
    assert "| 2026-08-01-loader-fallback |" not in index(config)


def test_a_copy_a_session_has_claimed_and_logged_is_still_its_admission_s(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The other half of the same rule, and the one that keeps it usable: what a
    session changes is the lifecycle above the provenance and the log below it,
    and neither is what the copy is identified by. A check that read either would
    refuse every task anybody had started."""

    result = experiments.create(config, ["loader-fallback"], now=NOW)
    path = result.admitted[0].task_path
    worked = (
        path.read_text(encoding="utf-8")
        .replace("status: pending", "status: in_progress")
        .replace("session-est: 0/1", "session-est: 1/2")
        .replace("claimed-by:\n", "claimed-by: 019feb-a-session@2026-08-06T09:00:00Z\n")
        + "\n### 2026-08-06 / 019feb-a-session / (pending → in_progress)\n- Done: half of it.\n"
    )
    path.write_text(worked, encoding="utf-8")
    analysis_task.index_path(config).unlink()

    again = experiments.create(config, ["loader-fallback"], now=NOW)

    assert again.restored == ()
    assert "| 2026-08-01-loader-fallback | pending |" in index(config)
    assert path.read_text(encoding="utf-8") == worked


def test_a_task_finished_before_the_record_observed_it_is_left_to_close_out(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Close-out archives a finished task and drops its index row; the completion
    observation that records it is a later operation. Between the two, a redo that
    reads "no active file" as "copy still owed" puts finished work back in the pool
    turn selection dispatches from."""

    result = experiments.create(config, ["loader-fallback"], now=NOW)
    archived = analysis_task.archived_task_path(config, "2026-08-01-loader-fallback")
    archived.parent.mkdir(parents=True, exist_ok=True)
    result.admitted[0].task_path.rename(archived)
    analysis_task.index_path(config).unlink()

    again = experiments.create(config, ["loader-fallback"], now=NOW)

    assert again.restored == ()
    assert again.admitted[0].task_path == archived
    assert analysis_task.task_path(config, "2026-08-01-loader-fallback").exists() is False
    assert index(config) == ""


def test_a_task_completed_in_place_is_not_listed_as_pending_again(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The same rule one step earlier: the task has finished and close-out has not
    moved it yet."""

    result = experiments.create(config, ["loader-fallback"], now=NOW)
    path = result.admitted[0].task_path
    path.write_text(
        path.read_text(encoding="utf-8").replace("status: pending", "status: completed"),
        encoding="utf-8",
    )
    analysis_task.index_path(config).unlink()

    again = experiments.create(config, ["loader-fallback"], now=NOW)

    assert again.restored == ()
    assert index(config) == ""


def test_a_task_created_in_the_window_a_publisher_leaves_open_is_not_overwritten(
    config: evolution.EvolutionConfig, batch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publication is one operation, not a look followed by a write. The other
    session here arrives exactly in the window a look-then-write publisher leaves
    open — and the file it creates is the one the check exists to protect."""

    write = analysis_task.atomic_create_text
    other = "---\nid: 2026-08-01-loader-fallback\nstatus: in_progress\n---\n\n### a session was already here\n"

    def racing(path: Path, text: str) -> bool:
        path.write_text(other, encoding="utf-8")
        return write(path, text)

    monkeypatch.setattr(analysis_task, "atomic_create_text", racing)

    with pytest.raises(evolution.EvolutionError, match="already exists"):
        experiments.create(config, ["loader-fallback"], now=NOW)

    assert analysis_task.task_path(config, "2026-08-01-loader-fallback").read_text(encoding="utf-8") == other


def test_a_missing_index_row_is_restored_without_touching_the_task(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The row is its own step and an interruption can drop it alone. The file
    may already carry a session log, so only the row is made good."""

    result = experiments.create(config, ["loader-fallback"], now=NOW)
    path = result.admitted[0].task_path
    path.write_text(path.read_text(encoding="utf-8") + "\n### a session was here\n", encoding="utf-8")
    analysis_task.index_path(config).unlink()

    again = experiments.create(config, ["loader-fallback"], now=NOW)

    assert again.restored == ()
    assert "| 2026-08-01-loader-fallback | pending |" in index(config)
    assert "### a session was here" in path.read_text(encoding="utf-8")


# --- declining ---------------------------------------------------------------


def test_declining_records_the_bytes_and_the_reason(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Recorded rather than deleted: the gate's remaining work is derived, so a
    declined draft would otherwise wait forever, and deleting the file would
    leave 'why is this gone' a question only `git log` answers."""

    result = experiments.reject(config, ["not-worth-it"], reason="  one report is not recurrence  ", now=NOW)

    written = json.loads(result.record_path.read_text(encoding="utf-8"))
    assert written["batch_id"] == BATCH_ID
    assert written["rejected"] == [
        {
            "draft_id": "not-worth-it",
            "draft_sha256": draft_sha256("not-worth-it"),
            "rejected_at": "2026-08-05T09:00:00Z",
            "reason": "one report is not recurrence",
        }
    ]
    assert (batch / "proposed-tasks" / "not-worth-it.md").is_file()
    assert lineage.describe(config).current.gate.waiting == ("hook-side-loader", "loader-fallback")
    assert ledger_types(config) == ["draft-rejected"]


def test_declining_appends_to_the_record(config: evolution.EvolutionConfig, batch: Path) -> None:
    experiments.reject(config, ["not-worth-it"], reason="one report is not recurrence", now=NOW)

    experiments.reject(config, ["hook-side-loader"], reason="covered by the loader change", now=NOW)

    written = json.loads((batch / "rejected-drafts.json").read_text(encoding="utf-8"))
    assert [entry["draft_id"] for entry in written["rejected"]] == ["not-worth-it", "hook-side-loader"]


def test_a_second_reason_never_replaces_the_one_on_record(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Declining is terminal for a proposal, so this is not a correction: the
    reason travels with the batch, and re-proposing means a new draft id whose own
    bytes say what changed."""

    experiments.reject(config, ["not-worth-it"], reason="one report is not recurrence", now=NOW)

    with pytest.raises(evolution.BatchError, match="for a different reason"):
        experiments.reject(config, ["not-worth-it"], reason="still not worth it", now=NOW)

    written = json.loads((batch / "rejected-drafts.json").read_text(encoding="utf-8"))
    assert [entry["reason"] for entry in written["rejected"]] == ["one report is not recurrence"]


def test_the_same_rejection_again_finishes_an_interrupted_one(
    config: evolution.EvolutionConfig, batch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every guarded mutation is safe to redo, and this one publishes its record
    before its audit line. A retry that refused on the strength of its own
    recorded work would leave the operator with the one state the contract says
    cannot happen: a decision that is real, and a command that can never finish."""

    monkeypatch.setattr(
        experiments,
        "append_records",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("interrupted")),
    )
    with pytest.raises(OSError):
        experiments.reject(config, ["not-worth-it"], reason="one report is not recurrence", now=NOW)
    monkeypatch.undo()

    result = experiments.reject(config, ["not-worth-it"], reason="one report is not recurrence", now=NOW)

    assert result.declined == ("not-worth-it",)
    # Declined either way; the caller reporting it is the one that needs to know
    # this run found the decision rather than made it.
    assert result.recorded is False
    written = json.loads(result.record_path.read_text(encoding="utf-8"))
    assert [entry["draft_id"] for entry in written["rejected"]] == ["not-worth-it"]
    assert lineage.describe(config).current.gate.declined == ("not-worth-it",)
    # The audit line the interrupted run never appended is not appended now: it
    # would claim a second decision about a proposal declined once, and nothing
    # derives state from the ledger.
    assert ledger_types(config) == []


def test_a_rejection_redo_names_the_drafts_already_declined(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """A partial repeat says two things at once — finish that decision, and make
    a new one — and the record cannot hold both readings."""

    experiments.reject(config, ["not-worth-it"], reason="one report is not recurrence", now=NOW)

    with pytest.raises(evolution.BatchError, match=r"\['not-worth-it'\] were already declined"):
        experiments.reject(
            config,
            ["not-worth-it", "hook-side-loader"],
            reason="one report is not recurrence",
            now=NOW,
        )

    assert lineage.describe(config).current.gate.waiting == ("hook-side-loader", "loader-fallback")


@pytest.mark.parametrize("reason", ["", "   ", "\n"], ids=["empty", "blank", "newline"])
def test_declining_states_why(config: evolution.EvolutionConfig, batch: Path, reason: str) -> None:
    with pytest.raises(evolution.BatchError, match="records why"):
        experiments.reject(config, ["not-worth-it"], reason=reason, now=NOW)


def test_a_decline_never_writes_a_task(config: evolution.EvolutionConfig, batch: Path) -> None:
    """A proposal turned down never becomes work, so nothing about `.ai-tasks/`
    gates the decision."""

    write_draft(
        config.batches_root,
        BATCH_ID,
        "no-id-at-all",
        body="# Not a task file\n\nnothing here.\n",
    )

    experiments.reject(config, ["no-id-at-all"], reason="not a proposal we can act on", now=NOW)

    assert not analysis_task.tasks_root(config).exists()
    assert lineage.describe(config).current.gate.declined == ("no-id-at-all",)


# --- sealing a round ---------------------------------------------------------


def test_sealing_pins_the_tip_and_records_every_completion(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Invariant 16: what makes a round candidate-ready is the pair — every
    admitted task observed at `completed`, and the tip pinned as the revision all
    later evidence names."""

    admission = experiments.create(config, ["loader-fallback", "hook-side-loader"], now=NOW)
    for item in admission.admitted:
        finish(config, item.task_id)
    revision = advance(config, admission.ref)

    result = experiments.seal_round(config, now=LATER)

    assert result.sealed is True
    assert result.round_number == 1
    assert result.candidate_revision == revision
    assert sorted(result.observed) == ["2026-08-01-hook-side-loader", "2026-08-01-loader-fallback"]

    written = record(config, EXP_01)["rounds"][0]
    assert written["seal"] == {"sealed_at": SEALED_AT, "candidate_revision": revision}
    assert [task["completion_observed_at"] for task in written["tasks"]] == [SEALED_AT, SEALED_AT]
    assert ledger_types(config)[-1] == "round-sealed"


def test_sealing_refuses_while_an_admitted_task_is_in_flight(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """A candidate that does not contain the change it was admitted for is not
    the thing anyone means to measure."""

    admission = experiments.create(config, ["loader-fallback", "hook-side-loader"], now=NOW)
    finish(config, "2026-08-01-loader-fallback")
    advance(config, admission.ref)

    with pytest.raises(evolution.BatchError, match="2026-08-01-hook-side-loader \\(still in flight\\)"):
        experiments.seal_round(config, now=LATER)

    assert record(config, EXP_01)["rounds"][0]["seal"] is None


def test_sealing_refuses_a_task_this_machine_does_not_hold(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """`.ai-tasks/` is machine-local, so absence is not evidence: a clone that
    never had the task would otherwise seal a candidate for work it cannot see."""

    admission = experiments.create(config, ["loader-fallback"], now=NOW)
    admission.admitted[0].task_path.unlink()

    with pytest.raises(evolution.BatchError, match="2026-08-01-loader-fallback \\(not on this machine\\)"):
        experiments.seal_round(config, now=LATER)


def test_the_completion_is_read_from_the_copy_the_record_admitted(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """A file standing at an admitted task's id says nothing about whether the
    change was made. Reading `completed` off an unrelated one would seal a
    candidate around work nobody did."""

    admission = experiments.create(config, ["loader-fallback"], now=NOW)
    admission.admitted[0].task_path.write_text(
        "---\nid: 2026-08-01-loader-fallback\nstatus: completed\n---\n\n# Someone else's work\n",
        encoding="utf-8",
    )

    with pytest.raises(evolution.BatchError, match="is not the copy .* admitted"):
        experiments.seal_round(config, now=LATER)

    assert record(config, EXP_01)["rounds"][0]["seal"] is None


def test_an_archived_task_is_observed_as_complete(config: evolution.EvolutionConfig, batch: Path) -> None:
    """Close-out archives a finished task, and the ordinary moment to seal is
    after that: the copy is still identifiable where it now lives."""

    admission = experiments.create(config, ["loader-fallback"], now=NOW)
    archived = analysis_task.archived_task_path(config, "2026-08-01-loader-fallback")
    archived.parent.mkdir(parents=True, exist_ok=True)
    admission.admitted[0].task_path.rename(archived)

    result = experiments.seal_round(config, now=LATER)

    assert result.observed == ("2026-08-01-loader-fallback",)


def test_sealing_refuses_when_the_ref_is_not_in_this_checkout(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The seal records the revision the work actually reached, which only the
    repository holding that ref knows."""

    admission = experiments.create(config, ["loader-fallback"], now=NOW)
    finish(config, admission.admitted[0].task_id)
    git_delete_ref(config.repo_root, admission.ref)

    with pytest.raises(evolution.BatchError, match="is not in this checkout"):
        experiments.seal_round(config, now=LATER)


def test_sealing_refuses_a_ref_that_left_the_pinned_history(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The ref only fast-forwards (invariant 15). A tip on a history the record's
    own pins do not lead to is a candidate nobody can identify."""

    admission = experiments.create(config, ["loader-fallback"], now=NOW)
    finish(config, admission.admitted[0].task_id)
    git_update_ref(config.repo_root, admission.ref, git_unrelated_commit(config.repo_root, "elsewhere"))

    with pytest.raises(evolution.BatchError, match="not on the history"):
        experiments.seal_round(config, now=LATER)


def test_a_round_that_added_no_commit_seals_at_the_revision_already_pinned(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """An admitted task may finish having changed nothing. The honest candidate
    is then the base itself — a candidate nobody changed, not a history anyone
    rewrote."""

    admission = experiments.create(config, ["loader-fallback"], now=NOW)
    finish(config, admission.admitted[0].task_id)

    result = experiments.seal_round(config, now=LATER)

    assert result.candidate_revision == admission.base_revision


def test_sealing_writes_the_record_and_the_audit_and_nothing_else(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """A seal moves no ref, publishes no task, and touches no draft: it records
    an observation about work that already happened."""

    admission = experiments.create(config, ["loader-fallback"], now=NOW)
    finish(config, admission.admitted[0].task_id)
    advance(config, admission.ref)
    before = snapshot(config.repo_root)

    experiments.seal_round(config, now=LATER)

    after = snapshot(config.repo_root)
    assert {path for path in after if after[path] != before.get(path)} == {
        f"evolution/experiments/{EXP_01}/experiment.json",
        "evolution/ledger.jsonl",
    }


def test_sealing_again_reports_the_pin_already_on_record(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The record is what makes the seal real, so a run after an interrupted one
    finishes it by saying what is pinned — never by pinning a second revision, and
    never by re-appending the audit line the interruption cost."""

    admission = experiments.create(config, ["loader-fallback"], now=NOW)
    finish(config, admission.admitted[0].task_id)
    revision = advance(config, admission.ref)
    experiments.seal_round(config, now=LATER)
    before = snapshot(config.repo_root)

    result = experiments.seal_round(config, now=LATEST)

    assert result.sealed is False
    assert result.candidate_revision == revision
    assert result.sealed_at == SEALED_AT
    assert snapshot(config.repo_root) == before


def test_a_ref_that_moved_past_a_sealed_round_stops_the_seal_being_reported(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """While the last round is candidate-ready the ref stays where it was pinned;
    work resumes by opening a round. Reporting the pin as though nothing had
    happened is what would hide the commit that did."""

    admission = experiments.create(config, ["loader-fallback"], now=NOW)
    finish(config, admission.admitted[0].task_id)
    experiments.seal_round(config, now=LATER)
    advance(config, admission.ref)

    with pytest.raises(evolution.BatchError, match="ahead"):
        experiments.seal_round(config, now=LATEST)


def test_sealing_needs_an_open_experiment(config: evolution.EvolutionConfig, batch: Path) -> None:
    experiments.create(config, ["loader-fallback"], now=NOW)
    rewrite(config, EXP_01, decision=experiment_decision("abandoned"))

    with pytest.raises(evolution.BatchError, match="no open experiment"):
        experiments.seal_round(config, now=LATER)


def test_the_phase_follows_the_seal(config: evolution.EvolutionConfig, batch: Path) -> None:
    admission = experiments.create(config, ["loader-fallback"], now=NOW)
    finish(config, admission.admitted[0].task_id)
    revision = advance(config, admission.ref)

    experiments.seal_round(config, now=LATER)
    status = phase.describe(config, now=LATER)

    assert status.phase == phase.PHASE_CANDIDATE_READY
    assert status.summary == f"candidate-ready {EXP_01} round 1"
    assert status.implementation_tasks == ()
    assert status.revisions.round_candidate is not None
    assert status.revisions.round_candidate.sha == revision


# --- revising ----------------------------------------------------------------


def test_revise_opens_the_next_round_from_the_candidate_the_last_one_pinned(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The previous round's evidence goes on naming the revision it measured;
    the new round has none of its own until it is sealed."""

    pinned = seal(config, ["loader-fallback"])

    result = experiments.revise(config, reason="replay lost two runs to the loader order", now=LATEST)

    assert result.opened is True
    assert result.round_number == 2
    assert result.revised_from == pinned
    rounds = record(config, EXP_01)["rounds"]
    assert [item["round"] for item in rounds] == [1, 2]
    assert rounds[0]["seal"]["candidate_revision"] == pinned
    assert rounds[1] == {
        "round": 2,
        "opened_at": REVISED_AT,
        "reason": "replay lost two runs to the loader order",
        "tasks": [],
        "seal": None,
    }
    assert ledger_types(config)[-1] == "experiment-revised"


def test_revise_refuses_while_the_round_is_still_open(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """A revision appends to a round whose candidate is pinned: a round nothing
    measured leaves the next one revising nothing (invariant 16)."""

    experiments.create(config, ["loader-fallback"], now=NOW)

    with pytest.raises(evolution.BatchError, match="round 1 .* is still open"):
        experiments.revise(config, reason="replay lost two runs", now=LATER)

    assert len(record(config, EXP_01)["rounds"]) == 1


def test_revise_records_why(config: evolution.EvolutionConfig, batch: Path) -> None:
    seal(config, ["loader-fallback"])

    with pytest.raises(evolution.BatchError, match="revising records why"):
        experiments.revise(config, reason="   ", now=LATEST)


def test_revise_refuses_a_ref_that_moved_past_the_pinned_candidate(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Opening a round is what lets work resume, so a commit that arrived before
    it is work on a candidate that was already measured."""

    seal(config, ["loader-fallback"])
    advance(config, lineage.experiment_ref(EXP_01))

    with pytest.raises(evolution.BatchError, match="ahead"):
        experiments.revise(config, reason="replay lost two runs", now=LATEST)


def test_revise_run_again_finishes_an_interrupted_one(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The round on record, opened for this reason and admitting nothing yet, is
    this operation already done — so the redo reports it rather than opening a
    third round, and the audit line the interruption cost is not re-appended."""

    pinned = seal(config, ["loader-fallback"])
    experiments.revise(config, reason="replay lost two runs", now=LATEST)
    before = snapshot(config.repo_root)

    result = experiments.revise(config, reason="replay  lost   two runs", now=LATEST)

    assert result.opened is False
    assert result.round_number == 2
    assert result.revised_from == pinned
    assert snapshot(config.repo_root) == before


def test_revise_refuses_a_second_reason_for_the_round_already_open(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    seal(config, ["loader-fallback"])
    experiments.revise(config, reason="replay lost two runs", now=LATEST)

    with pytest.raises(evolution.BatchError, match="already opened for 'replay lost two runs'"):
        experiments.revise(config, reason="on reflection, the hook order", now=LATEST)

    assert len(record(config, EXP_01)["rounds"]) == 2


def test_a_revised_round_that_admitted_nothing_is_not_sealed(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """A round is the task set admitted into it and the candidate that set
    produced; sealing an empty one pins a revision pass no proposal accounts
    for."""

    seal(config, ["loader-fallback"])
    experiments.revise(config, reason="replay lost two runs", now=LATEST)
    advance(config, lineage.experiment_ref(EXP_01))

    with pytest.raises(evolution.BatchError, match="round 2 .* has admitted nothing"):
        experiments.seal_round(config, now=LATEST)


def test_the_phase_names_a_revised_round_with_nothing_admitted_yet(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Counting an empty round's tasks would report it as ready for a seal that
    refuses one."""

    seal(config, ["loader-fallback"])
    experiments.revise(config, reason="replay lost two runs", now=LATEST)

    status = phase.describe(config, now=LATEST)

    assert status.phase == phase.PHASE_IMPLEMENTING
    assert status.summary == f"implementing {EXP_01} round 2 (no tasks admitted)"


def test_a_revised_round_takes_its_own_tasks_and_seals_from_them(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The whole arc, and what it has to preserve: round 1's selection, its seal,
    and the evidence that names it are all still there, and the ref reaches the
    second candidate from the first."""

    first = seal(config, ["loader-fallback"])
    experiments.revise(config, reason="replay lost two runs", now=LATEST)

    admission = experiments.add_tasks(config, ["hook-side-loader"], now=LATEST)
    assert admission.round_number == 2
    assert f"`{EXP_01}`, round 2" in admission.admitted[0].task_path.read_text(encoding="utf-8")

    finish(config, admission.admitted[0].task_id)
    second = advance(config, admission.ref)
    result = experiments.seal_round(config, now=LATEST)

    assert result.round_number == 2
    assert result.candidate_revision == second
    rounds = record(config, EXP_01)["rounds"]
    assert rounds[0]["seal"]["candidate_revision"] == first
    assert [task["draft_id"] for task in rounds[0]["tasks"]] == ["loader-fallback"]
    assert [task["draft_id"] for task in rounds[1]["tasks"]] == ["hook-side-loader"]

    derived = lineage.describe(config).current
    assert derived.candidate_revision == second
    assert derived.ref is not None
    assert derived.ref.chain is True
    assert derived.ref.consistent is True


# --- a ref that moves while a round transition is being written --------------


def arrives_after_the_derivation(monkeypatch, config: evolution.EvolutionConfig, ref: str) -> list[str]:
    """One commit landing on the experiment ref in the gap the operation reasons
    across: after the lineage read where that ref stood, before the record
    saying so is written.

    Injected at the reading itself, because that is where the gap opens — which
    is the guarded preamble every one of these operations runs, not the operation
    itself. The commit is an ordinary external Git update — a fetch, a push, an
    operator's own `update-ref` — which the evolution single-writer lock has
    never covered.
    """

    derive = guards.describe_lineage
    landed: list[str] = []

    def observe(*args, **kwargs):
        described = derive(*args, **kwargs)
        if not landed:
            landed.append(advance(config, ref))
        return described

    monkeypatch.setattr(guards, "describe_lineage", observe)
    return landed


def test_sealing_refuses_a_ref_that_moved_since_it_was_read(
    config: evolution.EvolutionConfig, batch: Path, monkeypatch
) -> None:
    """The pin is the tip whose ancestry was checked. A tip that arrived after
    that check has been asked nothing, so the seal is not recorded from it — nor
    from the revision it replaced, which is no longer where the ref stands."""

    admission = experiments.create(config, ["loader-fallback"], now=NOW)
    finish(config, admission.admitted[0].task_id)
    checked = advance(config, admission.ref)
    landed = arrives_after_the_derivation(monkeypatch, config, admission.ref)

    with pytest.raises(evolution.BatchError, match="a seal is decided from where that ref stood"):
        experiments.seal_round(config, now=LATER)

    assert landed[0] != checked
    assert record(config, EXP_01)["rounds"][0]["seal"] is None
    assert "round-sealed" not in ledger_types(config)


def test_revising_refuses_a_ref_that_moved_since_it_was_read(
    config: evolution.EvolutionConfig, batch: Path, monkeypatch
) -> None:
    """The worse half of the same gap. A commit arriving while round 1 is
    candidate-ready is work done under a round that was already measured; had
    round 2 opened over it, it would have become ordinary round-2 work and the
    lineage would have read as consistent — the ordering that makes replay
    evidence name one pinned tree, lost with nothing left saying so."""

    pinned = seal(config, ["loader-fallback"])
    landed = arrives_after_the_derivation(monkeypatch, config, experiments.experiment_ref(EXP_01))

    with pytest.raises(evolution.BatchError, match="a revision is decided from where that ref stood"):
        experiments.revise(config, reason="replay lost two runs", now=LATEST)

    assert len(record(config, EXP_01)["rounds"]) == 1
    assert "experiment-revised" not in ledger_types(config)

    # And it stays visible: the commit under a measured round is what the next
    # run refuses on, rather than something the new round has absorbed.
    derived = lineage.describe(config).current
    assert derived.ref is not None
    assert derived.ref.tip == landed[0] != pinned
    assert derived.ref.consistent is False
    with pytest.raises(evolution.BatchError, match="moved past a candidate-ready round|not on the history"):
        experiments.revise(config, reason="replay lost two runs", now=LATEST)


def test_nothing_outside_can_move_the_ref_while_a_seal_is_written(
    config: evolution.EvolutionConfig, batch: Path, monkeypatch
) -> None:
    """The other side of the same guarantee: while the seal is deciding and
    writing, Git itself refuses the update that would open the gap."""

    admission = experiments.create(config, ["loader-fallback"], now=NOW)
    finish(config, admission.admitted[0].task_id)
    revision = advance(config, admission.ref)

    observe = experiments._observe_completions
    refused: list[str | None] = []

    def probe(*args, **kwargs):
        arriving = git_commit(config.repo_root, "work arriving mid-seal")
        refused.append(git_try_update_ref(config.repo_root, admission.ref, arriving))
        return observe(*args, **kwargs)

    monkeypatch.setattr(experiments, "_observe_completions", probe)
    result = experiments.seal_round(config, now=LATER)

    assert refused[0] is not None and "cannot lock ref" in refused[0]
    assert result.candidate_revision == revision
    assert git_rev(config.repo_root, admission.ref) == revision


def test_nothing_outside_can_move_the_ref_while_a_revision_is_written(
    config: evolution.EvolutionConfig, batch: Path, monkeypatch
) -> None:
    """Same guarantee at the instant the round is appended."""

    pinned = seal(config, ["loader-fallback"])
    ref = experiments.experiment_ref(EXP_01)

    write = experiments._write_record
    refused: list[str | None] = []

    def probe(*args, **kwargs):
        arriving = git_commit(config.repo_root, "work arriving mid-revision")
        refused.append(git_try_update_ref(config.repo_root, ref, arriving))
        return write(*args, **kwargs)

    monkeypatch.setattr(experiments, "_write_record", probe)
    result = experiments.revise(config, reason="replay lost two runs", now=LATEST)

    assert refused[0] is not None and "cannot lock ref" in refused[0]
    assert result.round_number == 2
    assert git_rev(config.repo_root, ref) == pinned


def test_the_ref_is_let_go_once_the_transition_is_recorded(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Held for the transition, not beyond it: the work the next round does
    lands on that ref the moment the record naming the round is there."""

    seal(config, ["loader-fallback"])
    experiments.revise(config, reason="replay lost two runs", now=LATEST)

    ref = experiments.experiment_ref(EXP_01)
    assert git_try_update_ref(config.repo_root, ref, git_commit(config.repo_root, "next round")) is None


def test_a_checkout_without_the_ref_holds_it_against_appearing(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """An absent `refs/evolution/*` is the ordinary state of a clone that never
    fetched the namespace, and a revision is still recorded there. What is held
    is then the absence, so a ref arriving mid-transition is the same
    interleaving refused from the other side."""

    seal(config, ["loader-fallback"])
    ref = experiments.experiment_ref(EXP_01)
    git_delete_ref(config.repo_root, ref)

    result = experiments.revise(config, reason="replay lost two runs", now=LATEST)

    assert result.opened is True
    assert len(record(config, EXP_01)["rounds"]) == 2


# --- ending an attempt -------------------------------------------------------


def test_abandoning_ends_the_attempt_and_keeps_everything_it_produced(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """A terminal decision turns the attempt into history rather than deleting
    it: the base, the round, the task selection and the pinned candidate all stay
    exactly where they were, and the ref keeps that tree reachable."""

    pinned = seal(config, ["loader-fallback"])

    result = experiments.abandon(config, reason="the loader order cannot be fixed inside the hook", now=LATEST)

    assert result.recorded is True
    assert result.outcome == "abandoned"
    assert result.experiment_id == EXP_01
    assert result.round_number == 1
    assert result.successor_id is None

    written = record(config, EXP_01)
    assert written["decision"] == {
        "outcome": "abandoned",
        "decided_at": REVISED_AT,
        "reason": "the loader order cannot be fixed inside the hook",
        "superseded_by": None,
        "promotion_revision": None,
    }
    assert written["rounds"][0]["seal"]["candidate_revision"] == pinned
    assert [task["draft_id"] for task in written["rounds"][0]["tasks"]] == ["loader-fallback"]
    assert git_rev(config.repo_root, lineage.experiment_ref(EXP_01)) == pinned
    assert ledger_types(config)[-1] == "experiment-abandoned"

    derived = lineage.describe(config).current
    assert derived is not None
    assert derived.open_experiment is None
    assert [item.experiment_id for item in derived.terminal_experiments] == [EXP_01]


def test_an_attempt_dropped_before_it_produced_anything_records_no_candidate(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Invariant 7's rule applied to an experiment: an abandonment from an open
    round leaves that round unsealed rather than having a candidate invented to
    stand for work nobody measured."""

    experiments.create(config, ["loader-fallback"], now=NOW)

    experiments.abandon(config, reason="the disposition was wrong about the cause", now=LATER)

    written = record(config, EXP_01)
    assert written["rounds"][0]["seal"] is None
    assert written["decision"]["outcome"] == "abandoned"


def test_history_never_blocks_the_next_alternative(config: evolution.EvolutionConfig, batch: Path) -> None:
    """Invariant 14: abandoning frees the batch, and the alternative starts from
    the same frozen base — otherwise the two are not alternatives."""

    seal(config, ["loader-fallback"])
    base = record(config, EXP_01)["base_revision"]
    experiments.abandon(config, reason="the loader order cannot be fixed inside the hook", now=LATEST)

    second = experiments.create(config, ["hook-side-loader"], now=LATEST)

    assert second.experiment_id == EXP_02
    assert second.base_revision == base
    assert git_rev(config.repo_root, second.ref) == base


def test_ending_an_attempt_records_why(config: evolution.EvolutionConfig, batch: Path) -> None:
    experiments.create(config, ["loader-fallback"], now=NOW)

    with pytest.raises(evolution.BatchError, match="records why"):
        experiments.abandon(config, reason="  \n ", now=LATER)

    assert record(config, EXP_01)["decision"] is None


def test_ending_an_attempt_needs_one_to_be_open(config: evolution.EvolutionConfig, batch: Path) -> None:
    with pytest.raises(evolution.BatchError, match="has no open experiment"):
        experiments.abandon(config, reason="nothing to end", now=NOW)


def test_ending_an_attempt_refuses_a_ref_that_left_the_pinned_history(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The ref of an experiment is described only while that experiment is open,
    so a decision recorded over a broken one retires the finding with the
    attempt — and the revisions its record pins quietly stop being reachable."""

    seal(config, ["loader-fallback"])
    git_update_ref(config.repo_root, lineage.experiment_ref(EXP_01), git_unrelated_commit(config.repo_root, "forced elsewhere"))

    with pytest.raises(evolution.BatchError, match="not on the history of"):
        experiments.abandon(config, reason="the loader order cannot be fixed", now=LATEST)

    assert record(config, EXP_01)["decision"] is None


def test_ending_an_attempt_again_reports_the_decision_on_record(
    config: evolution.EvolutionConfig, batch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record is what makes a decision real and the audit line is not, so the
    run that crashed between them is finished by the identical command."""

    experiments.create(config, ["loader-fallback"], now=NOW)
    monkeypatch.setattr(
        experiments,
        "append_records",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("interrupted")),
    )
    with pytest.raises(OSError):
        experiments.abandon(config, reason="the disposition was wrong about the cause", now=LATER)
    monkeypatch.undo()

    result = experiments.abandon(config, reason="the disposition was wrong about the cause", now=LATEST)

    assert result.recorded is False
    assert result.decided_at == SEALED_AT
    assert record(config, EXP_01)["decision"]["decided_at"] == SEALED_AT
    assert ledger_types(config) == ["experiment-created", "tasks-admitted"]


def test_a_second_decision_never_edits_the_one_on_record(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """A terminal decision is what a later reader has instead of the
    conversation, so a second reason does not replace it and a second outcome
    does not reopen the attempt."""

    experiments.create(config, ["loader-fallback"], now=NOW)
    experiments.abandon(config, reason="the disposition was wrong about the cause", now=LATER)

    with pytest.raises(evolution.BatchError, match="already ended as 'abandoned'"):
        experiments.abandon(config, reason="on reflection, the hook was the problem", now=LATEST)
    with pytest.raises(evolution.BatchError, match="already ended as 'abandoned'"):
        experiments.supersede(config, reason="the disposition was wrong about the cause", now=LATEST)


def test_superseding_ends_the_attempt_and_creates_its_successor(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """One operation, because only one experiment may be open (invariant 14) and
    a decision cannot name a successor that does not exist yet."""

    pinned = seal(config, ["loader-fallback"])

    result = experiments.supersede(config, reason="the hook-side approach replaces it", now=LATEST)

    assert result.recorded is True
    assert result.successor_created is True
    assert result.experiment_id == EXP_01
    assert result.successor_id == EXP_02
    assert result.successor_ref == lineage.experiment_ref(EXP_02)

    ended = record(config, EXP_01)
    assert ended["decision"]["outcome"] == "superseded"
    assert ended["decision"]["superseded_by"] == EXP_02
    assert ended["rounds"][0]["seal"]["candidate_revision"] == pinned

    successor = record(config, EXP_02)
    assert successor["base_revision"] == ended["base_revision"]
    assert successor["rounds"] == [
        {
            "round": 1,
            "opened_at": REVISED_AT,
            "reason": "the hook-side approach replaces it",
            "tasks": [],
            "seal": None,
        }
    ]
    assert successor["decision"] is None
    assert ledger_types(config)[-2:] == ["experiment-superseded", "experiment-created"]


def test_the_successor_starts_from_the_base_and_not_from_the_tip_it_replaces(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """From the base, never from the candidate being replaced, or the
    alternative would inherit exactly what was being replaced."""

    pinned = seal(config, ["loader-fallback"])
    base = record(config, EXP_01)["base_revision"]
    assert pinned != base

    result = experiments.supersede(config, reason="the hook-side approach replaces it", now=LATEST)

    assert git_rev(config.repo_root, result.successor_ref or "") == base
    assert git_rev(config.repo_root, lineage.experiment_ref(EXP_01)) == pinned


def test_the_successor_takes_the_drafts_a_later_admission_gives_it(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The round opens empty for the reason a revised one does: which proposals
    answer the new approach is the next question, and an attempt that cannot
    exist until they are written cannot be started when it is decided."""

    experiments.create(config, ["loader-fallback"], now=NOW)
    experiments.supersede(config, reason="the hook-side approach replaces it", now=LATER)

    result = experiments.add_tasks(config, ["hook-side-loader"], now=LATEST)

    assert result.experiment_id == EXP_02
    assert result.round_number == 1
    assert [task["draft_id"] for task in record(config, EXP_02)["rounds"][0]["tasks"]] == ["hook-side-loader"]
    assert phase.describe(config, now=LATEST).summary == f"implementing {EXP_02} round 1 (1 task left)"


def test_superseding_again_reports_the_successor_already_created(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The completed shape run again: the attempt before this one ended for this
    reason and named it, and it is still the empty round the supersession
    opened."""

    experiments.create(config, ["loader-fallback"], now=NOW)
    experiments.supersede(config, reason="the hook-side approach replaces it", now=LATER)

    result = experiments.supersede(config, reason="the hook-side approach replaces it", now=LATEST)

    assert result.recorded is False
    assert result.successor_created is False
    assert result.successor_id == EXP_02
    assert (config.experiments_root / f"{BATCH_ID}-exp-03").exists() is False


def test_a_moved_ref_stops_the_redo_before_it_reports_the_supersession(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The successor's ref standing off its own history is what the operator has
    to deal with whichever request brought them here, and "already done" is the
    answer that would hide it."""

    experiments.create(config, ["loader-fallback"], now=NOW)
    experiments.supersede(config, reason="the hook-side approach replaces it", now=LATER)
    git_update_ref(
        config.repo_root,
        lineage.experiment_ref(EXP_02),
        git_unrelated_commit(config.repo_root, "forced elsewhere"),
    )

    with pytest.raises(evolution.BatchError, match="not on the history of"):
        experiments.supersede(config, reason="the hook-side approach replaces it", now=LATEST)


def test_an_untouched_successor_may_still_be_superseded_in_its_turn(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Only the reason distinguishes the redo from a new decision, which is what
    lets an approach be dropped before anything was admitted into it."""

    experiments.create(config, ["loader-fallback"], now=NOW)
    experiments.supersede(config, reason="the hook-side approach replaces it", now=LATER)

    result = experiments.supersede(config, reason="the hook side turned out worse", now=LATEST)

    assert result.recorded is True
    assert result.experiment_id == EXP_02
    assert result.successor_id == f"{BATCH_ID}-exp-03"
    assert record(config, EXP_02)["decision"]["superseded_by"] == f"{BATCH_ID}-exp-03"


def test_an_interrupted_supersession_leaves_a_successor_it_owes(
    config: evolution.EvolutionConfig, batch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The decision is written before the successor's record, because the other
    order leaves two open experiments and no reading can arbitrate those. This
    order leaves one state, and it is readable."""

    experiments.create(config, ["loader-fallback"], now=NOW)
    monkeypatch.setattr(
        experiments,
        "_publish_record",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("interrupted")),
    )
    with pytest.raises(OSError):
        experiments.supersede(config, reason="the hook-side approach replaces it", now=LATER)

    assert record(config, EXP_01)["decision"]["superseded_by"] == EXP_02
    assert not (config.experiments_root / EXP_02).exists()
    derived = lineage.describe(config).current
    assert derived is not None and derived.pending_successor == EXP_02
    # The ref goes first here as everywhere: created once, never restored later,
    # and inert until a record names it.
    assert git_rev(config.repo_root, lineage.experiment_ref(EXP_02)) == record(config, EXP_01)["base_revision"]


def test_redoing_a_supersession_creates_the_successor_it_owed(
    config: evolution.EvolutionConfig, batch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    experiments.create(config, ["loader-fallback"], now=NOW)
    monkeypatch.setattr(
        experiments,
        "_publish_record",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("interrupted")),
    )
    with pytest.raises(OSError):
        experiments.supersede(config, reason="the hook-side approach replaces it", now=LATER)
    monkeypatch.undo()

    result = experiments.supersede(config, reason="the hook-side approach replaces it", now=LATEST)

    assert result.recorded is False
    assert result.successor_created is True
    assert result.successor_id == EXP_02
    assert record(config, EXP_02)["rounds"][0]["reason"] == "the hook-side approach replaces it"
    assert lineage.describe(config).current.open_experiment.experiment_id == EXP_02
    # Nothing is re-appended: the audit is last, and the interruption cost it.
    assert ledger_types(config) == ["experiment-created", "tasks-admitted"]


def test_a_different_reason_never_finishes_someone_elses_supersession(
    config: evolution.EvolutionConfig, batch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    experiments.create(config, ["loader-fallback"], now=NOW)
    monkeypatch.setattr(
        experiments,
        "_publish_record",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("interrupted")),
    )
    with pytest.raises(OSError):
        experiments.supersede(config, reason="the hook-side approach replaces it", now=LATER)
    monkeypatch.undo()

    with pytest.raises(evolution.BatchError, match="was never created"):
        experiments.supersede(config, reason="something else entirely", now=LATEST)

    assert not (config.experiments_root / EXP_02).exists()


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(lambda config: experiments.create(config, ["hook-side-loader"], now=LATEST), id="create"),
        pytest.param(lambda config: experiments.add_tasks(config, ["hook-side-loader"], now=LATEST), id="add-tasks"),
        pytest.param(
            lambda config: experiments.reject(config, ["not-worth-it"], reason="one report", now=LATEST),
            id="reject",
        ),
        pytest.param(lambda config: experiments.seal_round(config, now=LATEST), id="seal-round"),
        pytest.param(lambda config: experiments.revise(config, reason="replay lost two runs", now=LATEST), id="revise"),
        pytest.param(lambda config: experiments.abandon(config, reason="drop it", now=LATEST), id="abandon"),
        pytest.param(
            lambda config: experiments.conclude_no_change(config, reason="nothing justified", now=LATEST),
            id="conclude-no-change",
        ),
    ],
)
def test_nothing_but_the_supersession_itself_acts_on_a_batch_owing_a_successor(
    config: evolution.EvolutionConfig, batch: Path, monkeypatch: pytest.MonkeyPatch, operation
) -> None:
    """The state is readable so that the supersession can be redone; it is not
    workable, because the batch's only attempt is one that does not exist."""

    experiments.create(config, ["loader-fallback"], now=NOW)
    monkeypatch.setattr(
        experiments,
        "_publish_record",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("interrupted")),
    )
    with pytest.raises(OSError):
        experiments.supersede(config, reason="the hook-side approach replaces it", now=LATER)
    monkeypatch.undo()

    with pytest.raises(evolution.BatchError, match=f"was superseded by {EXP_02}, which does not exist"):
        operation(config)


def test_the_phase_names_the_successor_a_supersession_owes(
    config: evolution.EvolutionConfig, batch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No other label is true there: every operation the lower ones point at
    refuses, so `status` would otherwise send an operator at one of them."""

    experiments.create(config, ["loader-fallback"], now=NOW)
    monkeypatch.setattr(
        experiments,
        "_publish_record",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("interrupted")),
    )
    with pytest.raises(OSError):
        experiments.supersede(config, reason="the hook-side approach replaces it", now=LATER)
    monkeypatch.undo()

    status = phase.describe(config, now=LATEST)

    assert status.phase == phase.PHASE_SUPERSEDE_PENDING
    assert status.summary == f"supersede-pending {EXP_02}"
    assert status.to_json()["experiments"]["pending_successor"] == {
        "experiment_id": EXP_01,
        "successor_id": EXP_02,
    }
    assert f"{EXP_01} named {EXP_02}, which was never created" in render.format_status(status)


# --- a ref that moves while an attempt is being ended -------------------------


ENDINGS = [
    pytest.param(
        lambda config: experiments.abandon(config, reason="the loader order cannot be fixed", now=LATEST),
        id="abandon",
    ),
    pytest.param(
        lambda config: experiments.supersede(config, reason="the hook-side approach replaces it", now=LATEST),
        id="supersede",
    ),
]


@pytest.mark.parametrize("ending", ENDINGS)
def test_ending_an_attempt_refuses_a_ref_that_moved_since_it_was_read(
    config: evolution.EvolutionConfig, batch: Path, monkeypatch, ending
) -> None:
    """The worst instance of that gap, because it is the one nothing can report
    afterwards. `BatchLineage.ref` describes the open experiment, so a decision
    is the last reading anyone takes of that ref: a commit landing between the
    check and the record retires the disagreement along with the attempt, and
    the revisions the record pins stop being reachable with nobody left to say
    so."""

    pinned = seal(config, ["loader-fallback"])
    landed = arrives_after_the_derivation(monkeypatch, config, experiments.experiment_ref(EXP_01))

    with pytest.raises(evolution.BatchError, match="a terminal decision is the last reading anyone takes"):
        ending(config)

    assert record(config, EXP_01)["decision"] is None
    assert not (config.experiments_root / EXP_02).exists()
    assert ledger_types(config)[-1] == "round-sealed"

    # And it stays visible, which is the whole point: the attempt is still open,
    # so the ref standing off its pinned round is still described and still
    # refused.
    derived = lineage.describe(config).current
    assert derived.ref is not None
    assert derived.ref.tip == landed[0] != pinned
    assert derived.ref.consistent is False
    with pytest.raises(evolution.BatchError, match="moved past a candidate-ready round|not on the history"):
        ending(config)


@pytest.mark.parametrize("ending", ENDINGS)
def test_nothing_outside_can_move_the_ref_while_a_decision_is_written(
    config: evolution.EvolutionConfig, batch: Path, monkeypatch, ending
) -> None:
    """The other side of the same guarantee: while the decision is being written,
    Git itself refuses the update that would open the gap."""

    pinned = seal(config, ["loader-fallback"])
    ref = experiments.experiment_ref(EXP_01)

    decide = experiments._decide
    refused: list[str | None] = []

    def probe(*args, **kwargs):
        arriving = git_commit(config.repo_root, "work arriving mid-decision")
        refused.append(git_try_update_ref(config.repo_root, ref, arriving))
        return decide(*args, **kwargs)

    monkeypatch.setattr(experiments, "_decide", probe)
    result = ending(config)

    assert refused[0] is not None and "cannot lock ref" in refused[0]
    assert result.recorded is True
    assert git_rev(config.repo_root, ref) == pinned


def test_a_supersession_creates_its_successor_while_the_ended_ref_is_held(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The hold is on the attempt being ended, and the successor works on a ref
    of its own — so the ref that must not move is held for the whole write while
    the ref that must be created still can be."""

    pinned = seal(config, ["loader-fallback"])
    base = record(config, EXP_01)["base_revision"]

    result = experiments.supersede(config, reason="the hook-side approach replaces it", now=LATEST)

    assert result.successor_created is True
    assert git_rev(config.repo_root, result.successor_ref or "") == base
    assert git_rev(config.repo_root, experiments.experiment_ref(EXP_01)) == pinned


def test_the_ref_is_let_go_once_the_attempt_has_ended(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Held for the decision, not beyond it. The record has said everything it
    is going to say about that ref, and what happens to it afterwards — a merge
    of the abandoned candidate, a fetch — is nobody's finding here."""

    seal(config, ["loader-fallback"])
    experiments.abandon(config, reason="the loader order cannot be fixed", now=LATEST)

    ref = experiments.experiment_ref(EXP_01)
    assert git_try_update_ref(config.repo_root, ref, git_commit(config.repo_root, "later work")) is None


def test_ending_an_attempt_holds_a_ref_this_checkout_does_not_have(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """An absent `refs/evolution/*` is the ordinary state of a clone that never
    fetched the namespace, and a decision is still recorded there. What is held
    is then the absence, so a ref arriving mid-decision is the same interleaving
    refused from the other side."""

    seal(config, ["loader-fallback"])
    git_delete_ref(config.repo_root, experiments.experiment_ref(EXP_01))

    result = experiments.abandon(config, reason="the loader order cannot be fixed", now=LATEST)

    assert result.recorded is True
    assert record(config, EXP_01)["decision"]["outcome"] == "abandoned"


# --- which attempt a decision is about ---------------------------------------


def test_a_decision_may_name_the_attempt_it_ends(config: evolution.EvolutionConfig, batch: Path) -> None:
    """A precondition, not a lookup: the request states which attempt it was
    built against, and an operator acting on a lineage that has since moved on
    finds that out instead of ending whatever happens to be open."""

    experiments.create(config, ["loader-fallback"], now=NOW)

    result = experiments.abandon(config, reason="the disposition was wrong", experiment_id=EXP_01, now=LATER)

    assert result.experiment_id == EXP_01
    assert record(config, EXP_01)["decision"]["outcome"] == "abandoned"


def test_a_decision_naming_an_attempt_this_batch_never_had_is_refused(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    experiments.create(config, ["loader-fallback"], now=NOW)

    with pytest.raises(evolution.BatchError, match="has no experiment"):
        experiments.abandon(config, reason="drop it", experiment_id=EXP_02, now=LATER)

    assert record(config, EXP_01)["decision"] is None


def test_naming_an_attempt_that_already_ended_asks_to_finish_that_decision(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The ambiguity the name resolves, from the redo side: the request is about
    the attempt that ended, so it holds to that decision's own outcome and
    reason rather than quietly ending the successor standing open."""

    experiments.create(config, ["loader-fallback"], now=NOW)
    experiments.supersede(config, reason="the hook-side approach replaces it", now=LATER)

    result = experiments.supersede(
        config,
        reason="the hook-side approach replaces it",
        experiment_id=EXP_01,
        now=LATEST,
    )

    assert result.recorded is False
    assert result.successor_created is False
    assert result.experiment_id == EXP_01
    assert result.successor_id == EXP_02
    assert not (config.experiments_root / f"{BATCH_ID}-exp-03").exists()


def test_naming_the_open_successor_supersedes_it_for_the_very_same_reason(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The reported finding: unnamed, this shape reads as the predecessor's
    supersession redone, because a human reason is the only thing telling the
    two apart and both spell it identically. Named, both are expressible — and
    this one is a new decision about the attempt that is open."""

    experiments.create(config, ["loader-fallback"], now=NOW)
    experiments.supersede(config, reason="the hook-side approach replaces it", now=LATER)

    unnamed = experiments.supersede(config, reason="the hook-side approach replaces it", now=LATEST)
    assert unnamed.recorded is False
    assert unnamed.experiment_id == EXP_01

    result = experiments.supersede(
        config,
        reason="the hook-side approach replaces it",
        experiment_id=EXP_02,
        now=LATEST,
    )

    assert result.recorded is True
    assert result.experiment_id == EXP_02
    assert result.successor_id == f"{BATCH_ID}-exp-03"
    assert record(config, EXP_02)["decision"]["reason"] == "the hook-side approach replaces it"
    assert record(config, f"{BATCH_ID}-exp-03")["rounds"][0]["tasks"] == []


def test_a_named_redo_holds_to_the_decision_that_is_on_record(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Naming the attempt that ended asks to finish its decision, so a different
    reason is refused rather than applied to the attempt that is open — which is
    the mistake naming a target exists to make impossible."""

    experiments.create(config, ["loader-fallback"], now=NOW)
    experiments.supersede(config, reason="the hook-side approach replaces it", now=LATER)

    with pytest.raises(evolution.BatchError, match="already ended as 'superseded'"):
        experiments.supersede(config, reason="something else entirely", experiment_id=EXP_01, now=LATEST)
    with pytest.raises(evolution.BatchError, match="already ended as 'superseded'"):
        experiments.abandon(config, reason="the hook-side approach replaces it", experiment_id=EXP_01, now=LATEST)

    assert record(config, EXP_02)["decision"] is None
    assert record(config, EXP_01)["decision"]["outcome"] == "superseded"


def test_a_decision_naming_an_attempt_older_than_the_one_that_ended_is_refused(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """With nothing open, the only decision left to finish is the newest
    attempt's: everything before it is history a second decision never edits."""

    experiments.create(config, ["loader-fallback"], now=NOW)
    experiments.supersede(config, reason="the hook-side approach replaces it", now=LATER)
    experiments.abandon(config, reason="the hook side turned out worse", now=LATEST)

    with pytest.raises(evolution.BatchError, match="is not the attempt"):
        experiments.abandon(config, reason="the loader order cannot be fixed", experiment_id=EXP_01, now=LATEST)


def test_a_named_supersession_still_finishes_the_successor_it_owes(
    config: evolution.EvolutionConfig, batch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The interrupted state has no ambiguity to resolve — nothing is open — so
    naming the attempt that owes the successor is the same request as before."""

    experiments.create(config, ["loader-fallback"], now=NOW)
    monkeypatch.setattr(
        experiments,
        "_publish_record",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("interrupted")),
    )
    with pytest.raises(OSError):
        experiments.supersede(config, reason="the hook-side approach replaces it", now=LATER)
    monkeypatch.undo()

    result = experiments.supersede(
        config,
        reason="the hook-side approach replaces it",
        experiment_id=EXP_01,
        now=LATEST,
    )

    assert result.successor_created is True
    assert lineage.describe(config).current.open_experiment.experiment_id == EXP_02


# --- concluding the batch ----------------------------------------------------


def test_concluding_no_change_ends_the_batch_and_fabricates_nothing(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Invariant 7. The record carries the reason and nothing else: no
    candidate, no experiment, no promotion revision."""

    experiments.reject(config, list(DRAFTS), reason="one report each is not recurrence", now=NOW)

    result = experiments.conclude_no_change(config, reason="no cluster reached recurrence", now=LATER)

    assert result.recorded is True
    assert result.outcome == "no-change"
    assert json.loads(result.record_path.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "batch_id": BATCH_ID,
        "outcome": "no-change",
        "decided_at": SEALED_AT,
        "reason": "no cluster reached recurrence",
        "experiment_id": None,
        "promotion_revision": None,
        "promotion": None,
    }
    assert ledger_types(config)[-1] == "batch-concluded"
    assert evolution.current_batch(config) is None
    assert phase.describe(config, now=LATER).phase == phase.PHASE_IDLE


def test_a_batch_between_attempts_concludes_over_its_whole_history(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Every attempt dropped is a valid way to reach `no-change`: the evidence
    justified a change nobody could make work, and the record says so."""

    experiments.create(config, ["loader-fallback"], now=NOW)
    experiments.abandon(config, reason="the loader order cannot be fixed", now=LATER)
    experiments.reject(config, ["hook-side-loader", "not-worth-it"], reason="not recurrence", now=LATER)

    assert phase.describe(config, now=LATER).phase == phase.PHASE_CONCLUSION_PENDING

    result = experiments.conclude_no_change(config, reason="both approaches failed replay", now=LATEST)

    assert result.batch_id == BATCH_ID
    assert lineage.describe(config).current is None
    assert record(config, EXP_01)["decision"]["outcome"] == "abandoned"


def test_concluding_refuses_while_an_attempt_is_open(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    experiments.create(config, ["loader-fallback"], now=NOW)
    experiments.reject(config, ["hook-side-loader", "not-worth-it"], reason="not recurrence", now=NOW)

    with pytest.raises(evolution.BatchError, match="still has an open experiment"):
        experiments.conclude_no_change(config, reason="nothing justified", now=LATER)

    assert not (batch / "outcome.json").exists()


def test_concluding_refuses_while_a_proposal_is_still_waiting(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """"The evidence justified no change" is a claim the batch's own gate has to
    support: a draft nobody decided is a proposal that says otherwise."""

    experiments.reject(config, ["loader-fallback"], reason="one report is not recurrence", now=NOW)

    with pytest.raises(evolution.BatchError, match="waiting at its admission gate"):
        experiments.conclude_no_change(config, reason="nothing justified", now=LATER)


def test_concluding_no_change_refuses_over_a_promoted_attempt(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The contradiction read from the side with nothing to name: a batch whose
    candidate reached the source line concluded by promoting it."""

    seal(config, ["loader-fallback"])
    why = "replay showed fewer remediation rounds with no regression"
    rewrite(
        config,
        EXP_01,
        decision=experiment_decision("promoted", reason=why, promotion_revision="f" * 40),
        promotion=prepared_promotion(candidate_revision=candidate_of(config, EXP_01), reason=why),
    )
    experiments.reject(config, ["hook-side-loader", "not-worth-it"], reason="not recurrence", now=LATER)

    with pytest.raises(evolution.BatchError, match="record a promotion"):
        experiments.conclude_no_change(config, reason="nothing justified", now=LATEST)


def test_concluding_records_why(config: evolution.EvolutionConfig, batch: Path) -> None:
    experiments.reject(config, list(DRAFTS), reason="one report each is not recurrence", now=NOW)

    with pytest.raises(evolution.BatchError, match="concluding records why"):
        experiments.conclude_no_change(config, reason="", now=LATER)

    assert not (batch / "outcome.json").exists()


def test_concluding_needs_a_current_batch(config: evolution.EvolutionConfig) -> None:
    with pytest.raises(evolution.BatchError, match="nothing to conclude"):
        experiments.conclude_no_change(config, reason="nothing justified", now=NOW)


def test_concluding_again_finishes_an_interrupted_conclusion(
    config: evolution.EvolutionConfig, batch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The outcome record ends the batch, so a redo cannot find it by asking
    which batch is current — its own first run is why none is."""

    experiments.reject(config, list(DRAFTS), reason="one report each is not recurrence", now=NOW)
    monkeypatch.setattr(
        experiments,
        "append_records",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("interrupted")),
    )
    with pytest.raises(OSError):
        experiments.conclude_no_change(config, reason="no cluster reached recurrence", now=LATER)
    monkeypatch.undo()

    result = experiments.conclude_no_change(config, reason="no cluster reached recurrence", now=LATEST)

    assert result.recorded is False
    assert result.batch_id == BATCH_ID
    assert result.decided_at == SEALED_AT
    assert json.loads(result.record_path.read_text(encoding="utf-8"))["decided_at"] == SEALED_AT


def test_a_conclusion_nobody_recorded_is_not_read_as_one(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """A different reason after the batch ended is a new decision about a cycle
    that is over, not the same one redone."""

    experiments.reject(config, list(DRAFTS), reason="one report each is not recurrence", now=NOW)
    experiments.conclude_no_change(config, reason="no cluster reached recurrence", now=LATER)

    with pytest.raises(evolution.BatchError, match="nothing to conclude"):
        experiments.conclude_no_change(config, reason="on reflection, something else", now=LATEST)


# --- promoting ---------------------------------------------------------------
#
# The other way a batch ends, and the only one that writes outside this
# repository's own records: a commit on the source line. So these run against a
# real release ref, a real merge, and a real replay — what a promotion carries is
# the tree a run measured, and a fixture standing in for any of the three would
# leave that proved only against itself.


RELEASE_REF = "refs/heads/release"
PROMOTED = datetime(2026, 8, 8, 9, 0, 0, tzinfo=timezone.utc)
PROMOTED_AT = "2026-08-08T09:00:00Z"
EXPECTED = "fewer remediation rounds, with quality and elapsed time unchanged"
WHY = "replay showed fewer remediation rounds with no regression"
TARGETS = ("orch-hub", "ai-native-development")


@pytest.fixture
def release(config: evolution.EvolutionConfig) -> str:
    """The source line a candidate is promoted onto — a ref of its own rather
    than whichever branch this checkout is on, because that is what the merge
    input is a property of."""

    sha = git_rev(config.repo_root, "HEAD")
    git_update_ref(config.repo_root, RELEASE_REF, sha)
    return sha


def prepared(config: evolution.EvolutionConfig, *, report: object | None = None) -> replay.Replay:
    """Everything a promotion needs before it can be one: a settled gate, a
    sealed round, and a completed run measuring that round's candidate as it
    would be integrated onto the release line."""

    experiments.reject(config, ["not-worth-it"], reason="one report is not recurrence", now=NOW)
    seal(config, ["loader-fallback", "hook-side-loader"])
    harness = FakeHarness(report=report if report is not None else completed_report())
    replay.start(config, harness, source_ref=RELEASE_REF, expectation=EXPECTED, now=LATEST)
    return replay.conclude(config, harness, now=LATEST).replay


def outcome_of(config: evolution.EvolutionConfig) -> dict:
    return json.loads((config.batches_root / BATCH_ID / "outcome.json").read_text(encoding="utf-8"))


def repoint(config: evolution.EvolutionConfig, ref: str) -> None:
    """Say the recorded run integrated onto another line. For the cases where
    what is being tested is which ref a promotion would move, rather than which
    one the replay fixture happened to use."""

    path = config.experiments_root / EXP_01 / "replays.json"
    written = json.loads(path.read_text(encoding="utf-8"))
    written["replays"][0]["integration"]["merge_input_ref"] = ref
    path.write_text(json.dumps(written, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_promoting_puts_the_measured_tree_on_the_source_line(
    config: evolution.EvolutionConfig, batch: Path, release: str
) -> None:
    """The merge unit: the line as it stood and the round's pinned candidate as
    parents, the tree the replay measured as the content. Asserted against Git
    rather than against the record that claims it."""

    run = prepared(config)

    result = experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)

    assert result.merged is True and result.recorded is True
    assert result.promotion_revision == git_rev(config.repo_root, RELEASE_REF)
    assert result.promotion_revision not in (run.integration.candidate_revision, release)
    assert git_tree(config.repo_root, result.promotion_revision) == run.integration.tree
    assert revisions.commit_shape(config.repo_root, result.promotion_revision) == (
        run.integration.tree,
        (release, run.integration.candidate_revision),
    )
    assert result.merge_input_ref == RELEASE_REF and result.merge_input_revision == release
    assert result.round_number == 1


def test_a_promotion_is_recorded_by_both_the_experiment_and_the_batch(
    config: evolution.EvolutionConfig, batch: Path, release: str
) -> None:
    """One event, two records, and every later reading checks them against each
    other: the decision turns the attempt into history, the outcome ends the
    batch and states the merge unit that was promoted."""

    run = prepared(config)

    result = experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)

    decision = record(config, EXP_01)["decision"]
    assert decision["outcome"] == "promoted"
    assert decision["promotion_revision"] == result.promotion_revision
    assert decision["superseded_by"] is None
    assert decision["decided_at"] == PROMOTED_AT

    assert outcome_of(config) == {
        "schema_version": 1,
        "batch_id": BATCH_ID,
        "outcome": "promoted",
        "decided_at": PROMOTED_AT,
        "reason": WHY,
        "experiment_id": EXP_01,
        "promotion_revision": result.promotion_revision,
        "promotion": {
            "round": 1,
            "candidate_revision": run.integration.candidate_revision,
            "merge_input_revision": release,
            "merge_input_ref": RELEASE_REF,
            "tree": run.integration.tree,
            "planned_targets": list(TARGETS),
        },
    }
    assert ledger_types(config)[-2:] == ["experiment-promoted", "batch-concluded"]


def test_a_promoted_batch_is_over_and_releases_the_next_cohort(
    config: evolution.EvolutionConfig, batch: Path, release: str
) -> None:
    """Invariant 14: only a promotion or a no-change conclusion ends a batch, and
    what `status` reports afterwards is the promotion as history."""

    prepared(config)
    result = experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)

    assert lineage.current_batch(config) is None
    status = phase.describe(config, now=PROMOTED)
    assert status.current_batch is None
    assert status.last_promotion is not None
    assert status.last_promotion.revision == result.promotion_revision
    assert status.last_promotion.round_number == 1
    assert status.last_promotion.planned_targets == TARGETS
    assert status.to_json()["last_promotion"]["tree"] == result.tree


def test_a_promotion_this_controller_made_before_the_merge_unit_existed_still_reports(
    config: evolution.EvolutionConfig, batch: Path, release: str
) -> None:
    """The whole reason a version-1 promoted record is read rather than refused.
    The experiment that promoted this batch was written by a build that recorded
    only the revision; the outcome it wrote states the merge unit, and `status`
    goes on answering — where refusing the record would take the batch it ended,
    every batch after it, and every operation guarded by that reading."""

    result = prepared_promotion_at_version_1(config)

    assert lineage.current_batch(config) is None
    status = phase.describe(config, now=PROMOTED)
    assert status.last_promotion is not None
    assert status.last_promotion.revision == result.promotion_revision
    assert status.to_json()["last_promotion"]["tree"] == result.tree


def prepared_promotion_at_version_1(config: evolution.EvolutionConfig) -> experiments.PromotionResult:
    """A promoted batch whose experiment record is the shape the build before the
    prepared promotion wrote — the promotion made here, then the record put back
    the way that build would have left it."""

    prepared(config)
    result = experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)
    downgrade(config, EXP_01)
    return result


def test_the_planned_targets_are_a_plan_and_never_a_deployment(
    config: evolution.EvolutionConfig, batch: Path, release: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A promoted revision reaches a target when that target is redeployed and
    not before, so the record says what was intended and the rendering says
    where to ask what is actually there.

    The registry location is overridable and machine-local, so the override is
    cleared: what this asserts is about the plan, not about which repositories
    the machine running the suite happens to manage.
    """

    monkeypatch.delenv("AI_NATIVE_DEPLOYMENT_REGISTRY", raising=False)
    prepared(config)
    experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)

    text = render.format_status(phase.describe(config, now=PROMOTED))
    assert "planned targets: orch-hub, ai-native-development" in text
    assert "the plan this promotion recorded, not what they hold" in text
    # And the reading beside it, which is where what they hold is answered: this
    # machine manages neither of those names, and says so per target rather than
    # letting the plan above stand in for it.
    assert "what each planned target holds now, from its own .ai-deploy-lock.json:" in text
    assert "orch-hub — no repository of that name is registered on this machine" in text


def test_a_promotion_may_plan_no_target_yet(
    config: evolution.EvolutionConfig, batch: Path, release: str
) -> None:
    """Empty is a fact about the plan, not about the deployment."""

    prepared(config)
    result = experiments.promote(config, reason=WHY, targets=(), now=PROMOTED)

    assert result.planned_targets == ()
    assert outcome_of(config)["promotion"]["planned_targets"] == []
    assert "planned targets: none named" in render.format_status(phase.describe(config, now=PROMOTED))


@pytest.mark.parametrize(
    "targets",
    [["/Users/someone/checkouts/target"], ["~/target"], ["../target"], [""]],
    ids=["absolute-path", "home-relative", "relative-path", "empty"],
)
def test_a_machine_local_path_is_not_a_planned_target(
    config: evolution.EvolutionConfig, batch: Path, release: str, targets: list[str]
) -> None:
    """The record is committed and the next reader's checkout is somewhere else.
    Refused before anything moves, since by the time a record is validated the
    merge would already be on the line."""

    prepared(config)

    with pytest.raises(evolution.ValidationError, match="targets planned for this promotion"):
        experiments.promote(config, reason=WHY, targets=targets, now=PROMOTED)

    assert git_rev(config.repo_root, RELEASE_REF) == release
    assert not (config.batches_root / BATCH_ID / "outcome.json").exists()


def test_a_target_planned_twice_refuses(
    config: evolution.EvolutionConfig, batch: Path, release: str
) -> None:
    prepared(config)

    with pytest.raises(evolution.BatchError, match="named more than once as a planned target"):
        experiments.promote(config, reason=WHY, targets=["orch-hub", "orch-hub"], now=PROMOTED)


def test_promoting_records_why(config: evolution.EvolutionConfig, batch: Path, release: str) -> None:
    prepared(config)

    with pytest.raises(evolution.BatchError, match="a promotion records why"):
        experiments.promote(config, reason="  ", targets=TARGETS, now=PROMOTED)

    assert git_rev(config.repo_root, RELEASE_REF) == release


# --- what a promotion is refused on ------------------------------------------


def test_a_round_nobody_replayed_cannot_be_promoted(
    config: evolution.EvolutionConfig, batch: Path, release: str
) -> None:
    """Invariant 10: what reaches the source line is a tree something measured."""

    experiments.reject(config, ["not-worth-it"], reason="one report is not recurrence", now=NOW)
    seal(config, ["loader-fallback", "hook-side-loader"])

    with pytest.raises(evolution.BatchError, match="is incomplete and cannot be promoted"):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)

    assert git_rev(config.repo_root, RELEASE_REF) == release
    assert record(config, EXP_01)["decision"] is None


def test_a_run_that_failed_cannot_be_promoted(
    config: evolution.EvolutionConfig, batch: Path, release: str
) -> None:
    prepared(config, report=completed_report(outcome="failed", metrics=(), elapsed_seconds=None))

    with pytest.raises(evolution.BatchError, match="is failed and cannot be promoted"):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)

    assert git_rev(config.repo_root, RELEASE_REF) == release


def test_a_source_line_that_moved_since_the_run_cannot_be_promoted(
    config: evolution.EvolutionConfig, batch: Path, release: str
) -> None:
    """The candidate is immutable and the line is not: a run that was exact
    yesterday describes nothing today, and the answer is another run rather than
    a promotion of what was measured against a line that has gone."""

    prepared(config)
    git_update_ref(config.repo_root, RELEASE_REF, git_sibling_commit(config.repo_root, release, "later\n", "release work"))
    moved = git_rev(config.repo_root, RELEASE_REF)

    with pytest.raises(evolution.BatchError, match="is stale and cannot be promoted"):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)

    assert git_rev(config.repo_root, RELEASE_REF) == moved


def test_a_check_this_checkout_cannot_make_is_not_one_that_passed(
    config: evolution.EvolutionConfig, batch: Path, release: str
) -> None:
    """A clone without the source-line ref cannot say whether the merge input
    moved, and an unanswered check is not agreement."""

    prepared(config)
    git_delete_ref(config.repo_root, RELEASE_REF)

    with pytest.raises(evolution.BatchError, match="cannot be answered here"):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)


def test_a_revised_round_leaves_its_evidence_behind(
    config: evolution.EvolutionConfig, batch: Path, release: str
) -> None:
    """Invariant 16: a round's evidence describes the candidate that round
    pinned, so opening the next one is what makes it stale by construction."""

    prepared(config)
    experiments.revise(config, reason="the excluded case needs the loader fallback too", now=PROMOTED)

    with pytest.raises(evolution.BatchError, match="round 2 .* cannot be promoted"):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)

    assert git_rev(config.repo_root, RELEASE_REF) == release


def test_a_run_still_going_holds_the_promotion_back(
    config: evolution.EvolutionConfig, batch: Path, release: str
) -> None:
    """A promotion ends the experiment, and nothing could afterwards conclude
    that run or record why it stopped. Promotable evidence says nothing about it:
    a second run started beside a result that is still exact leaves that result
    promotable by design."""

    prepared(config)
    replay.start(config, FakeHarness(), source_ref=RELEASE_REF, expectation=EXPECTED, now=PROMOTED)

    with pytest.raises(evolution.BatchError, match="round 1 attempt 2 .* is still running"):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)

    assert git_rev(config.repo_root, RELEASE_REF) == release
    assert record(config, EXP_01)["decision"] is None


class Unanswering(FakeHarness):
    """A harness asked for a run that never says what it began.

    The window the durable request exists to cover: the record names the run and
    this controller has heard nothing back, so something may be measuring the
    round right now.
    """

    def start(self, request: object) -> object:
        raise RuntimeError("the harness never answered")


def test_an_outstanding_request_holds_the_promotion_back(
    config: evolution.EvolutionConfig, batch: Path, release: str
) -> None:
    """The harness may be running something this record does not name yet, and a
    promotion would leave nobody able to answer for it. The reader deliberately
    does not report the request beside promotable evidence, so this is asked of
    the record."""

    prepared(config)
    with pytest.raises(RuntimeError):
        replay.start(config, Unanswering(), source_ref=RELEASE_REF, expectation=EXPECTED, now=PROMOTED)

    with pytest.raises(evolution.BatchError, match="round 1 attempt 2 outstanding"):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)

    assert git_rev(config.repo_root, RELEASE_REF) == release


def test_a_proposal_still_waiting_holds_the_promotion_back(
    config: evolution.EvolutionConfig, batch: Path, release: str
) -> None:
    """The gate belongs to the batch, and a promotion ends the batch: a draft
    left waiting is this batch's own analysis with nobody left to answer it."""

    seal(config, ["loader-fallback", "hook-side-loader"])
    harness = FakeHarness(report=completed_report())
    replay.start(config, harness, source_ref=RELEASE_REF, expectation=EXPECTED, now=LATEST)
    replay.conclude(config, harness, now=LATEST)

    with pytest.raises(evolution.BatchError, match="waiting at its admission gate"):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)

    assert git_rev(config.repo_root, RELEASE_REF) == release


def test_only_the_tree_that_was_measured_is_promoted(
    config: evolution.EvolutionConfig, batch: Path, release: str
) -> None:
    """Two commits only imply a merge result. A checkout that merges them into
    something else — another strategy, another normalization — would put a tree
    nobody exercised on the line with every recorded revision agreeing."""

    prepared(config)
    path = config.experiments_root / EXP_01 / "replays.json"
    written = json.loads(path.read_text(encoding="utf-8"))
    written["replays"][0]["integration"]["tree"] = "b" * 40
    path.write_text(json.dumps(written, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(evolution.BatchError, match="what is promoted is the tree that was exercised"):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)

    assert git_rev(config.repo_root, RELEASE_REF) == release


def test_a_source_line_that_moves_under_the_promotion_refuses(
    config: evolution.EvolutionConfig, batch: Path, release: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The line is read and moved afterwards, and nothing this package locks
    covers the gap — what advances a release line is ordinary Git. So the move
    is a compare-and-swap against the revision the run integrated onto, and a
    line that took a commit between the last look and the write refuses in Git's
    own words rather than carrying a tree nobody measured."""

    prepared(config)
    stood = experiments._standing

    def interleaved(config_, experiment, prepared_):
        answer = stood(config_, experiment, prepared_)
        git_update_ref(config.repo_root, RELEASE_REF, git_sibling_commit(config.repo_root, release, "x\n", "meanwhile"))
        return answer

    monkeypatch.setattr(experiments, "_standing", interleaved)

    with pytest.raises(evolution.BatchError, match=f"{RELEASE_REF} did not take"):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)

    assert record(config, EXP_01)["decision"] is None
    assert not (config.batches_root / BATCH_ID / "outcome.json").exists()
    # The prepared promotion stays: nothing here proves it never landed, and the
    # next run is what asks the line rather than assuming either way.
    assert record(config, EXP_01)["promotion"] is not None


def test_a_line_that_moved_before_the_merge_landed_gives_up_the_promotion(
    config: evolution.EvolutionConfig, batch: Path, release: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same race caught one step earlier, where the answer is different: the
    merge demonstrably never reached the line, so the prepared promotion is
    discarded rather than left blocking the experiment for work that can never
    be finished — the evidence behind it describes a line that has moved on."""

    prepared(config)
    made = revisions.commit_tree

    def interleaved(repo_root, tree, parents, message):
        result = made(repo_root, tree, parents, message)
        git_update_ref(config.repo_root, RELEASE_REF, git_sibling_commit(config.repo_root, release, "x\n", "meanwhile"))
        return result

    monkeypatch.setattr(experiments, "commit_tree", interleaved)

    with pytest.raises(evolution.BatchError, match="never reached"):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)

    assert record(config, EXP_01)["promotion"] is None
    assert record(config, EXP_01)["decision"] is None
    assert not (config.batches_root / BATCH_ID / "outcome.json").exists()
    # And the experiment is free again: the guard that refuses to end an attempt
    # under a prepared promotion has nothing left to hold.
    experiments.abandon(config, reason="the release line moved on", now=LATEST)


def test_the_line_a_working_tree_is_on_is_not_promoted_onto(
    config: evolution.EvolutionConfig, batch: Path, release: str
) -> None:
    """A promotion moves a ref and touches no working tree, which is only safe
    while the ref is nobody's: moving this one would leave that tree and its
    index describing the commit before it."""

    run = prepared(config)
    checked_out = revisions.checked_out_refs(config.repo_root)
    assert checked_out and RELEASE_REF not in checked_out
    branch = next(iter(checked_out))
    git_update_ref(config.repo_root, branch, run.integration.merge_input_revision)
    repoint(config, branch)

    with pytest.raises(evolution.BatchError, match="is checked out at"):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)

    assert git_rev(config.repo_root, branch) == run.integration.merge_input_revision
    assert record(config, EXP_01)["decision"] is None
    assert record(config, EXP_01)["promotion"] is None


def test_a_line_checked_out_by_another_worktree_is_not_promoted_onto(
    config: evolution.EvolutionConfig, batch: Path, release: str, tmp_path: Path
) -> None:
    """`HEAD` answers only for the checkout that asks. A branch handed to a
    linked worktree is one this process never looks at, and `update-ref` moves
    it there without a word — leaving a directory the operator may not have
    thought about describing the commit before."""

    prepared(config)
    linked = git_worktree(config.repo_root, tmp_path / "linked", RELEASE_REF)
    assert Path(revisions.checked_out_refs(config.repo_root)[RELEASE_REF]).resolve() == linked

    with pytest.raises(evolution.BatchError, match=f"{RELEASE_REF} is checked out at"):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)

    assert git_rev(config.repo_root, RELEASE_REF) == release
    assert record(config, EXP_01)["promotion"] is None


def test_promoting_needs_an_open_experiment(
    config: evolution.EvolutionConfig, batch: Path, release: str
) -> None:
    """A terminal decision is never reopened, and an attempt that was abandoned
    is not one a promotion can be argued for afterwards."""

    prepared(config)
    experiments.abandon(config, reason="the regression is in the approach", now=PROMOTED)

    with pytest.raises(evolution.BatchError, match="has no open experiment"):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)


# --- a promotion whose records did not land ----------------------------------


def interrupt(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Stop the promotion at one of its writes, the way a process that dies
    between two of them does."""

    monkeypatch.setattr(
        experiments,
        name,
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("interrupted")),
    )


def test_the_merge_a_promotion_made_is_recognised_rather_than_made_again(
    config: evolution.EvolutionConfig, batch: Path, release: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once the line has moved the promotion exists in the world, and this
    experiment's evidence is stale from then on — including for the run that
    comes back to finish it. So the second run asks the prepared record which
    commit was this operation's, and Git whether that commit is on the line,
    before it asks anything of the evidence."""

    run = prepared(config)
    interrupt(monkeypatch, "_decide")
    with pytest.raises(OSError):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)
    monkeypatch.undo()
    carried = git_rev(config.repo_root, RELEASE_REF)
    assert carried != release
    assert record(config, EXP_01)["promotion"]["revision"] == carried

    result = experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)

    assert result.merged is False and result.recorded is True
    assert result.promotion_revision == carried
    assert git_rev(config.repo_root, RELEASE_REF) == carried
    assert revisions.commit_shape(config.repo_root, carried) == (
        run.integration.tree,
        (release, run.integration.candidate_revision),
    )
    assert outcome_of(config)["promotion_revision"] == carried


def test_a_promotion_the_line_has_moved_past_is_still_finished(
    config: evolution.EvolutionConfig, batch: Path, release: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A line that took another commit after the promotion still carries the
    promotion. Reading the *tip* would call that unrecognisable and leave the
    operation half-done for good — the merge on the canonical line with no
    record of it anywhere — so the question is ancestry."""

    prepared(config)
    interrupt(monkeypatch, "_decide")
    with pytest.raises(OSError):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)
    monkeypatch.undo()
    carried = git_rev(config.repo_root, RELEASE_REF)
    later = git_sibling_commit(config.repo_root, carried, "later\n", "ordinary release work")
    git_update_ref(config.repo_root, RELEASE_REF, later)

    result = experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)

    assert result.merged is False and result.promotion_revision == carried
    # Nothing rewound: the promotion is a fact about a commit, not about where
    # the branch happens to stand afterwards.
    assert git_rev(config.repo_root, RELEASE_REF) == later
    assert record(config, EXP_01)["decision"]["promotion_revision"] == carried
    assert outcome_of(config)["promotion"]["merge_input_revision"] == release


def test_a_promotion_interrupted_before_the_line_moved_moves_it_next_time(
    config: evolution.EvolutionConfig, batch: Path, release: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window the prepared record exists for: a merge commit made, named by
    the record, and not yet on the line. The next run moves that commit rather
    than making a second one."""

    prepared(config)
    interrupt(monkeypatch, "move_ref")
    with pytest.raises(OSError):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)
    monkeypatch.undo()
    made = record(config, EXP_01)["promotion"]["revision"]
    assert git_rev(config.repo_root, RELEASE_REF) == release

    result = experiments.promote(config, reason=WHY, targets=TARGETS, now=LATEST)

    assert result.promotion_revision == made
    assert git_rev(config.repo_root, RELEASE_REF) == made
    assert record(config, EXP_01)["promotion"]["revision"] == made
    assert outcome_of(config)["promotion_revision"] == made


def test_an_attempt_is_not_ended_under_a_prepared_promotion(
    config: evolution.EvolutionConfig, batch: Path, release: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one place this controller could create the split it exists to
    prevent: abandoning the experiment retires the only record saying the line
    may already be carrying its merge."""

    prepared(config)
    interrupt(monkeypatch, "move_ref")
    with pytest.raises(OSError):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)
    monkeypatch.undo()

    for ending in (
        lambda: experiments.abandon(config, reason="on reflection, no", now=LATEST),
        lambda: experiments.supersede(config, reason="try another approach", now=LATEST),
        # And the other way to move on: a round opened over the prepared merge
        # leaves it naming a round that is no longer the last, which is a
        # promotion nothing could afterwards record.
        lambda: experiments.revise(config, reason="one more pass", now=LATEST),
    ):
        with pytest.raises(evolution.BatchError, match="moving on now would retire"):
            ending()


def test_a_record_that_lost_its_prepared_promotion_stops_every_operation(
    config: evolution.EvolutionConfig, batch: Path, release: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the guard above rests on, from the record's side. The prepared
    promotion is the whole of what this controller knows about a merge it has
    made, so a record that no longer states it must fail closed: read as "none
    prepared", the same three operations would proceed and `promote` would make a
    second merge for a line that may already be carrying the first.

    The version is what makes that reading available at all — a current record
    always writes the field — so a lost one is refused where a version-1 record's
    absent one is read as the shape it is."""

    prepared(config)
    interrupt(monkeypatch, "move_ref")
    with pytest.raises(OSError):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)
    monkeypatch.undo()
    made = record(config, EXP_01)["promotion"]["revision"]

    path = config.experiments_root / EXP_01 / "experiment.json"
    written = json.loads(path.read_text(encoding="utf-8"))
    del written["promotion"]
    path.write_text(json.dumps(written, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    for operation in (
        lambda: experiments.abandon(config, reason="on reflection, no", now=LATEST),
        lambda: experiments.supersede(config, reason="try another approach", now=LATEST),
        lambda: experiments.revise(config, reason="one more pass", now=LATEST),
        lambda: experiments.promote(config, reason=WHY, targets=TARGETS, now=LATEST),
    ):
        with pytest.raises(evolution.ValidationError, match="missing required property 'promotion'"):
            operation()

    # Nothing moved on the strength of a record nobody could read: the line still
    # stands where it did, and the merge that run made is still off it — named by
    # nothing but the field a repair has to put back.
    assert git_rev(config.repo_root, RELEASE_REF) == release
    assert revisions.contains(config.repo_root, made, release) is False


def test_a_line_that_moved_for_somebody_else_is_not_this_promotion(
    config: evolution.EvolutionConfig, batch: Path, release: str
) -> None:
    """An ordinary commit on the line is refused as the stale evidence it makes,
    rather than adopted as a promotion nobody made."""

    prepared(config)
    git_update_ref(config.repo_root, RELEASE_REF, git_sibling_commit(config.repo_root, release, "other\n", "release work"))

    with pytest.raises(evolution.BatchError, match="is stale and cannot be promoted"):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)


def test_a_merge_this_operation_did_not_make_is_not_its_promotion(
    config: evolution.EvolutionConfig, batch: Path, release: str
) -> None:
    """The hard case for recognition by shape, and the reason the commit is
    recorded instead: a merge somebody made by hand has the same two parents and
    the same tree, down to the last byte but its own identity. Adopting it would
    record a promotion this controller never performed — with the operator's
    reason, the plan, and the audit line attached to somebody else's commit."""

    run = prepared(config)
    by_hand, complaint = revisions.commit_tree(
        config.repo_root,
        run.integration.tree,
        [release, run.integration.candidate_revision],
        "merge the candidate, by hand",
    )
    assert by_hand is not None, complaint
    git_update_ref(config.repo_root, RELEASE_REF, by_hand)

    with pytest.raises(evolution.BatchError, match="is stale and cannot be promoted"):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)

    assert record(config, EXP_01)["decision"] is None
    assert record(config, EXP_01)["promotion"] is None
    assert not (config.batches_root / BATCH_ID / "outcome.json").exists()


def test_a_decision_without_its_outcome_is_finished_by_the_same_promotion(
    config: evolution.EvolutionConfig, batch: Path, release: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The decision makes the promotion real and the outcome ends the batch, so
    between them is a batch still current with nothing open — this operation's
    own redo, rebuilt from the records the first run left."""

    run = prepared(config)
    monkeypatch.setattr(
        experiments,
        "_conclude_promoted",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("interrupted")),
    )
    with pytest.raises(OSError):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)
    monkeypatch.undo()
    carried = git_rev(config.repo_root, RELEASE_REF)
    assert record(config, EXP_01)["decision"]["promotion_revision"] == carried
    assert lineage.current_batch(config) is not None

    result = experiments.promote(config, reason=WHY, targets=TARGETS, now=LATEST)

    assert result.merged is False and result.recorded is True
    # The moment the decision was made, not the moment this finished it.
    assert result.decided_at == PROMOTED_AT
    assert outcome_of(config)["decided_at"] == PROMOTED_AT
    assert outcome_of(config)["promotion"]["tree"] == run.integration.tree
    # The decision's audit line may have landed and nothing can tell; an audit is
    # not state, so it is not written a second time.
    assert ledger_types(config).count("experiment-promoted") == 1
    assert ledger_types(config)[-1] == "batch-concluded"


def downgrade(config: evolution.EvolutionConfig, experiment_id: str) -> None:
    """Put a record back into the shape the build before the prepared promotion
    wrote: its version, and no merge unit — the field did not exist there."""

    path = config.experiments_root / experiment_id / "experiment.json"
    written = json.loads(path.read_text(encoding="utf-8"))
    written["schema_version"] = 1
    written.pop("promotion", None)
    path.write_text(json.dumps(written, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_a_decision_recorded_before_the_merge_unit_existed_is_reported_not_guessed(
    config: evolution.EvolutionConfig, batch: Path, release: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same interrupted window, left by the build that recorded no merge unit
    on the experiment. The unit itself is recoverable from the run and the commit;
    the targets that promotion was planned for are recoverable from nowhere,
    because nothing wrote them down. Taking this run's would state a plan nobody
    promoted under as the original, so the state is reported instead.

    The lineage still reads throughout — which is the point of accepting the
    record at all — so `status` answers and only the operation that would have to
    invent something refuses."""

    prepared(config)
    interrupt(monkeypatch, "_conclude_promoted")
    with pytest.raises(OSError):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)
    monkeypatch.undo()
    downgrade(config, EXP_01)

    assert lineage.current_batch(config) is not None

    with pytest.raises(evolution.BatchError, match="the plan was never recorded"):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=LATEST)


def test_an_operation_over_a_record_written_at_version_1_writes_it_at_this_one(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Nothing migrates a record on the way in: the next operation that writes one
    writes the whole record at the current version, which is what the serializer
    already did with every other field."""

    experiments.create(config, ["loader-fallback"], now=NOW)
    downgrade(config, EXP_01)

    experiments.add_tasks(config, ["hook-side-loader"], now=LATER)

    assert record(config, EXP_01)["schema_version"] == lineage.EXPERIMENT_SCHEMA_VERSION == 2


def test_a_different_reason_after_the_decision_is_not_that_promotion_redone(
    config: evolution.EvolutionConfig, batch: Path, release: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared(config)
    interrupt(monkeypatch, "_conclude_promoted")
    with pytest.raises(OSError):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)
    monkeypatch.undo()

    with pytest.raises(evolution.BatchError, match="already ended as 'promoted'"):
        experiments.promote(config, reason="on reflection, something else", targets=TARGETS, now=LATEST)


def test_the_outcome_states_the_plan_the_promotion_was_made_under(
    config: evolution.EvolutionConfig, batch: Path, release: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A promotion is identified by everything it was made under, not by its
    human sentence. A retry naming other targets is refused rather than allowed
    to write them as the original plan — the record would then say the promotion
    was made for a set of targets nobody promoted to."""

    prepared(config)
    interrupt(monkeypatch, "_conclude_promoted")
    with pytest.raises(OSError):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)
    monkeypatch.undo()

    with pytest.raises(evolution.BatchError, match="prepared for .* planning"):
        experiments.promote(config, reason=WHY, targets=("orch-hub",), now=LATEST)

    result = experiments.promote(config, reason=WHY, targets=TARGETS, now=LATEST)
    assert result.planned_targets == TARGETS
    assert outcome_of(config)["promotion"]["planned_targets"] == list(TARGETS)


def test_a_promotion_from_a_cycle_that_closed_is_not_this_request_redone(
    config: evolution.EvolutionConfig, batch: Path, release: str
) -> None:
    """The redo reports the batch its own first run ended, which is the newest
    one. Searching every batch for a matching reason fetches back a promotion
    from a cycle that closed long ago and reports it as this request's work —
    while what this request actually needs is a batch to promote from."""

    prepared(config)
    first = experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)
    write_manifest(config.batches_root, SECOND_BATCH, ["r3"], analysis_task_id="2026-08-04-second-analysis")
    write_closure(config.batches_root, SECOND_BATCH, analysis_task_id="2026-08-04-second-analysis")
    (config.batches_root / SECOND_BATCH / "findings.md").write_text("# Findings\n", encoding="utf-8")
    experiments.conclude_no_change(config, reason="the second cohort justified nothing", now=LATEST)

    with pytest.raises(evolution.BatchError, match="nothing to promote"):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=LATEST)

    # And the promotion it would have reported is untouched history.
    assert phase.describe(config, now=LATEST).last_promotion.revision == first.promotion_revision


def test_a_finished_promotion_is_reported_only_to_the_request_that_made_it(
    config: evolution.EvolutionConfig, batch: Path, release: str
) -> None:
    """Same rule at the far end: the whole request identifies the operation, so
    a retry naming other targets is a different promotion of a batch that no
    longer exists rather than this one, already done."""

    prepared(config)
    experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)

    with pytest.raises(evolution.BatchError, match="nothing to promote"):
        experiments.promote(config, reason=WHY, targets=("orch-hub",), now=LATEST)


def test_promoting_again_reports_the_promotion_rather_than_making_a_second(
    config: evolution.EvolutionConfig, batch: Path, release: str
) -> None:
    """The outcome ends the batch, so the redo cannot find its own work by asking
    which batch is current — its first run is why none is."""

    prepared(config)
    first = experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)

    again = experiments.promote(config, reason=WHY, targets=TARGETS, now=LATEST)

    assert again.recorded is False and again.merged is False
    assert again.promotion_revision == first.promotion_revision
    assert again.decided_at == PROMOTED_AT
    assert again.tree == first.tree
    assert git_rev(config.repo_root, RELEASE_REF) == first.promotion_revision
    assert ledger_types(config).count("batch-concluded") == 1


def test_a_promotion_nobody_recorded_is_not_read_as_one(
    config: evolution.EvolutionConfig, batch: Path, release: str
) -> None:
    prepared(config)
    experiments.promote(config, reason=WHY, targets=TARGETS, now=PROMOTED)

    with pytest.raises(evolution.BatchError, match="nothing to promote"):
        experiments.promote(config, reason="a different candidate entirely", targets=TARGETS, now=LATEST)


def test_promoting_needs_a_current_batch(config: evolution.EvolutionConfig) -> None:
    with pytest.raises(evolution.BatchError, match="nothing to promote"):
        experiments.promote(config, reason=WHY, targets=TARGETS, now=NOW)


# --- the rest of the controller ----------------------------------------------


def test_the_lock_is_the_same_one_import_and_freeze_take(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Each of these writes several places at once; a second writer between two
    of them is exactly what the single-writer guard exists to prevent."""

    with state.single_writer_lock(config):
        with pytest.raises(evolution.LockError, match="evolution lock held"):
            experiments.create(config, ["loader-fallback"], now=NOW)


def test_the_phase_follows_the_admission(config: evolution.EvolutionConfig, batch: Path) -> None:
    """Nothing stores the lifecycle: `status` re-derives it from the records
    these operations write."""

    assert phase.describe(config, now=NOW).phase == phase.PHASE_PROPOSALS_PENDING

    experiments.create(config, ["loader-fallback"], now=NOW)
    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_IMPLEMENTING
    assert status.summary == f"implementing {EXP_01} round 1 (1 task left)"
    assert status.implementation_tasks == ("2026-08-01-loader-fallback",)
    assert status.revisions.base is not None
    assert status.revisions.base.sha == git_rev(config.repo_root, "HEAD")


def test_an_experiment_created_here_reads_back_as_one_lineage(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The writer and the reader are two halves of one contract, and a record the
    reader refuses is a record that must never have been written."""

    experiments.create(config, ["loader-fallback", "hook-side-loader"], now=NOW)

    derived = lineage.describe(config).current
    assert derived.open_experiment is not None
    assert derived.open_experiment.experiment_id == EXP_01
    assert derived.ref is not None
    assert derived.ref.consistent is True
    assert derived.ref.state == lineage.REF_AT_PIN
    assert derived.candidate_revision is None


def test_a_record_the_reader_would_refuse_is_never_published(
    config: evolution.EvolutionConfig, batch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every write here goes out through the reader's own parse, so the rules the
    schema subset cannot state — a round sealed with nothing admitted, a decision
    carrying the wrong field, `promoted` from a round nobody sealed — are
    enforced on the way out by the code that enforces them on the way in. A
    second statement of them beside the writer is what would let the two drift,
    and the direction it drifts in is a record this controller wrote and can no
    longer read."""

    refused: list[str] = []

    def refuse(_config, record, _directory):
        refused.append(record["experiment_id"])
        raise evolution.BatchError("the reader refuses this record")

    monkeypatch.setattr(experiments, "parse_experiment", refuse)

    with pytest.raises(evolution.BatchError, match="the reader refuses this record"):
        experiments.create(config, ["loader-fallback"], now=NOW)

    assert refused == [EXP_01]
    assert not (config.experiments_root / EXP_01).exists()
    assert not analysis_task.tasks_root(config).exists()


def test_an_experiment_written_by_hand_is_read_the_same_way(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The hand-written fixtures the reader's own suite uses stay admissible
    here: an operation that only understood its own writes would be describing a
    different contract from `lineage.py`."""

    write_experiment(
        config.experiments_root,
        EXP_01,
        base_revision=git_rev(config.repo_root, "HEAD"),
        rounds=[experiment_round(1, tasks=[admitted_task("loader-fallback", complete=False)])],
    )

    result = experiments.add_tasks(config, ["hook-side-loader"], now=NOW)

    assert result.experiment_id == EXP_01
    assert [task["draft_id"] for task in record(config, EXP_01)["rounds"][0]["tasks"]] == [
        "loader-fallback",
        "hook-side-loader",
    ]
