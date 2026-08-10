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

    Ordering is by the records' own `sequence`, which is the global order the
    real feed promises. The cursor is that sequence rendered as text; nothing
    outside this class relies on that.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def fetch_page(self, cursor: str | None, limit: int) -> FeedPage:
        if limit < 1:
            raise FeedError(f"page limit must be positive, got {limit}")
        records = self._records()
        after = self._decode_cursor(cursor)
        remaining = [record for record in records if _sequence_of(record) > after]
        items = remaining[:limit]
        if not items:
            return FeedPage(items=(), cursor=cursor, exhausted=True)
        return FeedPage(
            items=tuple(items),
            cursor=str(_sequence_of(items[-1])),
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

    def _records(self) -> list[Mapping[str, Any]]:
        directory = self.root / REPORTS_DIRNAME
        if not directory.is_dir():
            raise FeedError(f"feed directory has no {REPORTS_DIRNAME}/: {self.root}")
        records: list[Mapping[str, Any]] = []
        for path in sorted(directory.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise FeedError(f"unreadable feed record {path}: {exc}") from exc
            if not isinstance(record, dict):
                raise FeedError(f"feed record is not a JSON object: {path}")
            records.append(record)
        # A record whose sequence is unusable is still served: rejecting it is
        # the importer's decision to record, not the transport's to hide.
        return sorted(records, key=lambda record: (_sequence_of(record), str(record.get("report_key", ""))))

    @staticmethod
    def _decode_cursor(cursor: str | None) -> int:
        # Valid sequences start at 1, so -1 means "nothing consumed yet" and
        # still serves the sequence-0 records a malformed fixture produces.
        if cursor is None:
            return -1
        try:
            return int(cursor)
        except ValueError as exc:
            raise FeedError(f"invalid cursor for a directory feed: {cursor!r}") from exc


def _sequence_of(record: Mapping[str, Any]) -> int:
    """Sort key only. Anything the import schema would reject collapses to 0 so
    it is served once, on the first page, and recorded as a rejection."""

    value = record.get("sequence")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return 0
