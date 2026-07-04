"""Canonical payload deployment and status checks."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import hashing, lockfile, manifest, registry
from .paths import GITIGNORE_BEGIN, GITIGNORE_END, canonical_root, registry_path, source_root

PAYLOADS: tuple[tuple[str, str], ...] = (
    ("repo-root", ""),
    ("cursor", ".cursor"),
    ("codex", ".codex"),
    ("claude", ".claude"),
    ("orchestrator", ".cursor/orchestrator"),
)

GITIGNORE_BLOCK = f"""{GITIGNORE_BEGIN}
/.ai-deploy-manifest.json
/.cursor/
/.codex/
/.claude/
/CLAUDE.md
/ai-coding*.md
/.ai-tasks/
__pycache__/
*.py[cod]
!/.ai-deploy-lock.json
{GITIGNORE_END}
"""

GITIGNORE_BLOCK_PATTERN = re.compile(
    rf"{re.escape(GITIGNORE_BEGIN)}.*?{re.escape(GITIGNORE_END)}\n?",
    re.DOTALL,
)


@dataclass(frozen=True)
class DeploymentItem:
    canonical_relative_path: str
    target_relative_path: str
    source_path: Path
    mode: int
    render_template: bool = False

    def bytes_for_target(self, target_root: Path) -> bytes:
        if not self.render_template:
            return self.source_path.read_bytes()
        text = self.source_path.read_text(encoding="utf-8")
        return text.replace("{{REPO_ROOT}}", str(target_root)).encode("utf-8")


@dataclass(frozen=True)
class Drift:
    kind: str
    target_relative_path: str
    detail: str = ""


@dataclass(frozen=True)
class StatusResult:
    target_root: Path
    total_files: int
    drifts: tuple[Drift, ...]

    @property
    def in_sync(self) -> bool:
        return not self.drifts


def _posix(path: Path | PurePosixPath | str) -> str:
    return str(path).replace(os.sep, "/")


def is_forbidden_relative_path(path: Path | PurePosixPath | str) -> bool:
    parts = PurePosixPath(_posix(path)).parts
    if not parts:
        return False

    if any(part in {".git", ".venv", "__pycache__", "logs", ".logs"} for part in parts):
        return True

    filename = parts[-1]
    if filename in {"sessions.json", "settings.local.json"}:
        return True
    if filename == ".env":
        return True
    if filename.startswith(".env.") and filename != ".env.example":
        return True
    if filename.endswith((".pyc", ".pyo")):
        return True

    for index, part in enumerate(parts[:-1]):
        if part in {"claude", ".claude"} and parts[index + 1] == "projects":
            return True
    return False


def iter_deployment_items(root: Path | None = None) -> list[DeploymentItem]:
    root = (root or source_root()).resolve()
    canonical = canonical_root(root)
    items: list[DeploymentItem] = []

    for bucket, target_prefix in PAYLOADS:
        bucket_root = canonical / bucket
        if not bucket_root.exists():
            continue
        for source_path in sorted(bucket_root.rglob("*")):
            if not source_path.is_file():
                continue
            bucket_rel = source_path.relative_to(bucket_root)
            bucket_rel_posix = bucket_rel.as_posix()

            render_template = False
            if bucket == "codex" and bucket_rel_posix == "config.toml.template":
                target_rel = PurePosixPath(".codex/config.toml")
                render_template = True
            elif bucket == "codex" and bucket_rel_posix == "config.toml":
                continue
            elif target_prefix:
                target_rel = PurePosixPath(target_prefix) / PurePosixPath(bucket_rel_posix)
            else:
                target_rel = PurePosixPath(bucket_rel_posix)

            canonical_rel = source_path.relative_to(root).as_posix()
            if is_forbidden_relative_path(canonical_rel) or is_forbidden_relative_path(target_rel):
                continue
            items.append(
                DeploymentItem(
                    canonical_relative_path=canonical_rel,
                    target_relative_path=target_rel.as_posix(),
                    source_path=source_path,
                    mode=stat.S_IMODE(source_path.stat().st_mode),
                    render_template=render_template,
                )
            )
    return items


def append_gitignore_block(target_root: Path) -> Path:
    path = target_root / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if GITIGNORE_BEGIN in existing and GITIGNORE_END in existing:
        updated = GITIGNORE_BLOCK_PATTERN.sub(GITIGNORE_BLOCK, existing)
        if updated != existing:
            path.write_text(updated, encoding="utf-8")
        return path

    separator = "" if not existing or existing.endswith("\n") else "\n"
    if existing and not existing.endswith("\n\n"):
        separator += "\n"
    path.write_text(existing + separator + GITIGNORE_BLOCK, encoding="utf-8")
    return path


def deploy_canonical(
    target: str | Path,
    *,
    root: Path | None = None,
    registry_file: Path | None = None,
) -> dict[str, object]:
    root = (root or source_root()).resolve()
    target_root = Path(target).expanduser().resolve()
    if not target_root.is_dir():
        raise FileNotFoundError(f"target repo does not exist: {target_root}")

    manifest_records: list[dict[str, int | str]] = []
    lock_records: list[dict[str, int | str]] = []
    for item in iter_deployment_items(root):
        target_path = target_root / item.target_relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        rendered_bytes = item.bytes_for_target(target_root)
        target_path.write_bytes(rendered_bytes)
        target_path.chmod(item.mode)
        file_info = hashing.file_record(target_path)
        manifest_records.append(
            {
                "canonical_relative_path": item.canonical_relative_path,
                "target_relative_path": item.target_relative_path,
                "sha256": file_info["sha256"],
                "size_bytes": file_info["size_bytes"],
            }
        )
        source_stat = item.source_path.stat()
        lock_records.append(
            {
                "canonical_relative_path": item.canonical_relative_path,
                "target_relative_path": item.target_relative_path,
                "canonical_sha256": hashing.sha256_file(item.source_path),
                "canonical_size_bytes": source_stat.st_size,
            }
        )

    append_gitignore_block(target_root)
    deployed_manifest = manifest.build_manifest(source_root=root, target_root=target_root, files=manifest_records)
    manifest.write_manifest(target_root, deployed_manifest)
    deploy_lock = lockfile.build_lock(source_root=root, files=lock_records)
    lockfile.write_lock(target_root, deploy_lock)
    registry.add_repo(
        target_root,
        registry_file=registry_file or registry_path(root),
        source_root=root,
        require_manifest=True,
    )
    return deployed_manifest


def check_status(target: str | Path, *, root: Path | None = None) -> StatusResult:
    root = (root or source_root()).resolve()
    target_root = Path(target).expanduser().resolve()
    try:
        deployed_manifest = manifest.read_manifest(target_root)
    except manifest.ManifestError as exc:
        return StatusResult(target_root=target_root, total_files=0, drifts=(Drift("missing manifest", ".ai-deploy-manifest.json", str(exc)),))

    manifest_files = deployed_manifest["files"]
    assert isinstance(manifest_files, dict)
    current_items = {item.target_relative_path: item for item in iter_deployment_items(root)}
    drifts: list[Drift] = []

    for target_rel, raw_record in sorted(manifest_files.items()):
        if not isinstance(raw_record, dict):
            drifts.append(Drift("invalid manifest entry", str(target_rel), "record is not an object"))
            continue
        target_rel = str(raw_record.get("target_relative_path") or target_rel)
        manifest_hash = raw_record.get("sha256")
        if not isinstance(manifest_hash, str):
            drifts.append(Drift("invalid manifest entry", target_rel, "missing sha256"))
            continue

        item = current_items.get(target_rel)
        if item is None:
            canonical_rel = raw_record.get("canonical_relative_path", "unknown canonical path")
            drifts.append(Drift("extra deployed file", target_rel, f"tracked from {canonical_rel} but absent from canonical payload"))
            continue

        target_path = target_root / target_rel
        if not target_path.is_file():
            drifts.append(Drift("missing target file", target_rel))
        else:
            target_hash = hashing.sha256_file(target_path)
            if target_hash != manifest_hash:
                drifts.append(Drift("target modified", target_rel))

        canonical_hash = hashing.sha256_bytes(item.bytes_for_target(target_root))
        if canonical_hash != manifest_hash:
            drifts.append(Drift("canonical changed", target_rel))

    for target_rel in sorted(set(current_items) - {str(key) for key in manifest_files}):
        drifts.append(Drift("canonical changed", target_rel, "new canonical file not deployed"))

    return StatusResult(target_root=target_root, total_files=len(manifest_files), drifts=tuple(drifts))


def format_status(result: StatusResult, label: str | None = None) -> str:
    prefix = f"{label}: " if label else ""
    if result.in_sync:
        return f"{prefix}in sync ({result.total_files} files)"

    lines = [f"{prefix}drift detected ({len(result.drifts)} issues)"]
    order = (
        "missing manifest",
        "target modified",
        "canonical changed",
        "missing target file",
        "extra deployed file",
        "invalid manifest entry",
    )
    for kind in order:
        entries = [drift for drift in result.drifts if drift.kind == kind]
        if not entries:
            continue
        lines.append(f"  {kind}:")
        for drift in entries:
            detail = f" ({drift.detail})" if drift.detail else ""
            lines.append(f"    - {drift.target_relative_path}{detail}")
    return "\n".join(lines)
