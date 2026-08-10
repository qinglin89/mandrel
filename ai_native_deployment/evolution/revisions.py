"""Which canonical revision governs an evolution run.

Contract invariant 8 pins the runner: the stable protocol revision governing an
evolution task stays fixed for that task, and a candidate revision never
governs the run that creates it. The release line is therefore read from the
most recent release tag reachable from HEAD — never from the branch tip, which
on a working branch *is* the candidate.

Both revisions are what a lifecycle reader needs (invariant 10: a candidate is
exercised against a baseline, then promoted, revised, or reverted), so this
module names them together as well as separately.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

RELEASE_TAG_GLOB = "v[0-9]*"


@dataclass(frozen=True)
class Revision:
    """One commit in play, and the name it is known by.

    `ref` is None on a detached HEAD: the commit is still the answer, but there
    is no branch or tag to call it — which is a fact worth showing rather than
    an absence worth hiding.
    """

    sha: str
    ref: str | None = None

    def describe(self) -> str:
        short = self.sha[:12]
        return f"{self.ref} ({short})" if self.ref else short


@dataclass(frozen=True)
class Revisions:
    """The baseline and the candidate, as far as this checkout can tell.

    Either may be None, and each None means something different: no baseline is
    a checkout with no release tag, while no candidate is a checkout sitting
    exactly on the release line — nothing is being tried against it.
    """

    baseline: Revision | None = None
    candidate: Revision | None = None


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


def describe_revisions(repo_root: Path) -> Revisions:
    """Both revisions in play, for a lifecycle reader.

    The candidate is the tip whenever the tip is not the baseline commit — one
    rule, no special case for a checkout that has no release tag at all. There
    the tip is still what a run would execute, and calling it the candidate says
    so; what is missing in that repository is the baseline to measure it
    against, which the None baseline reports on its own.

    Read-only and never fatal: a path that is not the top of a work tree
    (`_is_repository_root`, which is also what stops a directory nested inside
    another repository from reporting its host's revisions) yields two Nones
    rather than an error, because a status command must still answer.
    """

    if not _is_repository_root(repo_root):
        return Revisions()

    tag = _release_tag(repo_root)
    baseline = None
    if tag:
        sha = _git(repo_root, "rev-parse", f"{tag}^{{commit}}")
        baseline = Revision(sha=sha, ref=tag) if sha else None

    head = _git(repo_root, "rev-parse", "HEAD")
    if not head or (baseline is not None and head == baseline.sha):
        return Revisions(baseline=baseline)
    branch = _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    return Revisions(baseline=baseline, candidate=Revision(sha=head, ref=branch if branch and branch != "HEAD" else None))


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
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    return result.stdout.strip()
