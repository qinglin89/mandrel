"""Taking the latest promotion back off the source line.

The counterpart of `experiments.promote`, and deliberately not part of it: a
promotion is a decision about an open experiment, while this acts on a batch that
is already over. Nothing here reopens anything. The experiment stays promoted,
the batch stays concluded, the outcome record is not edited, and the promotion
commit stays exactly where it is on the line — what a rollback adds is a new
commit that takes the change back out, and one record saying the line is no
longer carrying it (contract: Rollback).

**Which promotion.** The newest one this repository recorded, and no other. That
is `lineage.last_promoted`, the same derivation `status` reports as
`last_promotion`, so there is nothing to name and nothing to pick: an operator
reverses what the source line most recently took. Reaching further back is not
offered, because everything this repository did afterwards was built on the line
as that promotion left it — and the guard below is where that stops being an
assertion.

**Three writes, one order**, for `promote`'s reason: the Git half cannot be
undone by this controller, so it must be recognisable afterwards. The inverse
commit is made first and named by nothing, so a run stopping there leaves an
object Git collects. Then the record, with `reverted_at` null — which is what
makes that commit *this operation's* rather than a revert somebody made by hand.
Then the source-line ref moves, compare-and-swap from the tip the inverse was
made on top of. Then `reverted_at` is written, and the audit line after it.

A second run therefore asks the record which commit was this operation's, and
Git whether the line carries it. A commit's shape is not that identity — a
revert made by hand carries the same tree and parent — and the line's tip is not
either, since a line that took further commits still carries the rollback.

**Why an inverse rather than a reset.** The promotion is history and stays
history: the record of what was promoted, when, and on what evidence is the
whole point of the trail this controller keeps, and a line rewritten to drop it
would leave every other clone with the commit and this one without it. So the
change is taken back out by a commit that says so, computed against the line as
it now stands — every commit that landed after the promotion is carried through
rather than dropped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .config import ROLLBACK_SCHEMA_FILENAME, EvolutionConfig
from .errors import BatchError
from .guards import reason as require_reason
from .guards import require_line_not_checked_out, require_readable_evidence, settled
from .ledger import append_records, build_record
from .lineage import BatchLineage, Lineage
from .manifests import ROLLBACK_SCHEMA_VERSION
from .revisions import commit_shape, commit_tree, contains, move_ref, ref_tip, revert_tree
from .schema import format_rfc3339, load_schema, validate_or_raise
from .state import atomic_write_text, single_writer_lock

RECORD_PROMOTION_ROLLED_BACK = "promotion-rolled-back"

# What this operation is doing when it moves the ref, in the words the shared
# guard puts into its refusals.
_ACTION = "a rollback"

# Where the source line stands relative to a rollback that was prepared. The
# third is also the state of a rollback nobody has prepared yet: both are
# answered by making the inverse commit from where the line now stands.
_CARRIED = "carried"
_WAITING = "waiting"
_STALE = "stale"


@dataclass(frozen=True)
class RollbackResult:
    """A promotion taken back off the source line, and the commit that did it."""

    batch_id: str
    experiment_id: str
    # The promotion reversed, and the line it was reversed on — both read from
    # the outcome that recorded it, never from this run's arguments.
    promotion_revision: str
    source_ref: str
    # The tip the inverse was made from, which is where the line stood: the
    # promotion revision only when nothing followed it.
    reverted_from: str
    revision: str
    tree: str
    reason: str
    reverted_at: str
    record_path: Path
    # False when the source line already carried the inverse commit: the rollback
    # whose record was interrupted, recognised by the commit it made rather than
    # made a second time.
    reverted: bool = True
    # False when the whole rollback was already on record — the same one run
    # again after it had finished.
    recorded: bool = True


def rollback(
    config: EvolutionConfig,
    *,
    reason: str,
    now: datetime | None = None,
) -> RollbackResult:
    """Reverse the latest promotion on the source line it was put on.

    Four things are asked before anything is written, and each is a different way
    for a rollback to be the wrong operation:

    - **There is a promotion to reverse.** The newest batch that ended by
      promoting; a repository that never promoted has nothing here to take back.
    - **It is still effective.** A promotion a completed rollback already
      reversed is not reversed twice; the same request run again reports the
      rollback that is on record rather than making a second one.
    - **Nothing here was built on it.** No experiment of a later batch stands on
      the promoted commit. Those attempts were developed, measured and possibly
      promoted against a line that carries this change, so taking it back out
      from under them leaves work whose base says one thing and whose line says
      another. A checkout that cannot answer refuses, since this is the one
      question whose wrong answer is other people's work.
    - **No working tree is on the line.** The same repository-level guard a
      promotion makes, for the same reason, and worktree-wide.

    The evidence for a rollback is not in the run that justified the promotion.
    A replay measures a tree; whether the promotion argued from it should stand
    is a later judgement against a later cohort, and what this records of it is
    the operator's reason (contract: Promotion evidence).
    """

    moment = _moment(now)
    text = require_reason(
        reason,
        "a rollback records why the promotion was taken back off the source line; the promotion itself is "
        "recorded with the evidence that justified it, and what a later reader has for the reversal is this "
        "sentence and nothing else",
    )

    with single_writer_lock(config):
        lineage = settled(config, now=moment)
        promoted = lineage.last_promoted
        if promoted is None:
            raise BatchError(
                "no batch has promoted anything, so there is nothing on the source line to take back; a rollback "
                "reverses the promotion this repository most recently recorded (contract: Rollback)"
            )
        outcome = promoted.outcome or {}
        recorded = promoted.rollback
        # The lineage's own reading of whether that promotion is still what the
        # line carries, rather than a second interpretation of the same record: a
        # promotion nothing reversed and one whose reversal is still in flight
        # are both effective, and only the first of those is a rollback to make.
        if not promoted.promotion_effective and recorded is not None:
            return _redone(promoted, recorded, text)
        # The batch this acts on is the one whose records are about to be added
        # to, so its replay evidence is held to the same rule every guarded
        # operation applies: an unreadable record stops the operation rather than
        # being written over.
        require_readable_evidence(config, promoted)
        if recorded is not None:
            _require_same_rollback(promoted, recorded, text)

        stamp = format_rfc3339(moment)
        merge = outcome["promotion"]
        source_ref = merge["merge_input_ref"]
        tip = ref_tip(config.repo_root, source_ref)
        if tip is None:
            raise BatchError(
                f"{promoted.batch_id} promoted {outcome['promotion_revision'][:12]} onto {source_ref}, and this "
                "checkout does not hold that ref; a rollback moves the source line, so it is run from a checkout "
                "that has the line"
            )

        standing = _standing(config, promoted, recorded, tip)
        prepared = recorded
        reverted = standing != _CARRIED
        if reverted:
            _require_nothing_built_on(config, lineage, promoted)
            require_line_not_checked_out(config, source_ref, _ACTION)
            if standing == _STALE:
                prepared = _prepare(config, promoted, outcome, tip, text, at=stamp)
            _land(config, promoted, prepared)

        path = _finish(config, promoted, prepared, at=stamp)

    return _result(promoted, prepared, reverted_at=stamp, path=path, reverted=reverted)


def _standing(
    config: EvolutionConfig,
    promoted: BatchLineage,
    prepared: Mapping[str, Any] | None,
    tip: str,
) -> str:
    """Whether the prepared inverse reached the source line, is still waiting to,
    or has to be made again.

    The identity is the recorded commit, for the reason a promotion's is: a
    revert made by hand carries the same tree and the same parent, so matching
    shapes would adopt somebody else's commit as this rollback; and a line that
    took further commits after the inverse still carries it, so matching the tip
    would leave a run unable to finish what it had already done. The question is
    ancestry.

    `_STALE` is where this differs from a promotion, which discards a prepared
    merge the line never took and refuses. A promotion's merge carries a tree
    that evidence exists about, and a line that moved makes that evidence
    describe something else — while a rollback's content is computed from the
    line as it stands and nothing measured is invalidated by the line moving. So
    the answer is to make the inverse again from where the line now is, which is
    also what a rollback nobody has prepared yet needs.
    """

    if prepared is None:
        return _STALE
    if tip == prepared["revision"]:
        return _CARRIED
    if tip == prepared["reverted_from"]:
        return _WAITING
    carried = contains(config.repo_root, prepared["revision"], tip)
    if carried is None:
        raise BatchError(
            f"{promoted.batch_id}: whether {prepared['revision'][:12]} is on {prepared['source_ref']} (now at "
            f"{tip[:12]}) cannot be answered in this checkout, which does not hold both commits; that answer is "
            "what says whether this rollback already happened, so nothing is written until a checkout holding "
            "them says so"
        )
    return _CARRIED if carried else _STALE


def _require_nothing_built_on(config: EvolutionConfig, lineage: Lineage, promoted: BatchLineage) -> None:
    """No later attempt in this repository stands on the promotion being reversed.

    The acceptance the contract states as "no later pinned candidate lineage",
    and it is about work rather than about time: an experiment of a later batch
    whose base — or whose last pinned candidate — has the promotion in its
    history was developed, reviewed and measured against a source line carrying
    this change. Taking it back out from under that attempt leaves every reading
    of it wrong in a way no record here would show: the evidence names a tree
    that was integrated onto a line that no longer exists in that form.

    A later batch that has frozen a manifest and admitted nothing yet is not that
    — it has pinned no candidate and holds no work — so it does not stop a
    rollback. Nor does a replay run of such an attempt need its own rule: a run
    is measured against a merge input, and moving the line is exactly the drift
    the replay reader already reports and refuses a promotion on.

    Unanswerable refuses. Everywhere else here a Git question this checkout
    cannot answer is a fact about the clone and is reported rather than raised;
    this one stands between an irreversible move and somebody's work, so a clone
    that cannot see whether the two are related does not get to decide they are
    not.
    """

    revision = (promoted.outcome or {})["promotion_revision"]
    for later in lineage.after(promoted):
        for experiment in later.experiments:
            for pinned in dict.fromkeys((experiment.base_revision, experiment.pinned_revision)):
                built = contains(config.repo_root, revision, pinned)
                if built is None:
                    raise BatchError(
                        f"whether {experiment.experiment_id} stands on the promotion {revision[:12]} cannot be "
                        f"answered in this checkout, which does not hold both {pinned[:12]} and that commit; a "
                        "rollback that took the change out from under a later attempt would leave its evidence "
                        "describing a line that no longer exists, so this refuses rather than assume it does not"
                    )
                if built:
                    raise BatchError(
                        f"{experiment.experiment_id} ({later.batch_id}) stands on {pinned[:12]}, which has the "
                        f"promotion {revision[:12]} in its history; only the latest promotion is rolled back, and "
                        "a later attempt built on this one is work whose evidence would describe a source line "
                        "this rollback removes — end that lineage first, or reverse it with ordinary Git and "
                        "record why"
                    )


def _prepare(
    config: EvolutionConfig,
    promoted: BatchLineage,
    outcome: Mapping[str, Any],
    tip: str,
    reason: str,
    *,
    at: str,
) -> Mapping[str, Any]:
    """Make the inverse commit, and record it as this rollback before it is used.

    The commit first, because a commit nothing names is inert and collectable, so
    a run stopping there has done nothing at all. The record second and the ref
    move third: the record is what makes that commit this operation's, and
    without it a run interrupted around the move would have to recognise its own
    work by shape and could not tell it from a revert somebody made by hand.

    The content is computed rather than taken from any record: reverting is
    applying the promotion's change backwards to the line as it now stands, so
    the commits that landed after it are carried through. A promotion that
    nothing on the line has left any trace of refuses here — the inverse would
    carry the tree the line already has, which is a commit that says a rollback
    happened while changing nothing, and how the content went missing is a
    question this controller cannot answer for.
    """

    merge = outcome["promotion"]
    revision = outcome["promotion_revision"]
    carried = contains(config.repo_root, revision, tip)
    if carried is None:
        raise BatchError(
            f"whether the promotion {revision[:12]} is on {merge['merge_input_ref']} (now at {tip[:12]}) cannot "
            "be answered in this checkout, which does not hold both commits; a rollback takes a commit back off "
            "the line it is on, so it is run from a checkout that holds them"
        )
    if not carried:
        raise BatchError(
            f"{merge['merge_input_ref']} stands at {tip[:12]}, which does not have the promotion "
            f"{revision[:12]} in its history; there is nothing on that line to take back — the line was reset, "
            "rewritten, or is not the one this promotion was made onto, and none of those is something a "
            "rollback may quietly record as its own work"
        )

    tree, complaint = revert_tree(
        config.repo_root,
        revision=revision,
        parent=merge["merge_input_revision"],
        tip=tip,
    )
    if tree is None:
        raise BatchError(
            f"{promoted.batch_id}: taking {revision[:12]} back out of {merge['merge_input_ref']} at "
            f"{tip[:12]} produces no tree here: {complaint}; a revert that does not apply cleanly is a decision "
            "about what the line should hold, which is a person's job with a working tree in front of them"
        )
    standing = commit_shape(config.repo_root, tip)
    if standing is not None and standing[0] == tree:
        raise BatchError(
            f"{merge['merge_input_ref']} at {tip[:12]} already carries none of the promotion {revision[:12]}, so "
            "the inverse commit would change nothing; something outside this controller took that change off the "
            "line, and recording a rollback for it would say this operation did what it did not"
        )

    made, complaint = commit_tree(config.repo_root, tree, [tip], _message(promoted, outcome, tip, reason))
    if made is None:
        raise BatchError(
            f"{promoted.batch_id} has nothing to roll back with: {complaint}; the inverse is a commit made by "
            "whoever reverses, and a checkout Git will not commit in is one this rollback cannot be recorded from"
        )
    record = {
        "schema_version": ROLLBACK_SCHEMA_VERSION,
        "batch_id": promoted.batch_id,
        "experiment_id": outcome["experiment_id"],
        "promotion_revision": revision,
        "source_ref": merge["merge_input_ref"],
        "reverted_from": tip,
        "revision": made,
        "tree": tree,
        "reason": reason,
        "prepared_at": at,
        "reverted_at": None,
    }
    _write_rollback(config, promoted, record)
    return record


def _land(config: EvolutionConfig, promoted: BatchLineage, prepared: Mapping[str, Any]) -> None:
    """Move the source line to the prepared inverse, or refuse in Git's own words.

    A compare-and-swap from the tip the inverse was made on top of: what advances
    a release line is ordinary Git, which the single-writer lock does not cover,
    so the reading and the write are one operation. A line that took a commit in
    between refuses here rather than having those commits dropped by a tree
    computed before they existed — and the next run makes the inverse again from
    where the line then stands.

    The worktree question is asked again rather than inherited: a run finishing
    an interrupted rollback goes through the same call, and between two runs
    somebody may have checked the line out.
    """

    require_line_not_checked_out(config, prepared["source_ref"], _ACTION)
    moved = move_ref(config.repo_root, prepared["source_ref"], prepared["revision"], prepared["reverted_from"])
    if moved is None:
        return
    raise BatchError(
        f"{prepared['source_ref']} did not take {prepared['revision'][:12]}: {moved}; the inverse is put on the "
        f"line from the commit it was made against ({prepared['reverted_from'][:12]}) and from no other, so a "
        f"line that moved since is rolled back by running this again — {promoted.batch_id}'s promotion stays "
        "where it is until one of them lands"
    )


def _finish(
    config: EvolutionConfig,
    promoted: BatchLineage,
    prepared: Mapping[str, Any],
    *,
    at: str,
) -> Path:
    """Record that the line carries the inverse, then say so in the audit.

    `reverted_at` is when this controller found the line carrying it, which for a
    rollback finishing an interrupted one is later than the move itself. The
    record states both moments rather than one: `prepared_at` is when the inverse
    was made, and the pair is the window the move happened in — an honest answer
    where a single invented instant would not be.
    """

    record = dict(prepared, reverted_at=at)
    path = _write_rollback(config, promoted, record)
    append_records(
        config,
        [
            build_record(
                RECORD_PROMOTION_ROLLED_BACK,
                recorded_at=at,
                batch_id=promoted.batch_id,
                experiment_id=record["experiment_id"],
                revision=record["revision"],
            )
        ],
    )
    return path


def _write_rollback(config: EvolutionConfig, promoted: BatchLineage, record: Mapping[str, Any]) -> Path:
    """Publish the rollback record through the schema that reads it.

    Validated on the way out for the reason every writer here is: the reader
    enforces rules the schema states, and a writer that skipped them would
    publish a record its own package refuses to load — with the inverse commit
    already on the source line.
    """

    validate_or_raise(
        record,
        load_schema(config.schema_path(ROLLBACK_SCHEMA_FILENAME)),
        description=f"batch rollback record for {promoted.batch_id}",
    )
    atomic_write_text(promoted.batch.rollback_path, _json(record))
    return promoted.batch.rollback_path


def _require_same_rollback(promoted: BatchLineage, prepared: Mapping[str, Any], reason: str) -> None:
    """A rollback is finished as it was prepared.

    The reason is the only part a caller supplies, and the prepared record may
    already have an inverse commit on the source line — so a second run naming a
    different one is either a different rollback of a promotion that already has
    one on the way, or the same one with the sentence rewritten. Refused naming
    what is outstanding, the way a promotion refuses the same shape: the operator
    redoes this rollback, or says which of the two they meant.
    """

    if prepared["reason"] == reason:
        return
    raise BatchError(
        f"{promoted.batch_id} has a rollback of {prepared['promotion_revision'][:12]} prepared as "
        f"{prepared['revision'][:12]} for {prepared['reason']!r}, and this request is {reason!r}; a rollback is "
        "prepared once and finished as it was prepared, because that commit may already be on the source line"
    )


def _redone(promoted: BatchLineage, recorded: Mapping[str, Any], reason: str) -> RollbackResult:
    """The same rollback run again after it finished, or the refusal that this
    promotion has already been reversed.

    The record is written before the audit line, so a run interrupted between the
    two is finished by being run again — and what it reports is the rollback that
    is on record rather than a second inverse commit. A different reason is not
    that run: the promotion has been reversed, that is terminal, and a second
    sentence about it would be a decision nobody can act on.
    """

    if recorded["reason"] != reason:
        raise BatchError(
            f"{promoted.batch_id}'s promotion {recorded['promotion_revision'][:12]} was already rolled back as "
            f"{recorded['revision'][:12]} for {recorded['reason']!r}, which is not this request ({reason!r}); a "
            "promotion is reversed once, and what follows a rollback is the next batch rather than a second "
            "reversal of the same commit"
        )
    return _result(
        promoted,
        recorded,
        reverted_at=recorded["reverted_at"],
        path=promoted.batch.rollback_path,
        reverted=False,
        recorded=False,
    )


def _result(
    promoted: BatchLineage,
    record: Mapping[str, Any],
    *,
    reverted_at: str,
    path: Path,
    reverted: bool,
    recorded: bool = True,
) -> RollbackResult:
    """A rollback reported from the record it was prepared as.

    One builder for every way this operation ends, so what a caller is told about
    a rollback it finished is what it would have been told about the one that
    made it.
    """

    return RollbackResult(
        batch_id=promoted.batch_id,
        experiment_id=record["experiment_id"],
        promotion_revision=record["promotion_revision"],
        source_ref=record["source_ref"],
        reverted_from=record["reverted_from"],
        revision=record["revision"],
        tree=record["tree"],
        reason=record["reason"],
        reverted_at=reverted_at,
        record_path=path,
        reverted=reverted,
        recorded=recorded,
    )


def _message(promoted: BatchLineage, outcome: Mapping[str, Any], tip: str, reason: str) -> str:
    """What the inverse commit says about itself.

    Every value in it is in the records this operation writes; the point is that
    the commit is legible from Git alone, on a line whose readers are not running
    this controller. The `This reverts commit` sentence is Git's own convention
    and is written for a human reading `git log` — nothing here ever reads it
    back, because recognition is the recorded commit and never text. Nothing
    imported goes in it: the reason is the operator's own sentence (invariant 11).
    """

    merge = outcome["promotion"]
    revision = outcome["promotion_revision"]
    return (
        f"evolution: roll back {outcome['experiment_id']} promotion\n"
        "\n"
        f"This reverts commit {revision}, which carried\n"
        f"{merge['candidate_revision']} onto {merge['merge_input_revision']}\n"
        f"({merge['merge_input_ref']}) as round {merge['round']} of that experiment.\n"
        f"Reverted from {tip}.\n"
        f"Batch: {promoted.batch_id}\n"
        f"Reason: {reason}\n"
    )


def _moment(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise BatchError("rollback time must be timezone-aware; a naive datetime records an ambiguous moment")
    return now


def _json(record: Mapping[str, Any]) -> str:
    return json.dumps(record, indent=2, sort_keys=True) + "\n"


__all__ = ["RollbackResult", "rollback"]
