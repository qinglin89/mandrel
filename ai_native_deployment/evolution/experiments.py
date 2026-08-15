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

Four end things, and each of them is terminal for what it ends (contract:
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
- **promote** — the replayed candidate is carried onto the source line and the
  batch ends with it. What it puts there is the tree a replay measured, in a
  merge commit made from the line as it stood and the round's pinned candidate;
  the gate is that the evidence still describes that tree, and the ref move is a
  compare-and-swap, so a line that took a commit in between refuses rather than
  carrying something nobody exercised.
- **conclude-no-change** — the batch outcome of invariant 7. It fabricates
  nothing on the way out: no candidate, no experiment, no promotion revision, no
  merge, no deployment. It is `promote`'s counterpart, and every reading of a
  concluded batch checks the two against the experiments as one set.

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
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, NoReturn, Sequence

from ..hashing import sha256_bytes
from . import analysis_task, assessment
from .config import EvolutionConfig
from .errors import BatchError
from .guards import (
    current_cycle,
    held,
    no_open_experiment,
    reason as require_reason,
    require_consistent_ref,
    require_line_not_checked_out,
    require_no_pending_successor,
    require_open_experiment,
    require_readable_evidence,
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
    PreparedPromotion,
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
    OUTCOME_PROMOTED,
    OUTCOME_SCHEMA_VERSION,
    REJECTED_DRAFTS_SCHEMA_VERSION,
    Batch,
    read_rejected_drafts,
)
from .replay import Evidence, History, Integration, describe_evidence, read_replays
from .revisions import (
    commit_tree,
    contains,
    create_ref,
    merge_tree,
    move_ref,
    ref_tip,
    release_ref,
    resolve_commit,
)
from .schema import definition, format_rfc3339, load_schema, validate_or_raise
from .state import atomic_write_text, single_writer_lock

RECORD_EXPERIMENT_CREATED = "experiment-created"
RECORD_TASKS_ADMITTED = "tasks-admitted"
RECORD_DRAFT_REJECTED = "draft-rejected"
RECORD_ROUND_SEALED = "round-sealed"
RECORD_EXPERIMENT_REVISED = "experiment-revised"
RECORD_EXPERIMENT_ABANDONED = "experiment-abandoned"
RECORD_EXPERIMENT_SUPERSEDED = "experiment-superseded"
RECORD_EXPERIMENT_PROMOTED = "experiment-promoted"
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
class PromotionResult:
    """A candidate carried onto the source line, and the merge unit it went as."""

    batch_id: str
    experiment_id: str
    round_number: int
    reason: str
    decided_at: str
    # The three commits and the tree, restated here because a caller reporting a
    # promotion is reporting the merge rather than the record it landed in.
    candidate_revision: str
    merge_input_revision: str
    merge_input_ref: str
    tree: str
    promotion_revision: str
    planned_targets: tuple[str, ...]
    record_path: Path
    # False when the source line already carried this merge: the promotion whose
    # records were interrupted, recognised by the commit it made rather than
    # made a second time.
    merged: bool = True
    # False when the batch outcome was already on record — the whole operation
    # redone after it had finished.
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


# --- what a redo would find ---------------------------------------------------
#
# Every operation here is redoable by being run again with the same arguments: it
# finishes whatever its interrupted run left, reports what is on record, and
# writes nothing a second time. Which state each one recognises as its own work is
# a fact two readers need — the operation, to branch on it, and the console's
# gate, which would otherwise refuse the one verb that repairs an interruption —
# so each is stated once here and asked from both sides.
#
# They answer about the state alone. Whether the arguments match what is on record
# is the operation's to check, under the lock, against the record it is holding.


def redone_admission(experiment: Experiment) -> Round | None:
    """The round a grouped admission redone would finish the task copies of.

    An attempt that has got no further than the round its own creation opened:
    the record is what made that admission real, so what may still be owed is the
    copies. A second round, or a sealed one, is an attempt with a history — and a
    fresh admission over it is the second experiment invariant 14 refuses.
    """

    round_ = experiment.open_round
    return round_ if round_ is not None and len(experiment.rounds) == 1 else None


def redone_addition(experiment: Experiment) -> Round | None:
    """The round a further admission redone would finish the task copies of.

    Any open round that has admitted something. Which drafts the redo is about is
    the selection it is given, and `add_tasks` compares that against the round's
    own tasks; what this says is that there is a recorded admission here for a
    redo to be of.
    """

    round_ = experiment.open_round
    return round_ if round_ is not None and round_.tasks else None


def redone_seal(experiment: Experiment) -> Round | None:
    """The round a seal redone would report the pin of."""

    last = experiment.last_round
    return last if last.seal is not None else None


def redone_revision(experiment: Experiment) -> Round | None:
    """The round a revision redone would report having opened.

    Exactly the shape `_redo_revise` recognises: a round this revision opened and
    nothing has been admitted into yet, standing over one whose candidate is
    pinned. A round with work in it is not a revision waiting to be finished.
    """

    last = experiment.last_round
    if last.seal is not None or last.tasks or len(experiment.rounds) < 2:
        return None
    return last if experiment.rounds[-2].candidate_revision is not None else None


def redone_decision(current: BatchLineage, outcome: str) -> Experiment | None:
    """The attempt a terminal decision redone would report having ended.

    The newest one, always: ordinals run 1..N and only the newest may be open, so
    with none open the last is what any decision here can be finishing.
    """

    if current.open_experiment is not None:
        return None
    last = current.experiments[-1] if current.experiments else None
    decision = last.decision if last is not None else None
    return last if decision is not None and decision.outcome == outcome else None


def redone_conclusion(lineage: Lineage, *, reason: str | None = None) -> BatchLineage | None:
    """The batch a no-change conclusion redone would report having ended.

    Read from the whole lineage rather than from a current batch, because there
    is none: the outcome record is what ends a batch and it lands before the
    audit line, so a run interrupted between the two left nothing current and its
    own retry has only the concluded batch to recognise itself in.

    `reason` narrows it to the conclusion a caller is redoing, which is what the
    operation asks with. The gate asks without one — which sentence a verb would
    be given is an argument, and what it needs to know is whether this state
    holds a conclusion for a redo to be of.
    """

    concluded = [
        item
        for item in lineage.batches
        if item.outcome is not None
        and item.outcome["outcome"] == OUTCOME_NO_CHANGE
        and (reason is None or item.outcome["reason"] == reason)
    ]
    return concluded[-1] if concluded else None


# --- what a fresh run refuses -------------------------------------------------
#
# The other half of the same arrangement: each condition an operation here checks
# before it writes, stated once as the refusal it would give, and asked both by
# that operation and by the console's gate (`phase.allowed_actions`). Two
# statements of one rule is how a gate comes to refuse a verb the operation
# allows, or offer one it does not.
#
# Every one of these answers about state alone — the records as they now stand.
# What belongs to the moment of the write stays with the operation: a working
# tree sitting on the ref, an admitted task copy's identity, whatever Git answers
# under the lock, and any argument the verb is given.


def prepared_promotion_refusal(experiment: Experiment) -> str | None:
    """Why an experiment with a promotion in flight is not moved out from under it.

    The one place this controller can create the split it exists to prevent. A
    prepared promotion may already be on the source line with only its records
    missing; ending the attempt retires the record that says so, and opening the
    next round leaves the prepared merge naming a round that is no longer the
    last — which is a promotion nothing can afterwards record. Either way the
    canonical line is left carrying a merge no reading explains.

    Deliberately not the boundary accepted for a replay run in flight. That one
    leaves a record nobody can conclude, which is honest and costs an unanswered
    question; this one leaves another repository's release line describing
    something that never happened.
    """

    prepared = experiment.promotion
    if prepared is None:
        return None
    return (
        f"{experiment.experiment_id} has a promotion of {prepared.revision[:12]} prepared onto "
        f"{prepared.merge_input_ref}; moving on now would retire the only record that the line may already be "
        "carrying it — promote finishes that promotion, or discards it once the line proves it never arrived, "
        "and this is available afterwards"
    )


def second_attempt_refusal(current: BatchLineage, experiment: Experiment) -> str:
    """Why a grouped admission is not a second attempt over an open one.

    Names what is open rather than what was asked for, since the open attempt is
    what has to be dealt with either way (invariant 14).
    """

    last = experiment.last_round
    state = "open" if experiment.open_round is not None else "candidate-ready"
    return (
        f"{current.batch_id} already has an open experiment ({experiment.experiment_id}, round {last.number} "
        f"{state}, drafts {sorted(task.draft_id for task in last.tasks)}); invariant 14 allows one at a time — "
        "admit further drafts into its open round, or end it with a decision, which keeps it as evidence and "
        "frees the batch for an alternative"
    )


def admission_refusal(current: BatchLineage) -> str | None:
    """Why there is no attempt to admit further drafts into.

    Its own sentence rather than the shared one, because what it points at is the
    verb that would create the attempt rather than the decision that ended the
    last.
    """

    return None if current.open_experiment is not None else _no_attempt_to_admit_into(current)


def _no_attempt_to_admit_into(current: BatchLineage) -> str:
    return (
        f"{current.batch_id} has no open experiment to admit into; a grouped admission creates one from the "
        "drafts it selects, and every experiment of this batch starts from the base its first one froze"
    )


def open_round_refusal(experiment: Experiment) -> str | None:
    """Why a candidate-ready round takes no further work (invariant 16)."""

    return None if experiment.open_round is not None else _candidate_ready(experiment)


def _candidate_ready(experiment: Experiment) -> str:
    last = experiment.last_round
    return (
        f"round {last.number} of {experiment.experiment_id} is candidate-ready at "
        f"{(last.candidate_revision or '')[:12]}; its evidence names that pinned revision, so further work "
        "opens the next round rather than changing what was already measured (invariant 16)"
    )


def sealable_refusal(experiment: Experiment) -> str | None:
    """Why a round is not one to pin a candidate from.

    Two states, and neither is the one this verb's redo recognises: a round whose
    candidate is already pinned is reported back rather than refused
    (`redone_seal`), and what is left is a round that has not opened at all or
    one that has admitted nothing.
    """

    round_ = experiment.open_round
    if round_ is None:
        last = experiment.last_round
        return (
            f"round {last.number} of {experiment.experiment_id} is already sealed at "
            f"{(last.candidate_revision or '')[:12]}"
        )
    if round_.tasks:
        return None
    return (
        f"round {round_.number} of {experiment.experiment_id} has admitted nothing; a round is the task "
        "set admitted into it and the candidate that set produced, so sealing an empty one would pin a "
        "revision pass no proposal accounts for — admit the drafts this round needs, or end the attempt "
        "with a decision"
    )


def revisable_refusal(experiment: Experiment) -> str | None:
    """Why the next round is not opened over a round that is still open.

    A round nothing has been admitted into is this verb's own redo and is not
    refused here (`redone_revision`); the operation tells the two apart by the
    reason it is given, which is an argument rather than a state.
    """

    last = experiment.last_round
    if last.seal is not None or redone_revision(experiment) is not None:
        return None
    return _round_still_open(experiment)


def _round_still_open(experiment: Experiment) -> str:
    last = experiment.last_round
    return (
        f"round {last.number} of {experiment.experiment_id} is still open; a revision appends to a round whose "
        "candidate is already pinned, so seal this one first — the previous round's evidence is what a revision "
        "makes stale, and a round nothing measured leaves the next one revising nothing (invariant 16)"
    )


def gate_refusal(current: BatchLineage, action: str) -> str | None:
    """Why a batch does not end while a proposal is still waiting at its gate.

    The gate belongs to the batch, so an outcome recorded over a draft nobody
    decided leaves that proposal at a gate that no longer exists: this batch's
    own analysis said a change was warranted, and the answer is now unreachable.
    Both ways of giving one are terminal and either is an answer — admit it into
    an attempt, or decline it.
    """

    if not current.gate.waiting:
        return None
    return (
        f"{current.batch_id} still has draft(s) {list(current.gate.waiting)} waiting at its admission gate; "
        f"{action} ends the batch and the gate with it, leaving a proposal its own analysis made with nobody "
        "left to decide it — admit them or decline them, both of which are terminal"
    )


def conclusion_refusal(current: BatchLineage) -> str | None:
    """Why a batch cannot conclude `no-change`.

    Three different ways it can be, and each of them contradicts the conclusion
    rather than merely preceding it:

    - an open experiment is an attempt at a change, and an outcome is recorded
      after the last attempt ends, never over one that is running;
    - a promoted attempt says the source line moved, which is the same
      contradiction the reader refuses from the other side — that batch
      concluded by promoting, and the record has to say so;
    - a draft still waiting is a proposal the analysis made and nobody decided,
      so "the evidence justified no change" is a claim this batch's own gate
      does not support.
    """

    if current.open_experiment is not None:
        return (
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
        return (
            f"{current.batch_id} cannot conclude {OUTCOME_NO_CHANGE!r}: {promoted} record a promotion; a batch "
            "whose candidate reached the source line concluded by promoting it, and the outcome names which "
            "attempt it was and the revision that carries it"
        )
    return gate_refusal(current, "concluding that the evidence justified no change")


def in_flight_refusal(experiment: Experiment, history: History) -> str | None:
    """Why nothing is promoted while something is still being measured against it.

    A promotion is terminal for the experiment, and that is what makes this more
    than tidiness: afterwards there is no open experiment, so nothing can
    conclude a run, end one, or withdraw a request against it — the harness goes
    on with work no operation here can ever answer for. Every other terminal
    decision has the same boundary and keeps it, because abandoning or
    superseding is what an operator reaches for when something has gone wrong.
    This one is reached when everything went right, and the run that is going or
    the request that is outstanding is one command from being settled.

    Promotable evidence says nothing about either: a second run started beside a
    result that is still exact leaves it promotable by design, and the reader
    deliberately reports no outstanding request in that reading. So the question
    is asked of the record here rather than read off the evidence.
    """

    pending = history.pending
    if pending is not None:
        return (
            f"{experiment.experiment_id} has the replay request for round {pending.round_number} attempt "
            f"{pending.attempt} outstanding, so a run may be going that this record does not name yet; a "
            "promotion ends the experiment and with it every operation that could answer for that run — start "
            "the replay again to record what the harness began, or withdraw the request"
        )
    going = history.replays[-1] if history.replays else None
    if going is None or not going.running:
        return None
    return (
        f"round {going.round_number} attempt {going.attempt} of {experiment.experiment_id} is still running "
        f"under {going.harness.id} handle {going.harness.handle!r}; a promotion ends the experiment, and "
        "nothing could afterwards conclude that run or record why it stopped — conclude it, or end it, first"
    )


def promotable_refusal(experiment: Experiment, evidence: Evidence | None) -> str | None:
    """Why the round's evidence does not describe the tree a promotion would carry.

    The whole gate, and it is the reader's answer rather than a second opinion
    formed here: `promotable` is one completed run of this round whose merge
    input this checkout confirms has not moved. What it refuses it refuses in the
    words the reader already has for it, so an operator meets one explanation of
    stale evidence whether they asked `status` or asked for a promotion.

    Evidence is derived for an open experiment's current round, so the operation
    never asks this without one; None is a reading's own absence — no experiment
    is open — and answers it as the thing a promotion is missing.
    """

    if evidence is None:
        return (
            f"nothing has replayed a round of {experiment.experiment_id}; what reaches the source line is a "
            "tree a replay measured and still describes (invariant 10)"
        )
    if evidence.promotable:
        return None
    notes = list(evidence.drift) + list(evidence.unverified)
    return (
        f"round {evidence.round_number} of {experiment.experiment_id} is {evidence.state} and cannot be "
        f"promoted: {notes or ['nothing has measured it']}; what reaches the source line is a tree a replay "
        "measured and still describes (invariant 10), so replay this round as it now stands and promote that"
    )


def unfinished_promotion_refusal(experiment: Experiment) -> str | None:
    """Why a promotion interrupted before its batch outcome cannot be finished.

    One state and one only: a record from a build that kept no prepared merge
    (`experiment.json` v1). The outcome that ends the batch states the merge unit
    and the targets it was planned for, and neither was ever written down.
    """

    return None if experiment.promotion is not None else _no_merge_unit(experiment)


def _no_merge_unit(experiment: Experiment) -> str:
    decision = experiment.decision
    promoted = (decision.promotion_revision or "") if decision is not None else ""
    return (
        f"{experiment.experiment_id} was promoted as {promoted[:12]} by a build that "
        "recorded no merge unit on the experiment, and the outcome that would end this batch states the "
        "merge unit and the targets that promotion was planned for; the plan was never recorded, so this "
        "batch cannot be concluded from what is on record rather than from what a later run supposes"
    )


def base_release_refusal(current: BatchLineage, owed: assessment.Obligation | None) -> str | None:
    """Why a first base is not frozen before the release behind it is judged.

    Invariant 17. The reading it waits on is the *owning* cohort's, which is the
    first batch frozen after that promotion and not necessarily this one; the
    caller resolves that obligation (`assessment.obligation`) and hands it here.

    Only where a base is actually being frozen. A later experiment of the same
    batch takes the frozen commit rather than resolving one, so asking again
    there would gate work on a decision that can no longer change what it starts
    from — and a batch with no promotion anywhere before it is not waiting on
    anything.
    """

    if current.base_revision is not None or owed is None:
        return None
    subject = owed.frame.subject
    owner = "" if owed.owner_id == current.batch_id else f"{owed.owner_id}, the cohort frozen after it, "
    reading = owed.reading
    if reading is None:
        return (
            f"{owner or current.batch_id + ' '}has recorded no reading of the {subject.batch_id} release "
            f"({subject.revision[:12]}), so {current.batch_id} has nothing to freeze a base against; that "
            "cohort's reports are the first evidence of what the release did, and the commit every experiment "
            "of this batch starts from is either the line carrying it or the line with it taken back out "
            "(invariant 17)"
        )
    if reading.settled:
        return None
    return (
        f"{owner or current.batch_id + ' '}reads the {subject.batch_id} release {reading.verdict!r} and nobody "
        f"has settled it, so {current.batch_id}'s base cannot be frozen yet; the settlement is what says whether "
        f"the release stays on the line every alternative here is built from — {assessment.SETTLEMENT_RETAIN!r} "
        f"keeps it, {assessment.SETTLEMENT_ROLLED_BACK!r} takes it back off first, and either way the base is "
        "frozen afterwards (invariant 17)"
    )


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

    Where a base is being frozen, the release before this batch has to have been
    judged first (invariant 17): what that decision settles is whether the
    commit every alternative here starts from carries the previous release or the
    reversal of it.
    """

    moment = _moment(now)
    with single_writer_lock(config):
        known = settled(config, now=moment)
        current = current_cycle(config, now=moment, known=known)
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

        settlement = _require_release_settled(config, known, current)
        base_revision, base_release_ref = _base_revision(config, current, base)
        _require_settled_line(config, settlement, base_revision, base)
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
            raise BatchError(_no_attempt_to_admit_into(current))
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
    text = require_reason(
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
        pinned = redone_seal(experiment)
        if pinned is not None and pinned.seal is not None:
            return SealResult(
                batch_id=current.batch_id,
                experiment_id=experiment.experiment_id,
                round_number=pinned.number,
                candidate_revision=pinned.seal.candidate_revision,
                sealed_at=pinned.seal.sealed_at,
                sealed=False,
            )
        # Every remaining state of the round, in the words the gate reads off the
        # same predicate; the already-sealed one it also answers for was returned
        # just above as this operation's own redo.
        refusal = sealable_refusal(experiment)
        if refusal is not None:
            raise BatchError(refusal)

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
    text = require_reason(
        reason,
        "revising records why; the reason is what the next round's evidence is read against, and a revision "
        "with none says only that somebody was dissatisfied",
    )

    with single_writer_lock(config):
        current = current_cycle(config, now=moment)
        experiment = require_open_experiment(current, "revise")
        require_consistent_ref(current)
        # The other operation that can move an experiment out from under a
        # promotion in flight: opening the next round leaves the prepared merge
        # naming a round that is no longer the last, which is a promotion nothing
        # could afterwards record — while the merge may already be on the line.
        _require_no_prepared_promotion(experiment)

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

    opened = redone_revision(experiment)
    pinned = experiment.rounds[-2].candidate_revision if opened is not None else None
    if opened is not None:
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
    raise BatchError(_round_still_open(experiment))


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
    text = require_reason(
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
        _require_no_prepared_promotion(experiment)
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


def _require_no_prepared_promotion(experiment: Experiment) -> None:
    """An experiment with a promotion in flight is not moved out from under it
    (`prepared_promotion_refusal`)."""

    refusal = prepared_promotion_refusal(experiment)
    if refusal is not None:
        raise BatchError(refusal)


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
    promotion_revision: str | None = None,
) -> Experiment:
    """Write the terminal decision onto an experiment's record.

    It states none of the rules about which decision is available when, and that
    is deliberate: the record is published through the reader's own parse, so
    `promoted` from a round nobody sealed, a successor that is not the next
    ordinal, and a field paired with the wrong outcome are all refused by the
    same code that refuses them on the way back in. `promote` writes its decision
    through here for exactly that reason — it states which revision reached the
    source line and inherits the rest of the rules rather than restating them.

    It does not take the hold that keeps its own reading true, and each caller
    does: `_end_attempt` wraps this in `_unmoved` on the ref it is ending over,
    and so does `promote`. A decision is the last reading anyone takes of an
    experiment's ref, so a writer reaching this from a path of its own has to
    arrive holding it.
    """

    decided = replace(
        experiment,
        decision=Decision(
            outcome=outcome,
            decided_at=decided_at,
            reason=reason,
            superseded_by=successor.experiment_id if successor is not None else None,
            promotion_revision=promotion_revision,
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
    if redone_decision(current, outcome) is None or decision.reason != reason:
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


def promote(
    config: EvolutionConfig,
    *,
    reason: str,
    targets: Iterable[str],
    now: datetime | None = None,
) -> PromotionResult:
    """Carry the open experiment's replayed candidate onto the source line, and
    end the batch with it (invariants 9 and 10).

    What is promoted is a *tree*, not a pair of commits. The merge commit this
    writes carries the tree the replay measured, with the source line as it stood
    and the round's pinned candidate as its parents — so what reaches the line is
    the thing evidence exists about, rather than whatever a merge run here would
    produce today. The merge is recomputed all the same, and a result differing
    from the measured tree refuses: that is the difference between promoting the
    exercised integration and promoting two commits that once produced it.

    The gate is the replay reading and nothing softer. `promotable` means a
    completed run for this round whose merge input this checkout confirms has not
    moved — so an unsealed round, a superseded candidate, a source line that
    moved, a run still going, a failed run, and a check this clone could not make
    all refuse here, in the reader's own words. Work still in flight refuses too,
    and for a reason of its own: this decision ends the experiment, after which
    nothing can conclude a run or withdraw a request against it ever again. Both
    are asked where a promotion is *made*. A run finishing one already prepared
    asks neither, because by then the answer would be about what this operation
    itself did to the source line.

    Three writes make it real and they are made in one order, because a
    promotion is the one operation here that changes another repository's line
    and cannot take that back. The merge commit is written first and named by
    nothing, so a run stopping there leaves an orphan Git collects. Then the
    *prepared promotion* is recorded on the experiment — the merge unit and the
    exact commit — because everything after this point is recoverable only by a
    run that can say which commit was this operation's. Then the source-line ref
    moves, compare-and-swap from the revision the replay integrated onto, under
    Git's own lock.

    After that move the promotion exists in the world whatever happens to this
    process, and this experiment's evidence is stale from then on — including for
    the run that comes back to finish it. So a second run asks the prepared
    record first and Git second: the promotion is on the line if that exact
    commit is, which stays true when the line has taken further commits since,
    and a promotion that never got there is discarded rather than guessed at. The
    line is deliberately *not* held while the records land — the promotion is a
    fact about a commit, not about where a branch happens to stand afterwards,
    and holding it would turn an ordinary advance into a promotion nobody can
    finish. The experiment's ref is held, for the reason every terminal decision
    holds it: that reading does not survive the write it justifies.

    It promises nothing about deployment. `planned_targets` is what the operator
    intends to redeploy, recorded as the plan it is; what any target actually
    holds is read from that target's own receipt and never from here.
    """

    moment = _moment(now)
    text = require_reason(
        reason,
        "a promotion records why the evidence justified putting this candidate on the source line; the reason is "
        "what a later reader has instead of the conversation that produced it, and it is what the cohort measuring "
        "the result is read against",
    )
    planned = _planned_targets(config, targets)

    with single_writer_lock(config):
        lineage = settled(config, now=moment)
        current = lineage.current
        if current is None:
            return _redo_promotion(lineage, text, planned)
        require_stage_ended(config, current)
        require_no_pending_successor(current)
        # The preamble is assembled here rather than inherited from
        # `current_cycle`, for `conclude_no_change`'s reason: a promotion is what
        # stops a batch being current, so the state its own redo starts from is
        # one where nothing is.
        require_readable_evidence(config, current)

        stamp = format_rfc3339(moment)
        experiment = current.open_experiment
        if experiment is None:
            return _finish_promotion(config, current, text, planned)
        require_consistent_ref(current)
        _require_gate_settled(current, "promoting")

        outstanding = experiment.promotion
        if outstanding is None:
            integration = _promotable_integration(config, experiment, read_replays(config, experiment))
            experiment, prepared = _prepare_promotion(config, current, experiment, integration, text, planned, at=stamp)
        else:
            _require_same_promotion(experiment, outstanding, text, planned)
            prepared = outstanding
        standing = _standing(config, experiment, prepared)
        if standing == _LOST:
            _discard_promotion(config, experiment, prepared)
        if standing == _UNMOVED:
            _land(config, experiment, prepared)

        with _unmoved(
            config,
            experiment,
            current.ref.tip if current.ref is not None else None,
            "a promotion is a terminal decision, and the last reading anyone takes of that ref",
        ):
            _decide(
                config,
                experiment,
                DECISION_PROMOTED,
                prepared.reason,
                decided_at=stamp,
                successor=None,
                promotion_revision=prepared.revision,
            )
            append_records(
                config,
                [
                    build_record(
                        RECORD_EXPERIMENT_PROMOTED,
                        recorded_at=stamp,
                        batch_id=current.batch_id,
                        experiment_id=experiment.experiment_id,
                        round=prepared.round_number,
                        revision=prepared.revision,
                    )
                ],
            )
        path = _conclude_promoted(config, current, experiment, prepared, at=stamp)

    return _promoted(current, experiment, prepared, decided_at=stamp, path=path, merged=outstanding is None)


def _promoted(
    current: BatchLineage,
    experiment: Experiment,
    prepared: PreparedPromotion,
    *,
    decided_at: str,
    path: Path,
    merged: bool,
    recorded: bool = True,
) -> PromotionResult:
    """A promotion reported from the record it was prepared as.

    One builder for every way this operation ends, so what a caller is told about
    a promotion it finished is what it would have been told about the one that
    made it — the values come from the same record either way.
    """

    return PromotionResult(
        batch_id=current.batch_id,
        experiment_id=experiment.experiment_id,
        round_number=prepared.round_number,
        reason=prepared.reason,
        decided_at=decided_at,
        candidate_revision=prepared.candidate_revision,
        merge_input_revision=prepared.merge_input_revision,
        merge_input_ref=prepared.merge_input_ref,
        tree=prepared.tree,
        promotion_revision=prepared.revision,
        planned_targets=prepared.planned_targets,
        record_path=path,
        merged=merged,
        recorded=recorded,
    )


def _promotable_integration(
    config: EvolutionConfig,
    experiment: Experiment,
    history: History,
) -> Integration:
    """The integration this promotion may carry, or the refusal that none may be.

    Every check a fresh promotion makes before anything is written, and it is
    only ever reached by one: a run finishing a promotion that is already
    prepared must not come through here at all. A promotion moves the merge
    input, so from the moment the ref moves this experiment's evidence is stale
    forever — asking the evidence again would refuse the operation on the
    strength of what the operation itself did.
    """

    _require_nothing_in_flight(experiment, history)
    evidence = describe_evidence(config, experiment)
    _require_promotable(experiment, evidence)
    integration = evidence.replay.integration
    if integration.merge_input_ref is None:
        # Unreachable through `promotable`, which is a reading about a named ref
        # this checkout resolved; stated because what follows records that name
        # as the line it will move.
        raise BatchError(
            f"{experiment.experiment_id}: the run justifying this promotion integrated onto a detached revision, "
            "so there is no source line to move"
        )
    require_line_not_checked_out(config, integration.merge_input_ref, "a promotion")
    _require_measured_tree(config, experiment, integration)
    return integration


def _require_nothing_in_flight(experiment: Experiment, history: History) -> None:
    """Nothing is being measured against this experiment when it is promoted
    (`in_flight_refusal`)."""

    refusal = in_flight_refusal(experiment, history)
    if refusal is not None:
        raise BatchError(refusal)


def _require_promotable(experiment: Experiment, evidence: Evidence) -> None:
    """The round's evidence describes the tree this promotion would carry
    (`promotable_refusal`)."""

    refusal = promotable_refusal(experiment, evidence)
    if refusal is not None:
        raise BatchError(refusal)


def _require_measured_tree(config: EvolutionConfig, experiment: Experiment, integration: Integration) -> None:
    """Merging now produces the tree the run measured.

    The evidence has already established that the two commits are the ones the
    run integrated. This asks the question that pair cannot answer on its own:
    whether merging them *here* still produces the tree that was exercised. A
    checkout merging differently — another strategy, another normalization, a Git
    that resolves this differently — would put a tree nobody measured on the
    source line while every recorded revision agreed.
    """

    merged = merge_tree(
        config.repo_root,
        integration.merge_input_revision,
        integration.candidate_revision,
    )
    if merged.tree == integration.tree:
        return
    found = merged.tree[:12] if merged.tree is not None else f"no tree at all ({merged.complaint})"
    raise BatchError(
        f"{experiment.experiment_id}: integrating {integration.candidate_revision[:12]} onto "
        f"{integration.merge_input_revision[:12]} produces {found} here, and the run that justifies this "
        f"promotion measured {integration.tree[:12]}; what is promoted is the tree that was exercised, so a "
        "checkout that merges these two commits differently promotes nothing from this evidence"
    )


def _require_same_promotion(
    experiment: Experiment,
    prepared: PreparedPromotion,
    reason: str,
    planned: tuple[str, ...],
) -> None:
    """A promotion is finished as it was prepared.

    The prepared record is the whole of what this operation is — the candidate,
    the line, the tree, the reason it was justified by, and the targets it was
    planned for — so a second run naming any of it differently is asking for a
    different promotion of a candidate that already has one on the way. Refused
    naming what is outstanding, the way a replay refuses a request arriving
    against the one it is already holding: the operator either redoes this
    promotion or says which of the two they meant.

    Reason and targets are the only parts a caller supplies. The rest was read
    from the evidence and cannot differ without the line itself having changed,
    which is `_standing`'s question rather than this one.
    """

    if prepared.reason == reason and prepared.planned_targets == planned:
        return
    raise BatchError(
        f"{experiment.experiment_id} has a promotion of {prepared.revision[:12]} prepared for {prepared.reason!r} "
        f"planning {list(prepared.planned_targets)}, and this request is {reason!r} planning {list(planned)}; a "
        "promotion is prepared once and finished as it was prepared, so redo it with what is outstanding rather "
        "than end the batch on a plan the promotion was not made under"
    )


# Where the source line stands relative to a prepared promotion. These three are
# what a run finishing an interrupted promotion has to tell apart, and the whole
# reason the merge commit is recorded before the ref moves.
_LANDED = "landed"
_UNMOVED = "unmoved"
_LOST = "lost"


def _standing(config: EvolutionConfig, experiment: Experiment, prepared: PreparedPromotion) -> str:
    """Whether the prepared merge reached the source line, is still waiting to,
    or never will.

    The identity is the recorded commit and nothing weaker. A commit's *shape* —
    those two parents, that tree — is not it: a merge made by hand has the same
    shape, and reading shapes adopts it as a promotion nobody performed. The
    ref's *tip* is not it either: a line that took another commit after the
    promotion still carries the promotion, and a run that could not see that
    would be unable to finish work it had already done. So the question is
    ancestry — is this commit on that line — which is what those two get wrong in
    opposite directions.

    `_LOST` is a line standing somewhere the prepared merge is not, which is
    somebody else's commit arriving between the evidence and the move. Refusing
    to answer is the fourth case and stays a refusal: a checkout that cannot
    resolve the line, or cannot compare the two commits, does not know whether a
    promotion happened, and both things it could do about that are wrong.
    """

    ref = prepared.merge_input_ref
    tip = ref_tip(config.repo_root, ref)
    if tip is None:
        raise BatchError(
            f"{experiment.experiment_id} has a promotion of {prepared.revision[:12]} prepared onto {ref}, and "
            "this checkout does not hold that ref; whether the promotion reached the source line is what says "
            "whether this finishes one or makes one, so finish it from a checkout that has the line"
        )
    if tip == prepared.revision:
        return _LANDED
    if tip == prepared.merge_input_revision:
        return _UNMOVED
    carried = contains(config.repo_root, prepared.revision, tip)
    if carried is None:
        raise BatchError(
            f"{experiment.experiment_id}: whether {prepared.revision[:12]} is on {ref} (now at {tip[:12]}) cannot "
            "be answered in this checkout, which does not hold both commits; that answer is what says whether "
            "this promotion happened, so nothing is written until a checkout holding them says so"
        )
    return _LANDED if carried else _LOST


def _prepare_promotion(
    config: EvolutionConfig,
    current: BatchLineage,
    experiment: Experiment,
    integration: Integration,
    reason: str,
    planned: tuple[str, ...],
    *,
    at: str,
) -> tuple[Experiment, PreparedPromotion]:
    """Make the merge commit, and record it as this promotion before it is used.

    The commit first, because a commit nothing names is inert and collectable, so
    a run stopping there has done nothing at all. The record second and the ref
    move third: the record is what makes that commit *this operation's*, and
    without it a run interrupted around the move would have to recognise its own
    work by shape and could not tell it from a merge somebody made by hand.

    The order also settles what a retry may re-read, which is nothing. The
    recorded merge input is the revision the retry moves from — never a fresh
    reading of the line — because the two can differ, and a retry that re-pinned
    the line would put a tree measured against the old one onto the new.
    """

    revision, complaint = commit_tree(
        config.repo_root,
        integration.tree,
        [integration.merge_input_revision, integration.candidate_revision],
        _promotion_message(current, experiment, integration, reason),
    )
    if revision is None:
        raise BatchError(
            f"{experiment.experiment_id} has nothing to promote with: {complaint}; the merge unit is a commit "
            "carrying the measured tree, and it is made by whoever promotes — a checkout Git will not commit in "
            "is one this promotion cannot be recorded from"
        )
    prepared = PreparedPromotion(
        round_number=experiment.last_round.number,
        candidate_revision=integration.candidate_revision,
        merge_input_revision=integration.merge_input_revision,
        merge_input_ref=integration.merge_input_ref,
        tree=integration.tree,
        revision=revision,
        reason=reason,
        planned_targets=planned,
        prepared_at=at,
    )
    written = replace(experiment, promotion=prepared)
    _write_record(config, written)
    return written, prepared


def _land(config: EvolutionConfig, experiment: Experiment, prepared: PreparedPromotion) -> None:
    """Move the source line to the prepared merge, or refuse in Git's own words.

    A compare-and-swap from the revision the replay integrated onto: what
    advances a release line is ordinary Git, which the single-writer lock does
    not cover, so the reading and the write are one operation rather than a look
    followed by a leap. A line that took a commit in between refuses here instead
    of carrying a tree nobody measured.

    The worktree question is asked again rather than inherited from the gate: a
    run finishing an interrupted promotion never went through that gate, and
    between the two runs somebody may have checked the line out.
    """

    require_line_not_checked_out(config, prepared.merge_input_ref, "a promotion")
    moved = move_ref(config.repo_root, prepared.merge_input_ref, prepared.revision, prepared.merge_input_revision)
    if moved is None:
        return
    raise BatchError(
        f"{prepared.merge_input_ref} did not take {prepared.revision[:12]}: {moved}; the source line is moved "
        f"from the commit the replay integrated onto ({prepared.merge_input_revision[:12]}) and from no other, "
        f"so {experiment.experiment_id} is replayed against where the line now stands and promoted from that"
    )


def _discard_promotion(
    config: EvolutionConfig,
    experiment: Experiment,
    prepared: PreparedPromotion,
) -> NoReturn:
    """Give up a prepared promotion that demonstrably never reached the line.

    The one state a prepared promotion is dropped from, and dropping it is safe
    precisely because the state is demonstrable: the merge is not on the line and
    the line no longer stands where the merge was to be made from, so nothing
    anywhere carries it and the commit is an unreferenced object Git collects.
    Keeping it would shut this experiment out of every operation that refuses
    over an unfinished promotion, on behalf of one that can never be finished —
    the line has moved, so the evidence behind it describes a tree that is no
    longer the one in question.

    Written and then refused rather than written and retried: a promotion from
    here needs evidence measured against the line as it now stands, which is a
    replay and not something this operation may do on the operator's behalf.
    """

    _write_record(config, replace(experiment, promotion=None))
    raise BatchError(
        f"{experiment.experiment_id}: the promotion prepared as {prepared.revision[:12]} never reached "
        f"{prepared.merge_input_ref}, which no longer stands at the {prepared.merge_input_revision[:12]} it was "
        "to be made from; that prepared promotion is discarded, and the evidence behind it now describes a line "
        "that has moved on — replay this round as the line stands and promote that"
    )


def _promotion_message(
    current: BatchLineage,
    experiment: Experiment,
    integration: Integration,
    reason: str,
) -> str:
    """What the merge commit says about itself.

    Every value in it is already in the records this operation writes; the point
    is that the commit is legible from Git alone, on a line whose readers are not
    running this controller. Nothing imported goes in it — the reason is the
    operator's own sentence, and the ledger's rule about sanitized content is the
    rule here (invariant 11).
    """

    return (
        f"evolution: promote {experiment.experiment_id} round {experiment.last_round.number}\n"
        "\n"
        f"Candidate {integration.candidate_revision} replayed as integrated onto\n"
        f"{integration.merge_input_revision} ({integration.merge_input_ref}).\n"
        f"Batch: {current.batch_id}\n"
        f"Reason: {reason}\n"
    )


def _conclude_promoted(
    config: EvolutionConfig,
    current: BatchLineage,
    experiment: Experiment,
    prepared: PreparedPromotion,
    *,
    at: str,
) -> Path:
    """The batch outcome a promotion ends its cycle with.

    `conclude_no_change`'s counterpart, and the two are one set: this one names
    the experiment, the revision that carries it, and the merge unit it went as,
    where that one names none of them because there was nothing to name. Which
    of the two a batch got is what every later reading of its lineage is checked
    against.

    Every value comes from the prepared promotion rather than from the caller,
    which is what makes the record state the promotion that happened. The plan a
    promotion was made under was named when it was prepared; a run finishing an
    interrupted one has no standing to name a different one, and one that could
    would write a plan nobody promoted under as though it were the original.
    """

    record = {
        "schema_version": OUTCOME_SCHEMA_VERSION,
        "batch_id": current.batch_id,
        "outcome": OUTCOME_PROMOTED,
        "decided_at": at,
        "reason": prepared.reason,
        "experiment_id": experiment.experiment_id,
        "promotion_revision": prepared.revision,
        "promotion": {
            "round": prepared.round_number,
            "candidate_revision": prepared.candidate_revision,
            "merge_input_revision": prepared.merge_input_revision,
            "merge_input_ref": prepared.merge_input_ref,
            "tree": prepared.tree,
            "planned_targets": list(prepared.planned_targets),
        },
    }
    path = _write_outcome(config, current, record)
    append_records(
        config,
        [
            build_record(
                RECORD_BATCH_CONCLUDED,
                recorded_at=at,
                batch_id=current.batch_id,
                experiment_id=experiment.experiment_id,
                revision=prepared.revision,
                detail=OUTCOME_PROMOTED,
            )
        ],
    )
    return path


def _finish_promotion(
    config: EvolutionConfig,
    current: BatchLineage,
    reason: str,
    planned: tuple[str, ...],
) -> PromotionResult:
    """A promotion whose decision landed and whose outcome did not, finished.

    The decision is what makes the promotion real and the outcome is what ends
    the batch, so between them is a batch still current with no experiment open —
    a state nothing else may be written over and this operation's own redo. What
    it writes is the outcome the interrupted run would have, and it writes it
    from the promotion's own record: the merge unit, the reason, and the plan it
    was prepared under are all there, so nothing this run was called with can
    become part of a decision that was made before it.

    The audit line for the decision is not written again. It may have landed and
    nothing can tell, and an audit is not state — the settled rule for every redo
    here, and the reason each line is appended beside its own record rather than
    both at the end.
    """

    last = current.experiments[-1] if current.experiments else None
    decision = last.decision if last is not None else None
    if last is None or decision is None or decision.outcome != DECISION_PROMOTED:
        raise no_open_experiment(current, "promote")
    prepared = last.promotion
    if prepared is None:
        # A version-1 record, whose promotion recorded the revision alone and
        # stopped before the outcome. The merge unit is recoverable from the
        # evidence and the commit, and the plan it was made under is recoverable
        # from nowhere: it was never written down. Taking this run's targets for
        # it would write a plan nobody promoted under as though it were the
        # original — the one thing the outcome is trusted to state about intent
        # — so the state is reported instead of guessed at (design: fail closed
        # on missing provenance a decision needs).
        raise BatchError(_no_merge_unit(last))
    if decision.reason != reason:
        raise BatchError(
            f"{last.experiment_id} already ended as {decision.outcome!r} ({decision.reason!r}); a decision is "
            "recorded once and never edited, so redo the same one to finish an interrupted promotion — the "
            "batch it ends is concluded by the same reason it was promoted for"
        )
    _require_same_promotion(last, prepared, reason, planned)
    # The moment the decision was made, not the moment this finished it: one
    # promotion, and its two records state when it happened rather than when each
    # of them was written.
    path = _conclude_promoted(config, current, last, prepared, at=decision.decided_at)
    return _promoted(current, last, prepared, decided_at=decision.decided_at, path=path, merged=False)


def _redo_promotion(lineage: Lineage, reason: str, planned: tuple[str, ...]) -> PromotionResult:
    """The same promotion run again after it finished, or the refusal that
    nothing is current.

    The outcome record is what ends the batch, and the audit line follows it — so
    a run interrupted between the two left no batch current, and its own retry
    would otherwise report that there is nothing to promote. What it reports
    instead is the batch that retry ended, which is the *newest* one and no
    other: every batch before it was ended by an operation of its own, and a
    reason repeated across two cohorts would otherwise fetch back a promotion
    from a cycle that closed long ago and report it as this request's work.

    Bound to the whole request rather than to the reason alone, for the reason
    `_finish_promotion` is: a promotion is identified by what it was made under,
    and a human sentence is evidence rather than the identity of an operation.
    """

    latest = lineage.batches[-1] if lineage.batches else None
    outcome = latest.outcome if latest is not None else None
    if latest is None or outcome is None or outcome["outcome"] != OUTCOME_PROMOTED:
        raise BatchError(
            "no batch is current, so there is nothing to promote; a batch is current from the freeze of its "
            "manifest until its outcome is recorded (invariant 14), and freezing the next cohort is "
            "`aii-2 evolution start`"
        )
    merge = outcome["promotion"]
    if outcome["reason"] != reason or tuple(merge["planned_targets"]) != planned:
        raise BatchError(
            f"no batch is current, so there is nothing to promote; the newest, {latest.batch_id}, was promoted "
            f"for {outcome['reason']!r} planning {merge['planned_targets']}, which is not this request "
            f"({reason!r} planning {list(planned)}) redone — freezing the next cohort is `aii-2 evolution start`"
        )
    return PromotionResult(
        batch_id=latest.batch_id,
        experiment_id=outcome["experiment_id"],
        round_number=merge["round"],
        reason=reason,
        decided_at=outcome["decided_at"],
        candidate_revision=merge["candidate_revision"],
        merge_input_revision=merge["merge_input_revision"],
        merge_input_ref=merge["merge_input_ref"],
        tree=merge["tree"],
        promotion_revision=outcome["promotion_revision"],
        planned_targets=tuple(merge["planned_targets"]),
        record_path=latest.batch.outcome_path,
        merged=False,
        recorded=False,
    )


def _planned_targets(config: EvolutionConfig, targets: Iterable[str]) -> tuple[str, ...]:
    """The targets this promotion is intended for, checked before anything moves.

    Checked here rather than at the write, because everything a promotion does is
    irreversible by the time a record is validated: a name this record cannot
    hold would otherwise be discovered with the merge already on the source line.
    The shape comes from the contract's own statement about the field — a name,
    never a machine-local path — rather than a pattern restated here.

    A repeated name is refused rather than folded away: two entries for one target
    say nothing a single one does not, and a record listing it twice reads as a
    plan somebody made twice.
    """

    planned = tuple(str(name).strip() for name in targets)
    repeated = sorted({name for name in planned if planned.count(name) > 1})
    if repeated:
        raise BatchError(
            f"{repeated} named more than once as a planned target; a promotion plans a target once, and a list "
            "that repeats one says nothing the single entry does not"
        )
    validate_or_raise(
        list(planned),
        definition(load_schema(config.schema_path(OUTCOME_SCHEMA_FILENAME)), "promotion")["properties"][
            "planned_targets"
        ],
        description="the targets planned for this promotion",
    )
    return planned


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
    text = require_reason(
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
        # This operation assembles the preamble itself, so the reading
        # `current_cycle` makes is restated rather than inherited: a conclusion
        # is what stops a batch being current, and one written over an
        # unreadable replay record would take that record out of every later
        # reading along with the batch.
        require_readable_evidence(config, current)
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
            "promotion": None,
        }
        _write_outcome(config, current, record)
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


def _write_outcome(config: EvolutionConfig, current: BatchLineage, record: Mapping[str, Any]) -> Path:
    """Publish the record that ends a batch, through the schema that reads it.

    One writer for both conclusions, so the two ways a cycle ends cannot drift
    into two shapes of record — and so the pairing rule `read_outcome` enforces
    on the way in is met by whichever of them wrote it.
    """

    validate_or_raise(
        record,
        load_schema(config.schema_path(OUTCOME_SCHEMA_FILENAME)),
        description=f"batch outcome record for {current.batch_id}",
    )
    atomic_write_text(current.batch.outcome_path, _json(record))
    return current.batch.outcome_path


def _require_gate_settled(current: BatchLineage, action: str) -> None:
    """No proposal is still waiting when a batch ends (`gate_refusal`)."""

    refusal = gate_refusal(current, action)
    if refusal is not None:
        raise BatchError(refusal)


def _require_nothing_outstanding(current: BatchLineage) -> None:
    """A batch concludes `no-change` only when nothing about it is still open
    (`conclusion_refusal`)."""

    refusal = conclusion_refusal(current)
    if refusal is not None:
        raise BatchError(refusal)


def _redo_conclusion(lineage: Lineage, reason: str) -> ConclusionResult:
    """The same conclusion run again, or the refusal that nothing is current.

    The record is what ends the batch, and it is written before the audit line —
    so a run interrupted between the two left no batch current, and its own
    retry would otherwise report that there is nothing to conclude. The newest
    batch concluded `no-change` for exactly this reason is that operation,
    already done.
    """

    latest = redone_conclusion(lineage, reason=reason)
    outcome = latest.outcome or {} if latest is not None else {}
    if latest is None:
        raise BatchError(
            "no batch is current, so there is nothing to conclude; a batch is current from the freeze of its "
            "manifest until its outcome is recorded (invariant 14), and freezing the next cohort is "
            "`aii-2 evolution start`"
        )
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

    with held(config, experiment.ref, revision, experiment.experiment_id, requirement):
        yield


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


def _require_release_settled(
    config: EvolutionConfig,
    known: Lineage,
    current: BatchLineage,
) -> assessment.Obligation | None:
    """The release before this batch has been judged, before a base is frozen.

    Invariant 17, and it is asked here because this is where the decision would
    otherwise be made by accident. The first experiment of a batch freezes the
    commit every alternative in it starts from (invariant 15), and whether the
    previous release belongs in that commit is exactly what the reading of it
    settles: `retain` leaves the release on the line, `rolled-back` puts an
    inverse commit there first, so the base a freeze then takes is the line as
    the decision left it. Freezing before the answer would take whichever of the
    two the source line happened to be holding.

    The reading it waits on is the *owning* cohort's, which is the first batch
    frozen after that promotion and not necessarily this one. The two part
    company as soon as that cohort ends without answering: a batch that concluded
    `no-change` promoted nothing, so this batch still follows the older release
    while owing no reading of its own. Asking only whether *this* cohort owes one
    would find nothing to wait for and freeze a base on a line nobody decided
    about — the obligation stays where it was, and so does what waits on it.

    Only where a base is actually being frozen. A later experiment of the same
    batch takes the frozen commit rather than resolving one, so asking again
    there would gate work on a decision that can no longer change what it starts
    from — and a batch with no promotion anywhere before it is not waiting on
    anything.

    Nothing else in the batch waits on this either: the reading is taken while
    the analysis is still being written, and drafts, rejections and the closure
    are untouched by it. What waits is the one thing the decision moves.

    Returns the settled obligation, which is what the base is then held to; None
    where there is no release before this batch to judge.
    """

    if current.base_revision is not None:
        return None
    owed = assessment.obligation(config, current.batch, lineage=known)
    refusal = base_release_refusal(current, owed)
    if refusal is not None:
        raise BatchError(refusal)
    return owed


def _require_settled_line(
    config: EvolutionConfig,
    settlement: assessment.Obligation | None,
    base_revision: str,
    requested: str | None,
) -> None:
    """The base being frozen is the source line as the settlement left it.

    The other half of invariant 17, and without it the gate only asks that
    somebody answered. What the answer selects is a commit: `retain` says the
    alternatives are built on the line carrying the release, `rolled-back` says
    they are built on the line with the inverse commit on it. A base carrying
    neither — the pre-promotion revision after a `retain`, the promoted revision
    after a `rolled-back` — is the freeze deciding for itself what the human was
    asked, which is the accident this gate exists to stop.

    Asked of the resolved base whatever it came from, because `HEAD` and an
    explicit revision reach the wrong commit the same way: a checkout that never
    followed the source line, and an operator naming the revision the batch was
    frozen beside. It is also what keeps a rollback run *after* a recorded
    `retain` from quietly realigning the base — the reading says the release
    stays, and a line that no longer carries it is not the line that was decided
    on.

    Ancestry rather than equality: the base is ordinarily the line's tip and may
    be anything later built on it, and what matters is whether the commit the
    decision chose is in it.
    """

    if settlement is None:
        return
    decision = settlement.decision
    if decision is None:
        # Unreachable: the gate above refuses an unsettled reading and returns
        # nothing to hold a base to. Narrowing, not a second rule.
        return
    subject = settlement.frame.subject
    named = "" if requested is None else f" ({requested})"
    settled_as = f"{settlement.owner_id}'s reading of the {subject.batch_id} release was settled "
    if decision.settlement == assessment.SETTLEMENT_ROLLED_BACK:
        reversal = decision.rollback_revision
        if reversal is None:
            # Unreachable: the reader refuses a `rolled-back` settlement naming no
            # inverse commit, and holds the one it names to the rollback record.
            return
        _require_carried(
            config,
            base_revision,
            reversal,
            requested=named,
            requirement=(
                f"{settled_as}{assessment.SETTLEMENT_ROLLED_BACK!r}, so the base every experiment of this batch "
                f"starts from is the line carrying the inverse commit {reversal[:12]}"
            ),
        )
        return
    _require_carried(
        config,
        base_revision,
        subject.revision,
        requested=named,
        requirement=(
            f"{settled_as}{assessment.SETTLEMENT_RETAIN!r}, so the base every experiment of this batch starts "
            f"from is the line carrying the promotion {subject.revision[:12]}"
        ),
    )
    reversal = subject.rollback_revision
    if reversal is not None:
        # The other direction of the same question, and it is the one a rollback
        # run after the gate answered arrives at: the decision said the release
        # stays, so a base that took the reversal is a realignment nobody decided.
        _require_carried(
            config,
            base_revision,
            reversal,
            requested=named,
            carried=False,
            requirement=(
                f"{settled_as}{assessment.SETTLEMENT_RETAIN!r}, so the base every experiment of this batch "
                f"starts from is a line that has not taken the inverse commit {reversal[:12]} back off it"
            ),
        )


def _require_carried(
    config: EvolutionConfig,
    base_revision: str,
    revision: str,
    *,
    requested: str,
    requirement: str,
    carried: bool = True,
) -> None:
    """Whether the base about to be frozen has one commit in its history, where
    the settlement says what the answer has to be.

    Unanswerable refuses either way. Everywhere else a Git relation this clone
    cannot resolve is reported as the fact about the clone that it is; this one
    stands between a frozen base and every alternative built on it (invariant
    15), so a checkout that cannot see the relation does not get to assume it.
    """

    answer = contains(config.repo_root, revision, base_revision)
    if answer is None:
        raise BatchError(
            f"whether {base_revision[:12]}{requested} carries {revision[:12]} cannot be answered in this "
            f"checkout, which does not hold both commits; {requirement}, so this refuses rather than freeze a "
            "base it cannot place on the line the decision chose"
        )
    if answer is carried:
        return
    raise BatchError(
        f"{base_revision[:12]}{requested} " + ("does not carry" if carried else "carries") + f" {revision[:12]}; "
        f"{requirement} — a base off the line the settlement chose makes every alternative in this batch an "
        "attempt against a protocol nobody decided on (invariant 17), so name a revision on it or take the "
        "decision again"
    )


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

    round_ = redone_admission(experiment)
    admitted = {} if round_ is None else {task.draft_id: task for task in round_.tasks}
    if round_ is not None and set(admitted) == requested:
        return _finish(config, current, experiment, round_, tuple(admitted[key] for key in sorted(admitted)))

    raise BatchError(second_attempt_refusal(current, experiment))


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
        raise BatchError(_candidate_ready(experiment))
    return round_


def _serialize(experiment: Experiment) -> dict[str, Any]:
    """The record exactly as the schema holds it.

    One serializer for every write here, so an operation that appends a round or
    records a decision cannot drop a field the reader depends on: what is written
    back is the whole record the reader produced, with the one part that changed
    replaced.
    """

    decision = experiment.decision
    promotion = experiment.promotion
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
        # Written explicitly as null rather than left out, the way every other
        # absence here is: a reader meeting the key knows no promotion was
        # prepared, where a missing one would leave it deciding between that and
        # a record from before the field existed.
        "promotion": None
        if promotion is None
        else {
            "round": promotion.round_number,
            "candidate_revision": promotion.candidate_revision,
            "merge_input_revision": promotion.merge_input_revision,
            "merge_input_ref": promotion.merge_input_ref,
            "tree": promotion.tree,
            "revision": promotion.revision,
            "reason": promotion.reason,
            "planned_targets": list(promotion.planned_targets),
            "prepared_at": promotion.prepared_at,
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
