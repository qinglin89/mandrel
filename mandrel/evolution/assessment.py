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
the session that judged them. Recording one (`form`) is that same derivation run
as a write — a caller supplies only the half nobody can re-derive, and the record
is published through the reader that loads it back, so what a later read enforces
is what this write passed and a reading that could not be read is never written.

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

**The placing field is reported, never derived here.** A report's
`provenance.effective_revision` is the evaluated target's deploy-lock
`source_git_commit` as of that evaluation, which only the side holding that
worktree could state; a lock read from a target now says what it holds now, and
placement is an ancestry test, so filling the gap from one would move
pre-release reports onto the `after` side of a promotion the target was
redeployed onto later. The feed publishes it as half of an atomic pair with the
lock's payload digest, and the import client copies both or neither, so a report
here states the identity its runs were verified under or states nothing at all.
Reports imported before that publication carry no revision and never gain one —
a frozen manifest copies the record as staged — so a cohort built from them
excludes whole and the counterfactual below is what carries a direction
(contract: Release assessment).

Reported also means unchecked, with one exception: `_place` asks Git whether a
stated revision carries the promotion, and Git answers about that commit, not
about what the target ran. Whether the commit describes the payload at all is
settled where the receipt is written — the deploy states no revision when the
canonical tree it copied was not exactly that commit's — and whether the target
still matched that receipt while the evaluated work ran is settled by whoever
held it, at the boundaries of the runs that did the work. Both conditions sit in
the contract as what a publisher must establish before stating the field, because
a revision this side cannot verify is one it cannot refuse either: it would place
the report, and a misplaced report is a direction invented rather than a
denominator lost.

The exception is the field's *shape*, which is checkable here and is the one way
an unverifiable revision would be placed by something other than itself. The
field is a deploy lock's `source_git_commit`, a full object id; a feed stating
`HEAD`, a branch or an abbreviation instead would be resolved against this
repository's own position, so the report would be placed by where the reading
checkout happens to stand. That is asked of `lockfile.is_object_id`, the rule the
receipt side reads by, and answered as an exclusion naming what was stated — the
import client copies the published string verbatim and repairs nothing
(`hub._stated`), so this is the single place a badly shaped revision is judged,
and it reaches reports frozen into a manifest before the rule existed as readily
as ones imported after it.

The shape of the work is the difference nothing frozen states. Two cohorts are
two different task sets by construction, and no manifest version records what
kind of task each report judged, so that facet is carried as the unknown it is
rather than approximated by "the same repository appears on both sides". What
follows is not a weaker artifact but an honest division of labour: the cohorts
show the base rate and raise the suspicion, and a direction — in either sign — is
settled by the counterfactual.

**The counterfactual is five operations on one record.** `measure` pins the pair
and asks the replay harness for a run, `conclude` records the numbers it
reports, `abandon` records why a run ended when its harness cannot say,
`withdraw` gives up a request the harness never answered for, and `resolve`
records the reading those numbers settle. They write where the
reading is, not where an experiment's runs are, and they move nothing: the
release ref, the checkout and the frozen membership are untouched, because what
this controller owns is pinning what gets exercised and recording what came
back. The request is durable before the harness is asked — a harness may be
running something this record does not describe yet, and that window is what
names it — and every refusal is made before the asking, so a refusal costs
nothing that had already started.

The two ways out of a run nothing can finish are the same two the replay record
has, and they answer different states: `abandon` ends a run that was recorded,
`withdraw` gives up a request that never became one. Neither hands the position
back — a harness that cannot describe what it is running may well be running
something, so reissuing its key would answer the next request with that run —
and both leave the reading `inconclusive`, which is what this contract already
says about a comparison that was never made.

**The gate is the last thing written on it.** A reading is evidence until a
human answers it, and the answer is what the next base freeze waits on: `retain`
leaves the release as the line every alternative in the assessing batch starts
from, `rolled-back` takes it off first. The reversal itself is composed rather
than repeated — the rollback operation reverses the newest promotion, refuses on
a line later work stands on, and takes the reason and nothing else — so what is
added here is the decision, its sequencing, and the refusals that have to be made
before a commit rather than after one. Once it answers, nothing more is added to
the record: a settlement stands on the reading it was made from.

**Vocabulary is shared, not restated.** The case set, evaluator, harness,
regression and result shapes are the replay record's own value objects: a
comparison across two spellings of "who judged this" could not make the
separation invariant 5 asks for. The one deliberate difference is the
measurement — a run states a quantity as `baseline` and `candidate`, and here
the two sides are cohorts produced before and after a release, so they are named
`before` and `after`. A counterfactual's numbers cross that boundary once, on
the way in: its baseline is the pre-promotion revision and its candidate the
promoted one.

Every operation here that needs no harness takes the optional `expect` the rest
of this package takes (`experiments`: The state a caller read), checked first
inside its lock — the settlement's included, which is the one hold the composed
reversal also runs under.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..lockfile import is_object_id
from .config import ASSESSMENT_SCHEMA_FILENAME, EvolutionConfig
from .errors import BatchError, ValidationError
from .guards import reason as require_reason
from .guards import require_expected, settled
from .ledger import append_records, build_record
from .lineage import BatchLineage, Lineage
from .lineage import describe as describe_lineage
from .manifests import OUTCOME_PROMOTED, Batch, read_batch_record
from .replay import (
    BETTER_LOWER,
    BETTER_NEITHER,
    RESULT_COMPLETED,
    RESULT_FAILED,
    CaseSet,
    Evaluator,
    Exclusion,
    Harness,
    Integration,
    Regression,
    ReplayHarness,
    ReplayPlan,
    ReplayReport,
    ReplayRequest,
)
from .revisions import contains, resolve_commit
from .rollback import RollbackResult
from .rollback import reverse as reverse_promotion
from .schema import format_rfc3339, load_schema, validate_or_raise
from .state import atomic_write_text, single_writer_lock

ASSESSMENT_SCHEMA_VERSION = 1

RECORD_RELEASE_ASSESSED = "release-assessed"
RECORD_COUNTERFACTUAL_COMPLETED = "release-counterfactual-completed"
RECORD_RELEASE_SETTLED = "release-settled"

VERDICT_IMPROVED = "improved"
VERDICT_NEUTRAL = "neutral"
VERDICT_REGRESSED = "regressed"
VERDICT_INCONCLUSIVE = "inconclusive"
# The three readings that say which way the release moved the work. `neutral` is
# one of them: "the release changed nothing" is a claim about a measured
# difference, and it needs the same comparable cohorts `improved` does.
# `inconclusive` is the answer when they are not there.
DIRECTIONAL_VERDICTS = frozenset({VERDICT_IMPROVED, VERDICT_NEUTRAL, VERDICT_REGRESSED})
# Every reading a record may state: the three directional ones, then the one
# that is not. Stated here for `SETTLEMENTS`' reason — a surface offering the
# vocabulary takes it from the module that owns it, rather than spelling a fifth
# word this build would refuse at the write.
VERDICTS = (VERDICT_IMPROVED, VERDICT_NEUTRAL, VERDICT_REGRESSED, VERDICT_INCONCLUSIVE)

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"
CONFIDENCES = (CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW)

SETTLEMENT_RETAIN = "retain"
SETTLEMENT_ROLLED_BACK = "rolled-back"
# The two answers the gate has. There is no third: a release is either what the
# next base is frozen on or it is taken back off the line first, and "not yet" is
# the unsettled reading rather than a decision.
SETTLEMENTS = (SETTLEMENT_RETAIN, SETTLEMENT_ROLLED_BACK)

# Why a frozen report is in neither cohort. All four are about what its
# provenance can be shown to say, never about what its numbers came to.
EXCLUDED_REVISION_ABSENT = "effective-revision-absent"
EXCLUDED_REVISION_MALFORMED = "effective-revision-malformed"
EXCLUDED_REVISION_UNRESOLVABLE = "effective-revision-unresolvable"
EXCLUDED_POST_ROLLBACK = "post-rollback-effective-revision"
# The two a frozen manifest settles by itself: whether it states an effective
# revision, and whether what it states is a commit id, are both read off the
# committed bytes before Git is asked anything, so every clone reaches the same
# answer. That is what separates them from the two below, which are readings of a
# repository — and it is why a recorded exclusion naming one of them is held to
# the derivation exactly (`_require_placement`), including against the
# `EXCLUDED_REVISION_UNRESOLVABLE` a reader otherwise takes on trust.
MANIFEST_SETTLED_EXCLUSIONS = frozenset({EXCLUDED_REVISION_ABSENT, EXCLUDED_REVISION_MALFORMED})

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

    One run, measuring both revisions. That is the harness boundary's own shape —
    a report states each quantity on the base and on the candidate — and it is
    what makes the two halves of this comparison incapable of drifting apart:
    the case set, the evaluator and the configuration are one selection governing
    both sides, rather than two selections that have to be shown to have matched.
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

    @property
    def running(self) -> bool:
        """Still going: nothing has concluded it. Age concludes nothing — a
        harness that died leaves this true until a report says otherwise."""

        return self.result is None

    @property
    def failed(self) -> bool:
        """Measured nothing, and said why. The one state another attempt answers:
        a completed run is the comparison made, and a running one is it being
        made."""

        return self.result is not None and self.result.outcome == RESULT_FAILED


@dataclass(frozen=True)
class CounterfactualRequest:
    """A run this cohort committed to before it asked the harness for one.

    Everything here is the controller's own: the key the run will occupy, the
    pair pinned for it, and what it was expected to show. Nothing the harness
    chooses is in it, which is why it can be written before the harness is asked
    anything — and why it is not a run. Nothing derives evidence from it: while
    one stands, the release has been measured by nothing and no verdict rests on
    it.

    What it is for is the window in between. The harness is an external thing
    being started, and an answer that never arrives would otherwise leave a run
    going that no record names. This names it: `measure` run again re-submits the
    request unchanged, and the harness answers with the run it already began.
    """

    position: Position
    integration: Pinned
    expectation: str
    requested_at: str


@dataclass(frozen=True)
class WithdrawnRequest:
    """A position a request held and gave up without ever becoming a run.

    Kept rather than erased, for the reason a replay's withdrawal is: the
    position is half of what the harness is keyed on, and a withdrawal happens
    when the harness cannot describe what it is running — which means it may
    well be running something. A later request reissued at that key would be
    answered with *that* run, and the numbers of a comparison this record no
    longer names would arrive under the one it does. So the position stays
    allocated forever and the next request takes the one after it.

    What it carries is what a withdrawal is about afterwards: the window the
    stray run would have started in, and the pair pinned for it — which is what
    an operator has to go on to find that run at the harness and stop it.
    Deliberately not the expectation: that is a prediction kept to be read back
    against numbers, and this request produced none.
    """

    position: Position
    integration: Pinned
    requested_at: str
    withdrawn_at: str


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
    # The run asked for and not yet answered. The write that records the run a
    # request became clears it in the same file, so the only run one may stand
    # over is a failed one — which is the retry, at the position after it. Beside
    # a run still going or a completed one it would leave a reader choosing which
    # of the two the harness is measuring this release with.
    requested: CounterfactualRequest | None = None
    # The positions requests held and gave up. Never reissued, so what a further
    # attempt is numbered from is the run and these together.
    withdrawn: tuple[WithdrawnRequest, ...] = ()
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
            "counterfactual_request": None if self.requested is None else _request_json(self.requested),
            "counterfactual_withdrawn": [_withdrawal_json(taken) for taken in self.withdrawn],
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


@dataclass(frozen=True)
class Obligation:
    """The reading of one release, wherever it sits: whose it is and what it says.

    The obligation belongs to the first cohort frozen after the promotion and
    stays there — a later batch never acquires it — so this pairs that cohort's
    derived frame with whatever it has recorded so far. Ordinarily the two are
    the same batch and this says nothing new; it exists for the case where they
    are not, because the answer a base freeze waits on is the *owner's* and not
    the freezing cohort's own (invariant 17).
    """

    frame: Frame
    reading: Assessment | None

    @property
    def owner_id(self) -> str:
        """The cohort that owes the reading."""

        return self.frame.batch_id

    @property
    def settled(self) -> bool:
        """Whether the human gate has answered — the one state a base freeze may
        go ahead on. A reading nobody recorded and one nobody decided about are
        different states to an operator and both stop a freeze."""

        return self.decision is not None

    @property
    def decision(self) -> Decision | None:
        """The answer, where there is one: which line the release left behind is
        the one later work is built from."""

        return None if self.reading is None else self.reading.decision


@dataclass(frozen=True)
class Formed:
    """One reading recorded, and where it landed."""

    batch_id: str
    assessment: Assessment
    record_path: Path
    # False when this reports the reading already on record — the same formation
    # run again after an interruption, recognised by what it says rather than by
    # a marker beside it.
    recorded: bool = True


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


def measured_direction(goals: Sequence[Measurement]) -> str | None:
    """Which direction a counterfactual's goal quantities came to, or None when
    they came to no single one.

    The other rule asked by both sides: the run states what the release did, so a
    session forming a verdict reads it here and every later read of the record
    asks the same question of the same numbers. `goals` is the run's directional
    goals — measured on both revisions — and asking it of none of them is a
    question about a run that settled nothing, which its caller has already
    refused.

    The rule for more than one goal, stated once. A run supports the direction
    every goal that moved points; goals that did not move neither add to it nor
    stand against it, and all of them unchanged is `neutral` — a measured "the
    release changed nothing", which is a finding rather than an absence of one.
    Goals pointing both ways support no direction: which quantity a release is
    judged on is chosen when the run is configured, and invariant 13 records the
    others as observations, so a reader weighing an improvement against a
    regression afterwards would be making that choice on the operator's behalf.
    """

    directions = {_direction(measurement) for measurement in goals}
    moved = directions - {VERDICT_NEUTRAL}
    if not moved:
        return VERDICT_NEUTRAL
    return next(iter(moved)) if len(moved) == 1 else None


def settleable(run: Counterfactual | None, requested: CounterfactualRequest | None) -> bool:
    """Whether a reading carrying this evidence may be settled at all.

    The third rule asked by both sides: the gate asks it of the reading it is
    about to answer, and every read of a settled record asks it of what that
    record carries. A settlement stands over evidence that is over — a completed
    run, a failed one, or none at all — because nothing is added to a reading once
    its gate answers, so numbers arriving afterwards would have nowhere to go and
    the decision would rest on a measurement that is lost.

    Asked before the writing rather than only at it, which is what makes it a
    rule rather than a schema check: settling `rolled-back` puts a commit on the
    source line before this record is touched, and a refusal that waited for the
    write would refuse a decision whose release had already come off.
    """

    return requested is None and (run is None or not run.running)


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


def obligation(
    config: EvolutionConfig,
    batch: Batch,
    *,
    lineage: Lineage | None = None,
) -> Obligation | None:
    """The reading of the release before `batch`, read from the cohort that owes
    it. None when there is no release before `batch` at all.

    The distinction this exists for: "which release precedes this batch" and
    "which cohort answers for it" are two questions, and they part company as
    soon as the owing cohort ends without answering. A batch that concluded
    `no-change` promoted nothing, so the batch after it still follows the older
    release — while the obligation to read that release stays with the first
    cohort frozen after it, which is where the record is and where it stays.
    Asking `owed_by` of the freezing batch alone would find no obligation there
    and let the freeze proceed on a line nobody decided about (invariant 17).

    Read-only, and the answer is the whole state: which cohort owes it, the frame
    its record is checked against, and the record itself where there is one.
    """

    known = lineage if lineage is not None else describe_lineage(config)
    assessed = subject(known, batch)
    if assessed is None:
        return None
    owner = _owner(known, assessed)
    if owner is None:
        # `batch` is itself after that promotion, so the promotion has at least
        # one batch after it — this is unreachable, and returning "nothing owed"
        # rather than raising would be the one answer that quietly skips a gate.
        raise BatchError(
            f"{batch.batch_id} follows the {assessed.batch_id} release ({assessed.revision[:12]}), and no batch "
            "is recorded as frozen after that promotion; the cohort that owes the reading is the first one after "
            "it, so this lineage cannot say who answers for a release its own batches follow"
        )
    frame = describe(config, owner.batch, lineage=known)
    if frame is None:
        # Same shape: the owner follows that promotion by construction, so a
        # frame it cannot derive is a lineage disagreeing with itself.
        raise BatchError(
            f"{owner.batch_id} is the cohort that owes a reading of the {assessed.batch_id} release, and no "
            "release can be derived for it; the batches form a series (invariant 14), so the promotion this "
            "batch follows is the one that cohort follows too"
        )
    return Obligation(frame=frame, reading=read(config, owner.batch, frame=frame))


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
    withdrawn = tuple(
        _read_withdrawal(path, entry, assessed) for entry in record["counterfactual_withdrawn"]
    )
    requested = _read_request(path, record["counterfactual_request"], assessed, counterfactual, withdrawn)
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
    _require_allocated_once(path, counterfactual, requested, withdrawn)
    _require_settled_over_evidence(path, decision, counterfactual, requested)
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
        requested=requested,
        withdrawn=withdrawn,
        path=path,
    )


def form(
    config: EvolutionConfig,
    *,
    verdict: str,
    confidence: str,
    rationale: str,
    metrics: Sequence[Measurement] = (),
    expect: str | None = None,
    now: datetime | None = None,
) -> Formed:
    """Record the current cohort's reading of the release before it.

    What a caller supplies is only what nobody can re-derive: the quantities the
    two cohorts came to, read from evaluation artifacts this machine holds, and
    the verdict, confidence and rationale of the session that judged them.
    Everything else — which release, which reports on which side, the
    denominators, the exclusions, the comparability facets — is derived here from
    the two frozen manifests, the promoted batch's outcome, its rollback record
    if it has one, and Git. A record states the reading rather than choosing it,
    which is what makes the check every later read applies the same one this
    write passed.

    The preamble is deliberately not `guards.current_cycle`. That one settles
    which batch may be *acted on*, and it refuses while the analysis stage is
    still running — which is exactly when this is written: the reading is the
    generated analysis task's own second question, taken before the dispositions
    close. What this does need from the lineage is the same as everything else:
    which batch is current (invariant 14, from the whole history), and whether it
    is the cohort that owes the reading.

    The verdict is checked against the evidence the record carries, by the parser
    every read uses. In practice a reading formed here is `inconclusive` or
    nothing: no manifest version states the shape of the work, so the cohorts
    carry no direction on their own, and the counterfactual that settles one has
    not run when this is written. That is the artifact working rather than a
    limitation of it — the cohorts raise the suspicion and the pinned run answers
    it.

    Run again after an interrupted formation, this reports the reading already on
    record rather than writing a second one, and does not re-append the audit line
    that interruption may have cost — the rule a redone conclusion follows, for
    the same reason: the event happened once. A request that says something
    different is refused, because a cohort reads a release once and the record is
    what the counterfactual and the human settlement are afterwards added to.
    """

    moment = _moment(now)
    text = require_reason(
        rationale,
        "a release assessment records why its verdict is that verdict; the evidence the judging session had in "
        "front of it is machine-local and the cohorts are two different task sets, so this sentence is what a "
        "later reader has instead of both",
    )
    stated = tuple(metrics)

    with single_writer_lock(config):
        require_expected(config, expect)
        known = settled(config, now=moment)
        frame = _owing_frame(config, known)
        batch = frame.batch

        already = read(config, batch, frame=frame)
        if already is not None:
            return _redone(frame, already, stated, verdict, confidence, text)

        built = Assessment(
            batch_id=frame.batch_id,
            subject=_as_assessed(frame.subject),
            before=frame.before.report_keys,
            before_task_count=frame.before.task_count,
            after=frame.after.report_keys,
            after_task_count=frame.after.task_count,
            excluded=frame.excluded,
            comparability=frame.comparability,
            metrics=stated,
            counterfactual=None,
            verdict=verdict,
            confidence=confidence,
            rationale=text,
            formed_at=format_rfc3339(moment),
            decision=None,
        )
        recorded = _write_assessment(config, batch, built, frame)
        append_records(
            config,
            [
                build_record(
                    RECORD_RELEASE_ASSESSED,
                    recorded_at=built.formed_at,
                    batch_id=frame.batch_id,
                    experiment_id=frame.subject.experiment_id,
                    revision=frame.subject.revision,
                    detail=verdict,
                )
            ],
        )

    return Formed(batch_id=frame.batch_id, assessment=recorded, record_path=batch.assessment_path)


def _owing_frame(
    config: EvolutionConfig, known: Lineage, *, follow_answered: bool = False
) -> Frame:
    """The frame of the batch that owes a reading, or why there is none to write.

    Three ways there is nothing here to record, and they are different answers to
    an operator: no cohort is current, the current cohort follows no release, and
    the reading belongs to an earlier cohort whose answer is already on record.

    The third of those is a refusal only where that earlier cohort *answered*.
    An obligation it left outstanding — a cohort that ended without reading the
    release, or read it and was never settled — is still the obligation the next
    base freeze waits on, and the record still belongs in that cohort's own
    directory. So these operations follow it there rather than refusing from the
    cohort that happens to be current: a gate nothing can answer would stop the
    lineage for good, since what freezes the next cohort is the settlement and
    what would record the settlement is this.

    `follow_answered` is the settlement's own exception, and it is about
    reporting rather than about writing. Everything a settlement does to a
    reading that already carries a decision is report that decision back or
    refuse to re-take it (`_redone_settlement`), so reaching an answered owner
    is what makes a carried-forward settlement repeatable — and a settlement
    nobody can repeat is one a caller cannot recover, since losing the response
    and being interrupted after the record landed look the same from outside.
    The operations that *add* to a reading do not take the exception: for them an
    answered obligation is where the work stops, and stopping here says whose
    record it is, which the settled check further in could not — the cohort
    standing at the keyboard has recorded nothing at all.
    """

    current = known.current
    if current is None:
        raise BatchError(no_cohort_refusal())

    frame = describe(config, current.batch, lineage=known)
    if frame is None:
        raise BatchError(no_release_refusal(current.batch_id))
    if frame.owed:
        return frame

    # Never None: this cohort derived a release just above, so there is one
    # before it — and where the lineage cannot say which cohort owes that
    # reading, `obligation` refuses rather than answering.
    owed = obligation(config, current.batch, lineage=known)
    if owed is not None and (follow_answered or not owed.settled):
        return owed.frame
    raise BatchError(_not_the_owner(frame.batch_id, frame.subject, owed))


def _as_assessed(subject: Subject) -> Subject:
    """The release as the record states it, which is not quite as the lineage
    reads it now.

    `assessed.rollback_revision` is for a reversal that had *already* taken the
    promotion off the line when the reading was formed. The derived subject also
    carries an inverse commit that is only prepared — it needs one, since a report
    produced at a line that took that commit belongs to neither cohort — and
    writing it here would state that the release was reversed while `standing`
    says it was not, which is the contradiction the reader refuses.
    """

    return subject if subject.reversed_promotion else replace(subject, rollback_revision=None)


def _redone(
    frame: Frame,
    already: Assessment,
    metrics: tuple[Measurement, ...],
    verdict: str,
    confidence: str,
    rationale: str,
) -> Formed:
    """The same formation run again, or the refusal that this cohort has read
    this release already.

    Recognised by what the record says rather than by a marker: an interrupted
    formation either wrote its record or did not, and the one it wrote is the one
    a redo is asking for. What is compared is the caller's half alone — the
    derived half is derived on both sides, and `formed_at` is when the reading
    landed rather than part of it.
    """

    if (already.metrics, already.verdict, already.confidence, already.rationale) == (
        metrics,
        verdict,
        confidence,
        rationale,
    ):
        return Formed(
            batch_id=frame.batch_id,
            assessment=already,
            record_path=frame.batch.assessment_path,
            recorded=False,
        )
    raise BatchError(
        f"{frame.batch_id} already reads the {frame.subject.batch_id} release {already.verdict!r}, and this "
        f"request is {verdict!r}; a cohort reads a release once, and that record is what the counterfactual "
        "and the human settlement are added to — two readings of one release would leave a later reader "
        "choosing between them"
    )


def _write_assessment(
    config: EvolutionConfig,
    batch: Batch,
    built: Assessment,
    frame: Frame,
) -> Assessment:
    """Publish a reading through the two halves of the reader that loads it.

    The schema first and then the rules it cannot state, in the order and by the
    calls a read makes, so a writer cannot produce a record its own package
    refuses — a directional verdict its evidence does not support is refused here
    rather than committed and discovered by the next reader.
    """

    record = built.to_json()
    validate_or_raise(
        record,
        load_schema(config.schema_path(ASSESSMENT_SCHEMA_FILENAME)),
        description=f"release assessment record for {frame.batch_id}",
    )
    parsed = parse(config, record, batch, frame=frame)
    atomic_write_text(batch.assessment_path, json.dumps(record, indent=2, sort_keys=True) + "\n")
    return parsed


# --- the counterfactual ------------------------------------------------------


@dataclass(frozen=True)
class Measured:
    """A counterfactual this operation started, and the record that names it."""

    batch_id: str
    assessment: Assessment
    run: Counterfactual
    plan: ReplayPlan
    record_path: Path
    # True when this run answers a request an earlier run of this operation left
    # outstanding: the position and the pair come from that request, and the
    # harness was asked for the run it may already have been running.
    resumed: bool = False


@dataclass(frozen=True)
class Concluded:
    """What the counterfactual is, after asking about it.

    Three states reach here and `recorded` tells them apart: the run concluded
    now (True), a run still going (False, `running`), and a run whose result was
    already on record (False) — an interrupted conclusion reporting what it wrote
    rather than writing a second one.
    """

    batch_id: str
    assessment: Assessment
    run: Counterfactual
    recorded: bool

    @property
    def running(self) -> bool:
        return self.run.running

    @property
    def outcome(self) -> str | None:
        return None if self.run.result is None else self.run.result.outcome


@dataclass(frozen=True)
class Withdrawn:
    """A request given up, and the position it keeps.

    `withdrawn` is False when there was nothing outstanding — this operation run
    a second time, or run where no request stood. `request` is what was taken
    back, reported because it names the run that may still be going at a harness
    this controller will never hear from again.
    """

    batch_id: str
    assessment: Assessment
    request: CounterfactualRequest | None
    withdrawn: bool
    record_path: Path


def measure(
    config: EvolutionConfig,
    harness: ReplayHarness,
    *,
    expectation: str,
    expect: str | None = None,
    now: datetime | None = None,
) -> Measured:
    """Start the pinned two-revision run that settles what the release did.

    The pair is the release's own merge unit and nothing else: the pre-promotion
    revision is the merge input the outcome states — the promotion's first parent
    — and the promoted one is the revision itself. Neither is reconstructed and
    no integration is computed, because the promotion *is* that integration.
    What this asks of Git is only that both revisions are here to be exercised;
    whether the promotion carries the tree and parents the outcome records is the
    lineage's own reading, already taken by the time this writes anything.

    Nothing here moves a ref, touches the release checkout, or changes what any
    batch froze. The harness exercises both revisions wherever it likes —
    temporary worktrees are the ordinary answer — and this controller's whole
    part is pinning what it exercises and recording what it answered.

    `expectation` is recorded before any numbers exist, which is the only time it
    can be, and is deliberately not handed to the harness: a prediction given to
    the thing being measured is an instruction.

    The request is durable before the harness is asked. From the moment it is
    written the harness may be running something, so every refusal is made first
    — and run again with a request outstanding, this is that request resumed
    rather than a second one: the same key reaches the harness, which answers with
    the run it already began. What a resume may not do is re-pin the pair or
    restate the prediction, so the argument is checked against the request rather
    than acted on.

    A failed run is answered by another attempt at the next position; a completed
    one is not. The release is measured once and a second answer about it would
    leave a reader choosing between them — while a run that never measured
    anything has left nothing to choose.

    What a resume cannot get past is a harness that answers with something this
    record refuses to hold: the same key comes back with the same answer for as
    long as the request stands. `withdraw` is the way out of that, and the next
    attempt is numbered past what it gave up — which is why a position is
    allocated from the runs and the withdrawals together.

    `expect` is checked first inside the lock, which for this operation is also
    before the request is written and therefore before the harness may be running
    anything: a caller whose reading has moved gets a refusal rather than a run
    it did not decide on.
    """

    moment = _moment(now)
    predicted = require_reason(
        expectation,
        "a counterfactual records what it was expected to show, before it shows anything; a run started without "
        "one is read afterwards against whatever its numbers turn out to be, which is the reading the "
        "expectation exists to prevent",
    )

    with single_writer_lock(config):
        require_expected(config, expect)
        known = settled(config, now=moment)
        frame = _owing_frame(config, known)
        batch = frame.batch
        reading = _recorded(config, frame, "measure against the pre-promotion revision")
        _require_unsettled(frame, reading, "a run started now")

        requested = reading.requested
        resumed = requested is not None
        if requested is None:
            requested = _request(config, frame, reading, predicted, at=format_rfc3339(moment))
            reading = _write_assessment(config, batch, replace(reading, requested=requested), frame)
        else:
            _require_same_request(frame, requested, predicted)

        described = _describe(requested.position)
        plan = harness.start(_harness_request(requested))
        started = Counterfactual(
            position=requested.position,
            integration=requested.integration,
            cases=plan.cases,
            evaluator=plan.evaluator,
            harness=plan.harness,
            expectation=requested.expectation,
            # When the run began, which is when its pair was pinned — not the
            # moment a resume finally heard back about it.
            started_at=requested.requested_at,
            result=None,
        )
        try:
            recorded = _write_assessment(
                config,
                batch,
                replace(reading, counterfactual=started, requested=None),
                frame,
            )
        except (BatchError, ValidationError) as exc:
            raise BatchError(
                f"{frame.batch_id}: {plan.harness.id} began {described} as handle {plan.harness.handle!r} and "
                f"described it in a way this record cannot hold ({exc}); the request for that run is still on "
                "record, so nothing that may be running has been lost — but a harness answers one key with the "
                "run it already began, so asking again brings back the same answer: withdraw the request, and "
                "the next attempt takes the position after it"
            ) from exc

    return Measured(
        batch_id=frame.batch_id,
        assessment=recorded,
        run=started,
        plan=plan,
        record_path=batch.assessment_path,
        resumed=resumed,
    )


def conclude(
    config: EvolutionConfig,
    harness: ReplayHarness,
    *,
    expect: str | None = None,
    now: datetime | None = None,
) -> Concluded:
    """Ask the counterfactual for its numbers, and record them if it has any.

    Polling a run that is still going is the ordinary case and not an error: this
    reports the run unchanged and writes nothing, so it can be called as often as
    an operator likes. The clock is the controller's — a harness reports what it
    measured, and when that was observed is recorded by whoever observed it.

    The numbers cross one boundary on the way in. A run states each quantity as a
    baseline and a candidate; here the two sides are the line before the release
    and the release itself, so they are recorded as `before` and `after` — one
    vocabulary, because a cohort reading and a run's numbers that could not be
    compared would be two artifacts pretending to be one.

    What it does not do is decide anything. A completed run settles a direction
    (`measured_direction`), and recording that reading is a separate judgement
    with a reason of its own — supplying it here would be stating a verdict
    before the numbers existed, which is what the expectation is kept apart from.

    Run again after an interrupted conclusion, it reports the result already on
    record rather than polling for a second one; the audit line that interruption
    may have cost is not re-appended, which is the rule a redone seal follows.
    """

    moment = _moment(now)

    with single_writer_lock(config):
        require_expected(config, expect)
        known = settled(config, now=moment)
        frame = _owing_frame(config, known)
        batch = frame.batch
        reading = _recorded(config, frame, "conclude a run of")
        _require_unsettled(frame, reading, "numbers recorded now")
        going = _require_run(frame, reading, "concluded")
        if not going.running:
            return Concluded(batch_id=frame.batch_id, assessment=reading, run=going, recorded=False)

        report = harness.poll(going.harness.handle)
        if report is None:
            return Concluded(batch_id=frame.batch_id, assessment=reading, run=going, recorded=False)

        stamp = format_rfc3339(moment)
        concluded = replace(going, result=_result(report, at=stamp))
        try:
            recorded = _write_assessment(config, batch, replace(reading, counterfactual=concluded), frame)
        except (BatchError, ValidationError) as exc:
            raise BatchError(
                f"{frame.batch_id}: {going.harness.id} reported {_describe(going.position)} in a way this record "
                f"cannot hold ({exc}); the run is still recorded as going, so nothing has been lost — ask again "
                "once the harness can state what it measured, or end the run and record why"
            ) from exc
        _audit_run(config, frame, going, outcome=report.outcome, at=stamp)

    return Concluded(batch_id=frame.batch_id, assessment=recorded, run=concluded, recorded=True)


def abandon(
    config: EvolutionConfig,
    *,
    reason: str,
    expect: str | None = None,
    now: datetime | None = None,
) -> Concluded:
    """Record why the counterfactual ended when its harness cannot say.

    A run is going until something records that it stopped: age concludes
    nothing, and a harness that died, lost its handle, or answers with a report
    this record cannot hold would otherwise leave the run going forever — and
    with it the only comparison in which the release is the only difference,
    since a run may not be started under one that is still going.

    It takes no harness, for the same reason `replay.abandon` does: this is the
    path for when asking is not the problem. What it writes is a `failed` result,
    which is what the run was — no numbers, and the story in `detail` — and the
    reason is the operator's because the harness is the thing that could not give
    one. A run this ends is answered by another attempt, which is what a `failed`
    result means here as everywhere else.

    Run again after an interrupted abandonment, it reports the failure already on
    record. A run that ended some other way is not this operation's redo, and is
    reported back rather than overwritten.

    It ends a *run*, so a request that never became one is not its business:
    there is no record to write a failure onto, and inventing one would state a
    case set, an evaluator and a harness configuration nobody selected.
    `withdraw` takes that back instead.
    """

    moment = _moment(now)
    text = require_reason(
        reason,
        "ending a run records why; the record is all a later reader has of a run whose harness never reported, "
        "and a failure with no reason is indistinguishable from one nobody looked into",
    )

    with single_writer_lock(config):
        require_expected(config, expect)
        known = settled(config, now=moment)
        frame = _owing_frame(config, known)
        batch = frame.batch
        reading = _recorded(config, frame, "end a run of")
        _require_unsettled(frame, reading, "a run ended now")
        going = _require_run(frame, reading, "ended")
        if not going.running:
            result = going.result
            if result is not None and result.outcome == RESULT_FAILED and result.detail == text:
                return Concluded(batch_id=frame.batch_id, assessment=reading, run=going, recorded=False)
            raise BatchError(_already_ended(frame, going))

        stamp = format_rfc3339(moment)
        concluded = replace(
            going,
            result=RunResult(
                outcome=RESULT_FAILED,
                concluded_at=stamp,
                detail=text,
                elapsed_seconds=None,
                metrics=(),
                regressions=(),
                ambiguity=None,
            ),
        )
        recorded = _write_assessment(config, batch, replace(reading, counterfactual=concluded), frame)
        _audit_run(config, frame, going, outcome=RESULT_FAILED, at=stamp)

    return Concluded(batch_id=frame.batch_id, assessment=recorded, run=concluded, recorded=True)


def withdraw(config: EvolutionConfig, *, expect: str | None = None, now: datetime | None = None) -> Withdrawn:
    """Take back a request the harness never answered for.

    The way out of the window the request exists to cover, and the only one there
    is. A harness that cannot describe what it is running — a plan this record
    refuses to hold, a run it no longer knows about — leaves `measure` unable to
    finish, and asking again does not help: a conforming harness answers one key
    with the run it already began, so the same unrecordable answer comes back for
    as long as that request stands. Nothing else can clear it either, since
    `conclude` and `abandon` act on a run and there is none. Without this the
    release would go on being unmeasurable, and with it the one comparison in
    which the release is the only difference — which is to say the one thing a
    `regressed` reading can rest on.

    What it does not give up is the position. The harness is keyed on the round
    and the attempt, and the reason there is a withdrawal at all is that the
    harness may be running something nobody here can describe — so reissuing that
    key would answer the next request with the run this one abandoned. The
    position is recorded as withdrawn instead, and the next request takes the one
    after it.

    It takes no reason, and that is the difference from ending a run. A run that
    happened is history and carries why it stopped; a request that never became
    one measured nothing, is derived as nothing, and has no result to attach a
    reason to. What the withdrawal does record is what an operator needs to find
    the run that may be going at a harness this controller will never hear from
    again: the window it was started in, and the pair pinned for it.

    Run again, it reports that nothing was outstanding. That is the whole of its
    redo: a single write with nothing after it either has landed or has not, and
    the request's absence is the answer in both directions.
    """

    moment = _moment(now)

    with single_writer_lock(config):
        require_expected(config, expect)
        known = settled(config, now=moment)
        frame = _owing_frame(config, known)
        batch = frame.batch
        reading = _recorded(config, frame, "withdraw a run request of")
        _require_unsettled(frame, reading, "a request given up now")

        requested = reading.requested
        if requested is None:
            return Withdrawn(
                batch_id=frame.batch_id,
                assessment=reading,
                request=None,
                withdrawn=False,
                record_path=batch.assessment_path,
            )
        taken = WithdrawnRequest(
            position=requested.position,
            integration=requested.integration,
            requested_at=requested.requested_at,
            withdrawn_at=format_rfc3339(moment),
        )
        recorded = _write_assessment(
            config,
            batch,
            replace(reading, requested=None, withdrawn=reading.withdrawn + (taken,)),
            frame,
        )

    return Withdrawn(
        batch_id=frame.batch_id,
        assessment=recorded,
        request=requested,
        withdrawn=True,
        record_path=batch.assessment_path,
    )


def resolve(
    config: EvolutionConfig,
    *,
    verdict: str,
    confidence: str,
    rationale: str,
    expect: str | None = None,
    now: datetime | None = None,
) -> Formed:
    """Record the reading the completed counterfactual settles.

    A reading is formed while the cohorts are all there is, and in practice that
    is `inconclusive`: no manifest states the shape of the work, so the two task
    sets suspect rather than settle. This is where the pinned run answers — the
    verdict, the confidence and the sentence that says why, read off numbers that
    did not exist when the reading was formed.

    The verdict is held to that run by the parser every read uses: it supports
    the way every goal that moved points, all of them unmoved is `neutral`, and
    goals pointing both ways settle nothing. So a claim the only comparison
    supporting it contradicts never reaches the disk.

    This revises the reading rather than adding a second one, which is the
    difference from forming it: a formation happens once because two records of
    one release would leave a reader choosing between them, while this is the
    evidence that record was written to be added to. It stops when the gate
    settles — a decision stands on the reading it was made from, so evidence and
    judgement added afterwards would rewrite the thing that was decided from.
    Run again with the same reading, it reports what is on record and appends no
    second audit line.
    """

    moment = _moment(now)
    text = require_reason(
        rationale,
        "a release assessment records why its verdict is that verdict; this one rests on a run whose numbers a "
        "later reader has, and the sentence is what says which of them the reading turned on",
    )

    with single_writer_lock(config):
        require_expected(config, expect)
        known = settled(config, now=moment)
        frame = _owing_frame(config, known)
        batch = frame.batch
        reading = _recorded(config, frame, "resolve")
        _require_unsettled(frame, reading, "a reading recorded now")

        refusal = resolve_refusal(frame, reading)
        if refusal is not None:
            raise BatchError(refusal)

        if (reading.verdict, reading.confidence, reading.rationale) == (verdict, confidence, text):
            return Formed(
                batch_id=frame.batch_id,
                assessment=reading,
                record_path=batch.assessment_path,
                recorded=False,
            )

        stamp = format_rfc3339(moment)
        recorded = _write_assessment(
            config,
            batch,
            replace(reading, verdict=verdict, confidence=confidence, rationale=text, formed_at=stamp),
            frame,
        )
        append_records(
            config,
            [
                build_record(
                    RECORD_RELEASE_ASSESSED,
                    recorded_at=stamp,
                    batch_id=frame.batch_id,
                    experiment_id=frame.subject.experiment_id,
                    revision=frame.subject.revision,
                    detail=verdict,
                )
            ],
        )

    return Formed(batch_id=frame.batch_id, assessment=recorded, record_path=batch.assessment_path)


# --- the settlement ----------------------------------------------------------


@dataclass(frozen=True)
class Settled:
    """The gate answered, and what answering it did to the source line."""

    batch_id: str
    assessment: Assessment
    decision: Decision
    record_path: Path
    # The reversal this run performed, and None where it performed none: a
    # retained release, a rollback that had already landed when the gate came to
    # record it, and a settlement reported back rather than written again.
    reversal: RollbackResult | None = None
    # False when this reports the settlement already on record — the same
    # decision run again after an interruption, recognised by what it says.
    recorded: bool = True


def settle(
    config: EvolutionConfig,
    *,
    settlement: str,
    reason: str,
    expect: str | None = None,
    now: datetime | None = None,
) -> Settled:
    """Answer the human gate between a release and the next base freeze.

    What it decides is what the batch's own experiments will start from
    (invariant 17): `retain` leaves the release as the line every alternative in
    this batch builds on, and `rolled-back` takes it back off first, so the base
    the first experiment then freezes is the line as this decision left it. Until
    it is answered no base is frozen, which is what keeps the decision from being
    made silently by whatever the source line happened to hold.

    **The reversal is composed, not repeated.** `rolled-back` runs the rollback
    operation, which takes the reason and nothing else — which promotion, which
    line, whether anything later stands on it, and whether some working tree is
    sitting on that branch are all its own readings, and none of them is spelled
    a second time here. A reversal that already landed is adopted rather than
    made again: the operator may have run the rollback themselves, and a run
    interrupted between the commit and this record finishes by being repeated.

    **Refused before anything is made, and nothing writes in between.** A cohort
    that recorded no reading has nothing to settle, a gate that answered is not
    re-answered, evidence still in flight is not settled over, `retain` is
    refused for a release already off the line, and `rolled-back` is refused
    where the promotion this reading is about is not the one a rollback
    reverses. Each of those is asked before the commit, so a refusal costs
    nothing that had already happened — and the whole settlement runs under one
    hold of the single-writer lock, so the state they cleared is the state the
    reversal lands on. A composition that released the lock to reach the
    rollback's ordinary entry point would let another writer settle, measure or
    revise the reading in the gap, after which the inverse commit is on the
    source line for a decision nobody can record.

    What it does not do is weaken the rollback's own refusal that later work
    stands on the promotion. That refusal leaves the reading unsettled, and the
    way out of it is recorded rather than worked around: `retain`, whose reason
    says why a release a later attempt was built on stays on the line, or ending
    that lineage and reversing outside this controller — which is not an
    operation here, and so not one this gate can record as its own.

    Run again with the same answer, it reports the settlement on record and
    appends no second audit line — the rule every redo here follows, because the
    event happened once. A different answer is refused: the base freeze this gate
    releases may already have happened, and a decision the line was built on is
    not one to re-take.
    """

    moment = _moment(now)
    if settlement not in SETTLEMENTS:
        raise BatchError(
            f"{settlement!r} is not an answer to a release assessment; the gate settles {SETTLEMENT_RETAIN!r} — "
            f"the release stays the line the next base is frozen on — or {SETTLEMENT_ROLLED_BACK!r}, which takes "
            "it back off first"
        )
    text = require_reason(
        reason,
        "a settlement records why the release was kept or taken back; the reading is the evidence and this is "
        "the judgement made from it, which is the operator's own — an `inconclusive` reading is an ordinary "
        "ground for either answer",
    )

    with single_writer_lock(config):
        # First inside the one hold this whole settlement runs under, the
        # composed reversal included: a token checked again from inside that
        # rollback would be compared against a state this operation is the writer
        # of.
        require_expected(config, expect)
        known = settled(config, now=moment)
        # The one operation that follows an obligation to an owner that already
        # answered: what it has to do with a decision on record is report it, and
        # a redo that could not reach the record could not report it either.
        frame = _owing_frame(config, known, follow_answered=True)
        reading = _recorded(config, frame, "settle")
        decided = reading.decision
        if decided is not None:
            return _redone_settlement(frame, reading, decided, settlement, text)
        _require_settleable(frame, reading)
        if settlement == SETTLEMENT_RETAIN:
            _require_standing(frame)
            return _record_settlement(config, frame, reading, settlement, text, None, moment)
        # Already off the line — a completed rollback, since an inverse still in
        # flight leaves the promotion standing. The commit exists and the line
        # took it, so what is left to do is say so: an operator who reversed it
        # themselves and a run of this that stopped before its record are the same
        # state, and making a second inverse would reverse the reversal.
        if frame.subject.reversed_promotion:
            return _record_settlement(config, frame, reading, settlement, text, None, moment)
        _require_reversible(known, frame)

        # Inside the lock this operation already holds, through the rollback's
        # own lock-free entry (`rollback.reverse`). The reversal is still that
        # operation's whole self, refusals included, rather than its steps run
        # again from here — what the composition adds is that nothing else
        # writes between the refusals above and the commit they cleared. The
        # lock is not reentrant, so releasing it to reach the ordinary entry
        # point would leave exactly the window this gate cannot afford: another
        # writer settles the reading, starts a run, or revises the verdict, and
        # the inverse commit then lands on the source line for a decision this
        # run can no longer record.
        reversal = reverse_promotion(config, reason=text, now=moment)

        # Re-derived rather than carried across the reversal: the rollback
        # changed what the lineage says about this release, and the record about
        # to be written is checked against the reading as it now is. The reading
        # itself cannot have moved — nothing else has written since the refusals
        # above — so what this re-reads is the release, not the judgement.
        known = settled(config, now=moment)
        frame = _owing_frame(config, known, follow_answered=True)
        reading = _recorded(config, frame, "settle")
        return _record_settlement(config, frame, reading, settlement, text, reversal, moment)


def _record_settlement(
    config: EvolutionConfig,
    frame: Frame,
    reading: Assessment,
    settlement: str,
    reason: str,
    reversal: RollbackResult | None,
    moment: datetime,
) -> Settled:
    """Write the decision onto the reading, and say in the audit that the gate
    answered.

    The inverse commit is stated from what this run did where it did something,
    and from the rollback record's own reading where the reversal was already
    there. Either way the reader checks it against that record rather than
    against this write, which is what a settlement naming a commit nothing
    reversed is refused by.
    """

    revision = None
    if settlement == SETTLEMENT_ROLLED_BACK:
        revision = frame.subject.rollback_revision if reversal is None else reversal.revision
    decision = Decision(
        settlement=settlement,
        decided_at=format_rfc3339(moment),
        reason=reason,
        rollback_revision=revision,
    )
    recorded = _write_assessment(config, frame.batch, replace(reading, decision=decision), frame)
    append_records(
        config,
        [
            build_record(
                RECORD_RELEASE_SETTLED,
                recorded_at=decision.decided_at,
                batch_id=frame.batch_id,
                experiment_id=frame.subject.experiment_id,
                revision=frame.subject.revision,
                detail=settlement,
            )
        ],
    )
    return Settled(
        batch_id=frame.batch_id,
        assessment=recorded,
        decision=decision,
        record_path=frame.batch.assessment_path,
        reversal=reversal,
    )


def _redone_settlement(
    frame: Frame,
    reading: Assessment,
    decided: Decision,
    settlement: str,
    reason: str,
) -> Settled:
    """The same settlement run again, or the refusal that this gate has answered.

    Recognised by what the record says rather than by a marker, like every other
    redo here: the decision either landed or it did not, and the one that landed
    is what a redo is asking for. `decided_at` is when it landed rather than part
    of what was decided, so what is compared is the answer and the reason for it.

    It reports no reversal, because this run made none: a redo reaches here
    before the composition, and the inverse commit a previous run landed is
    named by the decision it is reporting back.
    """

    if (decided.settlement, decided.reason) == (settlement, reason):
        return Settled(
            batch_id=frame.batch_id,
            assessment=reading,
            decision=decided,
            record_path=frame.batch.assessment_path,
            recorded=False,
        )
    raise BatchError(
        f"{frame.batch_id}'s reading of the {frame.subject.batch_id} release was settled {decided.settlement!r} "
        f"at {decided.decided_at}: {decided.reason!r}; this gate answers once, because what it releases is the "
        "base every experiment of this batch is frozen on — a release judged again after that is the next "
        "cohort's reading of the line as it now is"
    )


def _require_settleable(frame: Frame, reading: Assessment) -> None:
    """The gate answers over evidence that is over (`settleable_refusal`)."""

    refusal = settleable_refusal(frame, reading)
    if refusal is not None:
        raise BatchError(refusal)


# --- what a fresh run refuses -------------------------------------------------
#
# Each condition the release verbs check before they write, stated once as the
# refusal it would give, and read both by the operation and by the console's gate
# (`phase.allowed_actions`). Two statements of one rule is how a gate comes to
# refuse a verb the operation allows, or offer one it does not.
#
# State alone: the frame the lineage derives and the reading on record. Every
# argument a verb is given — which way a settlement goes, what a resolution
# claims — stays with the operation, under the lock.


def no_cohort_refusal() -> str:
    """Why nothing here reads a release when no cohort is frozen."""

    return (
        "no batch is current, so there is no cohort to read a release from; an assessment is one frozen "
        "cohort's answer about the promotion before it, and what freezes a cohort is "
        "`mandrel evolution start` (invariant 14)"
    )


def no_release_refusal(batch_id: str) -> str:
    """Why a cohort that follows no promotion has nothing to assess (invariant 7)."""

    return (
        f"{batch_id} follows no promotion, so there is no release for it to assess; a repository "
        "that promoted nothing before this cohort and a predecessor that concluded `no-change` both leave "
        "nothing an upgrade effect could be measured against, and none is invented (invariant 7)"
    )


def owner_refusal(batch_id: str, subject: Subject, owed: Obligation | None) -> str | None:
    """Why the cohort standing at the keyboard is not the one that records this.

    An obligation an earlier cohort left *outstanding* is not this: the record
    still belongs in that cohort's directory and these operations follow it there
    (`_owing_frame`), because a gate nothing can answer would stop the lineage
    for good. What is refused is a reading already answered — with the one
    exception the settlement takes, since reporting a decision back is what makes
    a carried-forward settlement recoverable.
    """

    if owed is not None and not owed.settled:
        return None
    return _not_the_owner(batch_id, subject, owed)


def _not_the_owner(batch_id: str, subject: Subject, owed: Obligation | None) -> str:
    owner = batch_id if owed is None else owed.owner_id
    return (
        f"{batch_id} is not the cohort that owes a reading of the {subject.batch_id} release; "
        f"the first batch frozen after that promotion is {owner}, whose reports are the earliest evidence "
        "about it, and it has read that release and had it settled (invariant 14)"
    )


def reading_refusal(frame: Frame, reading: Assessment | None, action: str) -> str | None:
    """Why there is no reading for the counterfactual or the gate to act on.

    Everything those do is done to that record: it is where the run is written,
    and what the run's numbers afterwards settle. A cohort that has not read the
    release yet has nothing to add either to — and the order is the point rather
    than bookkeeping, since what a pinned run answers is a suspicion the cohorts
    raised, and what the gate answers is the reading that came of it.
    """

    return None if reading is not None else _nothing_read(frame, action)


def _nothing_read(frame: Frame, action: str) -> str:
    return (
        f"{frame.batch_id} has recorded no reading of the {frame.subject.batch_id} release, so there is "
        f"nothing to {action}; the cohorts are read first — what they come to is the suspicion a pinned run "
        "is started to settle, and the reading it leaves is what the gate answers"
    )


def unsettled_refusal(frame: Frame, reading: Assessment, described: str) -> str | None:
    """Why nothing is added to a reading the human gate has already answered."""

    decision = reading.decision
    if decision is None:
        return None
    return (
        f"{frame.batch_id}'s reading of the {frame.subject.batch_id} release was settled {decision.settlement!r} "
        f"at {decision.decided_at}, so {described} would change the record that decision was made from; a "
        "settlement stands on the evidence it stood on, and a release read again after its gate has answered is "
        "the next cohort's reading of the line as it now is"
    )


def run_refusal(frame: Frame, reading: Assessment, action: str) -> str | None:
    """Why there is no run for these two verbs to answer for.

    A request outstanding is the state where the newest thing on record is not a
    run at all: the harness was asked and its answer never reached this record.
    Reporting "no run" there would hide that something may be measuring right
    now — so what is reported instead is the two ways forward: start the run
    again, which records what the harness began, or give the request up.
    """

    if reading.requested is not None:
        return (
            f"{frame.batch_id} has {_describe(reading.requested.position)} of its counterfactual outstanding, "
            f"and no run recorded for it; the harness was asked for that run and its answer never reached this "
            f"record, so there is nothing here to be {action} — start the run again to record what it began, or "
            "withdraw the request"
        )
    return None if reading.counterfactual is not None else _nothing_started(frame, action)


def end_refusal(frame: Frame, reading: Assessment) -> str | None:
    """Why there is no run for an abandonment to record the end of.

    One state more than a conclusion's, and it is the asymmetry between the two
    redos: this operation writes one particular result, so a run that ended some
    other way is not its work and is refused rather than overwritten. The
    operation asks these in the same order and not as one call, since which of
    the two an ended run is turns on the reason it is given.
    """

    refusal = run_refusal(frame, reading, "ended")
    if refusal is not None:
        return refusal
    run = reading.counterfactual
    if run is None or run.running:
        return None
    return _already_ended(frame, run)


def _already_ended(frame: Frame, run: Counterfactual) -> str:
    result = run.result
    return (
        f"{_describe(run.position)} of {frame.batch_id}'s counterfactual already ended "
        f"{(result.outcome if result is not None else '')!r}: {(result.detail if result is not None else '')!r}; "
        "a run ends once, and what is on record is what it measured — start another attempt rather than "
        "restating how this one finished"
    )


def _nothing_started(frame: Frame, action: str) -> str:
    return (
        f"{frame.batch_id} has started no counterfactual of the {frame.subject.batch_id} release, so there "
        f"is no run to be {action}; what these record is the end of a run this controller started, and a "
        "harness invocation nothing here named is not one it can speak for"
    )


def measure_refusal(frame: Frame, reading: Assessment) -> str | None:
    """Why a further run is not started under the one already on record.

    Not asked of a request already outstanding: a measurement run again with one
    on record is that request resumed rather than a second run (`measure`). A
    failed run is what a further attempt is for, and is not one either.
    """

    previous = reading.counterfactual
    if reading.requested is not None or previous is None or previous.failed:
        return None
    subject = frame.subject
    return (
        f"{frame.batch_id} has {_describe(previous.position)} of its counterfactual "
        + ("still running" if previous.running else "completed")
        + f" against the {subject.batch_id} release; "
        + (
            "a release is measured against one pinned pair at a time, so a second run started under it "
            "would leave two answers with nothing to choose between them — conclude that run first"
            if previous.running
            else "the release is measured once, and a second completed run would leave a reader choosing "
            "which of two answers the reading rests on"
        )
    )


def resolve_refusal(frame: Frame, reading: Assessment) -> str | None:
    """Why the reading is not one a completed run settles the direction of."""

    run = reading.counterfactual
    if run is not None and run.completed:
        return None
    if run is None:
        found = (
            "none has been started, and the pinned run is the comparison in which the release is the "
            "only difference"
        )
    elif run.running:
        found = f"{_describe(run.position)} is still going"
    else:
        ended = run.result.detail if run.result is not None else ""
        found = f"{_describe(run.position)} ended {RESULT_FAILED!r}: {ended}"
    return (
        f"{frame.batch_id} has no completed counterfactual of the {frame.subject.batch_id} release, so "
        f"there is nothing for a reading to be settled by — {found}; what the cohorts allow on their own "
        f"is {VERDICT_INCONCLUSIVE!r}"
    )


def settleable_refusal(frame: Frame, reading: Assessment) -> str | None:
    """Why the gate does not answer over a measurement still in flight
    (`settleable`)."""

    if settleable(reading.counterfactual, reading.requested):
        return None
    requested = reading.requested
    if requested is not None:
        return (
            f"{frame.batch_id} has {_describe(requested.position)} of its counterfactual outstanding, so its "
            f"reading of the {frame.subject.batch_id} release is not one to settle yet; nothing is added to a "
            "reading once its gate answers, so a run this record never heard back about would have nowhere to "
            "report — start the run again to record what the harness began, or withdraw the request"
        )
    run = reading.counterfactual
    position = "" if run is None else f" ({_describe(run.position)})"
    return (
        f"{frame.batch_id}'s counterfactual of the {frame.subject.batch_id} release{position} is still going, so "
        "its reading is not one to settle yet; nothing is added to a reading once its gate answers, and that run "
        "is the one comparison in which the release is the only difference — conclude it, or end it with a "
        "reason if its harness cannot report"
    )


def redone_withdrawal(reading: Assessment) -> bool:
    """Whether a withdrawal run here would report that nothing was outstanding.

    The one release verb that never refuses on the record: a single write with
    nothing after it either landed or did not, and the request's absence is the
    answer in both directions (`withdraw`).
    """

    return reading.requested is None


def _require_standing(frame: Frame) -> None:
    """A release is retained while the source line still carries it."""

    assessed = frame.subject
    if not assessed.reversed_promotion:
        return
    raise BatchError(
        f"{assessed.batch_id}'s promotion {assessed.revision[:12]} is no longer on {assessed.merge_input_ref}: "
        f"{(assessed.rollback_revision or '')[:12]} took it back out. {SETTLEMENT_RETAIN!r} says the release "
        f"stays the line the next base is frozen on, and that line now carries the reversal instead — "
        f"{SETTLEMENT_ROLLED_BACK!r} is what happened, and its reason is where a release taken back for reasons "
        "this reading never found is explained"
    )


def _require_reversible(known: Lineage, frame: Frame) -> None:
    """The release this reading is about is the promotion a rollback reverses.

    The rollback operation picks its own target — the newest promotion this
    repository recorded — and in a lineage whose batches form a series that is
    this release: nothing can have promoted since, because the cohort holding
    this reading is the current batch and it has concluded nothing (invariant
    14). It is asked all the same, because of what is about to happen if it does
    not hold: a commit lands on the source line reversing some other promotion,
    and the settlement naming it is then refused for naming a commit this
    release's own rollback record does not — leaving a line reversed for a
    decision nobody can record.
    """

    promoted = known.last_promoted
    if promoted is not None and promoted.batch_id == frame.subject.batch_id:
        return
    raise BatchError(
        f"{SETTLEMENT_ROLLED_BACK!r} reverses the newest promotion this repository recorded, which is "
        + ("none at all" if promoted is None else f"{promoted.batch_id}'s")
        + f", and this reading is of the {frame.subject.batch_id} release ({frame.subject.revision[:12]}); a "
        "rollback takes the last change off the line and no other, so a reading of an earlier release is "
        "settled by retaining it or by reversing that change outside this controller and recording why"
    )


def _recorded(config: EvolutionConfig, frame: Frame, action: str) -> Assessment:
    """The reading this cohort has already recorded, or why there is none.

    Everything the counterfactual does is done to that record: it is where the
    run is written, and what the run's numbers afterwards settle. So is the human
    settlement, which is a judgement made from a reading rather than about a
    release in the abstract. A cohort that has not read the release yet has
    nothing to add either to — and the order is the point rather than
    bookkeeping, since what a pinned run answers is a suspicion the cohorts
    raised, and what the gate answers is the reading that came of it.
    """

    reading = read(config, frame.batch, frame=frame)
    if reading is None:
        raise BatchError(_nothing_read(frame, action))
    return reading


def _require_unsettled(frame: Frame, reading: Assessment, described: str) -> None:
    """Nothing is added to a reading the human gate has already answered
    (`unsettled_refusal`)."""

    refusal = unsettled_refusal(frame, reading, described)
    if refusal is not None:
        raise BatchError(refusal)


def _require_run(frame: Frame, reading: Assessment, action: str) -> Counterfactual:
    """The run these operations act on, or why there is none (`run_refusal`)."""

    refusal = run_refusal(frame, reading, action)
    run = reading.counterfactual
    if refusal is not None or run is None:
        raise BatchError(refusal or _nothing_started(frame, action))
    return run


def _request(
    config: EvolutionConfig,
    frame: Frame,
    reading: Assessment,
    expectation: str,
    *,
    at: str,
) -> CounterfactualRequest:
    """The request a fresh start makes durable before the harness is asked.

    Every refusal a start has is here, which is what the order is for: once the
    request is written the harness may be running something, so nothing that
    could refuse may still be waiting to be checked at that point.

    The position is the promoted experiment's next round, which no run and no
    withdrawn request of that experiment can ever hold: every position it
    allocated names a round it has, and a promoted experiment is terminal — it
    can never open another. A further attempt takes the one after everything this
    reading has allocated — the failed run and every request it gave up —
    because the harness answers one key with one run, so a key handed out twice
    is answered the second time with the first one's run.
    """

    subject = frame.subject
    previous = reading.counterfactual
    refusal = measure_refusal(frame, reading)
    if refusal is not None:
        raise BatchError(refusal)

    pinned = Pinned(
        base_revision=subject.merge_input_revision,
        candidate_revision=subject.revision,
        source_ref=subject.merge_input_ref,
        tree=subject.tree,
    )
    _require_measurable(config, frame, pinned)
    round_number = subject.round_number + 1
    return CounterfactualRequest(
        position=Position(
            experiment_id=subject.experiment_id,
            round_number=round_number,
            attempt=_allocated(previous, reading.withdrawn, round_number) + 1,
        ),
        integration=pinned,
        expectation=expectation,
        requested_at=at,
    )


def _allocated(
    run: Counterfactual | None,
    withdrawn: Sequence[WithdrawnRequest],
    round_number: int,
) -> int:
    """The last attempt of one round this reading has handed out, to a run or to
    a request it gave up.

    Read off the highest rather than counted, which is where this differs from
    the replay history: that record keeps every run it ever wrote, and this one
    keeps the latest — a retry replaces the failed run it answers. So the
    attempts on record are not 1..N and the count is not the allocation; the
    highest one still is, because nothing here ever hands out a lower one.
    """

    positions = [taken.position.attempt for taken in withdrawn if taken.position.round_number == round_number]
    if run is not None and run.position.round_number == round_number:
        positions.append(run.position.attempt)
    return max(positions, default=0)


def _require_measurable(config: EvolutionConfig, frame: Frame, pinned: Pinned) -> None:
    """This checkout holds both revisions of the pair.

    Asked before a harness is handed them, because a harness cannot exercise what
    this repository does not have and the refusal an operator can act on is
    "fetch the release line", not whatever a missing object looks like from
    inside somebody else's harness.

    Presence is the whole of what is asked here, and deliberately. Whether the
    promotion carries the tree and the parents the outcome records is checked
    wherever this checkout can describe that commit — by the lineage derivation,
    on every read, which this operation has already run before it reaches this
    point. Asking it a second time would be a second spelling of one refusal,
    and the case it would be reached in does not exist.
    """

    for described, revision in (
        ("the revision the source line stood at before the release", pinned.base_revision),
        ("the promotion", pinned.candidate_revision),
    ):
        if resolve_commit(config.repo_root, revision) is None:
            raise BatchError(
                f"{config.repo_root} does not hold {described} ({revision[:12]}) of the {frame.subject.batch_id} "
                "release, so there is no pair here to measure; a counterfactual exercises both revisions as they "
                f"are — fetch {pinned.source_ref} and the objects behind it, or run this where they are"
            )


def _require_same_request(frame: Frame, requested: CounterfactualRequest, expectation: str) -> None:
    """A resume asks for the run that is outstanding, not for a different one.

    The pair needs no checking — it is the release's own merge unit, and the
    reader holds every recorded request to it — so what an operator can restate
    differently is the prediction. A mismatch is a request they have not made
    yet: the harness may already be running this one, and answering the new one
    would start a second run under a key the first still holds.
    """

    if requested.expectation == expectation:
        return
    raise BatchError(
        f"{frame.batch_id} has {_describe(requested.position)} of its counterfactual outstanding, expected to "
        f"show {requested.expectation!r}; an expectation is recorded before the numbers exist and this run may "
        "already be producing them, so the one on record stands — a second prediction over a run in flight is "
        "the reading it exists to prevent"
    )


def _harness_request(requested: CounterfactualRequest) -> ReplayRequest:
    """The counterfactual as the replay boundary is asked for it.

    The boundary's own integration, stated exactly: the merge input is the
    pre-promotion revision, because the promotion is the candidate merged onto
    the line at that commit and its tree is what the two produced. So the pair is
    read off the outcome rather than merged again, and the base a run measures
    against is the same commit — which is the whole of what a counterfactual is,
    the release against the line immediately before it.

    Nothing is asked to be reproduced. `reproduce` is how a second run of one
    comparison asks for the first one's selections by name, and this comparison
    has one run: a report states each quantity on the base *and* on the
    candidate, so the cohort, the evaluator and the configuration are one
    selection governing both halves rather than two that would have to be shown
    to have matched. Naming the promotion's own replay selections here instead
    would buy a comparability nothing reads, at the price of a case set that
    harness no longer holds barring a suspected regression from ever being
    measured.
    """

    integration = requested.integration
    return ReplayRequest(
        experiment_id=requested.position.experiment_id,
        round_number=requested.position.round_number,
        attempt=requested.position.attempt,
        integration=Integration(
            base_revision=integration.base_revision,
            candidate_revision=integration.candidate_revision,
            merge_input_revision=integration.base_revision,
            merge_input_ref=integration.source_ref,
            tree=integration.tree,
        ),
        reproduce=None,
    )


def _result(report: ReplayReport, *, at: str) -> RunResult:
    """A harness's report as this record states it.

    The one place the two vocabularies meet. A run measures a baseline and a
    candidate; here they are the line before the release and the release, so they
    are recorded as `before` and `after` — the same names the cohort comparison
    uses, which is what lets the two be read together at all.
    """

    return RunResult(
        outcome=report.outcome,
        concluded_at=at,
        detail=report.detail,
        elapsed_seconds=report.elapsed_seconds,
        metrics=tuple(
            Measurement(
                metric=item.metric,
                unit=item.unit,
                before=item.baseline,
                after=item.candidate,
                better=item.better,
            )
            for item in report.metrics
        ),
        regressions=report.regressions,
        ambiguity=report.ambiguity,
    )


def _audit_run(
    config: EvolutionConfig,
    frame: Frame,
    run: Counterfactual,
    *,
    outcome: str,
    at: str,
) -> None:
    """One line for a run that ended, however it ended.

    The release the run was about is the revision, as it is in the reading's own
    line; the round is the harness key the run occupied, which is what an
    operator matches against the harness's own history.
    """

    append_records(
        config,
        [
            build_record(
                RECORD_COUNTERFACTUAL_COMPLETED,
                recorded_at=at,
                batch_id=frame.batch_id,
                experiment_id=frame.subject.experiment_id,
                round=run.position.round_number,
                revision=frame.subject.revision,
                detail=outcome,
            )
        ],
    )


def _describe(position: Position) -> str:
    """A run named by the key it occupied, which is also its identity to the
    harness that answered for it."""

    return f"round {position.round_number} attempt {position.attempt}"


def _moment(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise BatchError(
            "the moment a release assessment was formed must be timezone-aware; a naive datetime records an "
            "ambiguous instant, and the interval between the promotion, the freeze and this reading is what "
            "makes it a later judgement"
        )
    return now


def _owed(lineage: Lineage, batch: Batch, assessed: Subject) -> bool:
    """Whether `batch` is the first cohort frozen after that promotion."""

    owner = _owner(lineage, assessed)
    return owner is not None and owner.batch_id == batch.batch_id


def _owner(lineage: Lineage, assessed: Subject) -> BatchLineage | None:
    """The cohort that owes a reading of that release: the first one frozen
    after the promotion.

    None only where nothing was frozen after it, which is a repository whose
    newest batch is the one that promoted — there is no cohort yet whose reports
    could say what the release did.
    """

    promoted = next(item for item in lineage.batches if item.batch_id == assessed.batch_id)
    following = lineage.after(promoted)
    return following[0] if following else None


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

    Before Git is asked anything, the stated revision is held to the shape the
    field is defined to have: the evaluated target's deploy-lock
    `source_git_commit`, which is a full object id and nothing else
    (`lockfile.is_object_id` — the same rule that side is read by, since both
    resolve what they read against this repository). Git would answer for `HEAD`,
    a branch, a tag or an abbreviation just as readily, and that answer is a
    reading of where *this* checkout stands: a report whose provenance stated one
    of those would be placed by this repository's position at the moment of
    reading, change sides when the checkout moved, and do it on the one reading
    that costs somebody a promoted change. So it is excluded, naming what it
    stated — not repaired, and not completed from anything beside it (invariant
    4). The exclusion is a fact about the record rather than about the clone,
    which is what separates it from an unresolvable revision below: every
    checkout reaches it, and reaches it from committed content alone.
    """

    if member.effective_revision is None:
        return None, Excluded(
            report_key=member.report_key,
            batch_id=member.batch_id,
            reason=EXCLUDED_REVISION_ABSENT,
            detail="the report states no effective revision, so nothing places it on either side",
        )

    stated = member.effective_revision
    if not is_object_id(stated):
        return None, Excluded(
            report_key=member.report_key,
            batch_id=member.batch_id,
            reason=EXCLUDED_REVISION_MALFORMED,
            detail=(
                f"the report states an effective revision {stated!r}, which is not a commit id; resolving a "
                "name would place it by where this checkout stands rather than by what that target held"
            ),
        )
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
    recorded *and landed*, since a claim that the line no longer carries a
    release is not one an assessment may make on its own. That direction needs no
    exception: a promotion a completed rollback reversed is never effective
    again, so a record legitimately formed against a reversed release still reads
    against one.
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
        _require_reversal_landed(path, reversal, derived, "the assessment says the promotion was reversed by")

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

    integration = _read_pinned(path, stated["integration"], assessed, "the counterfactual")
    position = _read_position(path, stated["position"], assessed, "the counterfactual")

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
    if parsed is None and harness["handle"] is None:
        raise BatchError(
            f"{path}: the counterfactual is still running and carries no harness handle; the handle is what a "
            "later process polls, so a run recorded without one can never be concluded — record the handle the "
            "harness issued, or record why the run ended"
        )
    return Counterfactual(
        position=position,
        integration=integration,
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


def _read_withdrawal(
    path: Path,
    stated: Mapping[str, Any],
    assessed: Subject,
) -> WithdrawnRequest:
    """One position a request held and gave up.

    Held to the same release and the same kind of key a run is, because that is
    what it is a record of: the position the harness may be running something
    under. A withdrawal naming another pair of revisions accounts for no request
    this comparison ever made.
    """

    return WithdrawnRequest(
        position=_read_position(path, stated["position"], assessed, "a withdrawn counterfactual request"),
        integration=_read_pinned(path, stated["integration"], assessed, "a withdrawn counterfactual request"),
        requested_at=stated["requested_at"],
        withdrawn_at=stated["withdrawn_at"],
    )


def _require_allocated_once(
    path: Path,
    run: Counterfactual | None,
    requested: CounterfactualRequest | None,
    withdrawn: Sequence[WithdrawnRequest],
) -> None:
    """One position names one request, for good.

    Positions are what the harness is keyed on, so two holders are two requests
    it cannot tell apart: it answers the second with the run it began for the
    first, and numbers measured against one thing land under a record stating
    another. Runs, withdrawals and the outstanding request are counted together
    because a withdrawal is exactly that — a position allocated, and never to
    carry a run.
    """

    holders: dict[tuple[int, int], str] = {}
    described: list[tuple[str, Position]] = [
        (f"the withdrawn request at {_describe(taken.position)}", taken.position) for taken in withdrawn
    ]
    if run is not None:
        described.append((f"the run at {_describe(run.position)}", run.position))
    if requested is not None:
        described.append((f"the outstanding request at {_describe(requested.position)}", requested.position))
    for name, position in described:
        key = (position.round_number, position.attempt)
        earlier = holders.get(key)
        if earlier is not None:
            raise BatchError(
                f"{path}: {name} and {earlier} both hold round {position.round_number} attempt "
                f"{position.attempt} of the counterfactual; a position is allocated once and names one request "
                "for good, because that is what the harness is keyed on — a second request wearing it would be "
                "answered with the first one's run"
            )
        holders[key] = name


def _require_settled_over_evidence(
    path: Path,
    decision: Decision | None,
    run: Counterfactual | None,
    requested: CounterfactualRequest | None,
) -> None:
    """A settled reading has nothing still in flight.

    Nothing is added to a reading its gate has answered, which is the rule the
    operations keep — so a settlement recorded over a run still going, or over a
    request the harness never answered for, is a decision whose evidence could
    never be recorded even when it arrives: the numbers would have nowhere to go,
    and the run would stay going forever with the reading closed above it.

    The states a settlement stands over are therefore all the ones that are over:
    a completed run, a failed one, and no run at all — that last being the
    ordinary shape of a retained release nobody suspected of anything. The rule
    is `settleable`, which the gate asks before it decides; what is here is the
    record-shaped refusal for a file that already claims one.
    """

    if decision is None or settleable(run, requested):
        return
    if requested is not None:
        raise BatchError(
            f"{path}: the reading is settled {decision.settlement!r} at {decision.decided_at} while "
            f"{_describe(requested.position)} of its counterfactual is outstanding; the harness was asked for "
            "that run and its answer never reached this record, and nothing is added to a reading its gate has "
            "answered — record the run or withdraw the request, and settle over what came back"
        )
    if run is not None and run.running:
        raise BatchError(
            f"{path}: the reading is settled {decision.settlement!r} at {decision.decided_at} while "
            f"{_describe(run.position)} of its counterfactual is still going; nothing is added to a reading its "
            "gate has answered, so numbers arriving after the settlement could never be recorded — conclude the "
            "run or end it with a reason, and settle over what it came to"
        )


def _read_request(
    path: Path,
    stated: Mapping[str, Any] | None,
    assessed: Subject,
    run: Counterfactual | None,
    withdrawn: Sequence[WithdrawnRequest],
) -> CounterfactualRequest | None:
    """The run this cohort asked for and has no answer to yet.

    Held to the same release and the same kind of position a recorded run is:
    the request is what the run will be recorded as, so a request nobody could
    answer — the wrong pair of revisions, a key an experiment holds — is refused
    here rather than discovered once the harness has already begun something.

    The one run a request may stand over is a failed one, which is the retry: a
    run that measured nothing is over, and another attempt is what answers it.
    Over a run still going, the two are one comparison being measured twice with
    nothing to choose between the answers; over a completed one, the release
    would be measured a second time after it had been measured. Neither is a
    state the write that records a run can produce — it clears the request in the
    same file — so both are a record saying something no operation here did.

    A further attempt takes the position after everything already allocated in
    that round — the failed run, and every request given up. The harness is keyed
    on that position, so reissuing an allocated one would be answered with the
    run it already holds, and skipping ahead would leave an allocation nothing
    accounts for.
    """

    if stated is None:
        return None
    position = _read_position(path, stated["position"], assessed, "the counterfactual request")
    if run is not None:
        if not run.failed:
            raise BatchError(
                f"{path}: {_describe(run.position)} of the counterfactual is "
                + ("still running" if run.running else "completed")
                + f", and {_describe(position)} is outstanding beside it; the write that records the run a "
                "request became clears it in the same file, so a record holding both leaves a reader guessing "
                "which of the two the harness is measuring this release with"
            )
        if position.round_number != run.position.round_number:
            raise BatchError(
                f"{path}: {_describe(run.position)} of the counterfactual failed and {_describe(position)} is "
                f"outstanding; another attempt answers that run in its own round {run.position.round_number}, "
                "and one in a later round is a second comparison rather than the retry of this one"
            )
    expected = _allocated(run, withdrawn, position.round_number) + 1
    if position.attempt != expected:
        raise BatchError(
            f"{path}: {_describe(position)} of the counterfactual is outstanding, and round "
            f"{position.round_number}'s next attempt is {expected}; a request holds the position its run will "
            "take, and one holding any other position either skips an allocation or claims one a recorded run "
            "or a withdrawal already has"
        )
    return CounterfactualRequest(
        position=position,
        integration=_read_pinned(path, stated["integration"], assessed, "the counterfactual request"),
        expectation=stated["expectation"],
        requested_at=stated["requested_at"],
    )


def _read_pinned(path: Path, stated: Mapping[str, Any], assessed: Subject, described: str) -> Pinned:
    """The pair a run pinned, held to the release this assessment is about.

    A run of any other two revisions measured a different question, however
    well-formed it is — and the pair is not computed here or anywhere else: it is
    the merge unit the promoted batch's outcome states, so a record naming
    something else disagrees with the release rather than with this reading.
    """

    expected = (
        ("base_revision", assessed.merge_input_revision),
        ("candidate_revision", assessed.revision),
        ("source_ref", assessed.merge_input_ref),
        ("tree", assessed.tree),
    )
    for name, value in expected:
        if stated[name] != value:
            raise BatchError(
                f"{path}: {described} pinned {name} {stated[name]!r}, but this release's {name} is {value!r}; "
                "a run of another pair of revisions measured another question"
            )
    return Pinned(
        base_revision=stated["base_revision"],
        candidate_revision=stated["candidate_revision"],
        source_ref=stated["source_ref"],
        tree=stated["tree"],
    )


def _read_position(path: Path, stated: Mapping[str, Any], assessed: Subject, described: str) -> Position:
    """The harness key a run occupied, held to the one rule that makes it safe.

    A conforming harness answers one key with one run, so a counterfactual keyed
    on a position the promoted experiment holds would be answered with that
    experiment's run — the numbers of a candidate against its base arriving under
    a record stating the release against the line before it. The positions an
    experiment holds are its recorded runs *and* the requests it withdrew, since
    both stay allocated and neither is ever reissued, and every one of them names
    a round that experiment has: a replay record naming a round the experiment
    does not have is refused where it is read. So a round beyond the promoted one
    is held by nothing, and — the promoted experiment being terminal, unable ever
    to open another round — will go on being held by nothing.

    Machine-independent: the promoted round is the merge unit's own, so this is
    the same answer in every checkout.
    """

    position = Position(
        experiment_id=stated["experiment_id"],
        round_number=stated["round"],
        attempt=stated["attempt"],
    )
    if position.experiment_id != assessed.experiment_id:
        raise BatchError(
            f"{path}: {described} was keyed on {position.experiment_id}, and this release was promoted from "
            f"{assessed.experiment_id}; the key names the experiment whose positions it has to stay clear of, "
            "and one naming another experiment says nothing about that"
        )
    if position.round_number <= assessed.round_number:
        raise BatchError(
            f"{path}: {described} was keyed on round {position.round_number} of {position.experiment_id}, which "
            f"promoted round {assessed.round_number}; a harness answers one key with one run, so a key inside "
            "the experiment's own rounds is answered with the run that round was measured by — a candidate "
            "against its base, standing under a record of the release against the line before it"
        )
    return position


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
    promoted batch's outcome, read for what it says about the source line and not
    only for the commit it names.
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
        _require_reversal_landed(path, revision, derived, "the settlement names")
    return Decision(
        settlement=settlement,
        decided_at=stated["decided_at"],
        reason=stated["reason"],
        rollback_revision=revision,
    )


def _require_reversal_landed(path: Path, revision: str, derived: Subject, described: str) -> None:
    """A commit a record calls a reversal must be one the source line took.

    A rollback is two writes with a window between them: the record naming the
    inverse commit lands first, and the line is only recorded as carrying it once
    the ref has moved (`reverted_at`). Between the two the record already states
    the revision while the promotion is still effective, and that is a durable
    state — an interrupted rollback stays there until the operation is run again.
    So the commit's identity is not on its own an answer to "did this promotion
    come off the line"; the lineage's own reading of that is asked as well.

    Called after the identity check, which is the half that says *which* rollback
    is meant. This is the half that says it happened, and the two questions are
    separate in the ordinary flow: the settlement that depends on it is what a
    later base freeze is gated on, and a release still on the line is not one a
    record may settle as taken off it.
    """

    if not derived.standing:
        return
    raise BatchError(
        f"{path}: {described} the inverse commit {revision[:12]}, and the rollback record beside the promoted "
        "batch's outcome has that commit prepared without the source line recorded as carrying it; the promotion "
        "is still what the line has until the rollback operation finishes that record, and a reversal is a commit "
        "that landed rather than one that was made"
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

    The exclusions are checked as far as each reason is anyone's to check. Two of
    them a frozen manifest settles by itself — it states no effective revision, or
    states one that is not a commit id at all — and both are read off committed
    bytes before Git is asked anything, so every clone reaches the same answer
    (`MANIFEST_SETTLED_EXCLUSIONS`). Post-rollback is answerable wherever Git can
    answer at all. Only an unresolvable revision is nobody's to check: a clone
    that resolves what the forming machine could not has learned something about
    itself rather than about the record.

    So `unresolvable` is taken on trust in both directions — stated where this
    checkout placed the report, derived where the record claimed something else —
    but only between two answers that were Git's to give. Where either side names
    a manifest-settled reason the two must agree exactly, because there the bytes
    decide: a record calling a malformed revision unresolvable is not a machine
    reporting its own limits but a record-level defect wearing the one reason no
    reader checks, and taking it on trust would put every badly shaped revision
    back beyond reach of the rule that exists to catch it.
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
        derived = frame.exclusion(item.report_key)
        if derived is not None and derived.reason == item.reason:
            continue
        by_manifest = item.reason in MANIFEST_SETTLED_EXCLUSIONS or (
            derived is not None and derived.reason in MANIFEST_SETTLED_EXCLUSIONS
        )
        unresolvable = item.reason == EXCLUDED_REVISION_UNRESOLVABLE or (
            derived is not None and derived.reason == EXCLUDED_REVISION_UNRESOLVABLE
        )
        if unresolvable and not by_manifest:
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
            + (
                "whether a frozen manifest states a revision, and whether what it states is a commit id, are "
                "read from committed bytes before Git is asked anything, so that answer is the same in every "
                f"clone and {EXCLUDED_REVISION_UNRESOLVABLE!r} does not stand in for it in either direction"
                if by_manifest
                else "an exclusion states what the provenance could not say, and this one says something else"
            )
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

    A verdict resting on the counterfactual is also held to what that run
    measured. The cohorts are two different task sets and their numbers are read
    by the judging session, with `confidence` and `rationale` for what the reading
    weighs; the counterfactual is the release measured directly, so a direction it
    contradicts is not a judgement about it but a claim about something else.
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
        goals = tuple(measurement for measurement in run.metrics if measurement.directional)
        if not goals:
            raise BatchError(
                f"{path}: {verdict!r} rests on the counterfactual, and the run it records measured no goal "
                f"quantity on both revisions — every number it states is an observation ({BETTER_NEITHER!r}) "
                f"or missing a side, which settles nothing about the release (invariant 13)"
            )
        supported = measured_direction(goals)
        if supported is None:
            raise BatchError(
                f"{path}: {verdict!r} rests on the counterfactual, and its goal quantities point both ways "
                f"({_reading(goals)}); a run whose goals disagree settles no direction, and which of them "
                f"the release is judged on is chosen when the run is configured — the rest are recorded as "
                f"observations ({BETTER_NEITHER!r}) rather than weighed against it afterwards. What this "
                f"evidence allows is {VERDICT_INCONCLUSIVE!r}"
            )
        if supported != verdict:
            raise BatchError(
                f"{path}: {verdict!r} rests on the counterfactual, and the run it records measured "
                f"{supported!r} ({_reading(goals)}); a direction is what its own evidence came to, and a "
                "verdict the only comparison supporting it contradicts is an opinion with a run attached"
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


def _direction(measurement: Measurement) -> str:
    """Which way one measured goal moved, in the verdict's own words.

    Asked of a goal with both sides measured; `better` says which way is the
    improvement, and equal values are the release changing nothing about that
    quantity rather than a missing answer.
    """

    if measurement.after == measurement.before:
        return VERDICT_NEUTRAL
    improved = (
        measurement.after < measurement.before
        if measurement.better == BETTER_LOWER
        else measurement.after > measurement.before
    )
    return VERDICT_IMPROVED if improved else VERDICT_REGRESSED


def _reading(goals: Sequence[Measurement]) -> str:
    """What the run's goals came to, quantity by quantity — a refusal that names
    only the disagreement leaves the reader to reopen the record for the
    numbers."""

    return ", ".join(
        f"{measurement.metric} {measurement.before} → {measurement.after} with {measurement.better!r} better: "
        f"{_direction(measurement)}"
        for measurement in goals
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


def _request_json(request: CounterfactualRequest) -> dict[str, Any]:
    return {
        "position": _position_json(request.position),
        "integration": _pinned_json(request.integration),
        "expectation": request.expectation,
        "requested_at": request.requested_at,
    }


def _withdrawal_json(taken: WithdrawnRequest) -> dict[str, Any]:
    return {
        "position": _position_json(taken.position),
        "integration": _pinned_json(taken.integration),
        "requested_at": taken.requested_at,
        "withdrawn_at": taken.withdrawn_at,
    }


def _position_json(position: Position) -> dict[str, Any]:
    return {
        "experiment_id": position.experiment_id,
        "round": position.round_number,
        "attempt": position.attempt,
    }


def _pinned_json(integration: Pinned) -> dict[str, Any]:
    return {
        "base_revision": integration.base_revision,
        "candidate_revision": integration.candidate_revision,
        "source_ref": integration.source_ref,
        "tree": integration.tree,
    }


def _counterfactual_json(run: Counterfactual) -> dict[str, Any]:
    return {
        "position": _position_json(run.position),
        "integration": _pinned_json(run.integration),
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
