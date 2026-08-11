"""This package's whole conversation with Git.

The questions are read-only and none of them is fatal: a question Git cannot
answer returns None rather than raising, because a status command must still
answer. Five things here are not questions: `create_ref` / `move_ref`,
`merge_tree` / `revert_tree`, and `commit_tree`, which write and report their
failure instead of raising, and `held_at`, which writes nothing but takes Git's
own ref lock and therefore refuses when it cannot.

**The release line** (`release_line_revision`, `release_ref`). Contract
invariant 8 pins the runner: the stable protocol revision governing an evolution
task stays fixed for that task, and a candidate revision never governs the run
that creates it. It is therefore read from the most recent release tag reachable
from a commit — never from the branch tip, which on a working branch *is* a
candidate.

**The experiment refs** (`ref_tip`, `contains`, `resolve_commit`, `create_ref`,
`move_ref`, `held_at`). Where an experiment's durable ref sits, whether one
revision descends from another, which commit a name resolves to, the operations
that create a ref for a new experiment and move one from where it was observed,
and the one that holds a ref still while its record catches up with it.

**The integration** (`merge_tree`). What a candidate and the source line produce
together, computed without a checkout — the tree replay measures and a promotion
would carry.

**The promotion and its reversal** (`commit_tree`, `revert_tree`, `commit_shape`,
`checked_out_refs`). The commit that carries a measured tree onto the source
line and the one that takes it back off; the tree a revert produces against the
line as it now stands; the shape either commit is held to when a record claims
it; and which branches some working tree is sitting on, asked before this
package moves a ref anyone could be standing on. Whether a promotion *reached*
the line, or its inverse did, is `contains` — the recorded commit, on that line
— rather than a shape or a tip.

What is deliberately *not* here any more is a lifecycle reading built out of
`HEAD` against that tag. It answered "is this checkout on the release line",
which names no experiment, changes with a `git checkout`, and reports a
candidate for any unrelated branch (contract: What is derived). The revisions in
play are properties of the batch and experiment records, so `lineage.py` derives
them and `phase.py` reports them; this module only resolves the names.
"""

from __future__ import annotations

import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

from .errors import RefHoldError

RELEASE_TAG_GLOB = "v[0-9]*"

# `git update-ref`'s spelling of "this ref must not exist yet". Passing it as the
# expected old value makes creation a compare-and-swap Git performs under its own
# ref lock, rather than a check this process makes and then acts on.
ABSENT_REVISION = "0" * 40

# What `git update-ref --stdin` says once a transaction is prepared. Preparing is
# what takes Git's lock on every ref in the transaction and holds it until the
# transaction is committed or aborted — which is the whole mechanism here.
_PREPARED = "prepare: ok"


@dataclass(frozen=True)
class Revision:
    """One commit in play, and the name it is known by.

    `ref` is None for a commit no name leads to — a round's pinned candidate, a
    promotion revision on the source line. The commit is still the answer; what
    is missing is a shorthand for it, which is a fact worth showing rather than
    an absence worth hiding.
    """

    sha: str
    ref: str | None = None

    def describe(self) -> str:
        short = self.sha[:12]
        return f"{self.ref} ({short})" if self.ref else short


def release_line_revision(repo_root: Path) -> str | None:
    """The stable release-line revision of `repo_root`, or None when there is none.

    None is a real answer, not a failure: a checkout with no release tag has no
    stable protocol revision to name, and the manifest and generated task say so
    explicitly rather than substituting the candidate they happen to sit on
    (invariant 4 keeps missing fields explicit).
    """

    return release_ref(repo_root, "HEAD")


def release_ref(repo_root: Path, revision: str) -> str | None:
    """The most recent release tag reachable from `revision`, or None when there
    is none.

    An experiment records this beside the base revision it froze, so the record
    still states which released protocol that candidate builds on after the tag
    itself has moved or gone. A commit ahead of every tag has no answer, and that
    is recorded as the absence it is.
    """

    if not _is_repository_root(repo_root):
        return None
    return _git(repo_root, "describe", "--tags", "--abbrev=0", "--match", RELEASE_TAG_GLOB, revision) or None


def resolve_commit(repo_root: Path, revision: str) -> str | None:
    """The full sha `revision` names, or None when this checkout cannot say.

    Used where a revision is about to be *recorded*: a base revision or a ref
    creation names one commit forever, so it is resolved to the sha rather than
    stored as the branch, tag, or `HEAD` the operator happened to type — every
    one of which means a different commit tomorrow.
    """

    if not _is_repository_root(repo_root):
        return None
    return _git(repo_root, "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}") or None


def ref_tip(repo_root: Path, ref: str) -> str | None:
    """The commit a ref points at, or None when this checkout does not have it.

    None is the ordinary answer on a fresh clone: `refs/evolution/experiments/*`
    is outside the default fetch refspec, so a repository can hold every
    experiment record and none of the refs. That absence is a fact about this
    checkout, never about the lineage — the record's pinned revisions are what
    identify the trees, and the ref is what keeps them reachable where it exists.
    """

    return resolve_commit(repo_root, ref)


def create_ref(repo_root: Path, ref: str, revision: str) -> str | None:
    """Create `ref` at `revision`, or say why that did not happen.

    Creation only: the expected old value is `ABSENT_REVISION`, so an existing
    ref refuses instead of being moved. A ref already holding an experiment's
    rounds is the one thing that must never be reset.
    """

    return move_ref(repo_root, ref, revision, ABSENT_REVISION)


def move_ref(repo_root: Path, ref: str, revision: str, expected: str) -> str | None:
    """Move `ref` from `expected` to `revision`, or say why that did not happen.

    None means moved. Anything else is Git's own complaint, returned rather than
    raised so the caller can name what the ref was to it — which is the part an
    operator needs and this module does not know.

    The expectation is not advisory: `update-ref` compares and swaps under its
    own ref lock, so the reading a caller decided from and the write it decided
    on are one operation rather than a look followed by a leap. That is the whole
    of what protects a source line here — between reading where it stands and
    putting a promotion on it, what moves it is ordinary Git, and this package's
    single-writer lock covers none of that. `ABSENT_REVISION` is the same
    mechanism spelled for a ref that must not exist yet (`create_ref`).
    """

    result = _run(repo_root, "update-ref", ref, revision, expected)
    if result is None:
        return "git is not available here"
    if result.returncode == 0:
        return None
    return " ".join((result.stderr or result.stdout or f"git update-ref exited {result.returncode}").split())


def commit_tree(
    repo_root: Path,
    tree: str,
    parents: Sequence[str],
    message: str,
) -> tuple[str | None, str]:
    """A commit carrying `tree` with `parents` in order, or why there is none.

    Both of the commits this package puts on the source line are made here, and
    each is built from a tree that was decided before the commit was: the merge
    unit a promotion carries — two parents, the line as it stood and the
    candidate, with the tree replay measured — and the inverse commit a rollback
    carries, one parent and the tree the line has once the promotion's change is
    taken back out. Neither is the result of a merge performed by this call;
    recomputing one is a check the caller makes on the tree, never the source of
    what gets committed.

    `commit-tree` writes an object and names it in nothing, which is what makes
    the order recoverable: an interrupted promotion or rollback leaves a commit
    nothing points at, and Git collects it. What makes it real is the ref move
    after it.

    Returns the commit and no complaint, or no commit and Git's own words — the
    `merge_tree` shape rather than a raise. The complaint an operator meets most
    is an unset `user.email`: these commits carry whoever made them, and
    inventing an identity would put this package's name on a human decision.
    """

    if not _is_repository_root(repo_root):
        return None, f"{repo_root} is not the top of a work tree"
    arguments = ["commit-tree", tree]
    for parent in parents:
        arguments += ["-p", parent]
    result = _run(repo_root, *arguments, "-m", message)
    if result is None:
        return None, "git is not available here"
    if result.returncode != 0:
        complaint = result.stderr.strip() or result.stdout.strip() or f"git commit-tree exited {result.returncode}"
        return None, " ".join(complaint.split())
    written = result.stdout.strip()
    if not written:
        return None, "git commit-tree reported success and wrote no commit"
    return written, ""


def checked_out_refs(repo_root: Path) -> dict[str, str] | None:
    """Every branch a working tree of this repository is sitting on, and where.

    Asked before a ref this package is about to move: moving the branch a work
    tree is on leaves that work tree and its index describing the commit before,
    so every file the move brought in reads as a pending deletion and an operator
    reaching for the obvious repair loses whatever else was uncommitted there.
    Nothing else here writes to a ref anyone checks out, which is why this is
    asked in one place rather than by every writer.

    Every worktree rather than this process's own `HEAD`, because `HEAD` answers
    only for the checkout that asks. A branch handed to a linked worktree
    (`git worktree add`) is one nothing here would otherwise look at, and
    low-level `update-ref` moves it with no complaint at all — that worktree is
    left in exactly the state described above, in a directory the operator who
    promoted may not even know about.

    None means Git could not say, which is not an empty mapping: a repository
    whose every worktree is detached answers with one. A guard that cannot get an
    answer refuses rather than proceeds.
    """

    if not _is_repository_root(repo_root):
        return None
    listed = _git(repo_root, "worktree", "list", "--porcelain")
    if listed is None:
        return None
    checked_out: dict[str, str] = {}
    directory = ""
    for line in listed.splitlines():
        # A worktree is a `worktree <path>` line and the lines under it; a
        # detached one carries no `branch` line at all, which is precisely the
        # case this guard has nothing to say about.
        if line.startswith("worktree "):
            directory = line[len("worktree ") :]
        elif line.startswith("branch "):
            checked_out[line[len("branch ") :]] = directory
    return checked_out


def commit_shape(repo_root: Path, revision: str) -> tuple[str, tuple[str, ...]] | None:
    """The tree a commit carries and the commits it was made from, or None when
    this checkout cannot say.

    What a promotion is recognised by when its records did not land. A commit's
    own sha depends on when it was made, so a redo cannot recompute the one an
    interrupted run created; what it can do is ask whether the commit the source
    line now stands at was made from those two parents and carries that tree,
    which no other commit is (contract: recognition is identity).
    """

    described = _git(repo_root, "show", "--no-patch", "--format=%T %P", f"{revision}^{{commit}}")
    if not described:
        return None
    fields = described.split()
    return fields[0], tuple(fields[1:])


def merge_tree(repo_root: Path, ours: str, theirs: str) -> tuple[str | None, str]:
    """The tree merging `theirs` into `ours` produces, or why there is none.

    What replay measures is the candidate *as it would be integrated*, and what
    a promotion carries is a tree rather than a pair of commits: a different
    merge base, a conflict resolved by hand, and a different strategy all land
    somewhere else from the same two revisions. So the tree is computed here,
    from the two commits, and recorded with them.

    `git merge-tree --write-tree` computes it without a working tree, an index,
    or a checkout — this repository is ordinarily sitting on unrelated work, and
    a merge that touched it would be a far larger promise than a replay is
    making. It does write: the resulting tree, and any blobs the merge created,
    enter the object database unreferenced, which is what lets a harness check
    that tree out. Git collects them if nothing ever names them.

    Returns the tree and no complaint, or no tree and Git's own words — the
    `create_ref` shape rather than the raise, since a candidate that does not
    integrate is an ordinary answer an operator acts on. Both failures Git spells
    the same way (a non-zero exit) are refused the same way, deliberately: a
    conflict *also* prints a tree, and that tree holds the conflict markers, so
    taking it would measure a state no promotion could produce. A Git too old
    for `--write-tree` (2.38) lands here too, saying so in its own usage error,
    for the reason a Git too old for ref transactions does in `_prepare`.
    """

    return _written_tree(repo_root, "--write-tree", ours, theirs)


def revert_tree(repo_root: Path, *, revision: str, parent: str, tip: str) -> tuple[str | None, str]:
    """The tree `tip` has once `revision`'s change is taken back out, or why
    there is none.

    A rollback's content, and it is a three-way merge rather than a tree the
    record already holds: reverting means applying the change backwards *to the
    line as it now stands*, so the commits that landed after the promotion are
    carried through rather than dropped. Git's own revert states it the same
    way, and this is that computation without a checkout — `revision` as the
    merge base, the line's tip as one side, and the commit `revision` was made
    from as the other, which is what makes the promotion's own change the
    difference that gets reversed.

    Where nothing followed the promotion the answer degenerates to the tree the
    line had before it, which is the obvious case and the one this must not be
    written as: pinning that tree would silently discard every commit since.

    A conflict is a non-zero exit and refuses like every other failure here
    (`merge_tree`): the change cannot be taken back out without deciding
    something, and deciding it is a person's job with a working tree in front of
    them. `--merge-base` needs Git 2.40, and one too old for it says so in its
    own usage error rather than being probed for.
    """

    return _written_tree(repo_root, "--write-tree", f"--merge-base={revision}", tip, parent)


def _written_tree(repo_root: Path, *arguments: str) -> tuple[str | None, str]:
    """One `git merge-tree` run and one reading of what it answered.

    Shared so that a tree computed for an integration and a tree computed for a
    revert are refused on the same terms — in particular that a conflict prints a
    tree and is still not an answer.
    """

    if not _is_repository_root(repo_root):
        return None, f"{repo_root} is not the top of a work tree"
    result = _run(repo_root, "merge-tree", *arguments)
    if result is None:
        return None, "git is not available here"
    written = result.stdout.strip().splitlines()
    if result.returncode == 0:
        if not written:
            return None, "git merge-tree reported success and wrote no tree"
        return written[0].strip(), ""
    complaint = result.stderr.strip() or result.stdout.strip() or f"git merge-tree exited {result.returncode}"
    return None, " ".join(complaint.split())


@contextmanager
def held_at(repo_root: Path, ref: str, revision: str | None) -> Iterator[None]:
    """Hold `ref` exactly where it was observed, for the length of the body.

    A read of a ref answers where it stood, which stops being true the moment
    the reading is over. Where that answer is about to be *recorded* — a
    candidate pinned, a round opened from one — the record has to be written
    while the answer still holds, and no lock this package takes can arrange
    that: what moves an experiment ref is ordinary Git, a fetch or a push or an
    operator's own `update-ref`.

    So the ref is held rather than read twice. `start` / `verify` / `prepare`
    does both jobs in one transaction: `verify` refuses when the ref has moved
    since it was observed, and preparing takes Git's own lock on it and keeps it
    until the transaction ends — every other updater fails while the body runs.
    Nothing is written; a verified transaction leaves the ref exactly as it
    found it, so committing and aborting it mean the same thing.

    `revision` is None for a ref this checkout does not hold, held then against
    creation: an absent `refs/evolution/*` is the ordinary state of a clone that
    never fetched the namespace, and one appearing mid-transition is the same
    interleaving arriving from the other side.

    Refuses rather than reports, unlike everything else here — a caller cannot
    be handed a failure and a running body at the same time. It refuses on a
    path that is not the top of a work tree for the same reason `release_ref`
    answers None there: a directory inside someone else's checkout would
    otherwise take a lock in that repository.
    """

    process = _prepare(repo_root, ref, revision or ABSENT_REVISION)
    try:
        yield
    finally:
        _release(process)


def _prepare(repo_root: Path, ref: str, expected: str) -> subprocess.Popen[str]:
    """Git's lock on `ref`, taken and held, or a refusal in Git's own words.

    Git's words are the useful ones here: a stale expectation is reported as the
    two revisions it found and wanted, and a Git too old for transactions says
    so as an unknown command. Either way nothing has been locked and the caller
    writes nothing.
    """

    if not _is_repository_root(repo_root):
        raise RefHoldError(f"{ref} cannot be held: {repo_root} is not the top of a work tree")
    try:
        process = subprocess.Popen(
            ["git", "-C", str(repo_root), "update-ref", "--stdin"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (FileNotFoundError, OSError) as exc:
        raise RefHoldError(f"{ref} cannot be held: git is not available here") from exc
    try:
        process.stdin.write(f"start\nverify {ref} {expected}\nprepare\n")
        process.stdin.flush()
        for line in iter(process.stdout.readline, ""):
            if line.strip() == _PREPARED:
                return process
    except OSError:
        pass
    raise RefHoldError(f"{ref}: {_refusal(process)}")


def _refusal(process: subprocess.Popen[str]) -> str:
    """Why the transaction did not prepare, as one line.

    Ending the process is part of the answer: a preparation that failed holds
    nothing, and closing its input is what releases whatever it did reach.
    """

    stdout, stderr = process.communicate()
    complaint = stderr.strip() or stdout.strip() or f"git update-ref exited {process.returncode}"
    return " ".join(complaint.split())


def _release(process: subprocess.Popen[str]) -> None:
    """Let the ref go, reporting nothing.

    The transaction only ever verified, so there is no outcome to report: it
    leaves the same ref committed or aborted, and by now the write it was
    protecting has either landed or been abandoned. A holder that dies releases
    the lock the same way — except when it is killed outright, which leaves
    behind the lock file Git itself names in the next update's error.
    """

    try:
        process.stdin.write("commit\n")
        process.stdin.flush()
    except OSError:
        pass
    try:
        process.communicate()
    except OSError:
        pass


def contains(repo_root: Path, ancestor: str, descendant: str) -> bool | None:
    """Whether `descendant` has `ancestor` in its history, or None if unanswerable.

    Three-valued on purpose. "Not an ancestor" is a ref that was rewritten or
    reset — the fast-forward-only rule broken (invariant 15), and a real finding.
    "Cannot tell" is a repository that does not hold both objects, which says
    nothing about the lineage and must not be reported as divergence.
    """

    result = _run(repo_root, "merge-base", "--is-ancestor", ancestor, descendant)
    if result is None:
        return None
    if result.returncode in (0, 1):
        return result.returncode == 0
    return None


def _is_repository_root(repo_root: Path) -> bool:
    """True only when `repo_root` is itself the top of a work tree.

    A path merely *inside* another repository would otherwise report that
    repository's release line as its own — which is how a temporary directory
    under someone's checkout silently acquires a runner revision.
    """

    top = _git(repo_root, "rev-parse", "--show-toplevel")
    if not top:
        return False
    try:
        return Path(top).resolve() == repo_root.resolve()
    except OSError:
        return False


def _git(repo_root: Path, *args: str) -> str | None:
    """Standard output of a git command, or None when it failed for any reason.

    Callers that need to tell one failure from another use `_run` instead: here
    a non-zero exit and an absent git binary are the same answer, which is right
    only when the question is "what does git say" rather than "did it say no".
    """

    result = _run(repo_root, *args)
    return result.stdout.strip() if result is not None and result.returncode == 0 else None


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return None
