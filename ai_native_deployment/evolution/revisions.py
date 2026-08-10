"""This package's whole conversation with Git.

Three questions, all read-only and none of them fatal: a question Git cannot
answer returns None rather than raising, because a status command must still
answer.

**The release line** (`release_line_revision`). Contract invariant 8 pins the
runner: the stable protocol revision governing an evolution task stays fixed for
that task, and a candidate revision never governs the run that creates it. It is
therefore read from the most recent release tag reachable from HEAD — never from
the branch tip, which on a working branch *is* a candidate.

**The experiment refs** (`ref_tip`, `contains`). Where an experiment's durable
ref sits, and whether one revision descends from another.

What is deliberately *not* here any more is a lifecycle reading built out of
`HEAD` against that tag. It answered "is this checkout on the release line",
which names no experiment, changes with a `git checkout`, and reports a
candidate for any unrelated branch (contract: What is derived). The revisions in
play are properties of the batch and experiment records, so `lineage.py` derives
them and `phase.py` reports them; this module only resolves the names.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

RELEASE_TAG_GLOB = "v[0-9]*"


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

    if not _is_repository_root(repo_root):
        return None
    return _release_tag(repo_root)


def ref_tip(repo_root: Path, ref: str) -> str | None:
    """The commit a ref points at, or None when this checkout does not have it.

    None is the ordinary answer on a fresh clone: `refs/evolution/experiments/*`
    is outside the default fetch refspec, so a repository can hold every
    experiment record and none of the refs. That absence is a fact about this
    checkout, never about the lineage — the record's pinned revisions are what
    identify the trees, and the ref is what keeps them reachable where it exists.
    """

    if not _is_repository_root(repo_root):
        return None
    return _git(repo_root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}") or None


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


def _release_tag(repo_root: Path) -> str | None:
    return _git(repo_root, "describe", "--tags", "--abbrev=0", "--match", RELEASE_TAG_GLOB) or None


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
