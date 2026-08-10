"""Which canonical revision governs an evolution run.

Contract invariant 8 pins the runner: the stable protocol revision governing an
evolution task stays fixed for that task, and a candidate revision never
governs the run that creates it. The release line is therefore read from the
most recent release tag reachable from HEAD — never from the branch tip, which
on a working branch *is* the candidate.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

RELEASE_TAG_GLOB = "v[0-9]*"


def release_line_revision(repo_root: Path) -> str | None:
    """The stable release-line revision of `repo_root`, or None when there is none.

    None is a real answer, not a failure: a checkout with no release tag has no
    stable protocol revision to name, and the manifest and generated task say so
    explicitly rather than substituting the candidate they happen to sit on
    (invariant 4 keeps missing fields explicit).
    """

    if not _is_repository_root(repo_root):
        return None
    tag = _git(repo_root, "describe", "--tags", "--abbrev=0", "--match", RELEASE_TAG_GLOB)
    return tag or None


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
