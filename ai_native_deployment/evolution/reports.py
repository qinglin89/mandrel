"""Normalization, eligibility, and identity of one imported report.

Two gates run in this order, and the order is the point:

1. **Eligibility** — the contract's own admission rules, checked against the
   fields eligibility is about, so a rejected candidate carries a reason an
   operator can act on (`source-task-not-archived`, not "schema error at
   $.source.archived").
2. **Schema** — the full versioned shape, which is stricter and catches
   everything else.

Rejection is data, not failure: a malformed candidate is recorded and skipped,
never raised. Only a broken local contract (unreadable schema) raises.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from ..hashing import sha256_bytes
from .config import IMPORT_SCHEMA_FILENAME, EvolutionConfig
from .schema import load_schema, validate

SUPPORTED_REPORT_SCHEMA_VERSION = 1

# The L1+L2 artifact set. `evidence` and `static_metrics` are L1 (mechanical),
# `semantic_report` and `report_markdown` are L2 (judged); a report missing any
# of them is not a complete evaluation and cannot join a batch.
REQUIRED_ARTIFACTS = ("evidence", "static_metrics", "semantic_report", "report_markdown")

REASON_MALFORMED = "malformed-record"
REASON_UNSUPPORTED_SCHEMA_VERSION = "unsupported-report-schema-version"
REASON_NOT_ARCHIVED = "source-task-not-archived"
REASON_NOT_COMPLETED = "source-task-not-completed"
REASON_MISSING_ARTIFACT = "missing-artifact"
REASON_SCHEMA_INVALID = "schema-invalid"
REASON_ARTIFACT_BYTES_MISSING = "artifact-bytes-unavailable"
REASON_ARTIFACT_HASH_MISMATCH = "artifact-hash-mismatch"


@dataclass(frozen=True)
class Artifact:
    name: str
    sha256: str
    size_bytes: int
    media_type: str


@dataclass(frozen=True)
class Rejection:
    """A candidate that will not enter the pool, and why."""

    reason: str
    detail: str
    report_key: str | None = None


@dataclass(frozen=True)
class NormalizedReport:
    report_key: str
    sequence: int
    generated_at: str
    repo_id: str
    repo_name: str | None
    task_id: str
    evaluation_id: str
    artifacts: tuple[Artifact, ...]
    bundle_sha256: str
    record: Mapping[str, Any]

    @property
    def dedup_key(self) -> tuple[str, str]:
        """Batches count unique completed tasks (invariant 1), so identity for
        pooling is the source task — never the report or the evaluation, which
        an evaluator rerun would multiply."""

        return (self.repo_id, self.task_id)


def canonical_json(value: Any) -> bytes:
    """Byte form used for every content hash: sorted keys, no insignificant
    whitespace, UTF-8. Two structurally equal records hash identically
    regardless of how the feed serialized them."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def load_import_schema(config: EvolutionConfig) -> Mapping[str, Any]:
    """The import contract, read once per run and handed to `normalize`."""

    return load_schema(config.schema_path(IMPORT_SCHEMA_FILENAME))


def normalize(record: Any, config: EvolutionConfig, schema: Mapping[str, Any]) -> NormalizedReport | Rejection:
    """Validate one raw feed record into a `NormalizedReport`, or say why not."""

    if not isinstance(record, dict):
        return Rejection(REASON_MALFORMED, f"expected a JSON object, got {type(record).__name__}")

    report_key = record.get("report_key")
    key = report_key if isinstance(report_key, str) and report_key else None

    version = record.get("schema_version")
    if version != SUPPORTED_REPORT_SCHEMA_VERSION:
        return Rejection(
            REASON_UNSUPPORTED_SCHEMA_VERSION,
            f"report schema_version {version!r}; this build supports {SUPPORTED_REPORT_SCHEMA_VERSION}",
            key,
        )

    source = record.get("source")
    if not isinstance(source, dict):
        return Rejection(REASON_MALFORMED, "record has no source object", key)
    if config.eligibility.require_archived_task and source.get("archived") is not True:
        return Rejection(REASON_NOT_ARCHIVED, "source task is not archived", key)
    if source.get("completed") is not True:
        return Rejection(REASON_NOT_COMPLETED, "source task is not completed", key)

    artifacts = record.get("artifacts")
    if not isinstance(artifacts, dict):
        return Rejection(REASON_MALFORMED, "record has no artifacts object", key)
    for name in REQUIRED_ARTIFACTS:
        if not isinstance(artifacts.get(name), dict):
            return Rejection(REASON_MISSING_ARTIFACT, f"artifacts.{name} is absent", key)

    errors = validate(record, schema)
    if errors:
        return Rejection(REASON_SCHEMA_INVALID, "; ".join(errors[:3]), key)

    return NormalizedReport(
        report_key=record["report_key"],
        sequence=record["sequence"],
        generated_at=record["generated_at"],
        repo_id=source["repo_id"],
        repo_name=source.get("repo_name"),
        task_id=source["task_id"],
        evaluation_id=source["evaluation_id"],
        artifacts=tuple(
            Artifact(
                name=name,
                sha256=artifacts[name]["sha256"],
                size_bytes=artifacts[name]["size_bytes"],
                media_type=artifacts[name]["media_type"],
            )
            for name in REQUIRED_ARTIFACTS
        ),
        # Covers the whole validated record — every artifact hash, the
        # evaluator identity, and the full provenance block — so a frozen batch
        # pins exactly what was analyzed.
        bundle_sha256=sha256_bytes(canonical_json(record)),
        record=record,
    )


def verify_artifact_bytes(report: NormalizedReport, blobs: Mapping[str, bytes]) -> Rejection | None:
    """Check fetched bytes against what the record declared. A feed that serves
    the wrong body for a correct-looking record must not reach the pool."""

    for artifact in report.artifacts:
        data = blobs.get(artifact.name)
        if data is None:
            return Rejection(REASON_ARTIFACT_BYTES_MISSING, f"feed returned no bytes for {artifact.name}", report.report_key)
        if len(data) != artifact.size_bytes:
            return Rejection(
                REASON_ARTIFACT_HASH_MISMATCH,
                f"{artifact.name}: declared {artifact.size_bytes} bytes, fetched {len(data)}",
                report.report_key,
            )
        digest = sha256_bytes(data)
        if digest != artifact.sha256:
            return Rejection(
                REASON_ARTIFACT_HASH_MISMATCH,
                f"{artifact.name}: declared sha256 {artifact.sha256}, fetched {digest}",
                report.report_key,
            )
    return None
