#!/usr/bin/env python3
"""Credentialed live probe: does the published orch-hub feed still answer the
contract `mandrel/evolution/hub.py` was reconciled against?

NOT part of `scripts/check.sh`, and deliberately so (conventions: credentialed
and network-dependent probes stay off the required gate). It needs a reachable
orch-hub and a real token, so it can neither gate a merge nor run in CI — the
feed usually lives on the operator's own machine, which no GitHub runner can
reach. Run it by hand after a client or feed change:

    export ORCH_HUB_URL=http://127.0.0.1:8765
    export ORCH_HUB_TOKEN=...
    scripts/probe-orch-hub.py

**Read-only.** It builds the shipped client, reads one page, and verifies one
report's artifact bytes. It writes no cursor, no pool, no ledger, and no
artifact — so it is safe to run repeatedly and against an unfamiliar feed, and
it is not a substitute for `mandrel evolution sync`, which is what actually
imports.

What it proves that no unit test can: that the *live* service still serves the
shape the client translates. Every offline test answers with a fixture written
from this contract, so the two would agree with each other indefinitely after
the feed moved. Concretely it checks the page envelope, that exhaustion is a
value the feed reports, that a catalog entry still translates into a record the
real import schema admits, that the protocol identity each entry states is the
one that reaches the record, and that artifact bytes fetched by wire name hash to
the digests the entry published.

The identity check needs the *raw* page as well as the translated records, so the
page body is teed as the shipped client reads it and the two are compared side by
side: what each entry stated is what its record must carry, which catches a
client that dropped, reshaped, or invented an identity against a feed that is
perfectly healthy. It also reports how many entries state one at all — the number
an operator watches to see whether placeable reports have started arriving. A
feed serving none still passes and says so: absence is a fact about those
reports, not a defect in this client.

Exit status is 0 when every check passed, 1 when one failed, and 2 when the
probe could not run at all (no credentials, no reachable feed).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mandrel import evolution  # noqa: E402
from mandrel.evolution import hub, reports  # noqa: E402

PAGE_LIMIT = 5


def main() -> int:
    config = evolution.load_config(REPO_ROOT)
    bodies: list[bytes] = []
    try:
        feed = hub.feed_from_config(config, environ=os.environ, opener=teeing_opener(bodies))
    except evolution.FeedError as exc:
        print(f"probe cannot run: {exc}")
        return 2

    # The URL is printed; the token never is, here or in any error this client
    # raises.
    print(f"probing {feed.base_url}{feed.feed_path}")

    try:
        page = feed.fetch_page(None, PAGE_LIMIT)
    except evolution.FeedError as exc:
        print(f"probe cannot run: {exc}")
        return 2

    checks: list[tuple[str, bool, str]] = [
        ("page-envelope", True, f"{len(page.items)} entr(ies), cursor={page.cursor!r}"),
        (
            "exhaustion-reported",
            isinstance(page.exhausted, bool),
            f"exhausted={page.exhausted} (from the feed's has_more, never inferred)",
        ),
        protocol_identity_check(bodies[0] if bodies else b"", page.items),
    ]

    if not page.items:
        checks.append(("record-translates", False, "the feed served no entries, so nothing could be translated"))
        return report(checks)

    schema = reports.load_import_schema(config)
    record = page.items[0]
    result = reports.normalize(record, config, schema)
    if isinstance(result, reports.Rejection):
        checks.append(
            (
                "record-translates",
                False,
                f"the translated entry is not admissible: {result.reason} — {result.detail}",
            )
        )
        return report(checks)

    checks.append(("record-translates", True, f"{result.report_key} normalizes as a v1 import record"))

    try:
        blobs = feed.fetch_artifacts(record)
    except evolution.FeedError as exc:
        checks.append(("artifact-bytes", False, str(exc)))
        return report(checks)

    mismatch = reports.verify_artifact_bytes(result, blobs)
    checks.append(
        (
            "artifact-bytes",
            mismatch is None,
            (
                f"{len(blobs)} artifact(s) hash to the digests the entry published"
                if mismatch is None
                else f"{mismatch.reason} — {mismatch.detail}"
            ),
        )
    )
    return report(checks)


def teeing_opener(bodies: list[bytes]) -> hub.Opener:
    """The client's own opener, keeping a copy of each body it reads.

    Built from the client's default so the probe exercises the transport the tool
    ships — the redirect refusal included, which is the rule that decides where
    the bearer token may go. The copy is what lets the identity check compare the
    page as served with the records it became; without it the probe could only
    report what the translation produced, which is the half that cannot tell a
    renamed field from an unstated one.
    """

    default = hub._default_opener()

    def opener(request: Any, timeout: float | None = None) -> Any:
        return _Tee(default(request, timeout=timeout), bodies)

    return opener


class _Tee:
    """One response, passed through, with what was read appended to `sink`."""

    def __init__(self, response: Any, sink: list[bytes]) -> None:
        self._response = response
        self._sink = sink

    def read(self, size: int = -1) -> bytes:
        data = self._response.read(size)
        self._sink.append(data)
        return data

    def __enter__(self) -> "_Tee":
        self._response.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        self._response.__exit__(*exc)


def protocol_identity_check(body: bytes, records: tuple[Any, ...]) -> tuple[str, bool, str]:
    """Does the identity each entry states reach the record it became?

    The rule is the contract's, restated here against the live wire rather than
    against a fixture: an entry whose `provenance.protocol` is available with both
    halves must translate to exactly those two values, and anything else — absent,
    unavailable, legacy, half a pair — must translate to two nulls.

    An entry stating no identity is reported, not failed: every report published
    before orch-hub captured the identity says exactly that, and the count is what
    tells an operator whether placeable reports have started arriving. That count
    is also the only signal for a hub that renamed one of the pair's fields, since
    under the contract's names such an entry states nothing — indistinguishable
    here from a report whose runs were never verified, and told apart by reading
    the entry's own `detail`.
    """

    try:
        entries = json.loads(body.decode("utf-8"))["reports"]
    except (UnicodeDecodeError, ValueError, KeyError, TypeError):
        return ("protocol-identity", False, "the page body could not be re-read to compare provenance against")
    if not isinstance(entries, list):
        return ("protocol-identity", False, f"the page served {type(entries).__name__} where the catalog entries go")

    stated = 0
    for entry, record in zip(entries, records):
        published = entry.get("provenance") if isinstance(entry, dict) else None
        section = published.get("protocol") if isinstance(published, dict) else None
        expected: tuple[Any, Any] = (None, None)
        if isinstance(section, dict) and section.get("available") is True:
            halves = (section.get("effective_revision"), section.get("deploy_lock_hash"))
            if all(isinstance(half, str) and half.strip() for half in halves):
                expected = halves
                stated += 1
        provenance = record["provenance"]
        actual = (provenance["effective_revision"], provenance["deploy_lock_hash"])
        if actual != expected:
            key = entry.get("report_key") if isinstance(entry, dict) else None
            return (
                "protocol-identity",
                False,
                f"entry {key!r} states {expected} and the record carries {actual}",
            )
    return (
        "protocol-identity",
        True,
        f"{stated} of {len(entries)} entr(ies) state a verified identity; each record carries what its entry did",
    )


def report(checks: list[tuple[str, bool, str]]) -> int:
    print()
    for name, passed, detail in checks:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}: {detail}")
    failed = [name for name, passed, _ in checks if not passed]
    print()
    print(f"VERDICT: {'the live feed matches the client contract' if not failed else 'mismatch in ' + ', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
