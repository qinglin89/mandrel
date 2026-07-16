"""Canonical payload deployment and status checks."""

from __future__ import annotations

import os
import re
import stat
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import hashing, lockfile, manifest, registry
from .paths import GITIGNORE_BEGIN, GITIGNORE_END, canonical_root, registry_path, source_root

PAYLOADS: tuple[tuple[str, str], ...] = (
    ("repo-root", ""),
    ("protocols", ".ai-protocol/protocols"),
    ("workflow", ".ai-protocol/workflow"),
    ("meta", ".ai-protocol/meta"),
    ("cursor", ".cursor"),
    ("codex", ".codex"),
    ("claude", ".claude"),
    ("orchestrator", ".cursor/orchestrator"),
)

# /ai-coding*.md stays during the layout transition: targets deployed before
# the .ai-protocol/ cut still carry the legacy files untracked; drop the line
# once every target has had its legacy docs removed after the deploy wave.
GITIGNORE_BLOCK = f"""{GITIGNORE_BEGIN}
/.ai-deploy-manifest.json
/.ai-protocol/
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

CLAUDE_MD_TARGET = "CLAUDE.md"
NORMALIZED_SHA256_FIELD = "normalized_sha256"
CLAUDE_MD_MEMORY_IMPORT_TOPICS = frozenset(
    {
        "overview",
        "architecture",
        "design",
        "conventions",
    }
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


@dataclass(frozen=True)
class DeployPreviewChange:
    action: str
    target_relative_path: str
    detail: str = ""


@dataclass(frozen=True)
class DeployPreviewResult:
    target_root: Path
    total_files: int
    changes: tuple[DeployPreviewChange, ...]
    gitignore_action: str

    @property
    def changed(self) -> tuple[DeployPreviewChange, ...]:
        return tuple(change for change in self.changes if change.action != "unchanged")


@dataclass(frozen=True)
class OrchestratorBootstrapResult:
    target_root: Path
    orchestrator_root: Path
    venv_path: Path
    python_path: Path
    requirements_path: Path
    env_path: Path
    env_created: bool
    python_executable: str


def _posix(path: Path | PurePosixPath | str) -> str:
    return str(path).replace(os.sep, "/")


def _venv_python_path(venv_path: Path) -> Path:
    if os.name == "nt":
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"


def _normalize_claude_md_memory_imports(data: bytes) -> bytes:
    """Treat the eager `@.ai/<topic>.md` imports in the loader (CLAUDE.md)
    and their directory-form `@.ai/<topic>/index.md` upgrades as the same
    content for status purposes. Applies to the four upgradeable memory
    topics only, anywhere in the file — the loader is fully deploy-owned."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data

    lines = text.splitlines(keepends=True)
    normalized_lines: list[str] = []

    for line in lines:
        body = line.rstrip("\r\n")
        newline = line[len(body) :]
        stripped = body.rstrip(" \t")
        trailing = body[len(stripped) :]
        prefix = "@.ai/"
        suffix = "/index.md"

        if stripped.startswith(prefix) and stripped.endswith(suffix):
            topic = stripped[len(prefix) : -len(suffix)]
            if topic in CLAUDE_MD_MEMORY_IMPORT_TOPICS:
                body = f"{prefix}{topic}.md{trailing}"

        normalized_lines.append(body + newline)

    return "".join(normalized_lines).encode("utf-8")


def _status_bytes(item: DeploymentItem, data: bytes) -> bytes:
    if item.target_relative_path == CLAUDE_MD_TARGET:
        return _normalize_claude_md_memory_imports(data)
    return data


def _status_sha256(item: DeploymentItem, data: bytes) -> str:
    return hashing.sha256_bytes(_status_bytes(item, data))


def _manifest_status_hash(record: dict[object, object], manifest_hash: str) -> str:
    normalized_hash = record.get(NORMALIZED_SHA256_FIELD)
    if isinstance(normalized_hash, str):
        return normalized_hash
    return manifest_hash


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


def planned_gitignore_action(target_root: Path) -> str:
    path = target_root / ".gitignore"
    if not path.exists():
        return "add"
    existing = path.read_text(encoding="utf-8")
    if GITIGNORE_BEGIN in existing and GITIGNORE_END in existing:
        updated = GITIGNORE_BLOCK_PATTERN.sub(GITIGNORE_BLOCK, existing)
        return "unchanged" if updated == existing else "update"
    return "update"


def preview_deploy(target: str | Path, *, root: Path | None = None) -> DeployPreviewResult:
    root = (root or source_root()).resolve()
    target_root = Path(target).expanduser().resolve()
    if not target_root.is_dir():
        raise FileNotFoundError(f"target repo does not exist: {target_root}")

    changes: list[DeployPreviewChange] = []
    for item in iter_deployment_items(root):
        target_path = target_root / item.target_relative_path
        rendered_bytes = item.bytes_for_target(target_root)
        detail = ""

        if not target_path.exists():
            action = "add"
        elif not target_path.is_file():
            action = "blocked"
            detail = "target path exists but is not a file"
        else:
            content_changed = target_path.read_bytes() != rendered_bytes
            mode_changed = stat.S_IMODE(target_path.stat().st_mode) != item.mode
            if content_changed or mode_changed:
                action = "update"
                if content_changed and mode_changed:
                    detail = "content and mode differ"
                elif mode_changed:
                    detail = "mode differs"
            else:
                action = "unchanged"

        changes.append(
            DeployPreviewChange(
                action=action,
                target_relative_path=item.target_relative_path,
                detail=detail,
            )
        )

    return DeployPreviewResult(
        target_root=target_root,
        total_files=len(changes),
        changes=tuple(changes),
        gitignore_action=planned_gitignore_action(target_root),
    )


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
        manifest_record = {
            "canonical_relative_path": item.canonical_relative_path,
            "target_relative_path": item.target_relative_path,
            "sha256": file_info["sha256"],
            "size_bytes": file_info["size_bytes"],
        }
        if item.target_relative_path == CLAUDE_MD_TARGET:
            manifest_record[NORMALIZED_SHA256_FIELD] = _status_sha256(item, rendered_bytes)
        manifest_records.append(manifest_record)
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


def bootstrap_orchestrator(
    target: str | Path,
    *,
    python_executable: str = "python3.14",
) -> OrchestratorBootstrapResult:
    target_root = Path(target).expanduser().resolve()
    if not target_root.is_dir():
        raise FileNotFoundError(f"target repo does not exist: {target_root}")

    orchestrator_root = target_root / ".cursor" / "orchestrator"
    if not orchestrator_root.is_dir():
        raise FileNotFoundError(f"orchestrator payload is missing: {orchestrator_root}")

    requirements_path = orchestrator_root / "requirements.txt"
    if not requirements_path.is_file():
        raise FileNotFoundError(f"orchestrator requirements are missing: {requirements_path}")

    venv_path = orchestrator_root / ".venv"
    if venv_path.exists() and not venv_path.is_dir():
        raise FileExistsError(f"orchestrator venv path exists but is not a directory: {venv_path}")

    subprocess.run([python_executable, "-m", "venv", str(venv_path)], cwd=target_root, check=True)

    python_path = _venv_python_path(venv_path)
    if not python_path.is_file():
        raise FileNotFoundError(f"orchestrator venv python was not created: {python_path}")

    subprocess.run([str(python_path), "-m", "pip", "install", "-U", "pip"], cwd=target_root, check=True)
    subprocess.run(
        [str(python_path), "-m", "pip", "install", "-r", str(requirements_path)],
        cwd=target_root,
        check=True,
    )

    env_example_path = orchestrator_root / ".env.example"
    env_path = orchestrator_root / ".env"
    env_created = False
    if env_example_path.is_file() and not env_path.exists():
        shutil.copy2(env_example_path, env_path)
        env_created = True

    return OrchestratorBootstrapResult(
        target_root=target_root,
        orchestrator_root=orchestrator_root,
        venv_path=venv_path,
        python_path=python_path,
        requirements_path=requirements_path,
        env_path=env_path,
        env_created=env_created,
        python_executable=python_executable,
    )


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
        manifest_status_hash = _manifest_status_hash(raw_record, manifest_hash)

        item = current_items.get(target_rel)
        if item is None:
            canonical_rel = raw_record.get("canonical_relative_path", "unknown canonical path")
            drifts.append(Drift("extra deployed file", target_rel, f"tracked from {canonical_rel} but absent from canonical payload"))
            continue

        target_path = target_root / target_rel
        canonical_bytes = item.bytes_for_target(target_root)
        canonical_hash = hashing.sha256_bytes(canonical_bytes)
        if not target_path.is_file():
            drifts.append(Drift("missing target file", target_rel))
        else:
            target_bytes = target_path.read_bytes()
            target_hash = hashing.sha256_bytes(target_bytes)
            target_status_hash = _status_sha256(item, target_bytes)
            if target_hash != manifest_hash and target_status_hash != manifest_status_hash:
                drifts.append(Drift("target modified", target_rel))

        canonical_status_hash = _status_sha256(item, canonical_bytes)
        if canonical_hash != manifest_hash and canonical_status_hash != manifest_status_hash:
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


def format_deploy_preview(result: DeployPreviewResult) -> str:
    counts = {
        "add": 0,
        "update": 0,
        "unchanged": 0,
        "blocked": 0,
    }
    for change in result.changes:
        counts[change.action] = counts.get(change.action, 0) + 1

    lines = [
        f"{result.target_root}: dry-run deploy preview ({result.total_files} files)",
        f"  add: {counts['add']}, update: {counts['update']}, unchanged: {counts['unchanged']}, blocked: {counts['blocked']}",
        f"  gitignore: {result.gitignore_action}",
        "  manifest: would write .ai-deploy-manifest.json",
        "  lockfile: would write .ai-deploy-lock.json",
        "  registry: would add/update local registry entry",
    ]

    for action in ("blocked", "add", "update"):
        entries = [change for change in result.changes if change.action == action]
        if not entries:
            continue
        lines.append(f"  {action}:")
        for change in entries:
            detail = f" ({change.detail})" if change.detail else ""
            lines.append(f"    - {change.target_relative_path}{detail}")
    return "\n".join(lines)
