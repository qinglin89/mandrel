"""The protected orch-hub report feed, as an HTTP client.

orch-hub owns report publication and the global feed; this repository owns what
it does with the reports. That feed is not published yet, so this module states
the contract this build expects and holds itself to it — a client written
against an unstated shape would fail later as a confusing parse error instead of
now as a mismatch someone can act on.

**Wire contract.**

```text
GET  <ORCH_HUB_URL><source.report_feed_path>?limit=<n>[&cursor=<opaque>]
     Authorization: Bearer <ORCH_HUB_TOKEN>
     -> 200 {"items": [<import record>, ...],
             "next_cursor": <string|null>,
             "exhausted": <bool>}

GET  <ORCH_HUB_URL><source.report_feed_path>/<report_key>/artifacts/<name>
     Authorization: Bearer <ORCH_HUB_TOKEN>
     -> 200 <artifact bytes>   404 the feed does not serve this artifact
```

Each item is one `evaluation-import.schema.json` record; this module does not
validate them, because deciding what a record is worth is the importer's job and
a transport that silently dropped a malformed record would hide a rejection the
ledger is supposed to carry.

Artifact URLs are *derived* from the record rather than carried in it: the
import schema pins the artifact object to `sha256`/`size_bytes`/`media_type`
with `additionalProperties: false`, so a per-artifact href is not representable.
Derivation is therefore the contract, not a shortcut.

**`exhausted` is required.** It is what authorizes a later, separate `freeze` to
treat the pool as the whole eligible set, so it is read from the feed and never
inferred from an empty page or a null cursor. A feed that omits it gets a
refusal, not a guess.

**Not ready.** Until orch-hub publishes the feed there is nothing to point at.
`feed_from_config` says so in one actionable sentence naming both environment
variables and the `--feed-dir` alternative, rather than failing later inside a
socket call.

**Credentials.** The token travels in a header, never in a URL or a log line,
and never appears in an error message. Plain `http` is refused for anything but
a loopback host: a bearer token on the wire in clear text is a leak that no
later care can undo, and a local orch-hub is the only case where it is not. For
the same reason no redirect is followed — checking the configured URL says
nothing about where a `Location` header would send the token.

**Bounds.** Every response body is read under a limit this client owns rather
than one the feed declares, so a corrupt or hostile feed cannot make the tool
buffer until it dies.
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
            query["cursor"] = cursor
        payload = self._get_json(f"{self._endpoint()}?{urllib.parse.urlencode(query)}")

        items = payload.get("items")
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise FeedError(f"{self._endpoint()}: 'items' must be a list of report objects")
        exhausted = payload.get("exhausted")
        if not isinstance(exhausted, bool):
            raise FeedError(
                f"{self._endpoint()}: 'exhausted' must be a boolean saying whether the feed ended with this page; "
                "it decides whether a later freeze may treat the pool as the whole eligible set, so it is never inferred"
            )
        next_cursor = payload.get("next_cursor")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise FeedError(f"{self._endpoint()}: 'next_cursor' must be a string or null")

        return FeedPage(
            items=tuple(items),
            # A null cursor leaves discovery where it was. Reading it as "start
            # over" would rewind past reports already inspected and re-import the
            # feed from the beginning on every drained run.
            cursor=next_cursor if next_cursor is not None else cursor,
            exhausted=exhausted,
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
            declared = meta.get("size_bytes") if isinstance(meta, dict) else None
            body = self._get_bytes(
                self._artifact_url(report_key, str(name)),
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
        body = self._get_bytes(url, limit=None, missing_ok=False)
        try:
            payload = json.loads((body or b"").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FeedError(f"{url}: response is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise FeedError(f"{url}: response is not a JSON object")
        return payload

    def _get_bytes(self, url: str, *, limit: int | None, missing_ok: bool = True) -> bytes | None:
        """One GET. None means the feed answered 404 and `missing_ok`.

        A 404 for an artifact is the feed stating it does not serve that body:
        the report's L1+L2 set is not durable, which the importer records as a
        rejection with a reason. Every other failure raises instead — an
        unreachable or misbehaving feed says nothing about a report's
        eligibility, and burying a good report on a transport hiccup is
        permanent.

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
            if exc.code == 404 and missing_ok:
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
            "--feed-dir <path> to import from a local report bundle. orch-hub's global feed is a separate "
            "deliverable; until it is published, --feed-dir is the supported path."
        )
    return OrchHubFeed(url, token, config.source.report_feed_path, opener=opener, timeout=timeout)


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
    if code == 404:
        return (
            f"orch-hub has no endpoint at {url} (HTTP 404); check the feed URL and "
            "[source].report_feed_path in evolution/config.toml"
        )
    return f"orch-hub returned HTTP {code} ({reason}) for {url}"
