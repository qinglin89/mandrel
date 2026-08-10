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
    admitted_task,
    draft_body,
    draft_sha256,
    experiment_round,
    experiment_decision,
    git_commit,
    git_repo,
    git_rev,
    git_unrelated_commit,
    git_update_ref,
    make_repo,
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
from ai_native_deployment.evolution import analysis_task, experiments, lineage, phase, state

BATCH_ID = "evolution-batch-0001"
SECOND_BATCH = "evolution-batch-0002"
EXP_01 = f"{BATCH_ID}-exp-01"
EXP_02 = f"{BATCH_ID}-exp-02"
ANALYSIS_TASK = "2026-07-31-evolution-batch-0001-analysis"

NOW = datetime(2026, 8, 5, 9, 0, 0, tzinfo=timezone.utc)
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
    (`pending`, `0/<total>`, unclaimed) refuses from the frontmatter side."""

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
