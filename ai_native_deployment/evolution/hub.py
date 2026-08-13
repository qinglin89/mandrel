"""The protected orch-hub report feed, as an HTTP client.

orch-hub owns report publication and the global feed; this repository owns what
it does with the reports. The feed is published, and this module is written
against the shape it actually serves (reconciled against the running service on
2026-08-12) rather than against the shape this repository would have preferred.

**Wire contract.**

```text
GET  <ORCH_HUB_URL><source.report_feed_path>?limit=<n>[&after=<watermark>]
     Authorization: Bearer <ORCH_HUB_TOKEN>
     -> 200 {"enabled": true,
             "reports": [<catalog entry>, ...],
             "cursor": <int>, "next_cursor": <int>,
             "has_more": <bool>}
     400 the watermark is not a non-negative integer

GET  <ORCH_HUB_URL><source.report_feed_path>/<report_key>/artifacts/<wire name>
     Authorization: Bearer <ORCH_HUB_TOKEN>
     -> 200 <artifact bytes, byte for byte as published>
        404 unknown report key or artifact name    410 published, bytes pruned
        409 incoherent stored identity             500 unreadable artifact
```

**The cursor stays opaque above this module.** orch-hub pages an append-only
catalog by an integer watermark sent as `after`, so the client converts at the
boundary — string in `FeedPage`, watermark on the wire — and nothing above
`ReportFeed` learns that a cursor is a number. `next_cursor` is always present:
orch-hub echoes the watermark unchanged for an empty page, so a drained feed
does not rewind and the client needs no rule of its own for that.

**`exhausted` is a value the feed reports**, never one this client infers. It
authorizes a later, separate `freeze` to treat the pool as the whole eligible
set, so it is read from `has_more` (`exhausted` is its negation) and a page
omitting it gets a refusal, not a guess. A short page proves nothing either way.

**Translation.** A catalog entry is not an `evaluation-import.schema.json`
record: orch-hub names the same facts its own way (`seq`, `generated_ts`, flat
repo/task fields, artifacts as a list keyed by published filename) and carries no
`schema_version`, `media_type`, or `provenance` block in this repository's sense.
`_import_record` maps one to the other so both feeds hand the importer the same
shape and `DirectoryFeed` stays interchangeable. Two mappings are derivations
rather than restatements, and both are deliberate:

- `source.completed` is read from the entry's `archived`. orch-hub catalogs a
  report only once its task was archived at publication, and this protocol
  archives a task only through the completion close-out — so archived is how
  this feed says completed. It is *mapped*, not asserted: an entry that ever
  said `archived: false` yields `completed: false` and the importer's own
  `source-task-not-completed` gate still fires.
- `media_type` is derived from the published filename, because the feed states
  the four names and their types are fixed but never repeats the type per entry.

The translation is **total**: it always yields a record, never raises, and never
drops one. Deciding what a record is worth stays the importer's job — a
transport that quietly discarded a malformed entry would hide a rejection the
ledger is supposed to carry — so a field orch-hub did not send arrives absent or
null and is refused downstream with a reason an operator can act on.

What the entry carries and the import schema has no home for (`repo_path`,
`cataloged_ts`, `target_source`, and orch-hub's own runs/git provenance) is
dropped: the schema closes its objects with `additionalProperties: false`, and
`target_source` in particular is evaluation-trigger provenance, which the
evolution contract deliberately puts outside artifact eligibility.

**Provenance is stated as absent.** The import schema requires this
repository's provenance block, and orch-hub publishes none of those fields, so
every translated record carries them null. That is honest rather than inert: a
release assessment reads `effective_revision`, and a null one lands the cohort
in the designed "missing provenance is inconclusive" path instead of inventing a
revision no target ever reported.

Nothing here can be made to fill it in. `effective_revision` means the canonical
commit the evaluated target's deployed payload came from — its
`.ai-deploy-lock.json` `source_git_commit` — as of the evaluation, and the only
side that held that worktree then is the one that published the report. This
client sees a catalog entry; a lock it read today would state what that target
holds today, which places a pre-release report on the wrong side of a promotion
the target has since been redeployed onto. So the field is left null until the
feed carries it, and what that would take is written down in the contract
(`evolution/README.md`, Release assessment): `source_git_commit` verbatim as
`effective_revision` and `canonical_payload_sha256` as `deploy_lock_hash`, both
absent for a target that carried no lock. Whichever names orch-hub gives them,
`_unstated_provenance` is the one place this translation reads them from.

**A published revision is not a checked one.** Both fields are copied from a
receipt this client never sees, and a receipt is only an account of the payload
under the two conditions the contract states: the deploy could tie the payload it
wrote to that commit (a lock written from a dirty canonical tree states no
revision at all, and one written before that rule states a commit nothing
checked), and the target still matched its receipt when it was evaluated. Neither
is answerable from a catalog entry, so when the feed does carry the fields this
translation copies what it is told and copies an absent field as absent — it
never derives one, never falls back to a neighbouring value, and never treats a
present `deploy_lock_hash` as evidence for a missing revision. Anything weaker
turns an unverified lock into a cohort placement, which is worse than the null it
replaced: an excluded report costs a denominator, a misplaced one invents a
direction.

**Integrity headers are not trusted.** The raw-artifact route serves bytes that
no longer match their recorded digest with
`X-Report-Artifact-Integrity: mismatched` rather than hiding them, precisely so
the consumer can catch the edit. This client therefore ignores the header for
every decision and lets `verify_artifact_bytes` compare the bytes against the
feed's own manifest — the check belongs to the side that did not publish them.

**Credentials.** The token travels in a header, never in a URL or a log line,
and never appears in an error message. Plain `http` is refused for anything but
a loopback host: a bearer token on the wire in clear text is a leak that no
later care can undo, and a local orch-hub is the only case where it is not. For
the same reason no redirect is followed — checking the configured URL says
nothing about where a `Location` header would send the token. Confirmed against
the published feed rather than assumed: no route it serves redirects, so the
refusal costs nothing and is kept.

**Bounds.** Every response body is read under a limit this client owns rather
than one the feed declares, so a corrupt or hostile feed cannot make the tool
buffer until it dies. Also confirmed against the published feed: the largest
artifact it serves is measured in tens of kilobytes, three orders of magnitude
under the bound, so no streaming-to-disk design is needed for real reports.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping

from .config import EvolutionConfig
from .errors import FeedError
from .feed import FeedPage

DEFAULT_TIMEOUT_SECONDS = 30.0

# The most this client will buffer from one response — a feed page or an
# artifact body alike. A declared `size_bytes` is the other side's claim, and the
# import schema sets no ceiling on it, so reading up to it lets a feed name a
# size no machine can hold. A smaller declared size still tightens the read; a
# body that fills this cap fails the caller's size/hash check, which names both
# numbers, so the bound rejects rather than silently truncates.
MAX_RESPONSE_BYTES = 32 * 1024 * 1024

ARTIFACTS_PATH_SEGMENT = "artifacts"

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})

# Bound for feed-controlled text quoted back in an error message.
MAX_QUOTED_CHARS = 200

# The version of the import record this client emits. It is this repository's
# record shape, not orch-hub's — the feed carries no such field, and the entry's
# own `evaluator.schema_version` is the evaluator's rubric, a different fact.
IMPORT_RECORD_SCHEMA_VERSION = 1

# The import schema's artifact name -> the filename orch-hub publishes it under.
# orch-hub's route selector is a fixed four-value whitelist, so this mapping is
# total in both directions and nothing here is ever built from feed-controlled
# text.
ARTIFACT_WIRE_NAMES = {
    "evidence": "evidence.json",
    "static_metrics": "static_metrics.json",
    "semantic_report": "semantic_report.json",
    "report_markdown": "report.md",
}

# Derived, because the feed states these types once for the four fixed names
# rather than repeating them per entry, while the import schema requires one per
# artifact.
ARTIFACT_MEDIA_TYPES = {
    "evidence": "application/json",
    "static_metrics": "application/json",
    "semantic_report": "application/json",
    "report_markdown": "text/markdown",
}

Opener = Callable[..., Any]


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect rather than follow it with the token attached.

    `urllib`'s default handler copies the request headers — `Authorization`
    among them — onto the redirected request, and the destination is chosen by
    whoever answered. Checking the configured base URL therefore proves nothing
    about where the token ends up: one `Location` to another origin, or from
    https down to plain http, hands the bearer token to a host nobody checked.

    Refusing outright is the whole rule, so there is no origin comparison to get
    subtly wrong — default ports, case, internationalized hosts, a chain that is
    same-origin at each step and not end to end. The stated wire contract names
    two exact endpoints; a redirect is not part of it, and naming the
    destination makes a legitimate one a config edit rather than a mystery.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        fp.close()
        raise FeedError(
            f"orch-hub redirected {req.full_url} to {_bounded(newurl)} (HTTP {code}); the request carries "
            "the bearer token, so it is not resent to a destination this client has not checked. Point the "
            "feed URL environment variable at the final URL if that redirect is expected."
        )


def _default_opener() -> Opener:
    """The opener every client uses unless a caller injects one.

    Built here rather than reaching for `urllib.request.urlopen`, which runs the
    process-global opener: refusing redirects has to be this client's own
    property and not something another import can replace.
    """

    return urllib.request.build_opener(_RefuseRedirects()).open


class OrchHubFeed:
    """A `ReportFeed` backed by the protected orch-hub API.

    `opener` is `urllib.request.urlopen`'s signature, injected so tests exercise
    the request this client builds and the responses it accepts without a
    socket.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        feed_path: str,
        *,
        opener: Opener | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = _checked_base_url(base_url)
        self.feed_path = "/" + feed_path.strip("/")
        self._token = token
        self._open = opener or _default_opener()
        self._timeout = timeout

    def fetch_page(self, cursor: str | None, limit: int) -> FeedPage:
        if limit < 1:
            raise FeedError(f"page limit must be positive, got {limit}")
        query = {"limit": str(limit)}
        if cursor is not None:
            # The watermark orch-hub pages by. It is a string here because a
            # cursor is opaque above this boundary; the feed refuses one that is
            # not a non-negative integer rather than silently resetting.
            query["after"] = cursor
        payload = self._get_json(f"{self._endpoint()}?{urllib.parse.urlencode(query)}")

        enabled = payload.get("enabled")
        if enabled is not None and enabled is not True:
            raise FeedError(
                f"{self._endpoint()}: orch-hub reports its evaluation subsystem disabled, so this page describes "
                "no part of the feed; enable report publication on the hub before importing"
            )

        reports = payload.get("reports")
        if not isinstance(reports, list) or any(not isinstance(item, dict) for item in reports):
            raise FeedError(f"{self._endpoint()}: 'reports' must be a list of catalog entry objects")
        has_more = payload.get("has_more")
        if not isinstance(has_more, bool):
            raise FeedError(
                f"{self._endpoint()}: 'has_more' must be a boolean saying whether the catalog already holds more "
                "beyond this page; it decides whether a later freeze may treat the pool as the whole eligible set, "
                "so the end of the feed is never inferred from a short page"
            )
        next_cursor = payload.get("next_cursor")
        # An integer today; accepted as a string too, so an opaque watermark
        # later would not read as a malformed page. Null is refused either way:
        # orch-hub echoes the cursor unchanged for an empty page, so a missing
        # one is a broken page rather than "stay where you are".
        if isinstance(next_cursor, bool) or not isinstance(next_cursor, (int, str)) or next_cursor == "":
            raise FeedError(
                f"{self._endpoint()}: 'next_cursor' must be the watermark to send as the next 'after' — an integer, "
                f"or the cursor unchanged for an empty page; got {next_cursor!r}"
            )

        return FeedPage(
            items=tuple(_import_record(entry) for entry in reports),
            cursor=str(next_cursor),
            exhausted=not has_more,
        )

    def fetch_artifacts(self, record: Mapping[str, Any]) -> dict[str, bytes]:
        report_key = record.get("report_key")
        if not isinstance(report_key, str) or not report_key:
            raise FeedError("record has no report_key; cannot locate its artifacts")
        artifacts = record.get("artifacts")
        if not isinstance(artifacts, dict):
            return {}

        blobs: dict[str, bytes] = {}
        for name, meta in artifacts.items():
            wire_name = ARTIFACT_WIRE_NAMES.get(str(name))
            if wire_name is None:
                # Not one of the four names the feed publishes, so no URL
                # addresses it. Left absent rather than requested: the importer
                # records the report as missing that body, which is the truth,
                # where a 404 raised from here would bury an eligible report on
                # a name this client simply cannot ask for.
                continue
            declared = meta.get("size_bytes") if isinstance(meta, dict) else None
            body = self._get_bytes(
                self._artifact_url(report_key, wire_name),
                limit=declared if isinstance(declared, int) and not isinstance(declared, bool) and declared >= 0 else None,
            )
            if body is not None:
                blobs[str(name)] = body
        return blobs

    def _endpoint(self) -> str:
        return f"{self.base_url}{self.feed_path}"

    def _artifact_url(self, report_key: str, name: str) -> str:
        # Quoted with no safe characters, so a key or name containing a slash
        # addresses one path segment instead of escaping the endpoint.
        return (
            f"{self._endpoint()}/{urllib.parse.quote(report_key, safe='')}"
            f"/{ARTIFACTS_PATH_SEGMENT}/{urllib.parse.quote(name, safe='')}"
        )

    def _request(self, url: str) -> urllib.request.Request:
        return urllib.request.Request(
            url,
            method="GET",
            headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
        )

    def _get_json(self, url: str) -> Mapping[str, Any]:
        body = self._get_bytes(url, limit=None, gone_ok=False)
        try:
            payload = json.loads((body or b"").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FeedError(f"{url}: response is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise FeedError(f"{url}: response is not a JSON object")
        return payload

    def _get_bytes(self, url: str, *, limit: int | None, gone_ok: bool = True) -> bytes | None:
        """One GET. None means the feed answered 410 and `gone_ok`.

        410 is the feed stating that this artifact was published and its bytes
        are gone: the report's L1+L2 set is no longer durable, which the importer
        records as a rejection with a reason. Every other failure raises
        instead — including 404, which here means the report key or the artifact
        name addresses nothing, a defect in what this client asked for rather
        than a fact about retention. Telling the two apart is what lets a pruned
        report be recorded as such while a wrong key stays loud. An unreachable
        or misbehaving feed likewise says nothing about a report's eligibility,
        and burying a good report on a transport hiccup is permanent.

        `limit` is what the record declared for this body, if anything. It may
        tighten the read and never widen it: the bound belongs to this client,
        because the declaring side is the one that may be lying.
        """

        cap = MAX_RESPONSE_BYTES if limit is None else min(limit, MAX_RESPONSE_BYTES)
        try:
            with self._open(self._request(url), timeout=self._timeout) as response:
                # One byte past the bound: a body that fills it is over the size
                # this client will accept, and the caller's size/hash check
                # reports the mismatch rather than this client buffering on.
                return response.read(cap + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 410 and gone_ok:
                return None
            raise FeedError(_http_message(url, exc.code, exc.reason)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise FeedError(f"orch-hub feed unreachable at {url}: {exc}") from exc


def feed_from_config(
    config: EvolutionConfig,
    *,
    environ: Mapping[str, str],
    opener: Opener | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> OrchHubFeed:
    """Build the client from `evolution/config.toml` plus the environment.

    The config names which variables hold the URL and the token; the values stay
    out of Git entirely (invariant 11 and the repository's credential rule).
    `environ` is passed in rather than read here so a caller can be tested
    without mutating the process environment.
    """

    url = (environ.get(config.source.url_env) or "").strip()
    token = (environ.get(config.source.token_env) or "").strip()
    missing = [name for name, value in ((config.source.url_env, url), (config.source.token_env, token)) if not value]
    if missing:
        raise FeedError(
            f"the orch-hub report feed is not configured: {' and '.join(missing)} "
            f"{'is' if len(missing) == 1 else 'are'} unset. Export both to import from orch-hub, or pass "
            "--feed-dir <path> to import from a local report bundle. The feed is published, so this is a "
            "configuration gap rather than a missing deliverable; --feed-dir remains the offline path for "
            "fixtures and replay."
        )
    return OrchHubFeed(url, token, config.source.report_feed_path, opener=opener, timeout=timeout)


def _import_record(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    """One orch-hub catalog entry as an `evaluation-import.schema.json` record.

    Total by construction: every field is copied or derived without judging
    whether the result is admissible, and an entry that is not even an object is
    handed on untouched. Validation belongs to the importer, which turns a
    missing or malformed field into a recorded rejection carrying a reason; a
    translation that raised here would take a whole page down over one bad
    entry, and one that skipped the entry would hide a rejection the ledger is
    supposed to carry.

    `schema_version` is stamped rather than read, because this function is what
    produces that shape — the feed has no such field, and the entry's own
    `evaluator.schema_version` is the evaluator's rubric version instead.
    """

    if not isinstance(entry, dict):
        return entry

    evaluator = entry.get("evaluator")
    evaluator = evaluator if isinstance(evaluator, dict) else {}
    # `archived` is how this feed says completed (module docstring). Read
    # through rather than asserted, so an entry that ever said otherwise still
    # meets the importer's `source-task-not-completed` gate.
    archived = entry.get("archived")

    return {
        "schema_version": IMPORT_RECORD_SCHEMA_VERSION,
        "report_key": entry.get("report_key"),
        "sequence": entry.get("seq"),
        "generated_at": entry.get("generated_ts"),
        "source": {
            # The slug, not the display name: pooling identity is (repo, task),
            # and the slug is the one orch-hub keeps stable across renames.
            "repo_id": entry.get("repo_slug"),
            "repo_name": entry.get("repo_name"),
            "task_id": entry.get("task_id"),
            "evaluation_id": entry.get("evaluation_id"),
            "archived": archived,
            "completed": archived,
        },
        "evaluator": {
            "backend": evaluator.get("backend"),
            "model": evaluator.get("model"),
            "schema_version": evaluator.get("schema_version"),
        },
        "artifacts": _artifact_manifest(entry.get("artifacts")),
        "provenance": _unstated_provenance(),
    }


def _artifact_manifest(published: Any) -> dict[str, Any]:
    """The entry's artifact list as the schema's object, keyed by role."""

    by_wire_name: dict[str, Mapping[str, Any]] = {}
    if isinstance(published, list):
        for item in published:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                # First wins, so a feed repeating a name translates the same way
                # on every run and the record's content hash stays stable.
                by_wire_name.setdefault(item["name"], item)

    manifest: dict[str, Any] = {}
    for name, wire_name in ARTIFACT_WIRE_NAMES.items():
        item = by_wire_name.get(wire_name)
        if item is None:
            # Left out rather than nulled: `missing-artifact` is the accurate
            # rejection, and it names which one.
            continue
        manifest[name] = {
            "sha256": item.get("sha256"),
            "size_bytes": item.get("size"),
            "media_type": ARTIFACT_MEDIA_TYPES[name],
        }
    return manifest


def _unstated_provenance() -> dict[str, Any]:
    """The provenance block the import schema requires, said as absent.

    orch-hub publishes none of these facts — its own provenance block describes
    runs and git history instead — so every field is null rather than guessed. A
    release assessment reading a null `effective_revision` places the cohort in
    the inconclusive path, which is the designed answer for a report whose
    provenance never said, and a fabricated revision would be worse than none.

    This is also where a published one would be read (module docstring): the
    evaluated target's deploy-lock `source_git_commit` and
    `canonical_payload_sha256`, under whatever names the feed gives them.
    """

    return {
        "runner_protocol_revision": None,
        "deploy_lock_hash": None,
        "config_revision": None,
        "effective_revision": None,
        "dev": _unstated_role(),
        "review": _unstated_role(),
    }


def _unstated_role() -> dict[str, Any]:
    return {"agent": None, "model": None, "effort": None, "profile": None}


def _checked_base_url(base_url: str) -> str:
    parts = urllib.parse.urlsplit(base_url.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise FeedError(f"orch-hub feed URL must be an http(s) URL with a host, got {base_url!r}")
    if parts.scheme == "http" and (parts.hostname or "") not in LOOPBACK_HOSTS:
        raise FeedError(
            f"refusing to send the orch-hub token in clear text to {parts.netloc}: use https, "
            "or a loopback host for a local feed"
        )
    if parts.query or parts.fragment:
        raise FeedError(f"orch-hub feed URL carries a query or fragment; give the base URL only, got {base_url!r}")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def _bounded(text: str) -> str:
    """Quote feed-controlled text in a message without letting it be the message."""

    text = text.strip()
    if len(text) <= MAX_QUOTED_CHARS:
        return text
    return f"{text[:MAX_QUOTED_CHARS]}... ({len(text)} characters)"


def _http_message(url: str, code: int, reason: Any) -> str:
    if code in (401, 403):
        return (
            f"orch-hub rejected the request to {url} (HTTP {code}); check the token in the configured "
            "token environment variable and that it grants report-feed access"
        )
    if code == 400:
        return (
            f"orch-hub refused the watermark in {url} (HTTP 400); it pages by a non-negative integer sent as "
            "'after' and refuses anything else rather than resetting to the start of the feed (which re-imports "
            "everything) or to the end (which loses reports)"
        )
    if code == 404:
        return (
            f"orch-hub has nothing at {url} (HTTP 404): the endpoint, the report key, or the artifact name is "
            "unknown, or its evaluation subsystem is disabled. A published artifact whose bytes were pruned "
            "answers 410, so this is not the retention case; check the feed URL and [source].report_feed_path "
            "in evolution/config.toml"
        )
    if code == 409:
        return (
            f"orch-hub cannot address the artifacts for {url} (HTTP 409): the stored catalog identity is "
            "incoherent, so the report was published but its own entry cannot locate it — no retry heals this"
        )
    return f"orch-hub returned HTTP {code} ({reason}) for {url}"
