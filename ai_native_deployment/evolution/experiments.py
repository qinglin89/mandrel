"""The human admission gate, and the rounds an admitted attempt moves through.

`lineage.py` reads the batch and experiment records; this module is what writes
them. Between analysis and implementation sits one human gate (invariant 9), and
these are the three operations on it:

- **create** — the grouped admission. A person picks the drafts that belong
  together, and one operation creates the experiment's ref at the batch's base
  revision, the experiment record with its first round, and one `.ai-tasks/` copy
  per admitted draft.
- **add-tasks** — further drafts admitted into the round that is open.
- **reject** — a draft turned down, recorded so the gate stops waiting for a
  decision that was already made.

Two more move the round an experiment is working in, and both only append
(contract: Rounds):

- **seal-round** — the open round becomes candidate-ready: every task it
  admitted is observed at `completed` and the ref tip is pinned as that round's
  candidate revision, which is the only revision replay or a promotion may
  afterwards name (invariant 16).
- **revise** — the next round is opened from an already-pinned one, with the
  reason for it. It admits nothing: a revision is decided the moment replay
  reports, and what belongs in the new round is the next question — so the round
  opens empty and `add-tasks` fills it, rather than work staying blocked under a
  round that has already been measured.

Three end things, and each of them is terminal for what it ends (contract:
Terminal decisions, Batch outcome):

- **abandon** — the attempt is dropped, with a reason. Nothing is discarded: the
  record keeps the base, every round, every task selection and every candidate,
  and the batch is free for another alternative.
- **supersede** — the attempt is replaced, and the same operation creates the
  replacement at the batch's base. One operation because only one experiment may
  be open (invariant 14) and a decision cannot name a successor that does not
  exist. The successor opens with an empty round 1, which `add-tasks` fills, for
  the reason `revise` opens one: which proposals answer the new approach is the
  next question, not this one.
- **conclude-no-change** — the batch outcome of invariant 7. It fabricates
  nothing on the way out: no candidate, no experiment, no promotion revision, no
  merge, no deployment. The other way a batch ends is a promotion, which belongs
  to the operation that performs one.

**What makes the operation real.** Each writes several places at once — a Git
ref, a versioned record, `.ai-tasks/` and its index, the audit ledger — and the
order they are written in is the recovery story:

1. the ref, because it is the one thing that must never be created twice or
   recreated later: a clone missing `refs/evolution/*` is the ordinary state, and
   a repair that "restored" the ref at the base would put it behind the real work
   and let the next commit fork the history;
2. the experiment record, which is what makes the admission real — the drafts are
   consumed, the tasks are named, the base is frozen;
3. the task copies and their index rows, derivable from the record and the
   drafts, and therefore restorable;
4. the audit line, which nothing derives state from.

So an interruption can leave a ref with no record (inert; the next create with
the same selection adopts it), or a record whose task copies never landed (the
same command, run again, finishes them). It cannot leave a task in the active
pool that no record accounts for, which is the one direction that matters: an
orphaned active task is work a turn selection will dispatch with nothing behind
it. Redoing the same selection is the resume path throughout — the operations
recognise their own interrupted work rather than requiring a repair by hand.

**Copies, not moves** (contract: Change admission). The draft stays in the batch
that proposed it, and the experiment record holds the draft id, the sha256 of the
bytes admitted, and the task id the copy took. The copy is the draft plus one
`## Admission` section naming the batch, the experiment, the round, the base
revision, and the ref to work on — facts that do not exist when the draft is
written and that the session implementing it cannot proceed without.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from ..hashing import sha256_bytes
from . import analysis_task
from .config import EvolutionConfig
from .errors import BatchError, RefHoldError
from .guards import (
    current_cycle,
    no_open_experiment,
    require_consistent_ref,
    require_no_pending_successor,
    require_open_experiment,
    require_stage_ended,
    settled,
)
from .ledger import append_records, build_record
from .lineage import (
    DECISION_ABANDONED,
    DECISION_PROMOTED,
    DECISION_SUPERSEDED,
    EXPERIMENT_FILENAME,
    EXPERIMENT_SCHEMA_VERSION,
    AdmittedTask,
    BatchLineage,
    Decision,
    Experiment,
    Lineage,
    RefState,
    Round,
    Seal,
    experiment_ref,
    format_experiment_id,
    is_draft_id,
    parse_experiment,
)
from .config import OUTCOME_SCHEMA_FILENAME, REJECTED_DRAFTS_SCHEMA_FILENAME
from .manifests import (
    OUTCOME_NO_CHANGE,
    OUTCOME_SCHEMA_VERSION,
    REJECTED_DRAFTS_SCHEMA_VERSION,
    Batch,
    read_rejected_drafts,
)
from .revisions import create_ref, held_at, ref_tip, release_ref, resolve_commit
from .schema import format_rfc3339, load_schema, validate_or_raise
from .state import atomic_write_text, single_writer_lock

RECORD_EXPERIMENT_CREATED = "experiment-created"
RECORD_TASKS_ADMITTED = "tasks-admitted"
RECORD_DRAFT_REJECTED = "draft-rejected"
RECORD_ROUND_SEALED = "round-sealed"
RECORD_EXPERIMENT_REVISED = "experiment-revised"
RECORD_EXPERIMENT_ABANDONED = "experiment-abandoned"
RECORD_EXPERIMENT_SUPERSEDED = "experiment-superseded"
RECORD_BATCH_CONCLUDED = "batch-concluded"

DRAFT_SUFFIX = ".md"

# The taskfile schema's `id: <date-prefixed-slug>`, which is also the file name
# the copy takes — so this is the containment check as well as the shape one: one
# path segment, no traversal, no dot-file, no extension to confuse for one.
_TASK_ID = re.compile(r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9]+(-[a-z0-9]+)*\Z", re.ASCII)

# What an inert proposal looks like. A draft carrying anything else has been
# worked on where nothing dispatches it.
DRAFT_STATUS = "pending"
# `session-est: 0/<total>`: a dev session increments the current count as part of
# its claim (taskfile schema §4), so a draft nobody has worked on is still at 0
# — and a total of zero sessions is an estimate for no work at all.
_UNCONSUMED_SESSION_EST = re.compile(r"\A0/[1-9][0-9]*\Z", re.ASCII)
EMPTY_BLOCKERS = "[]"
ADMISSION_HEADING = "## Admission"
SESSION_LOG_HEADING = "## Session log"

# The body a task file carries: what the work is, what it covers, how it is
# recognised as done, and where it records itself. The intake contract writes all
# four for a pending task, and the dev and review contracts each read one of them
# — an admitted copy joins the same pool and is worked by the same sessions.
REQUIRED_SECTIONS = ("## Goal", "## Scope", "## Acceptance", SESSION_LOG_HEADING)

# Bound on the index-row summary lifted out of the draft's own title. The active
# index is a list, not a description.
_MAX_SUMMARY = 96


@dataclass(frozen=True)
class Admitted:
    """One draft's admission, as the operation carried it out."""

    draft_id: str
    task_id: str
    draft_sha256: str
    task_path: Path
    # True when this run wrote the copy to finish an admission that was already
    # recorded, rather than admitting the draft now.
    restored: bool = False


@dataclass(frozen=True)
class AdmissionResult:
    batch_id: str
    experiment_id: str
    round_number: int
    base_revision: str
    ref: str
    admitted: tuple[Admitted, ...]
    # False when the experiment already existed: `add-tasks`, or the same
    # selection run again after an interruption.
    created: bool

    @property
    def restored(self) -> tuple[str, ...]:
        """Task ids this run wrote to finish work an earlier run recorded."""

        return tuple(item.task_id for item in self.admitted if item.restored)


@dataclass(frozen=True)
class RejectionResult:
    batch_id: str
    declined: tuple[str, ...]
    record_path: Path
    # False when this run wrote nothing because the decision was already on
    # record: the same selection redone after an interruption. The drafts are
    # declined either way, and only the caller reporting it needs the difference.
    recorded: bool = True


@dataclass(frozen=True)
class SealResult:
    """A round made candidate-ready: what was pinned, and what was observed."""

    batch_id: str
    experiment_id: str
    round_number: int
    candidate_revision: str
    sealed_at: str
    # Tasks this run observed complete. Empty when every one of them had already
    # been observed by an earlier run whose seal did not land.
    observed: tuple[str, ...] = ()
    # False when the round was already candidate-ready: the record is what makes
    # the seal real, so this reports the pin that is on record rather than
    # writing a second one.
    sealed: bool = True


@dataclass(frozen=True)
class DecisionResult:
    """An attempt turned into history, and the successor a supersession created."""

    batch_id: str
    experiment_id: str
    outcome: str
    reason: str
    decided_at: str
    # The round the attempt ended in — open or candidate-ready, since an attempt
    # dropped before it produced anything records no candidate (invariant 7).
    round_number: int
    # The replacement created at the batch's base in the same operation. None for
    # an abandonment, which ends the attempt without one.
    successor_id: str | None = None
    successor_ref: str | None = None
    # False when the decision was already on record: the same decision redone
    # after an interruption. The successor is reported separately, because the
    # interruption that costs a supersession its successor leaves exactly the
    # state where one is False and the other True.
    recorded: bool = True
    successor_created: bool = False


@dataclass(frozen=True)
class ConclusionResult:
    """The batch outcome that ends a change cycle, and what it recorded."""

    batch_id: str
    outcome: str
    reason: str
    decided_at: str
    record_path: Path
    # False when the conclusion was already on record — the same one redone after
    # an interruption.
    recorded: bool = True


@dataclass(frozen=True)
class ReviseResult:
    """The next round, opened from the candidate the previous one pinned."""

    batch_id: str
    experiment_id: str
    round_number: int
    reason: str
    # The candidate revision this round revises: what the previous round pinned,
    # and what its evidence goes on describing.
    revised_from: str
    # False when the round was already open on record — the same revision redone
    # after an interruption.
    opened: bool = True


def create(
    config: EvolutionConfig,
    draft_ids: Iterable[str],
    *,
    base: str | None = None,
    reason: str | None = None,
    now: datetime | None = None,
) -> AdmissionResult:
    """Admit a group of drafts as a new experiment on the current batch.

    The first experiment of a batch freezes that batch's base revision — not the
    batch freeze, which happens before anyone knows a change is warranted
    (invariant 15). `base` defaults to `HEAD`, which is the source commit the
    operator is starting the work from; every later experiment of the same batch
    is created from the commit the first one froze, and a `base` naming anything
    else is refused rather than reconciled.

    Running it again with the same selection is the resume path, not a second
    admission: it finishes whatever the interrupted run left and reports what it
    completed.
    """

    moment = _moment(now)
    with single_writer_lock(config):
        current = current_cycle(config, now=moment)
        requested = _requested(draft_ids)
        batch = current.batch

        open_experiment = current.open_experiment
        if open_experiment is not None:
            # Both before the redo, not after it: a resumed admission still has a
            # base, and an operator naming a different one is asking for something
            # this is not about to do — and a redo writes, so it is as guarded as
            # the admission it finishes. The copies it writes tell their sessions
            # to commit on a ref, and a ref standing off the history the record
            # pins is not one to send work to.
            _require_requested_base(config, current, open_experiment.base_revision, base)
            require_consistent_ref(current)
            return _redo_create(config, current, open_experiment, requested)

        base_revision, base_release_ref = _base_revision(config, current, base)
        experiment_id = format_experiment_id(batch.batch_id, len(current.experiments) + 1)
        drafts = _collect(config, current, requested)
        stamp = format_rfc3339(moment)

        experiment = Experiment(
            experiment_id=experiment_id,
            batch_id=batch.batch_id,
            created_at=stamp,
            base_revision=base_revision,
            base_release_ref=base_release_ref,
            ref=experiment_ref(experiment_id),
            rounds=(
                Round(
                    number=1,
                    opened_at=stamp,
                    reason=(reason or "").strip() or _admission_reason(requested),
                    tasks=tuple(_admitted(draft, admitted_at=stamp) for draft in drafts),
                    seal=None,
                ),
            ),
            decision=None,
            directory=config.experiments_root / experiment_id,
        )

        _create_experiment_ref(config, experiment)
        _publish_record(config, experiment)
        written = _write_tasks(config, current, experiment, experiment.rounds[0], drafts)
        append_records(
            config,
            [
                build_record(
                    RECORD_EXPERIMENT_CREATED,
                    recorded_at=stamp,
                    batch_id=batch.batch_id,
                    experiment_id=experiment_id,
                    revision=base_revision,
                ),
                *_admission_records(batch, experiment, experiment.rounds[0], written, recorded_at=stamp),
            ],
        )
        return _result(batch, experiment, experiment.rounds[0], written, created=True)


def add_tasks(
    config: EvolutionConfig,
    draft_ids: Iterable[str],
    *,
    now: datetime | None = None,
) -> AdmissionResult:
    """Admit further drafts into the open experiment's open round.

    A round takes work only while it is open. Once it is candidate-ready its
    candidate is pinned and its evidence names it, so admitting into it would
    change what that evidence measured after the fact — `revise` opens the next
    round instead (invariant 16).
    """

    moment = _moment(now)
    with single_writer_lock(config):
        current = current_cycle(config, now=moment)
        requested = _requested(draft_ids)
        batch = current.batch

        experiment = current.open_experiment
        if experiment is None:
            raise BatchError(
                f"{batch.batch_id} has no open experiment to admit into; a grouped admission creates one from the "
                "drafts it selects, and every experiment of this batch starts from the base its first one froze"
            )
        round_ = _open_round(experiment)
        require_consistent_ref(current)
        admitted = {task.draft_id: task for task in round_.tasks}
        already = sorted(requested & set(admitted))
        if already:
            if set(already) == requested:
                # The same selection again: the record is what made the admission
                # real, so what is left of it is the copies.
                return _finish(
                    config,
                    current,
                    experiment,
                    round_,
                    tuple(admitted[draft_id] for draft_id in sorted(requested)),
                )
            raise BatchError(
                f"draft(s) {already} are already admitted into round {round_.number} of "
                f"{experiment.experiment_id}; a draft is consumed once, so redo the same selection to finish an "
                "interrupted admission, or admit only the drafts that are still waiting"
            )

        drafts = _collect(config, current, requested)
        stamp = format_rfc3339(moment)
        opened = replace(
            round_,
            tasks=round_.tasks + tuple(_admitted(draft, admitted_at=stamp) for draft in drafts),
        )
        updated = replace(experiment, rounds=experiment.rounds[:-1] + (opened,))

        _write_record(config, updated)
        written = _write_tasks(config, current, updated, opened, drafts)
        append_records(config, _admission_records(batch, updated, opened, written, recorded_at=stamp))
        return _result(batch, updated, opened, written, created=False)


def reject(
    config: EvolutionConfig,
    draft_ids: Iterable[str],
    *,
    reason: str,
    now: datetime | None = None,
) -> RejectionResult:
    """Decline drafts at the admission gate, with the reason.

    Recorded rather than deleted. Whether a draft is still waiting is derived
    from what took it or turned it down, so without this record a declined
    proposal waits forever — and deleting the file instead would leave "why is
    this gone" a question only `git log` answers. Declining is terminal for the
    proposal: re-proposing the idea means a new draft id.
    """

    moment = _moment(now)
    text = _reason(
        reason,
        "declining a draft records why; the reason travels with the batch and is what keeps a rejected "
        "proposal from being re-argued from memory",
    )

    with single_writer_lock(config):
        current = current_cycle(config, now=moment)
        batch = current.batch
        requested = _requested(draft_ids)
        recorded = {entry["draft_id"]: entry for entry in read_rejected_drafts(config, batch)}
        already = sorted(requested & set(recorded))
        if already:
            return _redo_reject(batch, requested, already, recorded, text)

        drafts = _collect(config, current, requested, for_tasks=False)
        stamp = format_rfc3339(moment)

        record = {
            "schema_version": REJECTED_DRAFTS_SCHEMA_VERSION,
            "batch_id": batch.batch_id,
            "rejected": [
                *(dict(entry) for entry in recorded.values()),
                *(
                    {
                        "draft_id": draft.draft_id,
                        "draft_sha256": draft.sha256,
                        "rejected_at": stamp,
                        "reason": text,
                    }
                    for draft in drafts
                ),
            ],
        }
        validate_or_raise(
            record,
            load_schema(config.schema_path(REJECTED_DRAFTS_SCHEMA_FILENAME)),
            description=f"rejected-drafts record for {batch.batch_id}",
        )
        atomic_write_text(batch.rejected_drafts_path, _json(record))

        append_records(
            config,
            [
                build_record(
                    RECORD_DRAFT_REJECTED,
                    recorded_at=stamp,
                    batch_id=batch.batch_id,
                    draft_id=draft.draft_id,
                )
                for draft in drafts
            ],
        )
        return RejectionResult(
            batch_id=batch.batch_id,
            declined=tuple(draft.draft_id for draft in drafts),
            record_path=batch.rejected_drafts_path,
        )


def _redo_reject(
    batch: Batch,
    requested: set[str],
    already: list[str],
    recorded: Mapping[str, Mapping[str, Any]],
    reason: str,
) -> RejectionResult:
    """The same rejection run again, or a refusal naming the decision on record.

    The record is what makes a rejection real — the audit line after it is not —
    so this exact selection, declined for this exact reason, *is* this operation,
    already done. Saying so is what makes the operation redoable at all: the run
    that published the record and then failed would otherwise have left its own
    retry permanently refused by the work it had already finished.

    Nothing is written, the ledger included. The interruption cost those lines,
    and re-appending them would claim a second decision about a proposal that was
    declined once — the rule a redone admission already follows.

    Anything else is a different decision about a spent proposal: declining is
    terminal, a second reason does not replace the one on record, and re-proposing
    the idea means a new draft id.
    """

    if set(already) != requested:
        raise BatchError(
            f"draft(s) {already} were already declined at {batch.batch_id}'s gate; declining is terminal for a "
            "proposal, so redo the same selection to finish an interrupted rejection, or decline only the drafts "
            "still waiting"
        )
    differing = sorted(draft_id for draft_id in already if recorded[draft_id]["reason"] != reason)
    if differing:
        raise BatchError(
            f"draft(s) {differing} were declined at {batch.batch_id}'s gate for a different reason "
            f"({recorded[differing[0]]['reason']!r}); declining is terminal for a proposal, so the reason on "
            "record stands — re-proposing the idea means a new draft id whose own bytes say what changed"
        )
    return RejectionResult(
        batch_id=batch.batch_id,
        declined=tuple(sorted(requested)),
        record_path=batch.rejected_drafts_path,
        recorded=False,
    )


# --- rounds ------------------------------------------------------------------


def seal_round(config: EvolutionConfig, *, now: datetime | None = None) -> SealResult:
    """Make the open round candidate-ready: observe its tasks, pin its candidate.

    Invariant 16. A round is measured only once every task admitted into it has
    been observed at `completed` and the ref tip is pinned as that round's
    candidate revision — after which replay, and any terminal decision, name that
    pinned revision rather than a tip that is still free to move.

    The observation is recorded because it is the only durable form of the fact:
    `.ai-tasks/` is machine-local and close-out archives a finished task away, so
    on another clone the task's own status is unreadable rather than merely
    absent. Sealing therefore happens on the machine that holds the tasks.

    Run again after an interrupted seal, it reports the pin already on record
    rather than writing a second one — the record is what makes the seal real,
    and the audit line it may have cost is not re-appended (the rule a redone
    rejection already follows).
    """

    moment = _moment(now)
    with single_writer_lock(config):
        current = current_cycle(config, now=moment)
        experiment = require_open_experiment(current, "seal")
        # Before the already-sealed shortcut, not after it: a ref that has moved
        # past a candidate-ready round is exactly the state this refuses, and
        # reporting the pin as though nothing had happened is what would hide it.
        require_consistent_ref(current)

        round_ = experiment.last_round
        if round_.seal is not None:
            return SealResult(
                batch_id=current.batch_id,
                experiment_id=experiment.experiment_id,
                round_number=round_.number,
                candidate_revision=round_.seal.candidate_revision,
                sealed_at=round_.seal.sealed_at,
                sealed=False,
            )
        if not round_.tasks:
            raise BatchError(
                f"round {round_.number} of {experiment.experiment_id} has admitted nothing; a round is the task "
                "set admitted into it and the candidate that set produced, so sealing an empty one would pin a "
                "revision pass no proposal accounts for — admit the drafts this round needs, or end the attempt "
                "with a decision"
            )

        stamp = format_rfc3339(moment)
        candidate = _pinnable_tip(experiment, current.ref)
        # Every ref question this seal depends on is answered before the hold,
        # and the pin is what the hold is taken at: what was checked is then what
        # is still there when the record naming it lands.
        with _unmoved(config, experiment, candidate, "a seal is decided from where that ref stood and pins it"):
            tasks, observed = _observe_completions(config, current, experiment, round_, observed_at=stamp)
            sealed = replace(round_, tasks=tasks, seal=Seal(sealed_at=stamp, candidate_revision=candidate))
            updated = replace(experiment, rounds=experiment.rounds[:-1] + (sealed,))

            _write_record(config, updated)
            append_records(
                config,
                [
                    build_record(
                        RECORD_ROUND_SEALED,
                        recorded_at=stamp,
                        batch_id=current.batch_id,
                        experiment_id=experiment.experiment_id,
                        round=round_.number,
                        revision=candidate,
                    )
                ],
            )
        return SealResult(
            batch_id=current.batch_id,
            experiment_id=experiment.experiment_id,
            round_number=round_.number,
            candidate_revision=candidate,
            sealed_at=stamp,
            observed=observed,
        )


def revise(config: EvolutionConfig, *, reason: str, now: datetime | None = None) -> ReviseResult:
    """Open the next round of the open experiment, from an already-pinned one.

    What makes the previous round's evidence stale is this record rather than
    anyone remembering to invalidate it: the old evidence goes on naming the
    round it measured, whose candidate revision was pinned before that evidence
    existed and has not moved since, and the new round has none of its own until
    it is sealed.

    The round opens empty. A revision is decided the moment replay reports, and
    the proposals it needs may not be written yet — while the last round is
    candidate-ready the ref stays where it was pinned, so waiting for those
    drafts before opening the round is waiting with the work blocked. `add-tasks`
    admits them afterwards.
    """

    moment = _moment(now)
    text = _reason(
        reason,
        "revising records why; the reason is what the next round's evidence is read against, and a revision "
        "with none says only that somebody was dissatisfied",
    )

    with single_writer_lock(config):
        current = current_cycle(config, now=moment)
        experiment = require_open_experiment(current, "revise")
        require_consistent_ref(current)

        last = experiment.last_round
        if last.seal is None:
            return _redo_revise(experiment, last, text)

        stamp = format_rfc3339(moment)
        opened = Round(number=last.number + 1, opened_at=stamp, reason=text, tasks=(), seal=None)
        updated = replace(experiment, rounds=experiment.rounds + (opened,))

        # Held where the check above found it, which for a candidate-ready round
        # is the pinned revision itself — or nothing, in a checkout without the
        # namespace. A commit arriving between the two would be adopted as the
        # new round's work, and a commit made under a round that was already
        # measured is the one thing this record must not be able to absorb.
        with _unmoved(
            config,
            experiment,
            current.ref.tip if current.ref is not None else None,
            "a revision is decided from where that ref stood and opens a round over it",
        ):
            _write_record(config, updated)
            append_records(
                config,
                [
                    build_record(
                        RECORD_EXPERIMENT_REVISED,
                        recorded_at=stamp,
                        batch_id=current.batch_id,
                        experiment_id=experiment.experiment_id,
                        round=opened.number,
                        revision=last.seal.candidate_revision,
                    )
                ],
            )
        return ReviseResult(
            batch_id=current.batch_id,
            experiment_id=experiment.experiment_id,
            round_number=opened.number,
            reason=text,
            revised_from=last.seal.candidate_revision,
        )


def _redo_revise(experiment: Experiment, last: Round, reason: str) -> ReviseResult:
    """The same revision run again, or a refusal naming the round that is open.

    A round this revision opened and nothing has been admitted into yet *is* this
    operation, already recorded — the record is what makes it real, and the audit
    line an interruption cost is not re-appended. Anything else is a round with
    work in it, or a second reason for one that already exists: both are answered
    by what is on record rather than by opening another.
    """

    previous = experiment.rounds[-2] if len(experiment.rounds) > 1 else None
    pinned = previous.candidate_revision if previous is not None else None
    if pinned is not None and not last.tasks:
        if last.reason == reason:
            return ReviseResult(
                batch_id=experiment.batch_id,
                experiment_id=experiment.experiment_id,
                round_number=last.number,
                reason=last.reason,
                revised_from=pinned,
                opened=False,
            )
        raise BatchError(
            f"round {last.number} of {experiment.experiment_id} was already opened for {last.reason!r}; a round "
            f"is opened once, so redo the same revision to finish an interrupted one — {reason!r} would be a "
            "second reason for a round that already exists, and what goes into that round is an admission"
        )
    raise BatchError(
        f"round {last.number} of {experiment.experiment_id} is still open; a revision appends to a round whose "
        "candidate is already pinned, so seal this one first — the previous round's evidence is what a revision "
        "makes stale, and a round nothing measured leaves the next one revising nothing (invariant 16)"
    )


# --- terminal decisions ------------------------------------------------------


def abandon(
    config: EvolutionConfig,
    *,
    reason: str,
    experiment_id: str | None = None,
    now: datetime | None = None,
) -> DecisionResult:
    """End the open experiment, without replacing it.

    Nothing is discarded: the record keeps the base, every round, every task
    selection and every candidate revision, and the ref keeps those trees
    reachable. A batch carrying three abandoned experiments is history, not
    damage — and history blocks nothing, so the batch is free for another
    alternative (invariant 14).

    The attempt may be abandoned from an open round as well as a
    candidate-ready one. An attempt dropped before it produced anything records
    no candidate rather than having one invented for it, which is invariant 7's
    rule applied to an experiment.

    `experiment_id` names the attempt this decision is about (see `_end_attempt`).
    """

    return _end_attempt(config, DECISION_ABANDONED, reason, experiment_id=experiment_id, now=now)


def supersede(
    config: EvolutionConfig,
    *,
    reason: str,
    experiment_id: str | None = None,
    now: datetime | None = None,
) -> DecisionResult:
    """Replace the open experiment with a fresh attempt at the same change.

    One operation rather than two, because only one experiment may be open
    (invariant 14) and a decision cannot name a successor that does not exist
    yet. The successor is therefore the next id in the series, created here, and
    it starts from the batch's base — never from the tip it replaces, or the
    alternative would inherit exactly what was being replaced.

    Its round 1 opens empty and `add-tasks` fills it, for the reason a revised
    round opens empty: which proposals answer the new approach is the next
    question, and a successor that cannot exist until they are written is an
    attempt that cannot be started when it is decided.

    `experiment_id` names the attempt this decision is about (see `_end_attempt`),
    which is what tells a supersession redone from an untouched successor
    superseded in its turn.
    """

    return _end_attempt(config, DECISION_SUPERSEDED, reason, experiment_id=experiment_id, now=now)


def _end_attempt(
    config: EvolutionConfig,
    outcome: str,
    reason: str,
    *,
    experiment_id: str | None,
    now: datetime | None,
) -> DecisionResult:
    """Record a terminal decision on the open experiment, and whatever it creates.

    Both decisions share everything but the successor, so they share the guards
    too. The ref check is one of them, and it is here for a reason particular to
    ending an attempt: a ref standing off the history its record pins is
    reported only for the *open* experiment, so a decision recorded over one
    retires the finding along with the attempt. What can no longer be seen can
    no longer be resolved, and the revisions that record pins would quietly stop
    being reachable. That is also why the decision is written while the ref is
    held where the check found it: the disagreement this refuses is one a commit
    arriving a moment later would make permanent.

    `experiment_id` is optional and names the attempt this decision is about. Two
    readings otherwise collide, and only in one shape: an untouched successor
    standing open under a supersession recorded for the very same reason is both
    "that supersession, redone" and "supersede this successor in its turn". Left
    unnamed, it is read as the redo, which writes nothing — the safe direction.
    Named, it is exactly what it says, so both are expressible: a human reason is
    evidence, not the identity of an operation. It is a precondition wherever it
    is given, so a request built against a lineage that has since moved on
    refuses instead of ending an attempt nobody was looking at.
    """

    moment = _moment(now)
    text = _reason(
        reason,
        f"a decision that an attempt is {outcome} records why; the reason is what a later reader has instead of "
        "the conversation that produced it, and an attempt that ended for no stated reason is one the next "
        "alternative cannot be built to avoid",
    )

    with single_writer_lock(config):
        # A supersession finishes its own interrupted run, so it is the one
        # operation that may act on a batch owing a successor.
        current = current_cycle(config, now=moment, finishing=outcome == DECISION_SUPERSEDED)
        named = _named_attempt(current, experiment_id)
        experiment = current.open_experiment
        if experiment is None:
            _require_named_ending(current, named)
            if outcome == DECISION_SUPERSEDED and current.pending_successor is not None:
                return _finish_supersession(config, current, text, now=moment)
            return _redo_decision(current, outcome, text)
        # Before the redo report, not after it: a moved ref is a fact about the
        # repository whichever request brought the operator here, and answering
        # "already done" is what would hide it — the ordering a grouped
        # admission and a seal already follow.
        require_consistent_ref(current)
        if named is not None and not named.open:
            # A decision explicitly about an attempt that has already ended can
            # only be the supersession that created the one now open, redone.
            return _require_redone_supersession(current, experiment, named, outcome, text)
        if outcome == DECISION_SUPERSEDED and named is None:
            redone = _superseded_already(current, experiment, text)
            if redone is not None:
                return redone

        stamp = format_rfc3339(moment)
        # Held from here until the last record lands, at the tip the check above
        # was answered for. After this decision nothing describes that ref again.
        with _unmoved(
            config,
            experiment,
            current.ref.tip if current.ref is not None else None,
            "a terminal decision is the last reading anyone takes of that ref, and it is taken on where the ref "
            "stood",
        ):
            successor = (
                _successor(config, current, experiment, reason=text, created_at=stamp)
                if outcome == DECISION_SUPERSEDED
                else None
            )
            if successor is not None:
                # The ref first, as everywhere here: it is the one thing that must
                # never be created twice or restored later, and a ref standing at
                # the base with no record yet is inert and adoptable. It is a ref
                # of its own, so the hold on the ending attempt's does not cover
                # it and does not stand in its way.
                _create_experiment_ref(config, successor)
            _decide(config, experiment, outcome, text, decided_at=stamp, successor=successor)
            if successor is not None:
                # After the decision, never before it. The other order leaves two
                # open experiments if it is interrupted, which no reading can
                # arbitrate; this one leaves a successor that is merely owed,
                # which the same operation redone finishes.
                _publish_record(config, successor)

            append_records(config, _decision_records(current, experiment, successor, outcome, recorded_at=stamp))
        return _decided(current, experiment, outcome, text, stamp, successor=successor, created=True)


def _named_attempt(current: BatchLineage, experiment_id: str | None) -> Experiment | None:
    """The experiment a request named, or None when it named none.

    An id this batch does not have is refused rather than ignored: it is a
    request about a lineage other than the one in front of it — another batch's
    attempt, a mistyped ordinal, or an experiment whose record is gone — and
    silently acting on whatever is open is how the wrong attempt gets ended.
    """

    if experiment_id is None:
        return None
    for experiment in current.experiments:
        if experiment.experiment_id == experiment_id:
            return experiment
    known = [experiment.experiment_id for experiment in current.experiments]
    raise BatchError(
        f"{current.batch_id} has no experiment {experiment_id!r}; its attempts are {known or 'none yet'} — a "
        "decision names the attempt it ends, and an id this batch never allocated names none of them"
    )


def _require_named_ending(current: BatchLineage, named: Experiment | None) -> None:
    """A decision named while nothing is open is about the attempt that ended.

    Which is the newest one, always: ordinals run 1..N and only the newest may be
    open, so with none open the last is what any decision here can be finishing.
    """

    if named is None:
        return
    last = current.experiments[-1] if current.experiments else None
    if last is not None and named.experiment_id == last.experiment_id:
        return
    raise BatchError(
        f"{named.experiment_id} is not the attempt {current.batch_id} last ended"
        + (f" ({last.experiment_id})" if last is not None else "")
        + "; a terminal decision is recorded once and never edited, so what a decision naming an earlier attempt "
        "would say is already on record"
    )


def _require_redone_supersession(
    current: BatchLineage,
    experiment: Experiment,
    named: Experiment,
    outcome: str,
    reason: str,
) -> DecisionResult:
    """The supersession that created the open attempt, named and redone.

    Naming an attempt that has already ended asks for one thing only: to finish
    the decision that ended it. So it holds to exactly the shape a redo has —
    this outcome, this reason, and a successor that is the experiment now open —
    and refuses anything else rather than quietly ending the open attempt in its
    place, which is the mistake naming a target exists to make impossible.

    Whether anything has been admitted into that successor since is not part of
    the shape here, though it is for the unnamed reading: what makes the request
    a redo is that it names the attempt already decided, and work done in the
    replacement neither completes nor undoes the decision that created it.
    """

    decision = named.decision
    if (
        outcome == DECISION_SUPERSEDED
        and decision is not None
        and decision.outcome == DECISION_SUPERSEDED
        and decision.superseded_by == experiment.experiment_id
        and decision.reason == reason
    ):
        return _supersession_on_record(current, named, decision, experiment)
    recorded = f"{decision.outcome!r} ({decision.reason!r})" if decision is not None else "no decision"
    raise BatchError(
        f"{named.experiment_id} already ended as {recorded}, and {experiment.experiment_id} is open; a decision "
        f"is recorded once and never edited, so naming {named.experiment_id} asks to finish that one — to end "
        f"the attempt that is open, name {experiment.experiment_id}"
    )


def _successor(
    config: EvolutionConfig,
    current: BatchLineage,
    experiment: Experiment,
    *,
    reason: str,
    created_at: str,
) -> Experiment:
    """The replacement a supersession creates: next id, same base, empty round 1.

    The base comes from the batch rather than from the attempt being replaced —
    the same commit every experiment of this batch starts from (invariant 15),
    which is also what `_base_revision` refuses to resolve when this checkout no
    longer holds it.
    """

    successor_id = format_experiment_id(current.batch_id, experiment.ordinal + 1)
    base_revision, base_release_ref = _base_revision(config, current, None)
    return Experiment(
        experiment_id=successor_id,
        batch_id=current.batch_id,
        created_at=created_at,
        base_revision=base_revision,
        base_release_ref=base_release_ref,
        ref=experiment_ref(successor_id),
        rounds=(Round(number=1, opened_at=created_at, reason=reason, tasks=(), seal=None),),
        decision=None,
        directory=config.experiments_root / successor_id,
    )


def _decide(
    config: EvolutionConfig,
    experiment: Experiment,
    outcome: str,
    reason: str,
    *,
    decided_at: str,
    successor: Experiment | None,
) -> Experiment:
    """Write the terminal decision onto an experiment's record.

    It states none of the rules about which decision is available when, and that
    is deliberate: the record is published through the reader's own parse, so
    `promoted` from a round nobody sealed, a successor that is not the next
    ordinal, and a field paired with the wrong outcome are all refused by the
    same code that refuses them on the way back in. A promotion has no operation
    in this controller yet; when one lands it inherits those rules here rather
    than restating them.
    """

    decided = replace(
        experiment,
        decision=Decision(
            outcome=outcome,
            decided_at=decided_at,
            reason=reason,
            superseded_by=successor.experiment_id if successor is not None else None,
            promotion_revision=None,
        ),
    )
    _write_record(config, decided)
    return decided


def _finish_supersession(
    config: EvolutionConfig,
    current: BatchLineage,
    reason: str,
    *,
    now: datetime,
) -> DecisionResult:
    """Create the successor a recorded supersession still owes.

    The decision is what made the supersession real, and it landed; what did not
    is the experiment it names. So the same supersession redone writes the ref
    and the record that are missing and nothing else — no second decision, and
    no ledger line, since the interruption cost the audit and re-appending would
    claim two supersessions where one happened.

    A different reason is a different decision about an attempt that already
    ended, so it is refused naming the one on record — the rule every redo here
    follows.
    """

    superseded = current.experiments[-1]
    decision = superseded.decision
    if decision is None or decision.reason != reason:
        raise BatchError(
            f"{superseded.experiment_id} was superseded for "
            f"{(decision.reason if decision else '')!r}, and {current.pending_successor} was never created; a "
            f"decision is recorded once, so redo that supersession for the same reason to finish it — {reason!r} "
            "would be a second decision about an attempt that has already ended"
        )

    successor = _successor(config, current, superseded, reason=reason, created_at=format_rfc3339(now))
    _create_experiment_ref(config, successor)
    _publish_record(config, successor)
    return _decided(
        current,
        superseded,
        decision.outcome,
        decision.reason,
        decision.decided_at,
        successor=successor,
        created=True,
        recorded=False,
    )


def _superseded_already(
    current: BatchLineage,
    experiment: Experiment,
    reason: str,
) -> DecisionResult | None:
    """This supersession, already done — or None, meaning it has not been.

    The reading a request that named no attempt gets. The completed shape is
    exact: the attempt before this one ended as `superseded` for this reason and
    named this experiment, and this experiment is still the empty round 1 that
    supersession opened. Anything else is a new decision about the attempt that
    is open, including superseding a successor that has been worked in, where
    nothing about the request is ambiguous.

    One shape answers to both readings — an untouched successor superseded for
    the very words its own creation recorded — and unnamed it is read as the
    redo, which writes nothing. That is the safe direction to be wrong in, and
    it is not the only expressible one: naming the attempt says which was meant
    (`_end_attempt`), since a human reason is evidence rather than the identity
    of an operation.
    """

    if experiment.ordinal < 2:
        return None
    previous = current.experiments[-2]
    decision = previous.decision
    if decision is None or decision.outcome != DECISION_SUPERSEDED:
        return None
    if decision.superseded_by != experiment.experiment_id or decision.reason != reason:
        return None
    round_ = experiment.last_round
    if len(experiment.rounds) > 1 or round_.tasks or round_.seal is not None:
        return None
    return _supersession_on_record(current, previous, decision, experiment)


def _supersession_on_record(
    current: BatchLineage,
    superseded: Experiment,
    decision: Decision,
    successor: Experiment,
) -> DecisionResult:
    """A supersession that is complete, reported from what is written down.

    Nothing is written and nothing is re-appended: the decision and the successor
    are both on record, and the audit line an interruption may have cost is not
    a second supersession's to claim.
    """

    return _decided(
        current,
        superseded,
        decision.outcome,
        decision.reason,
        decision.decided_at,
        successor=successor,
        created=False,
        recorded=False,
    )


def _redo_decision(current: BatchLineage, outcome: str, reason: str) -> DecisionResult:
    """The same decision run again, or a refusal naming what ended the attempt.

    A decision whose record landed and whose audit line did not is this
    operation, already done — the record is what makes it real. Anything else is
    a second decision about an attempt that is already history, and a terminal
    decision is never edited: what it says is what a later reader has.
    """

    last = current.experiments[-1] if current.experiments else None
    decision = last.decision if last is not None else None
    if last is None or decision is None:
        raise no_open_experiment(current, "end")
    if decision.outcome != outcome or decision.reason != reason:
        raise BatchError(
            f"{last.experiment_id} already ended as {decision.outcome!r} ({decision.reason!r}); a decision is "
            "recorded once and never edited, so redo the same one to finish an interrupted decision — what "
            "continues this batch is the next attempt"
        )
    return _decided(current, last, decision.outcome, decision.reason, decision.decided_at, recorded=False)


def _decided(
    current: BatchLineage,
    experiment: Experiment,
    outcome: str,
    reason: str,
    decided_at: str,
    *,
    successor: Experiment | None = None,
    created: bool = False,
    recorded: bool = True,
) -> DecisionResult:
    return DecisionResult(
        batch_id=current.batch_id,
        experiment_id=experiment.experiment_id,
        outcome=outcome,
        reason=reason,
        decided_at=decided_at,
        round_number=experiment.last_round.number,
        successor_id=successor.experiment_id if successor is not None else None,
        successor_ref=successor.ref if successor is not None else None,
        recorded=recorded,
        successor_created=created,
    )


def _decision_records(
    current: BatchLineage,
    experiment: Experiment,
    successor: Experiment | None,
    outcome: str,
    *,
    recorded_at: str,
) -> list[dict[str, Any]]:
    records = [
        build_record(
            RECORD_EXPERIMENT_SUPERSEDED if outcome == DECISION_SUPERSEDED else RECORD_EXPERIMENT_ABANDONED,
            recorded_at=recorded_at,
            batch_id=current.batch_id,
            experiment_id=experiment.experiment_id,
            round=experiment.last_round.number,
        )
    ]
    if successor is not None:
        records.append(
            build_record(
                RECORD_EXPERIMENT_CREATED,
                recorded_at=recorded_at,
                batch_id=current.batch_id,
                experiment_id=successor.experiment_id,
                revision=successor.base_revision,
            )
        )
    return records


def conclude_no_change(
    config: EvolutionConfig,
    *,
    reason: str,
    now: datetime | None = None,
) -> ConclusionResult:
    """End the current batch having changed nothing (invariant 7).

    A valid conclusion, and the common one: the evidence justified no protocol,
    memory, orchestrator, or evaluator change, or every attempt at one was
    dropped. It fabricates nothing on the way out — no candidate, no experiment,
    no promotion revision, no merge, no deployment — and the record carries the
    reason and nothing else.

    This is the record that releases the next cohort, so what it may be written
    over is exactly the state where nothing is left to do: the analysis stage
    ended, no experiment is open, no proposal is still waiting for a decision,
    and no attempt records a promotion. Those four are the phase `status` calls
    `conclusion-pending`, which is the point — an operator reading that a batch
    is waiting for its conclusion is reading the condition of this operation.

    The other way a batch ends is a promotion, whose outcome record names the
    experiment and the source-line revision it carried. That belongs to the
    operation that performs the promotion.
    """

    moment = _moment(now)
    text = _reason(
        reason,
        "concluding records why; a batch that ended is read afterwards only through what it wrote, and "
        "'no change' without a reason says nothing about what the evidence showed",
    )

    with single_writer_lock(config):
        lineage = settled(config, now=moment)
        current = lineage.current
        if current is None:
            return _redo_conclusion(lineage, text)
        require_stage_ended(config, current)
        require_no_pending_successor(current)
        _require_nothing_outstanding(current)

        stamp = format_rfc3339(moment)
        record = {
            "schema_version": OUTCOME_SCHEMA_VERSION,
            "batch_id": current.batch_id,
            "outcome": OUTCOME_NO_CHANGE,
            "decided_at": stamp,
            "reason": text,
            "experiment_id": None,
            "promotion_revision": None,
        }
        validate_or_raise(
            record,
            load_schema(config.schema_path(OUTCOME_SCHEMA_FILENAME)),
            description=f"batch outcome record for {current.batch_id}",
        )
        atomic_write_text(current.batch.outcome_path, _json(record))
        append_records(
            config,
            [
                build_record(
                    RECORD_BATCH_CONCLUDED,
                    recorded_at=stamp,
                    batch_id=current.batch_id,
                    detail=OUTCOME_NO_CHANGE,
                )
            ],
        )
        return ConclusionResult(
            batch_id=current.batch_id,
            outcome=OUTCOME_NO_CHANGE,
            reason=text,
            decided_at=stamp,
            record_path=current.batch.outcome_path,
        )


def _require_nothing_outstanding(current: BatchLineage) -> None:
    """A batch concludes `no-change` only when nothing about it is still open.

    Three different ways it can be, and each of them contradicts the conclusion
    rather than merely preceding it:

    - an open experiment is an attempt at a change, and an outcome is recorded
      after the last attempt ends, never over one that is running;
    - a promoted attempt says the source line moved, which is the same
      contradiction the reader refuses from the other side — that batch
      concluded by promoting, and the record has to say so;
    - a draft still waiting is a proposal the analysis made and nobody decided,
      so "the evidence justified no change" is a claim this batch's own gate
      does not support. Admit it or decline it; both are terminal, and either
      one is an answer.
    """

    if current.open_experiment is not None:
        raise BatchError(
            f"{current.batch_id} still has an open experiment ({current.open_experiment.experiment_id}); a "
            "batch's outcome is recorded after its last attempt ends, so abandon or supersede that one first — "
            "an abandoned attempt stays in the record as the evidence it is"
        )
    promoted = [
        experiment.experiment_id
        for experiment in current.experiments
        if experiment.decision is not None and experiment.decision.outcome == DECISION_PROMOTED
    ]
    if promoted:
        raise BatchError(
            f"{current.batch_id} cannot conclude {OUTCOME_NO_CHANGE!r}: {promoted} record a promotion; a batch "
            "whose candidate reached the source line concluded by promoting it, and the outcome names which "
            "attempt it was and the revision that carries it"
        )
    if current.gate.waiting:
        raise BatchError(
            f"{current.batch_id} still has draft(s) {list(current.gate.waiting)} waiting at its admission gate; "
            "concluding that the evidence justified no change while its own analysis has proposals nobody "
            "decided says two things at once — admit them or decline them, both of which are terminal"
        )


def _redo_conclusion(lineage: Lineage, reason: str) -> ConclusionResult:
    """The same conclusion run again, or the refusal that nothing is current.

    The record is what ends the batch, and it is written before the audit line —
    so a run interrupted between the two left no batch current, and its own
    retry would otherwise report that there is nothing to conclude. The newest
    batch concluded `no-change` for exactly this reason is that operation,
    already done.
    """

    concluded = [
        item
        for item in lineage.batches
        if item.outcome is not None
        and item.outcome["outcome"] == OUTCOME_NO_CHANGE
        and item.outcome["reason"] == reason
    ]
    if not concluded:
        raise BatchError(
            "no batch is current, so there is nothing to conclude; a batch is current from the freeze of its "
            "manifest until its outcome is recorded (invariant 14), and freezing the next cohort is "
            "`aii-2 evolution start`"
        )
    latest = concluded[-1]
    outcome = latest.outcome or {}
    return ConclusionResult(
        batch_id=latest.batch_id,
        outcome=OUTCOME_NO_CHANGE,
        reason=reason,
        decided_at=outcome["decided_at"],
        record_path=latest.batch.outcome_path,
        recorded=False,
    )


def _observe_completions(
    config: EvolutionConfig,
    current: BatchLineage,
    experiment: Experiment,
    round_: Round,
    *,
    observed_at: str,
) -> tuple[tuple[AdmittedTask, ...], tuple[str, ...]]:
    """This round's tasks, each carrying the observation that it finished.

    The status is read from the copy this admission published, identified the
    same way an interrupted admission identifies its own work: a file standing at
    an admitted task's id that is not that copy says nothing about whether the
    change was made, and reading `completed` off it would seal a candidate around
    work nobody did.

    A task the record already shows complete is not re-read. The observation is
    durable and the file is not: close-out archives it, another clone never had
    it, and an earlier run of this operation may have recorded the observation
    before the seal itself was interrupted.
    """

    tasks: list[AdmittedTask] = []
    observed: list[str] = []
    outstanding: list[str] = []
    for task in round_.tasks:
        if task.complete:
            tasks.append(task)
            continue
        path = analysis_task.existing_task_path(config, task.task_id)
        if path is None:
            tasks.append(task)
            outstanding.append(f"{task.task_id} (not on this machine)")
            continue
        _require_admitted_copy(config, current, experiment, round_, task, path)
        if not analysis_task.task_finished(config, task.task_id):
            tasks.append(task)
            outstanding.append(f"{task.task_id} (still in flight)")
            continue
        tasks.append(replace(task, completion_observed_at=observed_at))
        observed.append(task.task_id)

    if outstanding:
        raise BatchError(
            f"round {round_.number} of {experiment.experiment_id} is not ready to seal: {outstanding}; a "
            "candidate that does not contain the change it was admitted for is not what anyone means to measure "
            "(invariant 16), and `.ai-tasks/` is machine-local — a task nobody here holds is observed on the "
            "machine that worked it rather than assumed finished"
        )
    return tuple(tasks), tuple(observed)


def _pinnable_tip(experiment: Experiment, ref: RefState | None) -> str:
    """The revision this seal pins, or a refusal naming why it cannot be one.

    Stricter than the ref check every write here makes, and deliberately so: that
    one refuses a ref known to be wrong, while a seal must not pin a revision
    this checkout cannot show to descend from the history the record already
    names. The pin is immutable and every later piece of evidence names it, so
    "probably the right tree" is not a thing to write down.

    The tip pinned is the one that was checked — the observation the lineage made
    when it derived this experiment's ref state — rather than a fresh reading
    taken at write time. A ref that moved in between would otherwise be pinned
    without its ancestry ever having been asked about, which is the one property
    this refusal exists to establish.
    """

    if ref is None or ref.tip is None:
        raise BatchError(
            f"{experiment.ref} is not in this checkout, so this round has no tip to pin; the seal records the "
            "revision the work actually reached, which is a fact only the repository holding that ref has — "
            "fetch it, or seal where the work happened"
        )
    if ref.consistent is not True:
        raise BatchError(
            f"{ref.ref} stands at {ref.tip[:12]}, and this checkout cannot confirm it descends from the "
            f"{ref.pinned[:12]} the record pins ({ref.state}); a candidate revision is pinned once and every "
            "later piece of evidence names it, so it is pinned from a history Git can answer for"
        )
    return ref.tip


@contextmanager
def _unmoved(
    config: EvolutionConfig,
    experiment: Experiment,
    revision: str | None,
    requirement: str,
) -> Iterator[None]:
    """The experiment's ref, held where this operation read it, until its record
    says the same thing.

    Every transition here is decided from one reading of the ref and recorded
    afterwards, and the gap between is not this package's to schedule: the
    single-writer lock covers evolution runs, while what advances an experiment
    ref is ordinary Git. A commit arriving in that gap costs a seal the property
    the seal exists for — the pin would name a tree whose ancestry was never the
    one asked about — and costs a revision more than that: the arriving commit
    becomes the new round's work, so a commit made under a round that had
    already been measured is left indistinguishable from one made after,
    which is exactly the ordering invariant 16 gives replay evidence.

    A terminal decision loses something else again in that gap, and loses it
    permanently. `BatchLineage.ref` describes the *open* experiment, so the
    decision is the last reading of that ref anyone takes: a ref moving off the
    pinned history between the check and the record is a disagreement the check
    was there to catch, retired by the very write that follows it, with the
    revisions the record pins left unreachable and nothing able to report it.

    None of the three is recoverable by re-reading afterwards, because the
    records that would disagree are the ones being written. So the ref is held:
    it either stands where it was read for as long as the record takes to land,
    or this refuses and nothing is written from a reading that has already
    expired.

    An admission is not held this way, and should not be: it records no
    revision, the round it adds to is open, and a ref moving under an open round
    is the work itself proceeding — holding it there would block the very
    commits the admitted task is being written to make.
    """

    holding = ExitStack()
    try:
        holding.enter_context(held_at(config.repo_root, experiment.ref, revision))
    except RefHoldError as exc:
        raise BatchError(
            f"{experiment.experiment_id}: {exc}; {requirement}, so it is recorded while the ref is held there "
            "rather than from a reading that may already be stale — read the ref as it now stands and decide "
            "again"
        ) from exc
    with holding:
        yield


def _reason(text: str, requirement: str) -> str:
    """The human reason a decision records, as one line.

    Collapsed because it travels in a versioned record and is compared there: a
    redo recognising its own interrupted work matches the reason it wrote, and
    two spellings differing only in how they were wrapped would read as two
    different decisions.
    """

    collapsed = " ".join((text or "").split())
    if not collapsed:
        raise BatchError(requirement)
    return collapsed


def _requested(draft_ids: Iterable[str]) -> set[str]:
    """The selection, checked as a set of draft ids before it names any path."""

    requested = list(draft_ids)
    if not requested:
        raise BatchError("no draft was named; an admission or a rejection is a decision about specific proposals")
    unusable = sorted({value for value in requested if not is_draft_id(value)})
    if unusable:
        raise BatchError(
            f"{unusable} cannot be draft ids; a draft id is a kebab-case slug, which is what makes it one path "
            f"segment under proposed-tasks/ rather than a name that could reach anywhere else"
        )
    repeated = sorted({value for value in requested if requested.count(value) > 1})
    if repeated:
        raise BatchError(
            f"draft(s) {repeated} are named twice in one selection; a draft is consumed once, and naming it twice "
            "says nothing about which of the two was meant"
        )
    return set(requested)


# --- drafts ------------------------------------------------------------------


@dataclass(frozen=True)
class _Draft:
    """One waiting proposal, read and checked but not yet admitted."""

    draft_id: str
    path: Path
    text: str
    # Where the body starts, read from the frontmatter rather than by looking for
    # something that resembles a section: the admission block goes into the body,
    # and a `## ` line inside an unterminated frontmatter block is not one.
    body_start: int
    sha256: str
    task_id: str
    title: str


def _collect(
    config: EvolutionConfig,
    current: BatchLineage,
    requested: set[str],
    *,
    for_tasks: bool = True,
) -> tuple[_Draft, ...]:
    """Read every requested draft, refusing anything the gate cannot decide.

    `for_tasks` is False for a rejection, which needs the bytes and their hash
    but decides nothing about `.ai-tasks/`: a proposal turned down never becomes
    a task, so requiring it to name a free task id would refuse a decline over a
    detail the decline makes irrelevant.
    """

    gate = current.gate
    for draft_id in sorted(requested):
        if draft_id in gate.waiting:
            continue
        owner = gate.consumed.get(draft_id)
        if owner is not None:
            raise BatchError(
                f"draft {draft_id!r} was already admitted by {owner}; admitting is terminal for a proposal, and "
                "proposing the idea again means a new draft id whose own bytes say what the second proposal was"
            )
        if draft_id in gate.declined:
            raise BatchError(
                f"draft {draft_id!r} was declined at {current.batch_id}'s gate; declining is terminal for a "
                "proposal, and re-proposing means a new draft id"
            )
        raise BatchError(
            f"{_draft_path(current.batch, draft_id)} does not exist; the drafts waiting at "
            f"{current.batch_id}'s gate are {list(gate.waiting)}"
        )

    drafts = tuple(_read_draft(current.batch, draft_id, for_tasks=for_tasks) for draft_id in sorted(requested))
    if for_tasks:
        _require_free_task_ids(config, current, drafts)
    return drafts


def _draft_path(batch: Batch, draft_id: str) -> Path:
    """Where a draft waits. One construction, because the copy's provenance names
    this path and a later run rebuilds that line to recognise the copy."""

    return batch.directory / analysis_task.PROPOSED_TASKS_DIRNAME / f"{draft_id}{DRAFT_SUFFIX}"


def _read_draft(batch: Batch, draft_id: str, *, for_tasks: bool) -> _Draft:
    """One draft's bytes, its hash, and the task identity it declares.

    The draft is a schema-conforming task file, so the copy takes the id the
    draft itself states rather than one this controller invents: the bytes
    admitted and the bytes dispatched then say the same thing about what the task
    is, and `draft_sha256` describes both.
    """

    path = _draft_path(batch, draft_id)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BatchError(f"unreadable draft {path}: {exc}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BatchError(f"{path} is not UTF-8 text; a change-task draft is a task file: {exc}") from exc

    block = analysis_task.parse_frontmatter(text)
    if for_tasks:
        _require_task_file(path, text, block)
    return _Draft(
        draft_id=draft_id,
        path=path,
        text=text,
        body_start=block.body_start,
        sha256=sha256_bytes(raw),
        task_id=block.fields.get("id", ""),
        title=_title(text, draft_id, start=block.body_start),
    )


def _require_task_file(path: Path, text: str, block: analysis_task.Frontmatter) -> None:
    """Refuse a draft that is not the inert task file the gate decides about.

    Admission is a copy, so what is checked here is what the copy becomes: a
    pending task in the active pool, claimed by a session that increments its
    estimate, worked from its scope, and reviewed against its acceptance. A
    proposal that is a task file in name only reaches that pool as one anyway —
    nothing downstream re-reads it as a proposal — so the whole shape is checked
    at the one point where refusing it costs nothing but a redraft.

    The frontmatter block is checked before its fields for a reason of this
    module's own: an unterminated one has no body, and the admission provenance
    goes into the body. A file whose sections are all still inside its
    frontmatter would take the block in there with them.

    The body is read as sections rather than as text for the same kind of reason.
    A heading that occurs is not a section that is there once and says what it is
    for: two goals or two session logs leave every reader taking whichever it
    reaches first, a log with entries under it is a task someone has already
    worked, and an `## Admission` section is the one thing the gate itself adds.
    A line that only looks like a heading is refused before any of that, because
    it is what makes "which section is this line under" a question with more than
    one answer.
    """

    if not block.present:
        raise BatchError(
            f"{path}: no frontmatter block; a draft is a schema-conforming task file, and the lifecycle its copy "
            "is dispatched under — the id it takes, the status the gate decides about — is what that block carries"
        )
    if not block.closed:
        raise BatchError(
            f"{path}: the frontmatter block is never closed by a '---' line; everything below an unterminated one "
            "is still frontmatter, so this file declares no lifecycle to claim and has no body to admit"
        )
    if block.duplicated:
        raise BatchError(
            f"{path}: frontmatter declares {list(block.duplicated)} more than once; a field stated twice says two "
            "things about one task, and every reader takes whichever it reaches first"
        )

    task_id = _field(path, block, "id")
    if not _TASK_ID.match(task_id):
        raise BatchError(
            f"{path}: frontmatter id {task_id!r} is not a date-prefixed task slug; admission copies the draft to "
            "the task id it declares, and that id is also the file name the copy takes"
        )
    status = _field(path, block, "status")
    if status != DRAFT_STATUS:
        raise BatchError(
            f"{path}: a draft waiting at the gate carries status {DRAFT_STATUS!r}, not {status!r}; a proposal "
            "worked on where nothing dispatches it is not the inert draft the gate decides about"
        )
    estimate = _field(path, block, "session-est")
    if not _UNCONSUMED_SESSION_EST.match(estimate):
        raise BatchError(
            f"{path}: session-est {estimate!r} is not '0/<total>'; a session claims a task by incrementing that "
            "count, so a draft nobody has worked on yet is at zero of an estimate it does state"
        )
    blockers = _field(path, block, "blockers")
    if blockers != EMPTY_BLOCKERS:
        raise BatchError(
            f"{path}: blockers {blockers!r} is not {EMPTY_BLOCKERS!r}; a proposal that is already waiting on "
            "something is not work the gate can admit, and a blocked task in the active pool blocks nothing"
        )
    claimed = _field(path, block, "claimed-by")
    if claimed:
        raise BatchError(
            f"{path}: claimed-by {claimed!r} names a session; a draft is claimed by the session that picks its "
            "copy up out of the active pool, which is a thing that has not happened to a proposal"
        )

    body = text.splitlines()[block.body_start :]
    _require_plain_headings(path, body)
    sections = _sections(body)
    names = [name for name, _ in sections]
    missing = [heading for heading in REQUIRED_SECTIONS if heading not in names]
    if missing:
        raise BatchError(
            f"{path}: no {missing} section(s); an admitted copy is worked from its scope and reviewed against its "
            "acceptance, and the session log is where each session records what it did"
        )
    repeated = [heading for heading in REQUIRED_SECTIONS if names.count(heading) > 1]
    if repeated:
        raise BatchError(
            f"{path}: {repeated} section(s) declared more than once; a task worked from two scopes or reviewed "
            "against two acceptances has no one shape, and each reader takes whichever it reaches first"
        )
    if ADMISSION_HEADING in names:
        raise BatchError(
            f"{path}: the draft already carries an {ADMISSION_HEADING!r} section; that section is what admission "
            "itself adds, and a copy carrying two of them is one no later run can identify as this admission's"
        )
    _require_empty_log(path, body, sections)


def _require_plain_headings(path: Path, body: Sequence[str]) -> None:
    """Refuse a draft carrying a line that looks like a section heading but is
    indented.

    Such a line means different things to its readers at once — Markdown reads it
    as code, a session reading the copy reads it as a section, and the scan below
    reads it as neither — and admission is what carries that disagreement into
    the active pool. A copy could then hold a scope no two readers agree is one,
    or an `## Admission` section the record cannot identify it by, standing beside
    the one it can.

    Refusing the shape here is also what lets a section's extent be read exactly:
    where a section ends is where the next heading stands (`_is_section`), and a
    file with no ambiguous heading-looking line in it has one answer to that.
    Sections stand at column 0, the way the taskfile schema, the intake contract,
    and every task this controller writes state theirs.
    """

    for line in body:
        if line.strip().startswith("## ") and not _is_section(line):
            raise BatchError(
                f"{path}: {line.strip()!r} is indented; a task section stands at column 0, and a line that only "
                "looks like a heading is read as one by the session working the copy and as text by everything "
                "that checks it"
            )


def _require_empty_log(path: Path, body: Sequence[str], sections: Sequence[tuple[str, int]]) -> None:
    """A draft's session log is empty, because a session log is what happens to a
    task *after* it is dispatched.

    The heading alone is not the check. An entry under it describes sessions that
    claimed this task, incremented its estimate, and recorded what they did — none
    of which can have happened to a proposal nothing dispatches, and all of which
    the copy carries into the active pool as its own history. The rest of the
    frontmatter says the same thing from the other side (`pending`, `0/<total>`,
    unclaimed), so a log with entries in it is a file contradicting itself about
    whether the work has started.
    """

    start = next(index for name, index in sections if name == SESSION_LOG_HEADING)
    entry = next((line for line in _section_at(body, sections, start)[1:] if line.strip()), None)
    if entry is not None:
        raise BatchError(
            f"{path}: {SESSION_LOG_HEADING} already carries {entry.strip()!r}; a session log is appended to by the "
            "sessions that work the task, so an entry in a proposal nothing has dispatched records work on a task "
            "that does not exist yet"
        )


def _sections(lines: Sequence[str]) -> list[tuple[str, int]]:
    """Every section heading in a task body, with the line it stands on.

    Level two, and only level two: a session-log entry is a level-3 heading
    (taskfile schema §5), so a scan taking any `#`-prefixed line as a section
    would read an ordinary log as structure of its own. One reading, shared by
    the shape check, the admission-section identity check, and the renderer that
    decides where the provenance goes — three questions about the same sections,
    and an answer that differed between them would put the block somewhere the
    checks never looked.
    """

    return [(line.strip(), index) for index, line in enumerate(lines) if _is_section(line)]


def _is_section(line: str) -> bool:
    """A line that opens a section — not one that merely looks like it.

    The heading stands at column 0, where the taskfile schema and this
    controller's own renderer put every section they write. Indentation is the
    whole question: Markdown reads four spaces before a `##` as code rather than
    as a heading, so a reader that ends a section at such a line ends it *inside*
    the section, and everything below the false boundary is content nothing
    compares — which is how an unrecorded instruction hides under a provenance
    block that otherwise matches.

    Reading an indented line as content instead can only make a section come out
    longer than it is, and a section read long refuses where a section read short
    admits. Drafts carrying a heading-looking line that is indented are turned
    away at the gate (`_require_plain_headings`), so no file this controller
    writes leaves the two readings anything to disagree about.
    """

    return line.startswith("## ")


def _section_at(body: Sequence[str], sections: Sequence[tuple[str, int]], start: int) -> list[str]:
    """One whole section: its heading and everything under it, to the next one.

    A section ends where the next level-2 heading stands, or at the end of the
    body — the same extent whether the question is "is this log empty" or "is this
    the provenance that was written". Reading a fixed number of lines instead
    would answer about a prefix of the section and call it the section, and
    ending at a line that is not a heading (`_is_section`) answers about a prefix
    just the same, with the boundary chosen by whatever wrote the file.
    """

    following = [index for _, index in sections if index > start]
    return list(body[start : following[0] if following else len(body)])


def _field(path: Path, block: analysis_task.Frontmatter, name: str) -> str:
    """One frontmatter field of a draft, or a refusal naming what is missing."""

    if name not in block.fields:
        raise BatchError(
            f"{path}: frontmatter carries no {name!r}; a draft is a schema-conforming task file, and its copy is "
            "dispatched as one the moment it is admitted"
        )
    return block.fields[name]


def _require_free_task_ids(config: EvolutionConfig, current: BatchLineage, drafts: tuple[_Draft, ...]) -> None:
    """One task id belongs to one admission, and to nothing already in flight.

    Checked against the batch's whole experiment history as well as `.ai-tasks/`
    itself: the record could not otherwise say which bytes a task implemented or
    whose completion observation seals a round, and a copy that overwrote an
    existing task would destroy a session log to satisfy an admission.
    """

    admitted = {
        task.task_id: f"{experiment.experiment_id} as draft {task.draft_id!r}"
        for experiment in current.experiments
        for task in experiment.admitted_tasks
    }
    claimed: dict[str, str] = {}
    for draft in drafts:
        owner = admitted.get(draft.task_id)
        if owner is not None:
            raise BatchError(
                f"{draft.path}: task {draft.task_id!r} is already admitted by {owner}; one task implements one "
                "proposal, and a second draft admitted into it means a task id nothing can be traced through"
            )
        previous = claimed.setdefault(draft.task_id, draft.draft_id)
        if previous != draft.draft_id:
            raise BatchError(
                f"drafts {previous!r} and {draft.draft_id!r} both declare task id {draft.task_id!r}; one of them "
                "would take the other's file, and the record could name neither"
            )
        existing = analysis_task.existing_task_path(config, draft.task_id)
        if existing is not None:
            raise BatchError(
                f"{draft.path}: task {draft.task_id!r} already exists at {existing}; admission never overwrites a "
                "task file — give the draft an id of its own, or resolve the existing task first"
            )


def _title(text: str, draft_id: str, *, start: int = 0) -> str:
    """The draft's own heading, for the one line the active index shows.

    Read from the body: a frontmatter value that happens to look like a heading
    is a field, not a title.
    """

    for line in text.splitlines()[start:]:
        if line.startswith("# "):
            title = " ".join(line[2:].split())
            if title:
                return title[:_MAX_SUMMARY]
    return f"Change admitted from draft {draft_id}"


def _title_of(path: Path, draft_id: str) -> str:
    """The heading of a task file already on disk — read from the copy rather
    than from the draft, since that copy is what the row is about and it is the
    one thing this branch knows is there."""

    try:
        return _title(path.read_text(encoding="utf-8"), draft_id)
    except OSError:
        return f"Change admitted from draft {draft_id}"


def _admission_reason(requested: set[str]) -> str:
    return "grouped admission of draft(s) " + ", ".join(sorted(requested))


# --- writes ------------------------------------------------------------------


def _base_revision(
    config: EvolutionConfig,
    current: BatchLineage,
    requested: str | None,
) -> tuple[str, str | None]:
    """The commit this experiment starts from, and the release it builds on.

    A batch has exactly one base and its first experiment settled it (invariant
    15), so a later experiment takes that commit rather than resolving one:
    attempts against different sources are not alternatives to each other. An
    explicit `base` is still checked against it, because an operator who names a
    revision expecting it to be used should be told it was not.
    """

    frozen = current.base_revision
    if frozen is not None:
        _require_requested_base(config, current, frozen, requested)
        if resolve_commit(config.repo_root, frozen) is None:
            raise BatchError(
                f"this checkout does not hold {current.batch_id}'s base revision {frozen[:12]}; fetch it before "
                "starting another experiment, since the new ref has to be created at exactly that commit"
            )
        return frozen, current.base_release_ref

    revision = requested if requested is not None else "HEAD"
    resolved = _resolve(config, revision)
    return resolved, release_ref(config.repo_root, resolved)


def _require_requested_base(
    config: EvolutionConfig,
    current: BatchLineage,
    frozen: str,
    requested: str | None,
) -> None:
    """A named base is checked against the one this batch already froze.

    Silence would be the wrong answer in either direction: the base is not going
    to change (invariant 15), and an operator who named a revision expecting it to
    be used has to be told it was not.
    """

    if requested is None or _resolve(config, requested) == frozen:
        return
    raise BatchError(
        f"{current.batch_id} froze its base at {frozen[:12]} with {current.experiments[0].experiment_id}, so "
        f"{requested!r} cannot be this experiment's base; every alternative starts from the same commit or they "
        "are not alternatives (invariant 15)"
    )


def _resolve(config: EvolutionConfig, revision: str) -> str:
    resolved = resolve_commit(config.repo_root, revision)
    if resolved is None:
        raise BatchError(
            f"cannot resolve {revision!r} to a commit in {config.repo_root}; an experiment records the exact "
            "revision it starts from, so the base has to be a commit this repository holds"
        )
    return resolved


def _create_experiment_ref(config: EvolutionConfig, experiment: Experiment) -> None:
    """Create the durable ref at the base, or adopt the one an interrupted run
    already created there.

    Never moved and never recreated. A ref already holding an experiment's rounds
    is the one thing a repair must not touch, and a ref that is simply absent is
    the ordinary state of every clone that did not do this work — so the only
    thing this may safely do is create one that is not there.
    """

    tip = ref_tip(config.repo_root, experiment.ref)
    if tip == experiment.base_revision:
        return
    if tip is not None:
        raise BatchError(
            f"{experiment.ref} already exists at {tip[:12]}, not at {experiment.base_revision[:12]}; an "
            f"experiment id is never reused, so a ref standing where {experiment.experiment_id} is about to be "
            "created belongs to work this controller cannot account for"
        )
    failure = create_ref(config.repo_root, experiment.ref, experiment.base_revision)
    if failure is not None:
        raise BatchError(
            f"cannot create {experiment.ref} at {experiment.base_revision[:12]} for "
            f"{experiment.experiment_id}: {failure}"
        )


def _publish_record(config: EvolutionConfig, experiment: Experiment) -> Path:
    """Publish a new experiment directory by an atomic rename.

    A directory appearing before its record would stop every later read — one
    experiment is one directory, and a directory without `experiment.json` names
    no base, no tasks, and no candidate. So it appears complete or not at all;
    `.staging-*` residue from an interrupted publish is dot-prefixed and belongs
    to no experiment.
    """

    record = _serialize(experiment)
    _validate(config, record, experiment)

    root = config.experiments_root
    root.mkdir(parents=True, exist_ok=True)
    final = root / experiment.experiment_id
    if final.exists():
        raise BatchError(
            f"{final} already exists; an experiment id is allocated one past the highest its batch ever used and "
            "is never reused"
        )
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=root))
    try:
        atomic_write_text(staging / EXPERIMENT_FILENAME, _json(record))
        os.replace(staging, final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return final


def _write_record(config: EvolutionConfig, experiment: Experiment) -> Path:
    """Rewrite an existing experiment record, atomically.

    The record accumulates — a round's admitted tasks, its seal, the terminal
    decision — so unlike a frozen manifest it is rewritten in place. What is
    never rewritten is what it already says: every append is checked against the
    schema here and against the lineage rules the next read applies.
    """

    record = _serialize(experiment)
    _validate(config, record, experiment)
    path = experiment.directory / EXPERIMENT_FILENAME
    if not path.is_file():
        raise BatchError(f"{path} is gone; an experiment record is never recreated from a partial reading")
    atomic_write_text(path, _json(record))
    return path


def _validate(config: EvolutionConfig, record: Mapping[str, Any], experiment: Experiment) -> None:
    """Check the record about to be written the way it will be read back.

    Through the reader's own parse, not a schema check beside it: the schema
    subset has no cross-field conditionals, so every rule that makes a record one
    readable history — rounds that only append, a seal that waits for its tasks,
    a sealed round with something admitted into it, a decision carrying exactly
    the fields its outcome means, `promoted` only from a candidate-ready round —
    lives in `lineage.parse_experiment`. Stating any of them a second time here
    is what would let the writer and the reader drift, and the direction they
    drift in is a record this controller wrote and can no longer read.
    """

    parse_experiment(config, record, experiment.directory)


def _write_tasks(
    config: EvolutionConfig,
    current: BatchLineage,
    experiment: Experiment,
    round_: Round,
    drafts: tuple[_Draft, ...],
) -> tuple[Admitted, ...]:
    """Copy each admitted draft into `.ai-tasks/` and list it in the active index.

    Last, and derivable from the record: an interrupted run leaves tasks missing
    rather than leaving tasks nothing accounts for.
    """

    written: list[Admitted] = []
    for draft in drafts:
        text = _render_task(config, current, experiment, round_, draft)
        path = analysis_task.publish_task(config, draft.task_id, text, description="admitted change task")
        analysis_task.append_row(config, draft.task_id, _summary(experiment, draft.draft_id, draft.title))
        written.append(
            Admitted(
                draft_id=draft.draft_id,
                task_id=draft.task_id,
                draft_sha256=draft.sha256,
                task_path=path,
            )
        )
    return tuple(written)


def _redo_create(
    config: EvolutionConfig,
    current: BatchLineage,
    experiment: Experiment,
    requested: set[str],
) -> AdmissionResult:
    """The same grouped admission run again, or a refusal naming what is open.

    The record is what made the admission real, so an open experiment on its
    first round admitting exactly this selection *is* this operation, already
    recorded; what is left of it is the copies. Any other selection is a second
    experiment over one that is already open, which invariant 14 does not allow —
    and the refusal names what is open rather than what was asked for, since the
    open attempt is what has to be dealt with either way.
    """

    round_ = experiment.open_round
    admitted = {} if round_ is None else {task.draft_id: task for task in round_.tasks}
    if round_ is not None and len(experiment.rounds) == 1 and set(admitted) == requested:
        return _finish(config, current, experiment, round_, tuple(admitted[key] for key in sorted(admitted)))

    last = experiment.last_round
    state = "open" if round_ is not None else "candidate-ready"
    raise BatchError(
        f"{current.batch_id} already has an open experiment ({experiment.experiment_id}, round {last.number} "
        f"{state}, drafts {sorted(task.draft_id for task in last.tasks)}); invariant 14 allows one at a time — "
        "admit further drafts into its open round, or end it with a decision, which keeps it as evidence and "
        "frees the batch for an alternative"
    )


def _finish(
    config: EvolutionConfig,
    current: BatchLineage,
    experiment: Experiment,
    round_: Round,
    tasks: tuple[AdmittedTask, ...],
) -> AdmissionResult:
    """Write whatever an already-recorded admission still owes `.ai-tasks/`."""

    written = tuple(_restore_task(config, current, experiment, round_, task) for task in tasks)
    return _result(current.batch, experiment, round_, written, created=False)


def _restore_task(
    config: EvolutionConfig,
    current: BatchLineage,
    experiment: Experiment,
    round_: Round,
    task: AdmittedTask,
) -> Admitted:
    """Write the copy of one already-recorded admission, if it is not there.

    Only for a task the record says is still owed, and only where what is there
    is that copy. Three states, and each of them is ordinary:

    - the file is this experiment's copy and still in flight: left exactly as it
      is, since it may already carry a session log, and only its index row is
      made good — the step an interruption can drop on its own;
    - it has finished, whether the record has observed that yet or not: archived
      by close-out or `completed` in place, and either way it belongs to the
      close-out, not to the active pool a `pending` row would put it back in;
    - it is not here at all: written from the draft, which is re-read and
      re-hashed against what the record admitted, because the copy has to be made
      from the bytes that were admitted and a draft edited since is a state this
      controller cannot account for rather than one it should quietly copy.

    A task the record already shows complete is never recreated even when its
    file is gone: close-out archived it, and recreating it as pending would
    reopen work that finished.
    """

    existing = analysis_task.existing_task_path(config, task.task_id)
    if existing is not None:
        _require_admitted_copy(config, current, experiment, round_, task, existing)
        if not (task.complete or analysis_task.task_finished(config, task.task_id)):
            summary = _summary(experiment, task.draft_id, _title_of(existing, task.draft_id))
            analysis_task.append_row(config, task.task_id, summary)
        return _already(task, existing)
    if task.complete:
        return _already(task, analysis_task.task_path(config, task.task_id))

    draft = _read_draft(current.batch, task.draft_id, for_tasks=True)
    if draft.sha256 != task.draft_sha256:
        raise BatchError(
            f"{draft.path} no longer matches the bytes {experiment.experiment_id} admitted (recorded "
            f"{task.draft_sha256[:12]}, found {draft.sha256[:12]}); the copy owed to {task.task_id!r} has to be "
            "the proposal that was admitted, so restore the draft rather than admitting a different one under it"
        )
    if draft.task_id != task.task_id:
        raise BatchError(
            f"{draft.path} declares task id {draft.task_id!r}, but {experiment.experiment_id} admitted it as "
            f"{task.task_id!r}; the record names the task the copy took"
        )
    path = analysis_task.publish_task(
        config,
        task.task_id,
        _render_task(config, current, experiment, round_, draft),
        description="admitted change task",
    )
    analysis_task.append_row(config, task.task_id, _summary(experiment, draft.draft_id, draft.title))
    return Admitted(
        draft_id=task.draft_id,
        task_id=task.task_id,
        draft_sha256=task.draft_sha256,
        task_path=path,
        restored=True,
    )


def _already(task: AdmittedTask, path: Path) -> Admitted:
    return Admitted(
        draft_id=task.draft_id,
        task_id=task.task_id,
        draft_sha256=task.draft_sha256,
        task_path=path,
    )


def _require_admitted_copy(
    config: EvolutionConfig,
    current: BatchLineage,
    experiment: Experiment,
    round_: Round,
    task: AdmittedTask,
    path: Path,
) -> None:
    """Refuse to treat an unrelated file at this task id as the admitted copy.

    A redo that finds the file present declares that copy done, so what is at
    that path decides whether the admission has its task at all. Adopting an
    unrelated one lists somebody else's work as this experiment's, puts a
    `pending` row on it, and hands the record a task whose bytes implement
    nothing it names.

    Identity is read from the structures that own the values, never from the text
    containing them. The task id is the frontmatter field `id`, declared once — a
    file saying `not-id:`, discussing the id in prose, or naming two ids is not a
    task with that id — and the provenance is the one `## Admission` section, whole
    and exactly as this admission would write it. That section is the immutable
    part of a copy: the batch, the experiment, the round, the ref, the base, and
    the digest of the bytes admitted, none of which a session working the task has
    any reason to edit. Everything a session *does* change is outside it — the
    frontmatter lifecycle above, the session log below — so an ordinary claimed and
    logged copy still matches, while a file that merely mentions the same values
    does not.

    Whole means through the next level-2 boundary, not the first lines of it. A
    section carrying what this admission wrote and then a line more is a copy whose
    provenance says something the record never said — another base to work from,
    another ref to commit on — and reading only as far as the recorded lines
    reach is what leaves that line invisible. So is ending at a line that is not a
    heading: an indented `##` is code to Markdown and a section to nothing, and a
    boundary the file gets to invent is one it can put immediately after the
    recorded lines and hide the rest behind (`_is_section`).

    The cost of matching the section rather than sampling it: a copy written by a
    controller whose wording of that section differed no longer matches its own
    admission. That fails closed, naming the line that differs, which is the same
    answer this module gives to every other state it cannot account for.

    Nothing is repaired. The file may already carry a session log, and rewriting
    one to satisfy a redo would destroy the record it exists to keep.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BatchError(f"cannot read the admitted task {path}: {exc}") from exc

    detail = _unlike_admitted_copy(config, current, experiment, round_, task, text)
    if detail is not None:
        raise BatchError(
            f"{path} is not the copy {experiment.experiment_id} admitted as {task.task_id!r} ({detail}); an "
            "admission never overwrites a task file, and a file this record cannot identify is not the work it "
            "accounts for — resolve what is at that id"
        )


def _unlike_admitted_copy(
    config: EvolutionConfig,
    current: BatchLineage,
    experiment: Experiment,
    round_: Round,
    task: AdmittedTask,
    text: str,
) -> str | None:
    """What stops this file from being that admission's copy, or None."""

    block = analysis_task.parse_frontmatter(text)
    if not block.present or not block.closed:
        return "it carries no closed frontmatter block"
    if block.duplicated:
        # First occurrence wins in `fields`, so a block declaring the recorded id
        # and another one reads as this task to whatever scans down it and as some
        # other task to whatever does not. The restore path reads `status` out of
        # the same block to decide whether the copy is still owed, so the
        # ambiguity is not confined to the id either.
        return f"its frontmatter declares {list(block.duplicated)} more than once"
    declared = block.fields.get("id")
    if declared is None:
        return "its frontmatter declares no id"
    if declared != task.task_id:
        return f"its frontmatter declares id {declared!r}"

    body = text.splitlines()[block.body_start :]
    sections = _sections(body)
    at = [index for name, index in sections if name == ADMISSION_HEADING]
    if not at:
        return f"it carries no {ADMISSION_HEADING!r} section"
    if len(at) > 1:
        return f"it carries {len(at)} {ADMISSION_HEADING!r} sections"

    expected = _admission_section(config, current, experiment, round_, task.draft_id, task.draft_sha256)
    found = _section_at(body, sections, at[0])
    for index, line in enumerate(expected):
        if index >= len(found) or found[index] != line:
            return f"its admission section does not carry {line!r}"
    if len(found) > len(expected):
        return f"its admission section also carries {found[len(expected)]!r}"
    return None


# --- shapes ------------------------------------------------------------------


def _admitted(draft: _Draft, *, admitted_at: str) -> AdmittedTask:
    return AdmittedTask(
        task_id=draft.task_id,
        draft_id=draft.draft_id,
        draft_sha256=draft.sha256,
        admitted_at=admitted_at,
        completion_observed_at=None,
    )


def _open_round(experiment: Experiment) -> Round:
    round_ = experiment.open_round
    if round_ is None:
        last = experiment.last_round
        raise BatchError(
            f"round {last.number} of {experiment.experiment_id} is candidate-ready at "
            f"{(last.candidate_revision or '')[:12]}; its evidence names that pinned revision, so further work "
            "opens the next round rather than changing what was already measured (invariant 16)"
        )
    return round_


def _serialize(experiment: Experiment) -> dict[str, Any]:
    """The record exactly as the schema holds it.

    One serializer for every write here, so an operation that appends a round or
    records a decision cannot drop a field the reader depends on: what is written
    back is the whole record the reader produced, with the one part that changed
    replaced.
    """

    decision = experiment.decision
    return {
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "experiment_id": experiment.experiment_id,
        "batch_id": experiment.batch_id,
        "created_at": experiment.created_at,
        "base_revision": experiment.base_revision,
        "base_release_ref": experiment.base_release_ref,
        "ref": experiment.ref,
        "rounds": [
            {
                "round": round_.number,
                "opened_at": round_.opened_at,
                "reason": round_.reason,
                "tasks": [
                    {
                        "task_id": task.task_id,
                        "draft_id": task.draft_id,
                        "draft_sha256": task.draft_sha256,
                        "admitted_at": task.admitted_at,
                        "completion_observed_at": task.completion_observed_at,
                    }
                    for task in round_.tasks
                ],
                "seal": None
                if round_.seal is None
                else {
                    "sealed_at": round_.seal.sealed_at,
                    "candidate_revision": round_.seal.candidate_revision,
                },
            }
            for round_ in experiment.rounds
        ],
        "decision": None
        if decision is None
        else {
            "outcome": decision.outcome,
            "decided_at": decision.decided_at,
            "reason": decision.reason,
            "superseded_by": decision.superseded_by,
            "promotion_revision": decision.promotion_revision,
        },
    }


def _admission_records(
    batch: Batch,
    experiment: Experiment,
    round_: Round,
    written: tuple[Admitted, ...],
    *,
    recorded_at: str,
) -> list[dict[str, Any]]:
    return [
        build_record(
            RECORD_TASKS_ADMITTED,
            recorded_at=recorded_at,
            batch_id=batch.batch_id,
            experiment_id=experiment.experiment_id,
            round=round_.number,
            draft_id=item.draft_id,
            task_id=item.task_id,
        )
        for item in written
    ]


def _result(
    batch: Batch,
    experiment: Experiment,
    round_: Round,
    written: tuple[Admitted, ...],
    *,
    created: bool,
) -> AdmissionResult:
    return AdmissionResult(
        batch_id=batch.batch_id,
        experiment_id=experiment.experiment_id,
        round_number=round_.number,
        base_revision=experiment.base_revision,
        ref=experiment.ref,
        admitted=written,
        created=created,
    )


def _summary(experiment: Experiment, draft_id: str, title: str) -> str:
    """The active index's one line for an admitted change task: what it is, and
    which attempt it belongs to."""

    return f"{title} ({experiment.experiment_id}, draft {draft_id})"


def _render_task(
    config: EvolutionConfig,
    current: BatchLineage,
    experiment: Experiment,
    round_: Round,
    draft: _Draft,
) -> str:
    """The draft, plus what only the admission knows.

    A change task must state the batch's base revision and the experiment and
    draft it was admitted from (contract: Evolution task requirements), none of
    which exists when the draft is written. Without the ref, the session
    implementing it would also have nowhere to commit — the work belongs on the
    experiment's ref, not on whatever branch the checkout happens to be on.
    """

    block = "\n".join([*_admission_section(config, current, experiment, round_, draft.draft_id, draft.sha256), ""])
    lines = draft.text.splitlines(keepends=True)
    for index in range(draft.body_start, len(lines)):
        if _is_section(lines[index]):
            head = "".join(lines[:index])
            separator = "" if head.endswith("\n\n") or not head else "\n"
            return head + separator + block + "".join(lines[index:])
    raise BatchError(
        f"{draft.path}: no body section to admit before; a draft is a schema-conforming task file, and the "
        "admission provenance goes above its first section"
    )


def _admission_section(
    config: EvolutionConfig,
    current: BatchLineage,
    experiment: Experiment,
    round_: Round,
    draft_id: str,
    draft_sha256: str,
) -> tuple[str, ...]:
    """The whole `## Admission` section as it stands in a copy.

    The provenance lines and the blank line that separates them from the draft's
    own first section — everything between this heading and the next one, which is
    exactly the extent `_require_admitted_copy` reads back. Recognition compares
    the section, so the section is what gets written: a trailing line the renderer
    added and the comparison did not expect would refuse every copy this
    controller writes.
    """

    return (*_admission_lines(config, current, experiment, round_, draft_id, draft_sha256), "")


def _admission_lines(
    config: EvolutionConfig,
    current: BatchLineage,
    experiment: Experiment,
    round_: Round,
    draft_id: str,
    draft_sha256: str,
) -> tuple[str, ...]:
    """The `## Admission` section's own lines, written once and read back the same
    way.

    Every value in it comes from the record and the frozen manifest rather than
    from the draft's own text, which is what lets a later run rebuild this exact
    section from what the experiment says it admitted — and that reconstruction is
    how an admitted copy is recognised on the way back (`_require_admitted_copy`).
    Rendering and recognition through one function is the point: two spellings of
    the same section would drift, and the direction they drift in is a copy this
    controller wrote and can no longer identify.
    """

    relative = _draft_path(current.batch, draft_id).relative_to(config.repo_root).as_posix()
    runner = current.batch.manifest.get("runner_protocol_revision")
    release = experiment.base_release_ref or "no release tag reachable"
    return (
        ADMISSION_HEADING,
        "",
        f"Admitted from evolution batch `{current.batch_id}` under the normative",
        f"contract `{analysis_task.CONTRACT_PATH}`; every canonical change passes this",
        "human gate (invariant 9).",
        "",
        f"- Draft `{draft_id}`: `{relative}`,",
        f"  sha256 `{draft_sha256}`.",
        f"- Experiment `{experiment.experiment_id}`, round {round_.number}. Work on",
        f"  `{experiment.ref}`, which only",
        "  fast-forwards; the round is sealed — every admitted task observed complete,",
        "  the tip pinned — before anything measures the candidate (invariants 15, 16).",
        f"- Base revision `{experiment.base_revision}`",
        f"  ({release}): the commit every experiment of this batch starts from.",
        f"- Runner protocol revision: {runner or 'unknown — none recorded at freeze time'}.",
        "  It stays fixed for this task, and a candidate revision never governs the run",
        "  that creates it (invariant 8).",
    )


def _moment(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise BatchError("admission time must be timezone-aware; a naive datetime records an ambiguous moment")
    return now


def _json(record: Mapping[str, Any]) -> str:
    return json.dumps(record, indent=2, sort_keys=True) + "\n"


__all__ = [
    "Admitted",
    "AdmissionResult",
    "ConclusionResult",
    "DecisionResult",
    "RejectionResult",
    "ReviseResult",
    "SealResult",
    "abandon",
    "add_tasks",
    "conclude_no_change",
    "create",
    "reject",
    "revise",
    "seal_round",
    "supersede",
]
