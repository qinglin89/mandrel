"""Machine-local evolution runtime state under `.ai-evolution/`.

Everything here is ignored by Git by design (contract invariant 11): the
discovery cursor, the pending pool, and the raw imported bundles are runtime
data, not repository content.

Six things are kept deliberately separate, because they answer six different
questions and collapsing them loses the answer to one of them:

| Field           | Answers                                             |
|-----------------|-----------------------------------------------------|
| `cursor`        | how far the feed has been *inspected*               |
| `feed_exhausted`| whether that inspection reached the *end* of it     |
| `pending`       | which unique tasks are *eligible and unbatched*     |
| `rejected`      | which reports were seen and refused, and why        |
| `processed`     | which reports a frozen batch already claimed        |
| `superseded`    | which valid reports a later evaluation of the same  |
|                 | task replaced, and which one replaced them          |

`superseded` is separate from `rejected` because the two say opposite things
about the same report. A rejection is a validity answer out of a bounded
vocabulary the ledger publishes (`reports.REJECTION_REASONS`); a superseded
report was valid, was staged, and lost its task's slot only because a later
evaluation of that task exists. Filing one under the other would put a
judgement about the report into a vocabulary that describes the report's own
defects.

`feed_exhausted` is separate from the cursor because a cursor says only where
discovery stopped, never why. Discovery that stopped at its page bound leaves
a pool that is a prefix of the feed, and that fact has to outlive the run that
learned it: the freeze it would mislead may be a separate command (`batches.py`,
admission).

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
from .manifests import claimed_reports
from .reports import REJECTION_REASONS, NormalizedReport, canonical_json
from .schema import is_rfc3339_date_time, parse_rfc3339

STATE_SCHEMA_VERSION = 3

# The complete shape of every version this build reads. `to_json` writes the
# current one; `from_json` requires exactly the fields of the version the file
# declares. Older versions are upgraded on read rather than refused, because
# this state is a machine's own runtime record and losing its cursor and pool
# would re-import evidence the feed may no longer serve.
#
# Version 1 predates `feed_exhausted`. Versions 1 and 2 both hold two report
# slots per pooled task — a `primary` and its `reruns` — where version 3 holds
# one; the entry parser takes the version for exactly that reason, and the
# upgrade files the losing reports under `superseded` rather than dropping
# them.
_STATE_FIELDS = {
    1: ("cursor", "pending", "rejected", "processed"),
    2: ("cursor", "feed_exhausted", "pending", "rejected", "processed"),
    3: ("cursor", "feed_exhausted", "pending", "rejected", "processed", "superseded"),
}
_STATE_KEYS = {version: frozenset(fields) | {"schema_version"} for version, fields in _STATE_FIELDS.items()}
_POOL_ENTRY_FIELDS = frozenset({"repo_id", "task_id", "first_imported_at", "report"})
_LEGACY_POOL_ENTRY_FIELDS = frozenset({"repo_id", "task_id", "first_imported_at", "primary", "reruns"})
_REPORT_REF_FIELDS = frozenset(
    {"report_key", "sequence", "evaluation_id", "generated_at", "bundle_sha256", "artifacts_path"}
)
# Exactly what `importer._record_rejection` writes.
_REJECTION_FIELDS = frozenset({"reason", "detail", "recorded_at"})
# Exactly what `batches._claim_reports` writes.
_PROCESSED_FIELDS = frozenset({"batch_id", "recorded_at"})
# Exactly what `superseded_record` writes.
_SUPERSEDED_FIELDS = frozenset({"repo_id", "task_id", "report", "superseded_by", "recorded_at"})

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

    def supersedes(self, other: "ReportRef") -> bool:
        """Whether this report replaces `other` as its task's evidence.

        **The later evaluation wins, and `generated_at` is what says which one
        that is.** `sequence` is the feed's publication order, and orch-hub
        publishes a report only once the task it evaluated is archived — so a
        report written while its task was still active is withheld and enters
        the feed at the position where it *became* publishable, behind reports
        generated after it. Ordering on `sequence` would hand the slot to
        whichever pack the feed happened to release last, which is a fact about
        the publisher's gate rather than about the evaluations.

        Compared as instants, not as strings: `generated_at` carries whatever
        offset its producer wrote (the live pool holds `+00:00`, the fixtures
        hold `Z`), and lexical order across two offsets is not time order.

        `sequence` breaks a tie only. Two distinct reports share a publication
        position nowhere in the feed, so this is the arbitrary-but-total half of
        the rule: it exists so the answer never depends on arrival order.
        """

        return (parse_rfc3339(self.generated_at), self.sequence) > (
            parse_rfc3339(other.generated_at),
            other.sequence,
        )


def superseded_record(
    *,
    repo_id: str,
    task_id: str,
    ref: ReportRef,
    superseded_by: str,
    recorded_at: str,
) -> dict[str, Any]:
    """The record `superseded` holds for one replaced report.

    The whole reference, not just its key. The key alone would satisfy the one
    thing this map has to do — keep the report out of `known_report_keys` so a
    later `sync` does not fetch and stage it again forever — and would leave its
    staged bundle on disk with nothing in state able to name it. Keeping the
    reference costs six fields and means the replaced pack stays as findable as
    it was while it was pooled (invariant 4).

    `superseded_by` names the report that took the task's slot, so the chain
    from any replaced pack reaches the evidence that is actually in play.
    """

    return {
        "repo_id": repo_id,
        "task_id": task_id,
        "report": ref.to_json(),
        "superseded_by": superseded_by,
        "recorded_at": recorded_at,
    }


@dataclass
class PoolEntry:
    """One unique completed task awaiting a batch, and the one report that is
    its evidence.

    A batch counts tasks, not reports (invariant 1), and a task's evidence is
    its latest evaluation. A second evaluation of a pooled task does not make
    the entry hold two: the later one takes the slot and the other moves to
    `superseded`, whole (`superseded_record`), so provenance is preserved
    (invariant 4) without any reader having to decide which of two packs it is
    looking at.

    Invariant 4 asks for identity, revisions, models, hashes and explicit
    missing fields to be kept. It does not ask for two of them on one entry, and
    keeping two is what made every downstream reader — the membership test, the
    manifest, the claim — restate which one it meant.
    """

    repo_id: str
    task_id: str
    report: ReportRef
    first_imported_at: str

    @property
    def dedup_key(self) -> tuple[str, str]:
        return (self.repo_id, self.task_id)

    def report_keys(self) -> set[str]:
        """This entry's report keys — one of them. A set because every caller
        unions or compares it against another decision's keys."""

        return {self.report.report_key}

    def to_json(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "task_id": self.task_id,
            "first_imported_at": self.first_imported_at,
            "report": self.report.to_json(),
        }

    @classmethod
    def from_json(
        cls,
        data: Any,
        where: str,
        *,
        artifacts_root: str,
        version: int,
        recorded_at: str,
    ) -> tuple["PoolEntry", dict[str, dict[str, Any]]]:
        """Rebuild one entry of the shape `version` declares, with the
        supersessions an older shape implies.

        Versions 1 and 2 wrote a `primary` and a list of `reruns`, so an entry
        of theirs can carry reports this build's entry has no slot for. They are
        returned rather than dropped: a dropped key leaves `known_report_keys`,
        and a report that leaves it is re-fetched and re-staged by every later
        `sync`. `recorded_at` is when this build made that call — the old file
        recorded no such decision, so there is no earlier moment to claim.

        The survivor is chosen by the same rule the importer applies to a live
        arrival (`ReportRef.supersedes`), which is what makes the upgrade a
        correction and not merely a reshape: a v2 entry whose `primary` was the
        later-published pack of an earlier-generated evaluation comes out of
        this holding the pack that was actually generated last.
        """

        if not isinstance(data, dict):
            raise StateError(f"{where}: pool entry must be an object")
        if version >= 3:
            unexpected = set(data) - _POOL_ENTRY_FIELDS
            if unexpected:
                raise StateError(f"{where}: unexpected fields {sorted(unexpected)}")
            entry = cls(
                repo_id=_require_str(data, "repo_id", where),
                task_id=_require_str(data, "task_id", where),
                report=ReportRef.from_json(data.get("report"), f"{where}.report", artifacts_root=artifacts_root),
                first_imported_at=_require_timestamp(data, "first_imported_at", where),
            )
            return entry, {}

        unexpected = set(data) - _LEGACY_POOL_ENTRY_FIELDS
        if unexpected:
            raise StateError(f"{where}: unexpected fields {sorted(unexpected)}")
        reruns = data.get("reruns")
        if not isinstance(reruns, list):
            raise StateError(f"{where}: reruns must be a list")
        repo_id = _require_str(data, "repo_id", where)
        task_id = _require_str(data, "task_id", where)
        primary = ReportRef.from_json(data.get("primary"), f"{where}.primary", artifacts_root=artifacts_root)
        others = [
            ReportRef.from_json(item, f"{where}.reruns[{i}]", artifacts_root=artifacts_root)
            for i, item in enumerate(reruns)
        ]

        keys = [primary.report_key] + [ref.report_key for ref in others]
        if len(set(keys)) != len(keys):
            raise StateError(f"{where}: the same report appears twice on this entry")
        # The old shape's own rule, still enforced on the way out of it:
        # `primary` was defined as the highest sequence seen, so a file whose
        # rerun outranks it was already describing its pool incorrectly, and
        # upgrading it would launder that into a shape where the disagreement
        # is no longer visible.
        for ref in others:
            if ref.sequence > primary.sequence:
                raise StateError(
                    f"{where}: rerun {ref.report_key} has sequence {ref.sequence}, "
                    f"above primary {primary.report_key} at {primary.sequence}"
                )

        survivor = primary
        for ref in others:
            if ref.supersedes(survivor):
                survivor = ref
        entry = cls(
            repo_id=repo_id,
            task_id=task_id,
            report=survivor,
            first_imported_at=_require_timestamp(data, "first_imported_at", where),
        )
        replaced = {
            ref.report_key: superseded_record(
                repo_id=repo_id,
                task_id=task_id,
                ref=ref,
                superseded_by=survivor.report_key,
                recorded_at=recorded_at,
            )
            for ref in (primary, *others)
            if ref.report_key != survivor.report_key
        }
        return entry, replaced


@dataclass
class EvolutionState:
    cursor: str | None = None
    # False until a discovery pass reports the feed drained. The default is the
    # honest reading of a repository that has never synced: nobody has shown
    # the pool is the whole eligible set, and admission needs that shown, not
    # assumed (contract invariants 1 and 2).
    feed_exhausted: bool = False
    pending: list[PoolEntry] = field(default_factory=list)
    rejected: dict[str, dict[str, Any]] = field(default_factory=dict)
    processed: dict[str, dict[str, Any]] = field(default_factory=dict)
    superseded: dict[str, dict[str, Any]] = field(default_factory=dict)

    def find(self, repo_id: str, task_id: str) -> PoolEntry | None:
        """The pooled entry for one completed task, where it is still pending.

        **The pending pool only, and that is the whole second-entry rule.** A
        report claimed by a frozen batch leaves the pool with its entry
        (`batches._claim_reports`), so a later evaluation of the same task finds
        nothing here and opens a new entry — evidence for a later batch, which
        is what invariant 3 says later reports are. Were the claimed entry kept
        instead, the arriving pack would take its slot and push the manifest's
        own report into `superseded` — a second decision about a report that
        already holds one, which `_require_disjoint_keys` refuses to load, while
        the immutable manifest goes on naming it.

        The new entry is then admissible on its own terms against whatever base
        stands when the next batch is proposed. Nothing here or in the proposal
        asks which tasks earlier batches counted, so the same task can be a
        member of two batches — at two different commits carrying one protocol,
        and at one and the same base after a batch that concluded no change
        (`tests/test_evolution_batches.py`, the two rejoin cases).
        """

        for entry in self.pending:
            if entry.dedup_key == (repo_id, task_id):
                return entry
        return None

    def known_report_keys(self) -> set[str]:
        """Every report this repository has already decided about. The importer
        checks this before fetching, which is what makes a re-run of `sync`
        over an unchanged feed a no-op.

        Superseded reports count. Losing a task's slot is a decision about the
        report, not an absence of one, and a key missing here is a key the feed
        offers again on every pass."""

        keys: set[str] = set(self.rejected) | set(self.processed) | set(self.superseded)
        for entry in self.pending:
            keys |= entry.report_keys()
        return keys

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "cursor": self.cursor,
            "feed_exhausted": self.feed_exhausted,
            "pending": [
                entry.to_json()
                for entry in sorted(self.pending, key=lambda entry: (entry.report.sequence, entry.repo_id, entry.task_id))
            ],
            "rejected": self.rejected,
            "processed": self.processed,
            "superseded": self.superseded,
        }

    @classmethod
    def from_json(
        cls,
        data: Any,
        path: Path,
        *,
        artifacts_root: str,
        claimed: Mapping[str, set[str]],
    ) -> "EvolutionState":
        """Load a complete state of a version this build reads, or refuse.

        Every field of the declared version is required, absent included. A
        file that has lost its `cursor` or its `pending` array is a file that
        was damaged, and reading the gap as "nothing yet" is exactly the silent
        reset this module exists to prevent: the cursor rewinds, the pool
        empties, and the run looks successful.

        An older file is upgraded here instead. A version-1 file predates
        `feed_exhausted`, so it cannot say whether its discovery drained the
        feed, and the upgrade assumes it did not. That is the same answer a
        fresh state gives, and it costs one sync to correct — where refusing
        the file would cost the cursor and the pool behind it. Versions 1 and 2
        also hold two report slots per pooled task; the entry parser takes the
        version and files the replaced reports under `superseded`, so an
        upgrade loses no report from `known_report_keys` and no report is
        fetched twice.

        `artifacts_root` is the repository-relative bundle root from the config
        governing this run; staged references are checked against it, so state
        cannot point later evidence reads anywhere else in the tree.

        `claimed` maps each report a frozen manifest names to the batches naming
        it, so a `processed` claim is checked against the membership record it
        refers to rather than merely parsed. Both are required arguments: this
        state is only meaningful against the config and the batches it was
        written beside, and a defaulted parameter is a check a later call site
        can skip by omission.
        """

        if not isinstance(data, dict):
            raise StateError(f"{path}: state must be a JSON object")
        version = data.get("schema_version")
        if version not in _STATE_FIELDS:
            raise StateError(
                f"{path}: unsupported state schema_version {version!r}; this build reads {sorted(_STATE_FIELDS)}"
            )
        unexpected = set(data) - _STATE_KEYS[version]
        if unexpected:
            raise StateError(f"{path}: unexpected state fields {sorted(unexpected)}")
        for name in _STATE_FIELDS[version]:
            if name not in data:
                raise StateError(f"{path}: incomplete state, {name!r} is missing; version {version} requires it")

        cursor = data["cursor"]
        if cursor is not None and not isinstance(cursor, str):
            raise StateError(f"{path}: cursor must be a string or null")
        # Version 1 never recorded it, and the safe reading of "unknown" is
        # "not drained": the next sync says so for certain.
        feed_exhausted = data["feed_exhausted"] if version >= 2 else False
        if not isinstance(feed_exhausted, bool):
            raise StateError(f"{path}: feed_exhausted must be a boolean")
        pending = data["pending"]
        if not isinstance(pending, list):
            raise StateError(f"{path}: pending must be a list")
        # One reading of the clock for the whole file: an upgrade dates every
        # supersession it derives from the moment this build derived them, and
        # entries of one file were not decided at different moments.
        upgraded_at = utc_timestamp()
        entries: list[PoolEntry] = []
        replaced: dict[str, dict[str, Any]] = {}
        for index, item in enumerate(pending):
            entry, losers = PoolEntry.from_json(
                item,
                f"{path}: pending[{index}]",
                artifacts_root=artifacts_root,
                version=version,
                recorded_at=upgraded_at,
            )
            entries.append(entry)
            # Merged one key at a time, because `dict.update` would keep only
            # the last record for a key two legacy entries both claim and drop
            # the first — and dropping it is what hides the contradiction.
            # Version 2 put every one of an entry's report keys in front of
            # `_require_disjoint_keys`, which is what refused a file saying two
            # tasks own one report; only the survivors reach it now, so the
            # upgrade answers for the reports it moves.
            for report_key, record in losers.items():
                previous = replaced.get(report_key)
                if previous is not None:
                    raise StateError(
                        f"{path}: report {report_key} is held by both "
                        f"{previous['repo_id']}/{previous['task_id']} and {entry.repo_id}/{entry.task_id}"
                    )
                replaced[report_key] = record
        seen: set[tuple[str, str]] = set()
        for entry in entries:
            if entry.dedup_key in seen:
                raise StateError(f"{path}: pending holds {entry.repo_id}/{entry.task_id} twice")
            seen.add(entry.dedup_key)

        rejected = _require_rejections(data, path)
        processed = _require_processed(data, path, claimed)
        superseded = (
            _require_superseded(data, path, artifacts_root=artifacts_root)
            if version >= 3
            else replaced
        )
        # One report has one decision. The same key in two of these means the
        # state contradicts itself about what was already done with it, and
        # `known_report_keys` would flatten the contradiction away.
        _require_disjoint_keys(entries, rejected, processed, superseded, path=path)
        _require_settled_supersessions(entries, processed, superseded, path=path)

        return cls(
            cursor=cursor,
            feed_exhausted=feed_exhausted,
            pending=entries,
            rejected=rejected,
            processed=processed,
            superseded=superseded,
        )


def state_exists(config: EvolutionConfig) -> bool:
    """Whether this machine holds runtime state at all.

    The difference between "no file" and "a file that says nothing about X" is
    load-bearing above: `.ai-evolution/` is ignored, so a fresh clone has no
    state and never staged anything, while a state file that exists is this
    machine's own record and a gap in it is a gap in evidence
    (`batches._require_consistent_claims`).
    """

    return config.state_path.is_file()


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
    return EvolutionState.from_json(
        data,
        path,
        artifacts_root=config.storage.imported_artifacts,
        claimed=claimed_reports(config),
    )


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


def staged_bundle(config: EvolutionConfig, report_key: str) -> bool:
    """Whether one report's bundle is staged on this machine.

    One question with one answer, asked by everything that reports how much of a
    batch's evidence is here: a frozen batch owns its reports wherever it was
    frozen, so a clone that never staged them holds a manifest with nothing
    behind it — a fact to show rather than damage, since the manifest's hashes
    still say what was analyzed.

    Volatile, and that is why it is one function: no writer of this lifecycle
    moves a staged directory, so the reading moves on its own and the portfolio's
    `observations_revision` is a digest of exactly these answers
    (`portfolio.py`). An answer spelled twice would be a token over one spelling
    and a surface rendering the other.
    """

    return (config.artifacts_root / artifacts_dir_name(report_key)).is_dir()


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


def atomic_create_text(path: Path, text: str) -> bool:
    """Write `text` at `path` only if nothing is there. False when something is.

    For the files that must never be overwritten — a task file may already carry
    a session log — where `atomic_write_text` is the wrong tool by definition:
    `os.replace` overwrites, so a caller that looks first and replaces second has
    a window between the two, and the file that appears in it is the one the
    check existed to protect.

    `os.link` closes that window: it is the check, refusing atomically when the
    target exists. The content still lands through a temporary file, so an
    interruption leaves no partial file for a later run to accept as whole.
    """

    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp, path)
        except FileExistsError:
            return False
        return True
    finally:
        temp.unlink(missing_ok=True)


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


def _require_processed(
    data: Mapping[str, Any],
    path: Path,
    claimed: Mapping[str, set[str]],
) -> dict[str, Any]:
    """`processed` records the reports a frozen batch has claimed.

    The shape is exactly what `batches._claim_reports` writes: the batch that
    claimed the report, and when. Nothing more — the batch manifest is the
    membership record, and duplicating any of it here would give one fact two
    homes that can disagree.

    Checked as a reference, like everything else in this file, and the reference
    is resolved rather than merely parsed. A claim is what removes a report from
    discovery (`known_report_keys`), so a syntactically valid batch id that no
    manifest backs — or one whose manifest never names this report — hides the
    report from every future sync while nothing on disk says which batch
    analyzed it. Resolving the claim against the manifests makes that
    unrepresentable: the membership record is the authority, and a claim that
    contradicts it is corruption, not history.
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
        owners = claimed.get(report_key) or set()
        if batch_id not in owners:
            named_by = f"; it is named by {sorted(owners)}" if owners else "; no frozen manifest names it at all"
            raise StateError(
                f"{where}: no manifest for {batch_id} names this report{named_by} — a claim with no membership "
                "record behind it removes the report from discovery forever"
            )
        _require_timestamp(entry, "recorded_at", where)
    return value


def _require_superseded(data: Mapping[str, Any], path: Path, *, artifacts_root: str) -> dict[str, Any]:
    """`superseded` records the valid reports a later evaluation of the same
    task replaced.

    The shape is exactly what `superseded_record` writes, and the reference it
    carries is checked like any other: a replaced pack is still staged evidence
    on this machine, and a record naming a bundle nowhere under the configured
    root would aim a later read at whatever it names instead.

    The map key is the identity, so the reference's own `report_key` has to
    agree with it. Two spellings of one fact that can disagree is how a report
    ends up suppressed from discovery under one key while its record describes
    another.
    """

    value = data["superseded"]
    if not isinstance(value, dict):
        raise StateError(f"{path}: superseded must be an object")
    for report_key, entry in value.items():
        where = f"{path}: superseded[{report_key!r}]"
        if not report_key:
            raise StateError(f"{path}: superseded has an empty report key")
        if not isinstance(entry, dict):
            raise StateError(f"{where}: must be an object")
        unexpected = set(entry) - _SUPERSEDED_FIELDS
        if unexpected:
            raise StateError(f"{where}: unexpected fields {sorted(unexpected)}")
        _require_str(entry, "repo_id", where)
        _require_str(entry, "task_id", where)
        ref = ReportRef.from_json(entry.get("report"), f"{where}.report", artifacts_root=artifacts_root)
        if ref.report_key != report_key:
            raise StateError(f"{where}: the record describes report {ref.report_key}, not the key it is filed under")
        replacement = _require_str(entry, "superseded_by", where)
        if replacement == report_key:
            raise StateError(f"{where}: a report cannot supersede itself")
        _require_timestamp(entry, "recorded_at", where)
    return value


def _require_settled_supersessions(
    entries: list[PoolEntry],
    processed: Mapping[str, Any],
    superseded: Mapping[str, Any],
    *,
    path: Path,
) -> None:
    """Every replaced report names a replacement this state still accounts for.

    A supersession is only a decision if the report that won is somewhere: in
    the pool, claimed by a batch, or replaced in its turn by one that is. A
    `superseded_by` naming nothing removes the report from discovery while
    nothing on disk holds the evidence that displaced it — the same hole
    `_require_processed` refuses for a claim with no manifest behind it.

    Then the chain has to end. A group of records pointing at each other
    satisfies the check above and still leaves every report in it out of the
    pool, out of every batch, and out of the feed: evidence this machine has
    quietly stopped being able to reach.
    """

    live = set(processed)
    for entry in entries:
        live |= entry.report_keys()
    for report_key, record in superseded.items():
        replacement = record["superseded_by"]
        if replacement not in live and replacement not in superseded:
            raise StateError(
                f"{path}: superseded[{report_key!r}] names {replacement} as its replacement, and this state holds "
                "no such report — pending, claimed, or superseded in turn; a supersession with no surviving "
                "evidence behind it hides the report from every future sync"
            )

    # Peel the chains that reach live evidence; whatever cannot be peeled is in
    # a cycle, or feeds one.
    settled = set(live)
    unsettled = {key: record["superseded_by"] for key, record in superseded.items()}
    while True:
        resolved = [key for key, replacement in unsettled.items() if replacement in settled]
        if not resolved:
            break
        settled.update(resolved)
        for key in resolved:
            del unsettled[key]
    if unsettled:
        raise StateError(
            f"{path}: superseded reports {sorted(unsettled)} replace each other in a cycle, so none of them "
            "reaches a report that is still pending or claimed"
        )


def _require_disjoint_keys(
    entries: list[PoolEntry],
    rejected: Mapping[str, Any],
    processed: Mapping[str, Any],
    superseded: Mapping[str, Any],
    *,
    path: Path,
) -> None:
    seen: dict[str, str] = {}
    groups: list[tuple[str, Any]] = [
        ("rejected", rejected),
        ("processed", processed),
        ("superseded", superseded),
    ]
    for entry in entries:
        groups.append((f"pending {entry.repo_id}/{entry.task_id}", entry.report_keys()))
    for where, keys in groups:
        for report_key in keys:
            previous = seen.get(report_key)
            if previous is not None:
                raise StateError(f"{path}: report {report_key} appears in both {previous} and {where}")
            seen[report_key] = where
