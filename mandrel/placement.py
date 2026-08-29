"""Which canonical revision a target holds, and which ones it may hold.

Two readings, and both of them are about *identity* rather than about files. A
deployed target's `.ai-deploy-lock.json` states the commit its payload came
from, and states one only where the deployment's own captured paths, bytes and
modes matched that commit's deployable canonical tree exactly
(`lockfile.payload_source_commit`). So the receipt is trusted for what it says
the target holds; nothing here re-reads a target's files to check it.

**What the comparison is over.** The protocol that commit carries
(`lockfile.canonical_identity`), never the commit string. A commit is a
whole-repository identity: it moves for work under `mandrel/`, `tests/`, `docs/`
and `.ai/` that no target ever receives, so comparing strings would report every
ordinary commit on the source line as a protocol change on every deployed target
at once — a fleet-wide redeploy notice with a one-commit fuse, and readiness is
what consumes it. Two commits carrying the same deployable canonical tree are the
same deployment, and the receipt still names the commit an operator would
redeploy from.

**What a name resolves to.** A target is a name in the machine-local registry, a
directory that name is registered for, a receipt in that directory, and the
revision the receipt states — four steps, each of which can be the end of the
road. The whole chain is here because the two surfaces that walk it are `status`,
answering for one target an operator named, and the evolution console, answering
for the targets a promotion was planned for. One chain, one vocabulary for its
dead ends; two spellings would let one surface report a receipt the other refuses
to read.

**The five ways there is no revision.** They are not one state, because each is a
different next step and only some of them are work owed here: a name this machine
manages no repository for, a directory nothing has been deployed to, a receipt
this build cannot read, a receipt that ties its payload to no commit, and a name
two registered repositories share. Folding them together would report somebody
else's machine's targets, or a corrupt file, as a redeploy this operator owes.

**It never raises.** Every file this module reads belongs to a repository this
tool does not own, or to a machine-local inventory that is not lifecycle state at
all. So an unreadable one is reported as the state of that one target — and the
inventory, when it is the thing that will not read, as the state of every name at
once, since without it this machine cannot say which repository any name is.

**What it does not do.** It asks Git for the tree a commit holds and for nothing
else — never for history. Whether a stated revision *descends from* some commit
belongs to whoever is asking that (`evolution.deployment`); ancestry here would
clear a target a release behind, which is the one thing a receipt exists to
prevent. What is asked *here* is the narrower question `status` needs: whether
the target is running the protocol one of the two lines this repository is
currently deploying from carries.

**The lines.** At any moment there is exactly one **base** — the commit at the
configured source line — and at most one **candidate**, the latest sealed round
of an open experiment. This module takes both as values rather than deriving
them: the candidate is a fact about the evolution lineage, and a deploy-layer
verb that had to load an evolution policy before it could answer would make the
whole deployment surface depend on a workspace that gates none of it. The
caller that knows about experiments states them (`evolution.deployment`); the
caller that does not gets base alone, which is the answer for a checkout that
runs no experiments.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .lockfile import CanonicalIdentities, LockError, read_lock, stated_source_commit
from .paths import lock_path, registry_path, source_root
from .registry import RegistryError, load_registry

# The reasons a target states no revision to place. Preserved as the exact
# strings the evolution console has always published for them
# (`evolution.deployment`), because they are a machine surface there.
UNPLACED_UNSTATED = "unstated"
UNPLACED_NO_RECEIPT = "no-receipt"
UNPLACED_UNREADABLE = "unreadable"
UNPLACED_UNREGISTERED = "unregistered"
UNPLACED_AMBIGUOUS = "ambiguous"

# Where a stated revision stands among the lines this repository deploys from.
# Two names and an absence: a revision that is neither is not a third line, it is
# a target standing on a line nothing here is currently releasing.
LINE_BASE = "base"
LINE_CANDIDATE = "candidate"

# The line a checkout deploys from when nothing states another. Every repository
# that runs an evolution policy names its own (`evolution/config.toml`
# `[source_line].ref`) and that name wins; this is what remains for a checkout
# with no policy to consult, and it is deliberately a fully-qualified ref for the
# reason the policy pattern requires one — a bare branch name or a revision
# expression is resolved against whatever the reading repository is sitting on.
#
# It is independent of this repository's own `[source_line].ref` by design, and
# the two currently differ: evolution here promotes onto `refs/heads/dev`, and
# this stays `refs/heads/main`. They answer different questions. This one is the
# convention a repository with no evolution workspace is judged by, so it has to
# be the branch such a repository most likely releases from; the policy setting
# is one project's decision about where its own promotions land. Tracking that
# decision here would ship a project-specific default to every repository that
# has no project — and would move it whenever one project moved, quietly, since
# both values are valid refs and only one of them is the reader's release.
DEFAULT_SOURCE_LINE = "refs/heads/main"


@dataclass(frozen=True)
class TargetRevision:
    """What one target's own files say its payload came from.

    `revision` is the receipt's `source_git_commit` verbatim, and it is a full
    object id or it is not here at all (`lockfile.stated_source_commit`): a
    receipt naming `HEAD`, a branch, or an abbreviation would be resolved against
    the *reading* repository and would move whenever that did, so it is refused
    as unreadable rather than placed.

    `state` is None exactly when a revision was read. The two fields are one
    answer read from either end — the revision when there is one, the reason when
    there is not — rather than a value and a status that can disagree.
    """

    path: str | None
    revision: str | None
    state: str | None
    # The one sentence a state needs that its name cannot carry: which paths a
    # name is ambiguous between, what could not be read, why a receipt places
    # nothing. None where the state says the whole of it.
    detail: str | None = None

    @property
    def stated(self) -> bool:
        return self.revision is not None


@dataclass(frozen=True)
class Placement:
    """Where one stated revision stands among the lines being released.

    Three answers in two fields, and they are one answer read from either end.
    `line` names the line whose protocol the revision carries. Both fields None is
    an answer too: the revision was read and carries neither. `unreadable` is the
    third and is a fact about the reading checkout rather than about the target —
    a revision whose canonical tree this clone does not hold, which is a question
    left open rather than a target found to be elsewhere.

    Kept apart because they are different next steps. "Neither line" is a
    redeploy; "cannot say" is a fetch, and reporting it as a redeploy would send
    an operator to overwrite a target nobody established anything about.
    """

    line: str | None
    unreadable: str | None = None

    @property
    def placed(self) -> bool:
        return self.line is not None


@dataclass(frozen=True)
class SourceLines:
    """The lines a deployed target may be standing on, and what each deploys.

    `base` is the commit at `ref` right now; `candidate` is the sealed candidate
    a target may also legitimately be carrying, and None whenever there is none —
    no experiment open, or its latest round still taking work. Each is named as
    the commit an operator redeploys from and compared as the protocol it carries
    (`place`), which are two different facts about one string.

    A sealed round is the only candidate that may appear here. An open round's ref
    tip moves, so a target deployed from it produces evaluation reports naming a
    tree nothing can afterwards identify (evolution/README.md, invariant 16), and
    accepting the tip would make this surface bless exactly the deployment that
    cannot be measured.

    `unresolved` carries the complaints only: an absence with an ordinary reason
    — no experiment is open — is not one. What goes in is a line this repository
    should have been able to name and could not, which is the difference between
    "there is no candidate" and "this checkout cannot say whether there is one".
    """

    # None when this repository would not say which line it releases on — which
    # is a refusal to guess, not a default. `base` is then None with it.
    ref: str | None
    base: str | None
    candidate: str | None
    unresolved: tuple[str, ...] = ()
    # How a revision becomes the protocol it carries, bound to the repository
    # these lines were resolved in and shared across every target a sweep places
    # (`lockfile.CanonicalIdentities`). None only where there is no line to place
    # against anyway: `source_lines` always builds one, and the caller that
    # constructs this directly does so precisely to say it can name no line
    # (`evolution.deployment.source_lines`).
    identities: CanonicalIdentities | None = None

    @property
    def known(self) -> bool:
        """Whether any line was resolved at all — to a commit and to the protocol
        that commit carries, since a line whose payload this checkout cannot read
        is one nothing may be placed against and is dropped with the rest.

        False is not "nothing is current" — it is a reading that could not name a
        line, and a caller reporting it as a target's drift would blame the
        target for it."""

        return self.base is not None or self.candidate is not None

    def place(self, revision: str) -> Placement:
        """Which line's protocol `revision` carries, or why it carries neither.

        Canonical identity, never ancestry and never the commit string. A target
        deployed from a commit base has since built on top of is not at base
        *unless base built nothing a target receives*: what a receipt exists to
        state is the protocol that target is running, and two commits with the
        same deployable canonical tree are running the same one. Ancestry would
        clear a target a genuine release behind; string equality condemns every
        target on the fleet the next time anyone commits a test.

        Base first, so a candidate ref left sitting at base after a promotion —
        and any other case where the two lines carry one protocol — reads as the
        line it now is.

        Nothing is asked of Git where there is no line to place against: a
        checkout that could name none has already said the whole of what it can
        say, and running a lookup for it would turn its own silence into a
        finding about the target.
        """

        if self.identities is None or not self.known:
            return Placement(line=None)
        if self.identities.of(revision) is None:
            return Placement(
                line=None,
                unreadable=(
                    f"this checkout does not read the canonical tree of {revision}, "
                    "so it cannot say which protocol that payload carries"
                ),
            )
        if self.base is not None and self.identities.same(revision, self.base):
            return Placement(line=LINE_BASE)
        if self.candidate is not None and self.identities.same(revision, self.candidate):
            return Placement(line=LINE_CANDIDATE)
        return Placement(line=None)


def stated_revision(target_root: Path) -> TargetRevision:
    """What one target's receipt states, read from the target itself.

    For a caller that already has the directory — `status`, whose operator named
    it — so the two registry states below cannot arise and are not reported.
    """

    return _receipt(target_root, str(target_root))


def registered_revisions(
    names: Iterable[str],
    *,
    root: Path | None = None,
    registry_file: Path | None = None,
) -> tuple[TargetRevision, ...]:
    """What each named target states, resolved through the machine-local
    registry, in the order the names were given.

    The order is the caller's own: a list of planned targets is a record's list,
    and a reading that sorted it would stop being the list beside it.
    """

    wanted = tuple(names)
    root = (root or source_root()).resolve()
    try:
        entries = load_registry(registry_file, root)
    except (RegistryError, OSError, ValueError) as error:
        # The inventory itself rather than any one receipt, and reported against
        # every name because that is what it costs: this machine cannot say which
        # repository any of them is.
        #
        # Three families and one state. `RegistryError` is the registry module's
        # own complaint about a document that is not a registry; an `OSError` is a
        # file this process could not read at all — a permission, a directory
        # where the file belongs — and a `ValueError` is bytes that are not UTF-8
        # text. Only the first arrives named, so the path this reading asked for
        # is stated with the other two.
        detail = _inventory_complaint(root, registry_file, error)
        return tuple(
            TargetRevision(path=None, revision=None, state=UNPLACED_UNREADABLE, detail=detail) for _ in wanted
        )

    known: dict[str, list[str]] = {}
    for entry in entries:
        known.setdefault(entry["name"], []).append(entry["path"])
    return tuple(_named(name, known.get(name, [])) for name in wanted)


def source_lines(
    root: Path | None = None,
    *,
    ref: str | None = DEFAULT_SOURCE_LINE,
    candidate: str | None = None,
    unresolved: tuple[str, ...] = (),
    deploys: Callable[[str], bool],
) -> SourceLines:
    """The lines a target may be standing on, with `ref` and both protocols
    resolved here.

    Resolution is the whole of what this adds: the caller states which line it
    releases on and which candidate is sealed, and this turns each into the
    commit it names today and the protocol that commit carries. A line this
    checkout cannot resolve — the ref names nothing, or its canonical tree is not
    readable here — is dropped with the reason stated. That is a fact about the
    reading repository, never about any target, and reporting it as a target's
    drift would name a redeploy that is not owed.

    Both failures are the line's rather than a target's, which is why they are
    settled here and not at the moment a target is placed: a line whose protocol
    this checkout cannot read is one nothing can be placed against, and the
    caller already has one vocabulary for that (`unresolved`, `known`). A target
    whose *own* revision will not read is a different finding and is the target's
    (`place`).

    `deploys` is the payload mapping's own predicate, injected rather than
    imported: the mapping lives in the deploy layer and the deploy layer already
    reads this module, so a second copy of "which canonical files reach a target"
    is the only other way to have it — and two copies is exactly how one reader
    comes to call two commits the same protocol while another calls them
    different.
    """

    root = (root or source_root()).resolve()
    identities = CanonicalIdentities(root, deploys=deploys)
    base: str | None = None
    complaints = list(unresolved)
    if ref is not None:
        base = _ref_commit(root, ref)
        if base is None:
            complaints.append(f"{root} does not resolve {ref}, so this checkout cannot name the current release")
        elif identities.of(base) is None:
            complaints.append(
                f"{root} does not read the canonical tree of {ref} at {base}, "
                "so this checkout cannot say what the current release deploys"
            )
            base = None
    if candidate is not None and identities.of(candidate) is None:
        complaints.append(
            f"{root} does not read the canonical tree of the sealed candidate {candidate}, "
            "so this checkout cannot say what it deploys"
        )
        candidate = None
    return SourceLines(
        ref=ref,
        base=base,
        candidate=candidate,
        unresolved=tuple(complaints),
        identities=identities,
    )


def _named(name: str, paths: list[str]) -> TargetRevision:
    """One registered name, resolved as far as this machine can take it."""

    if not paths:
        return TargetRevision(
            path=None,
            revision=None,
            state=UNPLACED_UNREGISTERED,
            detail="no repository of that name is registered on this machine",
        )
    if len(paths) > 1:
        # A caller's list names one target. Two registered repositories sharing a
        # directory name is a real state of the local inventory, and picking one
        # would answer for a repository the caller may not have meant.
        return TargetRevision(
            path=None,
            revision=None,
            state=UNPLACED_AMBIGUOUS,
            detail=f"{len(paths)} repositories are registered under that name: {', '.join(sorted(paths))}",
        )
    return _receipt(Path(paths[0]), paths[0])


def _receipt(target_root: Path, path: str) -> TargetRevision:
    """The revision one directory's receipt states, or why it states none."""

    receipt = lock_path(target_root)
    if not receipt.is_file():
        return TargetRevision(
            path=path,
            revision=None,
            state=UNPLACED_NO_RECEIPT,
            detail=f"no deploy receipt at {receipt}",
        )
    try:
        stated = stated_source_commit(read_lock(target_root))
    except (OSError, ValueError, LockError) as error:
        # One state for every way a receipt does not answer: bytes this process
        # could not read, JSON that does not parse — `ValueError`, which is also
        # bytes that are not UTF-8 text to begin with — and a document that parses
        # into something other than a receipt this build reads, which is the
        # deploy contract's own judgement rather than this module's.
        return TargetRevision(path=path, revision=None, state=UNPLACED_UNREADABLE, detail=f"{receipt}: {error}")
    if stated is None:
        # The receipt's own null, and an ordinary one: a deploy states a source
        # commit only where the payload it copied matched that commit's tree
        # exactly (`lockfile.payload_source_commit`).
        #
        # Only that null reaches here. A receipt omitting the field is refused
        # above and read as `unreadable`, which is the difference between a target
        # whose payload nothing can place and a document that could not answer.
        return TargetRevision(
            path=path,
            revision=None,
            state=UNPLACED_UNSTATED,
            detail="the receipt ties its payload to no source commit, so nothing places what it holds",
        )
    return TargetRevision(path=path, revision=stated, state=None)


def _inventory_complaint(root: Path, registry_file: Path | None, error: Exception) -> str:
    """Why the registry could not be read, always naming the file.

    `RegistryError` names it already. An `OSError` or a decode error is Python's
    own and may name nothing an operator can act on — a byte offset says which
    position, never which file — so the path this reading asked for is stated
    with it.
    """

    if isinstance(error, RegistryError):
        return str(error)
    return f"{registry_file or registry_path(root)}: {error}"


def _ref_commit(root: Path, ref: str) -> str | None:
    """The commit `ref` names in this checkout, or None when it cannot say.

    Asked of `root` only when `root` is itself the top of a work tree. A path
    merely *inside* another repository would otherwise be answered by that
    repository's branches — which is how a temporary directory under someone's
    checkout silently acquires a release line, and how every target deployed from
    it would then be judged against a line it has nothing to do with.
    """

    top = _git(root, "rev-parse", "--show-toplevel")
    if top is None:
        return None
    try:
        if Path(top).resolve() != root:
            return None
    except OSError:
        return None
    return _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")


def _git(root: Path, *arguments: str) -> str | None:
    """Git's answer, or None for every way it did not give one.

    A refused question and an absent binary are the same answer here, because
    what is being asked is "what does this checkout say", and a checkout that
    cannot be asked says nothing.
    """

    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None
