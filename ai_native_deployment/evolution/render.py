"""Operator-facing text for the four evolution commands.

One module rather than a formatter beside each result type, because the four
commands describe one lifecycle: `sync`'s "feed drained" and `status`'s
"completeness unproven" are the same fact, and wording that drifts apart teaches
an operator that they are two.

Nothing here is persisted — terminal output, not committed content — so it is
the one place a rejection's verbatim diagnostic may be shown. Everything that
reaches Git goes through `ledger.py`, which publishes bounded reason codes only.
"""

from __future__ import annotations

from pathlib import Path

from .batches import REASON_POOL_INCOMPLETE, FreezeResult, StartResult
from .importer import STATUS_KNOWN, STATUS_NEW, STATUS_REJECTED, STATUS_RERUN, ListResult, SyncResult
from .phase import LifecycleStatus
from .revisions import Revisions

FIELD_WIDTH = 13


def format_list(result: ListResult) -> str:
    """What the feed is offering, and nothing about what was done with it."""

    counts = {status: 0 for status in (STATUS_NEW, STATUS_RERUN, STATUS_KNOWN, STATUS_REJECTED)}
    for candidate in result.candidates:
        counts[candidate.status] = counts.get(candidate.status, 0) + 1

    lines = [f"feed: {len(result.candidates)} record(s) inspected, {_drained(result.exhausted)}"]
    lines.append(_field("new", f"{counts[STATUS_NEW]} report(s), {result.new_task_count} unique completed task(s)"))
    lines.append(_field("rerun", f"{counts[STATUS_RERUN]} report(s) for tasks already pending"))
    lines.append(_field("known", f"{counts[STATUS_KNOWN]} report(s) already decided"))
    lines.append(_field("rejected", f"{counts[STATUS_REJECTED]} report(s)"))
    for candidate in result.candidates:
        if candidate.status == STATUS_REJECTED:
            lines.append(_field("", f"{candidate.report_key or '<no report_key>'}: {candidate.reason} — {candidate.detail}"))
    lines.append(_field("pool", f"{result.pool_size} unique completed task(s) already pending"))
    lines.append("nothing was written: list inspects the feed and local state only")
    return "\n".join(lines)


def format_sync(result: SyncResult) -> str:
    lines = [f"sync: {result.inspected} record(s) inspected, {_drained(result.exhausted)}"]
    lines.append(_field("imported", f"{len(result.imported)} new unique completed task(s)"))
    lines.append(_field("reruns", f"{len(result.reruns)} later report(s) for tasks already pending"))
    lines.append(_field("skipped", f"{len(result.skipped)} report(s) already decided"))
    lines.append(_field("rejected", f"{len(result.rejected)} report(s)"))
    for report_key, reason in result.rejected:
        lines.append(_field("", f"{report_key or '<no report_key>'}: {reason}"))
    lines.append(_field("pool", f"{result.pool_size} unique completed task(s) pending"))
    if result.cursor_after != result.cursor_before:
        lines.append(_field("discovery", "cursor advanced"))
    return "\n".join(lines)


def format_freeze(result: FreezeResult, repo_root: Path) -> str:
    lines: list[str] = []
    for batch_id in result.closed_batch_ids:
        lines.append(f"closed: {batch_id} — its analysis task completed; closure record written")

    decision = result.decision
    if result.frozen:
        lines.append(
            f"freeze: {result.batch_id} frozen — {decision.task_count} unique completed task(s), {decision.trigger}"
        )
        lines.append(_field("manifest", _relative(result.manifest_path, repo_root)))
        lines.append(_field("analysis", f"{_relative(result.analysis_task_path, repo_root)} (pending)"))
        lines.append("a human admits any change task this analysis proposes; the analysis itself edits no canonical file")
        return "\n".join(lines)

    if result.open_batch_id:
        lines.append(f"freeze: no batch — {result.open_batch_id} is still open for analysis")
        if result.analysis_task_id:
            lines.append(_field("analysis", result.analysis_task_id))
    else:
        lines.append(f"freeze: no batch — {decision.reason} ({decision.task_count}/{decision.target}, minimum {decision.minimum})")
        if decision.reason == REASON_POOL_INCOMPLETE:
            lines.append(_field("", "no discovery pass has reported the feed drained; run sync until it does"))
    if result.completed:
        lines.append(_field("repaired", ", ".join(result.completed)))
    return "\n".join(lines)


def format_start(result: StartResult, repo_root: Path) -> str:
    return f"{format_sync(result.sync)}\n{format_freeze(result.freeze, repo_root)}"


def format_status(status: LifecycleStatus) -> str:
    """The lifecycle phase, then the facts it was derived from."""

    decision = status.decision
    lines = [f"evolution: {status.summary}"]

    pool = f"{decision.task_count} unique completed task(s); target {decision.target}, minimum {decision.minimum}"
    lines.append(_field("pool", pool))
    if not status.pool_complete:
        lines.append(_field("", "pool completeness unproven — no discovery pass has reported the feed drained"))
    elif decision.waited_days is not None:
        lines.append(_field("", f"oldest pending report imported {decision.waited_days} day(s) ago, max wait {decision.max_wait_days}"))

    if decision.freeze:
        lines.append(_field("admission", f"ready to freeze — {decision.trigger}"))
    else:
        lines.append(_field("admission", f"no batch — {decision.reason}"))

    open_batch = status.open_batch
    open_note = f"open {open_batch.batch_id}" if open_batch else "none open"
    lines.append(_field("batches", f"{status.batch_count} frozen, {open_note}"))
    if open_batch is not None:
        lines.append(_field("", f"analysis task {open_batch.analysis_task_id or '<none named>'}"))
        lines.append(
            _field("", "dispositions recorded" if open_batch.findings_recorded else "dispositions not yet recorded")
        )
        if not open_batch.evidence_complete:
            lines.append(
                _field(
                    "",
                    f"evidence on this machine: {open_batch.evidence_local}/{open_batch.report_count} bundle(s) — "
                    "the rest were staged elsewhere",
                )
            )

    for batch in status.proposals:
        lines.append(_field("proposals", f"{batch.batch_id}: {', '.join(batch.drafts)} — awaiting human admission"))
    if status.implementation_tasks:
        lines.append(_field("implementing", ", ".join(status.implementation_tasks)))

    lines.extend(_revision_lines(status.revisions))
    return "\n".join(lines)


def _revision_lines(pair: Revisions) -> list[str]:
    """Two Nones mean something different from either None alone: no baseline is
    a checkout with no release tag, no candidate is a checkout sitting on the
    release line, and both together is a directory that is not a work-tree root
    at all — which has no revisions to report rather than two absent ones."""

    if pair.baseline is None and pair.candidate is None:
        return [_field("revisions", "none — not a git work tree root")]
    return [
        _field("baseline", pair.baseline.describe() if pair.baseline else "none — no release tag"),
        _field("candidate", pair.candidate.describe() if pair.candidate else "none — at the release line"),
    ]


def _field(name: str, value: str) -> str:
    return f"  {name.ljust(FIELD_WIDTH)}{value}"


def _drained(exhausted: bool) -> str:
    return "feed drained" if exhausted else "feed not drained (page bound reached or a fetch failed)"


def _relative(path: Path | None, repo_root: Path) -> str:
    if path is None:
        return "<none>"
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)
