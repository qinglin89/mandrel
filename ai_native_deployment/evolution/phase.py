"""Where the evolution lifecycle currently stands.

`status` answers one question — what is the next thing that happens, and what is
holding it — so it names a **phase**, not a pool count. The phase is derived on
every call from what is already on disk: the frozen manifests, the closure
records and drafts beside them, the runtime pool, the local task pool, and git.
Nothing here is stored, and nothing here writes.

That is deliberate and it is the same rule the workflow runbook's scheduler
follows: flow state that is written down is flow state that can disagree with
the artifacts. A phase re-derived from the artifacts cannot go stale, cannot be
lost with `.ai-evolution/`, and needs no migration when the lifecycle grows a
step.

**Precedence.** One label has to be chosen when several facts are true at once,
and the order is by what blocks the next action:

1. An open batch dominates (`batch-frozen`, then `dispositions-ready` once its
   dispositions are recorded). It is what stops another cohort forming
   (invariant 12), so it is what an operator needs to see.
2. `implementing` — an admitted change task is in flight. Implementations are
   serialized (invariant 12), so this is the next-most blocking fact.
3. `proposals-pending` — drafts are waiting at the human admission gate
   (invariant 9). Nothing proceeds until someone admits or discards them.
4. `pool` / `idle` — evidence accumulating, or nothing at all.

Every fact behind the choice is emitted in the JSON regardless of which label
won, so a reader that cares about a lower-precedence one does not have to
re-derive it.

**What is local.** `.ai-tasks/` is machine-local and gitignored, so the
`implementing` reading and an open batch's analysis lifecycle are answerable
only on the machine holding those files; every other clone reads the committed
closure record instead. Staged evaluation bundles under `.ai-evolution/` are
machine-local too, which is why an open batch reports how much of its evidence
is present *here* rather than treating an absent bundle as damage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from . import analysis_task
from .batches import AdmissionDecision, evaluate_admission
from .batches import open_batch as open_batch_of
from .config import EvolutionConfig
from .manifests import Batch, load_batches
from .revisions import Revision, Revisions, describe_revisions
from .state import artifacts_dir_name, load_state

SCHEMA_VERSION = 1

PHASE_IDLE = "idle"
PHASE_POOL = "pool"
PHASE_BATCH_FROZEN = "batch-frozen"
PHASE_DISPOSITIONS_READY = "dispositions-ready"
PHASE_PROPOSALS_PENDING = "proposals-pending"
PHASE_IMPLEMENTING = "implementing"


@dataclass(frozen=True)
class BatchView:
    """One frozen batch, as a lifecycle reader needs it."""

    batch_id: str
    task_count: int
    report_count: int
    analysis_task_id: str | None
    findings_recorded: bool
    closed: bool
    # Staged bundles for this batch's reports that exist on this machine. A
    # frozen cohort owns its reports wherever it was frozen, so a clone that
    # never staged them holds a manifest with no evidence behind it — a fact to
    # show, not an error: the manifest's hashes still say what was analyzed.
    evidence_local: int
    drafts: tuple[str, ...]

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
            "closed": self.closed,
            "evidence_local": self.evidence_local,
            "proposed_task_drafts": list(self.drafts),
        }


@dataclass(frozen=True)
class LifecycleStatus:
    """The derived phase and every fact it was derived from."""

    phase: str
    decision: AdmissionDecision
    pool_complete: bool
    batch_count: int
    open_batch: BatchView | None
    proposals: tuple[BatchView, ...]
    implementation_tasks: tuple[str, ...]
    revisions: Revisions

    @property
    def summary(self) -> str:
        """The phase as one line — the form the scope names, `pool N/<target>`
        included."""

        if self.phase == PHASE_POOL:
            return f"{PHASE_POOL} {self.decision.task_count}/{self.decision.target}"
        if self.open_batch is not None:
            return f"{self.phase} {self.open_batch.batch_id}"
        if self.phase == PHASE_PROPOSALS_PENDING:
            drafts = sum(len(batch.drafts) for batch in self.proposals)
            return f"{self.phase} ({drafts} draft{'s' if drafts != 1 else ''})"
        if self.phase == PHASE_IMPLEMENTING:
            count = len(self.implementation_tasks)
            return f"{self.phase} ({count} admitted task{'s' if count != 1 else ''})"
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
            },
            "batches": {
                "total": self.batch_count,
                "open": self.open_batch.to_json() if self.open_batch else None,
                "awaiting_admission": [batch.to_json() for batch in self.proposals],
            },
            "implementation_tasks": list(self.implementation_tasks),
            "revisions": {
                "baseline": _revision_json(self.revisions.baseline),
                "candidate": _revision_json(self.revisions.candidate),
            },
        }


def describe(config: EvolutionConfig, *, now: datetime | None = None) -> LifecycleStatus:
    """Derive the current phase. Read-only.

    Fails closed rather than guessing: an unreadable manifest, a state file that
    contradicts the batches beside it, or a file standing in for an analysis
    task all raise here exactly as they would during a freeze. A status that
    smoothed those over would report a lifecycle the next `start` refuses to
    act on.
    """

    moment = now or datetime.now(timezone.utc)
    state = load_state(config)
    batches = load_batches(config)
    # One reading of "open", shared with the freeze path: a status that applied
    # its own would describe a lifecycle `start` disagrees with.
    unfinished = open_batch_of(config, batches=batches)

    analysis_task_ids = {batch.analysis_task_id for batch in batches if batch.analysis_task_id}
    views = {
        batch.batch_id: _view(config, batch, closed=unfinished is None or batch.batch_id != unfinished.batch_id)
        for batch in batches
    }
    open_view = views[unfinished.batch_id] if unfinished else None
    proposals = tuple(view for view in views.values() if view.closed and view.drafts)
    implementing = _implementation_tasks(config, frozenset(views), exclude=analysis_task_ids)

    decision = evaluate_admission(
        config,
        state,
        now=moment,
        open_batch_id=unfinished.batch_id if unfinished else None,
    )

    return LifecycleStatus(
        phase=_phase(
            open_view=open_view,
            implementing=implementing,
            proposals=proposals,
            pool=decision.task_count,
        ),
        decision=decision,
        pool_complete=state.feed_exhausted,
        batch_count=len(batches),
        open_batch=open_view,
        proposals=proposals,
        implementation_tasks=implementing,
        revisions=describe_revisions(config.repo_root),
    )


def _phase(
    *,
    open_view: BatchView | None,
    implementing: tuple[str, ...],
    proposals: tuple[BatchView, ...],
    pool: int,
) -> str:
    if open_view is not None:
        return PHASE_DISPOSITIONS_READY if open_view.findings_recorded else PHASE_BATCH_FROZEN
    if implementing:
        return PHASE_IMPLEMENTING
    if proposals:
        return PHASE_PROPOSALS_PENDING
    return PHASE_POOL if pool else PHASE_IDLE


def _view(config: EvolutionConfig, batch: Batch, *, closed: bool) -> BatchView:
    return BatchView(
        batch_id=batch.batch_id,
        task_count=batch.task_count,
        report_count=len(batch.reports),
        analysis_task_id=batch.analysis_task_id,
        findings_recorded=batch.findings_recorded,
        closed=closed,
        evidence_local=sum(
            1 for key in batch.report_keys if (config.artifacts_root / artifacts_dir_name(key)).is_dir()
        ),
        drafts=_drafts(batch),
    )


def _drafts(batch: Batch) -> tuple[str, ...]:
    """Change-task drafts still waiting at the admission gate.

    Admission *moves* a draft into `.ai-tasks/` (contract: Change admission), so
    what remains here is exactly what no human has admitted yet — the directory
    is its own to-do list, with no second record to keep in step.
    """

    directory = batch.directory / analysis_task.PROPOSED_TASKS_DIRNAME
    if not directory.is_dir():
        return ()
    return tuple(sorted(path.name for path in directory.glob("*.md") if path.is_file()))


def _implementation_tasks(
    config: EvolutionConfig,
    batch_ids: frozenset[str],
    *,
    exclude: set[str],
) -> tuple[str, ...]:
    """Active local tasks that name a frozen batch — admitted change tasks.

    Reads the citation the contract already requires: every evolution task must
    cite "one immutable batch ID" (Evolution task requirements). So an active
    task mentioning one is an evolution task working on that batch's evidence,
    with no extra marker to invent and nothing for a session to keep in sync.

    The batch's own analysis task is excluded — its state is the open-batch
    reading, and counting it here would report `implementing` for a batch whose
    analysis is merely finished.

    Machine-local, like everything under `.ai-tasks/`: another clone shows no
    admitted tasks because it has none to show, not because none exist.
    """

    root = analysis_task.tasks_root(config)
    if not batch_ids or not root.is_dir():
        return ()

    found: list[str] = []
    for path in sorted(root.glob("*.md")):
        task_id = path.stem
        if task_id in exclude or path.name == analysis_task.INDEX_FILENAME:
            continue
        if analysis_task.task_status(path) == analysis_task.STATUS_COMPLETED:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if any(batch_id in text for batch_id in batch_ids):
            found.append(task_id)
    return tuple(found)


def _revision_json(revision: Revision | None) -> dict[str, Any] | None:
    return None if revision is None else {"sha": revision.sha, "ref": revision.ref}
