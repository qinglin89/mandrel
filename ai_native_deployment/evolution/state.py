"""Machine-local evolution runtime state under `.ai-evolution/`.

Everything here is ignored by Git by design (contract invariant 11): the
discovery cursor, the pending pool, and the raw imported bundles are runtime
data, not repository content.

Four things are kept deliberately separate, because they answer four different
questions and collapsing them loses the answer to one of them:

| Field      | Answers                                        |
|------------|------------------------------------------------|
| `cursor`   | how far the feed has been *inspected*          |
| `pending`  | which unique tasks are *eligible and unbatched* |
| `rejected` | which reports were seen and refused, and why   |
| `processed`| which reports a frozen batch already claimed   |

Malformed state is never repaired silently. A reset cursor re-imports; a
dropped pool loses evidence that the feed may no longer serve.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping

from ..hashing import sha256_bytes
from ..manifest import utc_timestamp
from .config import EvolutionConfig
from .errors import LockError, StateError
from .reports import NormalizedReport, canonical_json

STATE_SCHEMA_VERSION = 1

REPORT_JSON_FILENAME = "report.json"
ARTIFACTS_SUBDIR = "artifacts"

_SAFE_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
_MAX_SLUG = 64


@dataclass(frozen=True)
class ReportRef:
    """One report's identity inside a pool entry."""

    report_key: str
    sequence: int
    evaluation_id: str
    generated_at: str
    bundle_sha256: str
    artifacts_path: str

    def to_json(self) -> dict[str, Any]:
        return {
            "report_key": self.report_key,
            "sequence": self.sequence,
            "evaluation_id": self.evaluation_id,
            "generated_at": self.generated_at,
            "bundle_sha256": self.bundle_sha256,
            "artifacts_path": self.artifacts_path,
        }

    @classmethod
    def from_json(cls, data: Any, where: str) -> "ReportRef":
        if not isinstance(data, dict):
            raise StateError(f"{where}: report reference must be an object")
        return cls(
            report_key=_require_str(data, "report_key", where),
            sequence=_require_int(data, "sequence", where),
            evaluation_id=_require_str(data, "evaluation_id", where),
            generated_at=_require_str(data, "generated_at", where),
            bundle_sha256=_require_str(data, "bundle_sha256", where),
            artifacts_path=_require_str(data, "artifacts_path", where),
        )


@dataclass
class PoolEntry:
    """One unique completed task awaiting a batch.

    A batch counts tasks, not reports (invariant 1), but an evaluator rerun is
    still provenance worth keeping (invariant 4) — so reruns stay attached to
    the entry instead of being dropped or counted. `primary` is the
    highest-sequence report seen for the task: the most recent evaluation.
    """

    repo_id: str
    task_id: str
    primary: ReportRef
    first_imported_at: str
    reruns: list[ReportRef] = field(default_factory=list)

    @property
    def dedup_key(self) -> tuple[str, str]:
        return (self.repo_id, self.task_id)

    def report_keys(self) -> set[str]:
        return {self.primary.report_key} | {ref.report_key for ref in self.reruns}

    def to_json(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "task_id": self.task_id,
            "first_imported_at": self.first_imported_at,
            "primary": self.primary.to_json(),
            "reruns": [ref.to_json() for ref in sorted(self.reruns, key=lambda ref: ref.sequence)],
        }

    @classmethod
    def from_json(cls, data: Any, where: str) -> "PoolEntry":
        if not isinstance(data, dict):
            raise StateError(f"{where}: pool entry must be an object")
        reruns = data.get("reruns", [])
        if not isinstance(reruns, list):
            raise StateError(f"{where}: reruns must be a list")
        return cls(
            repo_id=_require_str(data, "repo_id", where),
            task_id=_require_str(data, "task_id", where),
            primary=ReportRef.from_json(data.get("primary"), f"{where}.primary"),
            first_imported_at=_require_str(data, "first_imported_at", where),
            reruns=[ReportRef.from_json(item, f"{where}.reruns[{i}]") for i, item in enumerate(reruns)],
        )


@dataclass
class EvolutionState:
    cursor: str | None = None
    pending: list[PoolEntry] = field(default_factory=list)
    rejected: dict[str, dict[str, Any]] = field(default_factory=dict)
    processed: dict[str, dict[str, Any]] = field(default_factory=dict)

    def find(self, repo_id: str, task_id: str) -> PoolEntry | None:
        for entry in self.pending:
            if entry.dedup_key == (repo_id, task_id):
                return entry
        return None

    def known_report_keys(self) -> set[str]:
        """Every report this repository has already decided about. The importer
        checks this before fetching, which is what makes a re-run of `sync`
        over an unchanged feed a no-op."""

        keys: set[str] = set(self.rejected) | set(self.processed)
        for entry in self.pending:
            keys |= entry.report_keys()
        return keys

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "cursor": self.cursor,
            "pending": [
                entry.to_json()
                for entry in sorted(self.pending, key=lambda entry: (entry.primary.sequence, entry.repo_id, entry.task_id))
            ],
            "rejected": self.rejected,
            "processed": self.processed,
        }

    @classmethod
    def from_json(cls, data: Any, path: Path) -> "EvolutionState":
        if not isinstance(data, dict):
            raise StateError(f"{path}: state must be a JSON object")
        version = data.get("schema_version")
        if version != STATE_SCHEMA_VERSION:
            raise StateError(
                f"{path}: unsupported state schema_version {version!r}; this build supports {STATE_SCHEMA_VERSION}"
            )
        cursor = data.get("cursor")
        if cursor is not None and not isinstance(cursor, str):
            raise StateError(f"{path}: cursor must be a string or null")
        pending = data.get("pending", [])
        if not isinstance(pending, list):
            raise StateError(f"{path}: pending must be a list")
        entries = [PoolEntry.from_json(item, f"{path}: pending[{i}]") for i, item in enumerate(pending)]
        seen: set[tuple[str, str]] = set()
        for entry in entries:
            if entry.dedup_key in seen:
                raise StateError(f"{path}: pending holds {entry.repo_id}/{entry.task_id} twice")
            seen.add(entry.dedup_key)
        return cls(
            cursor=cursor,
            pending=entries,
            rejected=_require_map(data, "rejected", path),
            processed=_require_map(data, "processed", path),
        )


def load_state(config: EvolutionConfig) -> EvolutionState:
    """Read the state file. A missing file is an empty pool, which is the
    correct reading of a repository that has never synced; anything else that
    cannot be parsed is an error."""

    path = config.state_path
    if not path.is_file():
        return EvolutionState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(f"unreadable evolution state {path}: {exc}") from exc
    return EvolutionState.from_json(data, path)


def save_state(config: EvolutionConfig, state: EvolutionState) -> Path:
    """Replace the state file atomically. A reader sees the state before or
    after this call, never a truncated file."""

    path = config.state_path
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, json.dumps(state.to_json(), indent=2, sort_keys=True) + "\n")
    return path


@contextmanager
def single_writer_lock(config: EvolutionConfig) -> Iterator[Path]:
    """Guard every mutating run.

    A stale lock is reported, never broken: this process cannot tell a crashed
    holder from a slow one, and breaking the lock on the wrong guess is how two
    importers end up interleaving writes to the same pool.
    """

    path = config.lock_path
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"pid": os.getpid(), "host": socket.gethostname(), "acquired_at": utc_timestamp()},
        sort_keys=True,
    )
    try:
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError as exc:
        raise LockError(f"evolution lock held: {path} ({_lock_holder(path)}); remove it if no run is active") from exc
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload + "\n")
        yield path
    finally:
        path.unlink(missing_ok=True)


def artifacts_dir_name(report_key: str) -> str:
    """Directory name for one report's bundle.

    The key comes from another system, so it is never used as a path component
    directly. The readable prefix is for humans; the hash suffix is what makes
    the name unique and stable.
    """

    slug = "".join(char if char in _SAFE_NAME_CHARS else "-" for char in report_key)
    slug = slug.strip("-.")[:_MAX_SLUG] or "report"
    return f"{slug}-{sha256_bytes(report_key.encode('utf-8'))[:16]}"


def stage_artifacts(config: EvolutionConfig, report: NormalizedReport, blobs: Mapping[str, bytes]) -> str:
    """Write one report's validated record and bodies under the runtime root.

    Staged through a temporary directory and moved into place, so an
    interrupted import leaves either the previous directory or the new one —
    never a half-written bundle that a later run would trust.

    Returns the repo-relative path recorded in state.
    """

    root = config.artifacts_root
    root.mkdir(parents=True, exist_ok=True)
    name = artifacts_dir_name(report.report_key)
    final = root / name
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=root))
    try:
        (staging / REPORT_JSON_FILENAME).write_bytes(canonical_json(report.record) + b"\n")
        bodies = staging / ARTIFACTS_SUBDIR
        bodies.mkdir()
        for artifact in report.artifacts:
            (bodies / artifact.name).write_bytes(blobs[artifact.name])
        if final.exists():
            shutil.rmtree(final)
        os.replace(staging, final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return config.artifacts_relative(name)


def _atomic_write(path: Path, text: str) -> None:
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _lock_holder(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "holder unknown"
    if not isinstance(data, dict):
        return "holder unknown"
    return f"pid {data.get('pid')} on {data.get('host')} since {data.get('acquired_at')}"


def _require_str(data: Mapping[str, Any], key: str, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise StateError(f"{where}: {key} must be a non-empty string")
    return value


def _require_int(data: Mapping[str, Any], key: str, where: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise StateError(f"{where}: {key} must be an integer")
    return value


def _require_map(data: Mapping[str, Any], key: str, path: Path) -> dict[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise StateError(f"{path}: {key} must be an object")
    return value
