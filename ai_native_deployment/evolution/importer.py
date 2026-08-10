"""Discovery and import: the two read paths the operator drives.

`list_candidates` inspects and writes nothing at all — not the cursor, not the
pool, not the ledger, not even the runtime directory. It is safe to run against
an unfamiliar feed.

`sync` imports. It is human-invoked (contract invariant 9), makes no evaluation
call of any kind, and never decides anything about batches: staging evidence
and forming a cohort are separate steps with separate gates.

**Interruption.** One page is one commit. Records are validated and staged
first, then the whole page — pool changes, rejections, and the new cursor —
lands in a single atomic state write, and the audit lines follow. Interrupt
anywhere and the cursor is still where the last completed page left it, so the
next run re-fetches that page and skips whatever it already knows. Bundles for
an uncommitted page are re-staged over themselves; they are never counted
twice, because the pool, not the artifact directory, is what counts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..manifest import utc_timestamp
from .config import EvolutionConfig
from .feed import ReportFeed
from .ledger import append_records, build_record
from .reports import (
    NormalizedReport,
    Rejection,
    load_import_schema,
    normalize,
    publishable_reason,
    verify_artifact_bytes,
)
from .state import EvolutionState, PoolEntry, ReportRef, load_state, save_state, single_writer_lock, stage_artifacts

DEFAULT_PAGE_SIZE = 50
# A drain bound, not a policy: a feed that keeps producing pages without ever
# reporting itself exhausted stops the run instead of looping forever.
DEFAULT_MAX_PAGES = 200

STATUS_NEW = "new"
STATUS_RERUN = "rerun"
STATUS_KNOWN = "known"
STATUS_REJECTED = "rejected"

RECORD_IMPORTED = "report-imported"
RECORD_REJECTED = "report-rejected"


@dataclass(frozen=True)
class Candidate:
    """What `list` says about one feed record, without touching anything."""

    report_key: str | None
    status: str
    sequence: int | None = None
    repo_id: str | None = None
    task_id: str | None = None
    evaluation_id: str | None = None
    reason: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class ListResult:
    candidates: tuple[Candidate, ...]
    cursor: str | None
    exhausted: bool
    pool_size: int

    @property
    def new_task_count(self) -> int:
        """Unique source tasks this feed would add to the pool — the number the
        admission threshold is measured in."""

        return len({(item.repo_id, item.task_id) for item in self.candidates if item.status == STATUS_NEW})


@dataclass(frozen=True)
class SyncResult:
    imported: tuple[str, ...]
    reruns: tuple[str, ...]
    rejected: tuple[tuple[str, str], ...]
    skipped: tuple[str, ...]
    cursor_before: str | None
    cursor_after: str | None
    pool_size: int
    exhausted: bool

    @property
    def inspected(self) -> int:
        return len(self.imported) + len(self.reruns) + len(self.rejected) + len(self.skipped)


def list_candidates(
    config: EvolutionConfig,
    feed: ReportFeed,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> ListResult:
    """Classify what the feed is offering. Read-only, by construction: this
    function holds no lock and calls nothing that writes."""

    state = load_state(config)
    schema = load_import_schema(config)
    known = state.known_report_keys()
    pooled = {entry.dedup_key for entry in state.pending}

    candidates: list[Candidate] = []
    cursor = state.cursor
    exhausted = False
    for _ in range(max_pages):
        page = feed.fetch_page(cursor, page_size)
        cursor = page.cursor
        for record in page.items:
            candidates.append(_classify(record, config, schema, known, pooled))
        if page.exhausted:
            exhausted = True
            break

    return ListResult(
        candidates=tuple(candidates),
        cursor=cursor,
        exhausted=exhausted,
        pool_size=len(state.pending),
    )


def sync(
    config: EvolutionConfig,
    feed: ReportFeed,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> SyncResult:
    """Import every eligible report the feed has not already been asked about."""

    with single_writer_lock(config):
        return sync_locked(config, feed, page_size=page_size, max_pages=max_pages)


def sync_locked(
    config: EvolutionConfig,
    feed: ReportFeed,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> SyncResult:
    """`sync` without acquiring the lock, for a caller that already holds it.

    `evolution start` is sync-then-admit under one lock: the pool it admits
    from must be the pool the sync just wrote, and the lock is a plain
    `O_EXCL` file, so taking it twice in one process would deadlock against
    itself.
    """

    schema = load_import_schema(config)
    imported: list[str] = []
    reruns: list[str] = []
    rejected: list[tuple[str, str]] = []
    skipped: list[str] = []
    exhausted = False

    state = load_state(config)
    cursor_before = state.cursor
    cursor = state.cursor

    for _ in range(max_pages):
        page = feed.fetch_page(cursor, page_size)
        cursor = page.cursor
        audit: list[dict[str, Any]] = []
        known = state.known_report_keys()

        for record in page.items:
            outcome = _import_one(record, config, feed, schema, state, known)
            if outcome.status == STATUS_KNOWN:
                skipped.append(outcome.report_key or "")
                continue
            known.add(outcome.report_key or "")
            if outcome.status == STATUS_REJECTED:
                rejected.append((outcome.report_key or "", outcome.reason or ""))
                # The reason code only. `outcome.detail` quotes the feed —
                # a malformed digest, a rejected field value — and the
                # ledger is committed (invariant 11). The full diagnostic
                # is kept in `state.rejected`, which is ignored runtime
                # data, so nothing an operator needs is lost.
                audit.append(
                    build_record(
                        RECORD_REJECTED,
                        report_key=outcome.report_key,
                        detail=publishable_reason(outcome.reason),
                    )
                )
                continue
            (reruns if outcome.status == STATUS_RERUN else imported).append(outcome.report_key or "")
            audit.append(
                build_record(
                    RECORD_IMPORTED,
                    report_key=outcome.report_key,
                    task_id=outcome.task_id,
                    detail=outcome.status,
                )
            )

        # One commit per page: state first (it is the truth), audit after.
        state.cursor = cursor
        save_state(config, state)
        append_records(config, audit)

        if page.exhausted:
            exhausted = True
            break

    return SyncResult(
        imported=tuple(imported),
        reruns=tuple(reruns),
        rejected=tuple(rejected),
        skipped=tuple(skipped),
        cursor_before=cursor_before,
        cursor_after=state.cursor,
        pool_size=len(state.pending),
        exhausted=exhausted,
    )


def _classify(
    record: Mapping[str, Any],
    config: EvolutionConfig,
    schema: Mapping[str, Any],
    known: set[str],
    pooled: set[tuple[str, str]],
) -> Candidate:
    key = record.get("report_key") if isinstance(record.get("report_key"), str) else None
    if key is not None and key in known:
        return Candidate(report_key=key, status=STATUS_KNOWN)

    result = normalize(record, config, schema)
    if isinstance(result, Rejection):
        return Candidate(report_key=result.report_key, status=STATUS_REJECTED, reason=result.reason, detail=result.detail)

    return Candidate(
        report_key=result.report_key,
        status=STATUS_RERUN if result.dedup_key in pooled else STATUS_NEW,
        sequence=result.sequence,
        repo_id=result.repo_id,
        task_id=result.task_id,
        evaluation_id=result.evaluation_id,
    )


def _import_one(
    record: Mapping[str, Any],
    config: EvolutionConfig,
    feed: ReportFeed,
    schema: Mapping[str, Any],
    state: EvolutionState,
    known: set[str],
) -> Candidate:
    key = record.get("report_key") if isinstance(record.get("report_key"), str) else None
    if key is not None and key in known:
        return Candidate(report_key=key, status=STATUS_KNOWN)

    result = normalize(record, config, schema)
    if isinstance(result, Rejection):
        return _record_rejection(state, result)

    # A transport failure here raises rather than rejecting the report: the
    # feed being unreachable says nothing about the report's eligibility, and
    # recording it as rejected would bury a good report permanently.
    blobs = feed.fetch_artifacts(record)
    mismatch = verify_artifact_bytes(result, blobs)
    if mismatch is not None:
        return _record_rejection(state, mismatch)

    artifacts_path = stage_artifacts(config, result, blobs)
    status = _stage_in_pool(state, result, artifacts_path)
    return Candidate(
        report_key=result.report_key,
        status=status,
        sequence=result.sequence,
        repo_id=result.repo_id,
        task_id=result.task_id,
        evaluation_id=result.evaluation_id,
    )


def _record_rejection(state: EvolutionState, rejection: Rejection) -> Candidate:
    key = rejection.report_key
    if key:
        state.rejected[key] = {
            "reason": rejection.reason,
            "detail": rejection.detail,
            "recorded_at": utc_timestamp(),
        }
    return Candidate(
        report_key=key,
        status=STATUS_REJECTED,
        reason=rejection.reason,
        detail=rejection.detail,
    )


def _stage_in_pool(state: EvolutionState, report: NormalizedReport, artifacts_path: str) -> str:
    """Place one validated report in the pending pool.

    The pool holds unique source tasks. A second report for a task already
    pooled is an evaluator rerun: it is kept as provenance (invariant 4) and
    does not raise the batch count (invariant 1). The higher sequence wins the
    `primary` slot, so the entry always describes the latest evaluation.
    """

    ref = ReportRef(
        report_key=report.report_key,
        sequence=report.sequence,
        evaluation_id=report.evaluation_id,
        generated_at=report.generated_at,
        bundle_sha256=report.bundle_sha256,
        artifacts_path=artifacts_path,
    )
    entry = state.find(report.repo_id, report.task_id)
    if entry is None:
        state.pending.append(
            PoolEntry(
                repo_id=report.repo_id,
                task_id=report.task_id,
                primary=ref,
                first_imported_at=utc_timestamp(),
            )
        )
        return STATUS_NEW

    if ref.sequence > entry.primary.sequence:
        entry.reruns.append(entry.primary)
        entry.primary = ref
    else:
        entry.reruns.append(ref)
    return STATUS_RERUN
