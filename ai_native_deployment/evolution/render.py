"""Operator-facing text for the evolution commands.

One module rather than a formatter beside each result type, because the commands
describe one lifecycle: `sync`'s "feed drained" and `status`'s "completeness
unproven" are the same fact, and wording that drifts apart teaches an operator
that they are two. The same holds across the read and the verbs — what `status`
calls candidate-ready is what `seal-round` reports having made.

Nothing here is persisted — terminal output, not committed content — so it is
the one place a rejection's verbatim diagnostic may be shown. Everything that
reaches Git goes through `ledger.py`, which publishes bounded reason codes only.
"""

from __future__ import annotations

from pathlib import Path

from .assessment import (
    SETTLEMENT_RETAIN,
    Concluded,
    Formed,
    Frame,
    Measurement,
    Settled,
    Subject,
    Withdrawn,
)
from .batches import REASON_POOL_INCOMPLETE, FreezeResult, StartResult
from .experiments import (
    AdmissionResult,
    ConclusionResult,
    DecisionResult,
    PromotionResult,
    RejectionResult,
    ReviseResult,
    SealResult,
)
from .importer import STATUS_KNOWN, STATUS_NEW, STATUS_REJECTED, STATUS_RERUN, ListResult, SyncResult
from .lineage import REF_ABSENT, Gate, PreparedPromotion
from .phase import (
    COUNTERFACTUAL_COMPLETED,
    COUNTERFACTUAL_FAILED,
    COUNTERFACTUAL_NONE,
    COUNTERFACTUAL_REQUESTED,
    COUNTERFACTUAL_RUNNING,
    ROUND_CANDIDATE_READY,
    ROUND_OPEN,
    Action,
    BatchRecord,
    LifecycleRevisions,
    LifecycleStatus,
    Promotion,
    ReleaseReading,
    Rollback,
)
from .replay import Evidence, PendingRun, RequestWithdrawn, RunConcluded, WithdrawnRequest
from .rollback import RollbackResult

FIELD_WIDTH = 13
HEX_DIGITS = "0123456789abcdef"


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


# --- what a lifecycle verb did -----------------------------------------------
#
# Each of these reports two things that read alike and are not the same: what is
# on record now, and what *this run* put there. Every operation in this package
# is redoable by being run again with the same arguments, so a command that
# finished someone else's interrupted work — or found the whole thing already
# done — has to say so rather than report a decision it only read. The flags the
# operations return for exactly this (`created`, `recorded`, `sealed`, `opened`,
# `successor_created`, and a copy's `restored`) are what these lines are of.


def format_admission(result: AdmissionResult, repo_root: Path) -> str:
    """A grouped admission, or drafts added to the round that is open.

    One formatter for both because they return one result and describe one
    fact — which drafts this experiment's open round now holds. `created` is what
    tells them apart, and `recorded` is the separate question of whether this run
    made the admission or found it: a redone selection has the record already and
    only its copies left to write, which `add-tasks` reaches by being run twice
    exactly as `create` does.
    """

    opened = "created" if result.created else "already open"
    lines = [
        f"admission: {result.experiment_id} {opened}, round {result.round_number} of {result.batch_id}",
        _field("base", _short(result.base_revision)),
        _field("ref", result.ref),
    ]
    if not result.admitted:
        lines.append(_field("admitted", "no draft — this round already held every one named"))
        return "\n".join(lines)

    for index, item in enumerate(result.admitted):
        note = " (copy written now, from a run that recorded it and stopped)" if item.restored else ""
        lines.append(
            _field("admitted" if index == 0 else "", f"{item.draft_id} → {_relative(item.task_path, repo_root)}{note}")
        )
    if not result.recorded and not result.restored:
        lines.append(_field("", "this run wrote nothing: the admission and its copies were already on record"))
    lines.append(_field("", "a session implementing one of these works on the ref above, never on this checkout"))
    return "\n".join(lines)


def format_rejection(result: RejectionResult, repo_root: Path) -> str:
    """Drafts turned down at the admission gate, and where that is recorded."""

    verb = "declined" if result.recorded else "already declined"
    lines = [f"reject: {len(result.declined)} draft(s) {verb} in {result.batch_id}"]
    for index, draft_id in enumerate(result.declined):
        lines.append(_field("drafts" if index == 0 else "", draft_id))
    lines.append(_field("record", _relative(result.record_path, repo_root)))
    if not result.recorded:
        lines.append(_field("", "this run wrote nothing: the same decision was already on record"))
    lines.append(_field("", "declining is terminal for the proposal; re-arguing the idea means a new draft"))
    return "\n".join(lines)


def format_seal(result: SealResult) -> str:
    """A round made candidate-ready, and the tasks that were observed for it."""

    state = "is candidate-ready" if result.sealed else "was already candidate-ready"
    lines = [
        f"seal-round: {result.experiment_id} round {result.round_number} {state}",
        _field("candidate", _short(result.candidate_revision)),
        _field("sealed", result.sealed_at),
    ]
    if result.observed:
        lines.append(_field("observed", f"{len(result.observed)} task(s) complete: {', '.join(result.observed)}"))
    elif result.sealed:
        # Every task had been observed by a run whose seal did not land: the
        # observations were durable, the pin was what was missing.
        lines.append(_field("observed", "nothing new — an earlier run had observed every task of this round"))
    if not result.sealed:
        lines.append(_field("", "this run wrote nothing: the pin above is the one on record"))
    lines.append(_field("", "replay and any promotion name this revision, never the ref tip"))
    return "\n".join(lines)


def format_revision(result: ReviseResult) -> str:
    """The next round of an experiment, opened from the candidate before it."""

    state = "open" if result.opened else "already open"
    lines = [
        f"revise: {result.experiment_id} round {result.round_number} {state}, revising {_short(result.revised_from)}",
        _field("reason", result.reason),
    ]
    if not result.opened:
        lines.append(_field("", "this run wrote nothing: the same revision was already on record"))
    lines.append(
        _field("", f"the round is empty — `add-tasks` admits what answers it; round {result.round_number - 1}'s evidence still names {_short(result.revised_from)}")
    )
    return "\n".join(lines)


def format_decision(result: DecisionResult) -> str:
    """An attempt turned into history, and the successor a supersession creates.

    Two facts rather than one, because the interruption that costs a supersession
    its successor leaves exactly the state where the decision is on record and the
    attempt it names does not exist — which is what the redo of this command
    finishes.
    """

    named = result.experiment_id if result.recorded else f"{result.experiment_id} was already {result.outcome}"
    lines = [
        f"{result.outcome}: {named}, round {result.round_number} of {result.batch_id}",
        _field("reason", result.reason),
        _field("decided", result.decided_at),
    ]
    if result.successor_id:
        created = "created now" if result.successor_created else "already there"
        lines.append(_field("successor", f"{result.successor_id} ({created})"))
        lines.append(_field("", f"{result.successor_ref} — at the batch's base, with an empty round 1"))
    if not result.recorded and not result.successor_created:
        lines.append(_field("", "this run wrote nothing: the same decision was already on record"))
    return "\n".join(lines)


def format_conclusion(result: ConclusionResult, repo_root: Path) -> str:
    """The batch outcome that ends a cycle having changed nothing."""

    state = "ended" if result.recorded else "had already ended"
    lines = [
        f"conclude-no-change: {result.batch_id} {state} with no change",
        _field("reason", result.reason),
        _field("decided", result.decided_at),
        _field("record", _relative(result.record_path, repo_root)),
    ]
    if not result.recorded:
        lines.append(_field("", "this run wrote nothing: the same conclusion was already on record"))
    lines.append(_field("", "nothing was fabricated on the way out: no candidate, no merge, no deployment"))
    return "\n".join(lines)


def format_promotion(result: PromotionResult, repo_root: Path) -> str:
    """A candidate carried onto the source line, and the batch it ended.

    The merge is reported as the unit it is — the tree, and the three commits
    that identify it — because that is what a promotion put there, rather than a
    pair of commits somebody may re-merge later into something else. The targets
    stay labelled as the plan they are, for `_promotion_lines`' reason: nothing
    here deploys anything.
    """

    state = "is on" if result.recorded else "was already on"
    lines = [
        f"promote: {result.experiment_id} round {result.round_number} {state} {result.merge_input_ref}, "
        f"ending {result.batch_id}",
        _field("merge", f"{_short(result.promotion_revision)}, tree {_short(result.tree)}"),
        _field(
            "",
            f"{_short(result.candidate_revision)} onto {result.merge_input_ref} at "
            f"{_short(result.merge_input_revision)}",
        ),
        _field("reason", result.reason),
        _field("decided", result.decided_at),
        _field("record", _relative(result.record_path, repo_root)),
    ]
    planned = ", ".join(result.planned_targets) if result.planned_targets else "none named"
    lines.append(_field("planned", f"{planned} — this deployed nothing; `aii-2 deploy` is what reaches a target"))
    if not result.merged and not result.recorded:
        lines.append(_field("", "this run wrote nothing: the promotion above is the one on record"))
    elif not result.merged:
        # The one interruption this verb has: the merge reached the line and the
        # records did not, so a second run recognises its own commit and finishes
        # rather than promoting twice.
        lines.append(
            _field("", "the source line already carried this merge — a run that made it and stopped before its records")
        )
    return "\n".join(lines)


def format_rollback(result: RollbackResult, repo_root: Path) -> str:
    """A promotion taken back off the line, by the commit that took it.

    Both facts are stated because both are true afterwards: the promotion is
    still what it was, and the line no longer carries it. Nothing about the
    experiment or the batch outcome changes here, which is the sentence at the
    end — an operator reading "rolled back" as "un-promoted" would look for a
    reopened experiment that does not exist.
    """

    state = "is off" if result.recorded else "was already off"
    lines = [
        f"rollback: {_short(result.promotion_revision)} {state} {result.source_ref} ({result.batch_id})",
        _field("inverse", f"{_short(result.revision)} from {_short(result.reverted_from)}, tree {_short(result.tree)}"),
        _field("reason", result.reason),
        _field("reverted", result.reverted_at),
        _field("record", _relative(result.record_path, repo_root)),
    ]
    if not result.reverted and not result.recorded:
        lines.append(_field("", "this run wrote nothing: the rollback above is the one on record"))
    elif not result.reverted:
        lines.append(
            _field("", "the line already carried this inverse — a run that committed it and stopped before its record")
        )
    lines.append(
        _field("", f"{result.experiment_id} stays promoted and the batch stays concluded — history is not edited")
    )
    return "\n".join(lines)


def format_run_ended(result: RunConcluded) -> str:
    """A replay run recorded as failed because its harness could not say.

    What it reports is a *run* ending, not a round: the candidate is untouched
    and the answer to a failed run is another attempt against the same pinned
    integration.
    """

    replay = result.replay
    state = "ended" if result.recorded else "had already ended"
    detail = replay.result.detail if replay.result is not None else ""
    lines = [
        f"replay-abandon: {result.experiment_id} round {replay.round_number} attempt {replay.attempt} {state} failed",
        _field("reason", detail),
        _field("candidate", _short(replay.integration.candidate_revision)),
    ]
    if not result.recorded:
        lines.append(_field("", "this run wrote nothing: the same failure was already on record"))
    lines.append(_field("", "the round is unmeasured again — another attempt answers it, from `replay-start`"))
    return "\n".join(lines)


def format_request_withdrawn(result: RequestWithdrawn) -> str:
    """A replay request given up before it ever became a run.

    The position it held is named because the harness may still be running
    something under it: this controller will never hear about that run, and
    stopping it is done at the harness.
    """

    pending = result.pending
    if pending is None or not result.withdrawn:
        return "\n".join(
            [
                f"replay-withdraw: nothing outstanding against {result.experiment_id}",
                _field("", "this run wrote nothing: no request stands, which is also what its redo reports"),
            ]
        )
    return "\n".join(
        [
            f"replay-withdraw: {result.experiment_id} round {pending.round_number} attempt {pending.attempt} "
            "given up",
            _field("requested", pending.requested_at),
            _field("pinned", f"{_short(pending.integration.candidate_revision)}, tree {_short(pending.integration.tree)}"),
            _field("", "the position stays allocated — the next request takes the one after it"),
            _field("", "a run may still be going at the harness under that key; stopping it is done there"),
        ]
    )


def format_reading(result: Formed, repo_root: Path, *, resolved: bool) -> str:
    """This cohort's reading of the release before it, recorded or revised.

    One formatter for both because they record one thing — the verdict this
    batch holds about that release — and the difference is what it was read
    from: the two cohorts, or the counterfactual that answered them.
    """

    reading = result.assessment
    verb = "assess-resolve" if resolved else "assess"
    state = "reads" if result.recorded else "already read"
    lines = [
        f"{verb}: {result.batch_id} {state} {_short(reading.subject.revision)} as "
        f"{reading.verdict} ({reading.confidence} confidence)",
        _field("release", f"{reading.subject.batch_id} ({reading.subject.experiment_id})"),
        _field("cohorts", f"{reading.before_task_count} task(s) before, {reading.after_task_count} after"),
        _field("rationale", reading.rationale),
        _field("record", _relative(result.record_path, repo_root)),
    ]
    for index, measurement in enumerate(reading.metrics):
        lines.append(_field("metrics" if index == 0 else "", _metric(measurement)))
    if not result.recorded:
        lines.append(_field("", "this run wrote nothing: the same reading was already on record"))
    if not reading.settled:
        lines.append(_field("", "the gate is unanswered — `settle` is what the next base freeze waits on"))
    return "\n".join(lines)


def format_counterfactual_ended(result: Concluded) -> str:
    """The pinned counterfactual recorded as failed because its harness could not
    say. Another `assess-measure` answers it, at the position after this one."""

    run = result.run
    state = "ended" if result.recorded else "had already ended"
    detail = run.result.detail if run.result is not None else ""
    lines = [
        f"assess-abandon: {result.batch_id} counterfactual attempt {run.position.attempt} {state} failed",
        _field("reason", detail),
        _field("pinned", f"{_short(run.integration.base_revision)} against {_short(run.integration.candidate_revision)}"),
    ]
    if not result.recorded:
        lines.append(_field("", "this run wrote nothing: the same failure was already on record"))
    lines.append(_field("", "the release is unmeasured again — another run answers it, from `assess-measure`"))
    return "\n".join(lines)


def format_counterfactual_withdrawn(result: Withdrawn) -> str:
    """A counterfactual request given up before it ever became a run."""

    request = result.request
    if request is None or not result.withdrawn:
        return "\n".join(
            [
                f"assess-withdraw: nothing outstanding against {result.batch_id}",
                _field("", "this run wrote nothing: no request stands, which is also what its redo reports"),
            ]
        )
    return "\n".join(
        [
            f"assess-withdraw: {result.batch_id} counterfactual attempt {request.position.attempt} given up",
            _field("requested", request.requested_at),
            _field(
                "pinned",
                f"{_short(request.integration.base_revision)} against "
                f"{_short(request.integration.candidate_revision)}",
            ),
            _field("", "the position stays allocated — the next request takes the one after it"),
            _field("", "a run may still be going at the harness under that key; stopping it is done there"),
        ]
    )


def format_settlement(result: Settled, repo_root: Path) -> str:
    """The release gate answered, and what answering it did to the source line.

    A `rolled-back` settlement performs the reversal itself, so the commit it
    made is reported here rather than left for the operator to run separately —
    and a settlement that adopted a reversal already on the line says so, since
    nothing was committed by this run.
    """

    decision = result.decision
    state = "settled" if result.recorded else "was already settled"
    lines = [
        f"settle: {result.batch_id} {state} {decision.settlement} for "
        f"{_short(result.assessment.subject.revision)}",
        _field("reason", decision.reason),
        _field("decided", decision.decided_at),
        _field("record", _relative(result.record_path, repo_root)),
    ]
    if decision.settlement == SETTLEMENT_RETAIN:
        lines.append(_field("", "the release stays the line the first experiment of this batch freezes its base on"))
    elif result.reversal is not None:
        lines.append(_field("inverse", f"{_short(result.reversal.revision)} — committed by this settlement"))
    elif decision.rollback_revision is not None:
        lines.append(
            _field("inverse", f"{_short(decision.rollback_revision)} — already on the line when the gate answered")
        )
    if not result.recorded:
        lines.append(_field("", "this run wrote nothing: the same answer was already on record"))
    return "\n".join(lines)


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

    lines.extend(_concluded_lines(status.batches))
    lines.extend(_gate_lines(status.gate))
    lines.extend(_experiment_lines(status))
    lines.extend(_prepared_lines(status.prepared_promotion))
    lines.extend(_replay_lines(status.evidence))
    lines.extend(_request_lines(status.replay_request, status.replay_withdrawn))
    lines.extend(_revision_lines(status.revisions, open_experiment=status.experiment is not None))
    lines.extend(_release_lines(status.release))
    lines.extend(_promotion_lines(status.last_promotion))
    lines.extend(_action_lines(status))
    return "\n".join(lines)


def _action_lines(status: LifecycleStatus) -> list[str]:
    """What may be done next, and how much of the rest refuses.

    The verbs are the one thing the lines above cannot say. Their refusals are
    what those lines already explain — a round nobody sealed, evidence that went
    stale, a release nobody settled — so this names the count and leaves the
    sentences to the JSON, where a surface acting on them reads them. Every one
    is emitted there; what is bounded here is how much of it an operator who can
    already see the state has to scroll past.

    A verb available because this state already holds its record is marked
    `(redo)` rather than listed as though there were something new to do. That
    distinction is the whole of what makes the list readable: `seal-round` beside
    a round already sealed reads as work outstanding, when what it offers is to
    report the pin — and the sentence saying so is in the JSON with the refusals.

    The revision comes last and is deliberately the last line of the whole
    reading: it is what an operator passes to the verb they just chose, and it
    describes everything above it.
    """

    if not status.actions:
        # Nothing was derived — a reading assembled by hand rather than a state
        # in which every verb refuses, which is a different thing to say.
        return []
    allowed = status.allowed
    lines: list[str] = []
    for index, action in enumerate(allowed):
        lines.append(_field("actions" if index == 0 else "", _action(action)))
    if not allowed:
        lines.append(_field("actions", "none — every verb refuses in this state"))
    refused = len(status.actions) - len(allowed)
    if refused:
        lines.append(_field("", f"{refused} other verb(s) refuse here; `--json` states each reason"))
    if status.state_revision:
        lines.append(_field("state", f"{status.state_revision} — what this reading is of"))
    return lines


def _action(action: Action) -> str:
    """One verb, the object it would act on — the id it is given — and whether
    what it offers is a fresh run or the redo of one already on record."""

    named = action.action if action.object_id is None else f"{action.action} — {_short(action.object_id)}"
    return f"{named} (redo)" if action.recovery is not None else named


def _short(object_id: str) -> str:
    """A revision is shortened the way every other revision here is.

    A batch or experiment id is left whole: it is already the name an operator
    types, and the JSON states every id in full for whatever passes it on.
    """

    return object_id[:12] if len(object_id) == 40 and not object_id.strip(HEX_DIGITS) else object_id


def _concluded_lines(batches: tuple[BatchRecord, ...]) -> list[str]:
    """The batches that ended, and how.

    Every one of them, uncapped: what a cohort follows is a position in this
    series, and a list that quietly stopped at the newest few would read as the
    whole history to anyone counting back from it. The current batch is not here
    — it is reported above, where what it is waiting for is.
    """

    ended = [item for item in batches if not item.current]
    lines: list[str] = []
    for index, item in enumerate(ended):
        lines.append(_field("concluded" if index == 0 else "", _concluded(item)))
    return lines


def _concluded(item: BatchRecord) -> str:
    promotion = item.promotion
    if promotion is None:
        return f"{item.batch_id} {item.outcome} — {item.reason}"
    rollback = promotion.rollback
    if rollback is None:
        tail = ""
    elif rollback.reverted_at is None:
        tail = " (rollback prepared, not on the line)"
    else:
        tail = " (rolled back)"
    return f"{item.batch_id} promoted {promotion.revision[:12]} from {promotion.experiment_id}{tail}"


def _prepared_lines(prepared: PreparedPromotion | None) -> list[str]:
    """A promotion prepared and not finished, which is the narrowest state here.

    Reported rather than met as a refusal: while it stands the merge may already
    be on the source line with only its records missing, so every other verb on
    this experiment refuses and the operator has one thing to do. Which of the
    two ways it ends — finished, or discarded once the line proves it never
    arrived — is the promotion's own reading of Git and not something to guess
    at here.
    """

    if prepared is None:
        return []
    return [
        _field(
            "prepared",
            f"promotion of {prepared.revision[:12]} — {prepared.candidate_revision[:12]} onto "
            f"{prepared.merge_input_ref} at {prepared.merge_input_revision[:12]}, tree {prepared.tree[:12]}",
        ),
        _field(
            "",
            f"prepared {prepared.prepared_at} for {prepared.reason!r} — promote finishes it, or discards it "
            "once the line proves it never arrived; nothing else runs on this experiment until then",
        ),
    ]


def _request_lines(request: PendingRun | None, withdrawn: tuple[WithdrawnRequest, ...]) -> list[str]:
    """Replay work the harness was asked for and never answered about.

    The outstanding request is also a `drift` note above, and is stated here as
    the position it holds because that is what the two answers to it need. It is
    left out of `drift` exactly when the evidence is promotable, so this line is
    the only place a promotable round reports one — "no note" never means
    "nothing outstanding".

    Withdrawals are listed for the one thing that can still be done about them:
    each is a position given up while the harness may have been running it, and
    nothing here will ever hear how that ended.
    """

    lines: list[str] = []
    if request is not None:
        lines.append(
            _field(
                "request",
                f"round {request.round_number} attempt {request.attempt} outstanding since "
                f"{request.requested_at} — a run may be going; start the replay again to record it, "
                "or withdraw the request",
            )
        )
    if withdrawn:
        positions = ", ".join(f"round {taken.round_number} attempt {taken.attempt}" for taken in withdrawn)
        lines.append(
            _field(
                "withdrawn",
                f"{positions} — given up without becoming runs; each may still be running at the harness, "
                "and no position is ever reissued",
            )
        )
    return lines


def _promotion_lines(promotion: Promotion | None) -> list[str]:
    """The last promotion, as the merge it was and the plan it carried.

    The targets are labelled `planned` on their own line rather than listed
    beside the revision, because the one thing this must not read as is a report
    of what those repositories hold. A promoted revision reaches a target when
    that target is redeployed and not before, and `aii-2 status <target>` is
    what answers for it.
    """

    if promotion is None:
        return []
    lines = [
        _field(
            "promoted",
            f"{promotion.revision[:12]} from {promotion.experiment_id} round {promotion.round_number} "
            f"({promotion.batch_id})",
        ),
        _field(
            "",
            f"{promotion.candidate_revision[:12]} onto {promotion.merge_input_ref} at "
            f"{promotion.merge_input_revision[:12]}, tree {promotion.tree[:12]}",
        ),
    ]
    planned = ", ".join(promotion.planned_targets) if promotion.planned_targets else "none named"
    lines.append(_field("", f"planned targets: {planned} — deployed only where `aii-2 status` says so"))
    lines.extend(_rollback_lines(promotion.rollback))
    return lines


def _rollback_lines(rollback: Rollback | None) -> list[str]:
    """The reversal of that promotion, when there is one.

    Shown under the promotion rather than instead of it, because both are true:
    the commit was promoted and a later commit took the change back out. An
    inverse the line has not been recorded as carrying is named as the operation
    in flight it is — the one state here whose next step is to run the rollback
    again.
    """

    if rollback is None:
        return []
    if rollback.reverted_at is None:
        prepared = (
            f"rollback prepared as {rollback.revision[:12]} from {rollback.reverted_from[:12]} — the source line "
            "has not been recorded as carrying it"
        )
        if rollback.unconfirmed is None:
            return [_field("", f"{prepared}; run the rollback again to finish it")]
        # The one reading here that acts differently from how it reads: the
        # record stands and the operation refuses on it, so telling an operator
        # to run it again would offer a verb that will not run.
        return [
            _field(
                "",
                f"{prepared}, and this checkout cannot confirm that commit — {rollback.unconfirmed}; it is "
                "finished from a checkout holding the commits it names",
            )
        ]
    return [_field("", f"rolled back by {rollback.revision[:12]} at {rollback.reverted_at} — {rollback.reason}")]


def _release_lines(release: ReleaseReading | None) -> list[str]:
    """The reading of the release before this batch — the gate the next first
    experiment base waits on (invariant 17).

    Absent for most batches, and that is ordinary: only a promotion produces
    something to assess. What is never ordinary is the shape of the absence, so
    every part of this says which absence it is — a cohort that has recorded
    nothing, cohorts with nothing in them, a check this checkout could not make,
    and a reading nobody has settled are four different next steps.
    """

    if release is None:
        return []
    frame = release.frame
    subject = frame.subject
    lines = [
        _field(
            "release",
            f"{subject.revision[:12]} from {subject.batch_id} ({subject.experiment_id}) — {_standing(subject)}",
        )
    ]
    owner = "this batch" if release.owned_here else f"{release.owner_id}, which has already concluded"
    lines.append(_field("", f"the reading of it is owed by {owner}"))
    lines.extend(_cohort_lines(release))
    lines.extend(_counterfactual_lines(release))
    lines.extend(_reading_lines(release))
    return lines


def _standing(subject: Subject) -> str:
    """Whether the source line still carries the release.

    Three states, because an inverse commit that the line has not been recorded
    as carrying is neither of the other two: the promotion stands, and something
    is half-done about it.
    """

    if not subject.standing:
        return f"reversed by {(subject.rollback_revision or '')[:12]}"
    if subject.rollback_revision is not None:
        return f"still on the source line; an inverse commit {subject.rollback_revision[:12]} is prepared against it"
    return "still on the source line"


def _cohort_lines(release: ReleaseReading) -> list[str]:
    """The denominators, the exclusions, and what the two together can carry.

    Precedence matters here and it is why the facets are not simply listed: an
    empty cohort fails every facet, so a surface reaching for the facet list over
    one would name a provenance mismatch when what happened is that no report
    could be placed at all. The exclusions are the answer in that case, and the
    numbers are absent evidence — never a reading against the release.
    """

    frame = release.frame
    before, after = frame.before.task_count, frame.after.task_count
    lines = [
        _field(
            "",
            f"cohorts: {before} unique task(s) before the release, {after} after; "
            f"a direction needs {frame.minimum_task_count} each",
        )
    ]
    if frame.excluded:
        counted: dict[str, int] = {}
        for item in frame.excluded:
            counted[item.reason] = counted.get(item.reason, 0) + 1
        placed = ", ".join(f"{reason} ({count})" for reason, count in sorted(counted.items()))
        lines.append(_field("", f"{len(frame.excluded)} of {len(frame.catalog)} report(s) excluded: {placed}"))
    if not before or not after:
        lines.append(
            _field(
                "",
                "an empty cohort carries no evidence either way — absent evidence, not a defect and not a "
                "reading against the release",
            )
        )
    else:
        lines.extend(_facet_lines(frame))
    if frame.unverified:
        lines.append(
            _field(
                "",
                f"{len(frame.unverified)} report(s) this checkout could not place: it cannot resolve what the "
                "target held — an unanswered check, never agreement",
            )
        )
    return lines


def _facet_lines(frame: Frame) -> list[str]:
    """Why the cohorts do or do not carry a direction on their own.

    The two ways a facet fails are separated because only one of them is about
    the cohorts: a facet no manifest states is a gap in what the feed publishes,
    and rendering it beside a real mismatch would read as broken provenance in
    both directions. `task-shape` is the standing example — unknown on every
    manifest version, which is exactly why a direction rests on the
    counterfactual instead.
    """

    incoherent = [facet for facet in frame.comparability.facets if not facet.coherent]
    if not incoherent:
        return [_field("", "the cohorts are comparable in every stated facet")]
    unstated = [facet.facet for facet in incoherent if not (set(facet.before) | set(facet.after)) - {None}]
    differing = [facet.facet for facet in incoherent if facet.facet not in unstated]
    lines: list[str] = []
    if differing:
        lines.append(
            _field("", f"the cohorts differ in {', '.join(differing)} — the release is not the only difference")
        )
    if unstated:
        lines.append(
            _field(
                "",
                f"no manifest states {', '.join(unstated)}, so the cohorts alone carry no direction — "
                "one rests on the pinned counterfactual",
            )
        )
    return lines


def _counterfactual_lines(release: ReleaseReading) -> list[str]:
    """The pinned before/after run, in whichever of its four states it stands.

    A request outstanding is reported ahead of the run it retries, because the
    run is history and the request is a comparison that may be going at a
    harness this repository will never hear from again. The failed run stays
    named beside it: it is why another was asked for.

    The positions given up are reported under every one of the states, the
    absence included: a request withdrawn leaves no run, no request, and a
    comparison that may still be going — so the state that says least about it is
    the one it would go missing from.
    """

    state = release.counterfactual_state
    run = release.counterfactual
    reading = release.reading
    requested = None if reading is None else reading.requested

    lines: list[str] = []
    if state == COUNTERFACTUAL_NONE:
        if reading is not None:
            lines.append(_field("", "counterfactual: none — nothing has measured this release directly"))
    elif state == COUNTERFACTUAL_REQUESTED and requested is not None:
        where = f"round {requested.position.round_number} attempt {requested.position.attempt}"
        lines.append(
            _field(
                "",
                f"counterfactual: requested at {requested.requested_at} ({where}) — a run may be going; "
                "ask again to record it, or withdraw the request",
            )
        )
        if run is not None and run.failed:
            lines.append(_field("", f"the run it retries failed: {run.result.detail if run.result else ''}"))
    elif run is not None:
        where = f"round {run.position.round_number} attempt {run.position.attempt}"
        if state == COUNTERFACTUAL_RUNNING:
            lines.append(_field("", f"counterfactual: running since {run.started_at} at {run.harness.id} ({where})"))
        elif state == COUNTERFACTUAL_FAILED:
            detail = run.result.detail if run.result else ""
            lines.append(_field("", f"counterfactual: failed ({where}) — {detail}; ask for another to measure again"))
        elif state == COUNTERFACTUAL_COMPLETED:
            lines.append(
                _field(
                    "",
                    f"counterfactual: completed ({where}) — {run.integration.base_revision[:12]} against "
                    f"{run.integration.candidate_revision[:12]}, expected {run.expectation!r}",
                )
            )

    if reading is not None and reading.withdrawn:
        positions = ", ".join(
            f"round {taken.position.round_number} attempt {taken.position.attempt}" for taken in reading.withdrawn
        )
        lines.append(
            _field("", f"positions given up: {positions} — each may still be running where nothing here will hear")
        )
    return lines


def _reading_lines(release: ReleaseReading) -> list[str]:
    """What was recorded, and what the human gate still owes.

    The settlement is stated as the commit it selects rather than as a word,
    because the thing an operator is about to do with it is freeze a base: the
    first experiment of the next cohort has to stand on the line the answer
    chose, and discovering that from a refusal is the miss this line exists to
    prevent.

    The release line above says what the source line carries now; this says what
    the reading is about, and after a `rolled-back` settlement they are two
    different states of one release — the ordinary end of a regression finding,
    where the verdict was reached over a release that was still on the line.
    """

    reading = release.reading
    if reading is None:
        return [
            _field(
                "",
                "no reading recorded — the analysis of this release forms one, and the next first experiment "
                "base waits on it being settled (invariant 17)",
            )
        ]
    lines = [
        _field("", f"reading: {reading.verdict} ({reading.confidence}) formed {reading.formed_at} — {reading.rationale}")
    ]
    if release.reversed_after_reading:
        lines.append(
            _field(
                "",
                "read while the release was still on the source line — the inverse commit came after it, so this "
                "verdict is a reading of the release and not of the reversal",
            )
        )
    goals = [item for item in reading.metrics if item.goal]
    if goals:
        lines.append(_field("", f"goal metrics: {', '.join(_metric(item) for item in goals)}"))

    decision = reading.decision
    if decision is None:
        if release.in_flight:
            lines.append(
                _field(
                    "",
                    "not settled, and a measurement is still in flight — nothing may be added once the gate "
                    "answers, so conclude the run, end it, or withdraw the request first",
                )
            )
        else:
            lines.append(_field("", "not settled — retain the release or roll it back; the next base freeze waits"))
        return lines

    selected = (
        release.frame.subject.revision if decision.settlement == SETTLEMENT_RETAIN else (decision.rollback_revision or "")
    )
    lines.append(_field("", f"settled {decision.settlement} at {decision.decided_at} — {decision.reason}"))
    lines.append(_field("", f"the next first experiment base must contain {selected[:12]}"))
    return lines


def _metric(measurement: Measurement) -> str:
    before = "?" if measurement.before is None else f"{measurement.before:g}"
    after = "?" if measurement.after is None else f"{measurement.after:g}"
    return f"{measurement.metric} {before}→{after} {measurement.unit} ({measurement.better} is better)"


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


def _replay_lines(evidence: Evidence | None) -> list[str]:
    """What the current round has been measured by, and what stops it counting.

    The two shortfalls are shown apart because an operator acts on them
    differently: `drift` is something that happened — a later round, a source
    line that moved, a run that failed — and is answered by running again;
    `unverified` is a question this checkout could not put to Git, and is
    answered by fetching the ref and asking again. A promotion is refused on
    either.
    """

    if evidence is None:
        return []
    run = evidence.replay
    where = "" if run is None else f" (round {run.round_number} attempt {run.attempt})"
    lines = [_field("replay", f"{evidence.state}{where}")]
    if evidence.promotable and run is not None and run.result is not None:
        lines.append(_field("", run.result.detail))
    for note in evidence.drift:
        lines.append(_field("", note))
    for note in evidence.unverified:
        lines.append(_field("", f"unverified here: {note}"))
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
    pending = status.pending_successor
    if pending is not None:
        # The one state where an operator has to act on the lifecycle itself
        # rather than on the work: nothing else will run until the supersession
        # that recorded its decision creates the attempt it named.
        lines.append(
            _field(
                "supersede",
                f"{pending.experiment_id} named {pending.successor_id}, which was never created — "
                "redo that supersession, for the reason on record, to finish it",
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


def _revision_lines(revisions: LifecycleRevisions, *, open_experiment: bool) -> list[str]:
    """The base is what the other two hang off — a batch with no experiment has
    no revisions in play at all, rather than three absent ones. The three are
    never collapsed into one line: substituting one for another is what an
    evidence trail must not do (contract: Revisions in play).

    The candidate and the tip belong to the *open* experiment, so why they are
    absent has to be read off whether there is one. Between attempts — every
    experiment abandoned or superseded, the batch waiting for its next one or
    for its outcome — there is no round to have left unsealed and no ref was
    looked up, and reporting the two ordinary absences there states two things
    about an experiment that does not exist.
    """

    if revisions.base is None:
        return [_field("revisions", "none in play — no experiment has frozen a base")]
    if not open_experiment:
        return [
            _field("base", revisions.base.describe()),
            _field("", "no experiment is open — nothing is pinned and no ref was read"),
        ]
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
