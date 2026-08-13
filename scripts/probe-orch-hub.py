#!/usr/bin/env python3
"""Credentialed live probe: does the published orch-hub feed still answer the
contract `ai_native_deployment/evolution/hub.py` was reconciled against?

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
it is not a substitute for `aii-2 evolution sync`, which is what actually
imports.

What it proves that no unit test can: that the *live* service still serves the
shape the client translates. Every offline test answers with a fixture written
from this contract, so the two would agree with each other indefinitely after
the feed moved. Concretely it checks the page envelope, that exhaustion is a
value the feed reports, that a catalog entry still translates into a record the
real import schema admits, and that artifact bytes fetched by wire name hash to
the digests the entry published.

Exit status is 0 when every check passed, 1 when one failed, and 2 when the
probe could not run at all (no credentials, no reachable feed).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_native_deployment import evolution  # noqa: E402
from ai_native_deployment.evolution import hub, reports  # noqa: E402

PAGE_LIMIT = 5


def main() -> int:
    config = evolution.load_config(REPO_ROOT)
    try:
        feed = hub.feed_from_config(config, environ=os.environ)
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
