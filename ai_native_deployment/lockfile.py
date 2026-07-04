"""Portable deploy lockfile creation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .hashing import sha256_bytes
from .manifest import SCHEMA_VERSION, source_git_commit
from .paths import lock_path


def build_lock(
    *,
    source_root: Path,
    files: Iterable[dict[str, int | str]],
    source_commit: str | None = None,
) -> dict[str, object]:
    deployed_files = [
        {
            "canonical_relative_path": str(record["canonical_relative_path"]),
            "target_relative_path": str(record["target_relative_path"]),
            "canonical_sha256": str(record["canonical_sha256"]),
            "canonical_size_bytes": int(record["canonical_size_bytes"]),
        }
        for record in sorted(files, key=lambda item: str(item["target_relative_path"]))
    ]
    payload = "\n".join(
        f"{item['target_relative_path']}\0{item['canonical_sha256']}" for item in deployed_files
    ).encode("utf-8")
    return {
        "schema_version": SCHEMA_VERSION,
        "source_repo": "ai-native-deployment",
        "source_git_commit": source_git_commit(source_root) if source_commit is None else source_commit,
        "canonical_payload_sha256": sha256_bytes(payload),
        "deployed_files": deployed_files,
    }


def write_lock(target_root: Path, lock: dict[str, object]) -> Path:
    path = lock_path(target_root)
    path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_lock(target_root: Path) -> dict[str, object]:
    return json.loads(lock_path(target_root).read_text(encoding="utf-8"))
