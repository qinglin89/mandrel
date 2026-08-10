"""Admission policy and the immutable batch freeze.

This is the step where evidence becomes a cohort. Three things make it safe:

- **A human triggers it** (invariant 9). Nothing here is scheduled, and no code
  path lowers the configured minimum.
- **The manifest is immutable** (invariant 3). It is written once, by an atomic
  directory rename, and never edited afterwards — a late report belongs to a
  later batch.
- **One open batch at a time** (invariant 12). A batch is open until its
  analysis has completed and recorded `findings.md`; while one is open, `start`
  completes it rather than starting a second.

**What may be frozen.** Only a pool that is the whole eligible set. `start`
admits from the pool its own sync produced, so a sync that stopped at its page
bound without draining the feed leaves a pool that is a prefix — and a cohort
frozen from a prefix has a denominator set by a local pagination limit rather
than by the evidence (invariants 1 and 2).

**Interruption.** A freeze commits in four steps, in this order: the manifest
(the durable membership statement), the state transition that moves the batched
reports out of the pending pool, the pending analysis task, then the audit line.
Each step is safe to redo, so a restarted `start` finds the open batch and
finishes whatever remains. The one residual, shared with the importer, is that
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

from ..hashing import sha256_bytes
from . import analysis_task
from .analysis_task import AnalysisTaskSpec
from .config import BATCH_SCHEMA_FILENAME, EvolutionConfig
from .errors import BatchError
from .feed import ReportFeed
from .importer import DEFAULT_MAX_PAGES, DEFAULT_PAGE_SIZE, SyncResult, sync_locked
from .ledger import append_records, build_record
from .manifests import (
    BATCH_SCHEMA_VERSION,
    FINDINGS_FILENAME,
    MANIFEST_FILENAME,
    Batch,
    load_batches,
    next_batch_id,
)
from .reports import (
    NormalizedReport,
    Rejection,
    canonical_json,
    load_import_schema,
    normalize,
    verify_artifact_bytes,
)
from .revisions import release_line_revision
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
)

RECORD_BATCH_FROZEN = "batch-frozen"

# Why a batch was frozen. Locally authored, bounded, and safe to publish — the
# ledger's `detail` carries one of these and nothing else.
TRIGGER_TARGET = "target-reached"
TRIGGER_MAX_WAIT = "max-wait-days-elapsed"
TRIGGER_FORCED = "human-forced-below-target"

# Why it was not.
REASON_OPEN_BATCH = "open-analysis-batch"
REASON_POOL_INCOMPLETE = "pool-incomplete"
REASON_POOL_EMPTY = "pool-empty"
REASON_BELOW_MINIMUM = "pool-below-minimum"
REASON_BELOW_TARGET = "pool-below-target"

# What a reconciling `start` completed, for the operator's benefit.
COMPLETED_STATE = "state-transition"
COMPLETED_TASK = "analysis-task"
COMPLETED_INDEX = "index-row"


@dataclass(frozen=True)
class AdmissionDecision:
    """Whether the pool may be frozen, and the numbers behind the answer."""

    freeze: bool
    task_count: int
    target: int
    minimum: int
    trigger: str | None = None
    reason: str | None = None
    forced: bool = False
    oldest_pending_at: str | None = None
    waited_days: int | None = None
    max_wait_days: int | None = None
    open_batch_id: str | None = None


@dataclass(frozen=True)
class FreezeResult:
    decision: AdmissionDecision
    batch_id: str | None = None
    manifest_path: Path | None = None
    analysis_task_id: str | None = None
    analysis_task_path: Path | None = None
    open_batch_id: str | None = None
    completed: tuple[str, ...] = ()

    @property
    def frozen(self) -> bool:
        return self.batch_id is not None


@dataclass(frozen=True)
class StartResult:
    """`start` is discovery then admission, under one lock."""

    sync: SyncResult
    freeze: FreezeResult


def is_open(config: EvolutionConfig, batch: Batch) -> bool:
    """Whether this batch still awaits its analysis.

    Two signals, and the asymmetry between them is the point:

    - `findings.md` is the committed disposition record, and the only signal
      that can *close* a batch. It travels with the repository, so a fresh clone
      reads a long-finished batch as closed. Closure that depended on
      machine-local files would leave every past batch open on any other
      machine, and the guard below would deadlock.
    - The generated analysis task, on a machine that has it, can only *hold a
      batch open*. The contract closes a batch after its analysis task
      "completes successfully" (Data layout), and that task writes its findings
      while it is still being developed and reviewed — so draft findings beside
      a task that is still pending, in progress, or in final review are not a
      completed analysis, and treating them as one lets a second cohort form
      while the first is still being analyzed.

    So local task state can hold open what the committed record calls closed,
    never the reverse: every machine agrees a batch is closed, or is more
    conservative than the machine doing the work. A task file that cannot be
    read counts as in flight (`analysis_task.task_status`) — an unreadable
    lifecycle has not been shown to have finished.
    """

    if not batch.findings_recorded:
        return True
    task_id = batch.analysis_task_id
    if task_id is None:
        # Nothing to consult: the manifest names no task, so the committed
        # record is all there is. `_complete_freeze` reports the missing field
        # for any batch that is still open.
        return False
    active = analysis_task.task_path(config, task_id)
    if not active.is_file():
        # Archived at close-out, or never present on this machine.
        return False
    return analysis_task.task_status(active) != analysis_task.STATUS_COMPLETED


def open_batch(config: EvolutionConfig) -> Batch | None:
    """The single batch awaiting analysis, if any.

    Two would mean the repository already contradicts invariant 12, which this
    controller cannot repair by choosing one of them.
    """

    unfinished = [batch for batch in load_batches(config) if is_open(config, batch)]
    if len(unfinished) > 1:
        raise BatchError(
            "more than one open analysis batch: "
            + ", ".join(batch.batch_id for batch in unfinished)
            + " — invariant 12 serializes analysis; complete the earlier batch's analysis and record its findings first"
        )
    return unfinished[0] if unfinished else None


def evaluate_admission(
    config: EvolutionConfig,
    state: EvolutionState,
    *,
    now: datetime,
    forced: bool = False,
    open_batch_id: str | None = None,
    pool_complete: bool = True,
) -> AdmissionDecision:
    """Apply the configured admission policy to the pending pool.

    Pure, and the only place that decides. The order of the tests is the policy:

    1. An open batch dominates everything (invariant 12).
    2. The pool must be the whole eligible set. `pool_complete` is false when
       the discovery that fed this decision stopped before the feed was
       drained; the count is then a prefix, and every threshold below would be
       measured against a number a page limit chose. Not an error — the next
       run continues from the cursor and admits the complete pool.
    3. The target forms a batch on its own — a `--force` alongside it changes
       nothing, so the batch is not recorded as forced.
    4. The configured minimum is a floor no path crosses. `--force` waives the
       *target*, never the minimum (contract: normal workflow).
    5. `max_wait_days` since the oldest pending report releases a batch that
       has met the minimum, so evidence does not age out waiting for a target
       that may never arrive. At `max_wait_days = 0` the minimum alone is
       enough, provided the oldest timestamp is not in the future: a source
       clock ahead of this one leaves the age rule no honest answer, and
       `waited_days` stays negative so the skew is visible rather than assumed
       away.
    6. Otherwise a human may force it, with a justification.

    `pool_complete` defaults to true because a bare `freeze` contacts no feed:
    it admits from what is already imported, which is complete by construction.
    """

    task_count = len(state.pending)
    oldest = min((entry.first_imported_at for entry in state.pending), default=None)
    waited = (now - parse_rfc3339(oldest)).days if oldest is not None else None

    def decision(*, freeze: bool, trigger: str | None = None, reason: str | None = None) -> AdmissionDecision:
        return AdmissionDecision(
            freeze=freeze,
            task_count=task_count,
            target=config.batch.target_task_count,
            minimum=config.batch.minimum_task_count,
            trigger=trigger,
            reason=reason,
            forced=trigger == TRIGGER_FORCED,
            oldest_pending_at=oldest,
            waited_days=waited,
            max_wait_days=config.batch.max_wait_days,
            open_batch_id=open_batch_id,
        )

    if open_batch_id is not None:
        return decision(freeze=False, reason=REASON_OPEN_BATCH)
    if not pool_complete:
        return decision(freeze=False, reason=REASON_POOL_INCOMPLETE)
    if task_count == 0:
        return decision(freeze=False, reason=REASON_POOL_EMPTY)
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
    runner_revision: str | None = None,
) -> FreezeResult:
    """Freeze the pending pool into a batch when policy allows, and create its
    pending analysis task."""

    with single_writer_lock(config):
        return freeze_locked(
            config,
            now=now,
            forced=forced,
            justification=justification,
            runner_revision=runner_revision,
        )


def start(
    config: EvolutionConfig,
    feed: ReportFeed,
    *,
    now: datetime | None = None,
    forced: bool = False,
    justification: str | None = None,
    runner_revision: str | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> StartResult:
    """The human-triggered entry point: repair, import, then admit.

    One lock covers all three, so the pool admission measures is exactly the
    pool the import produced. A feed failure aborts before any admission
    decision — an unreachable feed means the pool's completeness is unknown, and
    freezing an unknown pool is precisely what invariant 3 forbids.

    Repair runs *first*, and touches no feed. An open batch whose state
    transition or analysis task never landed is a frozen manifest with no way to
    be analyzed; making that repair wait for a reachable feed would leave the
    repository stuck on an outage that has nothing to do with it.
    """

    _require_justified(forced, justification)
    moment = _require_aware(now) if now is not None else datetime.now(timezone.utc)
    with single_writer_lock(config):
        repaired = _reconcile_locked(config, now=moment)
        imported = sync_locked(config, feed, page_size=page_size, max_pages=max_pages)
        admitted = freeze_locked(
            config,
            now=moment,
            forced=forced,
            justification=justification,
            runner_revision=runner_revision,
            pool_complete=imported.exhausted,
        )
    if repaired is not None:
        # The freeze above reconciled again and found nothing left; the steps
        # this run actually completed are the ones the first pass did.
        admitted = replace(admitted, completed=tuple(dict.fromkeys(repaired.completed + admitted.completed)))
    return StartResult(sync=imported, freeze=admitted)


def freeze_locked(
    config: EvolutionConfig,
    *,
    now: datetime | None = None,
    forced: bool = False,
    justification: str | None = None,
    runner_revision: str | None = None,
    pool_complete: bool = True,
) -> FreezeResult:
    """`freeze` without acquiring the lock, for a caller that already holds it."""

    _require_justified(forced, justification)
    moment = _require_aware(now) if now is not None else datetime.now(timezone.utc)

    repaired = _reconcile_locked(config, now=moment)
    if repaired is not None:
        return repaired

    state = load_state(config)
    decision = evaluate_admission(config, state, now=moment, forced=forced, pool_complete=pool_complete)
    if not decision.freeze:
        return FreezeResult(decision=decision)

    revision = runner_revision if runner_revision is not None else release_line_revision(config.repo_root)
    batches = load_batches(config)
    batch_id = next_batch_id(batches)
    task_id = analysis_task.analysis_task_id(batch_id, moment)
    if analysis_task.task_exists(config, task_id):
        raise BatchError(
            f"cannot create the analysis task for {batch_id}: {task_id} already exists in "
            f"{analysis_task.TASKS_DIRNAME}/; resolve it before freezing"
        )

    entries = _batched_entries(state)
    manifest = _build_manifest(
        config,
        batch_id=batch_id,
        entries=entries,
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

    return FreezeResult(
        decision=decision,
        batch_id=batch_id,
        manifest_path=directory / MANIFEST_FILENAME,
        analysis_task_id=task_id,
        analysis_task_path=task_file,
    )


def _reconcile_locked(config: EvolutionConfig, *, now: datetime) -> FreezeResult | None:
    """Finish an interrupted freeze of the open batch, or report there is none.

    Reads only what is already on disk — the manifests, the runtime state, and
    `.ai-tasks/` — so a caller can repair before it has a feed, or without one
    at all.
    """

    unfinished = open_batch(config)
    if unfinished is None:
        return None

    state = load_state(config)
    completed = _complete_freeze(config, unfinished, state, now=now)
    return FreezeResult(
        decision=evaluate_admission(config, state, now=now, open_batch_id=unfinished.batch_id),
        open_batch_id=unfinished.batch_id,
        analysis_task_id=unfinished.analysis_task_id,
        manifest_path=unfinished.manifest_path,
        completed=completed,
    )


def _require_justified(forced: bool, justification: str | None) -> None:
    """A forced batch is a human overriding the configured target, so the reason
    is part of the request. Refusing here keeps the force path from ever being
    silent (contract: forced sub-threshold batches require a justification)."""

    if forced and not (justification or "").strip():
        raise BatchError(
            "a forced batch needs a written human justification; it waives the configured target "
            "and the manifest records why"
        )


def _require_aware(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise BatchError("freeze time must be timezone-aware; a naive datetime makes the age rule guesswork")
    return moment


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
    created_at: datetime,
    decision: AdmissionDecision,
    justification: str | None,
    runner_revision: str | None,
    analysis_task_id: str,
) -> dict[str, Any]:
    """Assemble and validate the membership snapshot before anything is written.

    Every report the batched tasks own goes in, reruns included: they are
    provenance (invariant 4) and they do not raise the task count (invariant 1),
    which is derived from the distinct `(repo_id, task_id)` pairs.
    """

    schema = load_import_schema(config)
    reports: list[dict[str, Any]] = []
    for entry in entries:
        for ref in (entry.primary, *entry.reruns):
            reports.append(_manifest_report(config, entry, ref, schema))
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
        "reports": reports,
    }
    validate_or_raise(
        manifest,
        load_schema(config.schema_path(BATCH_SCHEMA_FILENAME)),
        description=f"batch manifest for {batch_id}",
    )
    return manifest


def _manifest_report(
    config: EvolutionConfig,
    entry: PoolEntry,
    ref: ReportRef,
    schema: Mapping[str, Any],
) -> dict[str, Any]:
    """One report's membership entry, with its cohort provenance read back from
    the staged bundle and the whole bundle re-verified against the pool."""

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
    """

    directory = config.repo_root / ref.artifacts_path
    path = directory / REPORT_JSON_FILENAME
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchError(f"cannot read the staged bundle for report {ref.report_key} at {path}: {exc}") from exc
    if not isinstance(record, dict):
        raise BatchError(f"staged bundle for report {ref.report_key} is not a JSON object: {path}")
    digest = sha256_bytes(canonical_json(record))
    if digest != ref.bundle_sha256:
        raise BatchError(
            f"staged bundle for report {ref.report_key} no longer matches the hash recorded at import "
            f"({path}); re-import it before freezing a batch that claims it"
        )

    result = normalize(record, config, schema)
    if isinstance(result, Rejection):
        raise BatchError(
            f"staged bundle for report {ref.report_key} no longer satisfies the import contract "
            f"({result.reason}) at {path}; re-import it before freezing a batch that claims it"
        )
    _require_same_identity(entry, ref, result, path)

    mismatch = verify_artifact_bytes(result, _staged_bodies(config, ref, result))
    if mismatch is not None:
        raise BatchError(
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
            raise BatchError(
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
        raise BatchError(
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
        raise BatchError(f"{final} already exists; a frozen batch is never rewritten")

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
        raise BatchError(
            f"{batch.manifest_path}: the open batch names no analysis_task_id, so this controller cannot tell "
            "which task is meant to analyze it; record its findings.md to close the batch, or restore the field"
        )
    spec = _task_spec(config, batch.manifest, task_id=task_id, batch_id=batch.batch_id)

    completed: list[str] = []
    _require_consistent_claims(config, batch, state)
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
    if analysis_task.append_index_row(config, spec):
        completed.append(COMPLETED_INDEX)
    return tuple(completed)


def _require_consistent_claims(config: EvolutionConfig, batch: Batch, state: EvolutionState) -> None:
    """The manifest's members must be accounted for: pending, or claimed by this
    batch and no other.

    A claim naming a different batch means two cohorts believe they own one
    report, and this controller cannot arbitrate which manifest is right.

    A member the state does not mention at all is judged against the rest of the
    batch. If the state knows *some* of this batch's reports and not others, the
    pool has lost entries the manifest was frozen from, and finishing the freeze
    over the gap would have `_claim_reports` manufacture the missing claims —
    turning lost evidence into a state that reads as consistent. If it knows
    none of them, this machine simply never staged this batch: `.ai-evolution/`
    is ignored runtime state, so a fresh clone carries every committed manifest
    and no pool at all, and refusing there would deadlock a checkout on a batch
    it could not possibly have staged.
    """

    pending = {report_key for entry in state.pending for report_key in entry.report_keys()}
    recorded = {key for key in batch.report_keys if key in pending or key in state.processed}
    for report_key in sorted(batch.report_keys):
        claim = state.processed.get(report_key)
        if claim is None:
            if report_key not in pending and recorded:
                raise BatchError(
                    f"report {report_key} is named by {batch.manifest_path} but is neither pending nor claimed in "
                    f"{config.state_path}, while {sorted(recorded)} still are; the pool lost part of the evidence "
                    "this batch was frozen from — restore the runtime state rather than freezing over the gap"
                )
            continue
        owner = claim.get("batch_id")
        if owner != batch.batch_id:
            raise BatchError(
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
    """Describe the generated task from the manifest alone, so a reconstructed
    task says exactly what the original said."""

    reports = tuple(manifest.get("reports") or ())
    directory = f"{config.storage.batches}/{batch_id}"
    justification = manifest.get("force_justification")
    return AnalysisTaskSpec(
        task_id=task_id,
        batch_id=batch_id,
        manifest_relative_path=f"{directory}/{MANIFEST_FILENAME}",
        proposed_tasks_relative_path=f"{directory}/{analysis_task.PROPOSED_TASKS_DIRNAME}",
        findings_relative_path=f"{directory}/{FINDINGS_FILENAME}",
        artifacts_root=config.storage.imported_artifacts,
        task_count=len({(report["repo_id"], report["task_id"]) for report in reports}),
        report_count=len(reports),
        runner_protocol_revision=_optional_string(manifest.get("runner_protocol_revision")),
        config_sha256=str(manifest.get("config_sha256") or ""),
        forced=bool(manifest.get("forced")),
        force_justification=_optional_string(justification),
    )


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
