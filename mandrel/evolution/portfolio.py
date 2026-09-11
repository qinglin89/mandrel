"""The portfolio: what is, and what was.

`status` answers what happens next. This answers the other question — every
batch this repository has frozen, every attempt made inside one, what measured
each attempt, how each ended, and what the release each batch followed was
judged to be — so that a surface rendering the whole history reads one document
instead of reading Git, the versioned records, `.ai-tasks/`, the drafts and
somebody's deploy receipt for itself. A second reader of those files is a second
implementation of the lineage, and the two come to disagree about what happened.

The shape is settled in `evolution/schemas/portfolio.schema.json` and the
boundary in `evolution/README.md` (Machine surface). What lives here is the
reading: one derivation, two tokens, and the projection of both into the
document.

**One derivation, not a second lifecycle.** The records are read through
`phase.read` — the same whole-lineage derivation `status` and every guarded
operation stand on — and what this module adds on top of it is what the index
row needs and that projection drops: each batch's own frozen denominator, and
how far its own reading of the release before it has got. Nothing here decides
what a record means.

**Two tokens, and the second one is why this module takes the reading twice.**
`state_revision` is the durable half, computed in `guards` and bracketed exactly
as `phase.describe` brackets it. `observations_revision` is the volatile half:
every reading this controller takes of something no writer of this lifecycle
moves — each admitted task's completion, the current batch's analysis stage, the
staged bundles, each planned target's receipt, the age the clock gives the
standing offer, and what this checkout can resolve. It digests the answers and
never the inputs they were read from: `now` differs at every call and an object
store is not a thing that can be enumerated, so a digest over either would move
on every reading and guarantee nothing.

Which leaves the bracket. The durable half is bracketed by reading its inputs
before and after, because they can be read without taking the reading. The
volatile half cannot: its inputs *are* answers, and the only honest way to take
them again is to take them the way they were taken the first time. So the
reading is taken twice and the two sets of answers are compared, rather than
re-asking each question through a second, cheaper path — a question asked two
ways is two questions, and the token would then be over the one nobody rendered.
The cost is one extra derivation per call, which is what a read-only console verb
can afford and a wrong token is not.

**The observation set is the same whichever document is asked for.** Both
documents carry one snapshot so that a console assembling an index and several
details can tell one reading from several, and a token over a per-document
question set could never say that. So these are the controller's readings of the
repository, not this document's own.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from . import (
    assessment as assessment_ops,
    experiments as experiment_ops,
    guards,
    phase,
    rollback as rollback_ops,
)
from .analysis_task import task_finished, tasks_root
from .batches import AdmissionDecision
from .config import EvolutionConfig
from .deployment import TargetHolding
from .errors import BatchError
from .lineage import BatchLineage, Experiment, Lineage, RefState, describe_ref
from .phase import Promotion, ReleaseReading
from .proposals import BatchProposal
from .schema import format_rfc3339
from .state import staged_bundle

# 1: the first published shape of this reading. Independent of the `status`
# payload version and of every persisted record's — those version a shape a
# writer committed, and this versions a reading — and it promises its exact key
# set the way `status` does, because the surfaces that render a portfolio drive
# a lifecycle from it.
SCHEMA_VERSION = 1

# The two readings, by the name the document states about itself.
DOCUMENT_INDEX = "index"
DOCUMENT_DETAIL = "detail"

# How far a batch's own reading of the release before it has got. `none` is most
# batches: there was no release in front of them, or the one there was belongs to
# an earlier batch that owes the reading and keeps it.
RELEASE_NONE = "none"
RELEASE_OWED = "owed"
RELEASE_RECORDED = "recorded"
RELEASE_SETTLED = "settled"

# The scheme `observations_revision` is computed by. Versioned for
# `state_revision`'s reason and its own: a digest from another scheme is a
# different thing from a reading that moved, and only the version tells them
# apart. It is never compared by a mutation — no verb accepts this token — so
# what the version buys is a console that can say "this is not my scheme" rather
# than re-reading forever.
OBSERVATIONS_REVISION_VERSION = 1
_REVISION_DIGITS = 16

# How many times the reading is taken again when something moves underneath it.
# `phase.describe`'s number and its reason: every writer here holds the
# single-writer lock for one short operation, so a second attempt already answers
# the ordinary race and a third losing as well is a repository being written to
# continuously — a state to report rather than to keep retrying through.
_SNAPSHOT_ATTEMPTS = 3

# How an answer is spelled in the digest. Booleans and absence are three values,
# not two: a task nobody could ask about is a different reading from one asked
# about and unfinished, and a digest that spelled either as the empty string
# would hold them equal.
_ANSWER_YES = "yes"
_ANSWER_NO = "no"
_ANSWER_UNASKED = "unasked"


@dataclass(frozen=True)
class Observations:
    """Every volatile answer this reading took, as a value.

    A value rather than a digest, for the reason `guards.Inputs` is one: the
    token says *what* was observed, and two of these say whether one reading
    observed it. The six groups are the contract's own list (Machine surface),
    and they are held apart rather than flattened at the source so that a
    projection can read the answers it publishes off the same object the token is
    computed from.
    """

    # Each admitted task with no durable completion observation, by the round
    # that admitted it, and whether this machine's copy stands complete. None is
    # the answer where no `.ai-tasks/` is here to consult, which is every clone
    # but the one the work was done on.
    completions: tuple[tuple[str, bool | None], ...]
    # The current batch and whether its analysis stage has ended. None when no
    # batch is current — a concluded batch's stage is history, read from the
    # committed closure record rather than from a file on this machine.
    analysis: tuple[str, bool] | None
    # Every frozen batch's claimed reports, and which of their bundles are staged
    # here.
    staged: tuple[tuple[str, bool], ...]
    # Each planned target of the last promotion, as its own receipt has it.
    targets: tuple[tuple[str, str, str | None, str | None], ...] | None
    # Whole days the standing offer has waited, which is the one thing in this
    # reading the clock alone moves. None where no offer stands or none of its
    # members is pooled.
    waited_days: int | None
    # What this checkout could and could not settle: each attempt's ref against
    # its record's pins, each recorded inverse commit against the promotion it
    # takes back out and against the line it was made for, the reports a release
    # frame could not place at all, and whether the base the next admission would
    # build on carries the release it was formed to judge. A fetch moves these
    # without moving any watched ref.
    resolved: tuple[tuple[str, str], ...]

    def answers(self) -> tuple[tuple[str, str], ...]:
        """Every answer as one ordered question/answer sequence.

        The digest's whole input, assembled once so that what is hashed is
        visibly what was read. Questions are named rather than positional: a
        reading with one fewer admitted task would otherwise hash the same as one
        whose task was renamed.
        """

        stated: list[tuple[str, str]] = []
        stated.extend((f"task:{name}", _answer(value)) for name, value in self.completions)
        if self.analysis is not None:
            stated.append((f"analysis:{self.analysis[0]}", _answer(self.analysis[1])))
        stated.extend((f"staged:{key}", _answer(value)) for key, value in self.staged)
        if self.targets is not None:
            stated.extend(
                (f"target:{target}", f"{state}|{revision or ''}|{detail or ''}")
                for target, state, revision, detail in self.targets
            )
        stated.append(("waited-days", _answer(self.waited_days)))
        stated.extend(self.resolved)
        return tuple(stated)

    @property
    def revision(self) -> str:
        """The volatile token: a digest of the answers above.

        Truncated for `state_revision`'s reason — this is read back by a console
        deciding whether to re-read, and a long one would be no better at saying
        two readings differ.
        """

        digest = hashlib.sha256()
        digest.update(f"{OBSERVATIONS_REVISION_VERSION}\n".encode())
        for question, answer in self.answers():
            digest.update(f"{question}={answer}\n".encode())
        return f"{OBSERVATIONS_REVISION_VERSION}-{digest.hexdigest()[:_REVISION_DIGITS]}"


@dataclass(frozen=True)
class Snapshot:
    """What state this reading is of, in two tokens, and when it was taken."""

    taken_at: str
    state_revision: str
    observations_revision: str

    def to_json(self) -> dict[str, Any]:
        return {
            "taken_at": self.taken_at,
            "state_revision": self.state_revision,
            "observations_revision": self.observations_revision,
        }


@dataclass(frozen=True)
class BatchRow:
    """One frozen batch as the series carries it: what it was drawn from, how
    many attempts it took, how it ended, and whether it owes a reading of the
    release before it.

    Everything whose size is that batch's own work — its reports, its gate, its
    experiments, their rounds, its reading — is behind a detail read of it, which
    is what keeps the index growing by one row per freeze and by nothing else.
    """

    batch_id: str
    frozen_at: str
    base_revision: str
    task_count: int
    report_count: int
    considered: int
    human_included: int
    experiment_count: int
    current: bool
    outcome: str | None
    decided_at: str | None
    reason: str | None
    promotion: Promotion | None
    release: str

    def to_json(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "frozen_at": self.frozen_at,
            "base_revision": self.base_revision,
            "task_count": self.task_count,
            "report_count": self.report_count,
            "considered": self.considered,
            "human_included": self.human_included,
            "experiment_count": self.experiment_count,
            "current": self.current,
            "outcome": self.outcome,
            "decided_at": self.decided_at,
            "reason": self.reason,
            "promotion": _promotion_json(self.promotion),
            "release": self.release,
        }


@dataclass(frozen=True)
class Index:
    """The series: where the lifecycle stands, what is waiting to become a
    batch, and one row per batch that ever was."""

    phase: str
    decision: AdmissionDecision
    max_wait_days: int
    pool_complete: bool
    proposal: BatchProposal | None
    batches: tuple[BatchRow, ...]
    promotion: Promotion | None
    deployment: tuple[TargetHolding, ...] | None

    def to_json(self) -> dict[str, Any]:
        return {
            # The same phase `status` names, from the same derivation over the
            # same lineage. Carried so a console does not need two calls for the
            # headline; the tokens are what say the two readings are of one state.
            "phase": self.phase,
            "pool": {
                # Everything pending, which is the denominator and never what a
                # freeze would form: that is the offer's own count below.
                "task_count": self.decision.pool_count,
                "target": self.decision.target,
                "minimum": self.decision.minimum,
                # The policy's, not the decision's: a decision reached before the
                # age rule is measured carries no threshold, and the reader needs
                # the number the clock below is measured against either way.
                "max_wait_days": self.max_wait_days,
                # False also means never synced: completeness is shown rather
                # than assumed.
                "complete": self.pool_complete,
            },
            "proposal": _proposal_json(self.proposal, self.decision),
            "batches": [row.to_json() for row in self.batches],
            "deployment": _deployment_json(self.promotion, self.deployment),
        }


@dataclass(frozen=True)
class Portfolio:
    """One portfolio document: which reading it is, what state it is of, and the
    reading itself.

    Exactly one of `index` and `detail` is stated and the other is null, paired
    with `document`. The implemented JSON Schema subset cannot say that — both
    halves are nullable there, so a document stating one and carrying neither
    validates — which is why it is checked here instead, in both directions.
    """

    document: str
    snapshot: Snapshot
    index: Index | None = None

    def __post_init__(self) -> None:
        if (self.index is not None) != (self.document == DOCUMENT_INDEX):
            raise BatchError(
                f"a portfolio document says it is {self.document!r} and carries "
                f"{'an' if self.index is not None else 'no'} index; the two are one statement, and a reading "
                "that published them apart would be a document nothing could dispatch on"
            )

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "document": self.document,
            "snapshot": self.snapshot.to_json(),
            "index": None if self.index is None else self.index.to_json(),
            # The other half of the union. Written as a stated absence rather
            # than left out, because this reading promises its exact key set.
            "detail": None,
        }


def index(config: EvolutionConfig, *, now: datetime | None = None) -> Portfolio:
    """The series, as one document. Read-only.

    Fails closed exactly as `status` does: an unreadable manifest, a lineage that
    cannot be read as one history, a release record its own reader refuses — each
    raises here rather than being smoothed over, because a portfolio that showed
    a history the next operation refuses to act on is worse than one that says it
    cannot be read.

    Both tokens are taken over one bracket. The durable inputs are read, the
    reading is taken twice, and the inputs are read again: equal inputs say no
    writer finished underneath it, and two equal sets of observations say the
    volatile answers it was assembled from were one set rather than two. Nothing
    here takes the single-writer lock — a reading that waited on an operation
    would be a very different promise, and taking that lock is itself a write.
    """

    moment = now or datetime.now(timezone.utc)
    inputs = guards.read_inputs(config)
    taken = _read(config, moment)
    for _ in range(_SNAPSHOT_ATTEMPTS):
        again = _read(config, moment)
        after = guards.read_inputs(config)
        if after == inputs and again.observations == taken.observations:
            return Portfolio(
                document=DOCUMENT_INDEX,
                snapshot=Snapshot(
                    taken_at=format_rfc3339(moment),
                    state_revision=guards.state_revision(after),
                    observations_revision=again.observations.revision,
                ),
                index=_index(config, again),
            )
        inputs, taken = after, again
    raise BatchError(
        f"this repository changed under the portfolio reading {_SNAPSHOT_ATTEMPTS} times over, so nothing here "
        "describes one moment of it; the two tokens are what a surface re-reads on, and a reading straddling a "
        "write would publish a pair naming a state nothing was ever read at — run it again once the writer "
        "holding the single-writer lock has finished"
    )


@dataclass(frozen=True)
class _Reading:
    """One whole-lineage derivation, and the volatile answers taken over it."""

    status: phase.LifecycleStatus
    lineage: Lineage
    # Every batch that owes a reading of the release before it, in series order.
    owed: tuple[ReleaseReading, ...]
    observations: Observations


def _read(config: EvolutionConfig, moment: datetime) -> _Reading:
    """The whole reading, without either token.

    The lineage, the status projection over it, each owing batch's release
    record, and the volatile answers — taken together because the tokens are
    about all of it at once, and taken again by the caller because that is the
    only way the volatile half can be bracketed.
    """

    status, lineage, _ = phase.read(config, moment)
    owed = _owed(config, lineage)
    return _Reading(
        status=status,
        lineage=lineage,
        owed=owed,
        observations=_observe(config, status, lineage, owed),
    )


def _owed(config: EvolutionConfig, lineage: Lineage) -> tuple[ReleaseReading, ...]:
    """Each batch that owes a reading of the release before it, with its frame
    and whatever it has recorded.

    Asked of every batch rather than of the one in play, because an obligation an
    ended batch never answered stays with that batch (invariant 17): a reader
    finds the outstanding one as the row whose release is `owed` or `recorded`,
    and a series that reported only the current batch's would show none. Only the
    batches that owe one are held — a later batch follows the same release and
    derives the same frame to read the record by, and owes nothing itself
    (`assessment.owed_by`), so keeping it would put one obligation on three rows.

    The obligation is the carrier `status` already reads the current batch's
    through, asked here of a batch known to be the owner — so the frame, the
    record and the validation between them are one derivation rather than this
    module's own spelling of `assessment.obligation`.
    """

    stated: list[ReleaseReading] = []
    for item in lineage.batches:
        if assessment_ops.owed_by(lineage, item.batch) is None:
            continue
        owed = assessment_ops.obligation(config, item.batch, lineage=lineage)
        if owed is not None:
            stated.append(ReleaseReading(batch_id=item.batch_id, obligation=owed))
    return tuple(stated)


def _release_state(release: ReleaseReading) -> str:
    """How far one batch's own reading of the release before it has got."""

    if release.reading is None:
        return RELEASE_OWED
    return RELEASE_SETTLED if release.settled else RELEASE_RECORDED


def _observe(
    config: EvolutionConfig,
    status: phase.LifecycleStatus,
    lineage: Lineage,
    owed: tuple[ReleaseReading, ...],
) -> Observations:
    """Every volatile answer this controller takes of this repository.

    The contract's list, in its order (Machine surface), and the same list
    whichever document was asked for: both carry one snapshot, so a set of
    questions that depended on the document asked could never say that two
    documents are one reading.
    """

    return Observations(
        completions=_completions(config, lineage),
        # The stage answer rather than the task file: `awaiting_analysis` reads
        # the task on the machine that has it and the committed closure record
        # everywhere else, so this moves for a durable write as well as for a
        # local one. That is what digesting answers means, and it is the safe
        # direction — the token moving twice for one event costs a re-read, and
        # a local completion moving neither token is the gap this exists to close.
        analysis=None
        if status.current_batch is None
        else (status.current_batch.batch_id, status.current_batch.analysis_complete),
        staged=_staged(config, lineage),
        targets=None
        if status.deployment is None
        else tuple(
            # The machine-local path is deliberately not among them: where a
            # repository sits on the machine that took the reading is no fact
            # about the lifecycle, so it is published nowhere and a token over it
            # would move for something nothing renders.
            (holding.target, holding.state, holding.revision, holding.detail)
            for holding in status.deployment
        ),
        # Only where an offer stands. The pool's own oldest report is deliberately
        # not it: a long-pooled report this base does not select cannot age this
        # batch out (`batches.evaluate_admission`).
        waited_days=None if status.proposal is None else status.decision.waited_days,
        resolved=_resolved(config, lineage, owed),
    )


def _completions(config: EvolutionConfig, lineage: Lineage) -> tuple[tuple[str, bool | None], ...]:
    """Whether each admitted task with no durable observation stands complete
    here.

    Asked only of the tasks a seal has not already observed: that observation is
    durable because the file is machine-local and close-out archives it away, so
    a task the record shows complete is not consulted at all and its answer is
    not volatile.

    None where this machine has no `.ai-tasks/` to consult, which is every clone
    but the one the work was done on — and a different answer from a task file
    that is there and unfinished.

    Every attempt's rounds and not only the open one. What turns on it today is
    the open round — a seal waits on exactly those tasks — but the detail read
    states this answer beside every admitted task it publishes, and the two
    documents share one snapshot: a question asked only where the index happens
    to need it would make the index and a batch detail of the same moment two
    readings.
    """

    asked = tasks_root(config).is_dir()
    stated: list[tuple[str, bool | None]] = []
    for item in lineage.batches:
        for experiment in item.experiments:
            for round_ in experiment.rounds:
                for task in round_.tasks:
                    if task.complete:
                        continue
                    name = f"{experiment.experiment_id}/{round_.number}/{task.task_id}"
                    stated.append((name, task_finished(config, task.task_id) if asked else None))
    return tuple(stated)


def _staged(config: EvolutionConfig, lineage: Lineage) -> tuple[tuple[str, bool], ...]:
    """Which of every batch's claimed report bundles are staged on this machine.

    Every batch and not only the current one, for the reason the release records
    are read that way: a detail read of a concluded batch reports what this
    machine holds of its evidence, and a token over the current batch's bundles
    alone would leave that answer outside it.
    """

    return tuple(
        (key, staged_bundle(config, key))
        for item in lineage.batches
        for key in sorted(item.batch.report_keys)
    )


def _resolved(
    config: EvolutionConfig,
    lineage: Lineage,
    owed: tuple[ReleaseReading, ...],
) -> tuple[tuple[str, str], ...]:
    """What this checkout could and could not settle.

    The questions Git answers over revisions no watched ref carries, so a fetch
    that moves no tip still changes them: whether an attempt's ref and its
    record's pins form one history, whether a recorded inverse commit is the
    promotion taken back out of the line it was made from, whether the line has
    already taken an inverse the record does not claim yet, which of a release
    frame's reports this clone can place at all, and whether the base the next
    admission would build on still carries the release it was formed to judge.

    The set is the whole of what an ancestry answer here decides, rather than the
    ones a reading happens to publish: `create` is refused or offered on the base
    question (`phase._base_refusal`), `rollback` offers finishing a reversal on
    the line's own answer, and the attempt's ref decides whether anything is
    recorded onto it at all (`guards.ref_refusal`). Every one of them is an
    allowed action moving with no record and no watched tip moving, which is the
    property the pair of tokens exists to hold.

    Each ref is stated as the whole diagnosis the surfaces publish rather than as
    its headline: a missing pin arriving can leave `state` and `chain` exactly
    where they were and move the first broken link, which is the pair an operator
    is sent to repair.

    The open attempt's ref is taken from the lineage rather than asked again —
    the derivation already resolved it, and asking a second time would be a
    second answer to one question inside one reading. The base question is asked
    through the admission's own predicate for that reason: a second spelling of
    it here would be a token over an answer no gate reads.

    Asked of every attempt in the series, so the cost of this reading grows with
    history while its payload does not. That is the trade the shared snapshot
    costs: asking only about the batch a document happens to be about would make
    an index and a detail taken at one moment carry different tokens, which is
    the one thing the pair is published for.
    """

    stated: list[tuple[str, str]] = []
    for item in lineage.batches:
        for experiment in item.experiments:
            state = _ref_state(config, item, experiment)
            stated.append((f"ref:{experiment.experiment_id}", _ref_answer(state)))
        if item.rollback is not None:
            # The lineage's own answer, taken while it held the record to it: the
            # empty string is a confirmed inverse, and a sentence is why this
            # checkout could not confirm one.
            stated.append((f"inverse:{item.batch_id}", item.rollback_unconfirmed or ""))
            if item.rollback["reverted_at"] is None:
                # Only while the record is unfinished, which is the window that
                # question exists for: the line may already carry the inverse,
                # and whether this clone can see that it does is what turns
                # `rollback` from a reversal into finishing the one on record.
                stated.append(
                    (
                        f"reversal:{item.batch_id}",
                        _answer(rollback_ops.unfinished_reversal(config, item) is not None),
                    )
                )
    current = lineage.current
    if current is not None:
        # The admission's own question, and the one an ordinary fetch answers: a
        # base whose relation to the release nobody here can compute refuses
        # `create` outright, and the same base with the objects present offers it.
        refusal = experiment_ops.base_on_the_line_refusal(config, lineage, current)
        stated.append((f"base:{current.batch_id}", refusal or ""))
    for entry in owed:
        stated.append((f"placement:{entry.batch_id}", ",".join(sorted(entry.frame.unverified))))
    return tuple(stated)


def _ref_answer(state: RefState) -> str:
    """One attempt's ref, as the whole reading of it the surfaces publish.

    `ref`, `pinned` and `pinned_expected` are the record's own and are covered by
    the durable token; what is stated here is everything the checkout answered —
    where the ref stands, how that stands against the pin, whether the pins form
    one history, and which link broke first.
    """

    broken = "" if state.chain_break is None else ">".join(state.chain_break)
    return f"{state.state}|{state.tip or ''}|{_answer(state.chain)}|{broken}"


def _ref_state(config: EvolutionConfig, item: BatchLineage, experiment: Experiment) -> RefState:
    open_experiment = item.open_experiment
    if open_experiment is not None and open_experiment.experiment_id == experiment.experiment_id:
        # Present whenever an experiment is open — the derivation resolves the
        # open attempt's ref and only that one (`lineage._batch_lineage`).
        if item.ref is not None:
            return item.ref
    return describe_ref(config, experiment)


def _index(config: EvolutionConfig, reading: _Reading) -> Index:
    status = reading.status
    return Index(
        phase=status.phase,
        decision=status.decision,
        # The policy's own threshold rather than the decision's: a decision
        # reached before the age rule is measured carries none, and the number a
        # reader needs the clock measured against is the same either way.
        max_wait_days=config.batch.max_wait_days,
        pool_complete=status.pool_complete,
        proposal=status.proposal,
        batches=_rows(reading),
        promotion=status.last_promotion,
        deployment=status.deployment,
    )


def _rows(reading: _Reading) -> tuple[BatchRow, ...]:
    """One row per frozen batch, in freeze order.

    The status projection and the manifest beside it: `status` publishes what a
    batch held and how it ended, and the row adds the three facts a series needs
    and a next-action reading has no use for — when membership stopped moving,
    the commit every attempt of it started from, and how far its own reading of
    the release before it has got.
    """

    owed = {entry.batch_id: _release_state(entry) for entry in reading.owed}
    records = {item.batch_id: item for item in reading.status.batches}
    rows: list[BatchRow] = []
    for item in reading.lineage.batches:
        record = records[item.batch_id]
        rows.append(
            BatchRow(
                batch_id=item.batch_id,
                frozen_at=item.batch.created_at,
                base_revision=item.base_revision,
                task_count=record.task_count,
                report_count=record.report_count,
                considered=item.batch.considered,
                human_included=item.batch.human_included,
                experiment_count=record.experiment_count,
                current=record.current,
                outcome=record.outcome,
                decided_at=record.decided_at,
                reason=record.reason,
                promotion=record.promotion,
                release=owed.get(item.batch_id, RELEASE_NONE),
            )
        )
    return tuple(rows)


def _proposal_json(proposal: BatchProposal | None, decision: AdmissionDecision) -> dict[str, Any] | None:
    """The batch on offer, as a row. Null when none stands.

    Two counts and never one: what a freeze would form is not what the pool
    holds, and a reader given a single number cannot tell a short pool from a
    pool whose evidence is about another protocol.

    The age comes from the admission decision rather than being measured here,
    because the decision is where the policy measures it — the oldest *member's*
    import, which is the timestamp a freeze is released against.
    """

    if proposal is None:
        return None
    return {
        "built_at": proposal.built_at,
        "source_line_ref": proposal.source_line_ref,
        "base_revision": proposal.base_revision,
        "task_count": proposal.task_count,
        "considered": proposal.considered,
        "human_included": len(proposal.human_included),
        "oldest_member_imported_at": decision.oldest_pending_at,
        # Negative where the source clock ran ahead of this one: the skew is
        # shown rather than assumed away.
        "waited_days": decision.waited_days,
        "not_selected": [
            {"reason": reason, "count": count} for reason, count in sorted(_not_selected(proposal).items())
        ],
    }


def _not_selected(proposal: BatchProposal) -> dict[str, int]:
    """The offer's non-selections by reason, as counts.

    An array of pairs on the way out rather than an object keyed by reason: the
    schema subset can constrain an enum in an item and cannot constrain a key, so
    this is the shape in which the vocabulary is actually checked.
    """

    tally: dict[str, int] = {}
    for item in proposal.not_selected:
        tally[item.reason] = tally.get(item.reason, 0) + 1
    return tally


def _promotion_json(promotion: Promotion | None) -> dict[str, Any] | None:
    """A change recorded as reaching the source line, with the merge unit it
    went as.

    Not `phase`'s projection of the same value, and the difference is one field:
    a rollback here states whether this checkout could confirm that the recorded
    commit is the promotion taken back out of the line it was made from. The
    next-action surface meets that as a refusal from the verb that would redo the
    reversal; a reading of what happened has nowhere else to put it.
    """

    if promotion is None:
        return None
    rollback = promotion.rollback
    return {
        "batch_id": promotion.batch_id,
        "experiment_id": promotion.experiment_id,
        "revision": promotion.revision,
        "round": promotion.round_number,
        "candidate_revision": promotion.candidate_revision,
        "merge_input_revision": promotion.merge_input_revision,
        "merge_input_ref": promotion.merge_input_ref,
        "tree": promotion.tree,
        # Planned, never deployed: what a target carries is its own receipt's to
        # state and lags every promotion until that target is redeployed.
        "planned_targets": list(promotion.planned_targets),
        "rollback": None
        if rollback is None
        else {
            "revision": rollback.revision,
            "reverted_from": rollback.reverted_from,
            # Null while the inverse commit exists and the line has not been
            # recorded as carrying it — an operation in flight, which is a
            # different next step from one that finished.
            "reverted_at": rollback.reverted_at,
            "reason": rollback.reason,
            "unconfirmed": rollback.unconfirmed,
        },
    }


def _deployment_json(
    promotion: Promotion | None,
    holdings: tuple[TargetHolding, ...] | None,
) -> dict[str, Any] | None:
    """What the planned targets hold, beside the two commits those answers are
    relative to.

    Null when nothing has been promoted: the plan is where the targets in play
    come from, so with no promotion there is no list to answer for — never an
    empty one, which would read as a promotion that planned nothing.
    """

    if promotion is None or holdings is None:
        return None
    return {
        "promotion": promotion.revision,
        # Asked first, because a line that took the reversal contains the
        # promotion too.
        "rollback": None if promotion.rollback is None else promotion.rollback.revision,
        "targets": [
            {
                "target": holding.target,
                "revision": holding.revision,
                "state": holding.state,
                "detail": holding.detail,
            }
            for holding in holdings
        ],
    }


def _answer(value: bool | int | str | None) -> str:
    """One observed answer, spelled so that three states stay three.

    A question nobody could ask is not a question answered no, and a digest that
    spelled either as the empty string would hold a clone with no task tree equal
    to one whose tasks are unfinished.
    """

    if value is None:
        return _ANSWER_UNASKED
    if isinstance(value, bool):
        return _ANSWER_YES if value else _ANSWER_NO
    return str(value)
