"""Whether a promotion actually improved the work that came after it.

Invariant 10 puts a replay between a candidate and the source line, and the
contract's last revision in play says why that is not the end of the evidence
chain: a promoted commit changes nothing an evaluation can see until targets
carry it, so the cohort that measures a release is the one whose reports were
produced at that effective revision. This module is that reading — one later
batch's answer about one earlier promotion.

**What is derived and what is recorded.** The release under assessment, the two
report cohorts, their denominators, the exclusions, and the comparability facets
all follow from two immutable manifests, the promoted batch's outcome, its
rollback record if it has one, and Git. So they are derived here on every read,
the way every other lifecycle reading in this package is, and the committed
record is checked against that derivation. What cannot be re-derived is what the
record exists for: measurements taken from machine-local evaluation artifacts, a
counterfactual run a harness made, and the verdict, confidence and rationale of
the session that judged them.

**The failure this exists to prevent** is a directional claim the cohorts cannot
support. Reports arrive from targets that were redeployed at different times, by
evaluators that move, on work of different shapes — so a difference in the
numbers between two cohorts is explained by the release only when nothing else
differs. Mixed or missing provenance therefore produces `inconclusive`, which is
a real result: knowing less is not evidence against a release, and a regression
claim is the one reading that costs somebody the change they promoted. A
suspected regression is answered by the counterfactual — the exact
pre-promotion and promoted revisions, one case set, one evaluator — because that
is the only comparison in which the release is the only difference.

The shape of the work is the difference nothing frozen states. Two cohorts are
two different task sets by construction, and no manifest version records what
kind of task each report judged, so that facet is carried as the unknown it is
rather than approximated by "the same repository appears on both sides". What
follows is not a weaker artifact but an honest division of labour: the cohorts
show the base rate and raise the suspicion, and a direction — in either sign — is
settled by the counterfactual.

**Vocabulary is shared, not restated.** The case set, evaluator, harness,
regression and result shapes are the replay record's own value objects: a
comparison across two spellings of "who judged this" could not make the
separation invariant 5 asks for. The one deliberate difference is the
measurement — a run states a quantity as `baseline` and `candidate`, and here
the two sides are cohorts produced before and after a release, so they are named
`before` and `after`. A counterfactual's numbers cross that boundary once, on
the way in: its baseline is the pre-promotion revision and its candidate the
promoted one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .config import ASSESSMENT_SCHEMA_FILENAME, EvolutionConfig
from .errors import BatchError
from .lineage import BatchLineage, Lineage
from .lineage import describe as describe_lineage
from .manifests import OUTCOME_PROMOTED, Batch, read_batch_record
from .replay import (
    BETTER_NEITHER,
    RESULT_COMPLETED,
    RESULT_FAILED,
    CaseSet,
    Evaluator,
    Exclusion,
    Harness,
    Regression,
)
from .revisions import contains, resolve_commit

ASSESSMENT_SCHEMA_VERSION = 1

VERDICT_IMPROVED = "improved"
VERDICT_NEUTRAL = "neutral"
VERDICT_REGRESSED = "regressed"
VERDICT_INCONCLUSIVE = "inconclusive"
# The three readings that say which way the release moved the work. `neutral` is
# one of them: "the release changed nothing" is a claim about a measured
# difference, and it needs the same comparable cohorts `improved` does.
# `inconclusive` is the answer when they are not there.
DIRECTIONAL_VERDICTS = frozenset({VERDICT_IMPROVED, VERDICT_NEUTRAL, VERDICT_REGRESSED})

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

SETTLEMENT_RETAIN = "retain"
SETTLEMENT_ROLLED_BACK = "rolled-back"

# Why a frozen report is in neither cohort. All three are about what its
# provenance can be shown to say, never about what its numbers came to.
EXCLUDED_REVISION_ABSENT = "effective-revision-absent"
EXCLUDED_REVISION_UNRESOLVABLE = "effective-revision-unresolvable"
EXCLUDED_POST_ROLLBACK = "post-rollback-effective-revision"

SIDE_BEFORE = "before"
SIDE_AFTER = "after"

# The respects in which the two cohorts have to be one cohort (invariant 5).
# Every one but the last is an equality facet: one value across both sides and
# none of it missing, because a cohort carrying two evaluator models is not one
# cohort and a provenance field nothing filled in cannot be shown to match.
FACET_EVALUATOR_BACKEND = "evaluator-backend"
FACET_EVALUATOR_MODEL = "evaluator-model"
FACET_EVALUATOR_RUBRIC = "evaluator-rubric-revision"
FACET_EVALUATOR_SCHEMA = "evaluator-schema-version"
FACET_RUNNER_REVISION = "runner-protocol-revision"
FACET_DEV_ROLE = "dev-role"
FACET_REVIEW_ROLE = "review-role"
# Whether the two cohorts did the same kind of work — and it is unknown on every
# manifest version. A frozen entry states identity, hashes, evaluator and
# deployment provenance and nothing about the shape of the task it judged, so
# invariant 4 keeps this missing rather than assumed. It is recorded all the same
# because of what its absence would otherwise be read as: the two cohorts are
# necessarily two different task sets (one report per completed task), so a
# difference in their numbers is explained by the work at least as well as by the
# release, and "at least one repository on both sides" is coverage rather than
# evidence that the work matched. A direction therefore cannot rest on the
# cohorts alone here; what settles one is the counterfactual, where the case set
# is pinned and the release is the only difference. The facet becomes answerable
# the day a manifest version carries durable task-shape provenance — a change to
# what the feed reports, not to this reading.
FACET_TASK_SHAPE = "task-shape"
# The weaker question the evidence supports, and the one facet whose rule is not
# equality: at least one repository present on both sides. Requiring the same set
# would refuse every real comparison, and requiring nothing would compare two
# projects and call the difference a release effect. It is also the facet an
# empty cohort fails, which is why `Comparability.coherent` can stay exactly
# "every facet is coherent".
FACET_REPOSITORY_COVERAGE = "repository-coverage"

# What a role's configuration is, as one value. Spelled out rather than compared
# field by field so the facet list stays the size of the question it answers.
_ROLE_FIELDS = ("agent", "model", "effort", "profile")


@dataclass(frozen=True)
class Subject:
    """The release an assessment is about, as the promoted batch recorded it.

    Read from that batch's `outcome.json` and never from the experiment's own
    prepared promotion: a promotion made at experiment schema version 1 states
    its revision alone, so on those the outcome is the only record carrying the
    merge unit. The before/after pair needs no reconstruction either — the
    pre-promotion revision is the merge input the outcome states, which is the
    promotion's first parent.
    """

    batch_id: str
    experiment_id: str
    revision: str
    round_number: int
    candidate_revision: str
    merge_input_revision: str
    merge_input_ref: str
    tree: str
    # The targets the promotion was *planned* for. A plan, never a deployment:
    # what a target holds is per target and read from its own deploy receipt, and
    # reading this list as the deployed cohort is exactly the false comparability
    # this module refuses. Kept because it is what an operator compares the
    # cohort's actual effective revisions against.
    planned_targets: tuple[str, ...]
    # Whether the source line still carries the promotion. False only for one a
    # completed rollback reversed; a rollback still in flight leaves it standing,
    # because that is the state the records describe.
    standing: bool
    # The inverse commit, when one is recorded — in flight or completed. Present
    # for an in-flight rollback while `standing` is still True: the commit exists,
    # and a report produced at a line that took it did not carry the change.
    rollback_revision: str | None

    @property
    def reversed_promotion(self) -> bool:
        """Whether this is an assessment of a release the line no longer holds.

        A different question from an assessment of a standing one, not the same
        question with a caveat: the cohort after a rollback was produced at a
        revision the change is not in, so what it can say is what the reversal
        did rather than what the release did.
        """

        return not self.standing


@dataclass(frozen=True)
class Member:
    """One frozen report, with the facts a comparison is drawn from.

    `batch_id` is the manifest it belongs to. The two cohorts come from two
    immutable manifests — the assessed batch's and the assessing one's — so a
    report cannot be traced back to a denominator without it.
    """

    report_key: str
    batch_id: str
    repo_id: str
    task_id: str
    effective_revision: str | None
    facets: Mapping[str, str | None]


@dataclass(frozen=True)
class Cohort:
    """One side of the comparison, and the denominator it comes to."""

    members: tuple[Member, ...]

    @property
    def report_keys(self) -> tuple[str, ...]:
        return tuple(member.report_key for member in self.members)

    @property
    def task_count(self) -> int:
        """Unique completed tasks — the unit invariant 1 counts in, so an
        evaluator rerun does not inflate a cohort into significance."""

        return len({(member.repo_id, member.task_id) for member in self.members})


@dataclass(frozen=True)
class Excluded:
    """A frozen report neither cohort could take, and why."""

    report_key: str
    batch_id: str
    reason: str
    detail: str


@dataclass(frozen=True)
class Facet:
    """One respect in which the cohorts either are or are not one cohort."""

    facet: str
    coherent: bool
    before: tuple[str | None, ...]
    after: tuple[str | None, ...]


@dataclass(frozen=True)
class Comparability:
    """Whether the release is the only difference between the two cohorts."""

    facets: tuple[Facet, ...]

    @property
    def coherent(self) -> bool:
        return all(facet.coherent for facet in self.facets)

    @property
    def incoherent(self) -> tuple[str, ...]:
        """The facets that differ, named. "Not comparable" without them is not
        something an operator or a rationale can act on."""

        return tuple(facet.facet for facet in self.facets if not facet.coherent)


@dataclass(frozen=True)
class Measurement:
    """One quantity, on the cohort produced before the release and on the one
    produced after it."""

    metric: str
    unit: str
    before: float | None
    after: float | None
    better: str

    @property
    def goal(self) -> bool:
        """Whether this is something the release was meant to move, as opposed to
        an observation recorded beside it (invariant 13)."""

        return self.better != BETTER_NEITHER

    @property
    def directional(self) -> bool:
        """A goal with both sides measured — the only kind of quantity a
        directional verdict can rest on."""

        return self.goal and self.before is not None and self.after is not None


@dataclass(frozen=True)
class Position:
    """The harness key a counterfactual run occupied.

    A conforming harness answers one key with one run, so this has to be a
    position no experiment holds — neither a recorded run nor a request it
    withdrew, since both stay allocated and neither is ever reissued.
    """

    experiment_id: str
    round_number: int
    attempt: int


@dataclass(frozen=True)
class Pinned:
    """The two revisions a counterfactual exercised, and the tree the promoted
    half carries.

    `base_revision` is the pre-promotion commit and `candidate_revision` the
    promotion itself, so the pair is the assessed release exactly as its outcome
    states it. Both are exercised without moving the source line; `source_ref`
    names the line they belong to rather than one the run touches.
    """

    base_revision: str
    candidate_revision: str
    source_ref: str
    tree: str


@dataclass(frozen=True)
class RunResult:
    """How a counterfactual ended."""

    outcome: str
    concluded_at: str
    detail: str
    elapsed_seconds: float | None
    metrics: tuple[Measurement, ...]
    regressions: tuple[Regression, ...]
    ambiguity: str | None

    @property
    def completed(self) -> bool:
        return self.outcome == RESULT_COMPLETED


@dataclass(frozen=True)
class Counterfactual:
    """The pinned two-revision run that answers a suspected regression.

    Recorded here rather than in the promoted experiment's `replays.json`, which
    binds every run to a round and to the candidate that round's seal pinned — a
    pre-promotion/promoted pair is neither. The lineage also reads that record on
    every derivation, so an entry it could not account for would stop `status`
    for the whole history instead of for this comparison.
    """

    position: Position
    integration: Pinned
    cases: CaseSet
    evaluator: Evaluator
    harness: Harness
    expectation: str
    started_at: str
    result: RunResult | None

    @property
    def completed(self) -> bool:
        return self.result is not None and self.result.completed


@dataclass(frozen=True)
class Decision:
    """The human settlement of one assessment: the gate between a release and
    the next base freeze."""

    settlement: str
    decided_at: str
    reason: str
    rollback_revision: str | None


@dataclass(frozen=True)
class Frame:
    """The comparison a batch's frozen provenance supports, derived.

    Not a verdict and not evidence: it is the question, with the denominator and
    the exclusions visible. Formation reads it to know what may be claimed, and
    every later read of a recorded assessment is checked against it.
    """

    batch: Batch
    subject: Subject
    before: Cohort
    after: Cohort
    excluded: tuple[Excluded, ...]
    comparability: Comparability
    # Every report the two frozen manifests name, placed or not. Which side a
    # report is on is a reading of Git and differs between clones; this is the
    # committed membership, which does not — so the checks that have to hold
    # everywhere are taken from here rather than from what this checkout placed:
    # the denominators (unique completed tasks) and the comparability facets are
    # both manifest facts, and a clone missing an object would otherwise skip them
    # and accept a count or a facet list nobody could have derived.
    catalog: tuple[Member, ...]
    # The unique-task count each cohort needs before a directional claim is
    # admissible. The analysis policy's own minimum cluster size: a claim about
    # the release is a cluster-level claim about unique completed tasks, and a
    # second threshold for it would be a second policy for one question.
    minimum_task_count: int
    # True when this batch is the one that owes the assessment — the first batch
    # frozen after the promotion. A later batch derives the same frame for
    # reading the record, and owes nothing itself.
    owed: bool

    @property
    def batch_id(self) -> str:
        return self.batch.batch_id

    @property
    def unverified(self) -> tuple[str, ...]:
        """Reports this checkout could not place, because it cannot resolve what
        the target held. A fact about the clone rather than about the release —
        reported, never read as agreement."""

        return tuple(
            item.report_key for item in self.excluded if item.reason == EXCLUDED_REVISION_UNRESOLVABLE
        )

    @property
    def cohorts_support_direction(self) -> bool:
        """Whether the two cohorts could carry `improved`, `neutral` or
        `regressed` between them — before any measurement is taken.

        The cohorts alone, deliberately: a direction may also rest on a completed
        counterfactual, which is evidence a frame knows nothing about. False for
        every frame while no manifest states the shape of the work.
        """

        return directional_admissible(
            coherent=self.comparability.coherent,
            before_task_count=self.before.task_count,
            after_task_count=self.after.task_count,
            minimum=self.minimum_task_count,
        )

    def placement(self, report_key: str) -> str | None:
        """Which side this checkout places one report on, or None when it places
        it nowhere — excluded, or not a member of either frozen manifest."""

        if report_key in self.before.report_keys:
            return SIDE_BEFORE
        if report_key in self.after.report_keys:
            return SIDE_AFTER
        return None

    def exclusion(self, report_key: str) -> Excluded | None:
        for item in self.excluded:
            if item.report_key == report_key:
                return item
        return None

    @property
    def members(self) -> tuple[Member, ...]:
        return self.before.members + self.after.members


@dataclass(frozen=True)
class Assessment:
    """One recorded reading of whether a release worked."""

    batch_id: str
    subject: Subject
    before: tuple[str, ...]
    before_task_count: int
    after: tuple[str, ...]
    after_task_count: int
    excluded: tuple[Excluded, ...]
    comparability: Comparability
    metrics: tuple[Measurement, ...]
    counterfactual: Counterfactual | None
    verdict: str
    confidence: str
    rationale: str
    formed_at: str
    decision: Decision | None
    path: Path | None = None

    @property
    def settled(self) -> bool:
        """Whether the human gate has answered. An unsettled assessment is
        evidence waiting for a decision, which is a different state from a batch
        that owes an assessment entirely."""

        return self.decision is not None

    @property
    def directional(self) -> bool:
        return self.verdict in DIRECTIONAL_VERDICTS

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": ASSESSMENT_SCHEMA_VERSION,
            "batch_id": self.batch_id,
            "assessed": {
                "batch_id": self.subject.batch_id,
                "experiment_id": self.subject.experiment_id,
                "revision": self.subject.revision,
                "round": self.subject.round_number,
                "candidate_revision": self.subject.candidate_revision,
                "merge_input_revision": self.subject.merge_input_revision,
                "merge_input_ref": self.subject.merge_input_ref,
                "tree": self.subject.tree,
                "standing": self.subject.standing,
                "rollback_revision": self.subject.rollback_revision,
            },
            "cohorts": {
                "before": {"report_keys": list(self.before), "task_count": self.before_task_count},
                "after": {"report_keys": list(self.after), "task_count": self.after_task_count},
                "excluded": [
                    {
                        "report_key": item.report_key,
                        "batch_id": item.batch_id,
                        "reason": item.reason,
                        "detail": item.detail,
                    }
                    for item in self.excluded
                ],
            },
            "comparability": {
                "coherent": self.comparability.coherent,
                "facets": [
                    {
                        "facet": facet.facet,
                        "coherent": facet.coherent,
                        "before": list(facet.before),
                        "after": list(facet.after),
                    }
                    for facet in self.comparability.facets
                ],
            },
            "metrics": [_measurement_json(measurement) for measurement in self.metrics],
            "counterfactual": None if self.counterfactual is None else _counterfactual_json(self.counterfactual),
            "verdict": self.verdict,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "formed_at": self.formed_at,
            "decision": None
            if self.decision is None
            else {
                "settlement": self.decision.settlement,
                "decided_at": self.decision.decided_at,
                "reason": self.decision.reason,
                "rollback_revision": self.decision.rollback_revision,
            },
        }


def directional_admissible(
    *,
    coherent: bool,
    before_task_count: int,
    after_task_count: int,
    minimum: int,
) -> bool:
    """Whether cohorts of this shape may carry a directional verdict on their own.

    One rule, asked by both sides of the artifact: formation asks it of the
    frame it derived, and every read asks it of the cohorts the record states.
    Two answers to "may this claim be made" would let a record be written that
    nothing afterwards accepts, or accepted where it could not have been written.

    A direction may also rest on a completed counterfactual, which is not a
    property of the cohorts and so is not asked here — the reader composes the
    two paths.
    """

    return coherent and before_task_count >= minimum and after_task_count >= minimum


def subject(lineage: Lineage, batch: Batch) -> Subject | None:
    """The release `batch`'s cohort would be assessing, or None when there is
    none before it.

    Read from records alone — no Git — because this is also what the analysis
    task a freeze generates states, and a task's text may not depend on which
    objects the machine writing it happens to hold.

    "The newest promotion before this batch" rather than the newest promotion
    outright: a batch assesses the release its own reports were produced after,
    and a promotion made later is one its cohort could not have seen.
    """

    promoted = _last_promoted_before(lineage, batch)
    if promoted is None:
        return None
    outcome = promoted.outcome or {}
    # Present on every `promoted` outcome: `read_outcome` refuses one that states
    # the revision without the merge unit it went as.
    merge = outcome["promotion"]
    record = promoted.rollback
    return Subject(
        batch_id=promoted.batch_id,
        experiment_id=outcome["experiment_id"],
        revision=outcome["promotion_revision"],
        round_number=merge["round"],
        candidate_revision=merge["candidate_revision"],
        merge_input_revision=merge["merge_input_revision"],
        merge_input_ref=merge["merge_input_ref"],
        tree=merge["tree"],
        planned_targets=tuple(merge["planned_targets"]),
        standing=promoted.promotion_effective,
        rollback_revision=None if record is None else record["revision"],
    )


def owed_by(lineage: Lineage, batch: Batch) -> Subject | None:
    """The release `batch` itself owes a reading of, or None when it owes none.

    The obligation belongs to the *first* cohort frozen after a promotion, which
    is the one whose reports are the earliest evidence about it. A later batch
    still derives the same subject — that is how it reads the record — and owes
    nothing, because the reading was taken and settled before it could freeze
    (invariant 14 keeps the batches in a series).

    Read from records alone, so the freeze that generates an analysis task can
    ask it without depending on which Git objects the machine holds.
    """

    assessed = subject(lineage, batch)
    if assessed is None or not _owed(lineage, batch, assessed):
        return None
    return assessed


def describe(
    config: EvolutionConfig,
    batch: Batch,
    *,
    lineage: Lineage | None = None,
) -> Frame | None:
    """The comparison `batch`'s frozen provenance supports. Read-only.

    None when there is no promotion before this batch to assess — a repository
    that has never promoted, or one whose only promotions came later. That is
    the answer for a `no-change` predecessor too: `no-change` fabricates no
    revision (invariant 7), so there is nothing an upgrade effect could be
    measured against and none is invented.
    """

    known = lineage if lineage is not None else describe_lineage(config)
    assessed = subject(known, batch)
    if assessed is None:
        return None

    promoted = next(item for item in known.batches if item.batch_id == assessed.batch_id)
    members = tuple(_members(promoted.batch)) + tuple(_members(batch))

    before: list[Member] = []
    after: list[Member] = []
    excluded: list[Excluded] = []
    # Several reports ordinarily name one effective revision, and each answer
    # costs a Git process. Cached per revision string, which is what a report
    # actually states — resolving it is half the question.
    answers: dict[str, tuple[str | None, str | None]] = {}
    for member in members:
        side, exclusion = _place(config, member, assessed, answers)
        if exclusion is not None:
            excluded.append(exclusion)
        elif side == SIDE_BEFORE:
            before.append(member)
        else:
            after.append(member)

    before_cohort = Cohort(members=tuple(before))
    after_cohort = Cohort(members=tuple(after))
    return Frame(
        batch=batch,
        subject=assessed,
        before=before_cohort,
        after=after_cohort,
        excluded=tuple(excluded),
        comparability=_comparability(before_cohort, after_cohort),
        catalog=members,
        minimum_task_count=config.analysis.minimum_cluster_task_count,
        owed=_owed(known, batch, assessed),
    )


def describe_current(config: EvolutionConfig, *, lineage: Lineage | None = None) -> Frame | None:
    """The frame of the batch whose change cycle is running, or None when no
    batch is current or none has a release to assess."""

    known = lineage if lineage is not None else describe_lineage(config)
    if known.current is None:
        return None
    return describe(config, known.current.batch, lineage=known)


def read(
    config: EvolutionConfig,
    batch: Batch,
    *,
    lineage: Lineage | None = None,
    frame: Frame | None = None,
) -> Assessment | None:
    """The assessment `batch` recorded, or None when it has none.

    Absence is the ordinary answer: most batches have no release before them to
    assess, and one that does has nothing recorded until its analysis forms it.

    Validated and then held to the repository, the way every other committed
    record here is. A record about a release this batch does not follow, over
    reports no frozen manifest names, or carrying a directional verdict its own
    cohorts cannot support is refused rather than read — the last of those is the
    whole point of the artifact, and a rule only the writer keeps is one any file
    written beside it escapes.
    """

    record = read_batch_record(
        config,
        batch,
        batch.assessment_path,
        schema_filename=ASSESSMENT_SCHEMA_FILENAME,
        description="release assessment record",
        foreign="one batch's cohort cannot record another's reading of a release",
    )
    if record is None:
        return None
    derived = frame if frame is not None else describe(config, batch, lineage=lineage)
    return parse(config, record, batch, frame=derived)


def parse(
    config: EvolutionConfig,
    record: Mapping[str, Any],
    batch: Batch,
    *,
    frame: Frame | None,
) -> Assessment:
    """One validated record as an `Assessment`, with the rules the schema subset
    cannot express.

    Two kinds of check, and they are kept apart deliberately. The record's
    internal consistency — the verdict against the comparability, the cohorts and
    the counterfactual it states — is machine-independent, so it is enforced
    everywhere and a record that passes it stays readable on every clone. Its
    agreement with the repository is enforced as far as the checkout can answer:
    a report placed on a side Git contradicts is refused, and a report this clone
    cannot resolve at all is left as the unanswered question it is.
    """

    path = batch.assessment_path
    if frame is None:
        raise BatchError(
            f"{path}: this batch follows no promotion, so there is no release for an assessment to be about; "
            "a repository that promoted nothing before this cohort has nothing to assess, and a record here "
            "names a release its own lineage does not have"
        )

    assessed = _read_subject(path, record["assessed"], frame.subject)
    cohorts = record["cohorts"]
    before = tuple(cohorts["before"]["report_keys"])
    after = tuple(cohorts["after"]["report_keys"])
    excluded = tuple(
        Excluded(
            report_key=item["report_key"],
            batch_id=item["batch_id"],
            reason=item["reason"],
            detail=item["detail"],
        )
        for item in cohorts["excluded"]
    )
    comparability = Comparability(
        facets=tuple(
            Facet(
                facet=item["facet"],
                coherent=item["coherent"],
                before=tuple(item["before"]),
                after=tuple(item["after"]),
            )
            for item in record["comparability"]["facets"]
        )
    )
    metrics = tuple(_read_measurement(entry) for entry in record["metrics"])
    counterfactual = _read_counterfactual(path, record["counterfactual"], assessed)
    # The settlement is held to the rollback record as the lineage reads it now,
    # never to the historical `assessed` block above: the ordinary flow forms the
    # assessment while the promotion stands, and the rollback the reading caused
    # comes afterwards. Validating the decision against the state at formation
    # time would refuse every settlement this gate actually produces.
    decision = _read_decision(path, record["decision"], frame.subject)

    # Asked of every record, whatever it concluded: a quantity stated twice or a
    # direction with nothing to point away from is a malformed measurement, and an
    # `inconclusive` reading is read by the next cohort like any other.
    _require_distinct_metrics(path, "the cohort comparison", metrics)
    _require_one_side(path, before, after, excluded)
    # Membership first: everything below reads the frozen manifests through the
    # keys this record states, which is only meaningful once they are known to be
    # keys the manifests have.
    _require_membership(path, before, after, excluded, frame)
    _require_stated_comparability(path, record["comparability"], comparability, before, after, frame)
    _require_counts(path, record, before, after, frame)
    _require_placement(path, before, after, excluded, frame)
    _require_supported_verdict(
        path,
        verdict=record["verdict"],
        comparability=comparability,
        before_task_count=cohorts["before"]["task_count"],
        after_task_count=cohorts["after"]["task_count"],
        minimum=frame.minimum_task_count,
        metrics=metrics,
        counterfactual=counterfactual,
    )

    return Assessment(
        batch_id=record["batch_id"],
        subject=assessed,
        before=before,
        before_task_count=cohorts["before"]["task_count"],
        after=after,
        after_task_count=cohorts["after"]["task_count"],
        excluded=excluded,
        comparability=comparability,
        metrics=metrics,
        counterfactual=counterfactual,
        verdict=record["verdict"],
        confidence=record["confidence"],
        rationale=record["rationale"],
        formed_at=record["formed_at"],
        decision=decision,
        path=path,
    )


def _owed(lineage: Lineage, batch: Batch, assessed: Subject) -> bool:
    """Whether `batch` is the first cohort frozen after that promotion."""

    promoted = next(item for item in lineage.batches if item.batch_id == assessed.batch_id)
    following = lineage.after(promoted)
    return bool(following) and following[0].batch_id == batch.batch_id


def _last_promoted_before(lineage: Lineage, batch: Batch) -> BatchLineage | None:
    """The newest batch that promoted, among those frozen before `batch`.

    Batch ids are allocated in sequence, so "before" is a position in the
    lineage's own order rather than a timestamp comparison — the same order
    `Lineage.after` reads.
    """

    promoted: BatchLineage | None = None
    for item in lineage.batches:
        if item.batch_id == batch.batch_id:
            break
        if item.outcome is not None and item.outcome["outcome"] == OUTCOME_PROMOTED:
            promoted = item
    return promoted


def _members(batch: Batch) -> list[Member]:
    """One batch's frozen reports, with the facets a comparison reads.

    A version-1 manifest carries identity and content hashes and no cohort
    provenance at all, which is not a defect in this reading: those reports
    simply cannot say what the target held, so they are placed nowhere and
    excluded with that reason (invariant 4 keeps a missing field missing).
    """

    members: list[Member] = []
    for report in batch.reports:
        evaluator = report.get("evaluator") or {}
        provenance = report.get("provenance") or {}
        effective = provenance.get("effective_revision")
        members.append(
            Member(
                report_key=str(report["report_key"]),
                batch_id=batch.batch_id,
                repo_id=str(report["repo_id"]),
                task_id=str(report["task_id"]),
                effective_revision=effective if isinstance(effective, str) and effective else None,
                facets={
                    FACET_EVALUATOR_BACKEND: _text(evaluator.get("backend")),
                    FACET_EVALUATOR_MODEL: _text(evaluator.get("model")),
                    FACET_EVALUATOR_RUBRIC: _text(evaluator.get("rubric_revision")),
                    FACET_EVALUATOR_SCHEMA: _text(evaluator.get("schema_version")),
                    FACET_RUNNER_REVISION: _text(provenance.get("runner_protocol_revision")),
                    FACET_DEV_ROLE: _role(provenance.get("dev")),
                    FACET_REVIEW_ROLE: _role(provenance.get("review")),
                    # Nothing to read: no manifest version has a field for the
                    # shape of the work, and both close `additionalProperties`.
                    # The honest value is the missing one (see the constant), and
                    # this is the single place a later version would be read.
                    FACET_TASK_SHAPE: None,
                    FACET_REPOSITORY_COVERAGE: str(report["repo_id"]),
                },
            )
        )
    return members


def _place(
    config: EvolutionConfig,
    member: Member,
    assessed: Subject,
    answers: dict[str, tuple[str | None, str | None]],
) -> tuple[str | None, Excluded | None]:
    """Which side one report belongs on, or why it belongs on neither.

    The question is about the revision that target actually held, and it is asked
    of Git: a revision carrying the promotion in its history was produced with
    the change, whether it is the promotion itself or something later built on
    it. Equality would place only the targets redeployed at exactly that commit,
    and read every later one as pre-release evidence.

    The rollback is asked first when there is one, because an inverse commit
    descends from the promotion: a line that took the reversal contains the
    promotion too, and reading it as `after` would count a target that gave the
    change back as a target that had it. It is neither cohort — not the release,
    and not the state before it — and it is asked of an inverse still in flight as
    well, since what matters is whether that commit reached the line the report
    was produced at, not whether this controller has finished recording it.
    """

    if member.effective_revision is None:
        return None, Excluded(
            report_key=member.report_key,
            batch_id=member.batch_id,
            reason=EXCLUDED_REVISION_ABSENT,
            detail="the report states no effective revision, so nothing places it on either side",
        )

    stated = member.effective_revision
    if stated not in answers:
        answers[stated] = _resolve(config, stated, assessed)
    resolved, side = answers[stated]
    if resolved is None:
        return None, Excluded(
            report_key=member.report_key,
            batch_id=member.batch_id,
            reason=EXCLUDED_REVISION_UNRESOLVABLE,
            detail=f"this checkout cannot resolve the effective revision {stated!r} to a commit",
        )
    if side is None:
        return None, Excluded(
            report_key=member.report_key,
            batch_id=member.batch_id,
            reason=EXCLUDED_POST_ROLLBACK,
            detail=(
                f"effective revision {stated!r} carries the inverse commit that took this promotion back off "
                "the line, so that target held neither the release nor the state before it"
            ),
        )
    return side, None


def _resolve(
    config: EvolutionConfig,
    stated: str,
    assessed: Subject,
) -> tuple[str | None, str | None]:
    """One effective revision, resolved and placed: `(commit, side)`.

    A commit with no side is a post-rollback line. No commit at all is a
    question this clone cannot answer — an unresolvable revision, or an ancestry
    check Git could not make because the objects are not here. Both are the same
    answer for a reader: not placed, and reported as such rather than guessed.
    """

    commit = resolve_commit(config.repo_root, stated)
    if commit is None:
        return None, None
    if assessed.rollback_revision is not None:
        reversed_here = contains(config.repo_root, assessed.rollback_revision, commit)
        if reversed_here is None:
            return None, None
        if reversed_here:
            return commit, None
    promoted = contains(config.repo_root, assessed.revision, commit)
    if promoted is None:
        return None, None
    return commit, SIDE_AFTER if promoted else SIDE_BEFORE


def _comparability(before: Cohort, after: Cohort) -> Comparability:
    """The facet-by-facet reading of whether the release is the only difference.

    Every facet but repository coverage is equality: one value across both
    cohorts and none of it missing. Repository coverage asks for an overlap
    instead, which is the weakest fact that makes the comparison about a protocol
    revision rather than about two projects — and, being an overlap, it is also
    what an empty cohort fails.

    Task shape is an equality facet nothing fills in, so it is the facet no
    cohort comparison currently passes. That is the reading rather than a gap in
    it: the cohorts are two different task sets, and until a manifest states what
    kind of work each report judged, a direction is settled by the counterfactual
    and not by the numbers the two sets came to.
    """

    facets: list[Facet] = []
    for name in (
        FACET_EVALUATOR_BACKEND,
        FACET_EVALUATOR_MODEL,
        FACET_EVALUATOR_RUBRIC,
        FACET_EVALUATOR_SCHEMA,
        FACET_RUNNER_REVISION,
        FACET_DEV_ROLE,
        FACET_REVIEW_ROLE,
        FACET_TASK_SHAPE,
        FACET_REPOSITORY_COVERAGE,
    ):
        before_values = _values(before, name)
        after_values = _values(after, name)
        if name == FACET_REPOSITORY_COVERAGE:
            coherent = bool(set(before_values) & set(after_values))
        else:
            union = set(before_values) | set(after_values)
            coherent = len(union) == 1 and None not in union
        facets.append(Facet(facet=name, coherent=coherent, before=before_values, after=after_values))
    return Comparability(facets=tuple(facets))


def _stated_comparability(frame: Frame, before: Sequence[str], after: Sequence[str]) -> Comparability:
    """The facets the cohorts a record states come to, read off the frozen
    manifests alone.

    The same derivation `describe` runs, over the record's own sides rather than
    the ones this checkout placed — which is what makes it answerable on a clone
    that cannot resolve an effective revision. Every key is a member here because
    membership was established first.
    """

    catalog = {member.report_key: member for member in frame.catalog}
    return _comparability(
        Cohort(members=tuple(catalog[key] for key in before)),
        Cohort(members=tuple(catalog[key] for key in after)),
    )


def _values(cohort: Cohort, facet: str) -> tuple[str | None, ...]:
    """The distinct values one cohort carries for one facet, sorted, with a
    missing one kept as null rather than dropped."""

    seen = {member.facets.get(facet) for member in cohort.members}
    return tuple(sorted((value for value in seen if value is not None))) + ((None,) if None in seen else ())


def _read_subject(path: Path, stated: Mapping[str, Any], derived: Subject) -> Subject:
    """The release this record says it assessed, held to the one its lineage has.

    The merge unit is compared in full. A record naming the right promotion with
    a different candidate, merge input or tree describes a release nobody made,
    and it is exactly the substitution the contract's Revisions in play exists to
    prevent — the counterfactual is pinned from these values.

    `standing` is the one field not compared to the current reading, and
    deliberately: the ordinary consequence of a regression finding is the
    rollback that follows it, so re-deriving it would make every such rollback
    contradict the assessment that justified it. What is checked is the other
    direction — a reversal this record asserts must be one the repository
    recorded, since a claim that the line no longer carries a release is not one
    an assessment may make on its own.
    """

    fields = (
        ("batch_id", derived.batch_id),
        ("experiment_id", derived.experiment_id),
        ("revision", derived.revision),
        ("round", derived.round_number),
        ("candidate_revision", derived.candidate_revision),
        ("merge_input_revision", derived.merge_input_revision),
        ("merge_input_ref", derived.merge_input_ref),
        ("tree", derived.tree),
    )
    for name, expected in fields:
        if stated[name] != expected:
            raise BatchError(
                f"{path}: the assessment states {name} {stated[name]!r} for the release it assessed, but the "
                f"promotion this batch follows records {expected!r}; an assessment of another release is not "
                "this batch's reading of the one before it"
            )

    standing = stated["standing"]
    reversal = stated["rollback_revision"]
    if standing and reversal is not None:
        raise BatchError(
            f"{path}: the assessment records the promotion as standing and names the inverse commit "
            f"{reversal[:12]} that reversed it; one of the two is what was assessed"
        )
    if not standing:
        if reversal is None:
            raise BatchError(
                f"{path}: the assessment records the promotion as no longer standing and names no inverse "
                "commit; a reversal is a commit, and an assessment of one states which"
            )
        if derived.rollback_revision != reversal:
            raise BatchError(
                f"{path}: the assessment says the promotion was reversed by {reversal[:12]}, which "
                + (
                    "no rollback record beside that batch's outcome names"
                    if derived.rollback_revision is None
                    else f"disagrees with the rollback record's own {derived.rollback_revision[:12]}"
                )
                + "; what came off the source line is the rollback record's to state"
            )

    return Subject(
        batch_id=stated["batch_id"],
        experiment_id=stated["experiment_id"],
        revision=stated["revision"],
        round_number=stated["round"],
        candidate_revision=stated["candidate_revision"],
        merge_input_revision=stated["merge_input_revision"],
        merge_input_ref=stated["merge_input_ref"],
        tree=stated["tree"],
        planned_targets=derived.planned_targets,
        standing=standing,
        rollback_revision=reversal,
    )


def _read_measurement(entry: Mapping[str, Any]) -> Measurement:
    return Measurement(
        metric=entry["metric"],
        unit=entry["unit"],
        before=entry["before"],
        after=entry["after"],
        better=entry["better"],
    )


def _read_counterfactual(
    path: Path,
    stated: Mapping[str, Any] | None,
    assessed: Subject,
) -> Counterfactual | None:
    """The pinned run, held to the release it claims to be about.

    A run of any other pair of revisions measured a different question, so it is
    not this assessment's counterfactual however well-formed it is. The result's
    pairing is the replay record's: `completed` measured both revisions and has
    numbers, `failed` has a reason and none — a partial sweep read as a cohort
    result is a measurement nobody made.
    """

    if stated is None:
        return None

    integration = stated["integration"]
    expected = (
        ("base_revision", assessed.merge_input_revision),
        ("candidate_revision", assessed.revision),
        ("source_ref", assessed.merge_input_ref),
        ("tree", assessed.tree),
    )
    for name, value in expected:
        if integration[name] != value:
            raise BatchError(
                f"{path}: the counterfactual pinned {name} {integration[name]!r}, but this release's "
                f"{name} is {value!r}; a run of another pair of revisions measured another question"
            )

    result = stated["result"]
    parsed: RunResult | None = None
    if result is not None:
        metrics = tuple(_read_measurement(entry) for entry in result["metrics"])
        regressions = tuple(
            Regression(case_id=entry["case_id"], summary=entry["summary"]) for entry in result["regressions"]
        )
        outcome = result["outcome"]
        if outcome == RESULT_COMPLETED and not metrics:
            raise BatchError(
                f"{path}: the counterfactual is recorded {RESULT_COMPLETED!r} and measured nothing; a run that "
                "reached the end of the cohort has numbers to state, and one that did not is a failure with a reason"
            )
        if outcome == RESULT_FAILED:
            stated_anyway = [
                name
                for name, present in (
                    ("metrics", metrics),
                    ("regressions", regressions),
                    ("ambiguity", result["ambiguity"]),
                )
                if present
            ]
            if stated_anyway:
                raise BatchError(
                    f"{path}: the counterfactual is recorded {RESULT_FAILED!r} and still states {stated_anyway}; "
                    "a run that did not measure both revisions reports why it stopped, and partial numbers read "
                    "as a result nobody produced"
                )
        _require_distinct_metrics(path, "the counterfactual", metrics)
        parsed = RunResult(
            outcome=outcome,
            concluded_at=result["concluded_at"],
            detail=result["detail"],
            elapsed_seconds=result["elapsed_seconds"],
            metrics=metrics,
            regressions=regressions,
            ambiguity=result["ambiguity"],
        )

    cases = stated["cases"]
    evaluator = stated["evaluator"]
    harness = stated["harness"]
    return Counterfactual(
        position=Position(
            experiment_id=stated["position"]["experiment_id"],
            round_number=stated["position"]["round"],
            attempt=stated["position"]["attempt"],
        ),
        integration=Pinned(
            base_revision=integration["base_revision"],
            candidate_revision=integration["candidate_revision"],
            source_ref=integration["source_ref"],
            tree=integration["tree"],
        ),
        cases=CaseSet(
            case_set_id=cases["case_set_id"],
            case_set_sha256=cases["case_set_sha256"],
            count=cases["count"],
            excluded=tuple(
                Exclusion(case_id=item["case_id"], reason=item["reason"]) for item in cases["excluded"]
            ),
        ),
        evaluator=Evaluator(
            backend=evaluator["backend"],
            model=evaluator["model"],
            rubric_revision=evaluator["rubric_revision"],
        ),
        harness=Harness(
            id=harness["id"],
            revision=harness["revision"],
            config_sha256=harness["config_sha256"],
            handle=harness["handle"],
        ),
        expectation=stated["expectation"],
        started_at=stated["started_at"],
        result=parsed,
    )


def _read_decision(path: Path, stated: Mapping[str, Any] | None, derived: Subject) -> Decision | None:
    """The human settlement, with the pairing the schema subset cannot state.

    A `rolled-back` settlement names the inverse commit, and it is the one the
    rollback record wrote: the decision is this gate's, the commit is the
    rollback operation's, and a settlement naming something else describes a
    reversal nothing performed. `retain` names none — a release left standing has
    no inverse commit to point at.

    `derived` is the lineage's current reading of the release and not the
    record's own `assessed` block, because the two describe different moments. An
    assessment is ordinarily formed while the promotion stands — its `assessed`
    block says so, and stays saying so, since re-deriving that field would make
    the rollback contradict the finding that justified it — and the settlement is
    appended after the rollback operation has run. The record the settlement is
    checked against is therefore the one written last: `rollback.json` beside the
    promoted batch's outcome.
    """

    if stated is None:
        return None

    settlement = stated["settlement"]
    revision = stated["rollback_revision"]
    if settlement == SETTLEMENT_RETAIN and revision is not None:
        raise BatchError(
            f"{path}: the assessment is settled {SETTLEMENT_RETAIN!r} and names the inverse commit "
            f"{revision[:12]}; a retained release has nothing taken back off the line"
        )
    if settlement == SETTLEMENT_ROLLED_BACK:
        if revision is None:
            raise BatchError(
                f"{path}: the assessment is settled {SETTLEMENT_ROLLED_BACK!r} and names no inverse commit; "
                "a rollback is a commit, and the settlement states which one it made"
            )
        if derived.rollback_revision != revision:
            raise BatchError(
                f"{path}: the settlement names the inverse commit {revision[:12]}, which "
                + (
                    "no rollback record beside the promoted batch's outcome names"
                    if derived.rollback_revision is None
                    else f"disagrees with the rollback record's own {derived.rollback_revision[:12]}"
                )
                + "; the rollback record stays the authority on what came off the line"
            )
    return Decision(
        settlement=settlement,
        decided_at=stated["decided_at"],
        reason=stated["reason"],
        rollback_revision=revision,
    )


def _require_stated_comparability(
    path: Path,
    stated: Mapping[str, Any],
    comparability: Comparability,
    before: Sequence[str],
    after: Sequence[str],
    frame: Frame,
) -> None:
    """The recorded facets must be the ones this record's own cohorts produce.

    Derived from the frozen manifests and the sides the record itself states, so
    the check holds on every clone: what makes a facet coherent is evaluator and
    provenance metadata a manifest committed, never something Git has to place.

    Checking the summary against the record's own facet list would leave that
    list unchecked, and the list is where the whole rule lives: a record over
    cohorts that mix rubric revisions could drop the seven honest facets, state
    one invented coherent one, and carry `improved` past every remaining check.
    The facets are also the accounting invariant 5 asks for, which is not
    something a record chooses.
    """

    derived = _stated_comparability(frame, before, after)
    expected = {facet.facet: facet for facet in derived.facets}
    recorded = {facet.facet: facet for facet in comparability.facets}
    if set(recorded) != set(expected):
        raise BatchError(
            f"{path}: the assessment records the comparability facets {sorted(recorded)}, and the cohorts it "
            f"states are compared in {sorted(expected)}; the facets are derived from the frozen manifests, so "
            "a record states the reading rather than choosing it"
        )
    for name, facet in expected.items():
        found = recorded[name]
        if (found.coherent, found.before, found.after) != (facet.coherent, facet.before, facet.after):
            raise BatchError(
                f"{path}: the {name!r} facet is recorded coherent={found.coherent!r} over before "
                f"{list(found.before)} / after {list(found.after)}, and the frozen manifests of "
                f"{frame.subject.batch_id} and {frame.batch_id} give coherent={facet.coherent!r} over before "
                f"{list(facet.before)} / after {list(facet.after)}"
            )

    if stated["coherent"] != comparability.coherent:
        raise BatchError(
            f"{path}: comparability is recorded {stated['coherent']!r} while its own facets say "
            f"{comparability.coherent!r}"
            + (f" ({', '.join(comparability.incoherent)} differ)" if comparability.incoherent else "")
        )


def _require_one_side(
    path: Path,
    before: Sequence[str],
    after: Sequence[str],
    excluded: Sequence[Excluded],
) -> None:
    """Each report is evidence in one place.

    A key on both sides is one report arguing for and against the release; a key
    in a cohort and in the exclusions is a report counted in a denominator it was
    also removed from.
    """

    keys = list(before) + list(after) + [item.report_key for item in excluded]
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            raise BatchError(
                f"{path}: report {key!r} is placed more than once; one report is evidence on one side, or "
                "excluded with a reason, and never both"
            )
        seen.add(key)


def _require_counts(
    path: Path,
    record: Mapping[str, Any],
    before: Sequence[str],
    after: Sequence[str],
    frame: Frame,
) -> None:
    """The stated denominators must be the unique completed tasks of the reports
    they name (invariant 1).

    Checked against the manifests rather than trusted, because this is the number
    a directional verdict is admissible against: a cohort of four reports from
    one task is one task's evidence however it is counted.

    Counted from the frozen membership and not from what this checkout placed. A
    report's `(repo_id, task_id)` is committed content, so the count is the same
    answer everywhere — including on the clone that cannot resolve one effective
    revision, which would otherwise skip the side entirely and accept whatever
    denominator the record claimed, up to and including one that lifts a thin
    cohort over the minimum.
    """

    tasks = {member.report_key: (member.repo_id, member.task_id) for member in frame.catalog}
    for side, keys in ((SIDE_BEFORE, before), (SIDE_AFTER, after)):
        stated = record["cohorts"][side]["task_count"]
        known = {tasks[key] for key in keys}
        if len(known) != stated:
            raise BatchError(
                f"{path}: the {side} cohort states {stated} unique completed task(s) and its reports come to "
                f"{len(known)}; the denominator is unique tasks, not reports (invariant 1)"
            )


def _require_membership(
    path: Path,
    before: Sequence[str],
    after: Sequence[str],
    excluded: Sequence[Excluded],
    frame: Frame,
) -> None:
    """Every report named here is a member of a frozen manifest, and every member
    is named.

    Both directions matter. A key no manifest names is evidence from outside the
    cohorts this comparison is drawn from — a report that was never frozen, or one
    belonging to some other batch entirely. A member the record never mentions is
    a denominator quietly narrowed, which is the base rate invariant 2 exists to
    keep knowable.
    """

    members = {member.report_key: member.batch_id for member in frame.catalog}
    named = set(before) | set(after) | {item.report_key for item in excluded}

    unknown = sorted(named - set(members))
    if unknown:
        raise BatchError(
            f"{path}: {unknown} named by this assessment belong to no frozen manifest of "
            f"{frame.subject.batch_id} or {frame.batch_id}; a comparison is drawn from frozen cohorts alone "
            "(invariant 3)"
        )
    missing = sorted(set(members) - named)
    if missing:
        raise BatchError(
            f"{path}: {missing} are frozen members of {frame.subject.batch_id} or {frame.batch_id} and this "
            "assessment places them nowhere; every report lands in a cohort or is excluded with a reason, so "
            "the denominator stays visible (invariant 2)"
        )
    for item in excluded:
        if item.batch_id != members[item.report_key]:
            raise BatchError(
                f"{path}: report {item.report_key!r} is excluded as a member of {item.batch_id}, but it is "
                f"frozen in {members[item.report_key]}"
            )


def _require_placement(
    path: Path,
    before: Sequence[str],
    after: Sequence[str],
    excluded: Sequence[Excluded],
    frame: Frame,
) -> None:
    """Placements must agree with what this checkout can establish.

    Asked only where there is an answer. A clone that cannot resolve what a
    target held has no opinion about that report, and refusing there would make a
    valid record unreadable everywhere the objects were never fetched — the same
    reason an unanswerable ancestry check is reported rather than raised
    throughout this package.

    The exclusions are checked in the direction that can overstate. A report
    excluded because its manifest states no effective revision is checkable from
    committed content alone, and a report excluded as post-rollback is checkable
    wherever Git can answer; a report excluded because the forming machine could
    not resolve its revision is not checkable at all, and a clone that can
    resolve it has learned something about itself rather than about the record.
    """

    for side, keys in ((SIDE_BEFORE, before), (SIDE_AFTER, after)):
        for key in keys:
            derived = frame.placement(key)
            if derived is not None and derived != side:
                raise BatchError(
                    f"{path}: report {key!r} is placed in the {side} cohort, but the effective revision its "
                    f"manifest states places it {derived}; a cohort is what the targets actually held"
                )
            exclusion = frame.exclusion(key)
            if exclusion is not None and exclusion.reason != EXCLUDED_REVISION_UNRESOLVABLE:
                raise BatchError(
                    f"{path}: report {key!r} is placed in the {side} cohort, but this checkout excludes it "
                    f"({exclusion.reason}): {exclusion.detail}"
                )

    for item in excluded:
        if item.reason == EXCLUDED_REVISION_UNRESOLVABLE:
            continue
        derived = frame.exclusion(item.report_key)
        if derived is not None and derived.reason in (item.reason, EXCLUDED_REVISION_UNRESOLVABLE):
            continue
        placed = frame.placement(item.report_key)
        if placed is not None:
            found = f"places it in the {placed} cohort"
        elif derived is not None:
            found = f"excludes it as {derived.reason!r}"
        else:
            found = "places it nowhere at all"
        raise BatchError(
            f"{path}: report {item.report_key!r} is excluded as {item.reason!r}, but this checkout {found}; "
            "an exclusion states what the provenance could not say, and this one says something else"
        )


def _require_supported_verdict(
    path: Path,
    *,
    verdict: str,
    comparability: Comparability,
    before_task_count: int,
    after_task_count: int,
    minimum: int,
    metrics: Sequence[Measurement],
    counterfactual: Counterfactual | None,
) -> None:
    """A directional verdict must rest on evidence that can carry one.

    Two kinds of evidence, and a record needs one of them. The **counterfactual**
    is the stronger: the exact pre-promotion and promoted revisions exercised
    over one case set by one evaluator, which is the only comparison in which the
    release is the only difference. A completed run carrying a goal quantity on
    both revisions settles a direction whatever the cohorts came to, because the
    claim is no longer resting on them.

    The **cohorts** carry one only when nothing else explains the difference:
    every comparability facet coherent, both sides at or above the minimum
    unique-task count (invariant 1's anecdote rule), and a goal quantity measured
    on both. Incomparable cohorts differ in the evaluator, the protocol revision
    or the work itself, any of which explains a difference at least as well. That
    includes the shape of the work, which no manifest states — so in practice the
    cohorts suspect and the counterfactual settles, which is the standing this
    evidence has rather than a rule added on top of it.

    `regressed` is checked before either: it always rests on the counterfactual,
    completed, because it is the verdict that costs somebody a promoted change
    and the one that therefore has to be measured rather than inferred.
    """

    if verdict not in DIRECTIONAL_VERDICTS:
        return

    run = counterfactual.result if counterfactual is not None and counterfactual.completed else None
    if verdict == VERDICT_REGRESSED and run is None:
        raise BatchError(
            f"{path}: {verdict!r} rests on the counterfactual, and this record has "
            + ("none" if counterfactual is None else "one that did not complete")
            + "; a suspected regression is settled by measuring the pre-promotion and promoted revisions "
            f"under one configuration, and until that is done the reading is {VERDICT_INCONCLUSIVE!r}"
        )
    if run is not None:
        if not any(measurement.directional for measurement in run.metrics):
            raise BatchError(
                f"{path}: {verdict!r} rests on the counterfactual, and the run it records measured no goal "
                f"quantity on both revisions — every number it states is an observation ({BETTER_NEITHER!r}) "
                f"or missing a side, which settles nothing about the release (invariant 13)"
            )
        return

    measured = any(measurement.directional for measurement in metrics)
    admissible = directional_admissible(
        coherent=comparability.coherent,
        before_task_count=before_task_count,
        after_task_count=after_task_count,
        minimum=minimum,
    )
    if admissible and measured:
        return

    # The gate is the shared predicate above; these are the same three conditions
    # said out loud, because "not supported" without them is not something an
    # operator or a rationale can act on.
    unmet: list[str] = []
    if not comparability.coherent:
        unmet.append(f"the cohorts are not comparable ({', '.join(comparability.incoherent)} differ)")
    if before_task_count < minimum or after_task_count < minimum:
        unmet.append(
            f"before {before_task_count} / after {after_task_count} unique completed task(s) against a "
            f"minimum of {minimum}"
        )
    if not measured:
        unmet.append(
            "no measurement carries one — a goal quantity with a before and an after, rather than an "
            f"observation recorded as {BETTER_NEITHER!r} or a side never measured (invariant 13)"
        )
    raise BatchError(
        f"{path}: {verdict!r} is a directional claim resting on the cohorts alone, and they do not "
        "support one — "
        + "; ".join(unmet)
        + f"; what this evidence allows is {VERDICT_INCONCLUSIVE!r}, or a direction settled by a "
        "completed counterfactual"
    )


def _require_distinct_metrics(path: Path, described: str, metrics: Sequence[Measurement]) -> None:
    """One value per quantity, and a direction with something to point away from.

    The replay record's rule, for the same reason: a name appearing twice leaves
    two values with nothing to choose between them, and a direction with no
    before claims an improvement over nothing.
    """

    seen: set[str] = set()
    for measurement in metrics:
        if measurement.metric in seen:
            raise BatchError(
                f"{path}: {described} measures {measurement.metric!r} twice; one reading reports one value per "
                "quantity, and two of them leave a reader to pick which the release is judged on"
            )
        seen.add(measurement.metric)
        if measurement.goal and measurement.before is None:
            raise BatchError(
                f"{path}: {described} calls {measurement.better!r} better for {measurement.metric!r} with no "
                f"before value to compare against; a quantity measured only after the release is recorded as "
                f"{BETTER_NEITHER!r}"
            )


def _measurement_json(measurement: Measurement) -> dict[str, Any]:
    return {
        "metric": measurement.metric,
        "unit": measurement.unit,
        "before": measurement.before,
        "after": measurement.after,
        "better": measurement.better,
    }


def _counterfactual_json(run: Counterfactual) -> dict[str, Any]:
    return {
        "position": {
            "experiment_id": run.position.experiment_id,
            "round": run.position.round_number,
            "attempt": run.position.attempt,
        },
        "integration": {
            "base_revision": run.integration.base_revision,
            "candidate_revision": run.integration.candidate_revision,
            "source_ref": run.integration.source_ref,
            "tree": run.integration.tree,
        },
        "cases": {
            "case_set_id": run.cases.case_set_id,
            "case_set_sha256": run.cases.case_set_sha256,
            "count": run.cases.count,
            "excluded": [
                {"case_id": exclusion.case_id, "reason": exclusion.reason} for exclusion in run.cases.excluded
            ],
        },
        "evaluator": {
            "backend": run.evaluator.backend,
            "model": run.evaluator.model,
            "rubric_revision": run.evaluator.rubric_revision,
        },
        "harness": {
            "id": run.harness.id,
            "revision": run.harness.revision,
            "config_sha256": run.harness.config_sha256,
            "handle": run.harness.handle,
        },
        "expectation": run.expectation,
        "started_at": run.started_at,
        "result": None
        if run.result is None
        else {
            "outcome": run.result.outcome,
            "concluded_at": run.result.concluded_at,
            "detail": run.result.detail,
            "elapsed_seconds": run.result.elapsed_seconds,
            "metrics": [_measurement_json(measurement) for measurement in run.result.metrics],
            "regressions": [
                {"case_id": regression.case_id, "summary": regression.summary}
                for regression in run.result.regressions
            ],
            "ambiguity": run.result.ambiguity,
        },
    }


def _text(value: Any) -> str | None:
    """One facet value as a string, or None when the manifest does not carry it.

    A schema version is an integer or a string depending on the evaluator that
    reported it, and two spellings of one version would read as two cohorts.
    """

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        return value or None
    return str(value)


def _role(role: Any) -> str | None:
    """One role's launch configuration as a single facet value.

    None when the block is absent or any part of it is missing: a role whose
    agent, model, effort or profile nobody recorded cannot be shown to match
    another cohort's, and invariant 4 keeps that unknown rather than treating it
    as equal.
    """

    if not isinstance(role, Mapping):
        return None
    values = [_text(role.get(field)) for field in _ROLE_FIELDS]
    if any(value is None for value in values):
        return None
    return ";".join(f"{field}={value}" for field, value in zip(_ROLE_FIELDS, values))
