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
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .assessment import Assessment, Counterfactual, Frame, Obligation
from .assessment import obligation as describe_obligation
from .batches import AdmissionDecision, awaiting_analysis, evaluate_admission
from .config import EvolutionConfig
from .lineage import BatchLineage, Experiment, Gate, Lineage, PreparedPromotion, RefState, Round
from .lineage import describe as describe_lineage
from .manifests import OUTCOME_PROMOTED, load_batches
from .replay import Evidence, History, PendingRun, WithdrawnRequest, describe_evidence, read_replays
from .revisions import Revision
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
    """

    revision: str
    reverted_from: str
    reverted_at: str | None
    reason: str


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
        }


def describe(config: EvolutionConfig, *, now: datetime | None = None) -> LifecycleStatus:
    """Derive the current phase. Read-only.

    Fails closed rather than guessing: an unreadable manifest, a state file that
    contradicts the batches beside it, a lineage that cannot be read as one
    history, a replay or release-assessment record its own reader refuses, or a
    file standing in for an analysis task all raise here exactly as they would
    during a freeze. A status that smoothed those over would report a lifecycle
    the next operation refuses to act on.
    """

    moment = now or datetime.now(timezone.utc)
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

    return LifecycleStatus(
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
    )


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
            "batch_id": frame.subject.batch_id,
            "experiment_id": frame.subject.experiment_id,
            "revision": frame.subject.revision,
            # Whether the source line still carries the release. Never what the
            # reading says: `standing` is about the line now, and the reading is
            # about the question it answered.
            "standing": frame.subject.standing,
            "rollback_revision": frame.subject.rollback_revision,
            "planned_targets": list(frame.subject.planned_targets),
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
