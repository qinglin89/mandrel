"""Temporary global Claude skill synchronization."""

from __future__ import annotations

import filecmp
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .deploy import is_forbidden_relative_path
from .paths import claude_global_skills_root, skills_backup_root, source_root

MANAGED_SKILLS: tuple[str, ...] = (
    "ai-housekeeping",
    "ai-init",
    "ai-load",
    "ai-sync",
    "ai-sync-v2",
    "ctd-tasks",
    "intake-task",
    "invoke",
)


class SkillsSyncError(RuntimeError):
    """Raised when global skill synchronization cannot proceed safely."""


@dataclass(frozen=True)
class SkillSyncChange:
    name: str
    action: str
    source_path: Path
    target_path: Path


@dataclass(frozen=True)
class SkillSyncResult:
    source_root: Path
    target_root: Path
    dry_run: bool
    changes: tuple[SkillSyncChange, ...]

    @property
    def changed(self) -> tuple[SkillSyncChange, ...]:
        return tuple(change for change in self.changes if change.action != "unchanged")


def _dircmp_equal(left: Path, right: Path) -> bool:
    comparison = filecmp.dircmp(left, right)
    if comparison.left_only or comparison.right_only or comparison.funny_files:
        return False

    for filename in comparison.common_files:
        left_file = left / filename
        right_file = right / filename
        if left_file.read_bytes() != right_file.read_bytes():
            return False
        if (left_file.stat().st_mode & 0o777) != (right_file.stat().st_mode & 0o777):
            return False

    return all(_dircmp_equal(left / dirname, right / dirname) for dirname in comparison.common_dirs)


def _validate_skill_source(source_path: Path) -> None:
    if not source_path.is_dir():
        raise SkillsSyncError(f"missing skill backup directory: {source_path}")
    if not (source_path / "SKILL.md").is_file():
        raise SkillsSyncError(f"skill backup is missing SKILL.md: {source_path}")

    for path in source_path.rglob("*"):
        relative_path = path.relative_to(source_path)
        if is_forbidden_relative_path(relative_path):
            raise SkillsSyncError(f"forbidden file in skill backup: {source_path.name}/{relative_path.as_posix()}")


def _copy_skill_dir(source_path: Path, target_path: Path) -> None:
    if target_path.exists() and not target_path.is_dir():
        raise SkillsSyncError(f"target skill path exists but is not a directory: {target_path}")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{target_path.name}.tmp-", dir=target_path.parent) as tmp_dir:
        tmp_path = Path(tmp_dir) / target_path.name
        shutil.copytree(source_path, tmp_path, copy_function=shutil.copy2)
        if target_path.exists():
            shutil.rmtree(target_path)
        shutil.move(str(tmp_path), str(target_path))


def sync_claude_global(
    *,
    dry_run: bool = False,
    root: Path | None = None,
    target_root: Path | None = None,
    skill_names: tuple[str, ...] = MANAGED_SKILLS,
) -> SkillSyncResult:
    """Sync parked backup skills into the user's global Claude skills directory."""

    repo_root = (root or source_root()).resolve()
    source = skills_backup_root(repo_root).resolve()
    target = (target_root or claude_global_skills_root()).expanduser().resolve()

    changes: list[SkillSyncChange] = []
    for name in skill_names:
        source_path = source / name
        target_path = target / name
        _validate_skill_source(source_path)

        if not target_path.exists():
            action = "add"
        elif not target_path.is_dir():
            raise SkillsSyncError(f"target skill path exists but is not a directory: {target_path}")
        elif _dircmp_equal(source_path, target_path):
            action = "unchanged"
        else:
            action = "update"

        change = SkillSyncChange(name=name, action=action, source_path=source_path, target_path=target_path)
        changes.append(change)

    if not dry_run:
        for change in changes:
            if change.action != "unchanged":
                _copy_skill_dir(change.source_path, change.target_path)

    return SkillSyncResult(source_root=source, target_root=target, dry_run=dry_run, changes=tuple(changes))


def format_sync_result(result: SkillSyncResult) -> str:
    mode = "would sync" if result.dry_run else "synced"
    lines = [
        "Temporary compatibility command.",
        f"{mode} skills-backup/* -> {result.target_root}",
        "This is not target repo deployment and does not update target manifests.",
    ]
    for change in result.changes:
        verb = f"would {change.action}" if result.dry_run and change.action != "unchanged" else change.action
        lines.append(f"{verb} {change.name}")
    return "\n".join(lines)
