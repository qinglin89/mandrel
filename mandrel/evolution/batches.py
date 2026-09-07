"""Admission policy, the batch proposal, and the immutable freeze.

This is the step where evidence becomes a cohort, and it is **two operations**:
a proposal offering a batch out of the pool (`proposals.py`), and the freeze
that fixes it. The split is what makes the human window real — everything a
person may argue with about membership happens while the offer is still ignored
runtime state, so the manifest is immutable from the moment it exists rather
than being an immutable record somebody edits.

Four things make it safe:

- **A human triggers both halves** (invariant 9). Nothing here is scheduled, and
  no code path lowers the configured minimum.
- **The proposal is a reading, never evidence.** It is rebuilt by the next
  `start` and thrown away rather than repaired. The freeze re-reads the source
  line, re-validates every member against the live pool and re-verifies every
  staged bundle before it writes, so nothing durable ever stands on an offer.
- **The manifest is immutable** (invariant 3). It is written once, by an atomic
  directory rename, and never edited afterwards — a late report belongs to a
  later batch.
- **One current batch at a time** (invariant 14). A batch is current from its
  freeze until its outcome is recorded — through analysis, admission, and every
  experiment its evidence justified. While one is current, `start` finishes
  whatever that batch's freeze left undone rather than forming a second cohort;
  the pool goes on accumulating in the meantime, which is where the next one
  comes from. Which batch that is comes from the whole lineage
  (`lineage.current_batch`) and is settled before anything here writes: an
  outcome record its own experiments contradict has concluded nothing, and two
  current batches leave no one cycle a repair or a freeze could belong to.

**What may be frozen.** Only a pool that is the whole eligible set — a cohort
frozen from a prefix has a denominator set by a local pagination limit rather
than by the evidence (invariants 1 and 2). Discovery records whether it drained
the feed (`state.feed_exhausted`), because the freeze it would mislead need not
be in the same run: `sync` and `freeze` are separate commands, and a cursor
alone does not say why discovery stopped where it did.

**Interruption.** A freeze commits in five steps, in this order: the manifest
(the durable membership statement), the state transition that moves the batched
reports out of the pending pool, the pending analysis task, the audit line, then
dropping the offer it fixed. Each step is safe to redo, so a restarted `start`
finds the open batch and finishes whatever remains — the offer included, since
one left standing beside a frozen batch is what a second freeze would read. The one residual, shared with the importer, is that
an interruption before the last step costs an audit line: nothing derives state
from the ledger, so a missing line cannot corrupt a decision. A crash mid-freeze
can leave a `.staging-*` directory under `evolution/batches/`; it is inert and
belongs to no batch.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..deploy import deploys_canonical_path
from ..hashing import sha256_bytes
from ..lockfile import CanonicalIdentities, is_object_id
from . import analysis_task
from .analysis_task import AnalysisTaskSpec
from .config import BATCH_SCHEMA_FILENAME, CLOSURE_SCHEMA_FILENAME, EvolutionConfig
from .errors import (
    AlreadyAnswered,
    CheckoutUnusable,
    EvolutionError,
    InputError,
    NothingToActOn,
    ProposalError,
    RecordError,
    StandingWork,
    StateError,
    UnknownSubject,
)
from .feed import ReportFeed
from .importer import DEFAULT_MAX_PAGES, DEFAULT_PAGE_SIZE, SyncResult, sync_locked
from .ledger import append_records, build_record
from .lineage import current_batch
from .lineage import describe as describe_lineage
from .manifests import (
    ASSESSMENT_FILENAME,
    BATCH_SCHEMA_VERSION,
    CLOSURE_FILENAME,
    CLOSURE_SCHEMA_VERSION,
    FINDINGS_FILENAME,
    HUMAN_REASONS,
    MANIFEST_FILENAME,
    MODE_AUTO,
    MODE_HUMAN_INCLUDED,
    REASON_HUMAN_EXCLUDED,
    REASON_IDENTITY_DIFFERS,
    REASON_REVISION_ABSENT,
    REASON_REVISION_MALFORMED,
    REASON_REVISION_UNRESOLVABLE,
    Batch,
    load_batches,
    next_batch_id,
    read_closure,
    require_coherent_selection,
)
from .proposals import (
    BatchProposal,
    Exclusion,
    Member,
    clear_proposal,
    load_proposal,
    save_proposal,
)
from .reports import (
    NormalizedReport,
    Rejection,
    canonical_json,
    load_import_schema,
    normalize,
    verify_artifact_bytes,
)
from .revisions import ref_tip, require_git
from .schema import format_rfc3339, load_schema, parse_rfc3339, validate_or_raise
from .state import (
    ARTIFACTS_SUBDIR,
    REPORT_JSON_FILENAME,
    EvolutionState,
    PoolEntry,
    ReportRef,
    atomic_write_text,
    load_state,
    save_state,
    single_writer_lock,
    state_exists,
)

RECORD_BATCH_FROZEN = "batch-frozen"
RECORD_BATCH_ANALYZED = "batch-analyzed"

# Why a batch was frozen. Locally authored, bounded, and safe to publish — the
# ledger's `detail` carries one of these and nothing else.
TRIGGER_TARGET = "target-reached"
TRIGGER_MAX_WAIT = "max-wait-days-elapsed"
TRIGGER_FORCED = "human-forced-below-target"

# Why it was not.
REASON_CURRENT_BATCH = "current-batch"
REASON_POOL_INCOMPLETE = "pool-incomplete"
REASON_POOL_EMPTY = "pool-empty"
# The pool holds evidence and none of it is this base's. Deliberately not the
# same answer as an empty pool, because the operator's next move differs: an
# empty pool waits for evaluations, while an off-base pool holds evidence about
# some other protocol and waits for reports produced at this line — or for a
# human to argue one of them in.
REASON_POOL_OFF_BASE = "pool-off-base"
# No batch has been offered, so there is no membership to measure. Distinct from
# every count below: those are answers about a batch, and this says there is no
# batch to answer about yet. Only a reading ever sees it — `freeze` requires the
# offer before it measures anything (`_require_current_proposal`).
REASON_NO_PROPOSAL = "no-proposal"
REASON_BELOW_MINIMUM = "pool-below-minimum"
REASON_BELOW_TARGET = "pool-below-target"

# The reasons that answer before any membership is selected. They are facts
# about the cycle and the pool rather than about a batch's members, which is
# what lets a caller ask whether it needs a proposal at all before requiring one
# (`_pool_reason`).
POOL_ONLY_REASONS = frozenset({REASON_CURRENT_BATCH, REASON_POOL_INCOMPLETE, REASON_POOL_EMPTY})

# What a reconciling `start` completed, for the operator's benefit.
COMPLETED_STATE = "state-transition"
COMPLETED_TASK = "analysis-task"
COMPLETED_INDEX = "index-row"
COMPLETED_PROPOSAL = "proposal-cleared"


@dataclass(frozen=True)
class AdmissionDecision:
    """Whether a batch may be formed, and the numbers behind the answer.

    `task_count` is the *proposal's* membership and never the pool's, because
    that is what a freeze would form. The two are different numbers the moment
    the base selects anything (R1), and measuring the thresholds against the
    pool would pass a target of 20 on a pool of 20 of which 2 belong to this
    base — then freeze the 2. `pool_count` is carried beside it so the
    difference is readable rather than implied: what a reader needs is both, and
    a single count cannot say whether a short batch is short of evidence or
    short of evidence *at this base*.
    """

    freeze: bool
    task_count: int
    pool_count: int
    target: int
    minimum: int
    trigger: str | None = None
    reason: str | None = None
    forced: bool = False
    oldest_pending_at: str | None = None
    waited_days: int | None = None
    max_wait_days: int | None = None
    # The batch whose change cycle is still running, when one is what refused
    # the freeze (invariant 14) — not only one still awaiting its analysis.
    current_batch_id: str | None = None


@dataclass(frozen=True)
class FreezeResult:
    decision: AdmissionDecision
    batch_id: str | None = None
    manifest_path: Path | None = None
    analysis_task_id: str | None = None
    analysis_task_path: Path | None = None
    current_batch_id: str | None = None
    completed: tuple[str, ...] = ()
    # Batches whose completed analysis this run published as a closure record.
    closed_batch_ids: tuple[str, ...] = ()

    @property
    def frozen(self) -> bool:
        return self.batch_id is not None


@dataclass(frozen=True)
class ProposalResult:
    """What building a batch proposal found, and whether one now stands.

    `proposal` is None for the two states in which there is nothing to offer: a
    batch whose change cycle is still running (invariant 14), and an empty
    pool. Neither is a failure, and neither leaves a stale proposal behind.
    """

    decision: AdmissionDecision
    proposal: BatchProposal | None = None
    proposal_path: Path | None = None
    current_batch_id: str | None = None
    analysis_task_id: str | None = None
    manifest_path: Path | None = None
    completed: tuple[str, ...] = ()
    # Batches whose completed analysis this run published as a closure record.
    closed_batch_ids: tuple[str, ...] = ()

    @property
    def proposed(self) -> bool:
        return self.proposal is not None


@dataclass(frozen=True)
class MoveResult:
    """One human move on the standing offer, and what the offer now measures.

    `changed` is false where the move was already recorded exactly as asked and
    nothing was written — the redo form every operation here has. It matters more
    than usual because this file is in the state digest: a move that rewrote an
    identical record would move an operator's outstanding token for no decision.
    """

    proposal: BatchProposal
    proposal_path: Path
    decision: AdmissionDecision
    repo_id: str
    task_id: str
    # `MODE_HUMAN_INCLUDED` or `REASON_HUMAN_EXCLUDED` — the two directions of
    # the window, named as the record names them.
    move: str
    changed: bool
    # True where the task already carried a move of this direction and this call
    # restated it, which is a correction rather than a first judgement.
    restated: bool = False


@dataclass(frozen=True)
class StartResult:
    """`start` is discovery then a proposal, under one lock.

    It stopped being discovery then a *freeze* when the formation split in two:
    the poll offers a batch and the human fixes it, so nothing durable is
    written between the two commands.
    """

    sync: SyncResult
    proposal: ProposalResult


def awaiting_analysis(config: EvolutionConfig, batch: Batch) -> bool:
    """Whether this batch still awaits its analysis.

    The analysis *stage*, not the batch: a batch stays current through
    admission, its experiments, and the decision that follows (invariant 14,
    `lineage.current_batch`). This reading is what tells `batch-frozen` from
    everything after it, and what a freeze interrupted before its analysis task
    landed is repaired against. It says nothing about currency on its own —
    every caller asks it of a batch already known to be current.

    The stage ends at a completed analysis task, never at a file that exists.
    Two things can show one, and they are checked in that order of authority:

    - The analysis task itself, on the machine that has it
      (`_analysis_completion`, which identifies the file as this batch's
      generated task before reading its status). This is the primary reading,
      and the only one anything is derived from: the contract ends the stage
      after its analysis task "completes successfully" (Data layout).
    - The committed closure record, everywhere else. `.ai-tasks/` is
      machine-local and ignored, so on a fresh clone the lifecycle simply
      cannot be read; the record is what the controller publishes from the
      completed task so the stage's end travels with the repository instead of
      deadlocking every other checkout on a batch it cannot see finish.

    `findings.md` gates both, and ends nothing by itself. The analysis task
    writes its dispositions while it is still being developed and reviewed, so
    draft findings beside a pending, in-progress, or in-final-review task are
    not a completed analysis — reading them as one would release unreviewed
    dispositions to the admission gate, on this machine or any other.

    A task that is present and unfinished holds the stage open even against a
    closure record: local lifecycle can contradict the committed record only by
    being more conservative than it. A task file that cannot be read counts as
    in flight — an unreadable lifecycle has not been shown to have finished.
    """

    if not batch.findings_recorded:
        return True
    local = _analysis_completion(config, batch)
    if local is not None:
        return not local
    # No lifecycle to read here — the ordinary case on every machine but one,
    # and also a manifest that names no task at all. The committed record is
    # then the only thing that can answer.
    return read_closure(config, batch) is None


def _analysis_completion(config: EvolutionConfig, batch: Batch) -> bool | None:
    """This machine's reading of the batch's analysis lifecycle, or None when it
    has no such task.

    One reading for both callers, because the guard and the record that attests
    to it must not be able to disagree: `awaiting_analysis` decides whether the
    analysis stage is over, and `record_closures` publishes that same answer
    to every other machine. The manifest is what says which task is meant to
    analyze this batch, so the spec — and the identity check it carries — is
    derived from the manifest rather than from whatever happens to sit at the
    task's path.
    """

    task_id = batch.analysis_task_id
    if task_id is None:
        return None
    spec = _task_spec(config, batch.manifest, task_id=task_id, batch_id=batch.batch_id)
    return analysis_task.completion(config, spec)


def record_closures(config: EvolutionConfig, *, now: datetime) -> tuple[str, ...]:
    """Publish the closure record for every batch this machine can prove done.

    The write that makes `awaiting_analysis`'s second reading possible. It runs
    on this machine because this is where the analysis task lives; it writes
    into the versioned batch directory because every other machine needs the
    answer and has no way to derive it. Only completion observed from the
    task's own lifecycle is published — nothing here infers closure from a
    file's existence, and nothing reads a lifecycle off a file that has not been
    identified as the task the manifest names (`_analysis_completion`). This
    record is the one thing another machine cannot check for itself, so
    attesting to it from an unverified artifact would export a local mistake as
    committed proof.

    Idempotent by the record's presence, and safe to run over every batch: a
    batch whose analysis finished before this record existed is closed by the
    next run that sees its completed task.

    Package-public because the admission operations in `experiments.py` open with
    it for the same reason the freeze does: the analysis stage has to be settled
    before either decides anything, and a second way of settling it would let
    them disagree about whether a batch's dispositions are released yet.
    """

    recorded: list[tuple[str, str]] = []
    for batch in load_batches(config):
        task_id = batch.analysis_task_id
        if task_id is None or not batch.findings_recorded:
            continue
        if _analysis_completion(config, batch) is not True:
            continue
        if read_closure(config, batch) is not None:
            continue
        _write_closure(config, batch, task_id=task_id, now=now)
        recorded.append((batch.batch_id, task_id))

    append_records(
        config,
        [
            build_record(
                RECORD_BATCH_ANALYZED,
                recorded_at=format_rfc3339(now),
                batch_id=batch_id,
                task_id=task_id,
            )
            for batch_id, task_id in recorded
        ],
    )
    return tuple(batch_id for batch_id, _ in recorded)


def _write_closure(config: EvolutionConfig, batch: Batch, *, task_id: str, now: datetime) -> Path:
    """Write one closure record, validated before it is published.

    Committed content, so it is validated against its versioned schema on the
    way out as well as on the way in, and written atomically: a half-written
    record is one `read_closure` would refuse, which would leave a finished
    batch reading as open on every machine but this one.
    """

    record = {
        "schema_version": CLOSURE_SCHEMA_VERSION,
        "batch_id": batch.batch_id,
        "analysis_task_id": task_id,
        "closed_at": format_rfc3339(now),
    }
    validate_or_raise(
        record,
        load_schema(config.schema_path(CLOSURE_SCHEMA_FILENAME)),
        description=f"batch closure record for {batch.batch_id}",
    )
    path = batch.closure_path
    atomic_write_text(path, json.dumps(record, indent=2, sort_keys=True) + "\n")
    return path


def batch_awaiting_analysis(config: EvolutionConfig, *, batches: list[Batch] | None = None) -> Batch | None:
    """The current batch awaiting analysis, if any.

    Which batch is current is settled first, and from the whole lineage
    (`lineage.current_batch`): two of them, or one whose terminal record its
    experiments contradict, leaves no single cycle an interrupted freeze could
    belong to. Asking "which current batch still awaits analysis" instead, as
    this once did, answers with one of two current batches whenever they are at
    different stages — and the repair below then completes that one and reports
    a normal run.

    Only a current batch is asked about. A concluded batch awaits nothing — its
    outcome ended the whole cycle — so a freeze interrupted before its analysis
    task landed is left as the history it is rather than repaired into a task
    for a cohort nobody is going to analyze.

    `batches` lets a caller that has already loaded and validated the manifests
    reuse them, so a reader deriving several facts at once does not re-read the
    batch directory per fact.
    """

    return _still_awaiting(config, current_batch(config, batches=batches))


def _still_awaiting(config: EvolutionConfig, current: Batch | None) -> Batch | None:
    """The current batch, when its analysis stage has not ended.

    One spelling for the two callers: the public reading above, which derives
    the lineage for itself, and the repair below, which is handed the derivation
    its write path already made.
    """

    if current is None or not awaiting_analysis(config, current):
        return None
    return current


def _pool_reason(state: EvolutionState, *, current_batch_id: str | None) -> str | None:
    """Why no batch forms, asked before any membership is selected.

    The tests that are about the change cycle and the pool rather than about a
    batch's members, split out so the two callers that must know whether they
    need a proposal at all can ask the same rule the policy applies. A second
    spelling here is how a freeze comes to demand an offer for a pool that has
    nothing in it.
    """

    if current_batch_id is not None:
        return REASON_CURRENT_BATCH
    if not state.feed_exhausted:
        return REASON_POOL_INCOMPLETE
    if not state.pending:
        return REASON_POOL_EMPTY
    return None


def evaluate_admission(
    config: EvolutionConfig,
    state: EvolutionState,
    *,
    proposal: BatchProposal | None = None,
    now: datetime,
    forced: bool = False,
    current_batch_id: str | None = None,
) -> AdmissionDecision:
    """Apply the configured admission policy to the batch on offer.

    Pure, and the only place that decides. The order of the tests is the policy:

    1. A current batch dominates everything (invariant 14). It is current from
       its freeze until its outcome is recorded, so the pool keeps accumulating
       through analysis, admission, and the whole change cycle, and the next
       cohort forms only once that batch has concluded.
    2. The pool must be the whole eligible set. `state.feed_exhausted` is false
       when discovery stopped before the feed was drained — or has never run;
       the count is then a prefix, and every threshold below would be measured
       against a number a page limit chose. Not an error — the next run
       continues from the cursor and admits the complete pool.
    3. An empty pool has no evidence at all.
    4. With no batch offered there is no membership to measure, and saying so is
       not the same as saying the offer would be empty.
    5. A pool none of whose reports belong to this base is a different answer
       from an empty one, and stating it as `pool-empty` would send an operator
       looking for evaluations they already have.
    6. The target forms a batch on its own — a `--force` alongside it changes
       nothing, so the batch is not recorded as forced.
    7. The configured minimum is a floor no path crosses. `--force` waives the
       *target*, never the minimum (contract: normal workflow).
    8. `max_wait_days` since the oldest *member* was imported releases a batch
       that has met the minimum, so evidence does not age out waiting for a
       target that may never arrive. At `max_wait_days = 0` the minimum alone is
       enough, provided the oldest timestamp is not in the future: a source
       clock ahead of this one leaves the age rule no honest answer, and
       `waited_days` stays negative so the skew is visible rather than assumed
       away.
    9. Otherwise a human may force it, with a justification.

    **The proposal arrives as data.** Tests 5 to 9 are about what a freeze would
    form, so they measure the offer's membership and not the pool it was
    selected out of. The offer is handed in rather than loaded here, which is
    what keeps this the pure single decision point: a caller with no offer to
    measure is answered by tests 1 to 4 and requires one for itself
    (`_pool_reason`, `POOL_ONLY_REASONS`).

    The age rule moves with the count for the same reason. A report that is not
    a member of this batch cannot age *this* batch out — under the pool's oldest
    timestamp, one long-pooled off-base report would release every later cohort
    on the day it arrived.

    The pool is read from the state rather than passed in by the caller, because
    the caller may not be the run that discovered anything: `sync` and `freeze`
    are separate commands, and a `freeze` told nothing would have to assume the
    pool is whole — which is exactly the assumption a bounded sync invalidates.
    """

    selected = proposal.selected if proposal is not None else ()
    task_count = len(selected)
    pool_count = len(state.pending)
    pooled = {entry.dedup_key: entry for entry in state.pending}
    oldest = min(
        (pooled[member.dedup_key].first_imported_at for member in selected if member.dedup_key in pooled),
        default=None,
    )
    waited = (now - parse_rfc3339(oldest)).days if oldest is not None else None

    def decision(*, freeze: bool, trigger: str | None = None, reason: str | None = None) -> AdmissionDecision:
        return AdmissionDecision(
            freeze=freeze,
            task_count=task_count,
            pool_count=pool_count,
            target=config.batch.target_task_count,
            minimum=config.batch.minimum_task_count,
            trigger=trigger,
            reason=reason,
            forced=trigger == TRIGGER_FORCED,
            oldest_pending_at=oldest,
            waited_days=waited,
            max_wait_days=config.batch.max_wait_days,
            current_batch_id=current_batch_id,
        )

    unformable = _pool_reason(state, current_batch_id=current_batch_id)
    if unformable is not None:
        return decision(freeze=False, reason=unformable)
    if proposal is None:
        return decision(freeze=False, reason=REASON_NO_PROPOSAL)
    if task_count == 0:
        return decision(freeze=False, reason=REASON_POOL_OFF_BASE)
    if task_count >= config.batch.target_task_count:
        return decision(freeze=True, trigger=TRIGGER_TARGET)
    if task_count < config.batch.minimum_task_count:
        return decision(freeze=False, reason=REASON_BELOW_MINIMUM)
    if waited is not None and waited >= config.batch.max_wait_days:
        return decision(freeze=True, trigger=TRIGGER_MAX_WAIT)
    if forced:
        return decision(freeze=True, trigger=TRIGGER_FORCED)
    return decision(freeze=False, reason=REASON_BELOW_TARGET)


def freeze(
    config: EvolutionConfig,
    *,
    now: datetime | None = None,
    forced: bool = False,
    justification: str | None = None,
    expect: str | None = None,
) -> FreezeResult:
    """Fix the standing proposal as a batch, and create its pending analysis task.

    The human's own command, and the first durable write of the whole
    formation: the manifest, the report claim and the analysis task land here,
    together and in that order, exactly as they did before the formation split
    in two.

    `expect` matters more here than anywhere else, because the lock does not
    span the human window: the proposal was built by one command and is fixed by
    another, with a person in between. A token taken from the reading the
    operator decided over is what makes that gap safe.
    """

    from .guards import require_expected

    # Before the lock: a forced freeze with no written reason is a request this
    # build never performs, and refusing it here keeps it from ever holding the
    # writer's lock to say so.
    _require_justified(forced, justification)
    with single_writer_lock(config):
        require_expected(config, expect)
        return freeze_locked(config, now=now, forced=forced, justification=justification)


def include(
    config: EvolutionConfig,
    repo_id: str,
    task_id: str,
    *,
    justification: str,
    actor: str,
    now: datetime | None = None,
    expect: str | None = None,
) -> MoveResult:
    """Argue one pooled task into the standing offer past the identity test.

    The only door for evidence the automatic test refuses, and invariant 1's
    sanctioned exception: a human-included member counts toward the batch
    thresholds like any other, because the judgement that a report belongs in
    this cohort is the human's to make and the configured minimum is a default
    for the automatic path. What bounds it is the record rather than a rule —
    the manifest states the two counts apart, and every inclusion carries the
    words and the name of whoever made it.

    That is also why the pool's four automatic non-selections are not treated
    differently here. A revision this clone cannot resolve is answered by a
    fetch and a rebuild, and an operator who instead argues the report in is
    making a claim about the evidence rather than about the clone — recorded,
    with their name on it, which is what the exception is for.
    """

    return _move(
        config,
        repo_id,
        task_id,
        into=True,
        justification=justification,
        actor=actor,
        now=now,
        expect=expect,
    )


def exclude(
    config: EvolutionConfig,
    repo_id: str,
    task_id: str,
    *,
    justification: str,
    actor: str,
    now: datetime | None = None,
    expect: str | None = None,
) -> MoveResult:
    """Take one task out of the standing offer, on a stated human judgement.

    The direction that most needs its reason recorded: an exclusion removes
    evidence the identity test had already selected, so it is the move able to
    shape what a cohort says. `human-excluded` is its own non-selection reason
    for that — a reader of the frozen manifest can tell a judgement from a fact
    about the record or the clone — and the words go beside it.

    Refused where the task is already out. A human judgement that changes no
    answer is not one the record should carry: it would tell a reader somebody
    decided what the machine had already decided, and the same rule drops one a
    rebuild finds redundant (`_select`).
    """

    return _move(
        config,
        repo_id,
        task_id,
        into=False,
        justification=justification,
        actor=actor,
        now=now,
        expect=expect,
    )


def _move(
    config: EvolutionConfig,
    repo_id: str,
    task_id: str,
    *,
    into: bool,
    justification: str,
    actor: str,
    now: datetime | None,
    expect: str | None,
) -> MoveResult:
    """Both directions of the human window, under one lock and one preflight.

    The lock is short and the window is not: this is one command in the middle
    of an unbounded human pause, so `expect` is checked first under it exactly
    as every other guarded operation checks its own.
    """

    from .guards import require_expected

    _require_identified(repo_id, task_id)
    _require_stated(justification, actor)
    moment = _require_aware(now) if now is not None else datetime.now(timezone.utc)
    with single_writer_lock(config):
        require_expected(config, expect)
        return _move_locked(
            config,
            repo_id,
            task_id,
            into=into,
            justification=justification.strip(),
            actor=actor.strip(),
            now=moment,
        )


def _move_locked(
    config: EvolutionConfig,
    repo_id: str,
    task_id: str,
    *,
    into: bool,
    justification: str,
    actor: str,
    now: datetime,
) -> MoveResult:
    """Apply one move to the offer on disk, or say why this state takes none.

    The preflight is the two rules `moves_refusal` reads, in its order and from
    the same functions: a frozen batch first — membership stops being arguable
    the moment a manifest exists (invariant 3) — then the offer, re-read against
    the live line and pool the way the freeze re-reads it. Arguing with an offer
    the freeze would refuse as stale is work the rebuild discards, and the
    rebuild carries every recorded move forward, so the operator loses nothing by
    being sent there first.

    A damaged offer raises out of `_require_current_proposal` exactly as it does
    for the freeze, rather than reading as no offer at all: this is the write
    side, and "there is nothing here" and "what is here cannot be read" send an
    operator to different places.
    """

    running = current_batch(config, batches=load_batches(config))
    if running is not None:
        raise AlreadyAnswered(frozen_membership_refusal(running.batch_id))
    state = load_state(config)
    proposal = _require_current_proposal(config, state)

    key = (repo_id, task_id)
    member = next((item for item in proposal.selected if item.dedup_key == key), None)
    exclusion = next((item for item in proposal.not_selected if item.dedup_key == key), None)
    # One answer per task, which the offer's own parser guarantees: whichever
    # list holds it, that entry names the reports the move is about.
    answered = member if member is not None else exclusion
    if answered is None:
        raise UnknownSubject(_unanswered_refusal(config, repo_id, task_id))

    moved: Member | Exclusion
    if into:
        if member is not None and member.mode == MODE_AUTO:
            raise AlreadyAnswered(
                f"{repo_id}/{task_id} is already a member of the offered batch, selected by the identity test; "
                "an inclusion records a human overriding that test, and one recorded here would tell a reader of "
                "the manifest that a judgement was needed where none was"
            )
        moved = Member(
            repo_id=repo_id,
            task_id=task_id,
            report_keys=answered.report_keys,
            mode=MODE_HUMAN_INCLUDED,
            justification=justification,
            actor=actor,
            moved_at=format_rfc3339(now),
        )
        # The move already on record for this task, in this direction — never
        # the automatic answer, which both branches have just refused.
        recorded: Member | Exclusion | None = member
    else:
        if exclusion is not None and exclusion.reason not in HUMAN_REASONS:
            raise AlreadyAnswered(
                f"{repo_id}/{task_id} is already left out of the offered batch ({exclusion.reason}); a human "
                "exclusion takes out evidence the identity test selected, and recording one over a report the "
                "test already refuses would report a judgement as the reason a fact is"
            )
        moved = Exclusion(
            repo_id=repo_id,
            task_id=task_id,
            report_keys=answered.report_keys,
            reason=REASON_HUMAN_EXCLUDED,
            justification=justification,
            actor=actor,
            moved_at=format_rfc3339(now),
        )
        recorded = exclusion

    # The redo: the same move, already recorded. Compared without its moment,
    # because restamping the clock is the whole of what a redo would change —
    # and this file is in the state digest, so it would refuse an outstanding
    # token over a decision nobody made.
    restated = recorded is not None
    unchanged = restated and replace(moved, moved_at=recorded.moved_at) == recorded
    updated = proposal if unchanged else _with_move(proposal, moved)
    path = config.proposal_path if unchanged else save_proposal(config, updated)
    return MoveResult(
        proposal=updated,
        proposal_path=path,
        decision=evaluate_admission(config, state, proposal=updated, now=now),
        repo_id=repo_id,
        task_id=task_id,
        move=MODE_HUMAN_INCLUDED if into else REASON_HUMAN_EXCLUDED,
        changed=not unchanged,
        restated=restated,
    )


def _with_move(proposal: BatchProposal, moved: Member | Exclusion) -> BatchProposal:
    """The offer with one task answered the other way.

    Dropped from both lists and re-added to one, so a move is a single decision
    rather than an insert beside a stale answer: the two lists together are the
    denominator, and a task in both is the contradiction the parser refuses.
    """

    key = moved.dedup_key
    selected = tuple(item for item in proposal.selected if item.dedup_key != key)
    not_selected = tuple(item for item in proposal.not_selected if item.dedup_key != key)
    if isinstance(moved, Member):
        return replace(proposal, selected=(*selected, moved), not_selected=not_selected)
    return replace(proposal, selected=selected, not_selected=(*not_selected, moved))


def _require_identified(repo_id: str, task_id: str) -> None:
    """A move argues about one named task, and both halves of that name are its.

    Neither half is validated any further here. The import contract bounds them
    only as non-empty strings — a slash, a space, a colon are all legal in
    either — so the only thing a caller can get wrong before the pool is
    consulted is stating nothing at all. Everything else is answered by whether
    the offer holds that task (`_unanswered_refusal`), which is the one authority
    on what its names are.

    Refused before the lock, for the reason `_require_stated` is: a caller that
    named no task is not waiting on the workspace.
    """

    missing = sorted(name for name, value in (("repo_id", repo_id), ("task_id", task_id)) if not (value or "").strip())
    if missing:
        raise InputError(
            f"a move names one pooled task by both halves of its identity, and {missing} is blank; the offer "
            "states the two apart, and so does every record a move lands in"
        )


def _require_stated(justification: str, actor: str) -> None:
    """A move nobody is named for records no judgement.

    Refused before the lock, and before anything is read: the record enforces
    the same rule on the way to disk (`manifests.attribution_problem`), and
    holding the writer's lock to discover a caller stated neither is a refusal
    taken in the wrong place.
    """

    missing = sorted(
        name for name, value in (("justification", justification), ("actor", actor)) if not (value or "").strip()
    )
    if missing:
        raise InputError(
            f"a human move must state {missing}; it is invariant 1's recorded exception and it survives into the "
            "frozen manifest, so a move with no words and no name is not a judgement anybody can read back"
        )


def _unanswered_refusal(config: EvolutionConfig, repo_id: str, task_id: str) -> str:
    return (
        f"the standing offer in {config.proposal_path} answers for no task called {repo_id}/{task_id}; a move "
        "argues with a membership somebody was offered, so check the names in that file — or run "
        "`mandrel evolution start` if the report has yet to be imported"
    )


def start(
    config: EvolutionConfig,
    feed: ReportFeed,
    *,
    now: datetime | None = None,
    expect: str | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> StartResult:
    """The human-triggered entry point: close, repair, import, then propose.

    One lock covers all four, so the proposal describes exactly the pool the
    import produced. A feed failure aborts before any proposal is built — an
    unreachable feed means the pool's completeness is unknown, and offering a
    batch out of an unknown pool is what invariant 3 forbids one commit later.

    Closing and repair run *first*, and touch no feed. A batch whose analysis
    finished is still recorded open until this machine publishes that fact, and
    an open batch whose state transition or analysis task never landed is a
    frozen manifest with no way to be analyzed; making either wait for a
    reachable feed would leave the repository stuck on an outage that has
    nothing to do with it.

    Nothing here forms a cohort. `start` used to end in the freeze, which put
    the pool, the manifest, the report claim and the analysis task in one
    uninterruptible call with no human between them. It now ends in a proposal,
    and `freeze` is the human's own command — so the whole window in which
    membership can still be argued with falls before the first durable write
    rather than beside it. `--force`
    went with it: waiving the configured target is a decision about forming a
    batch, and the poll no longer forms one.

    `expect` is the `state_revision` of the reading this run was decided from,
    and it is checked first thing under the lock like every other guarded
    operation's. Building a proposal is a write — it moves the token — so a
    caller acting on a reading somebody else has moved is refused here rather
    than silently overwriting the selection that reading described.
    """

    # Locally imported: `guards` reads this module's closures and stage
    # observation, so the dependency runs that way and the token check is the one
    # thing needed back.
    from .guards import require_expected

    moment = _require_aware(now) if now is not None else datetime.now(timezone.utc)
    with single_writer_lock(config):
        require_expected(config, expect)
        prepared = _prepare_locked(config, now=moment)
        imported = sync_locked(config, feed, page_size=page_size, max_pages=max_pages)
        proposed = _propose_locked(config, prepared, now=moment)
    return StartResult(sync=imported, proposal=proposed)


def build_proposal(config: EvolutionConfig, *, now: datetime | None = None) -> ProposalResult:
    """Offer a batch out of the pool as it already stands, without a feed.

    The same step `start` ends in, reachable on its own for a caller that
    already has the pool it wants offered.

    **No CLI verb exposes it, and no refusal names it.** An operator's rebuild is
    `mandrel evolution start`, whatever went stale: a command that offered a
    cohort without discovery would select out of a pool nothing had just proved
    whole, which is the completeness a freeze may not assume (invariants 1
    and 2). A recovery pointing at a verb this build has no parser for is worse
    than none, so the sentences that name the rebuild name only `start`.
    """

    moment = _require_aware(now) if now is not None else datetime.now(timezone.utc)
    with single_writer_lock(config):
        return _build_proposal_locked(config, now=moment)


def _build_proposal_locked(config: EvolutionConfig, *, now: datetime) -> ProposalResult:
    """Close, repair, and then select — for a caller that already holds the lock."""

    return _propose_locked(config, _prepare_locked(config, now=now), now=now)


@dataclass(frozen=True)
class _Prepared:
    """What the repository said before anything was selected out of it."""

    running: Batch | None
    closed: tuple[str, ...]
    repaired: FreezeResult | None


def _prepare_locked(config: EvolutionConfig, *, now: datetime) -> _Prepared:
    """Settle the cycle, publish what finished, and finish what was interrupted.

    First, and before any feed is contacted, for the reason it always was: a
    batch whose analysis finished is recorded open until this machine publishes
    that fact, and an open batch whose state transition or analysis task never
    landed is a frozen manifest with no way to be analyzed. Making either wait
    for a reachable feed would strand the repository on an outage that has
    nothing to do with the repair.

    Which batch is current is read once, here, and carried: it is the question
    the closure, the repair and the selection all hang off, and a second reading
    could answer differently from the one the first two acted on.
    """

    known = load_batches(config)
    running = current_batch(config, batches=known)
    closed = record_closures(config, now=now)
    return _Prepared(running=running, closed=closed, repaired=_reconcile_locked(config, now=now, current=running))


def _propose_locked(config: EvolutionConfig, prepared: _Prepared, *, now: datetime) -> ProposalResult:
    """Offer a batch out of the pool as it now stands, or say why there is none."""

    running, closed, repaired = prepared.running, prepared.closed, prepared.repaired
    if repaired is not None:
        return ProposalResult(
            decision=repaired.decision,
            current_batch_id=repaired.current_batch_id,
            analysis_task_id=repaired.analysis_task_id,
            manifest_path=repaired.manifest_path,
            completed=repaired.completed,
            closed_batch_ids=closed,
        )

    state = load_state(config)
    running_id = running.batch_id if running else None
    if running is not None or not state.pending:
        # Nothing to offer, and nothing left standing that says otherwise: a
        # proposal outliving the pool it selected over would be frozen against
        # reports the freeze then could not find. An *incomplete* pool is not
        # in this set on purpose — its reports are real and selecting over them
        # is a useful preview, and admission refuses the freeze on completeness
        # whatever the offer says.
        cleared = clear_proposal(config)
        return ProposalResult(
            decision=evaluate_admission(config, state, now=now, current_batch_id=running_id),
            current_batch_id=running_id,
            analysis_task_id=running.analysis_task_id if running else None,
            manifest_path=running.manifest_path if running else None,
            completed=(COMPLETED_PROPOSAL,) if cleared else (),
            closed_batch_ids=closed,
        )

    # Selected first, then measured: the thresholds are about the batch this
    # offer would form, so the offer has to exist before there is anything to
    # apply them to.
    proposal = _select(config, state, now=now, standing=_standing(config))
    decision = evaluate_admission(config, state, proposal=proposal, now=now, current_batch_id=running_id)
    path = save_proposal(config, proposal)
    return ProposalResult(decision=decision, proposal=proposal, proposal_path=path, closed_batch_ids=closed)


def _standing(config: EvolutionConfig) -> BatchProposal | None:
    """The offer already on disk, or nothing readable.

    A damaged proposal reads as none here, and that is the repair the refusal
    everywhere else names: nothing durable stands on an offer, so rebuilding
    over it costs the operator nothing and saves them deleting a file by hand.
    Everything that would *act* on a proposal — the freeze, and the reading the
    console publishes — still refuses it rather than guessing.
    """

    try:
        return load_proposal(config)
    except ProposalError:
        return None


def _select(
    config: EvolutionConfig,
    state: EvolutionState,
    *,
    now: datetime,
    standing: BatchProposal | None,
) -> BatchProposal:
    """The pool read against the base, as one offer.

    The base is the source line's tip read here, at the batch's creation, and it
    is carried to the freeze rather than resolved again there: a line that moves
    during the human window has to be visible as a mismatch, never as a quietly
    different selection (invariants 8 and 15).

    **Membership is the base's deployable canonical identity, and nothing else.**
    A pooled task is a member when the protocol its evaluated target was running
    is the protocol this base carries — `lockfile.canonical_identity`, the one
    derivation this repository has for *are these two commits the same protocol*,
    shared with deploy placement and release assessment so a report and a target
    are never judged by two different rules. Commit equality would be the wrong
    test and a much narrower one: the source line moves for work under
    `mandrel/`, `tests/` and `docs/` that no target ever receives, so a report
    produced one ordinary commit later than the base would be excluded from a
    cohort it belongs to.

    Every task the pool holds is answered, and the ones that are not members are
    recorded with why (`not_selected`). That record is the batch's denominator:
    a manifest saying nine of eleven is a different document from one saying
    nine, and the two the reader cannot see are exactly where a cohort's
    coherence claim would go wrong.

    **A human move survives the rebuild that would otherwise discard it.** The
    ordinary poll overwrites the standing offer, and a recorded judgement is not
    a reading: an operator who argued a report in on Monday and synced on
    Tuesday would find their decision gone with nothing said. So every move the
    standing offer carries is re-applied here (`_carried`), and the one thing
    that drops one is the automatic answer already agreeing with it — a move is
    the human overriding the identity test, and where the test no longer needs
    overriding there is no judgement left to record. Both directions, both for
    that reason: a report the base now selects is `auto` and not
    `human-included`, and a report the base now refuses is refused with the
    machine's own reason rather than a person's.

    The alternative — refusing to rebuild over an offer that carries a move —
    was rejected because it strands the workspace: `start` is the only rebuild
    every refusal names, the freeze refuses an offer the pool has outgrown, and
    a poll that would not overwrite leaves nothing that can be frozen and no
    verb to clear the way.

    An offer that selects what the standing one selected, at the same base, *is*
    the standing one and keeps its `built_at`. "What it selected" is a question
    about the decisions and not about the order they were assembled in: this
    loop follows the pool's arrival order, the standing offer is parsed back from
    a file, and the two need not run in the same direction — so the comparison
    rests on `BatchProposal` holding both lists in one order of its own.
    Stamping the clock on an unchanged offer would move `state_revision` on every
    poll that changed nothing — and this file is in that digest, so an operator's
    outstanding token would be refused by somebody else's no-op `start`. That is
    the false positive the watched-ref set was trimmed to avoid, and it would be
    reintroduced here.
    """

    base = _source_line_revision(config)
    identities = CanonicalIdentities(config.repo_root, deploys=deploys_canonical_path)
    if identities.of(base) is None:
        raise CheckoutUnusable(
            f"this checkout resolves {config.source_line.ref} to {base} but cannot read that commit's canonical "
            "tree, so it cannot say which protocol the batch would be formed at; every membership test compares "
            "against it, and an unreadable base would answer 'different protocol' for every pooled report "
            "(invariants 8, 15)"
        )

    moves = _carried(standing)
    selected: list[Member] = []
    not_selected: list[Exclusion] = []
    for entry in _batched_entries(state):
        keys = tuple(sorted(entry.report_keys()))
        reason = _non_selection(config, entry, base=base, identities=identities)
        move = moves.get(entry.dedup_key)
        if isinstance(move, Member) and reason is not None:
            selected.append(replace(move, report_keys=keys))
        elif isinstance(move, Exclusion) and reason is None:
            not_selected.append(replace(move, report_keys=keys))
        elif reason is None:
            selected.append(Member(repo_id=entry.repo_id, task_id=entry.task_id, report_keys=keys))
        else:
            not_selected.append(
                Exclusion(repo_id=entry.repo_id, task_id=entry.task_id, report_keys=keys, reason=reason)
            )

    offered = BatchProposal(
        built_at=format_rfc3339(now),
        source_line_ref=config.source_line.ref,
        base_revision=base,
        selected=tuple(selected),
        not_selected=tuple(not_selected),
    )
    if standing is not None and replace(offered, built_at=standing.built_at) == standing:
        return standing
    return offered


def _carried(standing: BatchProposal | None) -> dict[tuple[str, str], Member | Exclusion]:
    """The human moves the standing offer records, by the task they were made about.

    Only the moves: an automatic answer is re-derived from the base and the pool
    every time, so carrying one would pin an answer to the reading that produced
    it rather than to the evidence.

    Keyed by `(repo_id, task_id)` and not by the reports the task owned, because
    a judgement is about the task. A rerun arriving under a member a human
    argued in does not un-make the argument — the offer's own reports are
    refreshed from the pool when the move is re-applied, which is the same set
    the freeze would put in the manifest.
    """

    if standing is None:
        return {}
    moves: dict[tuple[str, str], Member | Exclusion] = {
        member.dedup_key: member for member in standing.selected if member.mode == MODE_HUMAN_INCLUDED
    }
    moves.update(
        {item.dedup_key: item for item in standing.not_selected if item.reason in HUMAN_REASONS}
    )
    return moves


def _non_selection(
    config: EvolutionConfig,
    entry: PoolEntry,
    *,
    base: str,
    identities: CanonicalIdentities,
) -> str | None:
    """Why this pooled task is not a member of a batch formed at `base`, or None
    where it is.

    **Every report the entry owns is asked, and all of them have to answer the
    base.** The revision asked about is each report's
    `provenance.effective_revision` — the evaluated target's own deploy-lock
    commit, which is what says which protocol produced the work. Invariant 5
    states membership per report; membership is *decided* per completed task
    (invariant 1), and a freeze puts every report of a member into the manifest
    stamped with that one decision. So the decision has to be true of all of
    them: testing the primary alone would freeze an off-base rerun marked
    `auto`, a claim about that report nobody derived, and it would make the
    answer depend on which of two disagreeing evaluations happened to arrive
    last.

    A rerun is a second reading of the same run rather than a second run
    (invariant 4), so in a coherent pool every report of a task states the same
    production revision and this test is the primary's. Where they disagree the
    task is not a member, and the human window is the door — the same door the
    reports that state nothing at all go through.

    The reason recorded is the **first** failure over the entry's reports, taken
    in a fixed order (`_entry_reports`: the primary, then the reruns by
    evaluation order). One task gets one reason, so it has to be a report's own
    reason to keep the denominator's portability intact: `revision-absent` and
    `revision-malformed` are settled identically in every clone, while
    `revision-unresolvable` is a fact about this one.

    The four ways to fail, kept apart because an operator's next move differs
    for each:

    - **absent** — the report states no effective revision, so there is nothing
      to derive an identity from. Four of the five reports in this repository's
      live pool are in exactly this state, which is why it is a first-class
      answer rather than a corner: it is the case a human inclusion exists for.
    - **malformed** — a revision is stated and it is not a commit id. A fact
      about the record: Git would resolve `HEAD`, a branch or an abbreviation
      just as readily, and that answer would be a reading of where *this*
      checkout stands rather than of what that target held.
    - **unresolvable** — a commit id this checkout cannot read the canonical
      tree of. A fact about the clone, which a fetch fixes and which another
      clone would not report at all.
    - **differs** — read, and carrying some other protocol than the base's.

    Both sides are known readable before anything is compared: the base is
    refused outright above, and an unresolvable member is answered before this
    gets here. That is what `CanonicalIdentities.same` guards against on its own
    — two revisions nobody can read are not thereby the same protocol — and
    asking it here rather than comparing the mappings by hand keeps one spelling
    of the equality.
    """

    for ref in _entry_reports(entry):
        stated = _staged_effective_revision(config, entry, ref)
        if stated is None:
            return REASON_REVISION_ABSENT
        if not is_object_id(stated):
            return REASON_REVISION_MALFORMED
        if identities.of(stated) is None:
            return REASON_REVISION_UNRESOLVABLE
        if not identities.same(base, stated):
            return REASON_IDENTITY_DIFFERS
    return None


def _entry_reports(entry: PoolEntry) -> tuple[ReportRef, ...]:
    """Every report one pooled task owns, in one fixed order.

    The primary first — it is the latest evaluation, so its answer is the task's
    headline answer — then the reruns in evaluation order. One spelling, shared
    by the membership test and the freeze, because the set a task is judged over
    and the set frozen into its manifest have to be the same set: a report the
    test never saw would enter the batch carrying a decision it was not part of.
    """

    return (entry.primary, *sorted(entry.reruns, key=lambda ref: (ref.sequence, ref.report_key)))


def _staged_effective_revision(config: EvolutionConfig, entry: PoolEntry, ref: ReportRef) -> str | None:
    """The protocol revision one staged report was produced at, as its bundle
    states it — or None where the report states none.

    Read from the staged record rather than from the pool, because the pool
    keeps identity and hashes and no report content: duplicating provenance into
    it would give the same fact two homes that can disagree, and the bundle is
    where every other reader (`_manifest_report`, the assessment cohorts) takes
    it from.

    **Read here, verified at the freeze.** This is a selection over ignored
    runtime state and nothing durable stands on it, so it does not re-hash the
    bundle or re-check the artifact bodies — `_verified_bundle` does that at the
    freeze, which is the last moment before the membership becomes immutable and
    the only moment where catching it matters.

    A bundle this machine cannot read at all stops the offer rather than being
    excluded from it. It is damage in machine-local state — the pool asserts
    evidence that is not there — and the freeze already stops on it for every
    selected report; letting the poll quietly record it as a non-member would
    turn a broken workspace into a smaller batch nobody was told about.
    """

    path = config.repo_root / ref.artifacts_path / REPORT_JSON_FILENAME
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(
            f"cannot read the staged bundle for report {ref.report_key} at {path}: {exc}; the pool names it as "
            f"the evidence for {entry.repo_id}/{entry.task_id}, and a batch cannot be selected around a report "
            "whose provenance this machine no longer holds; import the reports again"
        ) from exc
    provenance = record.get("provenance") if isinstance(record, dict) else None
    stated = provenance.get("effective_revision") if isinstance(provenance, dict) else None
    return stated if isinstance(stated, str) and stated.strip() else None


def freeze_locked(
    config: EvolutionConfig,
    *,
    now: datetime | None = None,
    forced: bool = False,
    justification: str | None = None,
) -> FreezeResult:
    """`freeze` without acquiring the lock, for a caller that already holds it.

    The order below is the safety argument. The questions that need no
    membership are asked first — a running cycle, an undrained feed, an empty
    pool — because "not enough evidence yet" is the contract's normal outcome
    and answering it needs no offer at all. The offer is then required and
    re-checked against what is actually here (the line and the pool), because
    everything below it measures the offer: the thresholds count the batch this
    freeze would form, so a membership nobody may freeze is not a membership
    they can be applied to. Every staged bundle is re-verified last, on the way
    into the manifest, before the first durable byte is written.
    """

    _require_justified(forced, justification)
    moment = _require_aware(now) if now is not None else datetime.now(timezone.utc)

    batches = load_batches(config)
    # Invariant 14 first, through the derivation `status` reads: one current
    # batch, and its terminal record agreeing with the experiments it concludes
    # over. Nothing below writes into a repository that cannot answer that —
    # neither the closure records, nor the repair, nor a new cohort — because
    # each of the three belongs to a change cycle, and an unreadable lineage
    # does not say which one.
    #
    # A current batch here does not mean one awaiting analysis: a batch stays
    # current through its experiments and rounds, which all happen after its
    # analysis closed.
    running = current_batch(config, batches=batches)

    closed = record_closures(config, now=moment)
    repaired = _reconcile_locked(config, now=moment, current=running)
    if repaired is not None:
        return replace(repaired, closed_batch_ids=closed)

    state = load_state(config)
    running_id = running.batch_id if running else None

    def no_batch(decision: AdmissionDecision) -> FreezeResult:
        return FreezeResult(
            decision=decision,
            current_batch_id=running_id,
            analysis_task_id=running.analysis_task_id if running else None,
            manifest_path=running.manifest_path if running else None,
            closed_batch_ids=closed,
        )

    if _pool_reason(state, current_batch_id=running_id) is not None:
        return no_batch(evaluate_admission(config, state, now=moment, forced=forced, current_batch_id=running_id))

    proposal = _require_current_proposal(config, state)
    decision = evaluate_admission(config, state, proposal=proposal, now=moment, forced=forced)
    if not decision.freeze:
        return no_batch(decision)

    revision = proposal.base_revision
    batch_id = next_batch_id(batches)
    task_id = analysis_task.analysis_task_id(batch_id, moment)
    if analysis_task.task_exists(config, task_id):
        raise StandingWork(
            f"cannot create the analysis task for {batch_id}: {task_id} already exists in "
            f"{analysis_task.TASKS_DIRNAME}/; resolve it before freezing"
        )

    entries = _proposed_entries(state, proposal)
    manifest = _build_manifest(
        config,
        batch_id=batch_id,
        entries=entries,
        proposal=proposal,
        created_at=moment,
        decision=decision,
        justification=justification,
        runner_revision=revision,
        analysis_task_id=task_id,
    )
    directory = _write_manifest(config, batch_id, manifest)

    _claim_reports(
        state,
        {report_key for entry in entries for report_key in entry.report_keys()},
        batch_id=batch_id,
        now=moment,
    )
    save_state(config, state)

    spec = _task_spec(config, manifest, task_id=task_id, batch_id=batch_id)
    task_file = analysis_task.write_task(config, spec)
    analysis_task.append_index_row(config, spec)

    append_records(
        config,
        [
            build_record(
                RECORD_BATCH_FROZEN,
                recorded_at=format_rfc3339(moment),
                batch_id=batch_id,
                task_id=task_id,
                revision=revision,
                detail=decision.trigger,
            )
        ],
    )
    # Last, and after the audit line: while it stands, the proposal is an offer
    # of a batch that now exists, and a second freeze reading it would try to
    # claim reports this one already claimed. An interruption before this point
    # leaves it for `_complete_freeze` to drop.
    clear_proposal(config)

    return FreezeResult(
        decision=decision,
        batch_id=batch_id,
        manifest_path=directory / MANIFEST_FILENAME,
        analysis_task_id=task_id,
        analysis_task_path=task_file,
        closed_batch_ids=closed,
    )


def _reconcile_locked(config: EvolutionConfig, *, now: datetime, current: Batch | None) -> FreezeResult | None:
    """Finish an interrupted freeze of the current batch, or report there is none.

    Reads only what is already on disk — the manifests, the runtime state, and
    `.ai-tasks/` — so a caller can repair before it has a feed, or without one
    at all.

    Which batch is current is the caller's one derivation rather than a reading
    of its own: a repair writes, so the cycle it belongs to is settled before
    anything is selected, and not by a second definition of "current".
    """

    unfinished = _still_awaiting(config, current)
    if unfinished is None:
        return None

    present = state_exists(config)
    state = load_state(config)
    completed = _complete_freeze(config, unfinished, state, now=now, state_present=present)
    return FreezeResult(
        # No offer is measured here: a repair belongs to the batch that is
        # already current, and invariant 14 answers before any membership does.
        decision=evaluate_admission(config, state, now=now, current_batch_id=unfinished.batch_id),
        current_batch_id=unfinished.batch_id,
        analysis_task_id=unfinished.analysis_task_id,
        manifest_path=unfinished.manifest_path,
        completed=completed,
    )


def _require_justified(forced: bool, justification: str | None) -> None:
    """A forced batch is a human overriding the configured target, so the reason
    is part of the request. Refusing here keeps the force path from ever being
    silent (contract: forced sub-threshold batches require a justification)."""

    if forced and not (justification or "").strip():
        raise InputError(
            "a forced batch needs a written human justification; it waives the configured target "
            "and the manifest records why"
        )


def _require_aware(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise InputError("freeze time must be timezone-aware; a naive datetime makes the age rule guesswork")
    return moment


def _source_line_revision(config: EvolutionConfig) -> str:
    """Where the configured source line stands when the batch is proposed: its
    base, and the protocol revision governing its work.

    One commit under two readings, and they were separate fields until the base
    became a parameter of the batch. Invariant 8 is the first: the stable protocol
    revision governing an evolution task stays fixed for that task, and a
    candidate revision never governs the run that creates it — which the line
    satisfies by construction, since candidates accumulate on
    `refs/evolution/experiments/*` and the only commits this package ever puts on
    the source line are a promotion's merge and a rollback's inverse. Invariant 15
    is the second: every experiment of this batch starts from this commit, and the
    membership test that selects the batch's evidence is equality against its
    deployable canonical identity.

    What it replaced was `git describe --tags HEAD`, which was wrong twice over:
    it answered with a *tag name* where every other revision in play is a commit,
    and it described whichever branch the operator was standing on. Measured on
    this repository when it was changed, `HEAD` described as `v2.3.0` while the
    two `canonical/` trees differed (`3ed93a7a` against `70a0265e`) — so the
    field named a release the cohort was demonstrably not run under, and stayed
    wrong until somebody tagged. Taking the line's tip rather than the tag
    reachable from it is safe here for the reason it was not there: this is the
    line the project releases from, named once in `evolution/config.toml`, and
    not the branch whoever ran the freeze was on.

    Refused rather than recorded as absent when this checkout does not hold the
    line. That is the reverse of what it did while the field was provenance
    only — a freeze was then a decision about the report pool alone, and refusing
    one over an unfetched ref would have stopped a cohort forming for a reason
    that had nothing to do with the evidence. The line is now what *selects* the
    evidence, so a checkout that cannot resolve it cannot say what the batch is:
    there is no membership to test against and no base for the alternatives to be
    built on. A fetch is the answer, and it is the ordinary state of a fresh
    clone rather than a repository to repair.
    """

    revision = ref_tip(config.repo_root, config.source_line.ref)
    if revision is None:
        require_git(config.repo_root, reading=f"where {config.source_line.ref} stands")
        raise CheckoutUnusable(_unfetched_line_refusal(config))
    return revision


def _unfetched_line_refusal(config: EvolutionConfig) -> str:
    """Why a checkout without the source line can neither propose nor freeze."""

    return (
        f"this checkout does not hold {config.source_line.ref}, which {config.path} names as the source line a "
        "batch is formed at; fetch that line before freezing, since the base is recorded as an exact commit, "
        "every alternative in the batch is built on it, and its canonical identity is what selects the batch's "
        "members (invariants 8, 15)"
    )


def current_batch_refusal(batch_id: str | None) -> str:
    """Why neither half of a formation runs beside a cycle that is still going.

    One sentence for both commands (invariant 14). `start` cannot offer a cohort
    beside one whose cycle is still running, and a freeze has nothing to fix for
    the same reason — so a reader meets the same fact whichever they asked about,
    and neither is sent at the other as a repair.
    """

    return (
        f"{batch_id} is current and no outcome has been recorded for it; the pool keeps accumulating and the "
        "next cohort forms once that batch concludes (invariant 14)"
    )


def freeze_refusal(
    config: EvolutionConfig,
    state: EvolutionState,
    decision: AdmissionDecision,
    proposal: BatchProposal | None,
) -> str | None:
    """Why this state does not accept a freeze, or None where it does.

    The read-only form of `freeze_locked`'s preflight, asked by the console's
    gate (`phase.allowed_actions`) with the same records the operation is handed,
    and in the operation's own order: the questions no membership answers, then
    the offer, then the thresholds over it. Order is the whole point — the three
    fail for different people, and a gate that asked them in another order names
    a missing offer where the operation answers "not enough evidence yet", or
    predicts a refusal a freeze never reaches.

    `decision` must be the one measured over `proposal`, which is what the
    operation applies the thresholds to. A decision measured over the pool would
    make this gate advertise a freeze that admission then refuses, on a pool
    whose evidence belongs to another protocol.

    **A short batch is a refusal only where no argument lifts it.** `--force`
    waives the configured target and nothing else (`evaluate_admission`), so
    `pool-below-target` beside a standing offer is a state `freeze` accepts —
    with a justification, which is the operator's to write and not a fact about
    this state. Every other unreleased reason (an undrained feed, an empty pool,
    a pool holding nothing at this base, the minimum) holds against every
    argument, and is refused here. Which is why the force flag is not modelled:
    the gate says whether the verb may run at all, never which arguments it was
    given (contract: The gate).
    """

    if decision.reason == REASON_CURRENT_BATCH:
        return current_batch_refusal(decision.current_batch_id)
    if decision.reason in POOL_ONLY_REASONS:
        return _admission_refusal(decision)
    refusal = proposal_refusal(config, state, proposal)
    if refusal is not None:
        return refusal
    if not decision.freeze and decision.reason != REASON_BELOW_TARGET:
        return _admission_refusal(decision)
    return None


def _admission_refusal(decision: AdmissionDecision) -> str:
    """Admission's own numbers, as the sentence a refused freeze gives.

    Both counts, never one. `task_count` is what the thresholds measured and
    `pool_count` is what it was selected out of, and the difference between them
    is the whole of an operator's next move: a short *batch* out of a full pool
    is evidence about another protocol, which no amount of waiting fixes.
    """

    return (
        f"admission does not release a batch ({decision.reason}: {decision.task_count} selected of "
        f"{decision.pool_count} pooled, target {decision.target}, minimum {decision.minimum}); a freeze forms a "
        "cohort only once policy releases one, and `--force` waives the configured target rather than any of this"
    )


def proposal_refusal(
    config: EvolutionConfig,
    state: EvolutionState,
    proposal: BatchProposal | None,
) -> str | None:
    """Why the standing offer is not one this freeze may fix, as a sentence.

    The `str` projection of `proposal_failure`, kept for the console's gate,
    which composes sentences and routes on nothing (`guards.evidence_refusal`
    is the same pair for the same reason).
    """

    failure = proposal_failure(config, state, proposal)
    return None if failure is None else str(failure)


def proposal_failure(
    config: EvolutionConfig,
    state: EvolutionState,
    proposal: BatchProposal | None,
) -> EvolutionError | None:
    """Why the standing offer is not one this freeze may fix, or None where it is.

    Three things are re-read here rather than trusted, because the single-writer
    lock does not span the human window — the proposal was built by one command
    and is fixed by another, with a person and an unbounded amount of time in
    between:

    - **A proposal exists.** The freeze fixes an offer somebody made; there is
      no path from the pool straight to a manifest, which is the whole point of
      splitting the formation.
    - **The line has not moved.** The base is what selects the batch's evidence
      and what every experiment of it starts from, so a proposal built against a
      different tip describes a different batch. Refused and rebuilt, never
      re-based in place: silently taking the new tip would freeze a membership
      nobody read against a base nobody chose. (`guards.read_inputs` watches the
      same ref, so a caller passing `--expect` is already refused one step
      earlier; this covers the operator who passed none.)
    - **The pool still holds exactly what was answered for.** Every task the
      offer answered for — members and non-members alike — has to still be
      pooled with the same reports, and every pooled task has to have been
      answered for. A member claimed by another freeze, dropped, or re-imported
      under different reports is one this freeze would silently substitute for;
      a task that entered the pool after the build was never considered. Both
      sides matter because the manifest records the whole denominator, not just
      the membership: an exclusion naming reports that have since changed is a
      denominator stating something that was not so at the freeze.

    Every refusal a stale offer earns names the rebuild, because that is the only
    repair: a proposal is a reading, and a stale reading is retaken rather than
    mended. The one exception is a checkout that cannot resolve the line at all,
    which no rebuild fixes and which answers with the same fetch `_select` asks
    for.

    Read-only, and stated once for both directions: the freeze raises it and the
    console's gate reports it. Two spellings of these three rules is how a menu
    comes to advertise a freeze the operation refuses as stale.

    Six sentences and three classes, drawn where the answer differs rather than
    where the sentence does. Nothing offered is `NothingToActOn`: there is
    something to build and no reading to retake. A line this checkout cannot
    resolve is `CheckoutUnusable`, which is the one answer here that is not
    `start` — the same fetch `_select` asks for, and the same class it raises.
    Everything else is one stale reading (`ProposalError`): a moved line and a
    pool that has grown or lost a member are the same fact about the offer, that
    it no longer describes what it was built against, and the same command
    answers both. Which of them it is stays in the sentence, where an operator
    reads it; the exit an adapter routes on is `stale`, and that is what the
    contract already promised a stale proposal would be.
    """

    if proposal is None:
        return NothingToActOn(
            f"no batch proposal stands in {config.proposal_path}; a freeze fixes an offer a human has seen, so "
            "build one with `mandrel evolution start`, review its membership, and freeze that"
        )

    tip = ref_tip(config.repo_root, config.source_line.ref)
    if tip is None:
        return CheckoutUnusable(_unfetched_line_refusal(config))
    if tip != proposal.base_revision:
        return ProposalError(
            f"the proposal was built at {proposal.base_revision} on {proposal.source_line_ref} and that line now "
            f"stands at {tip}; the base is what every experiment of this batch starts from and what selects its "
            "evidence, so the offer is refused rather than re-based — run `mandrel evolution start` again and "
            "review the batch it now proposes"
        )

    considered = {item.dedup_key: item for item in (*proposal.selected, *proposal.not_selected)}
    for key in sorted(considered):
        item = considered[key]
        entry = state.find(item.repo_id, item.task_id)
        if entry is None:
            return ProposalError(
                f"the proposal answered for {item.repo_id}/{item.task_id}, which is no longer in the pending "
                "pool; rebuild the proposal with `mandrel evolution start` and review what it now offers"
            )
        if set(entry.report_keys()) != set(item.report_keys):
            return ProposalError(
                f"{item.repo_id}/{item.task_id} holds different reports than the proposal offered; a later "
                "evaluation arrived after it was built, so rebuild it with `mandrel evolution start` and review "
                "the batch that reads"
            )
    arrived = [entry for entry in state.pending if entry.dedup_key not in considered]
    if arrived:
        names = ", ".join(f"{entry.repo_id}/{entry.task_id}" for entry in sorted(arrived, key=lambda e: e.dedup_key))
        return ProposalError(
            f"the pool holds {len(arrived)} task(s) the proposal never considered ({names}); a batch states what "
            "it was selected out of, so rebuild the proposal with `mandrel evolution start` rather than freezing "
            "a denominator that predates them"
        )
    return None


def frozen_membership_refusal(batch_id: str) -> str:
    """Why the human window is closed once a manifest exists.

    A different fact from `current_batch_refusal`, which is about forming the
    *next* cohort: this one is about the batch that already exists. Its
    membership was fixed by the freeze and a frozen manifest is immutable
    (invariant 3), so there is nothing here a move could act on — not a policy
    that will relent when the cycle ends.
    """

    return (
        f"the membership of {batch_id} was fixed when it was frozen and its manifest is immutable (invariant 3); "
        "a report is argued in or out before the freeze, so the next window opens with the offer that follows "
        "this batch's outcome"
    )


def moves_refusal(
    config: EvolutionConfig,
    state: EvolutionState,
    proposal: BatchProposal | None,
    *,
    current_batch_id: str | None,
) -> str | None:
    """Why this state accepts no human move, or None where it does.

    Two questions and in this order, which is `_move_locked`'s own: a frozen
    batch first, because a manifest closes the window rather than delaying it,
    then the offer against the live line and pool.

    Admission is deliberately not asked. The thresholds measure what a freeze
    would form, and a short offer is exactly the state a human move exists to
    argue with — gating the window on them would refuse the operator at the one
    moment the exception is for.
    """

    if current_batch_id is not None:
        return frozen_membership_refusal(current_batch_id)
    return proposal_refusal(config, state, proposal)


def _require_current_proposal(config: EvolutionConfig, state: EvolutionState) -> BatchProposal:
    """The standing proposal, or the refusal `proposal_refusal` states, raised.

    The write side of the same predicate the console's gate reads, so an
    operator meets one sentence whether they asked `status` or ran the freeze.
    """

    proposal = load_proposal(config)
    failure = proposal_failure(config, state, proposal)
    if failure is not None:
        if proposal is not None:
            # An offer exists, so the refusal came from a rule below the first —
            # and the first of those asks Git where the source line stands. A
            # `git` that will not run answers that exactly as a checkout without
            # the ref does, and only one of the two is a fetch away. Asked here
            # rather than inside the predicate because the gate reads that one
            # and a gate may not raise (`revisions.require_git`).
            require_git(config.repo_root, reading=f"where {config.source_line.ref} stands")
        raise failure
    # `proposal_failure` answers on a missing offer before anything else, so a
    # None here is unreachable; asserted rather than assumed, since the
    # narrowing is what the caller's `proposal.base_revision` stands on.
    assert proposal is not None
    return proposal


def _proposed_entries(state: EvolutionState, proposal: BatchProposal) -> list[PoolEntry]:
    """The pool entries the proposal selected, in the manifest's order.

    Ordered exactly as `_batched_entries` orders the pool rather than as the
    proposal lists them: the manifest's order is a property of the evidence,
    while the proposal is held by task identity so that the same selection stays
    one document while a human moves entries in and out.
    """

    selected = {member.dedup_key for member in proposal.selected}
    return [entry for entry in _batched_entries(state) if entry.dedup_key in selected]


def _batched_entries(state: EvolutionState) -> list[PoolEntry]:
    """Everything eligible at freeze time, in a deterministic order.

    The whole pool, not a target-sized slice: the target is the trigger that
    forms a batch, not a cap on it, and leaving a remainder behind would make
    those tasks wait for a second full target.
    """

    return sorted(state.pending, key=lambda entry: (entry.primary.sequence, entry.repo_id, entry.task_id))


def _build_manifest(
    config: EvolutionConfig,
    *,
    batch_id: str,
    entries: list[PoolEntry],
    proposal: BatchProposal,
    created_at: datetime,
    decision: AdmissionDecision,
    justification: str | None,
    runner_revision: str,
    analysis_task_id: str,
) -> dict[str, Any]:
    """Assemble and validate the membership snapshot before anything is written.

    Every report the batched tasks own goes in, reruns included: they are
    provenance (invariant 4) and they do not raise the task count (invariant 1),
    which is derived from the distinct `(repo_id, task_id)` pairs. It is the same
    set `_non_selection` judged, through the same `_entry_reports` — each of
    these reports earned the marker it is stamped with.

    The offer's whole answer is copied in, not just the half that was selected.
    A frozen batch has to state its own denominator — what was considered, what
    was left out and why — because a cohort's coherence claim is about the set
    it was drawn from, and a manifest listing only members reads as if the pool
    held exactly them (invariant 5).
    """

    members = {member.dedup_key: member for member in proposal.selected}
    schema = load_import_schema(config)
    reports: list[dict[str, Any]] = []
    for entry in entries:
        for ref in _entry_reports(entry):
            reports.append(_manifest_report(config, entry, ref, schema, members[entry.dedup_key]))
    reports.sort(key=lambda report: (report["sequence"], report["report_key"]))

    manifest = {
        "schema_version": BATCH_SCHEMA_VERSION,
        "batch_id": batch_id,
        "created_at": format_rfc3339(created_at),
        "config_sha256": config.sha256,
        "forced": decision.forced,
        "force_justification": justification.strip() if decision.forced and justification else None,
        "runner_protocol_revision": runner_revision,
        "analysis_task_id": analysis_task_id,
        "selection": _manifest_selection(proposal),
        "reports": reports,
    }
    validate_or_raise(
        manifest,
        load_schema(config.schema_path(BATCH_SCHEMA_FILENAME)),
        description=f"batch manifest for {batch_id}",
    )
    # Published through the reader's own cross-field rules, the way
    # `save_proposal` is: the shape check above says nothing about whether the
    # counts describe these reports, and a writer that skipped the agreement
    # could freeze — immutably — a manifest this build refuses to load.
    require_coherent_selection(manifest, where=f"batch manifest for {batch_id}")
    return manifest


def _manifest_selection(proposal: BatchProposal) -> dict[str, Any]:
    """The batch's denominator, as the immutable record states it.

    **Two counts and never one total.** A batch of ten that is nine automatic
    members and one a human argued in is a different document from a batch of
    ten, and a reader has to be able to see which they are holding: the human
    inclusion is invariant 1's sanctioned exception, and an exception that
    disappears into a sum is not an accounted-for judgement. `considered` is the
    third number, and it is what makes the other two a denominator rather than
    two more sizes.
    """

    included = proposal.human_included
    return {
        "considered": proposal.considered,
        "auto_selected": len(proposal.selected) - len(included),
        "human_included": len(included),
        "not_selected": [item.to_json() for item in proposal.not_selected],
    }


def _manifest_report(
    config: EvolutionConfig,
    entry: PoolEntry,
    ref: ReportRef,
    schema: Mapping[str, Any],
    member: Member,
) -> dict[str, Any]:
    """One report's membership entry, with its cohort provenance read back from
    the staged bundle and the whole bundle re-verified against the pool.

    `selection` is the task's, repeated on each of its reports: membership is
    decided per completed task (invariant 1) and a rerun is the same task's
    evidence, so every report of a member carries the same answer. Carried on
    the report rather than derived from a lookup elsewhere in the manifest so
    that a reader holding one entry can see why it is here at all.
    """

    record = _verified_bundle(config, entry, ref, schema)
    return {
        "report_key": ref.report_key,
        "sequence": ref.sequence,
        "repo_id": entry.repo_id,
        "task_id": entry.task_id,
        "evaluation_id": ref.evaluation_id,
        "bundle_sha256": ref.bundle_sha256,
        "generated_at": ref.generated_at,
        "evaluator": record.get("evaluator"),
        "provenance": record.get("provenance"),
        "selection": {
            "mode": member.mode,
            "justification": member.justification,
            "actor": member.actor,
            "moved_at": member.moved_at,
        },
    }


def _verified_bundle(
    config: EvolutionConfig,
    entry: PoolEntry,
    ref: ReportRef,
    schema: Mapping[str, Any],
) -> Mapping[str, Any]:
    """The staged bundle, re-verified whole before its content is pinned.

    Read at freeze rather than carried in state, for two reasons: the pool would
    otherwise duplicate provenance that already exists on disk, and re-reading
    is what lets the freeze confirm the evidence it is about to pin immutably is
    still exactly what was imported. Everything the pool asserts is checked back
    against the bundle — the record's hash, its conformance to the import
    contract, the identities state and record must agree on, and every artifact
    body against the hash and size the record declares, through the same
    verifier that guards the import.

    The bodies matter as much as the record: the record is a table of hashes, so
    an L1 or L2 artifact that was deleted or changed after import leaves the
    record byte-identical and the evidence gone. A manifest is immutable, so
    this is the last moment either can be caught; afterwards the batch pins
    hashes describing bytes nobody has, and every later analysis reads whatever
    is there now.

    Everything this stops on is `StateError`: the bundle is the pool's own
    evidence, and gone, edited, or disagreeing with the entry that names it are
    all one damaged machine-local record (`errors.StateError`). None of it is a
    state a verb can legally change — re-importing is what answers every one.
    """

    directory = config.repo_root / ref.artifacts_path
    path = directory / REPORT_JSON_FILENAME
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(
            f"cannot read the staged bundle for report {ref.report_key} at {path}: {exc}; re-import it before "
            "freezing a batch that claims it"
        ) from exc
    if not isinstance(record, dict):
        raise StateError(
            f"staged bundle for report {ref.report_key} is not a JSON object: {path}; re-import it before "
            "freezing a batch that claims it"
        )
    digest = sha256_bytes(canonical_json(record))
    if digest != ref.bundle_sha256:
        raise StateError(
            f"staged bundle for report {ref.report_key} no longer matches the hash recorded at import "
            f"({path}); re-import it before freezing a batch that claims it"
        )

    result = normalize(record, config, schema)
    if isinstance(result, Rejection):
        raise StateError(
            f"staged bundle for report {ref.report_key} no longer satisfies the import contract "
            f"({result.reason}) at {path}; re-import it before freezing a batch that claims it"
        )
    _require_same_identity(entry, ref, result, path)

    mismatch = verify_artifact_bytes(result, _staged_bodies(config, ref, result))
    if mismatch is not None:
        raise StateError(
            f"staged artifacts for report {ref.report_key} no longer match the record under {directory}: "
            f"{mismatch.detail}; re-import it before freezing a batch that claims it"
        )
    return record


def _staged_bodies(config: EvolutionConfig, ref: ReportRef, report: NormalizedReport) -> dict[str, bytes]:
    """Every declared artifact body, read back from the staged bundle."""

    directory = config.repo_root / ref.artifacts_path / ARTIFACTS_SUBDIR
    blobs: dict[str, bytes] = {}
    for artifact in report.artifacts:
        path = directory / artifact.name
        try:
            blobs[artifact.name] = path.read_bytes()
        except OSError as exc:
            raise StateError(
                f"staged bundle for report {ref.report_key} is missing artifact {artifact.name} at {path}: {exc}; "
                "re-import it before freezing a batch that claims it"
            ) from exc
    return blobs


def _require_same_identity(
    entry: PoolEntry,
    ref: ReportRef,
    report: NormalizedReport,
    path: Path,
) -> None:
    """State and bundle must agree on what this report is.

    The hash proves the bundle is intact; it says nothing about whether the pool
    entry pointing at it still describes the same report. A mismatch means the
    manifest would name one report and pin another one's content — and the
    manifest is what every later analysis reads identity from.
    """

    expected = (ref.report_key, ref.sequence, ref.evaluation_id, ref.generated_at, entry.repo_id, entry.task_id)
    actual = (
        report.report_key,
        report.sequence,
        report.evaluation_id,
        report.generated_at,
        report.repo_id,
        report.task_id,
    )
    if expected != actual:
        raise StateError(
            f"the pool entry for report {ref.report_key} disagrees with its staged bundle at {path}: "
            f"state says {expected}, the bundle says {actual}; re-import it before freezing a batch that claims it"
        )


def _write_manifest(config: EvolutionConfig, batch_id: str, manifest: Mapping[str, Any]) -> Path:
    """Publish the batch directory by an atomic rename.

    The manifest is immutable, so it must never be observable half-written and
    never be replaced: the directory appears complete or not at all, and an
    existing one stops the freeze instead of being overwritten.
    """

    root = config.batches_root
    root.mkdir(parents=True, exist_ok=True)
    final = root / batch_id
    if final.exists():
        raise AlreadyAnswered(f"{final} already exists; a frozen batch is never rewritten")

    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=root))
    try:
        atomic_write_text(staging / MANIFEST_FILENAME, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        os.replace(staging, final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return final


def _claim_reports(state: EvolutionState, report_keys: set[str], *, batch_id: str, now: datetime) -> None:
    """Move exactly these reports out of the pending pool.

    A move, not a copy: a report may hold exactly one decision, and state that
    listed a key as both pending and processed would not load (`state.py`). This
    is what makes a report "no longer eligible" — the pool is the eligibility
    record, the manifest is the membership record.

    Keyed on reports rather than tasks because a task can outlive its batched
    reports. A `start` whose freeze was interrupted syncs again before it
    resumes, so a fresh evaluation of an already-batched task can land in the
    pool between the manifest and the state commit. Claiming per report leaves
    that late report pending — a later batch's evidence (invariant 3) — instead
    of burying it under a batch that never named it.
    """

    recorded_at = format_rfc3339(now)
    for report_key in report_keys:
        state.processed[report_key] = {"batch_id": batch_id, "recorded_at": recorded_at}

    survivors: list[PoolEntry] = []
    for entry in state.pending:
        remaining = [ref for ref in (entry.primary, *entry.reruns) if ref.report_key not in report_keys]
        if not remaining:
            continue
        if len(remaining) <= len(entry.reruns):
            # Something was claimed. The highest sequence left takes `primary`,
            # which is what the slot means; `first_imported_at` stays as it was,
            # because it records when this task entered the pool and it did.
            primary = max(remaining, key=lambda ref: ref.sequence)
            entry.primary = primary
            entry.reruns = [ref for ref in remaining if ref.report_key != primary.report_key]
        survivors.append(entry)
    state.pending = survivors


def _complete_freeze(
    config: EvolutionConfig,
    batch: Batch,
    state: EvolutionState,
    *,
    now: datetime,
    state_present: bool,
) -> tuple[str, ...]:
    """Finish an interrupted freeze of the open batch, and report what was left.

    Called on every `start` that finds an open batch, so a restart converges
    instead of needing a repair by hand. Each step is idempotent, which is what
    makes repeated calls harmless: normally nothing is missing and this returns
    empty.

    No audit line is appended here. The `batch-frozen` record belongs to the
    freeze, and a second one for the same batch would make the ledger claim two
    freezes; a missing one costs an audit line and no state (`ledger.py`).
    """

    task_id = batch.analysis_task_id
    if task_id is None:
        raise RecordError(
            f"{batch.manifest_path}: the open batch names no analysis_task_id, so this controller cannot tell "
            "which task is meant to analyze it — and nothing can show that analysis completed; restore the field"
        )
    spec = _task_spec(config, batch.manifest, task_id=task_id, batch_id=batch.batch_id)

    completed: list[str] = []
    _require_consistent_claims(config, batch, state, state_present=state_present)
    # A key in `processed` is never also pending — state that claimed both would
    # not load — so the reports still owed to this batch are exactly these.
    outstanding = batch.report_keys - set(state.processed)
    if outstanding:
        _claim_reports(state, set(batch.report_keys), batch_id=batch.batch_id, now=now)
        save_state(config, state)
        completed.append(COMPLETED_STATE)

    # Whatever is already at the task's path decides whether this batch has an
    # analysis task at all, so it is checked before the step counts as done.
    analysis_task.assert_generated(config, spec)
    if not analysis_task.task_exists(config, task_id):
        analysis_task.write_task(config, spec)
        completed.append(COMPLETED_TASK)
    if clear_proposal(config):
        # The freeze this repair is finishing wrote its manifest and stopped
        # before dropping its own offer. Left standing, the next freeze would
        # read it and try to form a second batch out of reports this one claimed.
        completed.append(COMPLETED_PROPOSAL)
    if analysis_task.append_index_row(config, spec):
        completed.append(COMPLETED_INDEX)
    return tuple(completed)


def _require_consistent_claims(
    config: EvolutionConfig,
    batch: Batch,
    state: EvolutionState,
    *,
    state_present: bool,
) -> None:
    """The manifest's members must be accounted for: pending, or claimed by this
    batch and no other.

    A claim naming a different batch means two cohorts believe they own one
    report, and this controller cannot arbitrate which manifest is right.

    A member the state does not mention at all is a gap, and the discriminator
    is whether there is a state file at all — not how much of the batch it
    happens to mention. `.ai-evolution/` is ignored runtime state, so a fresh
    clone carries every committed manifest and no state whatsoever; refusing
    there would deadlock a checkout on a batch it could not possibly have
    staged. But a state file that exists is this machine's own record of what
    it staged, and a hole in it is lost evidence however large the hole is —
    finishing the freeze over one would have `_claim_reports` manufacture the
    missing claims, turning the loss into a state that reads as consistent with
    the manifest. Judging the gap against the rest of the batch, as this once
    did, made *total* loss the one size of hole that passed.

    Both answers are `StateError`, including the one whose sentence names two
    files. The manifest is immutable and this controller never rewrites one, so
    the only party to a disagreement it can restore is the state file — and the
    caller routing on the code needs the half it can act on, while the person
    reading the sentence is still shown both.
    """

    if not state_present:
        return

    pending = {report_key for entry in state.pending for report_key in entry.report_keys()}
    for report_key in sorted(batch.report_keys):
        claim = state.processed.get(report_key)
        if claim is None:
            if report_key not in pending:
                raise StateError(
                    f"report {report_key} is named by {batch.manifest_path} but is neither pending nor claimed in "
                    f"{config.state_path}; this machine's runtime state does not account for the batch's evidence — "
                    "restore the state rather than freezing over the gap, or, if this machine never staged this "
                    "batch, import the reports again before completing it here"
                )
            continue
        owner = claim.get("batch_id")
        if owner != batch.batch_id:
            raise StateError(
                f"report {report_key} is named by {batch.manifest_path} but claimed by {owner!r} in "
                f"{config.state_path}; one report belongs to one batch — resolve which manifest owns it first"
            )


def _task_spec(
    config: EvolutionConfig,
    manifest: Mapping[str, Any],
    *,
    task_id: str,
    batch_id: str,
) -> AnalysisTaskSpec:
    """Describe the generated task from the manifest and the batch series, so a
    reconstructed task says exactly what the original said.

    The one thing outside the manifest is the release this cohort follows, and it
    is stable for exactly as long as this batch is current: a promotion is
    recorded by a terminal outcome that is never edited, and no later batch can
    promote while this one has not concluded (invariant 14). So the repair path
    below derives the same subject the freeze did.
    """

    reports = tuple(manifest.get("reports") or ())
    directory = f"{config.storage.batches}/{batch_id}"
    justification = manifest.get("force_justification")
    # The manifest's own denominator, read back rather than recomputed: the pool
    # it was selected out of is machine-local and has moved, so a repaired task
    # reconstructed from a live reading would state a composition the frozen
    # batch never had.
    selection = manifest.get("selection")
    selection = selection if isinstance(selection, Mapping) else {}
    return AnalysisTaskSpec(
        task_id=task_id,
        batch_id=batch_id,
        manifest_relative_path=f"{directory}/{MANIFEST_FILENAME}",
        proposed_tasks_relative_path=f"{directory}/{analysis_task.PROPOSED_TASKS_DIRNAME}",
        findings_relative_path=f"{directory}/{FINDINGS_FILENAME}",
        closure_relative_path=f"{directory}/{CLOSURE_FILENAME}",
        artifacts_root=config.storage.imported_artifacts,
        task_count=len({(report["repo_id"], report["task_id"]) for report in reports}),
        report_count=len(reports),
        considered=_count(selection, "considered"),
        auto_selected=_count(selection, "auto_selected"),
        human_included=_count(selection, "human_included"),
        runner_protocol_revision=str(manifest["runner_protocol_revision"]),
        config_sha256=str(manifest.get("config_sha256") or ""),
        forced=bool(manifest.get("forced")),
        force_justification=_optional_string(justification),
        release=_release_spec(config, batch_id, directory=directory),
    )


def _count(selection: Mapping[str, Any], key: str) -> int:
    value = selection.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _release_spec(
    config: EvolutionConfig,
    batch_id: str,
    *,
    directory: str,
) -> analysis_task.ReleaseAssessment | None:
    """The release this batch is the first cohort after, or None when it follows
    none.

    Derived from the whole lineage, which by this point includes this batch's own
    manifest: the obligation belongs to the first cohort frozen after a promotion,
    and that is a position in the batch series rather than a property of one
    manifest. Identity only — the cohorts and their comparability are read when
    the assessment is formed, not baked into a task file that a repair on another
    machine would then have to reproduce.
    """

    # Locally imported for the reason `lineage` imports `replay` that way: the
    # assessment layer reads this module's batches and the replay vocabulary
    # above it, so the dependency between the two points that way and a
    # module-level import here would close the loop.
    from . import assessment

    known = describe_lineage(config)
    batch = next((item.batch for item in known.batches if item.batch_id == batch_id), None)
    if batch is None:
        return None
    assessed = assessment.owed_by(known, batch)
    if assessed is None:
        return None
    return analysis_task.ReleaseAssessment(
        batch_id=assessed.batch_id,
        experiment_id=assessed.experiment_id,
        revision=assessed.revision,
        round_number=assessed.round_number,
        candidate_revision=assessed.candidate_revision,
        merge_input_revision=assessed.merge_input_revision,
        merge_input_ref=assessed.merge_input_ref,
        tree=assessed.tree,
        planned_targets=assessed.planned_targets,
        assessment_relative_path=f"{directory}/{ASSESSMENT_FILENAME}",
    )


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
