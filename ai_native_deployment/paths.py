"""Shared filesystem locations for this checkout."""

from __future__ import annotations

import os
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
