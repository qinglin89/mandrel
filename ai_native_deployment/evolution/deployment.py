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
from an operator over a repository none of them are about.

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

from ..lockfile import read_lock
from ..paths import lock_path
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

    `revision` is the receipt's own `source_git_commit`, as the receipt states
    it — not the commit it resolved to. The two are the same wherever a receipt
    names a full commit id, and where they are not, what an operator is looking
    at is the string in that target's file rather than this repository's reading
    of it.
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
    except RegistryError as error:
        # The inventory itself, rather than any one target's receipt. Reported
        # against every name because that is what it costs: this machine cannot
        # say which repository any of them is.
        return tuple(
            TargetHolding(target=name, path=None, revision=None, state=HOLDING_UNREADABLE, detail=str(error))
            for name in targets
        )

    known: dict[str, list[str]] = {}
    for entry in entries:
        known.setdefault(entry["name"], []).append(entry["path"])

    placed: dict[str, tuple[str, str | None]] = {}
    return tuple(_holding(config, name, known.get(name, []), promotion, rollback, placed) for name in targets)


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
        lock = read_lock(Path(path))
    except (OSError, ValueError) as error:
        # `ValueError` is both halves of unreadable here: JSON that does not
        # parse, and bytes that are not UTF-8 text to begin with.
        return TargetHolding(
            target=name, path=path, revision=None, state=HOLDING_UNREADABLE, detail=f"{receipt}: {error}"
        )
    if not isinstance(lock, dict):
        return TargetHolding(
            target=name,
            path=path,
            revision=None,
            state=HOLDING_UNREADABLE,
            detail=f"{receipt} is not a deploy receipt",
        )

    stated = lock.get("source_git_commit")
    if stated is None:
        # The receipt's own null, and an ordinary one: a deploy states a source
        # commit only where the payload it copied matched that commit's tree
        # exactly (contract: When a lock may be published as one).
        return TargetHolding(
            target=name,
            path=path,
            revision=None,
            state=HOLDING_UNSTATED,
            detail="the receipt ties its payload to no source commit, so nothing places what it holds",
        )
    if not isinstance(stated, str) or not stated.strip():
        return TargetHolding(
            target=name,
            path=path,
            revision=None,
            state=HOLDING_UNREADABLE,
            detail=f"{receipt} states a source revision that is not a commit id",
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
