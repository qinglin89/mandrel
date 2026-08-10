"""The operator surface: CLI adapters, the orch-hub client, and the derived phase.

Everything runs against a temporary repository. The orch-hub feed does not exist
yet, so the client is exercised through an injected opener that answers the wire
contract `hub.py` states, which is the only way to test a client written against
an API that has not shipped.

Two tests are the exception and bind a loopback server: what `urllib` does with
a redirect — whose headers it copies, and to whom — is a property of the real
handler chain, and an injected opener replaces exactly the code under test. They
use `127.0.0.1` and an ephemeral port; nothing leaves the machine.

The temporary config lowers `target_task_count` to 2 and `minimum_task_count` to
1 so a full flow fits in two reports; `test_evolution_controller` asserts the
shipped file's real numbers load.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import threading
import urllib.error
import urllib.parse
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from evolution_fixtures import ARTIFACT_BODIES, git_repo, make_record, make_repo, snapshot, write_feed

from ai_native_deployment import cli, evolution
from ai_native_deployment.evolution import (
    analysis_task,
    batches,
    hub,
    importer,
    phase,
    render,
    reports,
    revisions,
)

TARGET = 2
MINIMUM = 1

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
BASE_URL = "https://orch-hub.example"
TOKEN = "s3cret-token"


# --- fixtures ----------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = make_repo(tmp_path)
    path = root / "evolution" / "config.toml"
    text = path.read_text(encoding="utf-8")
    for old, new in (
        ("target_task_count = 20", f"target_task_count = {TARGET}"),
        ("minimum_task_count = 10", f"minimum_task_count = {MINIMUM}"),
    ):
        assert old in text
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")
    return root


@pytest.fixture
def config(repo: Path) -> evolution.EvolutionConfig:
    return evolution.load_config(repo)


@pytest.fixture
def feed_root(tmp_path: Path) -> Path:
    return tmp_path / "feed"


@pytest.fixture(autouse=True)
def no_ambient_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's own orch-hub credentials must not decide what these tests
    exercise."""

    monkeypatch.delenv("ORCH_HUB_URL", raising=False)
    monkeypatch.delenv("ORCH_HUB_TOKEN", raising=False)


def records(count: int) -> list[dict]:
    return [
        make_record(key=f"r{index}", sequence=index, task_id=f"2026-07-{index:02d}-task")
        for index in range(1, count + 1)
    ]


def fill_pool(config: evolution.EvolutionConfig, feed_root: Path, count: int):
    feed = write_feed(feed_root, records(count))
    evolution.sync(config, feed)
    return feed


def freeze(config: evolution.EvolutionConfig, **kwargs) -> batches.FreezeResult:
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("runner_revision", "v2.2.0")
    return evolution.freeze(config, **kwargs)


def record_findings(config: evolution.EvolutionConfig, batch_id: str) -> Path:
    path = config.batches_root / batch_id / batches.FINDINGS_FILENAME
    path.write_text("# Findings\n\nNo protocol change justified.\n", encoding="utf-8")
    return path


def complete_analysis_task(config: evolution.EvolutionConfig, task_id: str) -> None:
    path = analysis_task.task_path(config, task_id)
    text = path.read_text(encoding="utf-8").replace("status: pending", "status: completed", 1)
    path.write_text(text, encoding="utf-8")


def close_batch(config: evolution.EvolutionConfig, batch_id: str, task_id: str) -> None:
    """Close a batch the way the contract does: dispositions committed, the
    analysis task completed, and the next controller run publishing the closure
    record from that status."""

    record_findings(config, batch_id)
    complete_analysis_task(config, task_id)
    freeze(config)
    assert (config.batches_root / batch_id / batches.CLOSURE_FILENAME).is_file()


def draft(config: evolution.EvolutionConfig, batch_id: str, name: str) -> Path:
    """A change-task draft, as an analysis session writes one: inert until a
    human moves it into `.ai-tasks/` (contract: Change admission)."""

    path = config.batches_root / batch_id / analysis_task.PROPOSED_TASKS_DIRNAME / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: {Path(name).stem}\nstatus: pending\n---\n\n# Proposal\n\nEvidence: {batch_id}.\n",
        encoding="utf-8",
    )
    return path


def admit(config: evolution.EvolutionConfig, path: Path) -> Path:
    """The human admission gate: move the draft into the active pool."""

    target = analysis_task.tasks_root(config) / path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.unlink()
    return target


def run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --- revisions ---------------------------------------------------------------


def test_both_revisions_are_reported_when_work_sits_on_top_of_a_release(tmp_path: Path) -> None:
    """The baseline is the release line; the tip on top of it is the candidate
    (invariants 8 and 10)."""
    root = git_repo(tmp_path / "tagged", tag="v2.2.0")

    pair = revisions.describe_revisions(root)

    assert pair.baseline is not None and pair.baseline.ref == "v2.2.0"
    assert pair.candidate is not None and pair.candidate.sha != pair.baseline.sha
    assert pair.candidate.ref not in (None, "HEAD")


def test_a_checkout_sitting_on_the_release_line_has_no_candidate(tmp_path: Path) -> None:
    root = git_repo(tmp_path / "at-release", tag=None)
    subprocess.run(["git", "-C", str(root), "tag", "v1.0.0"], check=True)

    pair = revisions.describe_revisions(root)

    assert pair.baseline is not None and pair.baseline.ref == "v1.0.0"
    assert pair.candidate is None


def test_an_untagged_checkout_has_a_candidate_but_no_baseline(tmp_path: Path) -> None:
    """Nothing to measure against is a fact about the repository, not a reason
    to hide what a run would execute."""
    pair = revisions.describe_revisions(git_repo(tmp_path / "untagged", tag=None))

    assert pair.baseline is None
    assert pair.candidate is not None


def test_a_directory_inside_another_repository_reports_no_revisions(tmp_path: Path) -> None:
    """Otherwise a temporary directory under someone's checkout silently
    acquires that checkout's baseline and tip."""
    inner = git_repo(tmp_path / "outer", tag="v9.9.9") / "nested"
    inner.mkdir()

    assert revisions.describe_revisions(inner) == revisions.Revisions()


# --- lifecycle phase ---------------------------------------------------------


def test_an_untouched_workspace_is_idle(config: evolution.EvolutionConfig) -> None:
    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_IDLE
    assert status.summary == "idle"
    assert status.decision.task_count == 0
    assert status.pool_complete is False


def test_a_staged_pool_reports_its_count_against_the_target(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, 1)

    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_POOL
    assert status.summary == f"pool 1/{TARGET}"
    assert status.pool_complete is True


def test_a_pool_left_as_a_prefix_reports_completeness_unproven(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """A page bound makes the count a prefix of the feed, not a denominator
    (invariants 1 and 2) — and the phase has to say so, because the number alone
    looks the same."""
    feed = write_feed(feed_root, records(3))
    evolution.sync(config, feed, page_size=1, max_pages=1)

    status = phase.describe(config, now=NOW)

    assert status.pool_complete is False
    assert status.decision.reason == batches.REASON_POOL_INCOMPLETE
    assert "completeness unproven" in render.format_status(status)


def test_a_frozen_batch_holds_the_lifecycle_at_batch_frozen(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)

    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_BATCH_FROZEN
    assert status.open_batch is not None
    assert status.open_batch.batch_id == result.batch_id
    assert status.open_batch.findings_recorded is False
    assert status.open_batch.evidence_local == status.open_batch.report_count
    assert status.decision.reason == batches.REASON_OPEN_BATCH


def test_recorded_dispositions_move_the_phase_without_closing_the_batch(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """`findings.md` is the disposition record and closes nothing on its own; the
    phase distinguishes the two so an operator can see analysis in progress."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    record_findings(config, result.batch_id or "")

    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_DISPOSITIONS_READY
    assert status.open_batch is not None and status.open_batch.batch_id == result.batch_id


def test_drafts_left_by_a_closed_analysis_are_the_admission_gate(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    close_batch(config, result.batch_id or "", result.analysis_task_id or "")
    draft(config, result.batch_id or "", "2026-08-02-tighten-contract.md")

    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_PROPOSALS_PENDING
    assert status.summary == "proposals-pending (1 draft)"
    assert status.proposals[0].drafts == ("2026-08-02-tighten-contract.md",)
    assert status.open_batch is None


def test_an_admitted_change_task_reports_implementing(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Admission moves the draft into `.ai-tasks/`, and the task cites its batch
    — the citation the contract's task requirements already demand."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    close_batch(config, result.batch_id or "", result.analysis_task_id or "")
    admit(config, draft(config, result.batch_id or "", "2026-08-02-tighten-contract.md"))

    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_IMPLEMENTING
    assert status.implementation_tasks == ("2026-08-02-tighten-contract",)
    assert not status.proposals


def test_a_completed_change_task_no_longer_counts_as_implementing(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    close_batch(config, result.batch_id or "", result.analysis_task_id or "")
    admitted = admit(config, draft(config, result.batch_id or "", "2026-08-02-tighten-contract.md"))
    admitted.write_text(
        admitted.read_text(encoding="utf-8").replace("status: pending", "status: completed"), encoding="utf-8"
    )

    status = phase.describe(config, now=NOW)

    assert status.implementation_tasks == ()
    assert status.phase == phase.PHASE_IDLE


def test_the_batch_analysis_task_is_not_counted_as_an_implementation(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """It cites its batch by construction; counting it would report
    `implementing` for a batch that is merely being analyzed."""
    fill_pool(config, feed_root, TARGET)
    freeze(config)

    status = phase.describe(config, now=NOW)

    assert status.implementation_tasks == ()


def test_a_batch_whose_evidence_was_staged_elsewhere_is_shown_not_failed(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """A frozen cohort owns its reports wherever it was frozen; `.ai-evolution/`
    is machine-local, so a clone holds the manifest and none of the bundles."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    shutil.rmtree(config.artifacts_root)

    status = phase.describe(config, now=NOW)

    assert status.open_batch is not None
    assert status.open_batch.evidence_local == 0
    assert "evidence on this machine: 0/" in render.format_status(status)
    assert result.batch_id in render.format_status(status)


def test_status_writes_nothing(config: evolution.EvolutionConfig, feed_root: Path, repo: Path) -> None:
    fill_pool(config, feed_root, TARGET)
    freeze(config)
    before = snapshot(repo)

    phase.describe(config, now=NOW)

    assert snapshot(repo) == before


def test_the_status_json_carries_the_phase_and_both_revisions(
    tmp_path: Path, feed_root: Path
) -> None:
    root = make_repo(tmp_path)
    git_repo(root, tag="v2.2.0")
    config = evolution.load_config(root)
    fill_pool(config, feed_root, 1)

    payload = phase.describe(config, now=NOW).to_json()

    assert payload["schema_version"] == phase.SCHEMA_VERSION
    assert payload["phase"] == phase.PHASE_POOL
    assert payload["pool"] == {
        "task_count": 1,
        "target": 20,
        "minimum": 10,
        "complete": True,
        "oldest_pending_at": payload["pool"]["oldest_pending_at"],
        "waited_days": payload["pool"]["waited_days"],
        "max_wait_days": 30,
    }
    assert payload["revisions"]["baseline"]["ref"] == "v2.2.0"
    assert payload["revisions"]["candidate"]["sha"] != payload["revisions"]["baseline"]["sha"]
    assert json.loads(json.dumps(payload)) == payload


# --- orch-hub client ---------------------------------------------------------


class FakeResponse(io.BytesIO):
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def hub_feed(routes: dict[str, object], *, seen: list | None = None) -> hub.OrchHubFeed:
    """A client wired to canned responses, keyed by URL.

    A route value may be bytes (a 200 body) or an exception to raise, which is
    how the transport-failure paths are exercised without a socket.
    """

    def opener(request, timeout=None):
        if seen is not None:
            seen.append(request)
        answer = routes.get(request.full_url)
        if answer is None:
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(answer)

    return hub.OrchHubFeed(BASE_URL, TOKEN, "/api/evaluation/reports", opener=opener)


def page_url(**query: str) -> str:
    return f"{BASE_URL}/api/evaluation/reports?{urllib.parse.urlencode(query)}"


def test_an_unset_feed_url_or_token_is_reported_as_not_ready(
    config: evolution.EvolutionConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """orch-hub's global feed is a separate deliverable; until it lands the
    message has to name both variables and the offline path."""
    with pytest.raises(evolution.FeedError) as excinfo:
        hub.feed_from_config(config, environ={})

    message = str(excinfo.value)
    assert "ORCH_HUB_URL" in message and "ORCH_HUB_TOKEN" in message
    assert "--feed-dir" in message


def test_only_the_missing_variable_is_named(config: evolution.EvolutionConfig) -> None:
    with pytest.raises(evolution.FeedError) as excinfo:
        hub.feed_from_config(config, environ={"ORCH_HUB_URL": BASE_URL})

    assert "ORCH_HUB_TOKEN is unset" in str(excinfo.value)


def test_the_token_travels_in_a_header_and_never_in_the_url(config: evolution.EvolutionConfig) -> None:
    seen: list = []
    body = json.dumps({"items": [], "next_cursor": None, "exhausted": True}).encode("utf-8")
    feed = hub_feed({page_url(limit="10"): body}, seen=seen)

    feed.fetch_page(None, 10)

    assert seen[0].get_header("Authorization") == f"Bearer {TOKEN}"
    assert TOKEN not in seen[0].full_url


def test_a_page_carries_its_items_cursor_and_exhaustion(config: evolution.EvolutionConfig) -> None:
    record = make_record(key="r1", sequence=1)
    body = json.dumps({"items": [record], "next_cursor": "c1", "exhausted": False}).encode("utf-8")
    feed = hub_feed({page_url(limit="5"): body})

    page = feed.fetch_page(None, 5)

    assert page.items == (record,)
    assert page.cursor == "c1"
    assert page.exhausted is False


def test_a_page_without_exhaustion_is_refused(config: evolution.EvolutionConfig) -> None:
    """It authorizes a later freeze to treat the pool as the whole eligible set,
    so it is read from the feed, never inferred."""
    body = json.dumps({"items": [], "next_cursor": None}).encode("utf-8")
    feed = hub_feed({page_url(limit="5"): body})

    with pytest.raises(evolution.FeedError, match="exhausted"):
        feed.fetch_page(None, 5)


def test_a_null_next_cursor_leaves_discovery_where_it_was(config: evolution.EvolutionConfig) -> None:
    """Reading it as "start over" would re-import the feed from the beginning on
    every drained run."""
    body = json.dumps({"items": [], "next_cursor": None, "exhausted": True}).encode("utf-8")
    feed = hub_feed({page_url(limit="5", cursor="c9"): body})

    assert feed.fetch_page("c9", 5).cursor == "c9"


def test_an_artifact_the_feed_does_not_serve_is_absent_rather_than_fatal(
    config: evolution.EvolutionConfig,
) -> None:
    """A 404 is the feed stating the body is not there: the L1+L2 set is not
    durable, which the importer records as a rejection with a reason."""
    record = make_record(key="r1", sequence=1)
    routes = {
        f"{BASE_URL}/api/evaluation/reports/r1/artifacts/{name}": body
        for name, body in list(ARTIFACT_BODIES.items())[:3]
    }
    feed = hub_feed(routes)

    blobs = feed.fetch_artifacts(record)

    assert set(blobs) == set(list(ARTIFACT_BODIES)[:3])


def test_a_transport_failure_fetching_an_artifact_raises(config: evolution.EvolutionConfig) -> None:
    """An unreachable feed says nothing about a report's eligibility, and
    recording it as rejected would bury a good report permanently."""
    record = make_record(key="r1", sequence=1)
    feed = hub_feed(
        {
            f"{BASE_URL}/api/evaluation/reports/r1/artifacts/{name}": urllib.error.URLError("connection reset")
            for name in ARTIFACT_BODIES
        }
    )

    with pytest.raises(evolution.FeedError, match="unreachable"):
        feed.fetch_artifacts(record)


def test_rejected_credentials_name_the_token_variable(config: evolution.EvolutionConfig) -> None:
    feed = hub_feed({page_url(limit="5"): urllib.error.HTTPError(page_url(limit="5"), 401, "Unauthorized", {}, None)})

    with pytest.raises(evolution.FeedError, match="token"):
        feed.fetch_page(None, 5)


def test_a_report_key_with_a_slash_addresses_one_path_segment(config: evolution.EvolutionConfig) -> None:
    """Otherwise a foreign key escapes the endpoint, the way it must never
    become a path component locally either."""
    seen: list = []
    feed = hub_feed({}, seen=seen)

    feed.fetch_artifacts({"report_key": "a/../b", "artifacts": {"evidence": {"size_bytes": 1}}})

    assert seen[0].full_url.endswith("/reports/a%2F..%2Fb/artifacts/evidence")


def test_a_body_larger_than_declared_is_bounded_and_then_rejected(
    config: evolution.EvolutionConfig,
) -> None:
    """The client stops reading at one byte past the declared size; the
    importer's hash check is what turns that into a rejection."""
    oversized = b"x" * 5000
    record = make_record(key="r1", sequence=1)
    declared = record["artifacts"]["evidence"]["size_bytes"]
    feed = hub_feed({f"{BASE_URL}/api/evaluation/reports/r1/artifacts/evidence": oversized})

    blobs = feed.fetch_artifacts(record)

    assert len(blobs["evidence"]) == declared + 1


def test_a_declared_size_never_widens_the_clients_own_read_bound(config: evolution.EvolutionConfig) -> None:
    """`size_bytes` has a minimum and no maximum in the import schema, so a feed
    declaring a petabyte must not turn into a petabyte-sized read: the declaring
    side is the one that may be lying."""
    asked: list[int] = []

    class Recording(FakeResponse):
        def read(self, size: int = -1) -> bytes:
            asked.append(size)
            return super().read(size)

    def opener(request: object, timeout: float | None = None) -> Recording:
        return Recording(b"x" * 8)

    record = make_record(key="r1", sequence=1)
    record["artifacts"]["evidence"]["size_bytes"] = 10**15
    feed = hub.OrchHubFeed(BASE_URL, TOKEN, "/api/evaluation/reports", opener=opener)

    feed.fetch_artifacts(record)

    assert max(asked) == hub.MAX_RESPONSE_BYTES + 1


def test_a_body_over_the_clients_limit_is_rejected_not_quietly_shortened(
    config: evolution.EvolutionConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap stops the read; the importer's size check is what stops the
    truncated body reaching the pool as a short artifact nobody declared."""
    monkeypatch.setattr(hub, "MAX_RESPONSE_BYTES", 2048)
    bodies = dict(ARTIFACT_BODIES, evidence=b"L" * 4096)
    record = make_record(key="r1", sequence=1, bodies=bodies)
    routes: dict[str, object] = {
        page_url(limit="50"): json.dumps({"items": [record], "next_cursor": "c1", "exhausted": True}).encode("utf-8")
    }
    routes.update({f"{BASE_URL}/api/evaluation/reports/r1/artifacts/{name}": body for name, body in bodies.items()})

    result = importer.sync(config, hub_feed(routes))

    assert result.imported == ()
    assert result.rejected == (("r1", reports.REASON_ARTIFACT_HASH_MISMATCH),)


def test_a_plaintext_url_to_a_remote_host_is_refused(config: evolution.EvolutionConfig) -> None:
    """A bearer token on the wire in clear text is a leak no later care undoes."""
    with pytest.raises(evolution.FeedError, match="clear text"):
        hub.feed_from_config(config, environ={"ORCH_HUB_URL": "http://orch-hub.example", "ORCH_HUB_TOKEN": TOKEN})


def test_a_local_plaintext_feed_is_allowed(config: evolution.EvolutionConfig) -> None:
    feed = hub.feed_from_config(config, environ={"ORCH_HUB_URL": "http://localhost:8080/", "ORCH_HUB_TOKEN": TOKEN})

    assert feed.base_url == "http://localhost:8080"


def test_a_url_that_is_not_http_is_refused(config: evolution.EvolutionConfig) -> None:
    with pytest.raises(evolution.FeedError, match="http"):
        hub.feed_from_config(config, environ={"ORCH_HUB_URL": "file:///etc", "ORCH_HUB_TOKEN": TOKEN})


Responder = Callable[[str], tuple[int, dict[str, str], bytes]]


@contextlib.contextmanager
def loopback_server(responder: Responder) -> Iterator[tuple[str, list[tuple[str, dict[str, str]]]]]:
    """A throwaway server on 127.0.0.1, with the list of requests it received.

    The two redirect tests need the real `urllib` handler chain: an injected
    opener would replace the code that decides where the token goes.
    """

    received: list[tuple[str, dict[str, str]]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            received.append((self.path, {key.lower(): value for key, value in self.headers.items()}))
            status, headers, body = responder(self.path)
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            """Silence the default stderr logging; the assertions are the output."""

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        server.shutdown()
        server.server_close()


def test_a_cross_origin_redirect_never_receives_the_token() -> None:
    """`urllib`'s default handler copies the request headers — `Authorization`
    among them — onto whichever destination answered with a `Location`, so
    checking the configured URL proves nothing about where the token lands."""
    drained = (200, {"Content-Type": "application/json"}, b'{"items": [], "next_cursor": null, "exhausted": true}')
    with loopback_server(lambda path: drained) as (elsewhere, elsewhere_received):
        with loopback_server(lambda path: (302, {"Location": f"{elsewhere}/feed"}, b"")) as (base, _):
            feed = hub.OrchHubFeed(base, TOKEN, "/api/evaluation/reports")

            with pytest.raises(evolution.FeedError, match="redirected") as excinfo:
                feed.fetch_page(None, 10)

    assert elsewhere_received == []
    assert TOKEN not in str(excinfo.value)


def test_a_same_origin_redirect_is_refused_as_well() -> None:
    """The rule is "no redirects", not an origin comparison — there is no
    same-origin test to get subtly wrong, and a chain that is same-origin at
    every hop still ends wherever the last one points."""
    with loopback_server(lambda path: (302, {"Location": "/moved"}, b"")) as (base, received):
        feed = hub.OrchHubFeed(base, TOKEN, "/api/evaluation/reports")

        with pytest.raises(evolution.FeedError, match="redirected"):
            feed.fetch_page(None, 10)

    assert len(received) == 1


def test_the_hub_client_imports_a_report_end_to_end(config: evolution.EvolutionConfig) -> None:
    """The client and the importer meet only at `ReportFeed`, so this is the one
    test that proves the pair works together."""
    record = make_record(key="r1", sequence=1)
    routes: dict[str, object] = {
        page_url(limit="50"): json.dumps({"items": [record], "next_cursor": "c1", "exhausted": True}).encode("utf-8")
    }
    routes.update(
        {f"{BASE_URL}/api/evaluation/reports/r1/artifacts/{name}": body for name, body in ARTIFACT_BODIES.items()}
    )

    result = importer.sync(config, hub_feed(routes))

    assert result.imported == ("r1",)
    assert result.exhausted is True


# --- CLI ---------------------------------------------------------------------


def test_status_prints_the_phase(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_repo(tmp_path)
    git_repo(root, tag="v2.2.0")

    code, out, _ = run(["evolution", "status", "--repo", str(root)], capsys)

    assert code == 0
    assert out.startswith("evolution: idle")
    assert "baseline" in out and "candidate" in out


def test_status_says_a_plain_directory_has_no_revisions_at_all(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two absent revisions and no repository are different facts; reporting
    "at the release line" for a directory with no release line is a lie."""
    code, out, _ = run(["evolution", "status", "--repo", str(repo)], capsys)

    assert code == 0
    assert "revisions    none — not a git work tree root" in out
    assert "baseline" not in out


def test_status_json_is_the_same_shape(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(["evolution", "status", "--repo", str(repo), "--json"], capsys)

    assert code == 0
    assert json.loads(out)["phase"] == phase.PHASE_IDLE


def test_list_inspects_the_feed_and_writes_nothing(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_feed(feed_root, records(2))
    before = snapshot(repo)

    code, out, _ = run(["evolution", "list", "--repo", str(repo), "--feed-dir", str(feed_root)], capsys)

    assert code == 0
    assert "2 report(s), 2 unique completed task(s)" in out
    assert "nothing was written" in out
    assert snapshot(repo) == before


def test_sync_imports_and_reports_the_pool(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_feed(feed_root, records(2))

    code, out, _ = run(["evolution", "sync", "--repo", str(repo), "--feed-dir", str(feed_root)], capsys)

    assert code == 0
    assert "imported" in out and "2 unique completed task(s) pending" in out
    assert evolution.load_state(evolution.load_config(repo)).pending


def test_start_runs_the_whole_flow_to_one_pending_analysis_task(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The acceptance end to end: an empty repository, a fixture feed, and one
    command reaching a frozen cohort with its pending analysis task."""
    write_feed(feed_root, records(TARGET))

    code, out, _ = run(["evolution", "start", "--repo", str(repo), "--feed-dir", str(feed_root)], capsys)

    config = evolution.load_config(repo)
    frozen = evolution.load_batches(config)
    assert code == 0
    assert len(frozen) == 1
    assert "frozen" in out and frozen[0].batch_id in out

    task_id = frozen[0].analysis_task_id or ""
    task = analysis_task.task_path(config, task_id).read_text(encoding="utf-8")
    assert "status: pending" in task
    assert analysis_task.CONTRACT_PATH in task
    assert frozen[0].batch_id in task
    assert analysis_task.PROPOSED_TASKS_DIRNAME in task
    assert f"| {task_id} " in analysis_task.index_path(config).read_text(encoding="utf-8")


def test_a_second_start_creates_no_second_batch(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_feed(feed_root, records(TARGET))
    run(["evolution", "start", "--repo", str(repo), "--feed-dir", str(feed_root)], capsys)

    code, out, _ = run(["evolution", "start", "--repo", str(repo), "--feed-dir", str(feed_root)], capsys)

    assert code == 0
    assert "still open for analysis" in out
    assert len(evolution.load_batches(evolution.load_config(repo))) == 1


def test_start_below_the_target_forms_no_batch_and_says_why(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Too little evidence is the contract's normal outcome, so the run
    succeeds and reports the reason."""
    write_feed(feed_root, records(1))

    code, out, _ = run(["evolution", "start", "--repo", str(repo), "--feed-dir", str(feed_root)], capsys)

    assert code == 0
    assert f"no batch — {batches.REASON_BELOW_TARGET}" in out
    assert evolution.load_batches(evolution.load_config(repo)) == []


def test_force_without_a_justification_is_refused(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_feed(feed_root, records(1))

    code, _, err = run(
        ["evolution", "start", "--repo", str(repo), "--feed-dir", str(feed_root), "--force"], capsys
    )

    assert code == 2
    assert "justification" in err
    assert evolution.load_batches(evolution.load_config(repo)) == []


def test_a_justified_force_freezes_a_below_target_batch(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_feed(feed_root, records(1))

    code, out, _ = run(
        [
            "evolution",
            "start",
            "--repo",
            str(repo),
            "--feed-dir",
            str(feed_root),
            "--force",
            "--justification",
            "Severe correctness failure; escalated by the maintainer.",
        ],
        capsys,
    )

    config = evolution.load_config(repo)
    manifest = json.loads(evolution.load_batches(config)[0].manifest_path.read_text(encoding="utf-8"))
    assert code == 0
    assert batches.TRIGGER_FORCED in out
    assert manifest["forced"] is True
    assert "escalated by the maintainer" in manifest["force_justification"].lower()


def test_a_run_without_a_feed_or_credentials_is_actionable(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run(["evolution", "sync", "--repo", str(repo)], capsys)

    assert code == 2
    assert "ORCH_HUB_URL" in err and "--feed-dir" in err


def test_a_missing_workspace_config_is_actionable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    empty = tmp_path / "elsewhere"
    empty.mkdir()

    code, _, err = run(["evolution", "status", "--repo", str(empty)], capsys)

    assert code == 2
    assert "missing evolution config" in err


def test_an_unavailable_feed_is_reported_without_touching_the_pool(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unreachable source says nothing about any report's eligibility, so the
    run stops instead of recording an empty discovery."""
    config = evolution.load_config(repo)
    before = snapshot(repo)

    code, _, err = run(
        ["evolution", "sync", "--repo", str(repo), "--feed-dir", str(tmp_path / "absent")], capsys
    )

    assert code == 2
    assert "reports/" in err
    assert snapshot(repo) == before
    assert not config.state_path.exists()


def test_a_cursor_the_feed_never_issued_is_refused(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reinterpreting an unrecognised cursor is how discovery silently skips
    reports it never inspected."""
    config = evolution.load_config(repo)
    fill_pool(config, feed_root, 1)
    persisted = evolution.load_state(config)
    persisted.cursor = "not-a-position"
    evolution.save_state(config, persisted)

    code, _, err = run(["evolution", "sync", "--repo", str(repo), "--feed-dir", str(feed_root)], capsys)

    assert code == 2
    assert "invalid cursor" in err


def test_corrupt_runtime_state_is_reported_not_reset(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = evolution.load_config(repo)
    fill_pool(config, feed_root, 1)
    config.state_path.write_text('{"schema_version": 2}', encoding="utf-8")

    code, _, err = run(["evolution", "status", "--repo", str(repo)], capsys)

    assert code == 2
    assert "incomplete state" in err


def test_lock_contention_is_reported_with_its_holder(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = evolution.load_config(repo)
    write_feed(feed_root, records(1))
    config.lock_path.parent.mkdir(parents=True, exist_ok=True)
    config.lock_path.write_text(
        json.dumps({"pid": 4242, "host": "another-host", "acquired_at": "2026-08-01T11:00:00Z"}), encoding="utf-8"
    )

    code, _, err = run(["evolution", "sync", "--repo", str(repo), "--feed-dir", str(feed_root)], capsys)

    assert code == 2
    assert "4242" in err and "another-host" in err
    assert "remove it if no run is active" in err
