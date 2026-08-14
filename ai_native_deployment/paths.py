"""Shared filesystem locations for this checkout, and the identity of a path
within a target."""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCE_ROOT = PACKAGE_DIR.parent

CANONICAL_DIRNAME = "canonical"
MANIFEST_FILENAME = ".ai-deploy-manifest.json"
LOCK_FILENAME = ".ai-deploy-lock.json"
REGISTRY_RELATIVE_PATH = Path(".registry") / "repos.local.json"
CLAUDE_USER_SKILLS_RELATIVE_PATH = Path(".claude") / "skills"

GITIGNORE_BEGIN = "# BEGIN ai-native-deployment"
GITIGNORE_END = "# END ai-native-deployment"


def target_path_identity(target_relative_path: str) -> str:
    """The file a target path names, as a comparable key.

    Two target paths that differ only in case or in Unicode normalization are
    one file on a default macOS or Windows volume. Measured on this checkout's
    APFS volume: `.CURSOR/hooks/x` and `.cursor/hooks/x` open the same inode,
    and a name written decomposed reopens the file written composed. So string
    equality does not answer the question the payload boundary asks — whether
    two canonical files are carried to one target file
    (`deploy.iter_deployment_items`) — and answering it with `==` lets a deploy
    write both, leave the target holding whichever came last, and still record
    a receipt describing both.

    Folded rather than probed, and folded identically on every host. The payload
    is the canonical tree's and the lock is portable, so a rule that asked the
    running filesystem would make one canonical tree a valid payload on Linux
    and an invalid one on macOS, and make a receipt's meaning depend on the
    machine that wrote it. The fold is therefore the conservative intersection
    of the supported semantics — Unicode canonical caseless matching (D146),
    which covers the case-insensitive and the normalization-insensitive volume
    at once — so it also refuses a pair only some hosts would alias. That
    direction is the safe one: a refusal is loud and answered by renaming a
    canonical file, while an accepted alias silently drops one and leaves a
    receipt naming a canonical commit for bytes the target does not hold.
    """

    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", target_relative_path).casefold())


def source_root() -> Path:
    """Return the canonical source checkout root."""

    override = os.environ.get("AI_NATIVE_DEPLOYMENT_SOURCE_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return DEFAULT_SOURCE_ROOT.resolve()


def canonical_root(root: Path | None = None) -> Path:
    return (root or source_root()) / CANONICAL_DIRNAME


def manifest_path(target_root: Path) -> Path:
    return target_root / MANIFEST_FILENAME


def lock_path(target_root: Path) -> Path:
    return target_root / LOCK_FILENAME


def registry_path(root: Path | None = None) -> Path:
    override = os.environ.get("AI_NATIVE_DEPLOYMENT_REGISTRY")
    if override:
        return Path(override).expanduser().resolve()
    return (root or source_root()) / REGISTRY_RELATIVE_PATH


def claude_user_skills_root() -> Path:
    """Personal-level skill root. Read-only here: nothing in this package
    writes to it — status reads it because a personal-level skill overrides a
    same-named deployed one. The override exists for tests and for a relocated
    agent home."""

    override = os.environ.get("AI_NATIVE_DEPLOYMENT_CLAUDE_SKILLS_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / CLAUDE_USER_SKILLS_RELATIVE_PATH
