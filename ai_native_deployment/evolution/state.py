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

Malformed state is never repaired silently, and an incomplete file is
malformed: a missing field is damage, not a default. A reset cursor re-imports;
a dropped pool loses evidence that the feed may no longer serve.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping

from ..hashing import sha256_bytes
from ..manifest import utc_timestamp
from .config import EvolutionConfig, batch_id_number
from .errors import LockError, StateError
from .reports import REJECTION_REASONS, NormalizedReport, canonical_json
from .schema import is_rfc3339_date_time

STATE_SCHEMA_VERSION = 1

# The complete version-1 shape. Both directions use it: `to_json` writes these
# fields, `from_json` requires exactly them.
_STATE_FIELDS = ("cursor", "pending", "rejected", "processed")
_STATE_KEYS = frozenset(_STATE_FIELDS) | {"schema_version"}
_POOL_ENTRY_FIELDS = frozenset({"repo_id", "task_id", "first_imported_at", "primary", "reruns"})
_REPORT_REF_FIELDS = frozenset(
    {"report_key", "sequence", "evaluation_id", "generated_at", "bundle_sha256", "artifacts_path"}
)
# Exactly what `importer._record_rejection` writes.
_REJECTION_FIELDS = frozenset({"reason", "detail", "recorded_at"})
# Exactly what `batches._claim_reports` writes.
_PROCESSED_FIELDS = frozenset({"batch_id", "recorded_at"})

_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z", re.ASCII)

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
    def from_json(cls, data: Any, where: str, *, artifacts_root: str) -> "ReportRef":
        """Rebuild one reference, checking what it means and not merely its
        Python types: a sequence of 0, an unparseable timestamp, or a digest
        that is not a digest is corruption, and corruption that loads is
        corruption the controller will act on."""

        if not isinstance(data, dict):
            raise StateError(f"{where}: report reference must be an object")
        unexpected = set(data) - _REPORT_REF_FIELDS
        if unexpected:
            raise StateError(f"{where}: unexpected fields {sorted(unexpected)}")
        return cls(
            report_key=_require_str(data, "report_key", where),
            # The import schema pins `sequence` to minimum 1, so 0 or negative
            # here is state that no import path could have written.
            sequence=_require_int(data, "sequence", where, minimum=1),
            evaluation_id=_require_str(data, "evaluation_id", where),
            generated_at=_require_timestamp(data, "generated_at", where),
            bundle_sha256=_require_digest(data, "bundle_sha256", where),
            artifacts_path=_require_artifacts_path(data, "artifacts_path", where, artifacts_root=artifacts_root),
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
    def from_json(cls, data: Any, where: str, *, artifacts_root: str) -> "PoolEntry":
        if not isinstance(data, dict):
            raise StateError(f"{where}: pool entry must be an object")
        unexpected = set(data) - _POOL_ENTRY_FIELDS
        if unexpected:
            raise StateError(f"{where}: unexpected fields {sorted(unexpected)}")
        reruns = data.get("reruns")
        if not isinstance(reruns, list):
            raise StateError(f"{where}: reruns must be a list")
        entry = cls(
            repo_id=_require_str(data, "repo_id", where),
            task_id=_require_str(data, "task_id", where),
            primary=ReportRef.from_json(data.get("primary"), f"{where}.primary", artifacts_root=artifacts_root),
            first_imported_at=_require_timestamp(data, "first_imported_at", where),
            reruns=[
                ReportRef.from_json(item, f"{where}.reruns[{i}]", artifacts_root=artifacts_root)
                for i, item in enumerate(reruns)
            ],
        )

        keys = [entry.primary.report_key] + [ref.report_key for ref in entry.reruns]
        if len(set(keys)) != len(keys):
            raise StateError(f"{where}: the same report appears twice on this entry")
        # `primary` is defined as the latest evaluation seen for the task; an
        # entry whose rerun outranks it would report a superseded evaluation as
        # current for every batch decision made from this pool.
        for ref in entry.reruns:
            if ref.sequence > entry.primary.sequence:
                raise StateError(
                    f"{where}: rerun {ref.report_key} has sequence {ref.sequence}, "
                    f"above primary {entry.primary.report_key} at {entry.primary.sequence}"
                )
        return entry


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
    def from_json(cls, data: Any, path: Path, *, artifacts_root: str) -> "EvolutionState":
        """Load a complete version-1 state, or refuse.

        Every field is required, absent included. A file that has lost its
        `cursor` or its `pending` array is a file that was damaged, and reading
        the gap as "nothing yet" is exactly the silent reset this module exists
        to prevent: the cursor rewinds, the pool empties, and the run looks
        successful.

        `artifacts_root` is the repository-relative bundle root from the config
        governing this run; staged references are checked against it, so state
        cannot point later evidence reads anywhere else in the tree.
        """

        if not isinstance(data, dict):
            raise StateError(f"{path}: state must be a JSON object")
        version = data.get("schema_version")
        if version != STATE_SCHEMA_VERSION:
            raise StateError(
                f"{path}: unsupported state schema_version {version!r}; this build supports {STATE_SCHEMA_VERSION}"
            )
        unexpected = set(data) - _STATE_KEYS
        if unexpected:
            raise StateError(f"{path}: unexpected state fields {sorted(unexpected)}")
        for name in _STATE_FIELDS:
            if name not in data:
                raise StateError(
                    f"{path}: incomplete state, {name!r} is missing; version {STATE_SCHEMA_VERSION} requires it"
                )

        cursor = data["cursor"]
        if cursor is not None and not isinstance(cursor, str):
            raise StateError(f"{path}: cursor must be a string or null")
        pending = data["pending"]
        if not isinstance(pending, list):
            raise StateError(f"{path}: pending must be a list")
        entries = [
            PoolEntry.from_json(item, f"{path}: pending[{i}]", artifacts_root=artifacts_root)
            for i, item in enumerate(pending)
        ]
        seen: set[tuple[str, str]] = set()
        for entry in entries:
            if entry.dedup_key in seen:
                raise StateError(f"{path}: pending holds {entry.repo_id}/{entry.task_id} twice")
            seen.add(entry.dedup_key)

        rejected = _require_rejections(data, path)
        processed = _require_processed(data, path)
        # One report has one decision. The same key in two of these means the
        # state contradicts itself about what was already done with it, and
        # `known_report_keys` would flatten the contradiction away.
        _require_disjoint_keys(entries, rejected, processed, path=path)

        return cls(cursor=cursor, pending=entries, rejected=rejected, processed=processed)


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
    return EvolutionState.from_json(data, path, artifacts_root=config.storage.imported_artifacts)


def save_state(config: EvolutionConfig, state: EvolutionState) -> Path:
    """Replace the state file atomically. A reader sees the state before or
    after this call, never a truncated file."""

    path = config.state_path
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(state.to_json(), indent=2, sort_keys=True) + "\n")
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


def atomic_write_text(path: Path, text: str) -> None:
    """Write `text` so a reader sees the file before or after, never during.

    Shared with the batch freeze: a half-written manifest is worse than an
    absent one, because absent means "not frozen" and half-written means
    "frozen, membership unknown".
    """

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


def _require_int(data: Mapping[str, Any], key: str, where: str, *, minimum: int | None = None) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise StateError(f"{where}: {key} must be an integer")
    if minimum is not None and value < minimum:
        raise StateError(f"{where}: {key} must be at least {minimum}, got {value}")
    return value


def _require_timestamp(data: Mapping[str, Any], key: str, where: str) -> str:
    value = _require_str(data, key, where)
    if not is_rfc3339_date_time(value):
        raise StateError(f"{where}: {key} must be an RFC 3339 timestamp, got {value!r}")
    return value


def _require_digest(data: Mapping[str, Any], key: str, where: str) -> str:
    value = _require_str(data, key, where)
    if _SHA256.match(value) is None:
        raise StateError(f"{where}: {key} must be a sha256 digest")
    return value


def _require_artifacts_path(data: Mapping[str, Any], key: str, where: str, *, artifacts_root: str) -> str:
    """A staged bundle lives at `<artifacts_root>/<directory name>` and nowhere
    else — that is the only form `stage_artifacts` can produce.

    Repository-relative is too weak a check to hold the ownership boundary: it
    admits `evolution/cases`, so corrupted state could aim a later evidence read
    at versioned repository content. The comparison is against the root of the
    config governing this run, written out in full so a sibling directory whose
    name merely starts the same way (`imported-artifacts-x`) is not mistaken for
    a child.
    """

    value = _require_str(data, key, where)
    prefix = f"{artifacts_root}/"
    name = value[len(prefix) :] if value.startswith(prefix) else ""
    if not name or "/" in name or name in {".", ".."}:
        raise StateError(f"{where}: {key} must name one directory directly under {artifacts_root}/, got {value!r}")
    return value


def _require_rejections(data: Mapping[str, Any], path: Path) -> dict[str, Any]:
    """Rejections are evidence: the reason an operator acts on, the diagnostic
    that quotes the feed, and when it was decided.

    A shapeless entry — `{}` at the extreme — would still contribute its report
    key to `known_report_keys`, so the report is skipped forever while the three
    things worth keeping about it are gone. The reason must be one of the codes
    `reports.py` authors, because that bounded vocabulary is what the ledger's
    publishable line is derived from.
    """

    value = data["rejected"]
    if not isinstance(value, dict):
        raise StateError(f"{path}: rejected must be an object")
    for report_key, entry in value.items():
        where = f"{path}: rejected[{report_key!r}]"
        if not report_key:
            raise StateError(f"{path}: rejected has an empty report key")
        if not isinstance(entry, dict):
            raise StateError(f"{where}: must be an object")
        unexpected = set(entry) - _REJECTION_FIELDS
        if unexpected:
            raise StateError(f"{where}: unexpected fields {sorted(unexpected)}")
        reason = _require_str(entry, "reason", where)
        if reason not in REJECTION_REASONS:
            raise StateError(f"{where}: reason {reason!r} is not one of the codes this controller records")
        _require_str(entry, "detail", where)
        _require_timestamp(entry, "recorded_at", where)
    return value


def _require_processed(data: Mapping[str, Any], path: Path) -> dict[str, Any]:
    """`processed` records the reports a frozen batch has claimed.

    The shape is exactly what `batches._claim_reports` writes: the batch that
    claimed the report, and when. Nothing more — the batch manifest is the
    membership record, and duplicating any of it here would give one fact two
    homes that can disagree.

    Checked as a reference, like everything else in this file: an entry that
    kept its key and lost its batch would remove the report from discovery
    while no longer saying which cohort analyzed it, so the report can be
    neither re-imported nor traced.
    """

    value = data["processed"]
    if not isinstance(value, dict):
        raise StateError(f"{path}: processed must be an object")
    for report_key, entry in value.items():
        where = f"{path}: processed[{report_key!r}]"
        if not report_key:
            raise StateError(f"{path}: processed has an empty report key")
        if not isinstance(entry, dict):
            raise StateError(f"{where}: must be an object")
        unexpected = set(entry) - _PROCESSED_FIELDS
        if unexpected:
            raise StateError(f"{where}: unexpected fields {sorted(unexpected)}")
        batch_id = _require_str(entry, "batch_id", where)
        if batch_id_number(batch_id) is None:
            raise StateError(f"{where}: batch_id {batch_id!r} is not a batch identifier")
        _require_timestamp(entry, "recorded_at", where)
    return value


def _require_disjoint_keys(
    entries: list[PoolEntry],
    rejected: Mapping[str, Any],
    processed: Mapping[str, Any],
    *,
    path: Path,
) -> None:
    seen: dict[str, str] = {}
    groups: list[tuple[str, Any]] = [("rejected", rejected), ("processed", processed)]
    for entry in entries:
        groups.append((f"pending {entry.repo_id}/{entry.task_id}", entry.report_keys()))
    for where, keys in groups:
        for report_key in keys:
            previous = seen.get(report_key)
            if previous is not None:
                raise StateError(f"{path}: report {report_key} appears in both {previous} and {where}")
            seen[report_key] = where
