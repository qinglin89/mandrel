"""Hash helpers used by manifests and drift checks."""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(path: Path) -> dict[str, int | str]:
    stat = path.stat()
    return {"sha256": sha256_file(path), "size_bytes": stat.st_size}
