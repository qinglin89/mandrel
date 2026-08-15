"""What the targets a promotion was planned for actually hold.

A promotion promises nothing about deployment (contract: Promotion). The outcome
records the targets it was *planned* for, as names, and that plan is the one
thing on this surface that must never be read as an observation: a promoted
commit changes nothing a target carries until that target is redeployed, and what
any of them carries is its own `.ai-deploy-lock.json` to state.

So this is the other half of that sentence, read rather than assumed. For each
planned name it resolves the machine-local registry entry, reads that target's
deploy receipt, and asks Git where the revision the receipt states sits relative
to the promotion — carrying it, behind it, or carrying the inverse commit that
took it back out. The plan and the reading are then two different lists on the
surface, which is what stops the first standing in for the second.

**It is machine-local, all of it.** The registry is a gitignored inventory of the
repositories this machine manages, and a target's receipt is a file in a
repository this system does not own. So a clone that manages nothing reads every
planned target as unregistered, which is the ordinary answer and not a finding —
exactly as a fresh clone reports a current batch's evidence bundles as absent
from this machine rather than as damage. Nothing here is lifecycle state: it is
in no digest, it gates no verb, and a redeploy is not a lifecycle write.

**It never fails the reading.** A broken registry or an unreadable receipt is
reported as the state of that one target rather than raised, which is the
opposite of how this package treats its own malformed records — and for the
reason that separates them: an evolution record this controller wrote and cannot
read back is damage to the lifecycle, while a file in somebody else's repository
is a question this reading could not answer. Refusing the whole console because a
target's receipt is corrupt would take the gate, the pool and the experiment away
from an operator over a repository none of them are about. That holds for every
way either file fails to answer — a document that is not one, bytes this process
could not read at all, bytes that are not text — and the local inventory is
answered for in the same breath as the receipts it names, since a registry that
will not read leaves this machine unable to say which repository any planned name
is.

**What it believes a receipt about.** Only what the deploy contract wrote:
`lockfile.stated_source_commit` holds the file to a schema this build reads, to a
receipt that states the field at all, and to a full object id, and anything else
is that target's `unreadable` state rather than a revision. The reason is the question this module asks — where a revision
sits relative to the promotion, asked of *this* repository's Git. A receipt
naming `HEAD`, a branch, or an abbreviation would be resolved against this
checkout rather than against what that target holds, so its answer would move
under a `git checkout` here and report a promotion as deployed where nothing was
ever deployed.

**Where a rollback comes in.** An inverse commit descends from the promotion, so
a line that took the reversal contains the promotion too, and asking about the
promotion alone would report a target that gave the change back as one still
running it. The reversal is therefore asked first, as `assessment` asks it when
placing reports — and it is asked of an inverse still in flight as well, since
what matters is whether that commit reached the line the target was deployed
from, not whether this controller has finished recording it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..lockfile import LockError, read_lock, stated_source_commit
from ..paths import lock_path, registry_path
from ..registry import RegistryError, load_registry
from .config import EvolutionConfig
from .revisions import contains, resolve_commit

# Where a target stands relative to the promotion. Nine states, and each one is a
# different next step for the operator rather than a shade of the same answer:
# the first four are placements of a revision a receipt states, and the rest are
# the reasons no revision could be placed at all.
#
# The line between them matters more than the count. `behind` is a target this
# machine can see and redeploy; `unregistered` is a name it holds no repository
# for and may never manage. Folding those into "not carrying it" would report
# somebody else's machine's targets as work outstanding here.
HOLDING_CARRIES = "carries"
HOLDING_REVERSED = "reversed"
HOLDING_BEHIND = "behind"
HOLDING_UNPLACEABLE = "unplaceable"
HOLDING_UNSTATED = "unstated"
HOLDING_NO_RECEIPT = "no-receipt"
HOLDING_UNREADABLE = "unreadable"
HOLDING_UNREGISTERED = "unregistered"
HOLDING_AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class TargetHolding:
    """One planned target, and what this machine can say it holds.

    `revision` is the receipt's own `source_git_commit` — and it is that string
    and the commit it was placed as at once, since a receipt states one only as a
    full object id (`lockfile.stated_source_commit`). A receipt naming anything
    else is not placed at all, so there is no case here where an operator is
    shown this repository's reading of a name rather than what that target's file
    says.
    """

    target: str
    # The machine-local path the registry gave this name, and null wherever the
    # name resolved to no single repository — which is the state itself rather
    # than a missing field.
    path: str | None
    revision: str | None
    state: str
    # The one sentence a state needs that its name cannot carry: which paths a
    # name is ambiguous between, what could not be read, what Git could not
    # answer. None where the state says the whole of it.
    detail: str | None = None

    @property
    def deployed(self) -> bool:
        """Whether a revision was placed at all — the states that answer the
        question from the receipt rather than reporting why they could not."""

        return self.state in (HOLDING_CARRIES, HOLDING_REVERSED, HOLDING_BEHIND)


def describe(
    config: EvolutionConfig,
    *,
    targets: Iterable[str],
    promotion: str,
    rollback: str | None = None,
) -> tuple[TargetHolding, ...]:
    """Read each planned target's own receipt and place what it states.

    Takes the promotion's revisions rather than the record, because what this
    answers is one ancestry question per target and the record is the caller's
    reading. The order is the plan's own: a promotion names its targets once
    (`experiments._planned_targets` refuses a repeat), and a reading that sorted
    them would stop being the list beside it.
    """

    try:
        entries = load_registry(source_root=config.repo_root)
    except (RegistryError, OSError, ValueError) as error:
        # The inventory itself, rather than any one target's receipt. Reported
        # against every name because that is what it costs: this machine cannot
        # say which repository any of them is.
        #
        # Three families and one state. `RegistryError` is the registry module's
        # own complaint about a document that is not a registry; an `OSError` is
        # a file this process could not read at all — a permission, a directory
        # where the file belongs — and a `ValueError` is bytes that are not UTF-8
        # text. Only the first arrives named, and reading the other two as
        # anything but this target state would fail the whole console over a
        # machine-local inventory no part of the lifecycle is kept in.
        detail = _inventory_complaint(config, error)
        return tuple(
            TargetHolding(target=name, path=None, revision=None, state=HOLDING_UNREADABLE, detail=detail)
            for name in targets
        )

    known: dict[str, list[str]] = {}
    for entry in entries:
        known.setdefault(entry["name"], []).append(entry["path"])

    placed: dict[str, tuple[str, str | None]] = {}
    return tuple(_holding(config, name, known.get(name, []), promotion, rollback, placed) for name in targets)


def _inventory_complaint(config: EvolutionConfig, error: Exception) -> str:
    """Why the registry could not be read, always naming the file.

    `RegistryError` names it already. An `OSError` or a decode error is Python's
    own and may name nothing an operator can act on — a byte offset says which
    position, never which file — so the path this reading asked for is stated
    with it.
    """

    if isinstance(error, RegistryError):
        return str(error)
    return f"{registry_path(config.repo_root)}: {error}"


def _holding(
    config: EvolutionConfig,
    name: str,
    paths: list[str],
    promotion: str,
    rollback: str | None,
    placed: dict[str, tuple[str, str | None]],
) -> TargetHolding:
    """One planned name, resolved as far as this machine can take it."""

    if not paths:
        return TargetHolding(
            target=name,
            path=None,
            revision=None,
            state=HOLDING_UNREGISTERED,
            detail="no repository of that name is registered on this machine",
        )
    if len(paths) > 1:
        # A plan names one target. Two registered repositories sharing a
        # directory name is a real state of the local inventory, and picking one
        # would answer for a repository the plan may not have meant.
        return TargetHolding(
            target=name,
            path=None,
            revision=None,
            state=HOLDING_AMBIGUOUS,
            detail=f"{len(paths)} repositories are registered under that name: {', '.join(sorted(paths))}",
        )

    path = paths[0]
    receipt = lock_path(Path(path))
    if not receipt.is_file():
        return TargetHolding(
            target=name,
            path=path,
            revision=None,
            state=HOLDING_NO_RECEIPT,
            detail=f"no deploy receipt at {receipt}",
        )
    try:
        stated = stated_source_commit(read_lock(Path(path)))
    except (OSError, ValueError, LockError) as error:
        # One state for every way a receipt does not answer: bytes this process
        # could not read, JSON that does not parse — `ValueError`, which is also
        # bytes that are not UTF-8 text to begin with — and a document that
        # parses into something other than a receipt this build reads, which is
        # the deploy contract's own judgement rather than this module's.
        return TargetHolding(
            target=name, path=path, revision=None, state=HOLDING_UNREADABLE, detail=f"{receipt}: {error}"
        )
    if stated is None:
        # The receipt's own null, and an ordinary one: a deploy states a source
        # commit only where the payload it copied matched that commit's tree
        # exactly (contract: When a lock may be published as one).
        #
        # Only that null reaches here. A receipt omitting the field is refused
        # above and read as `unreadable`, which is the difference between a
        # target whose payload nothing can place and a document that could not
        # answer — and the sentence below is written for the first of those.
        return TargetHolding(
            target=name,
            path=path,
            revision=None,
            state=HOLDING_UNSTATED,
            detail="the receipt ties its payload to no source commit, so nothing places what it holds",
        )
    if stated not in placed:
        placed[stated] = _place(config, stated, promotion, rollback)
    state, detail = placed[stated]
    return TargetHolding(target=name, path=path, revision=stated, state=state, detail=detail)


def _place(
    config: EvolutionConfig,
    stated: str,
    promotion: str,
    rollback: str | None,
) -> tuple[str, str | None]:
    """Where one stated revision sits relative to the promotion.

    Cached by the caller across targets, because several targets deployed in one
    pass hold the same revision and each answer is two Git calls.

    Three-valued throughout, as `revisions.contains` is: "cannot tell" is a
    checkout that does not hold both commits — the ordinary state of a clone that
    never fetched what a target was deployed from — and reporting it as "does not
    carry it" would name a redeploy that is not owed.
    """

    commit = resolve_commit(config.repo_root, stated)
    if commit is None:
        # Said of the revision rather than repeating it: the record states it in
        # its own field, and the line beside this one names it.
        return HOLDING_UNPLACEABLE, "this checkout does not hold that commit"
    if rollback is not None:
        reversed_here = contains(config.repo_root, rollback, commit)
        if reversed_here is None:
            return HOLDING_UNPLACEABLE, "this checkout cannot place it against the inverse commit"
        if reversed_here:
            return HOLDING_REVERSED, None
    carried = contains(config.repo_root, promotion, commit)
    if carried is None:
        return HOLDING_UNPLACEABLE, "this checkout cannot place it against this promotion"
    return (HOLDING_CARRIES, None) if carried else (HOLDING_BEHIND, None)
