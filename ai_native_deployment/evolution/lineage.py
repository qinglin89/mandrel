"""The batch and experiment lineage, read from what is committed.

A batch's change cycle is a shape, not a pointer: one frozen base, a series of
experiments against it of which at most one is open, and inside that one a
series of rounds of which at most one is unsealed. The contract's "What is
derived" section says where that shape may be read from — the manifests, the
closure and outcome records, the experiment records, the rejection records, the
experiment refs, and Git — and, just as importantly, where it may not:

- not from `HEAD` against the release tag, which names no experiment and
  changes with a `git checkout`;
- not from scanning `.ai-tasks/` for a batch citation, which finds nothing on a
  fresh clone and less as close-out archives tasks away;
- not from the ledger, which records that events happened and is never read
  back as state.

So this module reads records, and the records name their own tasks, drafts, and
revisions. Every derivation here works on a clone that has no `.ai-tasks/` and
no `refs/evolution/*` at all, because those are the ordinary conditions
everywhere but the one machine the work happened on.

**Fail closed, but only on the records.** A record that cannot be read as one
unambiguous history stops the read (two open experiments, two bases for one
batch, a round sealed with an unfinished task, a draft consumed twice) — the
same rule the manifests already follow, and for the same reason: a reading that
picks one interpretation lets the next operation act on the other. Git is
treated differently. A ref that is absent, ahead of where the record says it
should be, or standing on a history the record's own pins do not lead to is
reported as an observation rather than raised: the artifacts are intact, the
operator needs to be told which ref moved, and refusing to describe the
lifecycle is not how they find out. Guarded operations are what refuse on an
inconsistent ref; a reader names it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import analysis_task
from .config import (
    BATCH_ID_DIGITS,
    BATCH_ID_PREFIX,
    EXPERIMENT_SCHEMA_FILENAME,
    EvolutionConfig,
)
from .errors import BatchError
from .manifests import (
    OUTCOME_PROMOTED,
    Batch,
    load_batches,
    read_outcome,
    read_rejected_drafts,
)
from .revisions import commit_shape, contains, ref_tip
from .schema import load_schema, validate_or_raise

EXPERIMENT_FILENAME = "experiment.json"
EXPERIMENT_SCHEMA_VERSION = 1

# Identity and namespace, mirroring `experiment.schema.json`. The batch half is
# built from the config's own constants so the two spellings of a batch id
# cannot drift apart.
EXPERIMENT_ID_INFIX = "-exp-"
EXPERIMENT_ORDINAL_DIGITS = 2
REF_NAMESPACE = "refs/evolution/experiments"
# The ordinal exactly as `format_experiment_id` writes it: positive, zero-padded
# to EXPERIMENT_ORDINAL_DIGITS, and no wider than the number needs. A plain
# `[0-9]{2,}` accepts `00`, which names no attempt, and takes `01` and `001` as
# two ids for one position in the series — two directories an allocation counting
# "one past the highest" would hand out for the same ordinal.
_ORDINAL = rf"(?:0{{{EXPERIMENT_ORDINAL_DIGITS - 1}}}[1-9]|[1-9][0-9]{{{EXPERIMENT_ORDINAL_DIGITS - 1},}})"
_EXPERIMENT_ID = re.compile(
    rf"\A({re.escape(BATCH_ID_PREFIX)}[0-9]{{{BATCH_ID_DIGITS},}})"
    rf"{re.escape(EXPERIMENT_ID_INFIX)}({_ORDINAL})\Z",
    re.ASCII,
)
# The stem a draft file must have to be admissible, mirroring the `draft_id`
# pattern the experiment and rejection schemas share.
_DRAFT_ID = re.compile(r"\A[a-z0-9]+(-[a-z0-9]+)*\Z", re.ASCII)

DECISION_ABANDONED = "abandoned"
DECISION_SUPERSEDED = "superseded"
DECISION_PROMOTED = "promoted"

# Where an experiment's ref sits relative to what its record pins. The pin is
# the last sealed round's candidate revision, or the base revision while nothing
# has been sealed yet.
REF_ABSENT = "absent"
REF_AT_PIN = "at-pinned-revision"
REF_AHEAD = "ahead"
REF_DIVERGED = "diverged"
REF_UNKNOWN = "unknown"


@dataclass(frozen=True)
class Seal:
    """What makes a round candidate-ready (invariant 16)."""

    sealed_at: str
    candidate_revision: str


@dataclass(frozen=True)
class AdmittedTask:
    """One draft's admission into a round, and whether it was seen through."""

    task_id: str
    draft_id: str
    draft_sha256: str
    admitted_at: str
    completion_observed_at: str | None

    @property
    def complete(self) -> bool:
        """Observed at `completed` — the durable form of it, since the task file
        itself is machine-local and archived away by close-out."""

        return self.completion_observed_at is not None


@dataclass(frozen=True)
class Round:
    """One revision pass: the tasks admitted into it, and what it produced."""

    number: int
    opened_at: str
    reason: str
    tasks: tuple[AdmittedTask, ...]
    seal: Seal | None

    @property
    def candidate_ready(self) -> bool:
        return self.seal is not None

    @property
    def candidate_revision(self) -> str | None:
        return self.seal.candidate_revision if self.seal else None

    @property
    def unfinished(self) -> tuple[str, ...]:
        """Admitted tasks with no completion observation — what a seal waits on."""

        return tuple(task.task_id for task in self.tasks if not task.complete)


@dataclass(frozen=True)
class Decision:
    """The terminal decision that turns an experiment into history."""

    outcome: str
    decided_at: str
    reason: str
    superseded_by: str | None
    promotion_revision: str | None


@dataclass(frozen=True)
class PreparedPromotion:
    """The merge unit an experiment's promotion was prepared as.

    Written before the source line moves and never edited, so the same record is
    what an interrupted run is finished from and what the completed promotion
    went as. `revision` is the operation's identity — the exact commit it made —
    which is what a later run asks Git about instead of recognising a merge by
    its shape.
    """

    round_number: int
    candidate_revision: str
    merge_input_revision: str
    merge_input_ref: str
    tree: str
    revision: str
    reason: str
    planned_targets: tuple[str, ...]
    prepared_at: str


@dataclass(frozen=True)
class Experiment:
    """One attempt at the change a batch's analysis called for."""

    experiment_id: str
    batch_id: str
    created_at: str
    base_revision: str
    base_release_ref: str | None
    ref: str
    rounds: tuple[Round, ...]
    decision: Decision | None
    directory: Path
    # None until a promotion is prepared on this experiment, and from then on
    # what that promotion is. Not part of the decision: it is written before the
    # source line moves, while a decision states what already happened.
    promotion: PreparedPromotion | None = None

    @property
    def ordinal(self) -> int:
        return experiment_ordinal(self.experiment_id) or 0

    @property
    def open(self) -> bool:
        """Non-terminal: no decision has been recorded. Age, an empty ref, and a
        sealed last round are none of them terminal — only a decision is."""

        return self.decision is None

    @property
    def last_round(self) -> Round:
        return self.rounds[-1]

    @property
    def open_round(self) -> Round | None:
        """The round still taking work, if any. None through replay: a sealed
        last round leaves the experiment with no open round while the experiment
        itself stays non-terminal."""

        last = self.last_round
        return None if last.candidate_ready else last

    @property
    def candidate_revision(self) -> str | None:
        """What may be measured right now — the last round's pinned candidate,
        and nothing at all while that round is open.

        An earlier round's candidate is deliberately not the answer: it is the
        tree its own evidence describes, and reporting it as the current
        candidate is exactly the substitution invariant 16 exists to prevent.
        """

        return self.last_round.candidate_revision

    @property
    def pinned_revision(self) -> str:
        """The most recent revision any round pinned, or the base when none has.

        This is what the ref must hold: rounds only append, so every sealed
        candidate stays reachable and the tip is at or ahead of the latest one.
        """

        for round_ in reversed(self.rounds):
            if round_.candidate_revision is not None:
                return round_.candidate_revision
        return self.base_revision

    @property
    def admitted_tasks(self) -> tuple[AdmittedTask, ...]:
        return tuple(task for round_ in self.rounds for task in round_.tasks)

    @property
    def draft_ids(self) -> tuple[str, ...]:
        return tuple(task.draft_id for task in self.admitted_tasks)


@dataclass(frozen=True)
class RefState:
    """Where an experiment's ref actually sits, against where its record pins it."""

    ref: str
    tip: str | None
    pinned: str
    state: str
    # True when the last round is candidate-ready: the ref must then be exactly
    # at the pin, because work resumes by opening a round, never by committing
    # under one that has already been measured.
    pinned_expected: bool
    # Whether the record's own pins form one appending history — the base, then
    # each sealed round's candidate in order, each reachable from the next
    # (invariant 15). None when this checkout does not hold the objects to say.
    chain: bool | None
    # The first pair that does not, as (earlier, later); None unless `chain` is
    # False. Named because "this ref is inconsistent" without saying which of
    # several revisions broke the history is not something an operator can act
    # on.
    chain_break: tuple[str, str] | None

    @property
    def consistent(self) -> bool | None:
        """Whether the ref and the record's pins agree — None when this checkout
        cannot say, which is the ordinary answer for a clone that never fetched
        the namespace.

        A definite break outranks an unanswerable link, in either half: half a
        history checked is enough to know it is wrong, and never enough to call
        it right.
        """

        if self.chain is False or self.state == REF_DIVERGED:
            return False
        if self.pinned_expected and self.state == REF_AHEAD:
            return False
        if self.chain is None or self.state in (REF_ABSENT, REF_UNKNOWN):
            return None
        return True


@dataclass(frozen=True)
class Gate:
    """This batch's admission gate, derived rather than read off the directory.

    A draft is waiting when no experiment has taken it and nothing has turned it
    down. The directory alone cannot say that, because admission copies the
    draft and leaves it in place — so what is present is every proposal ever
    made, not the ones still to decide.
    """

    waiting: tuple[str, ...]
    consumed: Mapping[str, str]
    declined: tuple[str, ...]
    # Consumed or declined drafts whose file is gone. The record still holds the
    # id and the hash of what was admitted, so the lineage is intact; the bytes
    # are recoverable from history. Reported, not refused.
    missing: tuple[str, ...]
    # Files under `proposed-tasks/` whose stem could never be a draft id. No
    # admission could record one, so they are not waiting for a decision. Named
    # by file, since unlike everything else here they are not proposals.
    unusable: tuple[str, ...]


@dataclass(frozen=True)
class BatchLineage:
    """One batch's whole change cycle, as the committed records describe it."""

    batch: Batch
    experiments: tuple[Experiment, ...]
    open_experiment: Experiment | None
    outcome: Mapping[str, Any] | None
    gate: Gate
    ref: RefState | None

    @property
    def batch_id(self) -> str:
        return self.batch.batch_id

    @property
    def current(self) -> bool:
        """Current from the freeze of its manifest until its outcome is recorded
        (invariant 14) — through analysis, admission, every experiment, and the
        decision that follows."""

        return self.outcome is None

    @property
    def base_revision(self) -> str | None:
        """The source commit every experiment of this batch starts from, frozen
        by the first of them. None until there is one: a batch with no
        experiment has no base, and the freeze deliberately did not pin one."""

        return self.experiments[0].base_revision if self.experiments else None

    @property
    def base_release_ref(self) -> str | None:
        return self.experiments[0].base_release_ref if self.experiments else None

    @property
    def candidate_revision(self) -> str | None:
        """The revision replay may exercise, or None when there is nothing
        pinned to exercise."""

        return self.open_experiment.candidate_revision if self.open_experiment else None

    @property
    def terminal_experiments(self) -> tuple[Experiment, ...]:
        """History. Never a reason anything is blocked: abandoning or superseding
        frees the batch for another alternative (invariant 14)."""

        return tuple(experiment for experiment in self.experiments if not experiment.open)

    @property
    def pending_successor(self) -> str | None:
        """The successor a supersession recorded but had not created yet.

        Superseding writes two records — the decision that ends the attempt, and
        the successor it names — and the decision is written first because it is
        the only order whose interruption leaves one readable state: the other
        way round leaves two open experiments, which no reading can arbitrate
        (invariant 14). So this is the half-finished supersession, and the
        operation is finished by being redone.

        It can only ever be the newest experiment: ordinals run 1..N and a
        successor is N+1, so an existing successor would itself be the newest.

        Only a current batch can be in this state. A concluded one carrying it is
        refused as it is read (`_require_ended_attempts`), because a batch that
        ended is no longer where anyone looks for an unfinished operation.
        """

        return _pending_successor(self.experiments)


@dataclass(frozen=True)
class Lineage:
    """Every batch's lineage, and which one is current."""

    batches: tuple[BatchLineage, ...]
    current: BatchLineage | None


def describe(config: EvolutionConfig, *, batches: list[Batch] | None = None) -> Lineage:
    """Derive the lineage of every frozen batch. Read-only.

    One pass over the experiment records, so a repository with several batches
    reads them once and cannot end up applying two definitions of "current".
    """

    known = load_batches(config) if batches is None else batches
    by_batch = load_experiments(config, batches=known)
    lineages = tuple(
        _batch_lineage(config, batch, by_batch.get(batch.batch_id, ())) for batch in known
    )

    current = [lineage for lineage in lineages if lineage.current]
    _require_one_current(current)
    return Lineage(batches=lineages, current=current[0] if current else None)


def current_batch(config: EvolutionConfig, *, batches: list[Batch] | None = None) -> Batch | None:
    """The one batch whose change cycle is still running, read as a whole
    history rather than off a file's presence.

    Currency decides two writes — whether a new cohort may be frozen, and which
    interrupted freeze may be completed — so both ask here, through the same
    derivation `status` reads. An `outcome.json` the rest of its batch
    contradicts has ended nothing: it names an experiment the batch does not
    have, or concludes `no-change` over an attempt that records a promotion.
    Judging it by its own shape alone is what let a malformed conclusion release
    the next cohort while `status`, deriving the same repository, refused to
    describe it at all.
    """

    current = describe(config, batches=batches).current
    return current.batch if current is not None else None


def _require_one_current(current: list[BatchLineage]) -> None:
    """Invariant 14: one batch is current at a time.

    Refused rather than arbitrated. Whichever of the two a reader picks, the
    experiments and evidence it goes on to collect belong to the other — and a
    write path that picks one completes the interrupted freeze of a cohort while
    another cohort's change cycle is still running.
    """

    if len(current) > 1:
        raise BatchError(
            "more than one current batch: "
            + ", ".join(lineage.batch_id for lineage in current)
            + " — invariant 14 allows one at a time; record the earlier batch's outcome "
            "(promoted or no-change) before the next cohort continues"
        )


def describe_batch(config: EvolutionConfig, batch: Batch) -> BatchLineage:
    """One batch's lineage, for a caller that already has the batch.

    Still loads every experiment: one naming a batch that does not exist is a
    finding about the repository, not about whichever batch was asked for.
    """

    return _batch_lineage(config, batch, load_experiments(config).get(batch.batch_id, ()))


def load_experiments(
    config: EvolutionConfig,
    *,
    batches: list[Batch] | None = None,
) -> dict[str, tuple[Experiment, ...]]:
    """Every experiment record on disk, validated and grouped by batch.

    Fails closed on a *directory* it cannot account for, the way `load_batches`
    does: a name that is not an experiment id might be an experiment this build
    cannot read, and skipping it would let an allocation reuse an ordinal a
    record already claims. Dot-prefixed names are the exception — staging
    residue belongs to no experiment. Loose files are ignored: one experiment is
    one directory, and the tree ships with a `README.md` layout note beside them.

    An experiment naming a batch that does not exist is refused rather than
    dropped: it is either a cohort somebody deleted or an id that was mistyped,
    and both are histories this controller must not quietly stop showing.
    """

    root = config.experiments_root
    if not root.is_dir():
        return {}

    experiments: list[Experiment] = []
    for entry in sorted(root.iterdir()):
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        if experiment_ordinal(entry.name) is None:
            raise BatchError(
                f"{entry}: not an experiment identifier; only experiment records belong under "
                f"{config.storage.experiments}/"
            )
        experiments.append(_read_experiment(config, entry))

    known = {batch.batch_id for batch in (load_batches(config) if batches is None else batches)}
    grouped: dict[str, list[Experiment]] = {}
    for experiment in experiments:
        if experiment.batch_id not in known:
            raise BatchError(
                f"{experiment.directory / EXPERIMENT_FILENAME}: names batch {experiment.batch_id!r}, which has no "
                f"frozen manifest under {config.storage.batches}/; an experiment belongs to the cohort that "
                "justified it"
            )
        grouped.setdefault(experiment.batch_id, []).append(experiment)

    return {
        batch_id: tuple(sorted(found, key=lambda experiment: experiment.ordinal))
        for batch_id, found in grouped.items()
    }


def is_draft_id(value: str) -> bool:
    """Whether this string could be a draft id at all.

    The gate's own spelling of the rule, shared with the operations that admit
    and decline drafts (`experiments.py`): a draft id is a kebab-case slug, which
    is what makes it one path segment under `proposed-tasks/` rather than a name
    that could reach anywhere else.
    """

    return _DRAFT_ID.match(value) is not None


def experiment_ordinal(experiment_id: str) -> int | None:
    """The ordinal inside an experiment id, or None when the string is not one."""

    match = _EXPERIMENT_ID.match(experiment_id)
    return int(match.group(2)) if match else None


def experiment_batch_id(experiment_id: str) -> str | None:
    """The batch half of an experiment id, or None when the string is not one."""

    match = _EXPERIMENT_ID.match(experiment_id)
    return match.group(1) if match else None


def format_experiment_id(batch_id: str, ordinal: int) -> str:
    if ordinal < 1:
        raise BatchError(f"experiment ordinals start at 1, got {ordinal}")
    return f"{batch_id}{EXPERIMENT_ID_INFIX}{ordinal:0{EXPERIMENT_ORDINAL_DIGITS}d}"


def experiment_ref(experiment_id: str) -> str:
    return f"{REF_NAMESPACE}/{experiment_id}"


def describe_ref(config: EvolutionConfig, experiment: Experiment) -> RefState:
    """Where this experiment's ref sits, and whether everything its record pins
    leads there — as far as this checkout can tell.

    The tip against the latest pin is only the last link of that history. On its
    own it reports an experiment whose very first candidate sits on a history
    unrelated to the batch's frozen base as exactly what a well-behaved one looks
    like: the ref resting on the revision the record names. So the base has to
    reach the first sealed candidate, and each sealed candidate the next, before
    the tip is asked about at all.
    """

    pinned = experiment.pinned_revision
    expected = experiment.last_round.candidate_ready
    chain, chain_break = _pin_chain(config, experiment)
    tip = ref_tip(config.repo_root, experiment.ref)
    if tip is None:
        state = REF_ABSENT
    elif tip == pinned:
        state = REF_AT_PIN
    else:
        descends = contains(config.repo_root, pinned, tip)
        state = REF_UNKNOWN if descends is None else (REF_AHEAD if descends else REF_DIVERGED)
    return RefState(
        ref=experiment.ref,
        tip=tip,
        pinned=pinned,
        state=state,
        pinned_expected=expected,
        chain=chain,
        chain_break=chain_break,
    )


def _pin_chain(
    config: EvolutionConfig, experiment: Experiment
) -> tuple[bool | None, tuple[str, str] | None]:
    """Whether every revision this record pins descends from the one before it.

    Walks base → each sealed round's candidate in order, which is invariant 15's
    "each round's candidate revision stays reachable from the next one's" put to
    Git. A break is reported with the pair that broke it and is not softened by a
    later link nobody can answer: what is already known to be wrong does not
    become uncertain. An unanswerable link with no broken one is None — this
    checkout does not hold both objects, which says nothing about the lineage.

    Two identical revisions need no object to compare: a round sealed exactly
    where the previous one was pinned added no commit, which is a candidate
    nobody changed rather than a history anyone rewrote.
    """

    pins = [experiment.base_revision] + [
        round_.candidate_revision for round_ in experiment.rounds if round_.candidate_revision is not None
    ]
    unknown = False
    for earlier, later in zip(pins, pins[1:]):
        if earlier == later:
            continue
        descends = contains(config.repo_root, earlier, later)
        if descends is False:
            return False, (earlier, later)
        if descends is None:
            unknown = True
    return (None if unknown else True), None


def _batch_lineage(
    config: EvolutionConfig,
    batch: Batch,
    experiments: tuple[Experiment, ...],
) -> BatchLineage:
    _require_one_base(batch, experiments)
    open_experiments = [experiment for experiment in experiments if experiment.open]
    if len(open_experiments) > 1:
        raise BatchError(
            f"{batch.batch_id} has more than one open experiment: "
            + ", ".join(experiment.experiment_id for experiment in open_experiments)
            + " — invariant 14 allows one; abandon or supersede the earlier attempt, which keeps it as evidence"
        )
    _require_serial_experiments(batch, experiments)
    _require_named_successors(experiments)
    # Drafts before tasks: a draft admitted twice produces the same task id twice
    # as well, and of the two ways to say that, the repeated proposal is the one
    # an operator can act on.
    consumed = _consumed_drafts(batch, experiments)
    _require_one_task_per_admission(batch, experiments)
    open_experiment = open_experiments[0] if open_experiments else None

    outcome = read_outcome(config, batch)
    if outcome is not None:
        _require_ended_attempts(batch, experiments, open_experiment, outcome)
        _require_one_promotion(config, batch, experiments, outcome)

    return BatchLineage(
        batch=batch,
        experiments=experiments,
        open_experiment=open_experiment,
        outcome=outcome,
        gate=_gate(config, batch, consumed),
        ref=describe_ref(config, open_experiment) if open_experiment else None,
    )


def _require_one_base(batch: Batch, experiments: Iterable[Experiment]) -> None:
    """Invariant 15: the batch has one base, settled by its first experiment.

    Refused rather than reconciled, because attempts against different sources
    are not alternatives to each other and no reading of this repository can say
    which of the two the evidence meant.
    """

    bases = {experiment.base_revision: experiment.experiment_id for experiment in experiments}
    if len(bases) > 1:
        listed = ", ".join(f"{name} at {revision[:12]}" for revision, name in sorted(bases.items()))
        raise BatchError(
            f"{batch.batch_id} has experiments on more than one base revision ({listed}); every experiment of "
            "one batch starts from the commit its first experiment froze"
        )


def _require_serial_experiments(batch: Batch, experiments: tuple[Experiment, ...]) -> None:
    """A batch's experiments are one series: ordinals 1..N, and only the newest
    of them still open.

    Both halves are invariant 14's allocation rule read backwards. An id is
    handed out one past the highest that batch ever used, so a gap is an attempt
    whose record is gone — taking its base, its task selections, and its
    candidates with it — and nothing distinguishes that from an id allocated
    wrongly. And a new experiment is created only once the one before it ended,
    so an earlier attempt left open underneath a later one is a history that
    could not have happened: whichever of the two a reader calls current, the
    evidence it goes on to collect belongs to the other.
    """

    ordinals = [experiment.ordinal for experiment in experiments]
    if ordinals != list(range(1, len(experiments) + 1)):
        raise BatchError(
            f"{batch.batch_id} has experiment ordinals {ordinals}; they are allocated one at a time from 1 and "
            "an id is never reused, so a gap is an attempt whose record is missing rather than one that never "
            "existed"
        )
    stale = [experiment.experiment_id for experiment in experiments[:-1] if experiment.open]
    if stale:
        raise BatchError(
            f"{batch.batch_id}: {stale} carry no decision while {experiments[-1].experiment_id} exists after "
            "them; an experiment is created only once the one before it ended, so the open one is the newest "
            "(invariant 14) — record what happened to the earlier attempt"
        )


def _require_named_successors(experiments: tuple[Experiment, ...]) -> None:
    """A `superseded` decision names the experiment created to replace it: the
    next one in the series, and never itself.

    Superseding is one operation — it ends the attempt and creates its successor
    — so the replacement is the id allocated immediately past it. A name pointing
    anywhere else describes a replacement this controller could not have made,
    and leaves the reason an attempt ended pointing at evidence that answers a
    different question.

    Whether that successor is *here* is deliberately not checked. Ordinals run
    1..N, so the only name that can be missing is N+1 — the successor of the
    newest experiment, which is precisely the state an interrupted supersession
    leaves. Refusing it would make the interruption unrecoverable: every read
    raises, so the operation that would finish it cannot run either.
    `BatchLineage.pending_successor` names that state instead, and the guarded
    operations refuse on it (`experiments.py`) rather than the reader.

    That relaxation is for a batch still running, and `_require_ended_attempts`
    is where it ends: an outcome recorded over a successor nobody created has
    concluded a cohort whose newest attempt does not exist, and there is no
    recoverable reading of that to preserve.
    """

    for experiment in experiments:
        successor = experiment.decision.superseded_by if experiment.decision else None
        if successor is None:
            continue
        expected = format_experiment_id(experiment.batch_id, experiment.ordinal + 1)
        if successor != expected:
            relation = "itself" if successor == experiment.experiment_id else "another attempt"
            raise BatchError(
                f"{experiment.directory / EXPERIMENT_FILENAME}: names {successor!r} as its successor, which is "
                f"{relation}; superseding creates the replacement in the same operation, so the successor is the "
                f"next experiment in the series ({expected})"
            )


def _require_one_task_per_admission(batch: Batch, experiments: Iterable[Experiment]) -> None:
    """One task id belongs to one admission, across the batch's whole history.

    Grouped admission copies each draft to the task id its record names, so two
    admissions naming one task are two proposals with a single file between them.
    The record can then say neither which bytes that task implemented nor whose
    completion its one observation is — and that observation is what seals a
    round.
    """

    owners: dict[str, str] = {}
    for experiment in experiments:
        for task in experiment.admitted_tasks:
            admission = f"{experiment.experiment_id} as draft {task.draft_id!r}"
            previous = owners.setdefault(task.task_id, admission)
            if previous != admission:
                raise BatchError(
                    f"{batch.batch_id}: task {task.task_id!r} is admitted by {previous} and by {admission}; one "
                    "task implements one proposal, and a second draft admitted into it means a task id nothing "
                    "can be traced through"
                )


def _require_ended_attempts(
    batch: Batch,
    experiments: tuple[Experiment, ...],
    open_experiment: Experiment | None,
    outcome: Mapping[str, Any],
) -> None:
    """A batch's outcome is recorded after its last attempt ends — and an attempt
    that was never created has not ended either.

    Two shapes of the same contradiction. An open experiment under an outcome is
    the obvious one. The other is a supersession that recorded its decision and
    not the successor it names: that state is deliberately readable so the
    operation can be redone (`_require_named_successors`), but the relaxation is
    for a batch whose cycle is still running. An outcome over one concludes a
    cohort whose newest attempt does not exist — and it concludes it invisibly,
    because a batch that is no longer current is no longer where anything looks
    for a pending successor, so the redo that would finish it can no longer run
    and the next cohort is released over the wreckage.
    """

    if open_experiment is not None:
        raise BatchError(
            f"{batch.outcome_path}: this batch has concluded, but {open_experiment.experiment_id} carries no "
            "decision; a batch's outcome is recorded after its last experiment ends, never over an open one"
        )
    successor = _pending_successor(experiments)
    if successor is not None:
        raise BatchError(
            f"{batch.outcome_path}: this batch has concluded, while {experiments[-1].experiment_id} was "
            f"superseded by {successor}, which does not exist; an attempt nobody created has not ended either — "
            "remove this outcome, redo that supersession to finish it, and conclude the batch over what it "
            "actually did"
        )


def _pending_successor(experiments: Sequence[Experiment]) -> str | None:
    """The successor a supersession recorded but had not created yet, from a
    batch's experiments alone.

    Read from the newest experiment only, and that is the whole of it: ordinals
    run 1..N and a successor is N+1, so an existing successor would itself be the
    newest. `BatchLineage.pending_successor` is the same answer for a lineage
    that is already built; this is it for one still being checked.
    """

    if not experiments:
        return None
    decision = experiments[-1].decision
    return decision.superseded_by if decision is not None else None


def _require_one_promotion(
    config: EvolutionConfig,
    batch: Batch,
    experiments: Iterable[Experiment],
    outcome: Mapping[str, Any],
) -> None:
    """A concluded batch and every one of its experiments state one history.

    Two kinds of record, one event: the batch outcome ends the cycle and an
    experiment decision turns that attempt into history. The whole set has to
    agree, not only the record the outcome happens to name. A `no-change` batch
    over an experiment recording `promoted` says both that the source line moved
    and that it did not — the contradiction read from the side with nothing to
    name, which is why checking the named experiment alone never sees it. Two
    promoted experiments under one outcome leave the promotion trail two answers
    deep with no way to pick.
    """

    recorded = [
        experiment
        for experiment in experiments
        if experiment.decision is not None and experiment.decision.outcome == DECISION_PROMOTED
    ]
    if outcome["outcome"] != OUTCOME_PROMOTED:
        if recorded:
            raise BatchError(
                f"{batch.outcome_path}: concludes {outcome['outcome']!r}, while "
                f"{[experiment.experiment_id for experiment in recorded]} record a promotion; a batch whose "
                "candidate reached the source line concluded by promoting it, and invariant 7's no-change "
                "conclusion is for the batch that changed nothing"
            )
        return
    if len(recorded) > 1:
        raise BatchError(
            f"{batch.outcome_path}: {[experiment.experiment_id for experiment in recorded]} all record a "
            "promotion; one batch promotes one candidate, and the outcome names which of them it was"
        )

    # `read_outcome` has already refused a `promoted` outcome that names no
    # experiment, so the lookup below has something to look for.
    named = outcome["experiment_id"]
    promoted = {experiment.experiment_id: experiment for experiment in experiments}.get(named)
    if promoted is None:
        raise BatchError(
            f"{batch.outcome_path}: names {named!r} as the promoted experiment, but {batch.batch_id} has no such "
            "experiment; the outcome names the attempt whose record carries the decision"
        )
    decision = promoted.decision
    if decision is None or decision.outcome != DECISION_PROMOTED:
        recorded = decision.outcome if decision else "no decision"
        raise BatchError(
            f"{batch.outcome_path}: names {named!r} as promoted, but that experiment records {recorded!r}; "
            "one event, and the two records state it the same way"
        )
    if decision.promotion_revision != outcome["promotion_revision"]:
        raise BatchError(
            f"{batch.outcome_path}: promotes {outcome['promotion_revision']}, while {named} records "
            f"{decision.promotion_revision}; the promotion revision is one commit on the source line"
        )
    _require_checkable_merge_unit(config, batch, promoted, outcome)


def _require_checkable_merge_unit(
    config: EvolutionConfig,
    batch: Batch,
    promoted: Experiment,
    outcome: Mapping[str, Any],
) -> None:
    """The merge unit the outcome states is the one that was promoted.

    The outcome carries the round, the candidate, the source line it went onto
    and the tree because that is what makes the promotion revision *checkable*
    against the evidence rather than merely recorded beside it — and a claim
    nothing checks is a claim a schema-valid record can make freely. So the
    values are held to the three places they came from: the experiment's own
    prepared promotion, the completed replay that justified it, and, where this
    checkout holds the commit, Git.

    The commit is the strongest of the three and the only one outside this
    controller's own records, so it is asked whenever it can be — but its
    absence is a fact about the clone, not about the promotion (a repository
    that never fetched the source line holds neither), and a history that stops
    loading on a shallow clone would take every other reading with it. The two
    record-side checks answer everywhere.

    Read here rather than at each consumer because `status`, the promotion
    itself, and everything downstream read one derivation: a forged merge unit
    caught only where it is rendered is one every other reader still believes.
    """

    # Locally imported for `guards.require_readable_evidence`'s reason: `replay`
    # reads this module's records, so the dependency between the two points that
    # way and a module-level import would close the loop.
    from .replay import read_replays

    stated = outcome["promotion"]
    prepared = promoted.promotion
    if prepared is None:
        # Unreachable through `parse_experiment`, which refuses a `promoted`
        # decision stating no merge unit. Stated rather than assumed, because
        # everything below is the check that the outcome is not the only record
        # of what was promoted.
        raise BatchError(
            f"{batch.outcome_path}: names {promoted.experiment_id} as promoted, and that record states no merge "
            "unit for the outcome to be checked against"
        )
    unit = {
        "round": prepared.round_number,
        "candidate_revision": prepared.candidate_revision,
        "merge_input_revision": prepared.merge_input_revision,
        "merge_input_ref": prepared.merge_input_ref,
        "tree": prepared.tree,
        "planned_targets": list(prepared.planned_targets),
    }
    differing = sorted(field for field, value in unit.items() if stated[field] != value)
    if differing:
        raise BatchError(
            f"{batch.outcome_path}: states {differing} differently from the promotion {promoted.experiment_id} "
            f"was prepared as; the outcome states the merge unit that was promoted, which is the one the "
            "experiment recorded before the source line moved"
        )
    if outcome["reason"] != prepared.reason:
        raise BatchError(
            f"{batch.outcome_path}: concludes {outcome['reason']!r} while {promoted.experiment_id} was promoted "
            f"for {prepared.reason!r}; one decision, and the batch it ends is concluded by the reason it was "
            "promoted for"
        )

    measured = [
        run
        for run in read_replays(config, promoted).replays
        if run.completed
        and run.round_number == prepared.round_number
        and run.integration.candidate_revision == prepared.candidate_revision
        and run.integration.merge_input_revision == prepared.merge_input_revision
        and run.integration.merge_input_ref == prepared.merge_input_ref
        and run.integration.tree == prepared.tree
    ]
    if not measured:
        raise BatchError(
            f"{batch.outcome_path}: promotes the tree {prepared.tree[:12]} that round {prepared.round_number} of "
            f"{promoted.experiment_id} produced on {prepared.merge_input_ref}, and no completed run of that round "
            "measured that integration; what reaches the source line is a tree a replay measured (invariant 10)"
        )

    shape = commit_shape(config.repo_root, prepared.revision)
    if shape is None:
        return
    tree, parents = shape
    if tree != prepared.tree or parents != (prepared.merge_input_revision, prepared.candidate_revision):
        raise BatchError(
            f"{batch.outcome_path}: {prepared.revision[:12]} carries {tree[:12]} made from "
            f"{[parent[:12] for parent in parents]}, and the promotion is recorded as {prepared.tree[:12]} made "
            f"from {[prepared.merge_input_revision[:12], prepared.candidate_revision[:12]]}; the commit on the "
            "source line is what the record is held to"
        )


def _consumed_drafts(batch: Batch, experiments: Iterable[Experiment]) -> dict[str, str]:
    """Which experiment took each draft, refusing a draft two of them took.

    A lineage rule rather than a gate one, even though the gate is what reads the
    answer: a proposal admitted twice is two attempts implementing one set of
    bytes, whichever of them a reader is asking about.
    """

    consumed: dict[str, str] = {}
    for experiment in experiments:
        for draft_id in experiment.draft_ids:
            owner = consumed.setdefault(draft_id, experiment.experiment_id)
            if owner != experiment.experiment_id:
                raise BatchError(
                    f"draft {draft_id!r} of {batch.batch_id} is admitted by both {owner} and "
                    f"{experiment.experiment_id}; a draft is consumed once, and proposing the idea again means "
                    "a new draft id"
                )
    return consumed


def _gate(config: EvolutionConfig, batch: Batch, consumed: Mapping[str, str]) -> Gate:
    declined = tuple(entry["draft_id"] for entry in read_rejected_drafts(config, batch))
    both = sorted(set(declined) & set(consumed))
    if both:
        raise BatchError(
            f"{batch.rejected_drafts_path}: draft(s) {both} are recorded as declined and also admitted by an "
            "experiment; admitting and declining are both terminal for a proposal"
        )

    directory = batch.directory / analysis_task.PROPOSED_TASKS_DIRNAME
    present = sorted(path.name for path in directory.glob("*.md") if path.is_file()) if directory.is_dir() else []
    usable = [name for name in present if _DRAFT_ID.match(Path(name).stem)]
    spent = set(consumed) | set(declined)
    waiting = [Path(name).stem for name in usable]
    return Gate(
        waiting=tuple(draft_id for draft_id in waiting if draft_id not in spent),
        consumed=dict(sorted(consumed.items())),
        declined=tuple(sorted(declined)),
        missing=tuple(sorted(spent - set(waiting))),
        unusable=tuple(name for name in present if name not in usable),
    )


def _read_experiment(config: EvolutionConfig, directory: Path) -> Experiment:
    """One experiment record from disk."""

    path = directory / EXPERIMENT_FILENAME
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BatchError(
            f"{directory} has no {EXPERIMENT_FILENAME}; an experiment directory without its record names no "
            "base, no tasks, and no candidate — remove it or restore the record"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchError(f"unreadable experiment record {path}: {exc}") from exc
    if not isinstance(record, dict):
        raise BatchError(f"experiment record is not a JSON object: {path}")
    return parse_experiment(config, record, directory)


def parse_experiment(config: EvolutionConfig, record: Mapping[str, Any], directory: Path) -> Experiment:
    """One experiment record, validated and checked for the rules its schema
    cannot state.

    The implemented JSON Schema subset has no cross-field conditionals, so the
    shape tests that make a record one readable history live here: the id it
    sits under, the ref it claims, rounds that only append, a seal that waits
    for its tasks, a decision that carries exactly the fields its outcome means,
    and a prepared promotion naming a round this record sealed that ends as the
    decision it belongs to. Each of them is a way for a record to be
    individually well-formed and still describe two different histories.

    Public because the writers publish through it (`experiments.py`): an
    operation builds the next state of a record and validates it here before it
    lands, so a write cannot produce a record its own reader refuses. Stating
    those rules a second time on the write side is what would let the two drift
    — and the direction they drift in is a record this controller wrote and can
    no longer read.
    """

    path = directory / EXPERIMENT_FILENAME
    validate_or_raise(
        record,
        load_schema(config.schema_path(EXPERIMENT_SCHEMA_FILENAME)),
        description=f"experiment record {path}",
    )

    experiment_id = record["experiment_id"]
    if experiment_id != directory.name:
        raise BatchError(
            f"{path}: record names experiment {experiment_id!r} but sits in {directory.name!r}; an experiment's "
            "directory is its identity"
        )
    if experiment_batch_id(experiment_id) != record["batch_id"]:
        raise BatchError(
            f"{path}: experiment id {experiment_id!r} does not belong to batch {record['batch_id']!r}; the id is "
            "the batch id plus its ordinal"
        )
    if record["ref"] != experiment_ref(experiment_id):
        raise BatchError(
            f"{path}: claims ref {record['ref']!r}, but {experiment_id} works on {experiment_ref(experiment_id)!r}; "
            "one experiment, one durable ref"
        )

    rounds = tuple(_read_round(path, item) for item in record["rounds"])
    _require_appended_rounds(path, rounds)
    _require_one_admission_per_draft(path, rounds)
    decision = _read_decision(path, record["decision"], last=rounds[-1])
    # `.get`, because the field arrived after this schema version shipped: a
    # record written without it is not a record missing something, it is one
    # written before any promotion could be prepared.
    promotion = _read_promotion(path, record.get("promotion"), rounds=rounds, decision=decision)

    return Experiment(
        experiment_id=experiment_id,
        batch_id=record["batch_id"],
        created_at=record["created_at"],
        base_revision=record["base_revision"],
        base_release_ref=record["base_release_ref"],
        ref=record["ref"],
        rounds=rounds,
        decision=decision,
        directory=directory,
        promotion=promotion,
    )


def _read_round(path: Path, item: Mapping[str, Any]) -> Round:
    seal = item["seal"]
    tasks = tuple(
        AdmittedTask(
            task_id=task["task_id"],
            draft_id=task["draft_id"],
            draft_sha256=task["draft_sha256"],
            admitted_at=task["admitted_at"],
            completion_observed_at=task["completion_observed_at"],
        )
        for task in item["tasks"]
    )
    round_ = Round(
        number=item["round"],
        opened_at=item["opened_at"],
        reason=item["reason"],
        tasks=tasks,
        seal=Seal(sealed_at=seal["sealed_at"], candidate_revision=seal["candidate_revision"]) if seal else None,
    )
    if round_.candidate_ready and not round_.tasks:
        # An open round legitimately admits nothing for a while — `revise` opens
        # one so work can resume off the pinned candidate, and `add-tasks` fills
        # it. Sealing is where that stops being harmless: the pin is a candidate
        # revision evidence names and a promotion could carry, so a sealed round
        # with no admission behind it is canonical work that passed no gate
        # (invariants 9 and 16).
        raise BatchError(
            f"{path}: round {round_.number} pins a candidate but admitted nothing; a round is the task set "
            "admitted into it and the candidate that set produced, so this one names a revision no proposal "
            "accounts for"
        )
    if round_.candidate_ready and round_.unfinished:
        raise BatchError(
            f"{path}: round {round_.number} is sealed, but {list(round_.unfinished)} carry no completion "
            "observation; a candidate that does not contain the change it was admitted for is not what anyone "
            "means to measure (invariant 16)"
        )
    return round_


def _require_appended_rounds(path: Path, rounds: tuple[Round, ...]) -> None:
    """Rounds are numbered 1..N with no gaps, and only the last may be open.

    Both are invariant 15's "rounds only add" as a shape test. A gap means a
    round was removed, which takes its task selection and its candidate out of
    the history; an unsealed round with a later one after it means work resumed
    under a round that something has already measured against.
    """

    numbers = [round_.number for round_ in rounds]
    if numbers != list(range(1, len(rounds) + 1)):
        raise BatchError(
            f"{path}: rounds are numbered {numbers}; they are appended one at a time from 1 and none is ever removed"
        )
    unsealed = [round_.number for round_ in rounds if not round_.candidate_ready]
    if unsealed and unsealed != [len(rounds)]:
        raise BatchError(
            f"{path}: round(s) {unsealed} carry no seal while later rounds exist; a round is sealed before the "
            "next one opens, so every round but the last pins the candidate its evidence names"
        )


def _require_one_admission_per_draft(path: Path, rounds: tuple[Round, ...]) -> None:
    """A draft is consumed once, here as well as across experiments.

    Within one experiment the repeat is the easier mistake to make and the
    harder one to see: readmitting the same proposal in a later round produces a
    second task from bytes the first task already implemented, and the record
    would show one proposal answering for both.
    """

    counted: dict[str, int] = {}
    for round_ in rounds:
        for task in round_.tasks:
            counted[task.draft_id] = counted.get(task.draft_id, 0) + 1
    repeated = sorted(draft_id for draft_id, count in counted.items() if count > 1)
    if repeated:
        raise BatchError(
            f"{path}: draft(s) {repeated} are admitted more than once; a draft is consumed once, and proposing "
            "the same idea again means a new draft id"
        )


def _read_decision(path: Path, decision: Mapping[str, Any] | None, *, last: Round) -> Decision | None:
    """The terminal decision, with the pairings the schema subset cannot state.

    `promoted` is available only from a candidate-ready round, because what is
    promoted is the revision that round pinned and not the tip as it stood at
    decision time. `abandoned` and `superseded` may end an experiment whose last
    round was never sealed — an attempt dropped before it produced anything
    records no candidate rather than having one invented for it (invariant 7's
    rule, applied to experiments).
    """

    if decision is None:
        return None

    outcome = decision["outcome"]
    successor = decision["superseded_by"]
    promotion = decision["promotion_revision"]
    if (successor is not None) != (outcome == DECISION_SUPERSEDED):
        raise BatchError(
            f"{path}: only a {DECISION_SUPERSEDED!r} decision names a successor; this one is {outcome!r} and "
            f"names {successor!r}"
        )
    if (promotion is not None) != (outcome == DECISION_PROMOTED):
        raise BatchError(
            f"{path}: only a {DECISION_PROMOTED!r} decision names a promotion revision; this one is {outcome!r} "
            f"and names {promotion!r}"
        )
    if outcome == DECISION_PROMOTED and not last.candidate_ready:
        raise BatchError(
            f"{path}: promoted from round {last.number}, which was never sealed; what a promotion carries to the "
            "source line is the revision a candidate-ready round pinned, never an open round's tip (invariant 16)"
        )
    return Decision(
        outcome=outcome,
        decided_at=decision["decided_at"],
        reason=decision["reason"],
        superseded_by=successor,
        promotion_revision=promotion,
    )


def _read_promotion(
    path: Path,
    promotion: Mapping[str, Any] | None,
    *,
    rounds: tuple[Round, ...],
    decision: Decision | None,
) -> PreparedPromotion | None:
    """The merge unit a promotion was prepared as, checked against the rest of
    the record it sits in.

    Three pairings, and each is a way for the block to be well-formed and still
    describe a promotion this experiment did not have. It names a round *this*
    record sealed, at the candidate that seal pinned — a merge unit built on any
    other revision is evidence about a tree nothing here produced. It survives
    only into a `promoted` decision: an attempt that ended any other way and
    still carries a prepared promotion says the source line may be holding a
    merge for an experiment nobody promoted, which is the split the operation is
    ordered to prevent. And a `promoted` decision has one, because the revision
    alone is a commit nothing can afterwards be held to — the rule the batch
    outcome states from its own side.
    """

    if promotion is None:
        if decision is not None and decision.outcome == DECISION_PROMOTED:
            raise BatchError(
                f"{path}: records a promotion of {(decision.promotion_revision or '')[:12]} and not the merge "
                "unit it went as; a promotion revision on its own is a commit nothing can be held to, so the "
                "round, the candidate, the source line it was integrated onto, and the tree are recorded with it"
            )
        return None

    revision = promotion["revision"]
    if decision is not None and decision.outcome != DECISION_PROMOTED:
        raise BatchError(
            f"{path}: ended as {decision.outcome!r} while a promotion of {revision[:12]} is prepared on it; a "
            f"prepared promotion is either finished or discarded, and an attempt ended over one leaves "
            f"{promotion['merge_input_ref']} possibly carrying a merge no record explains"
        )
    if decision is not None and decision.promotion_revision != revision:
        raise BatchError(
            f"{path}: was promoted as {(decision.promotion_revision or '')[:12]} while the merge unit names "
            f"{revision[:12]}; one promotion, and the decision and the merge unit state the same commit"
        )
    number = promotion["round"]
    sealed = next((round_ for round_ in rounds if round_.number == number), None)
    if sealed is None or sealed.candidate_revision != promotion["candidate_revision"]:
        pinned = "no round of that number" if sealed is None else f"{(sealed.candidate_revision or 'nothing')[:12]}"
        raise BatchError(
            f"{path}: the prepared promotion names round {number} at candidate "
            f"{promotion['candidate_revision'][:12]}, and this record has {pinned}; what is promoted is the "
            "revision a candidate-ready round pinned (invariant 16)"
        )
    return PreparedPromotion(
        round_number=number,
        candidate_revision=promotion["candidate_revision"],
        merge_input_revision=promotion["merge_input_revision"],
        merge_input_ref=promotion["merge_input_ref"],
        tree=promotion["tree"],
        revision=revision,
        reason=promotion["reason"],
        planned_targets=tuple(promotion["planned_targets"]),
        prepared_at=promotion["prepared_at"],
    )
