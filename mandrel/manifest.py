"""Manifest creation and persistence."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .paths import manifest_path

SCHEMA_VERSION = 1


class ManifestError(RuntimeError):
    """Raised when a target manifest cannot be read or used."""


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def source_git_commit(source_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    commit = result.stdout.strip()
    return commit or None


def build_manifest(
    *,
    source_root: Path,
    target_root: Path,
    files: Iterable[dict[str, int | str]],
    deployed_at: str | None = None,
    source_commit: str | None = None,
) -> dict[str, object]:
    file_map = {
        str(record["target_relative_path"]): record
        for record in sorted(files, key=lambda item: str(item["target_relative_path"]))
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "source_repo_path": str(source_root),
        "source_git_commit": source_git_commit(source_root) if source_commit is None else source_commit,
        "deployed_at": deployed_at or utc_timestamp(),
        "target_repo_path": str(target_root),
        "files": file_map,
    }


def write_manifest(target_root: Path, manifest: dict[str, object]) -> Path:
    path = manifest_path(target_root)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_manifest(target_root: Path) -> dict[str, object]:
    path = manifest_path(target_root)
    if not path.is_file():
        raise ManifestError(f"missing manifest: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"invalid manifest JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ManifestError(f"invalid manifest shape: {path}")
    files = data.get("files")
    if not isinstance(files, dict):
        raise ManifestError(f"manifest has no files map: {path}")
    return data
