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
treated differently. A ref that is absent, or ahead of where the record says it
should be, is reported as an observation rather than raised: the artifacts are
intact, the operator needs to be told which ref moved, and refusing to describe
the lifecycle is not how they find out. Guarded operations are what refuse on
an inconsistent ref; a reader names it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import analysis_task
from .config import (
    BATCH_ID_DIGITS,
    BATCH_ID_PREFIX,
    EXPERIMENT_SCHEMA_FILENAME,
    EvolutionConfig,
)
from .errors import BatchError
from .manifests import Batch, load_batches, read_outcome, read_rejected_drafts
from .revisions import contains, ref_tip
from .schema import load_schema, validate_or_raise

EXPERIMENT_FILENAME = "experiment.json"
EXPERIMENT_SCHEMA_VERSION = 1

# Identity and namespace, mirroring `experiment.schema.json`. The batch half is
# built from the config's own constants so the two spellings of a batch id
# cannot drift apart.
EXPERIMENT_ID_INFIX = "-exp-"
EXPERIMENT_ORDINAL_DIGITS = 2
REF_NAMESPACE = "refs/evolution/experiments"
_EXPERIMENT_ID = re.compile(
    rf"\A({re.escape(BATCH_ID_PREFIX)}[0-9]{{{BATCH_ID_DIGITS},}})"
    rf"{re.escape(EXPERIMENT_ID_INFIX)}([0-9]{{{EXPERIMENT_ORDINAL_DIGITS},}})\Z",
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

    @property
    def consistent(self) -> bool | None:
        """Whether the ref agrees with the record — None when this checkout
        cannot say, which is the ordinary answer for a clone that never fetched
        the namespace."""

        if self.state in (REF_ABSENT, REF_UNKNOWN):
            return None
        if self.state == REF_DIVERGED:
            return False
        return not (self.pinned_expected and self.state == REF_AHEAD)


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
    if len(current) > 1:
        raise BatchError(
            "more than one current batch: "
            + ", ".join(lineage.batch_id for lineage in current)
            + " — invariant 14 allows one at a time; record the earlier batch's outcome "
            "(promoted or no-change) before the next cohort continues"
        )
    return Lineage(batches=lineages, current=current[0] if current else None)


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
    """Where this experiment's ref sits, as far as this checkout can tell."""

    pinned = experiment.pinned_revision
    expected = experiment.last_round.candidate_ready
    tip = ref_tip(config.repo_root, experiment.ref)
    if tip is None:
        state = REF_ABSENT
    elif tip == pinned:
        state = REF_AT_PIN
    else:
        descends = contains(config.repo_root, pinned, tip)
        state = REF_UNKNOWN if descends is None else (REF_AHEAD if descends else REF_DIVERGED)
    return RefState(ref=experiment.ref, tip=tip, pinned=pinned, state=state, pinned_expected=expected)


def _batch_lineage(
    config: EvolutionConfig,
    batch: Batch,
    experiments: tuple[Experiment, ...],
) -> BatchLineage:
    _require_one_base(batch, experiments)
    _require_named_successors(batch, experiments)
    open_experiments = [experiment for experiment in experiments if experiment.open]
    if len(open_experiments) > 1:
        raise BatchError(
            f"{batch.batch_id} has more than one open experiment: "
            + ", ".join(experiment.experiment_id for experiment in open_experiments)
            + " — invariant 14 allows one; abandon or supersede the earlier attempt, which keeps it as evidence"
        )
    open_experiment = open_experiments[0] if open_experiments else None

    outcome = read_outcome(config, batch)
    if outcome is not None and open_experiment is not None:
        raise BatchError(
            f"{batch.outcome_path}: this batch has concluded, but {open_experiment.experiment_id} carries no "
            "decision; a batch's outcome is recorded after its last experiment ends, never over an open one"
        )
    if outcome is not None:
        _require_agreeing_promotion(batch, experiments, outcome)

    return BatchLineage(
        batch=batch,
        experiments=experiments,
        open_experiment=open_experiment,
        outcome=outcome,
        gate=_gate(config, batch, experiments),
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


def _require_named_successors(batch: Batch, experiments: Iterable[Experiment]) -> None:
    """A `superseded` decision names the experiment that replaced it, and that
    experiment has to exist here: superseding creates its successor in the same
    operation, so a name with nothing behind it is a broken half of one."""

    known = {experiment.experiment_id for experiment in experiments}
    for experiment in experiments:
        successor = experiment.decision.superseded_by if experiment.decision else None
        if successor is not None and successor not in known:
            raise BatchError(
                f"{experiment.directory / EXPERIMENT_FILENAME}: names {successor!r} as its successor, but no such "
                f"experiment exists in {batch.batch_id}; superseding records the replacement it creates"
            )


def _require_agreeing_promotion(
    batch: Batch,
    experiments: Iterable[Experiment],
    outcome: Mapping[str, Any],
) -> None:
    """A promoted batch and the experiment it names say the same thing.

    Two records, one event: the batch outcome ends the cycle and the experiment
    decision turns that attempt into history. Left unchecked they can disagree
    about which attempt reached the source line, or with which commit — and the
    promotion evidence trail is then two answers deep with no way to pick.
    """

    named = outcome["experiment_id"]
    if named is None:
        return
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


def _gate(config: EvolutionConfig, batch: Batch, experiments: Iterable[Experiment]) -> Gate:
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
    """One experiment record, validated and checked for the rules its schema
    cannot state.

    The implemented JSON Schema subset has no cross-field conditionals, so the
    shape tests that make a record one readable history live here: the id it
    sits under, the ref it claims, rounds that only append, a seal that waits
    for its tasks, and a decision that carries exactly the fields its outcome
    means. Each of them is a way for a record to be individually well-formed and
    still describe two different histories.
    """

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
