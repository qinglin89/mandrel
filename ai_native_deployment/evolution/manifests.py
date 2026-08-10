"""Committed batch content: the immutable manifest, and the closure record.

The read side of a frozen batch — membership, versioning, and whether its
analysis has finished. `batches.py` owns the writes, the way it already owns
`_write_manifest` against this module's `read_manifest`.

Split out of `batches.py` because two layers need it and neither may import the
other: `state.py` checks a processed report against the batch that claims it,
and `batches.py` allocates ids and freezes new manifests.

**Versioning.** A frozen manifest is immutable (invariant 3), so the schema it
was written against is immutable too. Version 1 is the shape this repository
shipped before per-report cohort provenance existed; version 2 adds it. The
writer emits the current version and the reader accepts both, each against its
own frozen schema file. Tightening version 1 in place would instead have
redefined manifests already written under it — a manifest that was valid when
frozen would stop loading, with no migration possible on content that may not
be edited.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .config import (
    BATCH_SCHEMA_FILENAME,
    BATCH_SCHEMA_V1_FILENAME,
    CLOSURE_SCHEMA_FILENAME,
    EvolutionConfig,
    batch_id_number,
    format_batch_id,
)
from .errors import BatchError
from .schema import load_schema, validate_or_raise

MANIFEST_FILENAME = "manifest.json"
FINDINGS_FILENAME = "findings.md"
CLOSURE_FILENAME = "analysis-complete.json"

CLOSURE_SCHEMA_VERSION = 1

# What a freeze writes.
BATCH_SCHEMA_VERSION = 2
# What this build reads, and the frozen schema each version is validated
# against. Every version ever written keeps its entry here: dropping one would
# make an immutable manifest unreadable rather than merely outdated.
BATCH_SCHEMA_FILENAMES = {1: BATCH_SCHEMA_V1_FILENAME, 2: BATCH_SCHEMA_FILENAME}


@dataclass(frozen=True)
class Batch:
    """One frozen batch on disk, as its manifest describes it."""

    batch_id: str
    directory: Path
    manifest: Mapping[str, Any]

    @property
    def manifest_path(self) -> Path:
        return self.directory / MANIFEST_FILENAME

    @property
    def findings_path(self) -> Path:
        return self.directory / FINDINGS_FILENAME

    @property
    def findings_recorded(self) -> bool:
        """Whether the analysis has committed its dispositions.

        Necessary for a batch to be closed, and never sufficient on its own —
        the task writes this file while it is still being developed. What turns
        it into closure is the record beside it (`closure_path`), which the
        controller writes only from a completed analysis task.
        """

        return self.findings_path.is_file()

    @property
    def closure_path(self) -> Path:
        return self.directory / CLOSURE_FILENAME

    @property
    def schema_version(self) -> int | None:
        version = self.manifest.get("schema_version")
        return version if isinstance(version, int) and not isinstance(version, bool) else None

    @property
    def analysis_task_id(self) -> str | None:
        value = self.manifest.get("analysis_task_id")
        return value if isinstance(value, str) and value else None

    @property
    def reports(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.manifest.get("reports") or ())

    @property
    def report_keys(self) -> frozenset[str]:
        return frozenset(str(report["report_key"]) for report in self.reports)

    @property
    def task_count(self) -> int:
        """Unique completed tasks — the unit invariant 1 counts in. Reruns share
        a `(repo_id, task_id)` with their primary and so do not inflate it."""

        return len({(report["repo_id"], report["task_id"]) for report in self.reports})


def load_batches(config: EvolutionConfig) -> list[Batch]:
    """Every frozen batch, validated against the schema of its own version.

    Fails closed on anything it cannot account for: an unrecognised directory
    name might be a batch this build cannot read, and skipping it would let an
    allocation reuse an id a manifest already claims. Dot-prefixed names are the
    exception — those are staging residue from an interrupted freeze, which
    belongs to no batch.
    """

    root = config.batches_root
    if not root.is_dir():
        return []

    batches: list[Batch] = []
    for entry in sorted(root.iterdir()):
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        if batch_id_number(entry.name) is None:
            raise BatchError(
                f"{entry}: not a batch identifier; only frozen batches belong under {config.storage.batches}/"
            )
        manifest = read_manifest(config, entry / MANIFEST_FILENAME)
        if manifest.get("batch_id") != entry.name:
            raise BatchError(
                f"{entry / MANIFEST_FILENAME}: manifest claims batch_id {manifest.get('batch_id')!r} "
                f"but sits in {entry.name!r}; a manifest cannot name another batch's id"
            )
        batches.append(Batch(batch_id=entry.name, directory=entry, manifest=manifest))
    return batches


def next_batch_id(batches: list[Batch]) -> str:
    """One past the highest id ever allocated.

    Counted from the highest, not from how many exist: reusing the id of a batch
    whose directory was moved away would attach new evidence to an old cohort's
    name.
    """

    highest = max((batch_id_number(batch.batch_id) or 0 for batch in batches), default=0)
    return format_batch_id(highest + 1)


def claimed_reports(config: EvolutionConfig) -> dict[str, set[str]]:
    """Every report a frozen manifest names, mapped to the batches naming it.

    The authority for whether a `processed` claim in runtime state means
    anything: the manifests are the membership record, so a claim naming a batch
    that does not name the report back is a claim with nothing behind it. A key
    maps to a set because two manifests naming one report is a repository this
    controller must be able to describe rather than crash on — the caller
    decides what to do about it.
    """

    owners: dict[str, set[str]] = {}
    for batch in load_batches(config):
        for report_key in batch.report_keys:
            owners.setdefault(report_key, set()).add(batch.batch_id)
    return owners


def read_closure(config: EvolutionConfig, batch: Batch) -> Mapping[str, Any] | None:
    """The batch's committed closure record, or None when it has none.

    This is the portable half of the closure guard. `.ai-tasks/` is
    machine-local and ignored, so the analysis task's lifecycle can be read on
    at most one machine; the record travels with the repository, which is what
    lets every other clone tell a finished analysis from a draft. Reading task
    *absence* as completion, as this controller once did, gives the opposite
    answer everywhere the task never existed.

    Validated, and cross-checked against the manifest it sits beside: a record
    naming another batch or another task is corruption, and corruption that
    loads here releases the next cohort early.
    """

    path = batch.closure_path
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchError(f"unreadable batch closure record {path}: {exc}") from exc
    if not isinstance(record, dict):
        raise BatchError(f"batch closure record is not a JSON object: {path}")

    validate_or_raise(
        record,
        load_schema(config.schema_path(CLOSURE_SCHEMA_FILENAME)),
        description=f"batch closure record {path}",
    )
    if record["batch_id"] != batch.batch_id:
        raise BatchError(
            f"{path}: closure record names batch {record['batch_id']!r} but sits in {batch.batch_id!r}; "
            "one batch's completion cannot close another"
        )
    named = batch.analysis_task_id
    if named is not None and record["analysis_task_id"] != named:
        raise BatchError(
            f"{path}: closure record attests to task {record['analysis_task_id']!r}, but "
            f"{batch.manifest_path} names {named!r} as this batch's analysis task"
        )
    return record


def read_manifest(config: EvolutionConfig, path: Path) -> Mapping[str, Any]:
    """One manifest, validated against the schema of the version it declares."""

    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BatchError(
            f"{path.parent} has no {MANIFEST_FILENAME}; a batch directory without its membership snapshot "
            "is not a batch — remove it or restore the manifest"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchError(f"unreadable batch manifest {path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise BatchError(f"batch manifest is not a JSON object: {path}")

    version = manifest.get("schema_version")
    filename = BATCH_SCHEMA_FILENAMES.get(version) if isinstance(version, int) and not isinstance(version, bool) else None
    if filename is None:
        raise BatchError(
            f"{path}: unsupported batch manifest schema_version {version!r}; "
            f"this build reads {sorted(BATCH_SCHEMA_FILENAMES)}"
        )
    validate_or_raise(
        manifest,
        load_schema(config.schema_path(filename)),
        description=f"batch manifest {path} (schema version {version})",
    )
    return manifest
