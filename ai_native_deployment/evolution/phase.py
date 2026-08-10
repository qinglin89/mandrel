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
3. `implementing`, then `candidate-ready` once the round is sealed: the open
   experiment, which is where work and then replay happen.
4. `proposals-pending` — no experiment is open and drafts are waiting at the
   human admission gate (invariant 9).
5. `conclusion-pending` — nothing is open and nothing is waiting, so what the
   batch needs is its outcome: a promotion, or the `no-change` that says the
   evidence justified nothing (invariant 7).

Every fact behind the choice is emitted in the JSON regardless of which label
won, so a reader that cares about a lower-precedence one does not have to
re-derive it.

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

from .batches import AdmissionDecision, awaiting_analysis, evaluate_admission
from .config import EvolutionConfig
from .lineage import BatchLineage, Experiment, Gate, Lineage, RefState, Round
from .lineage import describe as describe_lineage
from .manifests import OUTCOME_PROMOTED, load_batches
from .revisions import Revision
from .state import artifacts_dir_name, load_state

SCHEMA_VERSION = 2

PHASE_IDLE = "idle"
PHASE_POOL = "pool"
PHASE_BATCH_FROZEN = "batch-frozen"
PHASE_DISPOSITIONS_READY = "dispositions-ready"
PHASE_PROPOSALS_PENDING = "proposals-pending"
PHASE_IMPLEMENTING = "implementing"
PHASE_CANDIDATE_READY = "candidate-ready"
PHASE_CONCLUSION_PENDING = "conclusion-pending"

# A round's two states (contract: Lifecycle states).
ROUND_OPEN = "open"
ROUND_CANDIDATE_READY = "candidate-ready"


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
class Promotion:
    """The last change this repository recorded as reaching the source line."""

    batch_id: str
    experiment_id: str
    revision: str


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

    @property
    def open_round(self) -> Round | None:
        return self.experiment.open_round if self.experiment else None

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
            },
            "gate": _gate_json(self.gate),
            "experiments": {
                "open": _experiment_json(self.experiment, self.ref),
                "history": [_terminal_json(experiment) for experiment in self.history],
            },
            "implementation_tasks": list(self.implementation_tasks),
            "revisions": {
                "base": _revision_json(self.revisions.base),
                "candidate_tip": _revision_json(self.revisions.candidate_tip),
                "round_candidate": _revision_json(self.revisions.round_candidate),
            },
            "last_promotion": None
            if self.last_promotion is None
            else {
                "batch_id": self.last_promotion.batch_id,
                "experiment_id": self.last_promotion.experiment_id,
                "revision": self.last_promotion.revision,
            },
        }


def describe(config: EvolutionConfig, *, now: datetime | None = None) -> LifecycleStatus:
    """Derive the current phase. Read-only.

    Fails closed rather than guessing: an unreadable manifest, a state file that
    contradicts the batches beside it, a lineage that cannot be read as one
    history, or a file standing in for an analysis task all raise here exactly
    as they would during a freeze. A status that smoothed those over would
    report a lifecycle the next operation refuses to act on.
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
        last_promotion=_last_promotion(lineage),
    )


def _round_tail(round_: Round) -> str:
    """What an open round is waiting for.

    A round whose every admitted task has been observed complete is still open —
    the seal is what pins its candidate, and nothing may be measured before that
    (invariant 16). Saying "0 tasks left" would report the work as the thing
    still outstanding when it is the seal.
    """

    left = len(round_.unfinished)
    if not left:
        return " (ready to seal)"
    return f" ({left} task{'s' if left != 1 else ''} left)"


def _phase(*, current: BatchLineage | None, stage_open: bool, pool: int) -> str:
    if current is None:
        return PHASE_POOL if pool else PHASE_IDLE
    if stage_open:
        return PHASE_DISPOSITIONS_READY if current.batch.findings_recorded else PHASE_BATCH_FROZEN
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


def _last_promotion(lineage: Lineage) -> Promotion | None:
    """The most recent batch outcome that promoted something.

    History rather than a revision in play, and the one piece of it a status
    reader needs: it is the commit the next cohort's reports have to be produced
    at before anything can measure whether the change worked.
    """

    promoted = [
        item
        for item in lineage.batches
        if item.outcome is not None and item.outcome["outcome"] == OUTCOME_PROMOTED
    ]
    if not promoted:
        return None
    latest = promoted[-1]
    outcome = latest.outcome or {}
    return Promotion(
        batch_id=latest.batch_id,
        experiment_id=outcome["experiment_id"],
        revision=outcome["promotion_revision"],
    )


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
