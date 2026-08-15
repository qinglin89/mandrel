"""Where the evolution lifecycle currently stands.

`status` answers one question — what is the next thing that happens, and what is
holding it — so it names a **phase**, not a pool count. The phase is derived on
every call from what is already on disk: the frozen manifests, the closure,
outcome and experiment records, the experiment refs, the runtime pool, and git.
Nothing here is stored, and nothing here writes.

That is deliberate and it is the same rule the workflow runbook's scheduler
follows: flow state that is written down is flow state that can disagree with
the artifacts. A phase re-derived from the artifacts cannot go stale, cannot be
lost with `.ai-evolution/`, and needs no migration when the lifecycle grows a
step.

**Precedence.** One label has to be chosen when several facts are true at once,
and since a batch is current from its freeze until its outcome (invariant 14),
the order is simply how far that one batch has got:

1. No current batch — `pool` / `idle`. Evidence accumulating, or nothing at all.
2. `batch-frozen`, then `dispositions-ready` once findings are written: the
   analysis stage, which ends at a completed analysis task.
3. `supersede-pending` — a supersession recorded its decision and not the
   successor it names, so the batch has no attempt to work in until the same
   supersession is redone. It outranks everything below it because every one of
   those operations refuses in that state.
4. `implementing`, then `candidate-ready` once the round is sealed: the open
   experiment, which is where work and then replay happen.
5. `proposals-pending` — no experiment is open and drafts are waiting at the
   human admission gate (invariant 9).
6. `conclusion-pending` — nothing is open and nothing is waiting, so what the
   batch needs is its outcome: a promotion, or the `no-change` that says the
   evidence justified nothing (invariant 7). It is also exactly the condition
   `conclude-no-change` writes under, so this label is the operation's own
   precondition read back.

Every fact behind the choice is emitted in the JSON regardless of which label
won, so a reader that cares about a lower-precedence one does not have to
re-derive it.

Replay evidence is emitted the same way and chooses no label. `candidate-ready`
says a round has a pinned candidate, which is what may be measured; whether
anything has measured it, and whether that measurement still describes the tree
a promotion would carry, is the separate reading a promotion is refused on. A
phase that folded the two together would report the same word for a round
nobody has replayed and one whose evidence went stale this morning.

**Nothing durable is reachable only as a refusal.** A phase label says how far
the batch has got; what narrows the next operation is often something else, and
each of those is a field rather than a note. A promotion prepared and not
finished leaves `promote` as the only verb its experiment accepts. A replay
request the harness never answered for is work that may be going, and it is
deliberately left out of the evidence notes exactly where the evidence supports
a promotion — so "no note" never means "nothing outstanding" and only the field
can say. The reading a release owes is a gate on the next base freeze, and it
belongs to the cohort frozen after the promotion whether or not that cohort is
still the current one. Every one of those used to be met as a refusal with
nothing on the surface explaining it.

**Absences are states.** Null here is a fact and not a gap, and the pairs that
would collapse into one are kept apart deliberately: a round nothing has
replayed against a batch with no round for evidence to be about, an inverse
commit the line has not taken against a promotion nobody reversed, an empty
cohort against cohorts that disagree, and a promotion nobody prepared against a
build that recorded no merge unit — the last of which is why the prepared
promotion is read off the open experiment and a promotion that happened is read
off its batch's outcome.

**What is local.** `.ai-tasks/` is machine-local and gitignored, so the analysis
stage of a current batch is answerable only on the machine holding that task;
every other clone reads the committed closure record instead. Nothing else here
depends on it — admitted change tasks are named by the experiment record, which
is committed. Staged evaluation bundles under `.ai-evolution/` are machine-local
too, which is why a current batch reports how much of its evidence is present
*here* rather than treating an absent bundle as damage.

**The gate.** Beside the reading, this answers what may be *done* with it:
`allowed_actions` names every verb the lifecycle has, says which of them this
state accepts, and gives the rest a reason. It is derived from the same facts and
in the same order the guarded operations check them, so an operator meets a
refusal here rather than at a write — and the narrowings that used to be
reachable only as a refusal (a prepared promotion, a supersession owing its
successor, a release nobody settled, a recorded rollback this checkout cannot
confirm) are what it exists for.

It is a reading, and the operation stays the authority. Four conditions are
deliberately not asked here, each because the answer belongs to the moment of the
write rather than to a status: whether some working tree is sitting on the source
line, whether a later attempt stands on the promotion a rollback would reverse,
whether an admitted task's file is the copy that admission published, and
whatever the harness or Git answers while the single-writer lock is held. Nor is
any argument a verb is given — which drafts an admission selects, which way a
settlement goes — since this says whether the verb may run at all. A verb this
gate allows may still refuse on one of those; a verb it refuses is refused.

**Two forms of every verb.** Every operation here is redoable by being run again
with the same arguments: it finishes what its interrupted run left, reports what
is on record, and writes nothing twice. So a state carrying an interrupted or
completed operation *accepts* that verb — an already-sealed round, a recorded
rejection, a batch that concluded, a promotion already reversed — and a gate
reading only the fresh form would refuse the one thing that repairs a durable
interruption. Each verb is therefore projected in both forms, and where this
state holds a redo rather than the conditions for a fresh run, the action says
what running it would finish or report and names the object that redo acts on,
which for a decision or a conclusion is one this batch already ended. Which state
a redo recognises is the owning module's to say (`experiments.redone_*`,
`replay.redone_*`, `rollback.redone_reversal`), asked from here and from the
operation itself; whether the arguments match what is on record stays with the
operation, under the lock.

`state_revision` is the token that makes acting on this reading safe. It is a
digest of the durable state the lifecycle is derived from — the versioned records,
the pool, the policy, and the tips of the refs those records name — so a mutation
given the revision an operator saw can refuse rather than act on a lifecycle
another writer has changed underneath it. It deliberately does not cover
`.ai-tasks/`: a task finishing is this machine's own work rather than a competing
writer's, and every operation re-reads it under the lock anyway.

It is taken twice, before and after the reading, and a difference between the two
is a writer that finished while this was being read: the reading would then
describe the repository before that write and the token would describe it after,
which is exactly the pair a later mutation is meant to refuse and would instead
accept. So the reading is taken again rather than published with a token that
does not describe it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import experiments, replay as replay_ops, rollback as rollback_ops
from .analysis_task import task_finished
from .assessment import Assessment, Counterfactual, Frame, Obligation, Subject
from .assessment import obligation as describe_obligation
from .batches import REASON_CURRENT_BATCH, AdmissionDecision, awaiting_analysis, evaluate_admission
from .config import EvolutionConfig
from .errors import BatchError
from .lineage import (
    DECISION_ABANDONED,
    DECISION_PROMOTED,
    DECISION_SUPERSEDED,
    BatchLineage,
    Experiment,
    Gate,
    Lineage,
    PreparedPromotion,
    RefState,
    Round,
)
from .lineage import describe as describe_lineage
from .manifests import OUTCOME_PROMOTED, load_batches
from .replay import Evidence, History, PendingRun, Replay, WithdrawnRequest, describe_evidence, read_replays
from .revisions import Revision, ref_tip
from .state import artifacts_dir_name, load_state

SCHEMA_VERSION = 7

PHASE_IDLE = "idle"
PHASE_POOL = "pool"
PHASE_BATCH_FROZEN = "batch-frozen"
PHASE_DISPOSITIONS_READY = "dispositions-ready"
PHASE_PROPOSALS_PENDING = "proposals-pending"
PHASE_IMPLEMENTING = "implementing"
PHASE_CANDIDATE_READY = "candidate-ready"
PHASE_SUPERSEDE_PENDING = "supersede-pending"
PHASE_CONCLUSION_PENDING = "conclusion-pending"

# A round's two states (contract: Lifecycle states).
ROUND_OPEN = "open"
ROUND_CANDIDATE_READY = "candidate-ready"

# What a release's counterfactual stands at. Four states and an absence, and the
# first of them is not a run at all: a request outstanding is a run that may be
# going at a harness this repository will never hear from again, and the two
# things to do about it — ask again, or give it up — are neither of the things a
# recorded run offers. It outranks a `failed` run because a request may only ever
# stand over one of those, and when it does the retry is what is live.
COUNTERFACTUAL_NONE = "none"
COUNTERFACTUAL_REQUESTED = "requested"
COUNTERFACTUAL_RUNNING = "running"
COUNTERFACTUAL_FAILED = "failed"
COUNTERFACTUAL_COMPLETED = "completed"

# Every verb the lifecycle has, named as the operator runs it. One name per
# domain operation and no name without one: a verb this surface offers that no
# operation implements is a lifecycle only the console believes in.
ACTION_START = "start"
ACTION_CREATE = "create"
ACTION_REJECT = "reject"
ACTION_ADD_TASKS = "add-tasks"
ACTION_SEAL_ROUND = "seal-round"
ACTION_REVISE = "revise"
ACTION_REPLAY_START = "replay-start"
ACTION_REPLAY_CONCLUDE = "replay-conclude"
ACTION_REPLAY_ABANDON = "replay-abandon"
ACTION_REPLAY_WITHDRAW = "replay-withdraw"
ACTION_PROMOTE = "promote"
ACTION_ABANDON = "abandon"
ACTION_SUPERSEDE = "supersede"
ACTION_CONCLUDE_NO_CHANGE = "conclude-no-change"
ACTION_ROLLBACK = "rollback"
ACTION_ASSESS = "assess"
ACTION_ASSESS_MEASURE = "assess-measure"
ACTION_ASSESS_CONCLUDE = "assess-conclude"
ACTION_ASSESS_ABANDON = "assess-abandon"
ACTION_ASSESS_WITHDRAW = "assess-withdraw"
ACTION_ASSESS_RESOLVE = "assess-resolve"
ACTION_SETTLE = "settle"

# What each verb acts on. The id is the durable one the operation itself takes —
# an `experiment_id` a decision may name, the batch a conclusion ends, the
# promotion a rollback reverses — so a surface holding one of these has the value
# to send rather than a label it would have to translate.
OBJECT_POOL = "pool"
OBJECT_BATCH = "batch"
OBJECT_EXPERIMENT = "experiment"
OBJECT_PROMOTION = "promotion"
OBJECT_RELEASE = "release"

# The scheme `state_revision` is computed by. A token from another scheme is a
# different thing from a lifecycle that moved, and only the version tells them
# apart — so a mutation refusing a stale expectation can say which happened.
STATE_REVISION_VERSION = 1
_REVISION_DIGITS = 16
# How many times a reading is taken again when a writer finishes underneath it.
# Every writer here holds the single-writer lock for one short operation, so a
# second attempt already answers the ordinary race; a third losing as well is a
# repository being written to continuously, which is a state to report rather
# than to keep retrying through.
_SNAPSHOT_ATTEMPTS = 3


@dataclass(frozen=True)
class BatchView:
    """The current batch, as a lifecycle reader needs it."""

    batch_id: str
    task_count: int
    report_count: int
    analysis_task_id: str | None
    findings_recorded: bool
    # The analysis stage has ended — a different and earlier moment than the
    # batch ending, which is what the outcome record does.
    analysis_complete: bool
    # Staged bundles for this batch's reports that exist on this machine. A
    # frozen cohort owns its reports wherever it was frozen, so a clone that
    # never staged them holds a manifest with no evidence behind it — a fact to
    # show, not an error: the manifest's hashes still say what was analyzed.
    evidence_local: int
    experiment_count: int

    @property
    def evidence_complete(self) -> bool:
        return self.evidence_local == self.report_count

    def to_json(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "task_count": self.task_count,
            "report_count": self.report_count,
            "analysis_task_id": self.analysis_task_id,
            "findings_recorded": self.findings_recorded,
            "analysis_complete": self.analysis_complete,
            "evidence_local": self.evidence_local,
            "experiment_count": self.experiment_count,
        }


@dataclass(frozen=True)
class LifecycleRevisions:
    """The commits in play for the current batch (contract: Revisions in play).

    Three of the contract's five, and they are the three this repository can
    know about a batch that has not concluded. The promotion revision belongs to
    a batch that has (`LifecycleStatus.last_promotion`), and the deployed
    effective revision is per target — it lives in each target's
    `.ai-deploy-lock.json`, which `aii-2 status` reads, and lags promotion until
    that target is redeployed.
    """

    base: Revision | None = None
    candidate_tip: Revision | None = None
    round_candidate: Revision | None = None


@dataclass(frozen=True)
class Rollback:
    """A promotion taken back off the source line, or an inverse on its way there.

    `reverted_at` is null while the inverse commit exists and the line has not
    been recorded as carrying it — an operation in flight rather than a rollback
    that did not happen. The two are different next steps: one is done, and the
    other is finished by running the rollback again.

    Unless it cannot be. `unconfirmed` is why this checkout could not settle that
    the recorded commit is the promotion taken back out of the line it was made
    from — an object it does not hold, or a Git that cannot compute the revert —
    and the rollback operation refuses on exactly that while the reader carries
    on. It is the one state here that reads differently from how it acts, which
    is why it is a field rather than something the next attempt tells you.
    """

    revision: str
    reverted_from: str
    reverted_at: str | None
    reason: str
    unconfirmed: str | None = None


@dataclass(frozen=True)
class Promotion:
    """The last change this repository recorded as reaching the source line.

    The merge unit as well as the revision, because the revision alone is a
    commit an operator cannot check against anything: with the round and the
    candidate it names what was measured, and with the merge input it names the
    line it went onto.

    `planned_targets` is what the promotion was recorded as intending to
    redeploy, and it is never what any target holds. That is per target, lags
    every promotion until the target is redeployed, and is read from that
    target's own `.ai-deploy-lock.json` — `aii-2 status`, not this.
    """

    batch_id: str
    experiment_id: str
    revision: str
    round_number: int
    candidate_revision: str
    merge_input_revision: str
    merge_input_ref: str
    tree: str
    planned_targets: tuple[str, ...]
    # The reversal, if this promotion has one. Not a second promotion and not an
    # absence of this one: the commit stays on the line and stays recorded, and
    # what this says is that a later commit took the change back out.
    rollback: Rollback | None = None


@dataclass(frozen=True)
class BatchRecord:
    """One frozen batch, as history: what it held and how it ended.

    Every batch is listed, the current one included, because "which batch is
    current" is a fact about the series rather than a property of a batch read
    in isolation — a reader given only the current one cannot say whether it is
    the first cohort after a promotion, and that is the question the release
    gate turns on.

    A promoted batch carries its merge unit here, read from its own outcome.
    That is deliberate and it is the only place this surface reads one from: the
    experiment record's prepared promotion is absent on a version-1 record for a
    reason that has nothing to do with whether a promotion happened, so a
    history entry taking the merge unit from there would report a build's age as
    a missing promotion. The outcome states it at every version.
    """

    batch_id: str
    task_count: int
    report_count: int
    experiment_count: int
    current: bool
    outcome: str | None
    decided_at: str | None
    reason: str | None
    promotion: Promotion | None

    def to_json(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "task_count": self.task_count,
            "report_count": self.report_count,
            "experiment_count": self.experiment_count,
            "current": self.current,
            # Null while the batch is current: an outcome is what ends one
            # (invariant 14), so its absence is the batch still running rather
            # than an outcome nobody recorded.
            "outcome": self.outcome,
            "decided_at": self.decided_at,
            "reason": self.reason,
            "promotion": _promotion_json(self.promotion),
        }


@dataclass(frozen=True)
class ReleaseReading:
    """The reading of the release before the current batch, wherever it sits.

    Two batches are in play and they are ordinarily one: the cohort whose cycle
    is running, and the cohort that owes the reading. They part company as soon
    as the owing cohort ends without answering — the obligation stays where the
    record is (invariant 17) — and a surface that showed only the current
    batch's own frame would offer an admission that the next base freeze
    refuses.
    """

    batch_id: str
    obligation: Obligation

    @property
    def owner_id(self) -> str:
        return self.obligation.owner_id

    @property
    def owned_here(self) -> bool:
        """Whether the cohort that owes the reading is the one running now."""

        return self.owner_id == self.batch_id

    @property
    def frame(self) -> Frame:
        return self.obligation.frame

    @property
    def reading(self) -> Assessment | None:
        return self.obligation.reading

    @property
    def settled(self) -> bool:
        return self.obligation.settled

    @property
    def assessed(self) -> Subject:
        """The release as the recorded reading is about it, which is not always
        as the lineage reads it now.

        The two part company on the ordinary end of a regression finding:
        `assessment.settle(..., "rolled-back")` forms nothing but writes its
        decision onto a reading formed while the promotion stood, and lands the
        inverse commit after it. From then on the record answers the
        standing-release question while the source line no longer carries the
        release. The reader keeps the record's own subject for that reason
        (`assessment._read_subject`) — re-deriving it would make every rollback
        contradict the assessment that justified it — and this is where the
        distinction reaches the surface.

        Identity is the same on both and only these two fields can differ: a
        record naming another promotion, candidate, merge input or tree is
        refused at read.
        """

        reading = self.reading
        return self.frame.subject if reading is None else reading.subject

    @property
    def reversed_after_reading(self) -> bool:
        """Whether the line stopped carrying the release after it was read.

        Asked of `standing` alone: an inverse commit that is only prepared
        leaves the promotion standing, and the record drops that commit rather
        than claiming a reversal (`assessment._as_assessed`), so the two differ
        there without answering different questions.
        """

        return self.assessed.standing and not self.frame.subject.standing

    @property
    def counterfactual(self) -> Counterfactual | None:
        return None if self.reading is None else self.reading.counterfactual

    @property
    def counterfactual_state(self) -> str:
        """Which of the four the pinned comparison stands at.

        The request outranks the run underneath it because the only run one may
        stand over is a failed one, and a failed run answered by another
        `measure` is history — what is live is the request, and what to do about
        it is ask again or give it up. The failed run stays reported beside it.
        """

        reading = self.reading
        if reading is None:
            return COUNTERFACTUAL_NONE
        if reading.requested is not None:
            return COUNTERFACTUAL_REQUESTED
        run = reading.counterfactual
        if run is None:
            return COUNTERFACTUAL_NONE
        if run.running:
            return COUNTERFACTUAL_RUNNING
        return COUNTERFACTUAL_COMPLETED if run.completed else COUNTERFACTUAL_FAILED

    @property
    def in_flight(self) -> bool:
        """Whether a measurement is still being taken.

        The settlement is refused over one, so this is the state a surface
        offering the gate has to resolve first — by concluding the run, ending
        it, or withdrawing the request.
        """

        return self.counterfactual_state in (COUNTERFACTUAL_REQUESTED, COUNTERFACTUAL_RUNNING)


@dataclass(frozen=True)
class Action:
    """One verb, whether this state accepts it, and what it would act on.

    Every verb is emitted every time, refused ones included, because a menu that
    listed only what is legal would leave "why not" to be discovered by running
    it — which is the whole failure this surface exists to prevent.

    `object_type` is null where the verb is about something that does not exist
    here: no promotion to reverse, no release to read, no experiment to end. That
    is the difference between a verb an operator can do something about and one
    that is simply not what this lifecycle is at, and it is what a rendering
    decides how loudly to say a refusal by.
    """

    action: str
    object_type: str | None = None
    object_id: str | None = None
    # Why this state does not accept the verb, in one line. None means it does.
    refusal: str | None = None
    # What running the verb here would finish or report, where what this state
    # holds is the record of that operation rather than the conditions for a
    # fresh one. Every operation in this package is redoable by being run again
    # with the same arguments, and a state carrying an interrupted one accepts
    # only that redo — so an allowed verb without this line is the ordinary form,
    # and one with it is the repair. None means the ordinary form.
    recovery: str | None = None

    @property
    def allowed(self) -> bool:
        return self.refusal is None

    def to_json(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "allowed": self.allowed,
            "object": None
            if self.object_type is None
            else {"type": self.object_type, "id": self.object_id},
            "reason": self.refusal,
            "recovers": self.recovery,
        }


@dataclass(frozen=True)
class PendingSuccessor:
    """A supersession whose decision landed and whose successor did not.

    Both ids, because neither on its own is the state: the attempt that ended is
    where the reason is recorded, and the one that does not exist is what the
    batch is owed.
    """

    experiment_id: str
    successor_id: str


@dataclass(frozen=True)
class LifecycleStatus:
    """The derived phase and every fact it was derived from."""

    phase: str
    decision: AdmissionDecision
    pool_complete: bool
    batch_count: int
    current_batch: BatchView | None
    gate: Gate | None
    experiment: Experiment | None
    ref: RefState | None
    history: tuple[Experiment, ...]
    revisions: LifecycleRevisions
    last_promotion: Promotion | None
    pending_successor: PendingSuccessor | None
    # What the open experiment's current round has been measured by. None when no
    # experiment is open: evidence is about a round, and a batch between attempts
    # has none — which is a different absence from a round nothing has replayed.
    evidence: Evidence | None = None
    # The open experiment's replay file itself. `evidence` is the derived reading
    # of the same records and this is what they still hold that no reading takes:
    # the request that is not a run yet, and the positions given up without
    # becoming one. Both name work that may be going at a harness, which is why
    # they are here rather than left to be met as a refusal.
    replays: History | None = None
    # Every frozen batch in order, the current one included.
    batches: tuple[BatchRecord, ...] = ()
    # The reading of the release before the current batch, from the cohort that
    # owes it. None when there is no release before it at all — which is most
    # batches, and is ordinary rather than missing.
    release: ReleaseReading | None = None
    # Every verb, and whether this state accepts it. Derived last, from the
    # fields above and in the order the operations check them.
    actions: tuple[Action, ...] = ()
    # What the answer above is about. A mutation handed this value can refuse
    # rather than act on a lifecycle that moved since it was read.
    state_revision: str = ""

    @property
    def open_round(self) -> Round | None:
        return self.experiment.open_round if self.experiment else None

    @property
    def prepared_promotion(self) -> PreparedPromotion | None:
        """The merge unit a promotion of the open experiment was prepared as.

        Durable in-flight state, and the narrowest one here: while it stands,
        `promote` is the only verb that experiment accepts, because the prepared
        merge may already be on the source line with only its records missing.

        Asked of the open experiment alone, which is what makes the absence mean
        one thing. A terminal promoted attempt records no prepared merge at
        experiment schema version 1, so reading this off history would report a
        build's age as a promotion nobody prepared; the merge unit of a promotion
        that happened is its batch's outcome (`BatchRecord.promotion`).
        """

        return self.experiment.promotion if self.experiment else None

    @property
    def replay_request(self) -> PendingRun | None:
        return self.replays.pending if self.replays else None

    @property
    def replay_withdrawn(self) -> tuple[WithdrawnRequest, ...]:
        return self.replays.withdrawn if self.replays else ()

    @property
    def allowed(self) -> tuple[Action, ...]:
        """The verbs this state accepts, in lifecycle order."""

        return tuple(action for action in self.actions if action.allowed)

    @property
    def implementation_tasks(self) -> tuple[str, ...]:
        """Admitted tasks of the open round with no completion observation.

        Read from the experiment record, which names its own tasks — never by
        scanning `.ai-tasks/` for a batch citation, which finds nothing on a
        fresh clone and less as close-out archives tasks away (contract: What is
        derived).
        """

        round_ = self.open_round
        return round_.unfinished if round_ else ()

    @property
    def summary(self) -> str:
        """The phase as one line — the form the scope names, `pool N/<target>`
        included."""

        if self.phase == PHASE_POOL:
            return f"{PHASE_POOL} {self.decision.task_count}/{self.decision.target}"
        if self.phase in (PHASE_IMPLEMENTING, PHASE_CANDIDATE_READY) and self.experiment is not None:
            round_ = self.experiment.last_round
            tail = _round_tail(round_) if self.phase == PHASE_IMPLEMENTING else ""
            return f"{self.phase} {self.experiment.experiment_id} round {round_.number}{tail}"
        if self.phase == PHASE_SUPERSEDE_PENDING and self.pending_successor is not None:
            return f"{self.phase} {self.pending_successor.successor_id}"
        if self.phase == PHASE_PROPOSALS_PENDING and self.gate is not None:
            drafts = len(self.gate.waiting)
            batch_id = self.current_batch.batch_id if self.current_batch else ""
            return f"{self.phase} {batch_id} ({drafts} draft{'s' if drafts != 1 else ''})"
        if self.current_batch is not None:
            return f"{self.phase} {self.current_batch.batch_id}"
        return self.phase

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "phase": self.phase,
            "summary": self.summary,
            "pool": {
                "task_count": self.decision.task_count,
                "target": self.decision.target,
                "minimum": self.decision.minimum,
                # False also means "never synced": completeness has to be shown,
                # not assumed (invariants 1 and 2).
                "complete": self.pool_complete,
                "oldest_pending_at": self.decision.oldest_pending_at,
                "waited_days": self.decision.waited_days,
                "max_wait_days": self.decision.max_wait_days,
            },
            "admission": {
                "freeze": self.decision.freeze,
                "trigger": self.decision.trigger,
                "reason": self.decision.reason,
                "current_batch": self.decision.current_batch_id,
            },
            "batches": {
                "total": self.batch_count,
                "current": self.current_batch.to_json() if self.current_batch else None,
                # Every batch, in freeze order — the series rather than the one
                # cohort in play, because which promotion a batch follows is a
                # position in it and not a fact any batch states about itself.
                "history": [item.to_json() for item in self.batches],
            },
            "gate": _gate_json(self.gate),
            "experiments": {
                "open": _experiment_json(self.experiment, self.ref),
                "history": [_terminal_json(experiment) for experiment in self.history],
                "pending_successor": None
                if self.pending_successor is None
                else {
                    "experiment_id": self.pending_successor.experiment_id,
                    "successor_id": self.pending_successor.successor_id,
                },
            },
            "implementation_tasks": list(self.implementation_tasks),
            "revisions": {
                "base": _revision_json(self.revisions.base),
                "candidate_tip": _revision_json(self.revisions.candidate_tip),
                "round_candidate": _revision_json(self.revisions.round_candidate),
            },
            "replay": _replay_json(self.evidence, self.replays),
            "release": _release_json(self.release),
            "last_promotion": _promotion_json(self.last_promotion),
            # What this reading is of. A mutation is given it back and refuses
            # when it no longer describes the repository.
            "state_revision": self.state_revision,
            # Every verb, refused ones included: what may be done next, and why
            # the rest may not.
            "allowed_actions": [action.to_json() for action in self.actions],
        }


def describe(config: EvolutionConfig, *, now: datetime | None = None) -> LifecycleStatus:
    """Derive the current phase. Read-only.

    Fails closed rather than guessing: an unreadable manifest, a state file that
    contradicts the batches beside it, a lineage that cannot be read as one
    history, a replay or release-assessment record its own reader refuses, or a
    file standing in for an analysis task all raise here exactly as they would
    during a freeze. A status that smoothed those over would report a lifecycle
    the next operation refuses to act on.

    The reading is bracketed by the token's own inputs rather than followed by
    them. Nothing here takes the single-writer lock — a status that waited on an
    operation would be a very different promise, and taking that lock is itself a
    write — so what stops this publishing a reading of one moment with a token
    from another is reading the inputs, then the lifecycle, then the inputs
    again. Equal, and no writer finished in between; different, and the reading
    is taken again.
    """

    moment = now or datetime.now(timezone.utc)
    inputs = _inputs(config)
    for _ in range(_SNAPSHOT_ATTEMPTS):
        reading, lineage = _read(config, moment)
        after = _inputs(config)
        if after == inputs:
            # The gate last, over the assembled reading: what may be done next is
            # a question about all of it at once, and deriving it from anything
            # narrower is how a surface comes to offer a verb some other field
            # already refuses.
            return replace(
                reading,
                actions=_actions(config, reading, lineage),
                state_revision=_state_revision(after),
            )
        inputs = after
    raise BatchError(
        f"the evolution records changed under this reading {_SNAPSHOT_ATTEMPTS} times over, so nothing here "
        "describes one moment of the repository; the token a mutation is given has to be of the state that was "
        "read, and a reading straddling a write is what it exists to refuse — run `status` again once the writer "
        "holding the single-writer lock has finished"
    )


def _read(config: EvolutionConfig, moment: datetime) -> tuple[LifecycleStatus, Lineage]:
    """The lifecycle as the artifacts now have it, without the gate or the token.

    Both of those are derived from this and from nothing else, which is why they
    are applied by the caller: the gate reads the assembled state, and the token
    is the bracket that says this state was one moment.

    The lineage travels with the reading because the gate asks the operations
    themselves what a redo would find here, and their predicates take the records
    rather than this projection of them.
    """

    state = load_state(config)
    known = load_batches(config)
    # One derivation, shared with every guarded operation: a status that applied
    # its own reading would describe a lifecycle they disagree with.
    lineage = describe_lineage(config, batches=known)
    current = lineage.current
    # The analysis stage of the current batch — asked only of that batch, since
    # a concluded one's stage is history rather than something being waited on.
    stage_open = current is not None and awaiting_analysis(config, current.batch)

    decision = evaluate_admission(
        config,
        state,
        now=moment,
        current_batch_id=current.batch_id if current else None,
    )

    reading = LifecycleStatus(
        phase=_phase(current=current, stage_open=stage_open, pool=decision.task_count),
        decision=decision,
        pool_complete=state.feed_exhausted,
        batch_count=len(known),
        current_batch=None if current is None else _view(config, current, analysis_complete=not stage_open),
        gate=current.gate if current else None,
        experiment=current.open_experiment if current else None,
        ref=current.ref if current else None,
        history=current.terminal_experiments if current else (),
        revisions=_revisions(current),
        last_promotion=_promotion(lineage.last_promoted),
        pending_successor=_pending_successor(current),
        evidence=_evidence(config, current),
        replays=_replays(config, current),
        batches=tuple(_record(item) for item in lineage.batches),
        release=_release(config, lineage, current),
    )
    return reading, lineage


def _round_tail(round_: Round) -> str:
    """What an open round is waiting for.

    A round whose every admitted task has been observed complete is still open —
    the seal is what pins its candidate, and nothing may be measured before that
    (invariant 16). Saying "0 tasks left" would report the work as the thing
    still outstanding when it is the seal.

    A round that has admitted nothing is the other way round, and is exactly what
    `revise` opens: the round exists so work can resume off the pinned candidate,
    and what it is waiting for is the admission that fills it. Counting its tasks
    would report it as ready for a seal that refuses one.
    """

    if not round_.tasks:
        return " (no tasks admitted)"
    left = len(round_.unfinished)
    if not left:
        return " (ready to seal)"
    return f" ({left} task{'s' if left != 1 else ''} left)"


def _phase(*, current: BatchLineage | None, stage_open: bool, pool: int) -> str:
    if current is None:
        return PHASE_POOL if pool else PHASE_IDLE
    if stage_open:
        return PHASE_DISPOSITIONS_READY if current.batch.findings_recorded else PHASE_BATCH_FROZEN
    if current.pending_successor is not None:
        return PHASE_SUPERSEDE_PENDING
    experiment = current.open_experiment
    if experiment is not None:
        return PHASE_IMPLEMENTING if experiment.open_round is not None else PHASE_CANDIDATE_READY
    if current.gate.waiting:
        return PHASE_PROPOSALS_PENDING
    return PHASE_CONCLUSION_PENDING


def _view(config: EvolutionConfig, current: BatchLineage, *, analysis_complete: bool) -> BatchView:
    batch = current.batch
    return BatchView(
        batch_id=batch.batch_id,
        task_count=batch.task_count,
        report_count=len(batch.reports),
        analysis_task_id=batch.analysis_task_id,
        findings_recorded=batch.findings_recorded,
        analysis_complete=analysis_complete,
        evidence_local=sum(
            1 for key in batch.report_keys if (config.artifacts_root / artifacts_dir_name(key)).is_dir()
        ),
        experiment_count=len(current.experiments),
    )


def _revisions(current: BatchLineage | None) -> LifecycleRevisions:
    """The base, the tip, and the pinned candidate — three different commits.

    The tip is what the ref actually holds and is absent on a clone that never
    fetched the namespace; the round candidate is what the record pinned, which
    is the only one replay evidence may name (invariant 16). They are equal only
    while the last round is candidate-ready and nobody has committed since.

    Both are also absent for a reason of a different kind: a batch between
    attempts has no open experiment, so no round left a candidate unsealed and
    no ref was inspected at all. That reason is not encoded here — a reader
    tells the two apart by `experiments.open`, which is null exactly then, and
    must consult it before explaining either absence.
    """

    if current is None:
        return LifecycleRevisions()
    base = None
    if current.base_revision is not None:
        base = Revision(sha=current.base_revision, ref=current.base_release_ref)
    tip = None
    if current.ref is not None and current.ref.tip is not None:
        tip = Revision(sha=current.ref.tip, ref=current.ref.ref)
    candidate = None
    if current.candidate_revision is not None:
        candidate = Revision(sha=current.candidate_revision)
    return LifecycleRevisions(base=base, candidate_tip=tip, round_candidate=candidate)


def _evidence(config: EvolutionConfig, current: BatchLineage | None) -> Evidence | None:
    """What the open experiment's current round has been measured by.

    Derived here rather than stored, like everything else in this status, and
    from the same experiment record the phase was chosen from — so `status` and
    the promotion gate that will refuse on it are reading one answer. It is asked
    of the open experiment only: a terminal attempt's runs are history, and a
    batch between attempts has no round for evidence to be about.
    """

    if current is None or current.open_experiment is None:
        return None
    return describe_evidence(config, current.open_experiment)


def _replays(config: EvolutionConfig, current: BatchLineage | None) -> History | None:
    """The open experiment's replay records, read whole.

    A second read of the file `_evidence` derives from, and deliberately through
    the same parser: the request and the withdrawals are records rather than
    readings, and `describe_evidence` returns only the reading. Both would refuse
    the same malformed file, so the two cannot disagree about what is on disk.
    """

    if current is None or current.open_experiment is None:
        return None
    return read_replays(config, current.open_experiment)


def _record(item: BatchLineage) -> BatchRecord:
    """One batch as a history entry, from its own manifest and outcome."""

    outcome = item.outcome or {}
    return BatchRecord(
        batch_id=item.batch_id,
        task_count=item.batch.task_count,
        report_count=len(item.batch.reports),
        experiment_count=len(item.experiments),
        current=item.current,
        outcome=outcome.get("outcome"),
        decided_at=outcome.get("decided_at"),
        reason=outcome.get("reason"),
        promotion=_promotion(item),
    )


def _release(
    config: EvolutionConfig,
    lineage: Lineage,
    current: BatchLineage | None,
) -> ReleaseReading | None:
    """The reading of the release before the current batch, and whose it is.

    Asked through `obligation` rather than of the current batch's own frame,
    because those are two questions: a cohort that ends without answering leaves
    the obligation where it was, and the next base freeze waits on the owner's
    record (invariant 17). Asking the freezing batch alone would find nothing
    owed and offer an admission that refuses.

    None for a batch with no release before it at all, which is most of them:
    only a promotion produces something to assess, and `no-change` produces
    nothing (invariant 7).
    """

    if current is None:
        return None
    owed = describe_obligation(config, current.batch, lineage=lineage)
    return None if owed is None else ReleaseReading(batch_id=current.batch_id, obligation=owed)


def _pending_successor(current: BatchLineage | None) -> PendingSuccessor | None:
    """The experiment a recorded supersession still owes, if there is one.

    Reported rather than raised for the same reason the lineage reads it that
    way: the operation that finishes the supersession has to be able to run, and
    an operator has to be able to see why nothing else will.
    """

    if current is None or current.pending_successor is None:
        return None
    return PendingSuccessor(
        experiment_id=current.experiments[-1].experiment_id,
        successor_id=current.pending_successor,
    )


def _promotion(promoted: BatchLineage | None) -> Promotion | None:
    """What a batch put on the source line, and whether it is still there.

    One function for both readings a console takes of a promotion — the latest
    one, which is what the next cohort's reports were produced at, and each
    batch's own in the history list. Two derivations of "what this batch
    promoted" would be two answers to what the line carries.

    `lineage.last_promoted` decides which is the latest, shared with the
    operation that reverses one for the same reason.
    """

    if promoted is None:
        return None
    outcome = promoted.outcome or {}
    if outcome.get("outcome") != OUTCOME_PROMOTED:
        return None
    # Present for every `promoted` outcome — `read_outcome` refuses one that
    # states the revision without the merge unit it went as.
    merge = outcome["promotion"]
    return Promotion(
        batch_id=promoted.batch_id,
        experiment_id=outcome["experiment_id"],
        revision=outcome["promotion_revision"],
        round_number=merge["round"],
        candidate_revision=merge["candidate_revision"],
        merge_input_revision=merge["merge_input_revision"],
        merge_input_ref=merge["merge_input_ref"],
        tree=merge["tree"],
        planned_targets=tuple(merge["planned_targets"]),
        rollback=_rollback(promoted),
    )


def _rollback(promoted: BatchLineage) -> Rollback | None:
    """The record taking that promotion back off the line, in flight or finished.

    Both states are reported, and `reverted_at` is what tells them apart: an
    inverse commit that exists while the line has not been recorded as carrying
    it is durable in-flight state, and an operator meeting it only as a refusal
    from the next attempt would have nothing to read it from.
    """

    record = promoted.rollback
    if record is None:
        return None
    return Rollback(
        revision=record["revision"],
        reverted_from=record["reverted_from"],
        reverted_at=record["reverted_at"],
        reason=record["reason"],
        # The lineage's own answer, taken while it held the record to it. Asking
        # Git again here would be a second answer to one question, and the
        # expensive half of it.
        unconfirmed=promoted.rollback_unconfirmed,
    )


# --- the gate ----------------------------------------------------------------


@dataclass(frozen=True)
class _Held:
    """The refusals more than one verb shares, settled once.

    The order is the preamble every guarded operation runs before it writes
    (`guards.current_cycle`), and asking it in that order is what makes the gate's
    answer the operation's: a verb refused by the batch it would act on is
    refused for that reason rather than for whatever the experiment underneath it
    happens to be doing.
    """

    # No batch is current, or the current one is still in its analysis stage.
    stage: str | None
    # A supersession recorded its decision and not the successor it named.
    successor: str | None
    # The open experiment's ref stands off the history its record pins.
    ref: str | None
    # A promotion of the open experiment is prepared and not finished.
    prepared: str | None

    @property
    def lineage(self) -> str | None:
        """The whole preamble, for the verbs that run all of it."""

        return self.stage or self.successor


def _actions(config: EvolutionConfig, status: LifecycleStatus, lineage: Lineage) -> tuple[Action, ...]:
    """Every verb, and what this state does with it.

    Emitted in lifecycle order — the pool, the gate, the work, its measurement,
    the decisions, the reversal, the release — so a reader scanning for what to
    do next reads them in the order they happen.
    """

    held = _Held(
        stage=_stage_refusal(status),
        successor=_successor_refusal(status),
        ref=_ref_refusal(status),
        prepared=_prepared_refusal(status),
    )
    return (
        _start_action(status),
        *_gate_actions(status, held),
        *_work_actions(config, status, held),
        *_replay_actions(status, held),
        *_decision_actions(status, held, lineage),
        _rollback_action(status, lineage),
        *_release_actions(status),
    )


def _on(object_type: str, object_id: str | None) -> tuple[str | None, str | None]:
    """The object a verb acts on, or nothing where it does not exist here."""

    return (object_type, object_id) if object_id is not None else (None, None)


def _stage_refusal(status: LifecycleStatus) -> str | None:
    current = status.current_batch
    if current is None:
        return (
            "no batch is current; a cohort is frozen by `start`, and every change lineage belongs to one "
            "(invariant 14)"
        )
    if not current.analysis_complete:
        return (
            f"{current.batch_id} is still in its analysis stage; its dispositions reach the admission gate when "
            "that task completes and the closure record says so (invariant 6)"
        )
    return None


def _successor_refusal(status: LifecycleStatus) -> str | None:
    pending = status.pending_successor
    if pending is None:
        return None
    return (
        f"{pending.experiment_id} was superseded by {pending.successor_id}, which was never created; the batch has "
        "nothing to work in until that supersession is redone for the reason on record"
    )


def _ref_refusal(status: LifecycleStatus) -> str | None:
    """A ref standing off the history its record pins stops every write into it.

    "Cannot tell" is not one: a clone that never fetched `refs/evolution/*` has no
    answer, which says nothing about the lineage (`guards.require_consistent_ref`).
    """

    ref = status.ref
    if ref is None or ref.consistent is not False:
        return None
    if ref.chain_break is not None:
        earlier, later = ref.chain_break
        return (
            f"{ref.ref}: {later[:12]} does not descend from {earlier[:12]}, which the record pins before it; work "
            "admitted onto that history leaves the revisions the record names unreachable"
        )
    return (
        f"{ref.ref} stands at {(ref.tip or 'nothing')[:12]}, not on the history of the {ref.pinned[:12]} its record "
        f"pins ({ref.state}); the ref only fast-forwards, and work admitted now would be measured as part of a "
        "candidate nobody can identify"
    )


def _prepared_refusal(status: LifecycleStatus) -> str | None:
    prepared = status.prepared_promotion
    experiment = status.experiment
    if prepared is None or experiment is None:
        return None
    return (
        f"{experiment.experiment_id} has a promotion of {prepared.revision[:12]} prepared onto "
        f"{prepared.merge_input_ref}; the line may already be carrying it, so `promote` finishes that promotion or "
        "discards it, and this is available afterwards"
    )


def _start_action(status: LifecycleStatus) -> Action:
    """Freezing the next cohort.

    Refused for one reason, and a short pool is not it: `start` is a discovery
    pass and then an admission, so running it below the target imports whatever
    has arrived and reports no batch — a normal outcome rather than a refusal.
    What it cannot do is form a cohort beside one whose cycle is still running.
    """

    decision = status.decision
    if decision.reason != REASON_CURRENT_BATCH:
        return Action(ACTION_START, OBJECT_POOL)
    return Action(
        ACTION_START,
        OBJECT_POOL,
        refusal=(
            f"{decision.current_batch_id} is current and no outcome has been recorded for it; the pool keeps "
            "accumulating and the next cohort forms once that batch concludes (invariant 14)"
        ),
    )


def _gate_actions(status: LifecycleStatus, held: _Held) -> tuple[Action, ...]:
    """The two terminal answers a draft at the admission gate can be given."""

    batch_id = status.current_batch.batch_id if status.current_batch else None
    return (
        _create_action(status, held, batch_id),
        _reject_action(status, held, batch_id),
    )


def _reject_action(status: LifecycleStatus, held: _Held, batch_id: str | None) -> Action:
    """Declining a draft, or the decision one already recorded reports back.

    Which drafts are already declined is the lineage's own reading of the gate:
    the record of a rejection is what makes it real, and the same selection redone
    for the reason on record reports it rather than deciding twice.
    """

    declined = status.gate.declined if status.gate is not None else ()
    if not held.lineage and declined and not _waiting(status):
        return Action(
            ACTION_REJECT,
            *_on(OBJECT_BATCH, batch_id),
            recovery=(
                f"draft(s) {list(declined)} are already declined at this gate; that selection redone for the "
                "reason on record reports the decision and writes nothing"
            ),
        )
    return Action(ACTION_REJECT, *_on(OBJECT_BATCH, batch_id), refusal=_reject_refusal(status, held))


def _create_action(status: LifecycleStatus, held: _Held, batch_id: str | None) -> Action:
    """A grouped admission, or the copies one already recorded still owes.

    The redo acts on the open experiment rather than on the batch: what it
    finishes is that attempt's task copies, and the selection it is given is the
    one already on its round.
    """

    experiment = status.experiment
    if not held.lineage and not held.ref and experiment is not None:
        round_ = experiments.redone_admission(experiment)
        if round_ is not None:
            return Action(
                ACTION_CREATE,
                OBJECT_EXPERIMENT,
                experiment.experiment_id,
                recovery=(
                    f"{experiment.experiment_id} is the attempt round {round_.number} was admitted into; the same "
                    f"selection ({list(task.draft_id for task in round_.tasks)}) redone writes whatever task copies "
                    "that admission still owes and records nothing twice"
                ),
            )
    return Action(ACTION_CREATE, *_on(OBJECT_BATCH, batch_id), refusal=_create_refusal(status, held))


def _create_refusal(status: LifecycleStatus, held: _Held) -> str | None:
    if held.lineage:
        return held.lineage
    experiment = status.experiment
    if experiment is not None:
        return held.ref or (
            f"{experiment.experiment_id} is open, and a batch runs one attempt at a time (invariant 14); "
            "`add-tasks` admits further drafts into its open round, and redoing the same selection is what finishes "
            "an interrupted admission"
        )
    if not _waiting(status):
        return (
            "no draft is waiting at the admission gate; what an experiment admits is a proposal this batch's own "
            "analysis made and nobody has decided yet"
        )
    return _base_refusal(status)


def _base_refusal(status: LifecycleStatus) -> str | None:
    """Invariant 17, asked only where a base is about to be frozen.

    A batch whose first experiment already froze one takes that commit rather
    than resolving one, so the release before it is not asked about again: the
    decision can no longer change what this batch starts from.
    """

    if status.revisions.base is not None:
        return None
    release = status.release
    if release is None or release.settled:
        return None
    owner = "this batch" if release.owned_here else release.owner_id
    subject = release.frame.subject
    reading = release.reading
    if reading is None:
        return (
            f"nothing has read the {subject.batch_id} release ({subject.revision[:12]}) that {owner} owes, and the "
            "base every alternative here starts from is the line that reading is settled onto (invariant 17)"
        )
    return (
        f"{owner} reads the {subject.batch_id} release {reading.verdict!r} and nobody has settled it; the "
        "settlement is what says whether the base carries the release or the reversal of it (invariant 17)"
    )


def _reject_refusal(status: LifecycleStatus, held: _Held) -> str | None:
    if held.lineage:
        return held.lineage
    if not _waiting(status):
        return (
            "no draft is waiting at the admission gate; declining is terminal, and a draft already decided is not "
            "decided again"
        )
    return None


def _work_actions(config: EvolutionConfig, status: LifecycleStatus, held: _Held) -> tuple[Action, ...]:
    """The three that move an attempt: admit into its round, pin it, open the next."""

    experiment = status.experiment
    object_id = experiment.experiment_id if experiment else None
    # Every one of the three refuses on the same two holds before it looks at the
    # round, so a redo is only on offer once those are clear — the order the
    # operations themselves check them in.
    open_here = None if held.lineage or held.ref else experiment
    return (
        _add_tasks_action(status, held, open_here, object_id),
        _seal_action(config, status, held, open_here, object_id),
        _revise_action(status, held, open_here, object_id),
    )


def _add_tasks_action(
    status: LifecycleStatus, held: _Held, open_here: Experiment | None, object_id: str | None
) -> Action:
    """Admitting further drafts, or the copies an admission already recorded owes."""

    round_ = experiments.redone_addition(open_here) if open_here is not None else None
    if round_ is not None and not _waiting(status) and open_here is not None:
        return Action(
            ACTION_ADD_TASKS,
            OBJECT_EXPERIMENT,
            object_id,
            recovery=(
                f"round {round_.number} of {open_here.experiment_id} has admitted "
                f"{list(task.draft_id for task in round_.tasks)}; that selection redone writes whatever task "
                "copies the admission still owes and records nothing twice"
            ),
        )
    return Action(ACTION_ADD_TASKS, *_on(OBJECT_EXPERIMENT, object_id), refusal=_add_tasks_refusal(status, held))


def _seal_action(
    config: EvolutionConfig,
    status: LifecycleStatus,
    held: _Held,
    open_here: Experiment | None,
    object_id: str | None,
) -> Action:
    """Pinning the round's candidate, or reporting the pin already on record."""

    pinned = experiments.redone_seal(open_here) if open_here is not None else None
    if pinned is not None and pinned.seal is not None and open_here is not None:
        return Action(
            ACTION_SEAL_ROUND,
            OBJECT_EXPERIMENT,
            object_id,
            recovery=(
                f"round {pinned.number} of {open_here.experiment_id} is already sealed at "
                f"{pinned.seal.candidate_revision[:12]}; run again this reports that pin and writes nothing"
            ),
        )
    return Action(
        ACTION_SEAL_ROUND, *_on(OBJECT_EXPERIMENT, object_id), refusal=_seal_refusal(config, status, held)
    )


def _revise_action(
    status: LifecycleStatus, held: _Held, open_here: Experiment | None, object_id: str | None
) -> Action:
    """Opening the next round, or reporting the empty one a revision already opened.

    A prepared promotion holds this off as it holds off the fresh form: the
    operation checks it before it recognises its own work, because an experiment
    with a merge in flight is not moved on from at all.
    """

    opened = experiments.redone_revision(open_here) if open_here is not None and not held.prepared else None
    if opened is not None and open_here is not None:
        return Action(
            ACTION_REVISE,
            OBJECT_EXPERIMENT,
            object_id,
            recovery=(
                f"round {opened.number} of {open_here.experiment_id} was opened for {opened.reason!r} and has "
                "admitted nothing; that revision redone for the same reason reports the round and writes nothing"
            ),
        )
    return Action(ACTION_REVISE, *_on(OBJECT_EXPERIMENT, object_id), refusal=_revise_refusal(status, held))


def _attempt_refusal(status: LifecycleStatus, held: _Held) -> str | None:
    """The open experiment these verbs act on, or why there is none.

    None is the answer only where there is one, which is what lets every caller
    below read `status.experiment` after it.
    """

    if held.lineage:
        return held.lineage
    if status.experiment is None:
        batch_id = status.current_batch.batch_id if status.current_batch else ""
        return (
            f"{batch_id} has no open experiment; a terminal decision is never reopened, and what continues a batch "
            "is the next attempt — a grouped admission of the drafts it needs"
        )
    return held.ref


def _add_tasks_refusal(status: LifecycleStatus, held: _Held) -> str | None:
    hold = _attempt_refusal(status, held)
    experiment = status.experiment
    if hold is not None or experiment is None:
        return hold
    if experiment.open_round is None:
        return (
            f"round {experiment.last_round.number} of {experiment.experiment_id} is candidate-ready; its candidate "
            "is pinned and its evidence names it, so `revise` opens the round further work is admitted into "
            "(invariant 16)"
        )
    if not _waiting(status):
        return (
            "no draft is waiting at the admission gate; a round admits proposals this batch's analysis made, and "
            "one already admitted or declined is decided"
        )
    return None


def _seal_refusal(config: EvolutionConfig, status: LifecycleStatus, held: _Held) -> str | None:
    hold = _attempt_refusal(status, held)
    experiment = status.experiment
    if hold is not None or experiment is None:
        return hold
    round_ = experiment.open_round
    if round_ is None:
        pinned = (experiment.last_round.candidate_revision or "")[:12]
        return f"round {experiment.last_round.number} of {experiment.experiment_id} is already sealed at {pinned}"
    if not round_.tasks:
        return (
            f"round {round_.number} of {experiment.experiment_id} has admitted nothing; a seal pins the revision a "
            "task set produced, and an empty round accounts for none of it"
        )
    outstanding = _unobserved(config, round_)
    if outstanding:
        return (
            f"round {round_.number} of {experiment.experiment_id} is not ready to seal: {list(outstanding)}; a "
            "candidate that does not contain the change it was admitted for is not what anyone means to measure, "
            "and `.ai-tasks/` is machine-local — a task nobody here holds is observed where it was worked"
        )
    return None


def _unobserved(config: EvolutionConfig, round_: Round) -> tuple[str, ...]:
    """Admitted tasks this machine cannot see finished.

    The same question the seal asks (`experiments._observe_completions`) and one
    check short of it: whether the file standing at that id is the copy the
    admission published belongs to the seal, under the lock, at the moment it
    records the observation. A task the record already shows complete is not
    asked about at all — the observation is durable and the file is not.
    """

    return tuple(task_id for task_id in round_.unfinished if not task_finished(config, task_id))


def _revise_refusal(status: LifecycleStatus, held: _Held) -> str | None:
    hold = _attempt_refusal(status, held)
    experiment = status.experiment
    if hold is not None or experiment is None:
        return hold
    if held.prepared:
        return held.prepared
    open_round = experiment.open_round
    if open_round is not None:
        return (
            f"round {open_round.number} of {experiment.experiment_id} is still open; a revision appends to a round "
            "whose candidate is already pinned, so seal this one first (invariant 16)"
        )
    return None


def _replay_actions(status: LifecycleStatus, held: _Held) -> tuple[Action, ...]:
    """Measuring the pinned candidate, and answering for a run that was started."""

    experiment = status.experiment
    object_id = experiment.experiment_id if experiment else None
    history = status.replays if not held.lineage and experiment is not None else None
    return (
        Action(ACTION_REPLAY_START, *_on(OBJECT_EXPERIMENT, object_id), refusal=_replay_start_refusal(status, held)),
        _run_action(
            status,
            held,
            ACTION_REPLAY_CONCLUDE,
            "concluded",
            object_id,
            replay_ops.redone_conclusion(history) if history is not None else None,
            "reports the result on record rather than polling for a second one",
        ),
        _run_action(
            status,
            held,
            ACTION_REPLAY_ABANDON,
            "ended",
            object_id,
            replay_ops.redone_end(history) if history is not None else None,
            "run again for the reason on record, this reports that failure and writes nothing",
        ),
        Action(ACTION_REPLAY_WITHDRAW, *_on(OBJECT_EXPERIMENT, object_id), refusal=_withdraw_refusal(status, held)),
    )


def _run_action(
    status: LifecycleStatus,
    held: _Held,
    action: str,
    ended: str,
    object_id: str | None,
    recovered: Replay | None,
    note: str,
) -> Action:
    """One of the two verbs that answer for a run, in both its forms.

    A run whose result is on record is what an interrupted answer left, and both
    verbs report it rather than writing twice — which is why a round already
    measured accepts them instead of refusing the way a fresh answer would.
    """

    if recovered is not None:
        outcome = recovered.result.outcome if recovered.result is not None else ""
        return Action(
            action,
            OBJECT_EXPERIMENT,
            object_id,
            recovery=(
                f"round {recovered.round_number} attempt {recovered.attempt} already ended {outcome!r}; {note}"
            ),
        )
    return Action(action, *_on(OBJECT_EXPERIMENT, object_id), refusal=_run_refusal(status, held, ended))


def _replay_start_refusal(status: LifecycleStatus, held: _Held) -> str | None:
    """A start, or the resume of a request the harness never answered for."""

    hold = _attempt_refusal(status, held)
    experiment = status.experiment
    if hold is not None or experiment is None:
        return hold
    open_round = experiment.open_round
    if open_round is not None:
        return (
            f"round {open_round.number} of {experiment.experiment_id} is still open; a round is measured once its "
            "candidate is pinned, because an open round's tip moves (invariant 16)"
        )
    going = _running_replay(status)
    if going is not None:
        return (
            f"round {going.round_number} attempt {going.attempt} is still running under {going.harness.id}; a round "
            "is measured against one integration at a time — conclude that run first"
        )
    return None


def _run_refusal(status: LifecycleStatus, held: _Held, action: str) -> str | None:
    """Concluding a run and ending one ask the same three questions."""

    hold = _attempt_refusal(status, held)
    experiment = status.experiment
    if hold is not None or experiment is None:
        return hold
    request = status.replay_request
    if request is not None:
        return (
            f"round {request.round_number} attempt {request.attempt} is outstanding and no run is recorded for it, "
            f"so there is nothing here to be {action}; start the replay again to record what the harness began, or "
            "withdraw the request"
        )
    runs = status.replays.replays if status.replays else ()
    if not runs:
        return (
            f"{experiment.experiment_id} has no recorded run to be {action}; a harness invocation nothing here "
            "named is not one this controller can speak for"
        )
    newest = runs[-1]
    if not newest.running:
        outcome = newest.result.outcome if newest.result is not None else ""
        return (
            f"round {newest.round_number} attempt {newest.attempt} already ended {outcome!r}; a run ends once, and "
            "what measures the round again is another attempt"
        )
    return None


def _withdraw_refusal(status: LifecycleStatus, held: _Held) -> str | None:
    hold = _attempt_refusal(status, held)
    if hold:
        return hold
    if status.replay_request is None:
        return (
            "no replay request is outstanding; a withdrawal takes back a request the harness never answered for, "
            "and a run that happened is ended with a reason instead"
        )
    return None


def _running_replay(status: LifecycleStatus) -> Replay | None:
    """The newest recorded run of the open round, while it is still going."""

    runs = status.replays.replays if status.replays else ()
    newest = runs[-1] if runs else None
    return newest if newest is not None and newest.running else None


def _decision_actions(status: LifecycleStatus, held: _Held, lineage: Lineage) -> tuple[Action, ...]:
    """The three ways an attempt ends, and the way a batch ends without one."""

    experiment = status.experiment
    object_id = experiment.experiment_id if experiment else None
    pending = status.pending_successor
    unfinished = _unfinished_promotion(status)
    batch_id = status.current_batch.batch_id if status.current_batch else None
    current = lineage.current
    return (
        Action(
            ACTION_PROMOTE,
            *_on(OBJECT_EXPERIMENT, unfinished.experiment_id if unfinished is not None else object_id),
            refusal=_promote_refusal(status, held),
        ),
        # The hold each of them runs differs by one question: a supersession is
        # the operation that may act on a batch owing a successor, and it is the
        # only one (`guards.current_cycle(finishing=...)`).
        _ending_action(status, held, held.lineage, ACTION_ABANDON, DECISION_ABANDONED, current, object_id),
        # A supersession that recorded its decision and not the successor it
        # named is redone against the attempt that ended, which is the id the
        # decision sits on — the value this object exists to hand over.
        _ending_action(
            status,
            held,
            held.stage,
            ACTION_SUPERSEDE,
            DECISION_SUPERSEDED,
            current,
            pending.experiment_id if pending is not None else object_id,
        ),
        _conclude_action(status, held, lineage, batch_id),
    )


def _ending_action(
    status: LifecycleStatus,
    held: _Held,
    hold: str | None,
    action: str,
    outcome: str,
    current: BatchLineage | None,
    object_id: str | None,
) -> Action:
    """A terminal decision, or the one this batch already recorded, reported back.

    The object is the attempt the redo acts on — history rather than anything
    open, since a decision redone is about the attempt that ended. That is also
    the id the operation takes as `experiment_id`, which is what distinguishes an
    interrupted supersession redone from an untouched successor superseded in
    its turn.
    """

    ended = experiments.redone_decision(current, outcome) if current is not None and not hold else None
    if ended is not None and ended.decision is not None:
        return Action(
            action,
            OBJECT_EXPERIMENT,
            ended.experiment_id,
            recovery=(
                f"{ended.experiment_id} already ended {outcome!r} at {ended.decision.decided_at}; that decision "
                "redone for the reason on record finishes whatever its run left and writes no second one"
            ),
        )
    refusal = _abandon_refusal(status, held) if action == ACTION_ABANDON else _supersede_refusal(status, held)
    return Action(action, *_on(OBJECT_EXPERIMENT, object_id), refusal=refusal)


def _conclude_action(
    status: LifecycleStatus, held: _Held, lineage: Lineage, batch_id: str | None
) -> Action:
    """Ending a batch having changed nothing, or reporting the record that did.

    Read from the whole lineage because the redo's own state has no current
    batch: the outcome record is what ends one, and a run interrupted between it
    and its audit line left nothing for a `current` reading to find.
    """

    if status.current_batch is None:
        concluded = experiments.redone_conclusion(lineage)
        if concluded is not None:
            outcome = concluded.outcome or {}
            return Action(
                ACTION_CONCLUDE_NO_CHANGE,
                OBJECT_BATCH,
                concluded.batch_id,
                recovery=(
                    f"{concluded.batch_id} already concluded {outcome.get('outcome')!r} at "
                    f"{outcome.get('decided_at')}; that conclusion redone for the reason on record reports it and "
                    "writes nothing"
                ),
            )
    return Action(ACTION_CONCLUDE_NO_CHANGE, *_on(OBJECT_BATCH, batch_id), refusal=_conclude_refusal(status, held))


def _unfinished_promotion(status: LifecycleStatus) -> Experiment | None:
    """A promotion whose decision landed and whose batch outcome did not.

    The batch is still current, its newest attempt is terminal, and that decision
    is a promotion — which is a state nothing else may be written over and this
    verb's own redo (`experiments._finish_promotion`). It reaches the phase label
    as `proposals-pending` or `conclusion-pending`, neither of which says so, and
    the gate is where it becomes visible.
    """

    if status.current_batch is None or status.experiment is not None:
        return None
    newest = status.history[-1] if status.history else None
    if newest is None or newest.decision is None or newest.decision.outcome != DECISION_PROMOTED:
        return None
    return newest


def _promote_refusal(status: LifecycleStatus, held: _Held) -> str | None:
    unfinished = _unfinished_promotion(status)
    if unfinished is not None:
        if held.lineage:
            return held.lineage
        if unfinished.promotion is None:
            return (
                f"{unfinished.experiment_id} was promoted by a build that recorded no merge unit on the experiment, "
                "and the outcome that ends this batch states the merge unit and the targets it was planned for; "
                "the plan was never written down, so this batch cannot be concluded from what is on record"
            )
        return None
    hold = _attempt_refusal(status, held)
    if hold:
        return hold
    waiting = _waiting(status)
    if waiting:
        return (
            f"{status.current_batch.batch_id if status.current_batch else ''} still has draft(s) {list(waiting)} "
            "at its admission gate, and a promotion ends the batch and the gate with it — admit them or decline them"
        )
    if held.prepared:
        # The one state a prepared promotion does not refuse: this verb is what
        # finishes it, or discards it once the line proves it never arrived.
        return None
    request = status.replay_request
    if request is not None:
        return (
            f"round {request.round_number} attempt {request.attempt} is outstanding, so a run may be going that no "
            "record names; a promotion ends the experiment and with it every operation that could answer for that "
            "run — start the replay again, or withdraw the request"
        )
    going = _running_replay(status)
    if going is not None:
        return (
            f"round {going.round_number} attempt {going.attempt} is still running under {going.harness.id}; a "
            "promotion ends the experiment, and nothing could afterwards record how that run finished"
        )
    evidence = status.evidence
    if evidence is not None and evidence.promotable:
        return None
    return _unpromotable(evidence)


def _unpromotable(evidence: Evidence | None) -> str:
    """Why the round's evidence does not describe the tree a promotion carries.

    In the reader's own words, and both shortfalls: `drift` is what this checkout
    established and `unverified` is what it could not answer, and a promotion is
    refused on either (contract: what is derived).
    """

    if evidence is None:
        return "no round has been replayed, and a promotion carries the tree a completed run measured"
    notes = [*evidence.drift, *(f"unverified here: {note}" for note in evidence.unverified)]
    tail = f" — {'; '.join(notes)}" if notes else ""
    return f"the round's replay evidence is {evidence.state}{tail}"


def _abandon_refusal(status: LifecycleStatus, held: _Held) -> str | None:
    hold = _attempt_refusal(status, held)
    if hold:
        return hold
    return held.prepared


def _supersede_refusal(status: LifecycleStatus, held: _Held) -> str | None:
    """Also the redo of an interrupted supersession, which is the one operation
    that may act on a batch owing the attempt one named."""

    if status.pending_successor is not None:
        return held.stage
    return _abandon_refusal(status, held)


def _conclude_refusal(status: LifecycleStatus, held: _Held) -> str | None:
    if held.lineage:
        return held.lineage
    unfinished = _unfinished_promotion(status)
    if unfinished is not None:
        return (
            f"{unfinished.experiment_id} records a promotion, so this batch concluded by promoting it; the outcome "
            "names which attempt it was and the revision that carries it — `promote` writes it"
        )
    experiment = status.experiment
    if experiment is not None:
        return (
            f"{experiment.experiment_id} is still open; a batch's outcome is recorded after its last attempt ends, "
            "so abandon or supersede that one first — an abandoned attempt stays in the record as the evidence it is"
        )
    waiting = _waiting(status)
    if waiting:
        return (
            f"{status.current_batch.batch_id if status.current_batch else ''} still has draft(s) {list(waiting)} "
            "at its admission gate; 'the evidence justified no change' is a claim this batch's own analysis does "
            "not support — admit them or decline them, both of which are terminal"
        )
    return None


def _rollback_action(status: LifecycleStatus, lineage: Lineage) -> Action:
    """Taking the newest promotion back off the line it was put on.

    Not gated on the analysis stage or on anything about the current batch: what
    it acts on is a promotion an earlier cohort recorded, and the batch running
    now is where the reading of that promotion lives rather than what it reverses.
    """

    promotion = status.last_promotion
    if promotion is None:
        return Action(
            ACTION_ROLLBACK,
            refusal=(
                "no batch has promoted anything, so there is nothing on the source line to take back; a rollback "
                "reverses the promotion this repository most recently recorded"
            ),
        )
    promoted = lineage.last_promoted
    record = promotion.rollback
    reversed_already = (
        rollback_ops.redone_reversal(promoted) if promoted is not None else None
    )
    if reversed_already is not None and record is not None:
        # The promotion is off the line and the record says so: this verb is how
        # a run interrupted between the two writes is finished, and run again for
        # the reason on record it reports the reversal rather than making a
        # second one.
        return Action(
            ACTION_ROLLBACK,
            OBJECT_PROMOTION,
            promotion.revision,
            recovery=(
                f"{promotion.revision[:12]} was already taken off {promotion.merge_input_ref} by "
                f"{record.revision[:12]} at {record.reverted_at}; run again for the reason on record this reports "
                "that rollback and writes nothing"
            ),
        )
    refusal = None
    if record is not None and record.unconfirmed is not None:
        refusal = (
            f"the inverse commit {record.revision[:12]} on record cannot be confirmed here — {record.unconfirmed}; "
            f"that record is what moves {promotion.merge_input_ref} and what says the promotion came off it, so it "
            "is acted on from a checkout holding the commits it names"
        )
    return Action(ACTION_ROLLBACK, OBJECT_PROMOTION, promotion.revision, refusal)


_RELEASE_ACTIONS = (
    ACTION_ASSESS,
    ACTION_ASSESS_MEASURE,
    ACTION_ASSESS_CONCLUDE,
    ACTION_ASSESS_ABANDON,
    ACTION_ASSESS_WITHDRAW,
    ACTION_ASSESS_RESOLVE,
    ACTION_SETTLE,
)


def _release_actions(status: LifecycleStatus) -> tuple[Action, ...]:
    """The seven verbs of the release reading, which the analysis stage does not
    gate.

    Deliberately outside the preamble every other verb here runs. The reading is
    the generated analysis task's own second question, taken while the
    dispositions are still being written — so gating these on that stage having
    ended would refuse them exactly when they are meant to be used
    (`assessment._owing_frame`).
    """

    release = status.release
    if release is None:
        absent = _no_release(status)
        return tuple(Action(action, refusal=absent) for action in _RELEASE_ACTIONS)
    refusals = _reading_refusals(release)
    recoveries = _reading_recoveries(release)
    return tuple(
        Action(
            action,
            OBJECT_RELEASE,
            release.owner_id,
            refusal=None if action in recoveries else refusals[action],
            recovery=recoveries.get(action),
        )
        for action in _RELEASE_ACTIONS
    )


def _reading_recoveries(release: ReleaseReading) -> dict[str, str]:
    """The release verbs this reading is the recorded work of.

    Every one of them is redoable the way the rest of the lifecycle is, and two
    of the states here are ones an operator can only reach by redoing: a
    settlement whose rollback landed and whose decision record did not, and a
    formation interrupted after its record. The other verbs close when the gate
    answers, so nothing is offered past a decision but the settlement itself and
    the reading it was made from.
    """

    reading = release.reading
    if reading is None:
        return {}
    subject = release.frame.subject
    decision = reading.decision
    found: dict[str, str] = {}
    # A reading this cohort holds is one its own formation can report back, and
    # so is an earlier cohort's while nobody has answered for it — the formation
    # follows an outstanding obligation to where the record is. What it does not
    # follow is an obligation already answered; only the settlement does
    # (`assessment._owing_frame`).
    if release.owned_here or decision is None:
        found[ACTION_ASSESS] = (
            f"{release.owner_id} already reads the {subject.batch_id} release {reading.verdict!r}; that reading "
            "formed again from the same cohorts and the same rationale reports the record and writes nothing"
        )
    if decision is not None:
        found[ACTION_SETTLE] = (
            f"the gate answered {decision.settlement!r} at {decision.decided_at}; that answer redone reports the "
            "decision on record — and finishes a reversal interrupted before it — rather than taking it again"
        )
        return found
    run = release.counterfactual
    if run is not None and not run.running:
        outcome = replay_ops.RESULT_COMPLETED if run.completed else replay_ops.RESULT_FAILED
        found[ACTION_ASSESS_CONCLUDE] = (
            f"the pinned counterfactual already ended {outcome!r}; run again this reports the result on record "
            "rather than polling for a second one"
        )
        if not run.completed:
            found[ACTION_ASSESS_ABANDON] = (
                f"the pinned counterfactual already ended {outcome!r}; run again for the reason on record this "
                "reports that failure and writes nothing"
            )
    return found


def _no_release(status: LifecycleStatus) -> str:
    if status.current_batch is None:
        return (
            "no batch is current, so there is no cohort to read a release from; an assessment is one frozen "
            "cohort's answer about the promotion before it (invariant 14)"
        )
    return (
        f"{status.current_batch.batch_id} follows no promotion; a repository that promoted nothing before this "
        "cohort and a predecessor that concluded `no-change` both leave nothing to measure against (invariant 7)"
    )


def _reading_refusals(release: ReleaseReading) -> dict[str, str | None]:
    """Each release verb against the reading as it now stands.

    Four states of the counterfactual and two of the reading itself, and the
    order they are asked in is the readers' own: whose record it is, whether
    there is a reading at all, whether its gate has answered, and only then what
    the run underneath it is doing.
    """

    subject = release.frame.subject
    reading = release.reading
    decision = None if reading is None else reading.decision
    state = release.counterfactual_state

    if not release.owned_here and decision is not None:
        foreign = (
            f"the reading of the {subject.batch_id} release belongs to {release.owner_id}, which settled it "
            f"{decision.settlement!r} at {decision.decided_at}; a later cohort reads the line as it now is rather "
            "than that release"
        )
        return {action: foreign for action in _RELEASE_ACTIONS}

    if reading is None:
        unread = (
            f"{release.owner_id} has recorded no reading of the {subject.batch_id} release "
            f"({subject.revision[:12]}); the cohorts are read first, and what they come to is the suspicion a "
            "pinned run is started to settle"
        )
        return {action: (None if action == ACTION_ASSESS else unread) for action in _RELEASE_ACTIONS}

    read = (
        f"{release.owner_id} has already read the {subject.batch_id} release {reading.verdict!r}; a cohort reads a "
        "release once, and `assess-resolve` is what revises that reading on a completed run"
    )
    if decision is not None:
        settled = (
            f"the reading of the {subject.batch_id} release was settled {decision.settlement!r} at "
            f"{decision.decided_at}; nothing is added to a reading once its gate answers, and a release read again "
            "afterwards is the next cohort's reading of the line as it now is"
        )
        return {
            **{action: settled for action in _RELEASE_ACTIONS},
            ACTION_ASSESS: read,
            ACTION_SETTLE: (
                f"the gate answered {decision.settlement!r} at {decision.decided_at}; it answers once, and the "
                "decision the next base was frozen on is not one to re-take"
            ),
        }

    return {
        ACTION_ASSESS: read,
        ACTION_ASSESS_MEASURE: _measure_refusal(state),
        ACTION_ASSESS_CONCLUDE: _running_refusal(release, "concluded"),
        ACTION_ASSESS_ABANDON: _running_refusal(release, "ended"),
        ACTION_ASSESS_WITHDRAW: None
        if state == COUNTERFACTUAL_REQUESTED
        else (
            "no run request is outstanding; a withdrawal takes back a request the harness never answered for, and "
            "a run that happened is ended with a reason instead"
        ),
        ACTION_ASSESS_RESOLVE: None
        if state == COUNTERFACTUAL_COMPLETED
        else (
            f"the pinned counterfactual is {state}, and what a reading is resolved by is a completed run; the "
            f"cohorts left this one at {reading.verdict!r} on their own"
        ),
        ACTION_SETTLE: (
            "a measurement is still in flight; nothing may be added to a reading once its gate answers, so "
            "conclude the run, end it, or withdraw the request first"
        )
        if release.in_flight
        else None,
    }


def _measure_refusal(state: str) -> str | None:
    """A failed run is answered by another attempt, and a completed one is not."""

    if state == COUNTERFACTUAL_RUNNING:
        return (
            "the pinned counterfactual is still running; a release is measured against one pinned pair at a time, "
            "so a second run started under it would leave two answers with nothing to choose between them"
        )
    if state == COUNTERFACTUAL_COMPLETED:
        return (
            "the pinned counterfactual has already completed; the release is measured once, and a second completed "
            "run would leave a reader choosing which of two answers the reading rests on"
        )
    return None


def _running_refusal(release: ReleaseReading, action: str) -> str | None:
    """Both verbs that answer for a run act on one that is going."""

    state = release.counterfactual_state
    if state == COUNTERFACTUAL_RUNNING:
        return None
    if state == COUNTERFACTUAL_REQUESTED:
        return (
            f"a run request is outstanding and no run is recorded for it, so there is nothing to be {action}; ask "
            "again to record what the harness began, or withdraw the request"
        )
    if state == COUNTERFACTUAL_NONE:
        return (
            f"no counterfactual of this release has been started, so there is no run to be {action}; a harness "
            "invocation nothing here named is not one this controller can speak for"
        )
    return f"the pinned counterfactual already ended {state!r}; a run ends once, and another attempt measures again"


def _waiting(status: LifecycleStatus) -> tuple[str, ...]:
    return status.gate.waiting if status.gate is not None else ()


# --- the revision this reading is of -----------------------------------------


@dataclass(frozen=True)
class _Inputs:
    """Everything `state_revision` is a digest of, read in one pass.

    A value rather than a digest so it can be compared with the same reading
    taken a moment later: the token says *what* the state is, and two of these
    say whether it was one state throughout.
    """

    # Every durable record, by repository-relative path and content hash.
    records: tuple[tuple[str, str], ...]
    # Where each ref in play stands, or None where this checkout does not hold it.
    tips: tuple[tuple[str, str | None], ...]


def _state_revision(inputs: _Inputs) -> str:
    """A digest of the durable state the lifecycle was derived from.

    What it is for: a mutation handed the revision an operator saw can refuse
    rather than act on a lifecycle another writer moved in between. So what it
    covers is what a writer changes — the versioned records under `evolution/`,
    the policy that reads them, the machine's pending pool, and the tips of the
    refs in play — and not what a reading merely mentions.

    Three things are deliberately outside it. The clock: `waited_days` and the
    freeze it releases move on their own, and a token that expired overnight
    would refuse operations over a repository nobody wrote to. `.ai-tasks/`: a
    task finishing is this machine's own work rather than a competing writer's,
    and every operation re-reads it under the lock. The audit ledger: it is not
    flow state, and a line appended beside a record already covered here would
    move the revision twice for one operation.

    Truncated on purpose. This detects a lifecycle that moved between a read and
    a write — the writer re-derives every refusal under the lock regardless — so
    what it needs is to be short enough to pass on a command line.
    """

    digest = hashlib.sha256()
    digest.update(f"{STATE_REVISION_VERSION}\n".encode())
    for path, content in inputs.records:
        digest.update(f"{path}={content}\n".encode())
    for ref, tip in inputs.tips:
        digest.update(f"{ref}={tip or ''}\n".encode())
    return f"{STATE_REVISION_VERSION}-{digest.hexdigest()[:_REVISION_DIGITS]}"


def _inputs(config: EvolutionConfig) -> _Inputs:
    """Read everything the token is over, independently of the reading.

    Independent on purpose, and that is the whole shape of this: it is taken
    before and after the reading it accompanies, so a ref set derived from that
    reading could not bracket it. The refs therefore come from the records —
    every ref a durable record names is a line this lifecycle stands on — rather
    than from what the reading made of them, which also covers the ones no
    reading mentions.
    """

    records: list[tuple[str, str]] = []
    refs = {_HEAD}
    for path in _authoritative_paths(config):
        try:
            data = path.read_bytes()
        except OSError:
            # A file that went away under the walk. Left out rather than raised:
            # it reads as a difference between the two brackets, which is what
            # the retry is for, and a record genuinely gone is a state the
            # reading itself reports.
            continue
        records.append((path.relative_to(config.repo_root).as_posix(), hashlib.sha256(data).hexdigest()))
        refs |= _refs_named(data)
    return _Inputs(
        records=tuple(records),
        tips=tuple((ref, ref_tip(config.repo_root, ref)) for ref in sorted(refs)),
    )


def _authoritative_paths(config: EvolutionConfig) -> list[Path]:
    """Every file the lifecycle is derived from, in one order.

    Walked rather than listed, so a record this workspace grows later is covered
    by being written rather than by somebody remembering to add it here.
    """

    known = [config.path, config.state_path]
    for root in (config.batches_root, config.experiments_root):
        known.extend(root.rglob("*"))
    return sorted(path for path in known if path.is_file())


# The commit an operation resolves when nothing on record names one. Exactly one
# does — the grouped admission that freezes a batch's first base takes `HEAD`
# where the operator names no revision (`experiments._base_revision`) — and it is
# in play whenever that admission is, which is before any record of this batch
# exists to name it. A checkout moved between the reading and the write is
# therefore a different base than the one the reading was taken against, and the
# cost of covering it is a token that stops being current when the operator
# switches branch: the safe direction, since the alternative is freezing a base
# nobody read.
_HEAD = "HEAD"

# What a durable record calls a ref. Everything else in these records is a
# revision — `reverted_from` and the pinned candidates included — and a revision
# is covered by the bytes that state it.
_REF_FIELDS = frozenset({"ref", "base_release_ref", "merge_input_ref", "source_ref"})


def _refs_named(data: bytes) -> set[str]:
    """Every ref this record names, wherever it names it.

    Walked rather than read field by field for the reason the paths above are:
    a record that grows another integration, another line, or another nesting is
    covered by stating it in one of these fields rather than by somebody
    remembering this function. A file that is not a record parses as nothing and
    names none — drafts are bytes at the admission gate, and their bytes are
    already covered.
    """

    try:
        record = json.loads(data)
    except (UnicodeDecodeError, ValueError):
        return set()
    found: set[str] = set()
    _collect_refs(record, found)
    return found


def _collect_refs(node: Any, found: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _REF_FIELDS and isinstance(value, str) and value.startswith("refs/"):
                found.add(value)
            else:
                _collect_refs(value, found)
    elif isinstance(node, list):
        for item in node:
            _collect_refs(item, found)


def _replay_json(evidence: Evidence | None, history: History | None) -> dict[str, Any] | None:
    """The round's evidence, with the two ways it falls short kept apart, and the
    records no reading takes.

    `drift` is what this checkout established and `unverified` is what it could
    not answer — a clone that never fetched the source-line ref cannot say
    whether the merge input moved. A reader that merged them would report a
    question nobody asked as a finding, and `promotable` is false either way.

    `request` is the one that has to be structural rather than a note. It reaches
    `drift` as prose today, which is enough to read and not enough to act on: the
    two things to do about it are resuming the start and withdrawing it, and both
    need the position it holds. It is also deliberately absent from `drift`
    exactly when the evidence is promotable, so "no note" never means "nothing
    outstanding" — this field is what says that.
    """

    if evidence is None:
        return None
    run = evidence.replay
    pending = history.pending if history else None
    return {
        "state": evidence.state,
        "round": evidence.round_number,
        "promotable": evidence.promotable,
        "run": None
        if run is None
        else {
            "round": run.round_number,
            "attempt": run.attempt,
            "started_at": run.started_at,
            "candidate_revision": run.integration.candidate_revision,
            "merge_input_revision": run.integration.merge_input_revision,
            "merge_input_ref": run.integration.merge_input_ref,
            "tree": run.integration.tree,
            "outcome": None if run.result is None else run.result.outcome,
            "concluded_at": None if run.result is None else run.result.concluded_at,
        },
        "request": None
        if pending is None
        else {
            "round": pending.round_number,
            "attempt": pending.attempt,
            "requested_at": pending.requested_at,
            "candidate_revision": pending.integration.candidate_revision,
            "merge_input_revision": pending.integration.merge_input_revision,
            "merge_input_ref": pending.integration.merge_input_ref,
            "tree": pending.integration.tree,
            "expectation": pending.expectation,
        },
        # Positions given up without ever becoming runs. Never reissued, and each
        # one may be a run still going that nothing here will hear about.
        "withdrawn": [
            {
                "round": taken.round_number,
                "attempt": taken.attempt,
                "requested_at": taken.requested_at,
                "withdrawn_at": taken.withdrawn_at,
                "candidate_revision": taken.integration.candidate_revision,
            }
            for taken in (history.withdrawn if history else ())
        ],
        "drift": list(evidence.drift),
        "unverified": list(evidence.unverified),
    }


def _promotion_json(promotion: Promotion | None) -> dict[str, Any] | None:
    if promotion is None:
        return None
    return {
        "batch_id": promotion.batch_id,
        "experiment_id": promotion.experiment_id,
        "revision": promotion.revision,
        "round": promotion.round_number,
        "candidate_revision": promotion.candidate_revision,
        "merge_input_revision": promotion.merge_input_revision,
        "merge_input_ref": promotion.merge_input_ref,
        "tree": promotion.tree,
        # Planned, never deployed: what a target actually carries is in that
        # target's own deploy receipt and lags this until it is redeployed
        # (contract: Revisions in play).
        "planned_targets": list(promotion.planned_targets),
        "rollback": None
        if promotion.rollback is None
        else {
            "revision": promotion.rollback.revision,
            "reverted_from": promotion.rollback.reverted_from,
            # Null while the inverse commit has not been recorded as reaching the
            # line: an operation in flight, which is a different next step from
            # one that finished.
            "reverted_at": promotion.rollback.reverted_at,
            "reason": promotion.rollback.reason,
        },
    }


def _prepared_json(prepared: PreparedPromotion | None) -> dict[str, Any] | None:
    """The merge a promotion of this experiment was prepared as, if one stands.

    Null is one state and only one: no promotion prepared. The other absence a
    prepared promotion has — a version-1 record, which kept no merge unit at all
    — belongs to an attempt that already ended, and this is asked of the open one.
    """

    if prepared is None:
        return None
    return {
        "round": prepared.round_number,
        "candidate_revision": prepared.candidate_revision,
        "merge_input_revision": prepared.merge_input_revision,
        "merge_input_ref": prepared.merge_input_ref,
        "tree": prepared.tree,
        "revision": prepared.revision,
        "reason": prepared.reason,
        "planned_targets": list(prepared.planned_targets),
        "prepared_at": prepared.prepared_at,
    }


def _release_json(release: ReleaseReading | None) -> dict[str, Any] | None:
    """The release before this batch: whose reading it is, what its provenance
    supports, and what has been recorded.

    The frame is emitted whether or not anything was recorded, because the
    denominators and the exclusions are what a reader judges an absent or a
    zero-cohort reading by — two empty cohorts are absent evidence, never a
    reading against the release, and only the exclusions say which of the two
    this is.
    """

    if release is None:
        return None
    frame = release.frame
    reading = release.reading
    assessed = release.assessed
    return {
        "owner_batch_id": release.owner_id,
        # False when the cohort that owes the reading already ended: the record
        # being waited on is not this batch's own (invariant 17).
        "owned_here": release.owned_here,
        # Read off the owner's own frame, so it is True wherever a reading is
        # owed at all. Emitted because `owned_here` on its own says where the
        # obligation sits and not that there is one.
        "owed": frame.owed,
        "settled": release.settled,
        "assessed": {
            "batch_id": assessed.batch_id,
            "experiment_id": assessed.experiment_id,
            "revision": assessed.revision,
            # Which question the reading answered, and never what the line
            # carries now: settling `rolled-back` writes the decision onto a
            # reading formed while the release stood and lands the inverse
            # commit after it, so on the path a regression finding ordinarily
            # ends these two disagree. Reading the lineage's current standing
            # here would report a cohort as having assessed a reversal it never
            # saw.
            "standing": assessed.standing,
            "rollback_revision": assessed.rollback_revision,
            "planned_targets": list(assessed.planned_targets),
            # The same release as the source line holds it now — the other half
            # of that pair, and the one a rollback moves. Identity is not
            # repeated because it cannot differ: a record naming another
            # promotion is refused at read.
            "source_line": {
                "standing": frame.subject.standing,
                "rollback_revision": frame.subject.rollback_revision,
            },
        },
        "cohorts": {
            "before": {
                "report_keys": list(frame.before.report_keys),
                "task_count": frame.before.task_count,
            },
            "after": {
                "report_keys": list(frame.after.report_keys),
                "task_count": frame.after.task_count,
            },
            "minimum_task_count": frame.minimum_task_count,
            "catalog_count": len(frame.catalog),
            "excluded": [
                {
                    "report_key": item.report_key,
                    "batch_id": item.batch_id,
                    "reason": item.reason,
                    "detail": item.detail,
                }
                for item in frame.excluded
            ],
            # Reports this checkout could not place at all. A fact about the
            # clone, never agreement.
            "unverified": list(frame.unverified),
        },
        "comparability": {
            "coherent": frame.comparability.coherent,
            "incoherent": list(frame.comparability.incoherent),
            "facets": [
                {
                    "facet": facet.facet,
                    "coherent": facet.coherent,
                    "before": list(facet.before),
                    "after": list(facet.after),
                }
                for facet in frame.comparability.facets
            ],
            # Whether the cohorts alone could carry a direction, before any
            # measurement. False on every frame while no manifest states the
            # shape of the work, which is why a direction rests on the
            # counterfactual instead.
            "cohorts_support_direction": frame.cohorts_support_direction,
        },
        "counterfactual": _counterfactual_json(release),
        "reading": None
        if reading is None
        else {
            "verdict": reading.verdict,
            "confidence": reading.confidence,
            "rationale": reading.rationale,
            "formed_at": reading.formed_at,
            "directional": reading.directional,
            "metrics": [
                {
                    "metric": measurement.metric,
                    "unit": measurement.unit,
                    "before": measurement.before,
                    "after": measurement.after,
                    "better": measurement.better,
                    "goal": measurement.goal,
                }
                for measurement in reading.metrics
            ],
        },
        "decision": None
        if reading is None or reading.decision is None
        else {
            "settlement": reading.decision.settlement,
            "decided_at": reading.decision.decided_at,
            "reason": reading.decision.reason,
            "rollback_revision": reading.decision.rollback_revision,
        },
    }


def _counterfactual_json(release: ReleaseReading) -> dict[str, Any]:
    """The pinned before/after run, in whichever of its four states it stands.

    `state` and the two records are emitted together rather than folded into
    one: a request may stand over a failed run, and the run it retries is still
    what an operator reads to know why.
    """

    reading = release.reading
    run = release.counterfactual
    requested = None if reading is None else reading.requested
    return {
        "state": release.counterfactual_state,
        "run": None
        if run is None
        else {
            "experiment_id": run.position.experiment_id,
            "round": run.position.round_number,
            "attempt": run.position.attempt,
            "started_at": run.started_at,
            "base_revision": run.integration.base_revision,
            "candidate_revision": run.integration.candidate_revision,
            "source_ref": run.integration.source_ref,
            "harness": run.harness.id,
            "expectation": run.expectation,
            "outcome": None if run.result is None else run.result.outcome,
            "concluded_at": None if run.result is None else run.result.concluded_at,
            "detail": None if run.result is None else run.result.detail,
        },
        "request": None
        if requested is None
        else {
            "experiment_id": requested.position.experiment_id,
            "round": requested.position.round_number,
            "attempt": requested.position.attempt,
            "requested_at": requested.requested_at,
            "base_revision": requested.integration.base_revision,
            "candidate_revision": requested.integration.candidate_revision,
            "expectation": requested.expectation,
        },
        "withdrawn": [
            {
                "experiment_id": taken.position.experiment_id,
                "round": taken.position.round_number,
                "attempt": taken.position.attempt,
                "requested_at": taken.requested_at,
                "withdrawn_at": taken.withdrawn_at,
            }
            for taken in (() if reading is None else reading.withdrawn)
        ],
    }


def _gate_json(gate: Gate | None) -> dict[str, Any] | None:
    if gate is None:
        return None
    return {
        "waiting": list(gate.waiting),
        "consumed": dict(gate.consumed),
        "declined": list(gate.declined),
        "missing": list(gate.missing),
        "unusable": list(gate.unusable),
    }


def _experiment_json(experiment: Experiment | None, ref: RefState | None) -> dict[str, Any] | None:
    if experiment is None:
        return None
    round_ = experiment.last_round
    return {
        "experiment_id": experiment.experiment_id,
        "created_at": experiment.created_at,
        "base_revision": experiment.base_revision,
        "rounds": len(experiment.rounds),
        "round": {
            "number": round_.number,
            "state": ROUND_OPEN if experiment.open_round is not None else ROUND_CANDIDATE_READY,
            "opened_at": round_.opened_at,
            "candidate_revision": round_.candidate_revision,
            "tasks": [
                {
                    "task_id": task.task_id,
                    "draft_id": task.draft_id,
                    "complete": task.complete,
                }
                for task in round_.tasks
            ],
        },
        # The one durable state that narrows this experiment to a single verb:
        # while it stands, `promote` finishes it or discards it, and everything
        # else refuses because the merge may already be on the source line.
        "prepared_promotion": _prepared_json(experiment.promotion),
        "ref": None
        if ref is None
        else {
            "ref": ref.ref,
            "tip": ref.tip,
            "pinned": ref.pinned,
            "state": ref.state,
            "chain": ref.chain,
            "chain_break": list(ref.chain_break) if ref.chain_break else None,
            "consistent": ref.consistent,
        },
    }


def _terminal_json(experiment: Experiment) -> dict[str, Any]:
    decision = experiment.decision
    return {
        "experiment_id": experiment.experiment_id,
        "outcome": decision.outcome if decision else None,
        "decided_at": decision.decided_at if decision else None,
        "superseded_by": decision.superseded_by if decision else None,
        "promotion_revision": decision.promotion_revision if decision else None,
        "rounds": len(experiment.rounds),
    }


def _revision_json(revision: Revision | None) -> dict[str, Any] | None:
    return None if revision is None else {"sha": revision.sha, "ref": revision.ref}
