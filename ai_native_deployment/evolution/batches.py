"""Admission policy and the immutable batch freeze.

This is the step where evidence becomes a cohort. Three things make it safe:

- **A human triggers it** (invariant 9). Nothing here is scheduled, and no code
  path lowers the configured minimum.
- **The manifest is immutable** (invariant 3). It is written once, by an atomic
  directory rename, and never edited afterwards — a late report belongs to a
  later batch.
- **One open batch at a time** (invariant 12). A batch is open until its
  analysis records `findings.md`; while one is open, `start` completes it rather
  than starting a second.

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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..hashing import sha256_bytes
from . import analysis_task
from .analysis_task import AnalysisTaskSpec
from .config import (
    BATCH_SCHEMA_FILENAME,
    EvolutionConfig,
    batch_id_number,
    format_batch_id,
)
from .errors import BatchError
from .feed import ReportFeed
from .importer import DEFAULT_MAX_PAGES, DEFAULT_PAGE_SIZE, SyncResult, sync_locked
from .ledger import append_records, build_record
from .reports import canonical_json
from .revisions import release_line_revision
from .schema import format_rfc3339, load_schema, parse_rfc3339, validate_or_raise
from .state import (
    REPORT_JSON_FILENAME,
    EvolutionState,
    PoolEntry,
    ReportRef,
    atomic_write_text,
    load_state,
    save_state,
    single_writer_lock,
)

BATCH_SCHEMA_VERSION = 1

MANIFEST_FILENAME = "manifest.json"
FINDINGS_FILENAME = "findings.md"

RECORD_BATCH_FROZEN = "batch-frozen"

# Why a batch was frozen. Locally authored, bounded, and safe to publish — the
# ledger's `detail` carries one of these and nothing else.
TRIGGER_TARGET = "target-reached"
TRIGGER_MAX_WAIT = "max-wait-days-elapsed"
TRIGGER_FORCED = "human-forced-below-target"

# Why it was not.
REASON_OPEN_BATCH = "open-analysis-batch"
REASON_POOL_EMPTY = "pool-empty"
REASON_BELOW_MINIMUM = "pool-below-minimum"
REASON_BELOW_TARGET = "pool-below-target"

# What a reconciling `start` completed, for the operator's benefit.
COMPLETED_STATE = "state-transition"
COMPLETED_TASK = "analysis-task"
COMPLETED_INDEX = "index-row"


@dataclass(frozen=True)
class Batch:
    """One frozen batch on disk, as its manifest describes it."""

    batch_id: str
    directory: Path
    manifest: Mapping[str, Any]

    @property
    def manifest_path(self) -> Path:
        return self.directory / MANIFEST_FILENAME

    @property
    def findings_path(self) -> Path:
        return self.directory / FINDINGS_FILENAME

    @property
    def is_open(self) -> bool:
        """Open until the analysis has recorded its dispositions.

        `findings.md` is the closure signal because the contract's data layout
        already defines it as the completed analysis record — one existing
        mechanism instead of a second flag that could disagree with it. The
        ledger is an audit, not a state store, so it is not consulted here.

        Deliberately not read from `.ai-tasks/`: task files are machine-local and
        ignored, so a fresh clone would have no archive, every past batch would
        read as open, and the guard below would deadlock on a repository whose
        batches are all long closed. The batch directory is versioned and travels
        with the repository, so closure travels with it.
        """

        return not self.findings_path.is_file()

    @property
    def analysis_task_id(self) -> str | None:
        value = self.manifest.get("analysis_task_id")
        return value if isinstance(value, str) and value else None

    @property
    def reports(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.manifest.get("reports") or ())

    @property
    def report_keys(self) -> frozenset[str]:
        return frozenset(str(report["report_key"]) for report in self.reports)

    @property
    def task_count(self) -> int:
        """Unique completed tasks — the unit invariant 1 counts in. Reruns share
        a `(repo_id, task_id)` with their primary and so do not inflate it."""

        return len({(report["repo_id"], report["task_id"]) for report in self.reports})


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


def load_batches(config: EvolutionConfig) -> list[Batch]:
    """Every frozen batch, validated against the versioned manifest schema.

    Fails closed on anything it cannot account for: an unrecognised directory
    name might be a batch this build cannot read, and skipping it would let an
    allocation reuse an id a manifest already claims. Dot-prefixed names are the
    exception — those are staging residue from an interrupted freeze, which
    belongs to no batch.
    """

    root = config.batches_root
    if not root.is_dir():
        return []

    schema = load_schema(config.schema_path(BATCH_SCHEMA_FILENAME))
    batches: list[Batch] = []
    for entry in sorted(root.iterdir()):
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        if batch_id_number(entry.name) is None:
            raise BatchError(
                f"{entry}: not a batch identifier; only frozen batches belong under {config.storage.batches}/"
            )
        manifest = _read_manifest(entry / MANIFEST_FILENAME, schema)
        if manifest.get("batch_id") != entry.name:
            raise BatchError(
                f"{entry / MANIFEST_FILENAME}: manifest claims batch_id {manifest.get('batch_id')!r} "
                f"but sits in {entry.name!r}; a manifest cannot name another batch's id"
            )
        batches.append(Batch(batch_id=entry.name, directory=entry, manifest=manifest))
    return batches


def open_batch(config: EvolutionConfig) -> Batch | None:
    """The single batch awaiting analysis, if any.

    Two would mean the repository already contradicts invariant 12, which this
    controller cannot repair by choosing one of them.
    """

    unfinished = [batch for batch in load_batches(config) if batch.is_open]
    if len(unfinished) > 1:
        raise BatchError(
            "more than one open analysis batch: "
            + ", ".join(batch.batch_id for batch in unfinished)
            + " — invariant 12 serializes analysis; record findings for the earlier batch first"
        )
    return unfinished[0] if unfinished else None


def next_batch_id(batches: list[Batch]) -> str:
    """One past the highest id ever allocated.

    Counted from the highest, not from how many exist: reusing the id of a batch
    whose directory was moved away would attach new evidence to an old cohort's
    name.
    """

    highest = max((batch_id_number(batch.batch_id) or 0 for batch in batches), default=0)
    return format_batch_id(highest + 1)


def evaluate_admission(
    config: EvolutionConfig,
    state: EvolutionState,
    *,
    now: datetime,
    forced: bool = False,
    open_batch_id: str | None = None,
) -> AdmissionDecision:
    """Apply the configured admission policy to the pending pool.

    Pure, and the only place that decides. The order of the tests is the policy:

    1. An open batch dominates everything (invariant 12).
    2. The target forms a batch on its own — a `--force` alongside it changes
       nothing, so the batch is not recorded as forced.
    3. The configured minimum is a floor no path crosses. `--force` waives the
       *target*, never the minimum (contract: normal workflow).
    4. `max_wait_days` since the oldest pending report releases a batch that
       has met the minimum, so evidence does not age out waiting for a target
       that may never arrive. At `max_wait_days = 0` the minimum alone is
       enough, provided the oldest timestamp is not in the future: a source
       clock ahead of this one leaves the age rule no honest answer, and
       `waited_days` stays negative so the skew is visible rather than assumed
       away.
    5. Otherwise a human may force it, with a justification.
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
    """The human-triggered entry point: import, then admit.

    One lock covers both halves, so the pool admission measures is exactly the
    pool the import produced. A feed failure aborts before any admission
    decision — an unreachable feed means the pool's completeness is unknown, and
    freezing an unknown pool is precisely what invariant 3 forbids.
    """

    _require_justified(forced, justification)
    with single_writer_lock(config):
        imported = sync_locked(config, feed, page_size=page_size, max_pages=max_pages)
        admitted = freeze_locked(
            config,
            now=now,
            forced=forced,
            justification=justification,
            runner_revision=runner_revision,
        )
    return StartResult(sync=imported, freeze=admitted)


def freeze_locked(
    config: EvolutionConfig,
    *,
    now: datetime | None = None,
    forced: bool = False,
    justification: str | None = None,
    runner_revision: str | None = None,
) -> FreezeResult:
    """`freeze` without acquiring the lock, for a caller that already holds it."""

    _require_justified(forced, justification)
    moment = _require_aware(now) if now is not None else datetime.now(timezone.utc)
    revision = runner_revision if runner_revision is not None else release_line_revision(config.repo_root)

    unfinished = open_batch(config)
    state = load_state(config)

    if unfinished is not None:
        completed = _complete_freeze(config, unfinished, state, now=moment)
        return FreezeResult(
            decision=evaluate_admission(
                config, state, now=moment, forced=forced, open_batch_id=unfinished.batch_id
            ),
            open_batch_id=unfinished.batch_id,
            analysis_task_id=unfinished.analysis_task_id,
            manifest_path=unfinished.manifest_path,
            completed=completed,
        )

    decision = evaluate_admission(config, state, now=moment, forced=forced)
    if not decision.freeze:
        return FreezeResult(decision=decision)

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

    reports: list[dict[str, Any]] = []
    for entry in entries:
        for ref in (entry.primary, *entry.reruns):
            reports.append(_manifest_report(config, entry, ref))
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


def _manifest_report(config: EvolutionConfig, entry: PoolEntry, ref: ReportRef) -> dict[str, Any]:
    """One report's membership entry, with its cohort provenance read back from
    the staged bundle and checked against the hash the pool recorded."""

    record = _staged_record(config, ref)
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


def _staged_record(config: EvolutionConfig, ref: ReportRef) -> Mapping[str, Any]:
    """The validated record staged at import, verified against its hash.

    Read at freeze rather than carried in state, for two reasons: the pool would
    otherwise duplicate provenance that already exists on disk, and re-reading
    is what lets the freeze confirm the evidence it is about to pin immutably is
    still byte-identical to what was imported. A bundle that has gone missing or
    changed stops the freeze — the alternative is a manifest whose hashes
    describe content nobody has.
    """

    path = config.repo_root / ref.artifacts_path / REPORT_JSON_FILENAME
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
    return record


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

    completed: list[str] = []
    # A key in `processed` is never also pending — state that claimed both would
    # not load — so the reports still owed to this batch are exactly these.
    outstanding = batch.report_keys - set(state.processed)
    if outstanding:
        _claim_reports(state, set(batch.report_keys), batch_id=batch.batch_id, now=now)
        save_state(config, state)
        completed.append(COMPLETED_STATE)

    task_id = batch.analysis_task_id
    if task_id is None:
        raise BatchError(
            f"{batch.manifest_path}: the open batch names no analysis_task_id, so this controller cannot tell "
            "which task is meant to analyze it; record its findings.md to close the batch, or restore the field"
        )
    spec = _task_spec(config, batch.manifest, task_id=task_id, batch_id=batch.batch_id)
    if not analysis_task.task_exists(config, task_id):
        analysis_task.write_task(config, spec)
        completed.append(COMPLETED_TASK)
    if analysis_task.append_index_row(config, spec):
        completed.append(COMPLETED_INDEX)
    return tuple(completed)


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


def _read_manifest(path: Path, schema: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BatchError(
            f"{path.parent} has no {MANIFEST_FILENAME}; a batch directory without its membership snapshot "
            "is not a batch — remove it or restore the manifest"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchError(f"unreadable batch manifest {path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise BatchError(f"batch manifest is not a JSON object: {path}")
    validate_or_raise(manifest, schema, description=f"batch manifest {path}")
    return manifest
