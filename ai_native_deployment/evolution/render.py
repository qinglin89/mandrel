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
from .lineage import REF_ABSENT, Gate
from .phase import ROUND_CANDIDATE_READY, ROUND_OPEN, LifecycleRevisions, LifecycleStatus

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

    if result.current_batch_id:
        lines.append(
            f"freeze: no batch — {result.current_batch_id} is still current; its outcome has not been recorded"
        )
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

    current = status.current_batch
    note = f"current {current.batch_id}" if current else "none current"
    lines.append(_field("batches", f"{status.batch_count} frozen, {note}"))
    if current is not None:
        lines.append(_field("", f"analysis task {current.analysis_task_id or '<none named>'}"))
        lines.append(_field("", _analysis_note(current.analysis_complete, current.findings_recorded)))
        if not current.evidence_complete:
            lines.append(
                _field(
                    "",
                    f"evidence on this machine: {current.evidence_local}/{current.report_count} bundle(s) — "
                    "the rest were staged elsewhere",
                )
            )

    lines.extend(_gate_lines(status.gate))
    lines.extend(_experiment_lines(status))
    lines.extend(_revision_lines(status.revisions))
    if status.last_promotion is not None:
        promotion = status.last_promotion
        lines.append(
            _field(
                "promoted",
                f"{promotion.revision[:12]} from {promotion.experiment_id} ({promotion.batch_id})",
            )
        )
    return "\n".join(lines)


def _analysis_note(complete: bool, findings_recorded: bool) -> str:
    if complete:
        return "analysis complete — its dispositions are at the admission gate"
    return "dispositions recorded" if findings_recorded else "dispositions not yet recorded"


def _gate_lines(gate: Gate | None) -> list[str]:
    """The admission gate, which is derived rather than read off the directory:
    admission copies a draft and leaves it in place, so what is present is every
    proposal ever made rather than the ones still to decide."""

    if gate is None:
        return []
    lines: list[str] = []
    if gate.waiting:
        lines.append(_field("proposals", f"{', '.join(gate.waiting)} — awaiting human admission"))
    if gate.consumed:
        lines.append(_field("admitted", ", ".join(f"{draft} → {owner}" for draft, owner in gate.consumed.items())))
    if gate.declined:
        lines.append(_field("declined", ", ".join(gate.declined)))
    if gate.missing:
        lines.append(_field("", f"decided draft(s) no longer on disk: {', '.join(gate.missing)}"))
    if gate.unusable:
        lines.append(_field("", f"file(s) under proposed-tasks/ that are not drafts: {', '.join(gate.unusable)}"))
    return lines


def _experiment_lines(status: LifecycleStatus) -> list[str]:
    lines: list[str] = []
    experiment = status.experiment
    if experiment is not None:
        # The round's own state, not the phase's: the phase can be held at an
        # earlier label by something else, and this line has to keep saying what
        # the record says.
        round_ = experiment.last_round
        state = ROUND_OPEN if experiment.open_round is not None else ROUND_CANDIDATE_READY
        lines.append(_field("experiment", f"{experiment.experiment_id}, round {round_.number} ({state})"))
        if status.implementation_tasks:
            lines.append(_field("implementing", ", ".join(status.implementation_tasks)))
        lines.extend(_ref_lines(status))
    if status.history:
        lines.append(
            _field(
                "history",
                ", ".join(
                    f"{item.experiment_id} {item.decision.outcome if item.decision else '<no decision>'}"
                    for item in status.history
                ),
            )
        )
    return lines


def _ref_lines(status: LifecycleStatus) -> list[str]:
    """The ref is only reported when it disagrees or cannot be checked.

    A ref sitting exactly where the record pins it is the ordinary state and
    says nothing an operator has to act on. Neither does an absent one: a clone
    that never fetched `refs/evolution/*` is the ordinary case everywhere but
    the machine the work happened on, and the `tip` line below already reports
    that this checkout does not hold it. A broken pin chain outranks everything
    else — it says which revision stopped leading to the next, which is what an
    operator can act on.
    """

    ref = status.ref
    if ref is None or ref.consistent is True:
        return []
    if ref.chain_break is not None:
        earlier, later = ref.chain_break
        return [
            _field("", f"{ref.ref}: {later[:12]} does not descend from {earlier[:12]} — the pinned history is broken")
        ]
    if ref.state == REF_ABSENT:
        return []
    if ref.consistent is None:
        return [_field("", f"{ref.ref}: {ref.state} — this checkout cannot confirm the pinned history")]
    return [_field("", f"{ref.ref}: {ref.state} — it is not at the revision the record pins ({ref.pinned[:12]})")]


def _revision_lines(revisions: LifecycleRevisions) -> list[str]:
    """The base is what the other two hang off — a batch with no experiment has
    no revisions in play at all, rather than three absent ones. The three are
    never collapsed into one line: substituting one for another is what an
    evidence trail must not do (contract: Revisions in play)."""

    if revisions.base is None:
        return [_field("revisions", "none in play — no experiment has frozen a base")]
    return [
        _field("base", revisions.base.describe()),
        _field(
            "candidate",
            revisions.round_candidate.describe()
            if revisions.round_candidate
            else "none pinned — the open round has not been sealed",
        ),
        _field(
            "tip",
            revisions.candidate_tip.describe()
            if revisions.candidate_tip
            else "none — the experiment ref is not in this checkout",
        ),
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
