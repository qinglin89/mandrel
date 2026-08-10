"""The append-only sanitized audit at `evolution/ledger.jsonl`.

This file is versioned, so nothing may reach it that is not safe to publish:
records carry identities, decisions, and hashes — never report content. The
`detail` field is written by this package from bounded reason codes, never
filled with material fetched from a feed.

The ledger is an audit, not a state store. Nothing derives the pending pool,
the cursor, or batch membership from it, which is what makes the write order
in `importer` safe: state is committed first, and an interruption between the
two can cost the final audit line but can never corrupt what the controller
believes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..manifest import utc_timestamp
from .config import LEDGER_SCHEMA_FILENAME, EvolutionConfig
from .errors import LedgerError
from .schema import load_schema, validate_or_raise

LEDGER_SCHEMA_VERSION = 1

# Written in schema order so appended lines match the ones already in the file.
# `build_record` drops anything absent from this tuple, so a field added to
# `ledger-record.schema.json` and not added here is silently discarded on the
# way out — the two lists move together.
FIELD_ORDER = (
    "record_type",
    "schema_version",
    "recorded_at",
    "report_key",
    "batch_id",
    "experiment_id",
    "round",
    "draft_id",
    "task_id",
    "revision",
    "disposition",
    "detail",
)


def build_record(record_type: str, *, recorded_at: str | None = None, **fields: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "record_type": record_type,
        "schema_version": LEDGER_SCHEMA_VERSION,
        "recorded_at": recorded_at or utc_timestamp(),
    }
    record.update({key: value for key, value in fields.items() if value is not None})
    return {key: record[key] for key in FIELD_ORDER if key in record}


def append_records(config: EvolutionConfig, records: Iterable[Mapping[str, Any]]) -> int:
    """Validate then append. Nothing is written unless every record conforms —
    a partially appended batch of records is an audit that lies by omission."""

    pending = list(records)
    if not pending:
        return 0

    schema = load_schema(config.schema_path(LEDGER_SCHEMA_FILENAME))
    for record in pending:
        validate_or_raise(record, schema, description=f"ledger record {record.get('record_type')!r}")

    path = config.ledger_path
    _require_appendable(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = "".join(json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n" for record in pending)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(lines)
    return len(pending)


def read_records(config: EvolutionConfig) -> list[dict[str, Any]]:
    path = config.ledger_path
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LedgerError(f"{path}:{number}: invalid JSON: {exc}") from exc
        if not isinstance(record, dict):
            raise LedgerError(f"{path}:{number}: record is not an object")
        records.append(record)
    return records


def _require_appendable(path: Path) -> None:
    """A file that does not end in a newline was truncated mid-write; appending
    would splice the new record onto half of an old one."""

    if not path.is_file() or path.stat().st_size == 0:
        return
    with path.open("rb") as stream:
        stream.seek(-1, 2)
        if stream.read(1) != b"\n":
            raise LedgerError(f"{path} does not end with a newline; repair the truncated final record before appending")
