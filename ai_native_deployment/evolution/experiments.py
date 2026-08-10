"""The human admission gate: what a decision to implement a proposal writes.

`lineage.py` reads the batch and experiment records; this module is what writes
them. Between analysis and implementation sits one human gate (invariant 9), and
these are the three operations on it:

- **create** — the grouped admission. A person picks the drafts that belong
  together, and one operation creates the experiment's ref at the batch's base
  revision, the experiment record with its first round, and one `.ai-tasks/` copy
  per admitted draft.
- **add-tasks** — further drafts admitted into the round that is open.
- **reject** — a draft turned down, recorded so the gate stops waiting for a
  decision that was already made.

**What makes the operation real.** Each writes several places at once — a Git
ref, a versioned record, `.ai-tasks/` and its index, the audit ledger — and the
order they are written in is the recovery story:

1. the ref, because it is the one thing that must never be created twice or
   recreated later: a clone missing `refs/evolution/*` is the ordinary state, and
   a repair that "restored" the ref at the base would put it behind the real work
   and let the next commit fork the history;
2. the experiment record, which is what makes the admission real — the drafts are
   consumed, the tasks are named, the base is frozen;
3. the task copies and their index rows, derivable from the record and the
   drafts, and therefore restorable;
4. the audit line, which nothing derives state from.

So an interruption can leave a ref with no record (inert; the next create with
the same selection adopts it), or a record whose task copies never landed (the
same command, run again, finishes them). It cannot leave a task in the active
pool that no record accounts for, which is the one direction that matters: an
orphaned active task is work a turn selection will dispatch with nothing behind
it. Redoing the same selection is the resume path throughout — the operations
recognise their own interrupted work rather than requiring a repair by hand.

**Copies, not moves** (contract: Change admission). The draft stays in the batch
that proposed it, and the experiment record holds the draft id, the sha256 of the
bytes admitted, and the task id the copy took. The copy is the draft plus one
`## Admission` section naming the batch, the experiment, the round, the base
revision, and the ref to work on — facts that do not exist when the draft is
written and that the session implementing it cannot proceed without.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..hashing import sha256_bytes
from . import analysis_task
from .batches import awaiting_analysis, record_closures
from .config import EXPERIMENT_SCHEMA_FILENAME, EvolutionConfig
from .errors import BatchError
from .ledger import append_records, build_record
from .lineage import (
    EXPERIMENT_FILENAME,
    EXPERIMENT_SCHEMA_VERSION,
    AdmittedTask,
    BatchLineage,
    Experiment,
    Round,
    experiment_ref,
    format_experiment_id,
    is_draft_id,
)
from .lineage import describe as describe_lineage
from .config import REJECTED_DRAFTS_SCHEMA_FILENAME
from .manifests import REJECTED_DRAFTS_SCHEMA_VERSION, Batch, read_rejected_drafts
from .revisions import create_ref, ref_tip, release_ref, resolve_commit
from .schema import format_rfc3339, load_schema, validate_or_raise
from .state import atomic_write_text, single_writer_lock

RECORD_EXPERIMENT_CREATED = "experiment-created"
RECORD_TASKS_ADMITTED = "tasks-admitted"
RECORD_DRAFT_REJECTED = "draft-rejected"

DRAFT_SUFFIX = ".md"

# The taskfile schema's `id: <date-prefixed-slug>`, which is also the file name
# the copy takes — so this is the containment check as well as the shape one: one
# path segment, no traversal, no dot-file, no extension to confuse for one.
_TASK_ID = re.compile(r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9]+(-[a-z0-9]+)*\Z", re.ASCII)

# What an inert proposal looks like. A draft carrying anything else has been
# worked on where nothing dispatches it.
DRAFT_STATUS = "pending"
# `session-est: 0/<total>`: a dev session increments the current count as part of
# its claim (taskfile schema §4), so a draft nobody has worked on is still at 0
# — and a total of zero sessions is an estimate for no work at all.
_UNCONSUMED_SESSION_EST = re.compile(r"\A0/[1-9][0-9]*\Z", re.ASCII)
EMPTY_BLOCKERS = "[]"
ADMISSION_HEADING = "## Admission"
SESSION_LOG_HEADING = "## Session log"

# The body a task file carries: what the work is, what it covers, how it is
# recognised as done, and where it records itself. The intake contract writes all
# four for a pending task, and the dev and review contracts each read one of them
# — an admitted copy joins the same pool and is worked by the same sessions.
REQUIRED_SECTIONS = ("## Goal", "## Scope", "## Acceptance", SESSION_LOG_HEADING)

# Bound on the index-row summary lifted out of the draft's own title. The active
# index is a list, not a description.
_MAX_SUMMARY = 96


@dataclass(frozen=True)
class Admitted:
    """One draft's admission, as the operation carried it out."""

    draft_id: str
    task_id: str
    draft_sha256: str
    task_path: Path
    # True when this run wrote the copy to finish an admission that was already
    # recorded, rather than admitting the draft now.
    restored: bool = False


@dataclass(frozen=True)
class AdmissionResult:
    batch_id: str
    experiment_id: str
    round_number: int
    base_revision: str
    ref: str
    admitted: tuple[Admitted, ...]
    # False when the experiment already existed: `add-tasks`, or the same
    # selection run again after an interruption.
    created: bool

    @property
    def restored(self) -> tuple[str, ...]:
        """Task ids this run wrote to finish work an earlier run recorded."""

        return tuple(item.task_id for item in self.admitted if item.restored)


@dataclass(frozen=True)
class RejectionResult:
    batch_id: str
    declined: tuple[str, ...]
    record_path: Path
    # False when this run wrote nothing because the decision was already on
    # record: the same selection redone after an interruption. The drafts are
    # declined either way, and only the caller reporting it needs the difference.
    recorded: bool = True


def create(
    config: EvolutionConfig,
    draft_ids: Iterable[str],
    *,
    base: str | None = None,
    reason: str | None = None,
    now: datetime | None = None,
) -> AdmissionResult:
    """Admit a group of drafts as a new experiment on the current batch.

    The first experiment of a batch freezes that batch's base revision — not the
    batch freeze, which happens before anyone knows a change is warranted
    (invariant 15). `base` defaults to `HEAD`, which is the source commit the
    operator is starting the work from; every later experiment of the same batch
    is created from the commit the first one froze, and a `base` naming anything
    else is refused rather than reconciled.

    Running it again with the same selection is the resume path, not a second
    admission: it finishes whatever the interrupted run left and reports what it
    completed.
    """

    moment = _moment(now)
    with single_writer_lock(config):
        current = _current_cycle(config, now=moment)
        requested = _requested(draft_ids)
        batch = current.batch

        open_experiment = current.open_experiment
        if open_experiment is not None:
            # Both before the redo, not after it: a resumed admission still has a
            # base, and an operator naming a different one is asking for something
            # this is not about to do — and a redo writes, so it is as guarded as
            # the admission it finishes. The copies it writes tell their sessions
            # to commit on a ref, and a ref standing off the history the record
            # pins is not one to send work to.
            _require_requested_base(config, current, open_experiment.base_revision, base)
            _require_consistent_ref(current)
            return _redo_create(config, current, open_experiment, requested)

        base_revision, base_release_ref = _base_revision(config, current, base)
        experiment_id = format_experiment_id(batch.batch_id, len(current.experiments) + 1)
        drafts = _collect(config, current, requested)
        stamp = format_rfc3339(moment)

        experiment = Experiment(
            experiment_id=experiment_id,
            batch_id=batch.batch_id,
            created_at=stamp,
            base_revision=base_revision,
            base_release_ref=base_release_ref,
            ref=experiment_ref(experiment_id),
            rounds=(
                Round(
                    number=1,
                    opened_at=stamp,
                    reason=(reason or "").strip() or _admission_reason(requested),
                    tasks=tuple(_admitted(draft, admitted_at=stamp) for draft in drafts),
                    seal=None,
                ),
            ),
            decision=None,
            directory=config.experiments_root / experiment_id,
        )

        _create_experiment_ref(config, experiment)
        _publish_record(config, experiment)
        written = _write_tasks(config, current, experiment, experiment.rounds[0], drafts)
        append_records(
            config,
            [
                build_record(
                    RECORD_EXPERIMENT_CREATED,
                    recorded_at=stamp,
                    batch_id=batch.batch_id,
                    experiment_id=experiment_id,
                    revision=base_revision,
                ),
                *_admission_records(batch, experiment, experiment.rounds[0], written, recorded_at=stamp),
            ],
        )
        return _result(batch, experiment, experiment.rounds[0], written, created=True)


def add_tasks(
    config: EvolutionConfig,
    draft_ids: Iterable[str],
    *,
    now: datetime | None = None,
) -> AdmissionResult:
    """Admit further drafts into the open experiment's open round.

    A round takes work only while it is open. Once it is candidate-ready its
    candidate is pinned and its evidence names it, so admitting into it would
    change what that evidence measured after the fact — `revise` opens the next
    round instead (invariant 16).
    """

    moment = _moment(now)
    with single_writer_lock(config):
        current = _current_cycle(config, now=moment)
        requested = _requested(draft_ids)
        batch = current.batch

        experiment = current.open_experiment
        if experiment is None:
            raise BatchError(
                f"{batch.batch_id} has no open experiment to admit into; a grouped admission creates one from the "
                "drafts it selects, and every experiment of this batch starts from the base its first one froze"
            )
        round_ = _open_round(experiment)
        _require_consistent_ref(current)
        admitted = {task.draft_id: task for task in round_.tasks}
        already = sorted(requested & set(admitted))
        if already:
            if set(already) == requested:
                # The same selection again: the record is what made the admission
                # real, so what is left of it is the copies.
                return _finish(
                    config,
                    current,
                    experiment,
                    round_,
                    tuple(admitted[draft_id] for draft_id in sorted(requested)),
                )
            raise BatchError(
                f"draft(s) {already} are already admitted into round {round_.number} of "
                f"{experiment.experiment_id}; a draft is consumed once, so redo the same selection to finish an "
                "interrupted admission, or admit only the drafts that are still waiting"
            )

        drafts = _collect(config, current, requested)
        stamp = format_rfc3339(moment)
        opened = replace(
            round_,
            tasks=round_.tasks + tuple(_admitted(draft, admitted_at=stamp) for draft in drafts),
        )
        updated = replace(experiment, rounds=experiment.rounds[:-1] + (opened,))

        _write_record(config, updated)
        written = _write_tasks(config, current, updated, opened, drafts)
        append_records(config, _admission_records(batch, updated, opened, written, recorded_at=stamp))
        return _result(batch, updated, opened, written, created=False)


def reject(
    config: EvolutionConfig,
    draft_ids: Iterable[str],
    *,
    reason: str,
    now: datetime | None = None,
) -> RejectionResult:
    """Decline drafts at the admission gate, with the reason.

    Recorded rather than deleted. Whether a draft is still waiting is derived
    from what took it or turned it down, so without this record a declined
    proposal waits forever — and deleting the file instead would leave "why is
    this gone" a question only `git log` answers. Declining is terminal for the
    proposal: re-proposing the idea means a new draft id.
    """

    moment = _moment(now)
    text = " ".join((reason or "").split())
    if not text:
        raise BatchError(
            "declining a draft records why; the reason travels with the batch and is what keeps a rejected "
            "proposal from being re-argued from memory"
        )

    with single_writer_lock(config):
        current = _current_cycle(config, now=moment)
        batch = current.batch
        requested = _requested(draft_ids)
        recorded = {entry["draft_id"]: entry for entry in read_rejected_drafts(config, batch)}
        already = sorted(requested & set(recorded))
        if already:
            return _redo_reject(batch, requested, already, recorded, text)

        drafts = _collect(config, current, requested, for_tasks=False)
        stamp = format_rfc3339(moment)

        record = {
            "schema_version": REJECTED_DRAFTS_SCHEMA_VERSION,
            "batch_id": batch.batch_id,
            "rejected": [
                *(dict(entry) for entry in recorded.values()),
                *(
                    {
                        "draft_id": draft.draft_id,
                        "draft_sha256": draft.sha256,
                        "rejected_at": stamp,
                        "reason": text,
                    }
                    for draft in drafts
                ),
            ],
        }
        validate_or_raise(
            record,
            load_schema(config.schema_path(REJECTED_DRAFTS_SCHEMA_FILENAME)),
            description=f"rejected-drafts record for {batch.batch_id}",
        )
        atomic_write_text(batch.rejected_drafts_path, _json(record))

        append_records(
            config,
            [
                build_record(
                    RECORD_DRAFT_REJECTED,
                    recorded_at=stamp,
                    batch_id=batch.batch_id,
                    draft_id=draft.draft_id,
                )
                for draft in drafts
            ],
        )
        return RejectionResult(
            batch_id=batch.batch_id,
            declined=tuple(draft.draft_id for draft in drafts),
            record_path=batch.rejected_drafts_path,
        )


def _redo_reject(
    batch: Batch,
    requested: set[str],
    already: list[str],
    recorded: Mapping[str, Mapping[str, Any]],
    reason: str,
) -> RejectionResult:
    """The same rejection run again, or a refusal naming the decision on record.

    The record is what makes a rejection real — the audit line after it is not —
    so this exact selection, declined for this exact reason, *is* this operation,
    already done. Saying so is what makes the operation redoable at all: the run
    that published the record and then failed would otherwise have left its own
    retry permanently refused by the work it had already finished.

    Nothing is written, the ledger included. The interruption cost those lines,
    and re-appending them would claim a second decision about a proposal that was
    declined once — the rule a redone admission already follows.

    Anything else is a different decision about a spent proposal: declining is
    terminal, a second reason does not replace the one on record, and re-proposing
    the idea means a new draft id.
    """

    if set(already) != requested:
        raise BatchError(
            f"draft(s) {already} were already declined at {batch.batch_id}'s gate; declining is terminal for a "
            "proposal, so redo the same selection to finish an interrupted rejection, or decline only the drafts "
            "still waiting"
        )
    differing = sorted(draft_id for draft_id in already if recorded[draft_id]["reason"] != reason)
    if differing:
        raise BatchError(
            f"draft(s) {differing} were declined at {batch.batch_id}'s gate for a different reason "
            f"({recorded[differing[0]]['reason']!r}); declining is terminal for a proposal, so the reason on "
            "record stands — re-proposing the idea means a new draft id whose own bytes say what changed"
        )
    return RejectionResult(
        batch_id=batch.batch_id,
        declined=tuple(sorted(requested)),
        record_path=batch.rejected_drafts_path,
        recorded=False,
    )


# --- the guarded preamble ----------------------------------------------------


def _current_cycle(config: EvolutionConfig, *, now: datetime) -> BatchLineage:
    """The batch these operations act on, settled before any of them writes.

    Three questions in one, and all three are the derivation `status` reads
    rather than a cheaper local reading: which batch is current (invariant 14,
    from the whole lineage — an outcome record its own experiments contradict has
    concluded nothing), whether its analysis stage has ended, and what its gate
    and experiments currently are.

    The closure records are published first for the same reason the freeze
    publishes them first: the stage's end is read from the analysis task's own
    lifecycle on the machine that has it, and from the committed record
    everywhere else. Admitting a draft before that stage ends would implement
    dispositions that are still being written.
    """

    record_closures(config, now=now)
    current = describe_lineage(config).current
    if current is None:
        raise BatchError(
            "no batch is current, so there is no admission gate to act on; freeze a cohort with "
            "`aii-2 evolution start` and let its analysis produce the drafts (invariant 14)"
        )
    if awaiting_analysis(config, current.batch):
        raise BatchError(
            f"{current.batch_id} is still in its analysis stage; drafts reach the gate when that task completes "
            f"and {current.batch.closure_path.name} records it — a proposal admitted before then implements "
            "dispositions nobody has reviewed (invariant 6)"
        )
    return current


def _require_consistent_ref(current: BatchLineage) -> None:
    """The open experiment's ref agrees with what its record pins, or nothing
    else is admitted into it.

    The reader deliberately reports a ref disagreement as data rather than
    raising — a status that refused to describe the lifecycle is not how an
    operator learns which ref moved. This is the other half of that decision: an
    operation that writes stops here. Admitting work onto a ref standing off the
    history the record pins would put that work on a tree the record cannot
    identify, and the round's later seal would pin it.

    "Cannot tell" is not a refusal. `refs/evolution/experiments/*` is outside the
    default fetch refspec, so a clone that never fetched the namespace has no
    ref and no answer — the ordinary state everywhere but the machine doing the
    work, and one that says nothing about the lineage.
    """

    ref = current.ref
    if ref is None or ref.consistent is not False:
        return
    if ref.chain_break is not None:
        earlier, later = ref.chain_break
        raise BatchError(
            f"{ref.ref}: {later[:12]} does not descend from {earlier[:12]}, which this experiment's record pins "
            "before it; rounds only add (invariant 15), so a candidate off that history leaves the revisions the "
            "record names unreachable — resolve the ref before admitting anything else into it"
        )
    raise BatchError(
        f"{ref.ref} stands at {(ref.tip or 'nothing')[:12]}, not on the history of the {ref.pinned[:12]} its "
        f"record pins ({ref.state}); the ref only fast-forwards, and work admitted onto it now would be measured "
        "as part of a candidate nobody can identify"
    )


def _requested(draft_ids: Iterable[str]) -> set[str]:
    """The selection, checked as a set of draft ids before it names any path."""

    requested = list(draft_ids)
    if not requested:
        raise BatchError("no draft was named; an admission or a rejection is a decision about specific proposals")
    unusable = sorted({value for value in requested if not is_draft_id(value)})
    if unusable:
        raise BatchError(
            f"{unusable} cannot be draft ids; a draft id is a kebab-case slug, which is what makes it one path "
            f"segment under proposed-tasks/ rather than a name that could reach anywhere else"
        )
    repeated = sorted({value for value in requested if requested.count(value) > 1})
    if repeated:
        raise BatchError(
            f"draft(s) {repeated} are named twice in one selection; a draft is consumed once, and naming it twice "
            "says nothing about which of the two was meant"
        )
    return set(requested)


# --- drafts ------------------------------------------------------------------


@dataclass(frozen=True)
class _Draft:
    """One waiting proposal, read and checked but not yet admitted."""

    draft_id: str
    path: Path
    text: str
    # Where the body starts, read from the frontmatter rather than by looking for
    # something that resembles a section: the admission block goes into the body,
    # and a `## ` line inside an unterminated frontmatter block is not one.
    body_start: int
    sha256: str
    task_id: str
    title: str


def _collect(
    config: EvolutionConfig,
    current: BatchLineage,
    requested: set[str],
    *,
    for_tasks: bool = True,
) -> tuple[_Draft, ...]:
    """Read every requested draft, refusing anything the gate cannot decide.

    `for_tasks` is False for a rejection, which needs the bytes and their hash
    but decides nothing about `.ai-tasks/`: a proposal turned down never becomes
    a task, so requiring it to name a free task id would refuse a decline over a
    detail the decline makes irrelevant.
    """

    gate = current.gate
    for draft_id in sorted(requested):
        if draft_id in gate.waiting:
            continue
        owner = gate.consumed.get(draft_id)
        if owner is not None:
            raise BatchError(
                f"draft {draft_id!r} was already admitted by {owner}; admitting is terminal for a proposal, and "
                "proposing the idea again means a new draft id whose own bytes say what the second proposal was"
            )
        if draft_id in gate.declined:
            raise BatchError(
                f"draft {draft_id!r} was declined at {current.batch_id}'s gate; declining is terminal for a "
                "proposal, and re-proposing means a new draft id"
            )
        raise BatchError(
            f"{_draft_path(current.batch, draft_id)} does not exist; the drafts waiting at "
            f"{current.batch_id}'s gate are {list(gate.waiting)}"
        )

    drafts = tuple(_read_draft(current.batch, draft_id, for_tasks=for_tasks) for draft_id in sorted(requested))
    if for_tasks:
        _require_free_task_ids(config, current, drafts)
    return drafts


def _draft_path(batch: Batch, draft_id: str) -> Path:
    """Where a draft waits. One construction, because the copy's provenance names
    this path and a later run rebuilds that line to recognise the copy."""

    return batch.directory / analysis_task.PROPOSED_TASKS_DIRNAME / f"{draft_id}{DRAFT_SUFFIX}"


def _read_draft(batch: Batch, draft_id: str, *, for_tasks: bool) -> _Draft:
    """One draft's bytes, its hash, and the task identity it declares.

    The draft is a schema-conforming task file, so the copy takes the id the
    draft itself states rather than one this controller invents: the bytes
    admitted and the bytes dispatched then say the same thing about what the task
    is, and `draft_sha256` describes both.
    """

    path = _draft_path(batch, draft_id)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BatchError(f"unreadable draft {path}: {exc}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BatchError(f"{path} is not UTF-8 text; a change-task draft is a task file: {exc}") from exc

    block = analysis_task.parse_frontmatter(text)
    if for_tasks:
        _require_task_file(path, text, block)
    return _Draft(
        draft_id=draft_id,
        path=path,
        text=text,
        body_start=block.body_start,
        sha256=sha256_bytes(raw),
        task_id=block.fields.get("id", ""),
        title=_title(text, draft_id, start=block.body_start),
    )


def _require_task_file(path: Path, text: str, block: analysis_task.Frontmatter) -> None:
    """Refuse a draft that is not the inert task file the gate decides about.

    Admission is a copy, so what is checked here is what the copy becomes: a
    pending task in the active pool, claimed by a session that increments its
    estimate, worked from its scope, and reviewed against its acceptance. A
    proposal that is a task file in name only reaches that pool as one anyway —
    nothing downstream re-reads it as a proposal — so the whole shape is checked
    at the one point where refusing it costs nothing but a redraft.

    The frontmatter block is checked before its fields for a reason of this
    module's own: an unterminated one has no body, and the admission provenance
    goes into the body. A file whose sections are all still inside its
    frontmatter would take the block in there with them.

    The body is read as sections rather than as text for the same kind of reason.
    A heading that occurs is not a section that is there once and says what it is
    for: two goals or two session logs leave every reader taking whichever it
    reaches first, a log with entries under it is a task someone has already
    worked, and an `## Admission` section is the one thing the gate itself adds.
    A line that only looks like a heading is refused before any of that, because
    it is what makes "which section is this line under" a question with more than
    one answer.
    """

    if not block.present:
        raise BatchError(
            f"{path}: no frontmatter block; a draft is a schema-conforming task file, and the lifecycle its copy "
            "is dispatched under — the id it takes, the status the gate decides about — is what that block carries"
        )
    if not block.closed:
        raise BatchError(
            f"{path}: the frontmatter block is never closed by a '---' line; everything below an unterminated one "
            "is still frontmatter, so this file declares no lifecycle to claim and has no body to admit"
        )
    if block.duplicated:
        raise BatchError(
            f"{path}: frontmatter declares {list(block.duplicated)} more than once; a field stated twice says two "
            "things about one task, and every reader takes whichever it reaches first"
        )

    task_id = _field(path, block, "id")
    if not _TASK_ID.match(task_id):
        raise BatchError(
            f"{path}: frontmatter id {task_id!r} is not a date-prefixed task slug; admission copies the draft to "
            "the task id it declares, and that id is also the file name the copy takes"
        )
    status = _field(path, block, "status")
    if status != DRAFT_STATUS:
        raise BatchError(
            f"{path}: a draft waiting at the gate carries status {DRAFT_STATUS!r}, not {status!r}; a proposal "
            "worked on where nothing dispatches it is not the inert draft the gate decides about"
        )
    estimate = _field(path, block, "session-est")
    if not _UNCONSUMED_SESSION_EST.match(estimate):
        raise BatchError(
            f"{path}: session-est {estimate!r} is not '0/<total>'; a session claims a task by incrementing that "
            "count, so a draft nobody has worked on yet is at zero of an estimate it does state"
        )
    blockers = _field(path, block, "blockers")
    if blockers != EMPTY_BLOCKERS:
        raise BatchError(
            f"{path}: blockers {blockers!r} is not {EMPTY_BLOCKERS!r}; a proposal that is already waiting on "
            "something is not work the gate can admit, and a blocked task in the active pool blocks nothing"
        )
    claimed = _field(path, block, "claimed-by")
    if claimed:
        raise BatchError(
            f"{path}: claimed-by {claimed!r} names a session; a draft is claimed by the session that picks its "
            "copy up out of the active pool, which is a thing that has not happened to a proposal"
        )

    body = text.splitlines()[block.body_start :]
    _require_plain_headings(path, body)
    sections = _sections(body)
    names = [name for name, _ in sections]
    missing = [heading for heading in REQUIRED_SECTIONS if heading not in names]
    if missing:
        raise BatchError(
            f"{path}: no {missing} section(s); an admitted copy is worked from its scope and reviewed against its "
            "acceptance, and the session log is where each session records what it did"
        )
    repeated = [heading for heading in REQUIRED_SECTIONS if names.count(heading) > 1]
    if repeated:
        raise BatchError(
            f"{path}: {repeated} section(s) declared more than once; a task worked from two scopes or reviewed "
            "against two acceptances has no one shape, and each reader takes whichever it reaches first"
        )
    if ADMISSION_HEADING in names:
        raise BatchError(
            f"{path}: the draft already carries an {ADMISSION_HEADING!r} section; that section is what admission "
            "itself adds, and a copy carrying two of them is one no later run can identify as this admission's"
        )
    _require_empty_log(path, body, sections)


def _require_plain_headings(path: Path, body: Sequence[str]) -> None:
    """Refuse a draft carrying a line that looks like a section heading but is
    indented.

    Such a line means different things to its readers at once — Markdown reads it
    as code, a session reading the copy reads it as a section, and the scan below
    reads it as neither — and admission is what carries that disagreement into
    the active pool. A copy could then hold a scope no two readers agree is one,
    or an `## Admission` section the record cannot identify it by, standing beside
    the one it can.

    Refusing the shape here is also what lets a section's extent be read exactly:
    where a section ends is where the next heading stands (`_is_section`), and a
    file with no ambiguous heading-looking line in it has one answer to that.
    Sections stand at column 0, the way the taskfile schema, the intake contract,
    and every task this controller writes state theirs.
    """

    for line in body:
        if line.strip().startswith("## ") and not _is_section(line):
            raise BatchError(
                f"{path}: {line.strip()!r} is indented; a task section stands at column 0, and a line that only "
                "looks like a heading is read as one by the session working the copy and as text by everything "
                "that checks it"
            )


def _require_empty_log(path: Path, body: Sequence[str], sections: Sequence[tuple[str, int]]) -> None:
    """A draft's session log is empty, because a session log is what happens to a
    task *after* it is dispatched.

    The heading alone is not the check. An entry under it describes sessions that
    claimed this task, incremented its estimate, and recorded what they did — none
    of which can have happened to a proposal nothing dispatches, and all of which
    the copy carries into the active pool as its own history. The rest of the
    frontmatter says the same thing from the other side (`pending`, `0/<total>`,
    unclaimed), so a log with entries in it is a file contradicting itself about
    whether the work has started.
    """

    start = next(index for name, index in sections if name == SESSION_LOG_HEADING)
    entry = next((line for line in _section_at(body, sections, start)[1:] if line.strip()), None)
    if entry is not None:
        raise BatchError(
            f"{path}: {SESSION_LOG_HEADING} already carries {entry.strip()!r}; a session log is appended to by the "
            "sessions that work the task, so an entry in a proposal nothing has dispatched records work on a task "
            "that does not exist yet"
        )


def _sections(lines: Sequence[str]) -> list[tuple[str, int]]:
    """Every section heading in a task body, with the line it stands on.

    Level two, and only level two: a session-log entry is a level-3 heading
    (taskfile schema §5), so a scan taking any `#`-prefixed line as a section
    would read an ordinary log as structure of its own. One reading, shared by
    the shape check, the admission-section identity check, and the renderer that
    decides where the provenance goes — three questions about the same sections,
    and an answer that differed between them would put the block somewhere the
    checks never looked.
    """

    return [(line.strip(), index) for index, line in enumerate(lines) if _is_section(line)]


def _is_section(line: str) -> bool:
    """A line that opens a section — not one that merely looks like it.

    The heading stands at column 0, where the taskfile schema and this
    controller's own renderer put every section they write. Indentation is the
    whole question: Markdown reads four spaces before a `##` as code rather than
    as a heading, so a reader that ends a section at such a line ends it *inside*
    the section, and everything below the false boundary is content nothing
    compares — which is how an unrecorded instruction hides under a provenance
    block that otherwise matches.

    Reading an indented line as content instead can only make a section come out
    longer than it is, and a section read long refuses where a section read short
    admits. Drafts carrying a heading-looking line that is indented are turned
    away at the gate (`_require_plain_headings`), so no file this controller
    writes leaves the two readings anything to disagree about.
    """

    return line.startswith("## ")


def _section_at(body: Sequence[str], sections: Sequence[tuple[str, int]], start: int) -> list[str]:
    """One whole section: its heading and everything under it, to the next one.

    A section ends where the next level-2 heading stands, or at the end of the
    body — the same extent whether the question is "is this log empty" or "is this
    the provenance that was written". Reading a fixed number of lines instead
    would answer about a prefix of the section and call it the section, and
    ending at a line that is not a heading (`_is_section`) answers about a prefix
    just the same, with the boundary chosen by whatever wrote the file.
    """

    following = [index for _, index in sections if index > start]
    return list(body[start : following[0] if following else len(body)])


def _field(path: Path, block: analysis_task.Frontmatter, name: str) -> str:
    """One frontmatter field of a draft, or a refusal naming what is missing."""

    if name not in block.fields:
        raise BatchError(
            f"{path}: frontmatter carries no {name!r}; a draft is a schema-conforming task file, and its copy is "
            "dispatched as one the moment it is admitted"
        )
    return block.fields[name]


def _require_free_task_ids(config: EvolutionConfig, current: BatchLineage, drafts: tuple[_Draft, ...]) -> None:
    """One task id belongs to one admission, and to nothing already in flight.

    Checked against the batch's whole experiment history as well as `.ai-tasks/`
    itself: the record could not otherwise say which bytes a task implemented or
    whose completion observation seals a round, and a copy that overwrote an
    existing task would destroy a session log to satisfy an admission.
    """

    admitted = {
        task.task_id: f"{experiment.experiment_id} as draft {task.draft_id!r}"
        for experiment in current.experiments
        for task in experiment.admitted_tasks
    }
    claimed: dict[str, str] = {}
    for draft in drafts:
        owner = admitted.get(draft.task_id)
        if owner is not None:
            raise BatchError(
                f"{draft.path}: task {draft.task_id!r} is already admitted by {owner}; one task implements one "
                "proposal, and a second draft admitted into it means a task id nothing can be traced through"
            )
        previous = claimed.setdefault(draft.task_id, draft.draft_id)
        if previous != draft.draft_id:
            raise BatchError(
                f"drafts {previous!r} and {draft.draft_id!r} both declare task id {draft.task_id!r}; one of them "
                "would take the other's file, and the record could name neither"
            )
        existing = analysis_task.existing_task_path(config, draft.task_id)
        if existing is not None:
            raise BatchError(
                f"{draft.path}: task {draft.task_id!r} already exists at {existing}; admission never overwrites a "
                "task file — give the draft an id of its own, or resolve the existing task first"
            )


def _title(text: str, draft_id: str, *, start: int = 0) -> str:
    """The draft's own heading, for the one line the active index shows.

    Read from the body: a frontmatter value that happens to look like a heading
    is a field, not a title.
    """

    for line in text.splitlines()[start:]:
        if line.startswith("# "):
            title = " ".join(line[2:].split())
            if title:
                return title[:_MAX_SUMMARY]
    return f"Change admitted from draft {draft_id}"


def _title_of(path: Path, draft_id: str) -> str:
    """The heading of a task file already on disk — read from the copy rather
    than from the draft, since that copy is what the row is about and it is the
    one thing this branch knows is there."""

    try:
        return _title(path.read_text(encoding="utf-8"), draft_id)
    except OSError:
        return f"Change admitted from draft {draft_id}"


def _admission_reason(requested: set[str]) -> str:
    return "grouped admission of draft(s) " + ", ".join(sorted(requested))


# --- writes ------------------------------------------------------------------


def _base_revision(
    config: EvolutionConfig,
    current: BatchLineage,
    requested: str | None,
) -> tuple[str, str | None]:
    """The commit this experiment starts from, and the release it builds on.

    A batch has exactly one base and its first experiment settled it (invariant
    15), so a later experiment takes that commit rather than resolving one:
    attempts against different sources are not alternatives to each other. An
    explicit `base` is still checked against it, because an operator who names a
    revision expecting it to be used should be told it was not.
    """

    frozen = current.base_revision
    if frozen is not None:
        _require_requested_base(config, current, frozen, requested)
        if resolve_commit(config.repo_root, frozen) is None:
            raise BatchError(
                f"this checkout does not hold {current.batch_id}'s base revision {frozen[:12]}; fetch it before "
                "starting another experiment, since the new ref has to be created at exactly that commit"
            )
        return frozen, current.base_release_ref

    revision = requested if requested is not None else "HEAD"
    resolved = _resolve(config, revision)
    return resolved, release_ref(config.repo_root, resolved)


def _require_requested_base(
    config: EvolutionConfig,
    current: BatchLineage,
    frozen: str,
    requested: str | None,
) -> None:
    """A named base is checked against the one this batch already froze.

    Silence would be the wrong answer in either direction: the base is not going
    to change (invariant 15), and an operator who named a revision expecting it to
    be used has to be told it was not.
    """

    if requested is None or _resolve(config, requested) == frozen:
        return
    raise BatchError(
        f"{current.batch_id} froze its base at {frozen[:12]} with {current.experiments[0].experiment_id}, so "
        f"{requested!r} cannot be this experiment's base; every alternative starts from the same commit or they "
        "are not alternatives (invariant 15)"
    )


def _resolve(config: EvolutionConfig, revision: str) -> str:
    resolved = resolve_commit(config.repo_root, revision)
    if resolved is None:
        raise BatchError(
            f"cannot resolve {revision!r} to a commit in {config.repo_root}; an experiment records the exact "
            "revision it starts from, so the base has to be a commit this repository holds"
        )
    return resolved


def _create_experiment_ref(config: EvolutionConfig, experiment: Experiment) -> None:
    """Create the durable ref at the base, or adopt the one an interrupted run
    already created there.

    Never moved and never recreated. A ref already holding an experiment's rounds
    is the one thing a repair must not touch, and a ref that is simply absent is
    the ordinary state of every clone that did not do this work — so the only
    thing this may safely do is create one that is not there.
    """

    tip = ref_tip(config.repo_root, experiment.ref)
    if tip == experiment.base_revision:
        return
    if tip is not None:
        raise BatchError(
            f"{experiment.ref} already exists at {tip[:12]}, not at {experiment.base_revision[:12]}; an "
            f"experiment id is never reused, so a ref standing where {experiment.experiment_id} is about to be "
            "created belongs to work this controller cannot account for"
        )
    failure = create_ref(config.repo_root, experiment.ref, experiment.base_revision)
    if failure is not None:
        raise BatchError(
            f"cannot create {experiment.ref} at {experiment.base_revision[:12]} for "
            f"{experiment.experiment_id}: {failure}"
        )


def _publish_record(config: EvolutionConfig, experiment: Experiment) -> Path:
    """Publish a new experiment directory by an atomic rename.

    A directory appearing before its record would stop every later read — one
    experiment is one directory, and a directory without `experiment.json` names
    no base, no tasks, and no candidate. So it appears complete or not at all;
    `.staging-*` residue from an interrupted publish is dot-prefixed and belongs
    to no experiment.
    """

    record = _serialize(experiment)
    _validate(config, record, experiment.experiment_id)

    root = config.experiments_root
    root.mkdir(parents=True, exist_ok=True)
    final = root / experiment.experiment_id
    if final.exists():
        raise BatchError(
            f"{final} already exists; an experiment id is allocated one past the highest its batch ever used and "
            "is never reused"
        )
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=root))
    try:
        atomic_write_text(staging / EXPERIMENT_FILENAME, _json(record))
        os.replace(staging, final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return final


def _write_record(config: EvolutionConfig, experiment: Experiment) -> Path:
    """Rewrite an existing experiment record, atomically.

    The record accumulates — a round's admitted tasks, its seal, the terminal
    decision — so unlike a frozen manifest it is rewritten in place. What is
    never rewritten is what it already says: every append is checked against the
    schema here and against the lineage rules the next read applies.
    """

    record = _serialize(experiment)
    _validate(config, record, experiment.experiment_id)
    path = experiment.directory / EXPERIMENT_FILENAME
    if not path.is_file():
        raise BatchError(f"{path} is gone; an experiment record is never recreated from a partial reading")
    atomic_write_text(path, _json(record))
    return path


def _validate(config: EvolutionConfig, record: Mapping[str, Any], experiment_id: str) -> None:
    validate_or_raise(
        record,
        load_schema(config.schema_path(EXPERIMENT_SCHEMA_FILENAME)),
        description=f"experiment record for {experiment_id}",
    )


def _write_tasks(
    config: EvolutionConfig,
    current: BatchLineage,
    experiment: Experiment,
    round_: Round,
    drafts: tuple[_Draft, ...],
) -> tuple[Admitted, ...]:
    """Copy each admitted draft into `.ai-tasks/` and list it in the active index.

    Last, and derivable from the record: an interrupted run leaves tasks missing
    rather than leaving tasks nothing accounts for.
    """

    written: list[Admitted] = []
    for draft in drafts:
        text = _render_task(config, current, experiment, round_, draft)
        path = analysis_task.publish_task(config, draft.task_id, text, description="admitted change task")
        analysis_task.append_row(config, draft.task_id, _summary(experiment, draft.draft_id, draft.title))
        written.append(
            Admitted(
                draft_id=draft.draft_id,
                task_id=draft.task_id,
                draft_sha256=draft.sha256,
                task_path=path,
            )
        )
    return tuple(written)


def _redo_create(
    config: EvolutionConfig,
    current: BatchLineage,
    experiment: Experiment,
    requested: set[str],
) -> AdmissionResult:
    """The same grouped admission run again, or a refusal naming what is open.

    The record is what made the admission real, so an open experiment on its
    first round admitting exactly this selection *is* this operation, already
    recorded; what is left of it is the copies. Any other selection is a second
    experiment over one that is already open, which invariant 14 does not allow —
    and the refusal names what is open rather than what was asked for, since the
    open attempt is what has to be dealt with either way.
    """

    round_ = experiment.open_round
    admitted = {} if round_ is None else {task.draft_id: task for task in round_.tasks}
    if round_ is not None and len(experiment.rounds) == 1 and set(admitted) == requested:
        return _finish(config, current, experiment, round_, tuple(admitted[key] for key in sorted(admitted)))

    last = experiment.last_round
    state = "open" if round_ is not None else "candidate-ready"
    raise BatchError(
        f"{current.batch_id} already has an open experiment ({experiment.experiment_id}, round {last.number} "
        f"{state}, drafts {sorted(task.draft_id for task in last.tasks)}); invariant 14 allows one at a time — "
        "admit further drafts into its open round, or end it with a decision, which keeps it as evidence and "
        "frees the batch for an alternative"
    )


def _finish(
    config: EvolutionConfig,
    current: BatchLineage,
    experiment: Experiment,
    round_: Round,
    tasks: tuple[AdmittedTask, ...],
) -> AdmissionResult:
    """Write whatever an already-recorded admission still owes `.ai-tasks/`."""

    written = tuple(_restore_task(config, current, experiment, round_, task) for task in tasks)
    return _result(current.batch, experiment, round_, written, created=False)


def _restore_task(
    config: EvolutionConfig,
    current: BatchLineage,
    experiment: Experiment,
    round_: Round,
    task: AdmittedTask,
) -> Admitted:
    """Write the copy of one already-recorded admission, if it is not there.

    Only for a task the record says is still owed, and only where what is there
    is that copy. Three states, and each of them is ordinary:

    - the file is this experiment's copy and still in flight: left exactly as it
      is, since it may already carry a session log, and only its index row is
      made good — the step an interruption can drop on its own;
    - it has finished, whether the record has observed that yet or not: archived
      by close-out or `completed` in place, and either way it belongs to the
      close-out, not to the active pool a `pending` row would put it back in;
    - it is not here at all: written from the draft, which is re-read and
      re-hashed against what the record admitted, because the copy has to be made
      from the bytes that were admitted and a draft edited since is a state this
      controller cannot account for rather than one it should quietly copy.

    A task the record already shows complete is never recreated even when its
    file is gone: close-out archived it, and recreating it as pending would
    reopen work that finished.
    """

    existing = analysis_task.existing_task_path(config, task.task_id)
    if existing is not None:
        _require_admitted_copy(config, current, experiment, round_, task, existing)
        if not (task.complete or analysis_task.task_finished(config, task.task_id)):
            summary = _summary(experiment, task.draft_id, _title_of(existing, task.draft_id))
            analysis_task.append_row(config, task.task_id, summary)
        return _already(task, existing)
    if task.complete:
        return _already(task, analysis_task.task_path(config, task.task_id))

    draft = _read_draft(current.batch, task.draft_id, for_tasks=True)
    if draft.sha256 != task.draft_sha256:
        raise BatchError(
            f"{draft.path} no longer matches the bytes {experiment.experiment_id} admitted (recorded "
            f"{task.draft_sha256[:12]}, found {draft.sha256[:12]}); the copy owed to {task.task_id!r} has to be "
            "the proposal that was admitted, so restore the draft rather than admitting a different one under it"
        )
    if draft.task_id != task.task_id:
        raise BatchError(
            f"{draft.path} declares task id {draft.task_id!r}, but {experiment.experiment_id} admitted it as "
            f"{task.task_id!r}; the record names the task the copy took"
        )
    path = analysis_task.publish_task(
        config,
        task.task_id,
        _render_task(config, current, experiment, round_, draft),
        description="admitted change task",
    )
    analysis_task.append_row(config, task.task_id, _summary(experiment, draft.draft_id, draft.title))
    return Admitted(
        draft_id=task.draft_id,
        task_id=task.task_id,
        draft_sha256=task.draft_sha256,
        task_path=path,
        restored=True,
    )


def _already(task: AdmittedTask, path: Path) -> Admitted:
    return Admitted(
        draft_id=task.draft_id,
        task_id=task.task_id,
        draft_sha256=task.draft_sha256,
        task_path=path,
    )


def _require_admitted_copy(
    config: EvolutionConfig,
    current: BatchLineage,
    experiment: Experiment,
    round_: Round,
    task: AdmittedTask,
    path: Path,
) -> None:
    """Refuse to treat an unrelated file at this task id as the admitted copy.

    A redo that finds the file present declares that copy done, so what is at
    that path decides whether the admission has its task at all. Adopting an
    unrelated one lists somebody else's work as this experiment's, puts a
    `pending` row on it, and hands the record a task whose bytes implement
    nothing it names.

    Identity is read from the structures that own the values, never from the text
    containing them. The task id is the frontmatter field `id`, declared once — a
    file saying `not-id:`, discussing the id in prose, or naming two ids is not a
    task with that id — and the provenance is the one `## Admission` section, whole
    and exactly as this admission would write it. That section is the immutable
    part of a copy: the batch, the experiment, the round, the ref, the base, and
    the digest of the bytes admitted, none of which a session working the task has
    any reason to edit. Everything a session *does* change is outside it — the
    frontmatter lifecycle above, the session log below — so an ordinary claimed and
    logged copy still matches, while a file that merely mentions the same values
    does not.

    Whole means through the next level-2 boundary, not the first lines of it. A
    section carrying what this admission wrote and then a line more is a copy whose
    provenance says something the record never said — another base to work from,
    another ref to commit on — and reading only as far as the recorded lines
    reach is what leaves that line invisible. So is ending at a line that is not a
    heading: an indented `##` is code to Markdown and a section to nothing, and a
    boundary the file gets to invent is one it can put immediately after the
    recorded lines and hide the rest behind (`_is_section`).

    The cost of matching the section rather than sampling it: a copy written by a
    controller whose wording of that section differed no longer matches its own
    admission. That fails closed, naming the line that differs, which is the same
    answer this module gives to every other state it cannot account for.

    Nothing is repaired. The file may already carry a session log, and rewriting
    one to satisfy a redo would destroy the record it exists to keep.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BatchError(f"cannot read the admitted task {path}: {exc}") from exc

    detail = _unlike_admitted_copy(config, current, experiment, round_, task, text)
    if detail is not None:
        raise BatchError(
            f"{path} is not the copy {experiment.experiment_id} admitted as {task.task_id!r} ({detail}); an "
            "admission never overwrites a task file, and a file this record cannot identify is not the work it "
            "accounts for — resolve what is at that id"
        )


def _unlike_admitted_copy(
    config: EvolutionConfig,
    current: BatchLineage,
    experiment: Experiment,
    round_: Round,
    task: AdmittedTask,
    text: str,
) -> str | None:
    """What stops this file from being that admission's copy, or None."""

    block = analysis_task.parse_frontmatter(text)
    if not block.present or not block.closed:
        return "it carries no closed frontmatter block"
    if block.duplicated:
        # First occurrence wins in `fields`, so a block declaring the recorded id
        # and another one reads as this task to whatever scans down it and as some
        # other task to whatever does not. The restore path reads `status` out of
        # the same block to decide whether the copy is still owed, so the
        # ambiguity is not confined to the id either.
        return f"its frontmatter declares {list(block.duplicated)} more than once"
    declared = block.fields.get("id")
    if declared is None:
        return "its frontmatter declares no id"
    if declared != task.task_id:
        return f"its frontmatter declares id {declared!r}"

    body = text.splitlines()[block.body_start :]
    sections = _sections(body)
    at = [index for name, index in sections if name == ADMISSION_HEADING]
    if not at:
        return f"it carries no {ADMISSION_HEADING!r} section"
    if len(at) > 1:
        return f"it carries {len(at)} {ADMISSION_HEADING!r} sections"

    expected = _admission_section(config, current, experiment, round_, task.draft_id, task.draft_sha256)
    found = _section_at(body, sections, at[0])
    for index, line in enumerate(expected):
        if index >= len(found) or found[index] != line:
            return f"its admission section does not carry {line!r}"
    if len(found) > len(expected):
        return f"its admission section also carries {found[len(expected)]!r}"
    return None


# --- shapes ------------------------------------------------------------------


def _admitted(draft: _Draft, *, admitted_at: str) -> AdmittedTask:
    return AdmittedTask(
        task_id=draft.task_id,
        draft_id=draft.draft_id,
        draft_sha256=draft.sha256,
        admitted_at=admitted_at,
        completion_observed_at=None,
    )


def _open_round(experiment: Experiment) -> Round:
    round_ = experiment.open_round
    if round_ is None:
        last = experiment.last_round
        raise BatchError(
            f"round {last.number} of {experiment.experiment_id} is candidate-ready at "
            f"{(last.candidate_revision or '')[:12]}; its evidence names that pinned revision, so further work "
            "opens the next round rather than changing what was already measured (invariant 16)"
        )
    return round_


def _serialize(experiment: Experiment) -> dict[str, Any]:
    """The record exactly as the schema holds it.

    One serializer for every write here, so an operation that appends a round or
    records a decision cannot drop a field the reader depends on: what is written
    back is the whole record the reader produced, with the one part that changed
    replaced.
    """

    decision = experiment.decision
    return {
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "experiment_id": experiment.experiment_id,
        "batch_id": experiment.batch_id,
        "created_at": experiment.created_at,
        "base_revision": experiment.base_revision,
        "base_release_ref": experiment.base_release_ref,
        "ref": experiment.ref,
        "rounds": [
            {
                "round": round_.number,
                "opened_at": round_.opened_at,
                "reason": round_.reason,
                "tasks": [
                    {
                        "task_id": task.task_id,
                        "draft_id": task.draft_id,
                        "draft_sha256": task.draft_sha256,
                        "admitted_at": task.admitted_at,
                        "completion_observed_at": task.completion_observed_at,
                    }
                    for task in round_.tasks
                ],
                "seal": None
                if round_.seal is None
                else {
                    "sealed_at": round_.seal.sealed_at,
                    "candidate_revision": round_.seal.candidate_revision,
                },
            }
            for round_ in experiment.rounds
        ],
        "decision": None
        if decision is None
        else {
            "outcome": decision.outcome,
            "decided_at": decision.decided_at,
            "reason": decision.reason,
            "superseded_by": decision.superseded_by,
            "promotion_revision": decision.promotion_revision,
        },
    }


def _admission_records(
    batch: Batch,
    experiment: Experiment,
    round_: Round,
    written: tuple[Admitted, ...],
    *,
    recorded_at: str,
) -> list[dict[str, Any]]:
    return [
        build_record(
            RECORD_TASKS_ADMITTED,
            recorded_at=recorded_at,
            batch_id=batch.batch_id,
            experiment_id=experiment.experiment_id,
            round=round_.number,
            draft_id=item.draft_id,
            task_id=item.task_id,
        )
        for item in written
    ]


def _result(
    batch: Batch,
    experiment: Experiment,
    round_: Round,
    written: tuple[Admitted, ...],
    *,
    created: bool,
) -> AdmissionResult:
    return AdmissionResult(
        batch_id=batch.batch_id,
        experiment_id=experiment.experiment_id,
        round_number=round_.number,
        base_revision=experiment.base_revision,
        ref=experiment.ref,
        admitted=written,
        created=created,
    )


def _summary(experiment: Experiment, draft_id: str, title: str) -> str:
    """The active index's one line for an admitted change task: what it is, and
    which attempt it belongs to."""

    return f"{title} ({experiment.experiment_id}, draft {draft_id})"


def _render_task(
    config: EvolutionConfig,
    current: BatchLineage,
    experiment: Experiment,
    round_: Round,
    draft: _Draft,
) -> str:
    """The draft, plus what only the admission knows.

    A change task must state the batch's base revision and the experiment and
    draft it was admitted from (contract: Evolution task requirements), none of
    which exists when the draft is written. Without the ref, the session
    implementing it would also have nowhere to commit — the work belongs on the
    experiment's ref, not on whatever branch the checkout happens to be on.
    """

    block = "\n".join([*_admission_section(config, current, experiment, round_, draft.draft_id, draft.sha256), ""])
    lines = draft.text.splitlines(keepends=True)
    for index in range(draft.body_start, len(lines)):
        if _is_section(lines[index]):
            head = "".join(lines[:index])
            separator = "" if head.endswith("\n\n") or not head else "\n"
            return head + separator + block + "".join(lines[index:])
    raise BatchError(
        f"{draft.path}: no body section to admit before; a draft is a schema-conforming task file, and the "
        "admission provenance goes above its first section"
    )


def _admission_section(
    config: EvolutionConfig,
    current: BatchLineage,
    experiment: Experiment,
    round_: Round,
    draft_id: str,
    draft_sha256: str,
) -> tuple[str, ...]:
    """The whole `## Admission` section as it stands in a copy.

    The provenance lines and the blank line that separates them from the draft's
    own first section — everything between this heading and the next one, which is
    exactly the extent `_require_admitted_copy` reads back. Recognition compares
    the section, so the section is what gets written: a trailing line the renderer
    added and the comparison did not expect would refuse every copy this
    controller writes.
    """

    return (*_admission_lines(config, current, experiment, round_, draft_id, draft_sha256), "")


def _admission_lines(
    config: EvolutionConfig,
    current: BatchLineage,
    experiment: Experiment,
    round_: Round,
    draft_id: str,
    draft_sha256: str,
) -> tuple[str, ...]:
    """The `## Admission` section's own lines, written once and read back the same
    way.

    Every value in it comes from the record and the frozen manifest rather than
    from the draft's own text, which is what lets a later run rebuild this exact
    section from what the experiment says it admitted — and that reconstruction is
    how an admitted copy is recognised on the way back (`_require_admitted_copy`).
    Rendering and recognition through one function is the point: two spellings of
    the same section would drift, and the direction they drift in is a copy this
    controller wrote and can no longer identify.
    """

    relative = _draft_path(current.batch, draft_id).relative_to(config.repo_root).as_posix()
    runner = current.batch.manifest.get("runner_protocol_revision")
    release = experiment.base_release_ref or "no release tag reachable"
    return (
        ADMISSION_HEADING,
        "",
        f"Admitted from evolution batch `{current.batch_id}` under the normative",
        f"contract `{analysis_task.CONTRACT_PATH}`; every canonical change passes this",
        "human gate (invariant 9).",
        "",
        f"- Draft `{draft_id}`: `{relative}`,",
        f"  sha256 `{draft_sha256}`.",
        f"- Experiment `{experiment.experiment_id}`, round {round_.number}. Work on",
        f"  `{experiment.ref}`, which only",
        "  fast-forwards; the round is sealed — every admitted task observed complete,",
        "  the tip pinned — before anything measures the candidate (invariants 15, 16).",
        f"- Base revision `{experiment.base_revision}`",
        f"  ({release}): the commit every experiment of this batch starts from.",
        f"- Runner protocol revision: {runner or 'unknown — none recorded at freeze time'}.",
        "  It stays fixed for this task, and a candidate revision never governs the run",
        "  that creates it (invariant 8).",
    )


def _moment(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise BatchError("admission time must be timezone-aware; a naive datetime records an ambiguous moment")
    return now


def _json(record: Mapping[str, Any]) -> str:
    return json.dumps(record, indent=2, sort_keys=True) + "\n"


__all__ = [
    "Admitted",
    "AdmissionResult",
    "RejectionResult",
    "add_tasks",
    "create",
    "reject",
]
