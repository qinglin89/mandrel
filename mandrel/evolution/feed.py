"""The report-source boundary.

orch-hub owns report publication; this repository owns what it does with the
reports. `ReportFeed` is the whole of what the importer may assume about the
source, which keeps the protected HTTP client (a later slice) and the
`DirectoryFeed` used by tests and fixtures interchangeable.

Cursors are opaque to everything above this boundary. Only the feed
implementation knows what its cursor means; the importer stores whatever it
was handed and gives it back unread. Inferring order from a cursor — or from
an evaluation timestamp, which is scoped to a source repository — would put
reports in an order the feed never promised.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from .errors import FeedError

REPORTS_DIRNAME = "reports"
ARTIFACTS_DIRNAME = "artifacts"

# One record's place in the directory feed's total order.
_Position = tuple[int, str, str]
# Valid sequences start at 1, so this precedes every record — including the
# sequence-0 ones a malformed fixture produces.
_START: _Position = (-1, "", "")


@dataclass(frozen=True)
class FeedPage:
    """One page of raw report records.

    `cursor` is what to send on the next call; `exhausted` says the feed had
    nothing after these items. An empty page returns the cursor it was given,
    so a drained feed does not rewind.
    """

    items: tuple[Mapping[str, Any], ...]
    cursor: str | None
    exhausted: bool


class ReportFeed(Protocol):
    """Read-only view of the global completed-report feed."""

    def fetch_page(self, cursor: str | None, limit: int) -> FeedPage:
        """Return up to `limit` records positioned after `cursor`."""

    def fetch_artifacts(self, record: Mapping[str, Any]) -> dict[str, bytes]:
        """Return the artifact bodies for one record, keyed by artifact name."""


class DirectoryFeed:
    """A feed backed by a directory tree — fixtures, replay, and offline work.

    ```text
    <root>/reports/*.json                          one record per file
    <root>/artifacts/<report_key>/<artifact-name>  the bodies
    ```

    Ordering starts from the records' own `sequence`, which is the global order
    the real feed promises, and is made **total** by the report key and the
    file the record came from. Sequence alone is not a position: two records
    the schema would reject collapse to the same unusable sequence, and so do
    two records that simply repeat one. A cursor built on a non-unique position
    silently drops every record sharing it — the importer would advance past
    reports it never saw and never recorded.

    The cursor is that ordering triple as JSON. Nothing outside this class
    reads it; the importer stores it and hands it back unread.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def fetch_page(self, cursor: str | None, limit: int) -> FeedPage:
        if limit < 1:
            raise FeedError(f"page limit must be positive, got {limit}")
        ordered = self._records()
        after = self._decode_cursor(cursor)
        remaining = [entry for entry in ordered if entry[0] > after]
        items = remaining[:limit]
        if not items:
            return FeedPage(items=(), cursor=cursor, exhausted=True)
        return FeedPage(
            items=tuple(record for _, record in items),
            cursor=_encode_cursor(items[-1][0]),
            exhausted=len(remaining) == len(items),
        )

    def fetch_artifacts(self, record: Mapping[str, Any]) -> dict[str, bytes]:
        report_key = record.get("report_key")
        if not isinstance(report_key, str) or not report_key:
            raise FeedError("record has no report_key; cannot locate its artifacts")
        directory = self._artifact_dir(report_key)
        artifacts = record.get("artifacts")
        if not isinstance(artifacts, dict):
            return {}
        blobs: dict[str, bytes] = {}
        for name in artifacts:
            path = directory / name
            if path.is_file():
                blobs[name] = path.read_bytes()
        return blobs

    def _artifact_dir(self, report_key: str) -> Path:
        root = (self.root / ARTIFACTS_DIRNAME).resolve()
        candidate = (root / report_key).resolve()
        if candidate != root and root not in candidate.parents:
            raise FeedError(f"report_key escapes the artifact root: {report_key!r}")
        return candidate

    def _records(self) -> list[tuple[_Position, Mapping[str, Any]]]:
        directory = self.root / REPORTS_DIRNAME
        if not directory.is_dir():
            raise FeedError(f"feed directory has no {REPORTS_DIRNAME}/: {self.root}")
        entries: list[tuple[_Position, Mapping[str, Any]]] = []
        for path in sorted(directory.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise FeedError(f"unreadable feed record {path}: {exc}") from exc
            if not isinstance(record, dict):
                raise FeedError(f"feed record is not a JSON object: {path}")
            entries.append((_position_of(record, path.name), record))
        # A record whose sequence is unusable is still served: rejecting it is
        # the importer's decision to record, not the transport's to hide. The
        # file name keeps two such records apart, so both are served.
        return sorted(entries, key=lambda entry: entry[0])

    @staticmethod
    def _decode_cursor(cursor: str | None) -> _Position:
        if cursor is None:
            return _START
        try:
            decoded = json.loads(cursor)
        except json.JSONDecodeError:
            decoded = None
        if (
            not isinstance(decoded, list)
            or len(decoded) != 3
            or not isinstance(decoded[0], int)
            or isinstance(decoded[0], bool)
            or not all(isinstance(part, str) for part in decoded[1:])
        ):
            raise FeedError(
                f"invalid cursor for a directory feed: {cursor!r}; expected the "
                "[sequence, report_key, file] position this feed issues"
            )
        return (decoded[0], decoded[1], decoded[2])


def _position_of(record: Mapping[str, Any], name: str) -> _Position:
    """Total order for one record: usable sequence, then report key, then the
    file it came from — which is unique within the directory, so no two records
    ever share a position."""

    key = record.get("report_key")
    return (_sequence_of(record), key if isinstance(key, str) else "", name)


def _encode_cursor(position: _Position) -> str:
    return json.dumps(list(position), separators=(",", ":"), ensure_ascii=False)


def _sequence_of(record: Mapping[str, Any]) -> int:
    """Sort component only. Anything the import schema would reject collapses
    to 0, so such records sort first and are served before the valid ones."""

    value = record.get("sequence")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return 0
