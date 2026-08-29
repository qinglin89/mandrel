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

**What it believes a receipt about.** Not its own judgement: the walk from a
planned name to the revision a receipt states is the deploy layer's
(`mandrel.placement`), because `status` asks for the same chain over one target
an operator named and a second spelling of it would let one surface place a
receipt the other refuses to read. What arrives here is either a revision — a
full object id, held to a schema this build reads and to a receipt that states
the field at all — or one of the five reasons there is none, which this module
passes through as that target's state. The shape matters for the question asked
below: a receipt naming `HEAD`, a branch, or an abbreviation would be resolved
against *this* checkout rather than against what that target holds, so its answer
would move under a `git checkout` here and report a promotion as deployed where
nothing was ever deployed.

**Which lines a target may be standing on** (`source_lines`) is the other
direction of the same subject and the reason this module rather than a deploy-
layer one answers it: the base is a policy fact (`config.toml`
`[source_line].ref`) and the sealed candidate is a lineage fact, and neither is
knowable without reading this workspace. `status` consumes the answer; nothing in
the deploy layer derives it, which is what keeps a deployment verb answerable in
a checkout that runs no evolution policy at all.

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

from .. import placement
from ..deploy import deploys_canonical_path
from ..placement import SourceLines, TargetRevision
from . import lineage
from .config import VERSIONED_ROOT, EvolutionConfig, load_config
from .errors import EvolutionError
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
# The five that are not this module's to name. They are the deploy layer's
# answer for a target that states no revision, published here under the names
# this console has always used for them — one set of strings, so a state cannot
# come to mean one thing to `status` and another to the console.
HOLDING_UNSTATED = placement.UNPLACED_UNSTATED
HOLDING_NO_RECEIPT = placement.UNPLACED_NO_RECEIPT
HOLDING_UNREADABLE = placement.UNPLACED_UNREADABLE
HOLDING_UNREGISTERED = placement.UNPLACED_UNREGISTERED
HOLDING_AMBIGUOUS = placement.UNPLACED_AMBIGUOUS


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

    names = tuple(targets)
    readings = placement.registered_revisions(names, root=config.repo_root)
    placed: dict[str, tuple[str, str | None]] = {}
    return tuple(
        _holding(config, name, reading, promotion, rollback, placed)
        for name, reading in zip(names, readings)
    )


def source_lines(repo_root: Path) -> SourceLines:
    """Which canonical identities a target deployed from `repo_root` may hold.

    The base is this repository's configured source line resolved to the commit
    it names right now, and the candidate is the latest **sealed** round of the
    current batch's open experiment — the only two places a deployment can stand
    and still be one this repository is releasing from.

    Sealed, never the ref tip. A round still taking work has a tip that moves, so
    a target deployed from it produces reports naming a tree nothing can
    afterwards identify (invariant 16), and blessing the tip here would make a
    deployment surface recommend exactly the deployment the measurement layer
    then throws away. An experiment with no sealed round therefore contributes
    nothing, which is base alone rather than an error.

    Three degradations, and they are deliberately not one. A repository with no
    evolution workspace runs no experiments, so the deploy layer's own default
    line is the whole answer and base alone is correct. A policy this workspace
    has and cannot produce names *no* line — this is the one place a guess would
    silently judge every target against a line the operator did not configure, so
    nothing is named and the reason is stated. A lineage that will not read
    leaves the configured base standing and drops the candidate with its reason:
    a damaged experiment record cannot make a target at base anything other than
    current, and the conservative direction for a candidate-deployed target is to
    report it as somewhere else, which costs a redeploy and never a false
    clearance.

    The first two are told apart by the workspace and never by the policy file,
    because every way a workspace *has* a policy and cannot hand it over — the
    file deleted, a directory or a dangling link in its place, a name this
    process cannot stat — leaves the line it configured unknown rather than
    absent, and unknown is precisely the case the default may not answer.

    Each named line is resolved to the protocol it carries as well as to the
    commit it names (`lockfile.canonical_identity`), and the payload mapping's own
    predicate is handed down for it. The deploy layer owns which canonical files
    reach a target and already reads `placement`, so the predicate travels from
    here rather than the other way; a second copy of that mapping is the only
    other way to have it, and two copies is how one reader comes to call two
    commits the same protocol while another calls them different.
    """

    if _no_workspace(repo_root):
        return placement.source_lines(repo_root, deploys=deploys_canonical_path)
    try:
        config = load_config(repo_root)
    except (EvolutionError, OSError, ValueError) as error:
        return SourceLines(
            ref=None,
            base=None,
            candidate=None,
            unresolved=(f"this repository's evolution policy does not read, so it names no source line: {error}",),
        )

    ref = config.source_line.ref
    try:
        current = lineage.describe(config).current
    except (EvolutionError, OSError, ValueError) as error:
        return placement.source_lines(
            repo_root,
            ref=ref,
            unresolved=(f"this checkout cannot read the experiment lineage, so it names no candidate: {error}",),
            deploys=deploys_canonical_path,
        )
    return placement.source_lines(
        repo_root,
        ref=ref,
        candidate=current.candidate_revision if current is not None else None,
        deploys=deploys_canonical_path,
    )


def _no_workspace(repo_root: Path) -> bool:
    """Whether this repository keeps no evolution workspace at all.

    The only question the default line may be decided on. It is asked of the
    workspace root rather than of the policy file inside it, and asked by name:
    `lstat`, so a link is the presence of a workspace rather than the presence of
    whatever it resolves to, and so a name that is there in any form at all
    counts as there. Absence is the filesystem saying the name does not exist —
    a root this process cannot stat is a workspace it cannot see, which is not
    the same fact and gets the same refusal as a policy it cannot read.
    """

    try:
        (repo_root / VERSIONED_ROOT).lstat()
    except FileNotFoundError:
        return True
    except OSError:
        # Every other way the name will not answer: a permission that stops the
        # walk, a component that is not a directory, a path the platform refuses.
        # None of them says nothing is there.
        return False
    return False


def _holding(
    config: EvolutionConfig,
    name: str,
    reading: TargetRevision,
    promotion: str,
    rollback: str | None,
    placed: dict[str, tuple[str, str | None]],
) -> TargetHolding:
    """One planned name, resolved as far as this machine can take it.

    A reading with no revision is already the whole answer — the deploy layer
    said why — and is carried through unchanged. A revision is placed here,
    because where it sits relative to *this* promotion is the only part of the
    question this module owns.
    """

    stated = reading.revision
    if stated is None:
        # Never both, and never neither: a reading states a reason exactly where
        # it states no revision (`placement.TargetRevision`), so what goes out
        # here is the deploy layer's own word for this target.
        assert reading.state is not None
        return TargetHolding(
            target=name,
            path=reading.path,
            revision=None,
            state=reading.state,
            detail=reading.detail,
        )

    if stated not in placed:
        placed[stated] = _place(config, stated, promotion, rollback)
    state, detail = placed[stated]
    return TargetHolding(target=name, path=reading.path, revision=stated, state=state, detail=detail)


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
