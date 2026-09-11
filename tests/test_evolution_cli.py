"""The operator surface: CLI adapters, the orch-hub client, and the derived phase.

Everything runs against a temporary repository. The client is exercised through
an injected opener answering the wire contract orch-hub publishes — the catalog
entry it really serves, not the import record this repository would have
preferred — so the translation `hub.py` performs is under test rather than
assumed. The one thing no injected opener can prove is that the live service
still answers that way; `scripts/probe-orch-hub.sh` is the credentialed check
for that, and it stays off the required gate.

Two tests are the exception and bind a loopback server: what `urllib` does with
a redirect — whose headers it copies, and to whom — is a property of the real
handler chain, and an injected opener replaces exactly the code under test. They
use `127.0.0.1` and an ephemeral port; nothing leaves the machine.

The temporary config lowers `target_task_count` to 2 and `minimum_task_count` to
1 so a full flow fits in two reports; `test_evolution_controller` asserts the
shipped file's real numbers load.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import io
import json
import os
import re
import shlex
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
from evolution_fixtures import (
    line_tip,
    at_line,
    ARTIFACT_BODIES,
    HUB_ARTIFACT_NAMES,
    HUB_LOCK_HASH,
    HUB_PROTOCOL_LEGACY,
    HUB_REVISION,
    RELEASE_REF,
    REPO_ROOT,
    FakeHarness,
    admitted_task,
    complete_task,
    completed_report,
    experiment_decision,
    experiment_round,
    git_checkout,
    git_commit,
    git_follow,
    git_repo,
    git_rev,
    git_unrelated_commit,
    git_update_ref,
    hub_page,
    hub_protocol,
    make_hub_entry,
    make_manifest_report,
    make_record,
    make_repo,
    promote_candidate,
    promotion_of,
    rejection,
    releasing_from,
    snapshot,
    write_closure,
    write_draft,
    write_experiment,
    write_feed,
    write_manifest,
    write_outcome,
    write_rejected_drafts,
)

from mandrel import cli, evolution
from mandrel.evolution import (
    analysis_task,
    assessment,
    batches,
    deployment,
    errors,
    experiments,
    guards,
    harness,
    hub,
    importer,
    lineage,
    phase,
    proposals,
    render,
    reports,
    replay,
    revisions,
    rollback,
    state,
)

TARGET = 2
MINIMUM = 1

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
BASE_URL = "https://orch-hub.example"
TOKEN = "s3cret-token"

# The revisions an experiment record pins. Opaque here on purpose: the lineage
# is read from the records, so nothing about it depends on this checkout holding
# these objects (contract: What is derived).
BASE = "a" * 40
CANDIDATE = "b" * 40

# The two batches a release reading is about: the batch whose experiment was
# promoted, and the first one frozen after it. Batch ids are allocated, so these
# are what the allocator would produce rather than names of a test's choosing.
FIRST = "evolution-batch-0001"
SECOND = "evolution-batch-0002"


# --- fixtures ----------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    # Under Git with the configured source line present: a batch is formed at
    # that line's tip, so a workspace that cannot resolve it forms nothing.
    root = git_repo(make_repo(tmp_path), tag="v2.2.0")
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


def records(config: evolution.EvolutionConfig, count: int) -> list[dict]:
    """`count` distinct completed tasks, every one produced at this repository's
    source-line tip — the base a batch formed now selects its members by."""

    return at_line(
        config,
        [
            make_record(key=f"r{index}", sequence=index, task_id=f"2026-07-{index:02d}-task")
            for index in range(1, count + 1)
        ],
    )


def fill_pool(config: evolution.EvolutionConfig, feed_root: Path, count: int):
    feed = write_feed(feed_root, records(config, count))
    evolution.sync(config, feed)
    return feed


def freeze(config: evolution.EvolutionConfig, **kwargs) -> batches.FreezeResult:
    """Offer the batch, then fix it — the two commands a human runs.

    The formation is two operations, so a test that means "form a batch out of
    this pool" has to do both. Where the split itself is what is under test, the
    two are called apart.
    """

    kwargs.setdefault("now", NOW)
    evolution.build_proposal(config, now=kwargs["now"])
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


def draft(config: evolution.EvolutionConfig, batch_id: str, draft_id: str) -> Path:
    """A change-task draft, as an analysis session writes one: inert until a
    human admits it into an experiment (contract: Change admission)."""

    return write_draft(config.batches_root, batch_id, draft_id)


def experiment(
    config: evolution.EvolutionConfig,
    batch_id: str,
    *,
    ordinal: int = 1,
    rounds: list[dict] | None = None,
    decision: dict | None = None,
) -> Path:
    """One experiment record for `batch_id`, on that batch's frozen base.

    Read from the manifest rather than restated: the batch is formed at a commit
    and every experiment of it starts there (`lineage._require_one_base`), so a
    record naming anything else is a lineage no reading accepts.
    """

    return write_experiment(
        config.experiments_root,
        f"{batch_id}-exp-{ordinal:02d}",
        base_revision=formed_base(config, batch_id),
        rounds=rounds,
        decision=decision,
    )


def formed_base(config: evolution.EvolutionConfig, batch_id: str) -> str:
    """The commit a batch was formed at, read from its manifest."""

    manifest = json.loads((config.batches_root / batch_id / "manifest.json").read_text(encoding="utf-8"))
    return str(manifest["runner_protocol_revision"])


def copy_into_tasks(config: evolution.EvolutionConfig, path: Path) -> Path:
    """A draft copied into the active pool, as grouped admission does — the file
    alone, with no experiment record behind it."""

    target = analysis_task.tasks_root(config) / path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def exits(failure: type[evolution.EvolutionError]) -> int:
    """The status a failure of this class leaves the process at.

    Named through the class rather than written as a number, which is the whole
    point of the taxonomy: what a test pins is that this refusal is *this kind
    of thing*, and the number it exits with is the map's business. A family
    renumbered here moves every assertion with it; a refusal that quietly
    becomes a corrupt-state report does not, and that is the failure worth
    catching (`mandrel/evolution/errors.py`).
    """

    return cli.EVOLUTION_EXIT_CODES[failure.kind]


# --- lifecycle phase ---------------------------------------------------------


def _action(status, verb: str):
    """One verb out of the gate, by name."""

    return next(action for action in status.actions if action.action == verb)


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
    feed = write_feed(feed_root, records(config, 3))
    evolution.sync(config, feed, page_size=1, max_pages=1)

    status = phase.describe(config, now=NOW)

    assert status.pool_complete is False
    assert status.decision.reason == batches.REASON_POOL_INCOMPLETE
    assert "completeness unproven" in render.format_status(status)


def test_a_standing_offer_reports_the_batch_proposed_phase(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """The phase between the pool and the first manifest — the only one in which
    membership can still be argued with."""
    fill_pool(config, feed_root, TARGET)
    evolution.build_proposal(config, now=NOW)

    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_BATCH_PROPOSED
    assert status.summary == f"batch-proposed {TARGET}/{TARGET}"
    assert status.proposal is not None
    assert status.to_json()["proposal"]["task_count"] == TARGET
    assert status.to_json()["proposal"]["considered"] == TARGET
    assert status.to_json()["proposal"]["not_selected"] == {}
    text = render.format_status(status)
    assert "proposal" in text and f"{TARGET} of {TARGET} considered task(s) at base" in text


def test_the_freeze_verb_is_offered_only_once_a_batch_is_on_offer(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Two conditions, and they fail for different people: an offer has to exist
    for a human to have seen it, and admission has to release it."""
    fill_pool(config, feed_root, TARGET)

    before = _action(phase.describe(config, now=NOW), phase.ACTION_FREEZE)
    assert not before.allowed and "no batch proposal stands" in (before.refusal or "")

    evolution.build_proposal(config, now=NOW)

    after = _action(phase.describe(config, now=NOW), phase.ACTION_FREEZE)
    assert after.allowed and after.refusal is None


def test_a_below_target_offer_is_a_freeze_the_gate_allows(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """`--force` waives the configured target, so a short offer is a state the
    verb accepts — with a justification the operator writes.

    The gate says whether a verb may run at all and never which arguments it was
    given (contract: The gate), so modelling the force flag as state is exactly
    what it must not do: what it reports is that the operation reaches its
    writes from here, which it does.
    """

    fill_pool(config, feed_root, MINIMUM)
    evolution.build_proposal(config, now=NOW)

    action = _action(phase.describe(config, now=NOW), phase.ACTION_FREEZE)

    assert action.allowed and action.refusal is None
    frozen = evolution.freeze(config, now=NOW, forced=True, justification="the batch is complete")
    assert frozen.frozen and frozen.decision.trigger == batches.TRIGGER_FORCED


def test_a_shortfall_no_force_waives_is_the_refusal_admission_gives(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Admission is asked first, exactly as the operation asks it.

    With an empty pool there is no offer either, and the gate used to name that:
    a missing proposal is a condition `freeze` never reaches here, since the
    minimum is a floor no argument crosses and the operation answers on the pool
    before it ever looks for an offer.
    """

    evolution.sync(config, write_feed(feed_root, []))

    action = _action(phase.describe(config, now=NOW), phase.ACTION_FREEZE)

    assert not action.allowed
    assert batches.REASON_POOL_EMPTY in (action.refusal or "")
    assert "no batch proposal stands" not in (action.refusal or "")


def test_a_line_that_moved_under_the_offer_is_refused_by_the_gate_too(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """The human window is unbounded and the writer's lock does not span it, so
    the base the offer was built at is re-read — by the freeze, and by the
    reading an operator decides from."""

    fill_pool(config, feed_root, TARGET)
    evolution.build_proposal(config, now=NOW)
    git_update_ref(config.repo_root, RELEASE_REF, git_commit(config.repo_root, "the line moves"))

    action = _action(phase.describe(config, now=NOW), phase.ACTION_FREEZE)

    assert not action.allowed
    assert "the offer is refused rather than re-based" in (action.refusal or "")
    with pytest.raises(evolution.ProposalError) as excinfo:
        evolution.freeze(config, now=NOW)
    assert str(excinfo.value) == action.refusal


def test_a_pool_that_outgrew_the_offer_is_refused_by_the_gate_too(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """A batch states what it was selected out of, so a task that arrived after
    the offer was built refuses the freeze — and the gate says so rather than
    advertising a verb that refuses this exact reading."""

    fill_pool(config, feed_root, TARGET)
    evolution.build_proposal(config, now=NOW)
    evolution.sync(config, write_feed(feed_root, records(config, TARGET + 1)))

    action = _action(phase.describe(config, now=NOW), phase.ACTION_FREEZE)

    assert not action.allowed
    assert "the proposal never considered" in (action.refusal or "")
    with pytest.raises(evolution.ProposalError) as excinfo:
        evolution.freeze(config, now=NOW)
    assert str(excinfo.value) == action.refusal


def test_the_window_verbs_are_offered_exactly_while_an_offer_stands(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """One condition for both, and it is not admission's: a short offer is
    precisely the state a human move exists to argue with, so the thresholds
    must not close the window at the moment invariant 1's exception is for.

    What does close it is the manifest. A frozen batch's membership is immutable
    (invariant 3), and the sentence says so rather than repeating the
    "another batch is running" refusal `start` gives — a reader has to be able
    to tell a window that has closed from a batch that is busy.
    """
    fill_pool(config, feed_root, MINIMUM)

    before = phase.describe(config, now=NOW)
    for verb in (phase.ACTION_BATCH_INCLUDE, phase.ACTION_BATCH_EXCLUDE):
        action = _action(before, verb)
        assert not action.allowed and "no batch proposal stands" in (action.refusal or "")

    evolution.build_proposal(config, now=NOW)
    offered = phase.describe(config, now=NOW)
    assert not offered.decision.freeze  # short of the target, and still arguable
    for verb in (phase.ACTION_BATCH_INCLUDE, phase.ACTION_BATCH_EXCLUDE):
        assert _action(offered, verb).allowed

    fill_pool(config, feed_root, TARGET)
    freeze(config, now=NOW)
    closed = phase.describe(config, now=NOW)
    for verb in (phase.ACTION_BATCH_INCLUDE, phase.ACTION_BATCH_EXCLUDE):
        action = _action(closed, verb)
        assert not action.allowed
        assert "was fixed when it was frozen" in (action.refusal or "")


def test_a_human_move_reads_back_through_the_console_and_the_command(
    config: evolution.EvolutionConfig, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The verb an operator types, the reading they check it against, and the
    number that makes the exception legible rather than silent."""
    fill_pool(config, feed_root, TARGET)
    evolution.build_proposal(config, now=NOW)

    code, out, _ = run(
        [
            "evolution",
            phase.ACTION_BATCH_EXCLUDE,
            "repo-alpha",
            "2026-07-01-task",
            "--reason",
            "the evaluated target was mispatched",
            "--actor",
            "operator@example.com",
            "--repo",
            str(config.repo_root),
        ],
        capsys,
    )

    assert code == 0
    assert "batch-exclude: repo-alpha/2026-07-01-task is now out of the offered batch" in out
    status = phase.describe(config, now=NOW)
    assert status.to_json()["proposal"]["not_selected"] == {batches.REASON_HUMAN_EXCLUDED: 1}
    assert status.to_json()["proposal"]["task_count"] == TARGET - 1


def test_a_human_inclusion_shows_in_the_reading_as_its_own_number(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Two numbers and never one total: a batch a human curated is a different
    document from one the identity test selected, and the reading says so in the
    human form as well as the JSON."""
    fill_pool(config, feed_root, TARGET)
    evolution.sync(
        config,
        write_feed(
            feed_root,
            [make_record(key="x1", sequence=50, task_id="2026-08-01-absent", effective_revision=None)],
        ),
    )
    evolution.build_proposal(config, now=NOW)
    evolution.include(
        config, "repo-alpha", "2026-08-01-absent", justification="historical evidence", actor="operator", now=NOW
    )

    status = phase.describe(config, now=NOW)

    assert status.to_json()["proposal"]["human_included"] == 1
    assert status.to_json()["proposal"]["task_count"] == TARGET + 1
    assert "1 of them included by a human" in render.format_status(status)


def test_the_window_reaches_a_task_whose_identity_holds_a_slash(
    config: evolution.EvolutionConfig, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The import contract bounds `repo_id` and `task_id` only as non-empty
    strings, and a publisher's `owner/repo` slug is the ordinary form of the
    first. So the adapter takes the pair as two arguments: one joined by a slash
    would have to divide it somewhere nothing states, and every such task —
    pooled, listed in the offer, and named in every refusal — would be
    unreachable by the only two verbs that can move it.

    Both halves are pinned, because the contract permits the slash in either."""
    evolution.sync(
        config,
        write_feed(
            feed_root,
            at_line(
                config,
                [
                    make_record(key="r1", sequence=1, repo_id="team/repo", task_id="2026-07-01-task"),
                    make_record(key="r2", sequence=2, repo_id="repo-alpha", task_id="2026-07/02-task"),
                ],
            ),
        ),
    )
    evolution.build_proposal(config, now=NOW)

    for repo_id, task_id in (("team/repo", "2026-07-01-task"), ("repo-alpha", "2026-07/02-task")):
        code, out, _ = run(
            [
                "evolution",
                phase.ACTION_BATCH_EXCLUDE,
                repo_id,
                task_id,
                "--reason",
                "the evaluated target was mispatched",
                "--actor",
                "operator",
                "--repo",
                str(config.repo_root),
            ],
            capsys,
        )

        assert code == 0
        # Printed joined, and that stays a display: a read identity is not a
        # parsed one.
        assert f"batch-exclude: {repo_id}/{task_id} is now out of the offered batch" in out

    offered = proposals.load_proposal(config)
    assert offered is not None and offered.task_count == 0
    assert {item.dedup_key for item in offered.not_selected} == {
        ("team/repo", "2026-07-01-task"),
        ("repo-alpha", "2026-07/02-task"),
    }


def test_a_move_refuses_a_token_taken_before_it(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """The offer is in the state digest, so a move is a write like any other:
    the lock does not span the human window, and a caller acting on a reading
    somebody else has moved is refused rather than layered on top of it."""
    fill_pool(config, feed_root, TARGET)
    evolution.build_proposal(config, now=NOW)
    token = phase.describe(config, now=NOW).state_revision
    evolution.exclude(config, "repo-alpha", "2026-07-01-task", justification="why", actor="operator", now=NOW)

    with pytest.raises(evolution.StaleStateError, match="the evolution records moved since"):
        evolution.include(
            config, "repo-alpha", "2026-07-01-task", justification="on second thought", actor="operator",
            now=NOW, expect=token,
        )


def test_a_frozen_batch_holds_the_lifecycle_at_batch_frozen(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)

    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_BATCH_FROZEN
    assert status.current_batch is not None
    assert status.current_batch.batch_id == result.batch_id
    assert status.current_batch.findings_recorded is False
    assert status.current_batch.analysis_complete is False
    assert status.current_batch.evidence_local == status.current_batch.report_count
    assert status.decision.reason == batches.REASON_CURRENT_BATCH


def test_recorded_dispositions_move_the_phase_without_ending_the_analysis(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """`findings.md` is the disposition record and ends nothing on its own; the
    phase distinguishes the two so an operator can see analysis in progress."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    record_findings(config, result.batch_id or "")

    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_DISPOSITIONS_READY
    assert status.current_batch is not None and status.current_batch.batch_id == result.batch_id
    assert status.current_batch.analysis_complete is False


def test_a_completed_analysis_leaves_its_batch_current_at_the_admission_gate(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """The analysis stage ends; the batch does not (invariant 14). What is
    waiting is the human admission gate, and the batch goes on holding the next
    batch back while it waits."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    close_batch(config, result.batch_id or "", result.analysis_task_id or "")
    draft(config, result.batch_id or "", "tighten-contract")

    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_PROPOSALS_PENDING
    assert status.summary == f"proposals-pending {result.batch_id} (1 draft)"
    assert status.current_batch is not None
    assert status.current_batch.analysis_complete is True
    assert status.gate is not None and status.gate.waiting == ("tighten-contract",)
    assert status.decision.reason == batches.REASON_CURRENT_BATCH


def test_an_admitted_draft_stops_waiting_at_the_gate(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Admission copies the draft and leaves it in place, so the directory keeps
    every proposal ever made. What makes it spent is the experiment record that
    took it — and a declined one is spent too."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    batch_id = result.batch_id or ""
    close_batch(config, batch_id, result.analysis_task_id or "")
    for draft_id in ("tighten-contract", "loader-fallback", "widen-scan"):
        draft(config, batch_id, draft_id)
    experiment(config, batch_id, rounds=[experiment_round(1, tasks=[admitted_task("tighten-contract")])])
    write_rejected_drafts(config.batches_root, batch_id, [rejection("widen-scan")])

    status = phase.describe(config, now=NOW)

    assert status.gate is not None
    assert status.gate.waiting == ("loader-fallback",)
    assert status.gate.consumed == {"tighten-contract": f"{batch_id}-exp-01"}
    assert status.gate.declined == ("widen-scan",)
    assert (config.batches_root / batch_id / analysis_task.PROPOSED_TASKS_DIRNAME / "tighten-contract.md").is_file()


def test_an_open_round_reports_the_tasks_it_is_still_waiting_on(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    batch_id = result.batch_id or ""
    close_batch(config, batch_id, result.analysis_task_id or "")
    draft(config, batch_id, "tighten-contract")
    experiment(
        config,
        batch_id,
        rounds=[
            experiment_round(
                1,
                tasks=[
                    admitted_task("tighten-contract", task_id="2026-08-02-tighten-contract", complete=False),
                    admitted_task("loader-fallback", complete=True),
                ],
            )
        ],
    )

    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_IMPLEMENTING
    assert status.implementation_tasks == ("2026-08-02-tighten-contract",)
    assert status.summary == f"implementing {batch_id}-exp-01 round 1 (1 task left)"
    assert "implementing 2026-08-02-tighten-contract" in render.format_status(status)


def test_a_round_whose_tasks_are_all_observed_complete_is_ready_to_seal(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Still an open round: what pins its candidate is the seal, and nothing may
    be measured before that (invariant 16)."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    batch_id = result.batch_id or ""
    close_batch(config, batch_id, result.analysis_task_id or "")
    experiment(config, batch_id, rounds=[experiment_round(1, tasks=[admitted_task("loader-fallback")])])

    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_IMPLEMENTING
    assert status.implementation_tasks == ()
    assert status.summary == f"implementing {batch_id}-exp-01 round 1 (ready to seal)"
    assert status.revisions.round_candidate is None


def test_a_sealed_round_is_candidate_ready_and_names_the_revision_it_pinned(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    batch_id = result.batch_id or ""
    close_batch(config, batch_id, result.analysis_task_id or "")
    experiment(config, batch_id, rounds=[experiment_round(1, candidate_revision=CANDIDATE)])

    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_CANDIDATE_READY
    assert status.summary == f"candidate-ready {batch_id}-exp-01 round 1"
    assert status.revisions.base is not None and status.revisions.base.sha == formed_base(config, batch_id)
    assert status.revisions.round_candidate is not None
    assert status.revisions.round_candidate.sha == CANDIDATE
    # No ref in this checkout, which says nothing about the record's pins — and
    # is reported once, as the missing tip, rather than also as a finding.
    assert status.revisions.candidate_tip is None
    rendered = render.format_status(status)
    assert "tip          none — the experiment ref is not in this checkout" in rendered
    assert "cannot confirm the pinned history" not in rendered


def test_a_candidate_that_does_not_descend_from_the_frozen_base_is_named(
    tmp_path: Path
) -> None:
    """Invariant 15 read back: the whole chain is checked, so an attempt built
    on a history the batch never froze is reported with the pair that broke it
    rather than as a ref resting where its record says."""
    root = git_repo(make_repo(tmp_path), tag="v2.2.0")
    config = evolution.load_config(root)
    write_manifest(
        config.batches_root,
        "evolution-batch-0001",
        ["r1"],
        analysis_task_id="2026-07-31-analysis",
        runner_protocol_revision=git_rev(root, "HEAD"),
    )
    record_findings(config, "evolution-batch-0001")
    write_closure(config.batches_root, "evolution-batch-0001", analysis_task_id="2026-07-31-analysis")
    stranded = git_unrelated_commit(root, "an attempt on a history of its own")
    write_experiment(
        config.experiments_root,
        "evolution-batch-0001-exp-01",
        base_revision=git_rev(root, "HEAD"),
        rounds=[experiment_round(1, candidate_revision=stranded)],
    )
    git_update_ref(root, "refs/evolution/experiments/evolution-batch-0001-exp-01", stranded)

    status = phase.describe(config, now=NOW)

    assert status.ref is not None
    assert status.ref.state == lineage.REF_AT_PIN
    assert status.ref.consistent is False
    assert status.ref.chain_break == (git_rev(root, "HEAD"), stranded)
    assert "the pinned history is broken" in render.format_status(status)


def test_terminal_experiments_are_history_and_block_no_alternative(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """A batch carrying two dropped attempts and one open alternative is an
    ordinary state, and the lifecycle reads from the open one."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    batch_id = result.batch_id or ""
    close_batch(config, batch_id, result.analysis_task_id or "")
    experiment(
        config,
        batch_id,
        ordinal=1,
        rounds=[experiment_round(1, tasks=[admitted_task("loader-fallback")], candidate_revision=CANDIDATE)],
        decision=experiment_decision("superseded", superseded_by=f"{batch_id}-exp-02"),
    )
    experiment(
        config,
        batch_id,
        ordinal=2,
        rounds=[experiment_round(1, tasks=[admitted_task("widen-scan")])],
        decision=experiment_decision("abandoned"),
    )
    experiment(
        config,
        batch_id,
        ordinal=3,
        rounds=[experiment_round(1, tasks=[admitted_task("tighten-contract", complete=False)])],
    )

    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_IMPLEMENTING
    assert status.experiment is not None and status.experiment.experiment_id == f"{batch_id}-exp-03"
    assert [item.experiment_id for item in status.history] == [f"{batch_id}-exp-01", f"{batch_id}-exp-02"]
    assert status.current_batch is not None and status.current_batch.experiment_count == 3
    assert f"{batch_id}-exp-01 superseded" in render.format_status(status)


def test_a_batch_with_nothing_open_and_nothing_waiting_awaits_its_conclusion(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """The `no-change` case: analysis justified no change, so there is no draft
    to admit and no experiment to run, and what the batch needs is the outcome
    that says so (invariant 7)."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    close_batch(config, result.batch_id or "", result.analysis_task_id or "")

    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_CONCLUSION_PENDING
    assert status.summary == f"conclusion-pending {result.batch_id}"
    assert status.gate is not None and status.gate.waiting == ()


@pytest.mark.parametrize("waiting", [False, True], ids=["conclusion-pending", "proposals-pending"])
def test_a_batch_between_attempts_explains_its_revisions_by_what_is_open(
    config: evolution.EvolutionConfig, feed_root: Path, waiting: bool
) -> None:
    """The candidate and the tip belong to the open experiment, and a batch
    whose only attempt was abandoned has none.

    Both absences then have one reason — there is nothing open — and neither of
    the ordinary explanations is true: no round was left unsealed, and no ref
    was looked up at all, so reporting them describes an experiment that does
    not exist. The phase does not discriminate either, which is why both of the
    states this batch can be in are here.
    """
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    batch_id = result.batch_id or ""
    close_batch(config, batch_id, result.analysis_task_id or "")
    experiment(
        config,
        batch_id,
        rounds=[experiment_round(1, tasks=[admitted_task("loader-fallback")])],
        decision=experiment_decision("abandoned"),
    )
    if waiting:
        draft(config, batch_id, "widen-scan")

    status = phase.describe(config, now=NOW)
    rendered = render.format_status(status)

    assert status.phase == (phase.PHASE_PROPOSALS_PENDING if waiting else phase.PHASE_CONCLUSION_PENDING)
    assert status.experiment is None and status.ref is None
    assert status.to_json()["experiments"]["open"] is None, "what the rendering reads the absence off"
    assert status.revisions.base is not None and status.revisions.base.sha == formed_base(config, batch_id)
    assert status.revisions.round_candidate is None and status.revisions.candidate_tip is None
    assert "no experiment is open" in rendered
    assert "the open round has not been sealed" not in rendered
    assert "the experiment ref is not in this checkout" not in rendered
    assert f"{batch_id}-exp-01 abandoned" in rendered


def test_a_concluded_batch_stops_being_current_and_reports_its_promotion(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    batch_id = result.batch_id or ""
    close_batch(config, batch_id, result.analysis_task_id or "")
    decision = experiment_decision(
        "promoted",
        reason="the candidate held across the replay cohort",
        promotion_revision="c" * 40,
    )
    experiment(
        config,
        batch_id,
        rounds=[experiment_round(1, candidate_revision=CANDIDATE)],
        decision=decision,
    )
    write_outcome(
        config.batches_root,
        batch_id,
        outcome="promoted",
        reason=decision["reason"],
        experiment_id=f"{batch_id}-exp-01",
        promotion_revision="c" * 40,
        # One event, two records: the outcome states the merge unit the
        # experiment was promoted as, down to the candidate its round pinned.
        promotion=promotion_of(candidate_revision=CANDIDATE),
    )

    status = phase.describe(config, now=NOW)

    assert status.current_batch is None
    assert status.phase == phase.PHASE_IDLE
    assert status.experiment is None and status.history == ()
    assert status.last_promotion is not None
    assert status.last_promotion.revision == "c" * 40
    assert status.last_promotion.experiment_id == f"{batch_id}-exp-01"
    assert status.decision.current_batch_id is None
    assert "promoted" in render.format_status(status)


def test_a_task_file_citing_the_batch_is_not_what_makes_it_admitted(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """`.ai-tasks/` is machine-local and close-out archives tasks away, so the
    old citation scan found nothing on a fresh clone and less as time passed.
    The experiment record names its own tasks; a file with no record behind it
    admits nothing (contract: What is derived)."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    batch_id = result.batch_id or ""
    close_batch(config, batch_id, result.analysis_task_id or "")
    copy_into_tasks(config, draft(config, batch_id, "tighten-contract"))

    status = phase.describe(config, now=NOW)

    assert status.implementation_tasks == ()
    assert status.phase == phase.PHASE_PROPOSALS_PENDING
    assert status.gate is not None and status.gate.waiting == ("tighten-contract",)


def test_the_lineage_outlives_a_checkout_and_a_lost_task_pool(
    tmp_path: Path, feed_root: Path
) -> None:
    """The acceptance the derivation exists for: another branch, another
    revision, and no `.ai-tasks/` at all still derive one lifecycle."""
    root = make_repo(tmp_path)
    git_repo(root, tag="v2.2.0")
    config = evolution.load_config(root)
    fill_pool(config, feed_root, 1)
    write_manifest(config.batches_root, "evolution-batch-0001", ["r1"], analysis_task_id="2026-07-31-analysis")
    record_findings(config, "evolution-batch-0001")
    write_closure(config.batches_root, "evolution-batch-0001", analysis_task_id="2026-07-31-analysis")
    experiment(config, "evolution-batch-0001", rounds=[experiment_round(1, candidate_revision=CANDIDATE)])
    before = phase.describe(config, now=NOW)

    first = git_rev(root, "HEAD~1")
    git_commit(root, "unrelated work")
    git_checkout(root, first)
    shutil.rmtree(analysis_task.tasks_root(config), ignore_errors=True)

    after = phase.describe(config, now=NOW)

    assert before.phase == after.phase == phase.PHASE_CANDIDATE_READY
    assert after.revisions.round_candidate is not None
    assert after.revisions.round_candidate.sha == CANDIDATE
    assert after.experiment is not None
    assert after.experiment.experiment_id == "evolution-batch-0001-exp-01"


def test_a_batch_whose_evidence_was_staged_elsewhere_is_shown_not_failed(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """A frozen batch owns its reports wherever it was frozen; `.ai-evolution/`
    is machine-local, so a clone holds the manifest and none of the bundles."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    shutil.rmtree(config.artifacts_root)

    status = phase.describe(config, now=NOW)

    assert status.current_batch is not None
    assert status.current_batch.evidence_local == 0
    assert "evidence on this machine: 0/" in render.format_status(status)
    assert result.batch_id in render.format_status(status)


def test_an_experiment_ref_that_moved_past_its_seal_is_reported_not_hidden(
    tmp_path: Path, feed_root: Path
) -> None:
    """A reader names it; the guarded operations are what refuse (invariant 16).
    Refusing to describe the lifecycle is not how an operator finds out."""
    root = git_repo(make_repo(tmp_path), tag="v2.2.0")
    config = evolution.load_config(root)
    write_manifest(
        config.batches_root,
        "evolution-batch-0001",
        ["r1"],
        analysis_task_id="2026-07-31-analysis",
        runner_protocol_revision=git_rev(root, "HEAD~1"),
    )
    record_findings(config, "evolution-batch-0001")
    write_closure(config.batches_root, "evolution-batch-0001", analysis_task_id="2026-07-31-analysis")
    sealed = git_rev(root, "HEAD")
    write_experiment(
        config.experiments_root,
        "evolution-batch-0001-exp-01",
        base_revision=git_rev(root, "HEAD~1"),
        rounds=[experiment_round(1, candidate_revision=sealed)],
    )
    git_update_ref(root, "refs/evolution/experiments/evolution-batch-0001-exp-01", git_commit(root, "late work"))

    status = phase.describe(config, now=NOW)

    assert status.phase == phase.PHASE_CANDIDATE_READY
    assert status.ref is not None
    assert status.ref.state == lineage.REF_AHEAD
    assert status.ref.consistent is False
    assert status.revisions.round_candidate is not None and status.revisions.round_candidate.sha == sealed
    assert status.revisions.candidate_tip is not None and status.revisions.candidate_tip.sha != sealed
    assert "is not at the revision the record pins" in render.format_status(status)


def test_status_writes_nothing(config: evolution.EvolutionConfig, feed_root: Path, repo: Path) -> None:
    fill_pool(config, feed_root, TARGET)
    freeze(config)
    before = snapshot(repo)

    phase.describe(config, now=NOW)

    assert snapshot(repo) == before


def test_the_status_json_carries_the_phase_and_the_revisions_in_play(
    tmp_path: Path, feed_root: Path
) -> None:
    root = make_repo(tmp_path)
    git_repo(root, tag="v2.2.0")
    config = evolution.load_config(root)
    fill_pool(config, feed_root, 1)

    payload = phase.describe(config, now=NOW).to_json()

    assert payload["schema_version"] == phase.SCHEMA_VERSION == 11
    assert payload["phase"] == phase.PHASE_POOL
    assert payload["pool"] == {
        "task_count": 1,
        "target": 20,
        "minimum": 10,
        "complete": True,
    }
    # The age rule and the count admission applied sit with the decision, not
    # with the pool: they measure the batch on offer, and no batch is offered
    # here — `fill_pool` imports, it does not propose.
    assert payload["admission"] == {
        "freeze": False,
        "trigger": None,
        "reason": batches.REASON_NO_PROPOSAL,
        "current_batch": None,
        "task_count": 0,
        "oldest_pending_at": None,
        "waited_days": None,
        "max_wait_days": 30,
    }
    assert payload["proposal"] is None
    assert payload["batches"] == {"total": 0, "current": None, "history": []}
    assert payload["gate"] is None
    assert payload["experiments"] == {"open": None, "history": [], "pending_successor": None}
    assert payload["revisions"] == {"base": None, "candidate_tip": None, "round_candidate": None}
    # No experiment is open, so there is no round for evidence to be about — a
    # different absence from a round nothing has replayed.
    assert payload["replay"] is None
    assert payload["last_promotion"] is None
    # Nothing has been promoted, so there is no release for any batch to read.
    assert payload["release"] is None
    assert json.loads(json.dumps(payload)) == payload


def test_the_status_json_names_the_five_revisions_apart(
    config: evolution.EvolutionConfig, feed_root: Path
) -> None:
    """Base, pinned candidate, and promotion are three different commits, and an
    evidence trail that substitutes one for another measures nothing (contract:
    Revisions in play). The deployed effective revision is per target and is not
    a property of this repository."""
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    batch_id = result.batch_id or ""
    close_batch(config, batch_id, result.analysis_task_id or "")
    experiment(config, batch_id, rounds=[experiment_round(1, candidate_revision=CANDIDATE)])

    payload = phase.describe(config, now=NOW).to_json()

    assert payload["revisions"]["base"] == {"sha": formed_base(config, batch_id), "ref": "v2.2.0"}
    assert payload["revisions"]["round_candidate"] == {"sha": CANDIDATE, "ref": None}
    assert payload["revisions"]["candidate_tip"] is None
    assert payload["experiments"]["open"]["round"] == {
        "number": 1,
        "state": phase.ROUND_CANDIDATE_READY,
        "opened_at": "2026-08-01T09:00:00Z",
        "candidate_revision": CANDIDATE,
        "tasks": [{"task_id": "2026-08-01-loader-fallback", "draft_id": "loader-fallback", "complete": True}],
    }
    assert payload["experiments"]["open"]["ref"]["state"] == lineage.REF_ABSENT
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


def artifact_url(key: str, name: str) -> str:
    """The published artifact's URL — by wire filename, which is what orch-hub's
    four-value selector accepts."""

    return f"{BASE_URL}/api/evaluation/reports/{key}/artifacts/{HUB_ARTIFACT_NAMES[name]}"


def artifact_routes(key: str, bodies: dict[str, bytes] | None = None) -> dict[str, object]:
    return {artifact_url(key, name): body for name, body in (bodies or ARTIFACT_BODIES).items()}


def http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "", {}, None)


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
    feed = hub_feed({page_url(limit="10"): hub_page([])}, seen=seen)

    feed.fetch_page(None, 10)

    assert seen[0].get_header("Authorization") == f"Bearer {TOKEN}"
    assert TOKEN not in seen[0].full_url


def test_a_page_carries_its_entries_cursor_and_exhaustion(config: evolution.EvolutionConfig) -> None:
    """`has_more` is the feed's own statement; `exhausted` is its negation, never
    a guess from a short page."""
    entry = make_hub_entry(key="r1", seq=1)
    feed = hub_feed({page_url(limit="5"): hub_page([entry], next_cursor=1, has_more=True)})

    page = feed.fetch_page(None, 5)

    assert page.cursor == "1"
    assert page.exhausted is False
    assert page.items[0]["report_key"] == "r1"


def test_the_watermark_is_sent_as_after_and_stays_opaque_upward(config: evolution.EvolutionConfig) -> None:
    """orch-hub pages an append-only catalog by an integer watermark, but nothing
    above `ReportFeed` may learn that a cursor is a number."""
    seen: list = []
    feed = hub_feed({page_url(limit="5", after="7"): hub_page([], cursor=7, next_cursor=7)}, seen=seen)

    page = feed.fetch_page("7", 5)

    assert "after=7" in seen[0].full_url and "cursor=" not in seen[0].full_url
    assert page.cursor == "7"


def test_a_page_without_has_more_is_refused(config: evolution.EvolutionConfig) -> None:
    """It authorizes a later freeze to treat the pool as the whole eligible set,
    so it is read from the feed, never inferred."""
    body = json.dumps({"enabled": True, "reports": [], "cursor": 0, "next_cursor": 0}).encode("utf-8")
    feed = hub_feed({page_url(limit="5"): body})

    with pytest.raises(evolution.FeedError, match="has_more"):
        feed.fetch_page(None, 5)


def test_an_empty_page_echoes_the_watermark_and_does_not_rewind(config: evolution.EvolutionConfig) -> None:
    """A drained feed returns the cursor unchanged. Reading a missing one as
    "start over" would re-import the feed from the beginning on every run, so a
    page without a watermark is refused rather than interpreted."""
    echoed = hub_feed({page_url(limit="5", after="9"): hub_page([], cursor=9, next_cursor=9)})
    assert echoed.fetch_page("9", 5).cursor == "9"

    missing = json.dumps({"enabled": True, "reports": [], "has_more": False, "next_cursor": None}).encode("utf-8")
    with pytest.raises(evolution.FeedError, match="next_cursor"):
        hub_feed({page_url(limit="5"): missing}).fetch_page(None, 5)


def test_a_disabled_evaluation_subsystem_is_not_an_empty_feed(config: evolution.EvolutionConfig) -> None:
    """Treating it as a drained page would let a freeze call an empty pool the
    whole eligible set."""
    body = json.dumps({"enabled": False, "reports": [], "cursor": 0, "next_cursor": 0, "has_more": False}).encode(
        "utf-8"
    )
    feed = hub_feed({page_url(limit="5"): body})

    with pytest.raises(evolution.FeedError, match="disabled"):
        feed.fetch_page(None, 5)


def test_a_pruned_artifact_is_absent_rather_than_fatal(config: evolution.EvolutionConfig) -> None:
    """410 is the feed stating the body was published and is gone: the L1+L2 set
    is no longer durable, which the importer records as a rejection."""
    record = make_record(key="r1", sequence=1)
    routes = artifact_routes("r1")
    routes[artifact_url("r1", "report_markdown")] = http_error(artifact_url("r1", "report_markdown"), 410)
    feed = hub_feed(routes)

    blobs = feed.fetch_artifacts(record)

    assert set(blobs) == {"evidence", "static_metrics", "semantic_report"}


def test_an_unknown_key_or_name_raises_instead_of_reading_as_pruned(config: evolution.EvolutionConfig) -> None:
    """404 says the request addressed nothing — a defect in what was asked, not
    a fact about retention. Recording it as a missing body would bury a report
    that is fine."""
    record = make_record(key="r1", sequence=1)
    routes = artifact_routes("r1")
    routes[artifact_url("r1", "evidence")] = http_error(artifact_url("r1", "evidence"), 404)
    feed = hub_feed(routes)

    with pytest.raises(evolution.FeedError, match="HTTP 404"):
        feed.fetch_artifacts(record)


def test_an_incoherent_stored_identity_raises(config: evolution.EvolutionConfig) -> None:
    """409 is the hub saying its own entry cannot address its artifacts; no retry
    heals it and no rejection reason describes it."""
    record = make_record(key="r1", sequence=1)
    routes = artifact_routes("r1")
    routes[artifact_url("r1", "evidence")] = http_error(artifact_url("r1", "evidence"), 409)
    feed = hub_feed(routes)

    with pytest.raises(evolution.FeedError, match="HTTP 409"):
        feed.fetch_artifacts(record)


def test_a_transport_failure_fetching_an_artifact_raises(config: evolution.EvolutionConfig) -> None:
    """An unreachable feed says nothing about a report's eligibility, and
    recording it as rejected would bury a good report permanently."""
    record = make_record(key="r1", sequence=1)
    feed = hub_feed({artifact_url("r1", name): urllib.error.URLError("connection reset") for name in ARTIFACT_BODIES})

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

    # The canned feed answers 404 for a URL it does not know, which is now a
    # refusal rather than an absent body; the request it built is the subject.
    with pytest.raises(evolution.FeedError):
        feed.fetch_artifacts({"report_key": "a/../b", "artifacts": {"evidence": {"size_bytes": 1}}})

    assert seen[0].full_url.endswith("/reports/a%2F..%2Fb/artifacts/evidence.json")


def test_a_body_larger_than_declared_is_bounded_and_then_rejected(
    config: evolution.EvolutionConfig,
) -> None:
    """The client stops reading at one byte past the declared size; the
    importer's hash check is what turns that into a rejection."""
    oversized = b"x" * 5000
    record = make_record(key="r1", sequence=1)
    declared = record["artifacts"]["evidence"]["size_bytes"]
    feed = hub_feed({**artifact_routes("r1"), artifact_url("r1", "evidence"): oversized})

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
    routes: dict[str, object] = {page_url(limit="50"): hub_page([make_hub_entry(key="r1", seq=1, bodies=bodies)])}
    routes.update(artifact_routes("r1", bodies))

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
    drained = (200, {"Content-Type": "application/json"}, hub_page([]))
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
    test that proves the pair works together — a published catalog entry in, a
    pooled report with verified bytes out."""
    routes: dict[str, object] = {page_url(limit="50"): hub_page([make_hub_entry(key="r1", seq=1)])}
    routes.update(artifact_routes("r1"))

    result = importer.sync(config, hub_feed(routes))

    assert result.imported == ("r1",)
    assert result.exhausted is True
    assert result.cursor_after == "1"


def test_a_published_identity_reaches_the_frozen_batch_unchanged(
    config: evolution.EvolutionConfig,
) -> None:
    """The identity is only worth publishing if it survives to where a release
    assessment reads it: the pool stages the record whole and the freeze copies
    its provenance into the immutable manifest, so what places a report in a
    batch is the pair its own feed stated."""
    # Published at this repository's base, so the reports are selected into the
    # batch at all: membership is what that same field decides.
    produced = line_tip(config)
    entries = [
        make_hub_entry(
            key=f"r{index}",
            seq=index,
            task_id=f"2026-07-0{index}-task",
            protocol=hub_protocol(revision=produced),
        )
        for index in (1, 2)
    ]
    routes: dict[str, object] = {page_url(limit="50"): hub_page(entries)}
    for entry in entries:
        routes.update(artifact_routes(entry["report_key"]))

    assert importer.sync(config, hub_feed(routes)).imported == ("r1", "r2")
    result = freeze(config)

    assert result.manifest_path is not None
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    provenance = [report["provenance"] for report in manifest["reports"]]
    assert [item["effective_revision"] for item in provenance] == [produced, produced]
    assert [item["deploy_lock_hash"] for item in provenance] == [HUB_LOCK_HASH, HUB_LOCK_HASH]


def test_a_translated_entry_is_the_record_the_import_schema_describes(
    config: evolution.EvolutionConfig,
) -> None:
    """The feed serves its own catalog entry; everything above `ReportFeed` reads
    an import record, so the translation is what has to be right."""
    feed = hub_feed({page_url(limit="5"): hub_page([make_hub_entry(key="r1", seq=4)])})

    record = feed.fetch_page(None, 5).items[0]

    assert record["schema_version"] == 1
    assert record["sequence"] == 4
    assert record["generated_at"] == "2026-07-30T10:00:00Z"
    assert record["source"]["repo_id"] == "repo-alpha"
    assert record["evaluator"]["model"] == "claude-opus-5"
    assert record["artifacts"]["report_markdown"]["media_type"] == "text/markdown"
    assert record["artifacts"]["evidence"]["size_bytes"] == len(ARTIFACT_BODIES["evidence"])
    # Nothing orch-hub says about runs or git survives: the import schema closes
    # its objects, and the entry here states no protocol identity, which a release
    # assessment must see as absent rather than invented.
    assert record["provenance"]["effective_revision"] is None
    assert "repo_path" not in record and "target_source" not in record
    assert reports.normalize(record, config, reports.load_import_schema(config)).report_key == "r1"


def test_a_verified_protocol_identity_is_copied_onto_the_record(
    config: evolution.EvolutionConfig,
) -> None:
    """The pair orch-hub publishes is the one fact of this repository's provenance
    block the feed states, and it is what places a report in a release side."""
    entry = make_hub_entry(key="r1", seq=1, protocol=hub_protocol())
    feed = hub_feed({page_url(limit="5"): hub_page([entry])})

    provenance = feed.fetch_page(None, 5).items[0]["provenance"]

    assert provenance["effective_revision"] == HUB_REVISION
    assert provenance["deploy_lock_hash"] == HUB_LOCK_HASH
    # Copied, not reshaped: the record states exactly what the feed did.
    assert provenance["effective_revision"] == entry["provenance"]["protocol"]["effective_revision"]
    # The rest of the block orch-hub does not hold, and this side does not invent.
    assert provenance["runner_protocol_revision"] is None
    assert provenance["config_revision"] is None
    assert provenance["dev"] == {"agent": None, "model": None, "effort": None, "profile": None}


@pytest.mark.parametrize(
    "section",
    [
        HUB_PROTOCOL_LEGACY,
        {"available": False, "detail": "contributing runs verified different protocol revisions"},
        {"available": False, "effective_revision": HUB_REVISION, "deploy_lock_hash": HUB_LOCK_HASH},
        {"available": True, "detail": "an earlier hub published a revision on its own"},
        hub_protocol(lock_hash=None),
        hub_protocol(revision=None),
        hub_protocol(revision="   "),
        hub_protocol(revision=42),
        hub_protocol(available="true"),
        hub_protocol(available=1),
        None,
        "unavailable",
    ],
    ids=[
        "legacy-detail-only",
        "unavailable-mixed-run-set",
        "unavailable-but-carrying-a-pair",
        "available-carrying-neither-half",
        "revision-without-payload",
        "payload-without-revision",
        "blank-revision",
        "revision-that-is-not-a-string",
        "availability-as-a-string",
        "availability-as-a-number",
        "section-served-as-null",
        "section-that-is-not-an-object",
    ],
)
def test_anything_short_of_a_stated_pair_translates_to_two_nulls(
    config: evolution.EvolutionConfig, section: object
) -> None:
    """Half an identity is not a weaker fact, it is a different one: the commit
    names a source tree and the digest names the bytes taken from it, so neither
    stands in for the other. `available` is the flag orch-hub sets only for an
    identity every contributing run verified, and anything that is not exactly
    that flag over exactly both halves leaves the report unplaceable — which the
    assessment reports as `effective-revision-absent` rather than misplacing it."""
    feed = hub_feed({page_url(limit="5"): hub_page([make_hub_entry(key="r1", seq=1, protocol=section)])})

    record = feed.fetch_page(None, 5).items[0]

    assert record["provenance"]["effective_revision"] is None
    assert record["provenance"]["deploy_lock_hash"] is None
    # Still an admissible report: unplaceable costs a denominator, not a report.
    assert reports.normalize(record, config, reports.load_import_schema(config)).report_key == "r1"


def test_an_entry_predating_the_protocol_section_states_no_identity(
    config: evolution.EvolutionConfig,
) -> None:
    """The section is absent, not null, on everything an older hub published."""
    entry = make_hub_entry(key="r1", seq=1)
    del entry["provenance"]["protocol"]
    feed = hub_feed({page_url(limit="5"): hub_page([entry])})

    record = feed.fetch_page(None, 5).items[0]

    assert record["provenance"]["effective_revision"] is None
    assert record["provenance"]["deploy_lock_hash"] is None


def test_no_neighbouring_field_supplies_a_missing_identity(
    config: evolution.EvolutionConfig,
) -> None:
    """The entry carries other commit-shaped values — the newest commit of the
    evaluation's git window among them — and none of them is the revision the
    target's payload came from. Completing an unstated identity from one would
    place a report by a commit no run was shown to have run under."""
    entry = make_hub_entry(key="r1", seq=1, protocol=hub_protocol(revision=None))
    feed = hub_feed({page_url(limit="5"): hub_page([entry])})

    record = feed.fetch_page(None, 5).items[0]

    assert entry["provenance"]["git"]["newest_sha"]
    assert record["provenance"]["effective_revision"] is None
    assert record["provenance"]["deploy_lock_hash"] is None


def test_archived_is_how_this_feed_says_completed(config: evolution.EvolutionConfig) -> None:
    """orch-hub catalogs a report only once its task was archived at publication,
    and this protocol archives only at completion close-out — but it is mapped
    rather than asserted, so the importer's own gate still decides."""
    admissible = hub_feed({page_url(limit="5"): hub_page([make_hub_entry(key="r1", seq=1)])})
    assert admissible.fetch_page(None, 5).items[0]["source"]["completed"] is True

    unarchived = hub_feed({page_url(limit="5"): hub_page([make_hub_entry(key="r2", seq=2, archived=False)])})
    record = unarchived.fetch_page(None, 5).items[0]

    assert record["source"]["completed"] is False
    rejection = reports.normalize(record, config, reports.load_import_schema(config))
    assert rejection.reason == reports.REASON_NOT_ARCHIVED


def test_an_entry_the_feed_mangled_is_translated_and_then_rejected(
    config: evolution.EvolutionConfig,
) -> None:
    """The translation never raises and never drops: a malformed entry has to
    reach the importer, which records the rejection the ledger carries."""
    entry = make_hub_entry(key="r1", seq=1)
    entry["artifacts"] = [item for item in entry["artifacts"] if item["name"] != "semantic_report.json"]
    entry["seq"] = "not-a-sequence"
    feed = hub_feed({page_url(limit="5"): hub_page([entry], next_cursor=1)})

    record = feed.fetch_page(None, 5).items[0]

    assert "semantic_report" not in record["artifacts"]
    assert reports.normalize(record, config, reports.load_import_schema(config)).reason == (
        reports.REASON_MISSING_ARTIFACT
    )


# --- CLI ---------------------------------------------------------------------


def _evolution_verbs() -> dict[str, argparse.ArgumentParser]:
    """Every `mandrel evolution <verb>` this build has a parser for, and its
    parser — read off the tree an operator actually meets, which is the only
    place the answer cannot drift from what ships."""

    parser = cli.build_parser()
    evolution_action = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    return evolution_action.choices["evolution"]._subparsers._group_actions[0].choices


def _exposed_verbs() -> set[str]:
    """Their names."""

    return set(_evolution_verbs())


def test_every_command_a_refusal_names_is_one_this_build_exposes() -> None:
    """A recovery pointing at a verb with no parser behind it is worse than no
    recovery at all: the operator follows it, meets `invalid choice`, and is left
    with the refusal and nowhere to go.

    Scanned over the source rather than asserted per sentence, because the
    failure this pins is a *rename* — the formation split `start` in two, and
    every sentence naming the old single command went stale at once. A refusal
    naming a Python entry point the CLI does not expose fails here the same way.
    """

    named = {
        match.group(1)
        for path in sorted(Path(evolution.__file__).parent.rglob("*.py"))
        for match in re.finditer(r"`mandrel evolution ([a-z][a-z0-9-]*)`", path.read_text(encoding="utf-8"))
    }

    assert named, "the scan found no commands at all, so it proves nothing"
    assert named <= _exposed_verbs(), f"named in a refusal, absent from the CLI: {sorted(named - _exposed_verbs())}"


def test_a_refusal_that_sends_an_operator_to_form_a_batch_names_both_commands(
    config: evolution.EvolutionConfig,
) -> None:
    """Forming a batch takes two commands, and a recovery naming only the poll
    leaves the operator exactly where they started.

    `start` offers a batch and writes no manifest, so an operator told to freeze
    with it runs it, sees no current batch, and meets the same refusal — which is
    how these four read before the split landed. The reachable refusals are
    raised here rather than quoted so the pin is on what an operator is actually
    told.
    """

    with pytest.raises(evolution.BatchError) as promoting:
        experiments.promote(config, reason="the candidate holds", targets=("orch-hub",), now=NOW)
    with pytest.raises(evolution.BatchError) as concluding:
        experiments.conclude_no_change(config, reason="nothing justified", now=NOW)

    for refusal in (
        guards.no_current_batch(),
        assessment.no_batch_refusal(),
        str(promoting.value),
        str(concluding.value),
    ):
        assert "`mandrel evolution start`" in refusal, refusal
        assert "`mandrel evolution freeze`" in refusal, refusal




def test_status_prints_the_phase(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_repo(tmp_path)
    git_repo(root, tag="v2.2.0")

    code, out, _ = run(["evolution", "status", "--repo", str(root)], capsys)

    assert code == 0
    assert out.startswith("evolution: idle")


def test_status_reports_no_revisions_in_play_before_any_experiment(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A checkout is not a candidate. With no batch there is nothing in play,
    whatever branch the operator happens to be on — the base arrives with the
    batch that was formed at it."""
    code, out, _ = run(["evolution", "status", "--repo", str(repo)], capsys)

    assert code == 0
    assert "revisions    none in play — no batch is current" in out
    assert "candidate" not in out


def test_status_json_is_the_same_shape(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(["evolution", "status", "--repo", str(repo), "--json"], capsys)

    assert code == 0
    assert json.loads(out)["phase"] == phase.PHASE_IDLE


def test_list_inspects_the_feed_and_writes_nothing(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_feed(feed_root, records(evolution.load_config(repo), 2))
    before = snapshot(repo)

    code, out, _ = run(["evolution", "list", "--repo", str(repo), "--feed-dir", str(feed_root)], capsys)

    assert code == 0
    assert "2 report(s), 2 unique completed task(s)" in out
    assert "nothing was written" in out
    assert snapshot(repo) == before


def test_sync_imports_and_reports_the_pool(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_feed(feed_root, records(evolution.load_config(repo), 2))

    code, out, _ = run(["evolution", "sync", "--repo", str(repo), "--feed-dir", str(feed_root)], capsys)

    assert code == 0
    assert "imported" in out and "2 unique completed task(s) pending" in out
    assert evolution.load_state(evolution.load_config(repo)).pending


def test_start_runs_the_whole_flow_to_one_pending_analysis_task(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The acceptance end to end: an empty repository, a fixture feed, and the
    two commands a formation takes — the poll that offers a batch, and the
    freeze that fixes it as a batch with its pending analysis task."""
    config = evolution.load_config(repo)
    write_feed(feed_root, records(evolution.load_config(repo), TARGET))

    offered, out, _ = run(["evolution", "start", "--repo", str(repo), "--feed-dir", str(feed_root)], capsys)

    assert offered == 0
    assert "nothing was frozen" in out
    assert evolution.load_batches(config) == [], "the poll writes nothing durable"

    code, out, _ = run(["evolution", "freeze", "--repo", str(repo)], capsys)

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


def test_a_second_start_offers_no_second_batch(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The pool goes on accumulating while a cycle runs; what it may not do is
    offer a second batch beside it (invariant 14)."""
    write_feed(feed_root, records(evolution.load_config(repo), TARGET))
    run(["evolution", "start", "--repo", str(repo), "--feed-dir", str(feed_root)], capsys)
    run(["evolution", "freeze", "--repo", str(repo)], capsys)

    code, out, _ = run(["evolution", "start", "--repo", str(repo), "--feed-dir", str(feed_root)], capsys)

    assert code == 0
    assert "is still current" in out
    assert len(evolution.load_batches(evolution.load_config(repo))) == 1
    assert not evolution.load_config(repo).proposal_path.exists()


def test_start_below_the_target_offers_a_batch_nothing_may_freeze_yet(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Too little evidence is the contract's normal outcome, so the run
    succeeds and reports the reason. The offer still stands: what admission
    refuses is forming a batch out of it, not selecting it."""
    write_feed(feed_root, records(evolution.load_config(repo), 1))

    code, out, _ = run(["evolution", "start", "--repo", str(repo), "--feed-dir", str(feed_root)], capsys)

    assert code == 0
    assert batches.REASON_BELOW_TARGET in out
    assert "nothing was frozen" in out
    assert evolution.load_config(repo).proposal_path.is_file()
    assert evolution.load_batches(evolution.load_config(repo)) == []


def test_force_without_a_justification_is_refused(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_feed(feed_root, records(evolution.load_config(repo), 1))
    run(["evolution", "start", "--repo", str(repo), "--feed-dir", str(feed_root)], capsys)

    code, _, err = run(["evolution", "freeze", "--repo", str(repo), "--force"], capsys)

    assert code == exits(evolution.InputError)
    assert "justification" in err
    assert evolution.load_batches(evolution.load_config(repo)) == []


def test_a_justified_force_freezes_a_below_target_batch(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--force` belongs to the freeze, not the poll: waiving the configured
    target is a decision about forming a batch, and `start` forms none."""
    write_feed(feed_root, records(evolution.load_config(repo), 1))
    run(["evolution", "start", "--repo", str(repo), "--feed-dir", str(feed_root)], capsys)

    code, out, _ = run(
        [
            "evolution",
            "freeze",
            "--repo",
            str(repo),
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
    assert not config.proposal_path.exists()


def test_a_freeze_with_nothing_offered_is_actionable(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one command an operator can reach for out of order, so its refusal
    has to name the command that produces what it needs.

    Asked of a pool the policy *would* release, because an empty or short pool
    answers earlier and for a different reason: `freeze` reports no batch and
    exits 0 there, exactly as it did before the formation split.
    """

    config = evolution.load_config(repo)
    evolution.sync(config, write_feed(feed_root, records(config, TARGET)))

    code, _, err = run(["evolution", "freeze", "--repo", str(repo)], capsys)

    assert code == exits(evolution.BatchError)
    assert "no batch proposal stands" in err and "mandrel evolution start" in err
    assert evolution.load_batches(config) == []


def test_a_freeze_below_the_target_reports_no_batch_rather_than_refusing(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Too little evidence stays the contract's normal outcome, not an error.

    Measured over the batch on offer, which is why the offer is built first: the
    thresholds count what a freeze would form, so a freeze asked before anybody
    has been offered a membership has nothing to apply them to.
    """

    config = evolution.load_config(repo)
    write_feed(feed_root, records(config, 1))
    run(["evolution", "start", "--repo", str(repo), "--feed-dir", str(feed_root)], capsys)

    code, out, _ = run(["evolution", "freeze", "--repo", str(repo)], capsys)

    assert code == 0
    assert batches.REASON_BELOW_TARGET in out
    assert "1 selected of 1 pooled" in out
    assert evolution.load_batches(config) == []


def test_a_freeze_before_anything_was_offered_sends_the_operator_to_the_poll(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A freeze fixes an offer somebody has seen. With no offer standing there is
    no membership to measure and no batch to answer about, so the refusal names
    the command that makes one rather than reporting a batch of nothing."""

    config = evolution.load_config(repo)
    evolution.sync(config, write_feed(feed_root, records(config, 1)))

    code, _, err = run(["evolution", "freeze", "--repo", str(repo)], capsys)

    assert code == exits(evolution.BatchError)
    assert "no batch proposal stands" in err
    assert "mandrel evolution start" in err
    assert evolution.load_batches(config) == []


def test_a_run_without_a_feed_or_credentials_is_actionable(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run(["evolution", "sync", "--repo", str(repo)], capsys)

    assert code == exits(evolution.FeedError)
    assert "ORCH_HUB_URL" in err and "--feed-dir" in err


def test_a_missing_workspace_config_is_actionable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    empty = tmp_path / "elsewhere"
    empty.mkdir()

    code, _, err = run(["evolution", "status", "--repo", str(empty)], capsys)

    assert code == exits(evolution.ConfigError)
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

    assert code == exits(evolution.FeedError)
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

    assert code == exits(evolution.FeedError)
    assert "invalid cursor" in err


def test_corrupt_runtime_state_is_reported_not_reset(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = evolution.load_config(repo)
    fill_pool(config, feed_root, 1)
    config.state_path.write_text('{"schema_version": 2}', encoding="utf-8")

    code, _, err = run(["evolution", "status", "--repo", str(repo)], capsys)

    assert code == exits(evolution.StateError)
    assert "incomplete state" in err


def test_lock_contention_is_reported_with_its_holder(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = evolution.load_config(repo)
    write_feed(feed_root, records(config, 1))
    config.lock_path.parent.mkdir(parents=True, exist_ok=True)
    config.lock_path.write_text(
        json.dumps({"pid": 4242, "host": "another-host", "acquired_at": "2026-08-01T11:00:00Z"}), encoding="utf-8"
    )

    code, _, err = run(["evolution", "sync", "--repo", str(repo), "--feed-dir", str(feed_root)], capsys)

    assert code == exits(evolution.LockError)
    assert "4242" in err and "another-host" in err
    assert "remove it if no run is active" in err


# --- the failure taxonomy ----------------------------------------------------
#
# Every evolution failure carries a `kind` and a `code` (`evolution/errors.py`),
# and the console turns the first into a process exit status. What is under test
# is that the classification is real: that the families are complete and
# distinct, that three failures an operator answers three different ways come
# back as three different answers, and that the one response which is neither a
# success nor a failure is neither.
#
# Nothing here reads a message. That is the property being bought — a sentence
# is written for a person and gets rewritten as the operation it explains gets
# sharper, and anything keying on its words reclassifies itself when it does.


def test_every_family_has_its_own_number_and_every_failure_has_a_family() -> None:
    """The map is complete, one-to-one, and does not collide with what the
    process already says.

    Structural rather than behavioral because that is where this can rot: a
    family added to `errors.py` and not to the map exits as the unclassified
    failure it is not, and nothing else would ever notice.
    """

    numbers = [cli.EVOLUTION_EXIT_CODES[kind] for kind in errors.ERROR_KINDS]

    assert set(cli.EVOLUTION_EXIT_CODES) == set(errors.ERROR_KINDS)
    assert len(set(numbers)) == len(numbers), "two families an operator answers differently share a number"
    # 0 is a completed run, 1 is a read whose answer is no, 2 is a command that
    # was not understood or a failure with no family at all.
    assert not {0, 1, cli.UNCLASSIFIED_EXIT} & set(numbers)

    found = {}
    pending = [errors.EvolutionError]
    while pending:
        failure = pending.pop()
        pending.extend(failure.__subclasses__())
        assert failure.kind in errors.ERROR_KINDS, f"{failure.__name__} has no family"
        assert found.setdefault(failure.code, failure) is failure, f"{failure.code} names two classes"
    assert harness.Unanswered in found.values(), "the harness family is reached by importing its module"


# The domain modules whose refusals have been drawn out of `BatchError`'s single
# code. Grows as the classification does; the point of naming them here rather
# than checking every module is that a module not yet on the list is work that
# has not happened, and one on it that regresses is a refusal that quietly went
# back to saying nothing.
CLASSIFIED_MODULES = ("replay", "rollback", "guards", "experiments", "assessment", "batches")


def test_the_classified_modules_raise_no_refusal_under_the_family_code() -> None:
    """Nothing in them reaches a caller as the bare refusal family.

    Read off the source rather than driven through the operations, because what
    this protects against is a raise site added later — a new refusal written the
    way its neighbours used to be written, on a path no test happens to reach,
    which is exactly the one an adapter then cannot route.

    The second half is the same rot one level up: a subclass added without a
    `code` of its own inherits `state-refused` and passes the first check while
    saying nothing more than `BatchError` did.
    """

    for name in CLASSIFIED_MODULES:
        source = Path(evolution.__file__).parent / f"{name}.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        constructed = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "BatchError" not in constructed, f"{name}.py still constructs the undifferentiated refusal"
        for constructed_name in sorted(constructed):
            failure = getattr(errors, constructed_name, None)
            if not (isinstance(failure, type) and issubclass(failure, errors.BatchError)):
                continue
            assert "code" in vars(failure), f"{failure.__name__} inherits {errors.BatchError.code!r}"


def test_a_narrowed_refusal_says_more_and_exits_the_same(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Splitting the refusal family moved nothing an operator sees.

    The point of the split is a caller that routes, so the two halves under test
    are the two that must not have travelled together: the `code` is narrower
    than the family's, and the number the process leaves and the sentence it
    prints are what they were. A refusal that started exiting differently because
    it was classified would have been a change to every shell already branching
    on it.
    """

    code, _, err = run(["evolution", "rollback", "--reason", "nothing was promoted here", "--repo", str(repo)], capsys)

    assert code == exits(evolution.BatchError) == exits(evolution.NothingToActOn)
    assert evolution.NothingToActOn.code != evolution.BatchError.code
    assert "no batch has promoted anything" in err


def test_a_stale_reading_a_refusal_and_a_damaged_record_are_three_answers(
    lineage_repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One verb, three failures, three answers — which is the whole point.

    They are three different next steps: read the lifecycle again, change what
    stands in the way, look at a file on this machine. Collapsed into one number
    the caller has to read the sentence to tell them apart, and the sentences are
    the part that moves.
    """

    config = evolution.load_config(lineage_repo)
    fill_pool(config, feed_root, TARGET)
    where = ["--repo", str(lineage_repo), "--feed-dir", str(feed_root)]
    before = token(config)

    # Legal, and the state does not permit it: nothing has been offered yet.
    refused, _, _ = run(["evolution", "freeze", "--repo", str(lineage_repo)], capsys)

    # Legal, permitted, and asked for a reading that has since moved.
    run(["evolution", "start", *where], capsys)
    stale, _, _ = run(["evolution", "freeze", "--repo", str(lineage_repo), "--expect", before], capsys)

    # Neither: this machine's own record of the pool cannot be read.
    config.state_path.write_text('{"schema_version": 2}', encoding="utf-8")
    damaged, _, _ = run(["evolution", "freeze", "--repo", str(lineage_repo)], capsys)

    assert refused == exits(evolution.BatchError)
    assert stale == exits(evolution.StaleStateError)
    assert damaged == exits(evolution.StateError)
    assert len({refused, stale, damaged}) == 3
    assert len({evolution.BatchError.code, evolution.StaleStateError.code, evolution.StateError.code}) == 3
    assert evolution.load_batches(config) == []


def test_a_generated_task_replaced_by_something_else_is_a_damaged_record(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A freeze redone over a file that is not the analysis task it generated.

    Read as the catch-all it once was, this told an operator their filesystem
    work had not completed — which sends them to redo the command, and the redo
    finds the same file and says the same thing. It is a record: the freeze
    wrote it, everything downstream reads a lifecycle out of it, and nothing
    here may write over one that may already carry a session log. So the answer
    is the record family, and what restores it is the manifest rather than
    version control — only this machine ever held the file.
    """

    config = evolution.load_config(repo)
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    path = analysis_task.task_path(config, result.analysis_task_id or "")
    path.write_text("# not the task that was generated\n", encoding="utf-8")

    code, _, err = run(["evolution", "freeze", "--repo", str(repo)], capsys)

    assert code == exits(evolution.RecordError)
    assert code not in {cli.UNCLASSIFIED_EXIT, exits(evolution.EvolutionError)}
    assert "is not the analysis task generated" in err


def test_an_operations_own_write_is_the_family_with_the_least_to_say(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The catch-all reaches the console under its own number too.

    What is left in it once the two failures that were only sitting there have
    been named: not the record, which is intact, and not a refusal about the
    lifecycle, which has not been reached — this machine simply would not hand
    the file over. That is the whole of `failed`, and it exits as itself rather
    than as an unclassified failure.

    The file is made unreadable rather than the read patched, because the
    condition is the point: a path the process cannot open is what the door
    behind this catches, and a patched `read_text` would assert the handler
    rather than the case reaching it.
    """

    if os.geteuid() == 0:
        pytest.skip("root reads a mode-000 file, so the condition cannot be created")

    config = evolution.load_config(repo)
    fill_pool(config, feed_root, TARGET)
    result = freeze(config)
    path = analysis_task.task_path(config, result.analysis_task_id or "")
    path.chmod(0o000)
    try:
        code, _, err = run(["evolution", "freeze", "--repo", str(repo)], capsys)
    finally:
        path.chmod(0o644)

    assert code == exits(evolution.EvolutionError)
    assert code != cli.UNCLASSIFIED_EXIT
    assert "cannot read the analysis task" in err


def test_a_git_below_the_floor_is_a_fact_about_this_machine(tmp_path: Path) -> None:
    """A Git too old to compute the tree says so, and says which version would.

    Two floors, because the difference is a whole verb: everything up to a
    promotion works on 2.38 and only a rollback needs 2.40, so a machine that
    can promote and cannot reverse has to be told which of the two it is.

    The revert half runs against the real Git — a `--merge-base` it cannot
    resolve is the failure branch this reading lives on — and the merge half
    injects the usage error an older Git answers a newer option with, which is
    the one thing a checkout holding a current Git cannot produce.
    """

    repo = tmp_path / "checkout"
    git_repo(repo, tag=None)
    head = git_commit(repo, "one")

    modern = revisions.revert_tree(repo, revision="deadbeef", parent=head, tip=head)

    assert modern.tree is None and not modern.conflicted and not modern.toolchain
    assert "could not parse" in modern.complaint

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(revisions, "git_version", lambda root: (2, 39, 0))
        rollback_floor = revisions.revert_tree(repo, revision="deadbeef", parent=head, tip=head)
        # The probe explains an answer; it never decides whether to ask. A merge
        # that succeeds is not asked about the version at all, even one this
        # would call ancient.
        patched.setattr(revisions, "git_version", lambda root: (0, 1))
        clean = revisions.merge_tree(repo, head, head)

    assert rollback_floor.toolchain and "2.39" in rollback_floor.complaint
    assert "2.40 or newer" in rollback_floor.complaint
    assert clean.tree is not None and not clean.toolchain

    with pytest.MonkeyPatch.context() as patched:
        # What a Git without `--write-tree` answers: an option it does not know,
        # and a usage exit that is neither of merge-tree's two answers.
        asked = revisions._run

        def older(root, *args):
            if args[0] != "merge-tree":
                return asked(root, *args)
            return subprocess.CompletedProcess(
                args, 129, "", "error: unknown option `write-tree'\nusage: git merge-tree"
            )

        patched.setattr(revisions, "_run", older)
        patched.setattr(revisions, "git_version", lambda root: (2, 37, 3))
        merge_floor = revisions.merge_tree(repo, head, head)

    assert merge_floor.toolchain and not merge_floor.conflicted
    assert "unknown option" in merge_floor.complaint, "Git's own words are kept"
    assert "2.37.3" in merge_floor.complaint and "2.38 or newer" in merge_floor.complaint


def test_a_git_that_cannot_merge_is_not_a_candidate_that_will_not(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The distinction the toolchain family exists for, at the console.

    A candidate that conflicts is a fact about the revisions, true in every
    checkout that holds them, and it retires the round. A Git that cannot compute
    the merge is a fact about this machine and retires nothing — read as the
    first, it would send an operator to resolve a conflict that is not there.
    """

    config = evolution.load_config(lineage_repo)
    sealed(config, feed_root, capsys)
    where = ["--repo", str(lineage_repo)]
    asked = ["--source-ref", RELEASE_REF, "--expectation", "fewer remediation rounds"]

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(
            replay,
            "merge_tree",
            lambda *args, **kwargs: revisions.MergeAnswer(
                None, "git merge-tree: unknown option `--write-tree'; this is git 2.30.0 and 2.38 or newer "
                "computes it", toolchain=True
            ),
        )
        code, _, err = run(["evolution", "replay-start", *asked, *where], capsys)

    assert code == exits(evolution.ToolchainError)
    assert code != exits(evolution.BatchError)
    assert "2.38 or newer" in err
    experiment = lineage.describe(config).current.open_experiment
    assert replay.read_replays(config, experiment).pending is None, "nothing was established, so nothing was pinned"


def test_a_git_that_will_not_run_is_not_a_checkout_that_lost_its_line(
    repo: Path, feed_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other half of the same distinction, one layer below the merge.

    Every question `revisions.py` puts comes back None twice over: Git looked and
    said no, and nothing looked at all. Collapsed, an absent Git reads as a
    checkout that does not hold the source line — a sentence that sends an
    operator to fetch a ref from a repository nothing ever asked about, and one
    no fetch will ever satisfy.

    `git` is taken off `PATH` rather than patched out, which is the condition
    itself: `_run` returns None only where the process could not be started, and
    that is the one thing an argument list cannot change.
    """

    config = evolution.load_config(repo)
    fill_pool(config, feed_root, TARGET)
    where = ["--repo", str(repo), "--feed-dir", str(feed_root)]

    monkeypatch.setenv("PATH", str(repo / "no-tools-here"))
    absent = revisions.merge_tree(repo, "HEAD", "HEAD")
    code, _, err = run(["evolution", "start", *where], capsys)

    # The integration half: no tree, and the reason is this machine rather than a
    # work tree nothing looked for.
    assert absent.tree is None and absent.toolchain and not absent.conflicted
    assert "not the top of a work tree" not in absent.complaint

    # The ref half, at the console.
    assert code == exits(evolution.ToolchainError)
    assert code != exits(evolution.BatchError)
    assert "git will not run on this machine" in err
    assert "fetch" not in err, "nothing looked at the repository, so nothing about it is being asked for"


def test_a_git_that_will_not_run_is_not_a_promotion_the_evidence_refuses(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The same distinction along the whole path a promotion takes, at both of
    the places it reads a silence and calls it something.

    A promotion asks two Git questions before it moves anything: whether the line
    still stands where the run measured it, and whether any work tree is sitting
    on it. Both come back None twice over — the ref is not in this checkout, or
    nothing here runs `git` — and the reader answering the first collapses them
    on purpose, because a `status` owes its reader a degraded answer. The
    promotion may not: read as the ref being absent, an operator is sent to fetch
    from a repository nothing ever asked about, and read as the line being busy,
    to a second clone. Neither repair exists on a machine with no git.

    The evidence gate answers first, so it is what the console shows. The
    worktree guard is asserted directly, because it is the last thing between the
    move and somebody's work tree and asks its own question rather than
    inheriting one — the property both `_land` docstrings rest on.
    """

    config = evolution.load_config(lineage_repo)
    measured(config, feed_root, capsys)
    where = ["--repo", str(lineage_repo)]

    with pytest.MonkeyPatch.context() as patched:
        patched.setenv("PATH", str(lineage_repo / "no-tools-here"))
        listed = revisions.checked_out_refs(lineage_repo)
        code, _, err = run(["evolution", "promote", "--reason", "the measured candidate", *where], capsys)
        with pytest.raises(evolution.ToolchainError) as guarded:
            guards.require_line_not_checked_out(config, RELEASE_REF, "a promotion")

    assert listed is None, "the reading itself still cannot say; what changed is who is told why"
    assert code == exits(evolution.ToolchainError)
    assert code != exits(evolution.BatchError)
    assert "git will not run on this machine" in err
    assert "fetch" not in err, "nothing looked at the repository, so nothing about it is being asked for"
    assert "cannot be answered" not in str(guarded.value), "no work tree was looked for, so none is reported"
    assert git_rev(lineage_repo, RELEASE_REF) == release, "the line stands where it stood"


def test_a_replay_record_this_build_cannot_read_is_not_a_mistyped_command(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A damaged record on disk reaches the console as one, through the guard
    every operation runs before it writes.

    The parser that refuses it serves the writers too, so what it raises about a
    value is `invalid` — which is the caller's family, and the caller here typed
    nothing. Read as that, an operator is told to retype a command over a file
    only they can decide about; read as a refusal, to change a lifecycle that is
    not what is wrong. It is the third thing, and the exit status says so.
    """

    config = evolution.load_config(lineage_repo)
    measured(config, feed_root, capsys)
    experiment = lineage.describe(config).current.open_experiment
    path = replay.replays_path(experiment)
    # Well-formed JSON, and not a replay history: the schema is what refuses it,
    # which is the door this translation was missing.
    path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")

    code, _, err = run(
        ["evolution", "promote", "--reason", "the measured candidate", "--repo", str(lineage_repo)], capsys
    )

    assert code == exits(evolution.RecordError)
    assert code not in {exits(evolution.InputError), exits(evolution.BatchError)}
    assert len({evolution.RecordError.code, evolution.ValidationError.code}) == 2
    assert str(path) in err
    assert git_rev(lineage_repo, RELEASE_REF) == release, "nothing was promoted over a record nobody can read"


def test_a_frozen_manifest_that_contradicts_itself_is_not_a_freeze_being_refused(
    repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other half of the same translation, on the record the repository
    carries rather than this machine.

    A manifest's counts and its reports have to describe one batch, and the
    rule saying so is asked in both directions: the freeze publishes through it,
    where refusing is right because the manifest is still being built and does
    not have to be published, and the reader asks it of a file frozen months ago
    under invariant 3. Answered there as a refusal, an operator is told to change
    a state — of a document that may not be edited, by a command that cannot run
    because reading it is the first thing every command does.
    """

    config = evolution.load_config(repo)
    fill_pool(config, feed_root, TARGET)
    frozen = freeze(config)
    path = config.batches_root / (frozen.batch_id or "") / batches.MANIFEST_FILENAME
    manifest = json.loads(path.read_text(encoding="utf-8"))
    # Shape-valid and self-contradicting: the denominator no longer counts the
    # reports beside it, which is the rule no schema can state.
    manifest["selection"]["considered"] += 1
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    code, _, err = run(["evolution", "status", "--repo", str(repo)], capsys)

    assert code == exits(evolution.RecordError)
    assert code not in {exits(evolution.InputError), exits(evolution.BatchError)}
    assert str(path) in err
    assert "restore it" in err, "the answer names what a person does with the file"


def test_a_pool_that_lost_its_evidence_is_not_a_freeze_the_state_permits(
    repo: Path, config: evolution.EvolutionConfig, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The third record this translation covers: not the repository's, and not a
    file a person restores from it either.

    A staged bundle and the `state.json` claim naming it are the pool's two
    halves, and both are damaged the same way — the pool asserts evidence that is
    not there. Read as a refusal, an operator is sent to change a lifecycle that
    is intact, and told nothing about the one thing that answers it: `.ai-evolution/`
    is ignored, so nothing here is restored from version control and importing
    the reports again is the whole recovery.

    Both halves are reached through `freeze`, which is where each is asked: the
    bundle when the manifest's content is pinned, and the claim when a restarted
    freeze reconciles the batch it already published.
    """

    fill_pool(config, feed_root, TARGET)
    entry = evolution.load_state(config).pending[0]
    body = config.repo_root / entry.report.artifacts_path / state.ARTIFACTS_SUBDIR / "semantic_report"
    staged = body.read_bytes()
    where = ["--repo", str(repo)]

    # The evidence the manifest is about to pin is not the evidence that was
    # imported.
    body.write_bytes(staged + b" tampered")
    evolution.build_proposal(config, now=NOW)
    lost_bundle, _, bundle_err = run(["evolution", "freeze", *where], capsys)
    unpinned = evolution.load_batches(config)
    body.write_bytes(staged)

    # And this machine's own record of what it staged, with a hole in it.
    result = freeze(config)
    raw = json.loads(config.state_path.read_text(encoding="utf-8"))
    claimed = dict(raw["processed"])
    del raw["processed"][sorted(claimed)[0]]
    config.state_path.write_text(json.dumps(raw), encoding="utf-8")
    lost_claim, _, claim_err = run(["evolution", "freeze", *where], capsys)

    assert lost_bundle == exits(evolution.StateError)
    assert lost_claim == exits(evolution.StateError)
    assert exits(evolution.StateError) not in {exits(evolution.BatchError), exits(evolution.InputError)}
    assert "no longer match the record" in bundle_err and "re-import" in bundle_err
    assert "neither pending nor claimed" in claim_err and "import the reports again" in claim_err
    # Neither run wrote: the first pinned no manifest over evidence nobody has,
    # and the second left the hole rather than claiming the batch over it.
    assert unpinned == []
    assert [batch.batch_id for batch in evolution.load_batches(config)] == [result.batch_id]
    assert json.loads(config.state_path.read_text(encoding="utf-8"))["processed"] == raw["processed"]


# --- the failure channel ------------------------------------------------------
#
# `--json` states what the taxonomy already knows, in a shape nothing has to
# read a sentence to route on. Failure only: what a completed run prints is a
# later contract, and inventing half of it here would be a second shape for the
# same answers.


def evolution_verbs() -> dict[str, argparse.ArgumentParser]:
    """Every subcommand of the evolution console, off the parser itself.

    Read from argparse rather than from a list written here, which is the point:
    a verb added without the flag is what this catches, and a list restating the
    console would be updated by the same commit that forgot it.
    """

    parser = build_evolution_parser()
    return dict(parser.choices)


def build_evolution_parser() -> argparse._SubParsersAction:
    top = cli.build_parser()
    commands = next(action for action in top._subparsers._group_actions if action.dest == "command")
    evolution_parser = commands.choices["evolution"]
    return next(
        action for action in evolution_parser._subparsers._group_actions if action.dest == "evolution_command"
    )


def envelope(err: str) -> dict[str, object]:
    """The failure the console stated, parsed the way an adapter would."""

    return json.loads(err)


def test_every_verb_of_the_console_states_its_failures_on_request() -> None:
    """The flag is a property of the console and not of any verb.

    A caller driving this thing has to be able to ask any of it for an answer it
    can route on; one verb without the channel is one an adapter falls back to
    reading prose for, which is the single case the taxonomy exists to remove.
    Asserted over the parser rather than by running each verb, because what this
    protects against is the verb written next.
    """

    verbs = evolution_verbs()

    assert verbs, "the console has verbs"
    for name, parser in sorted(verbs.items()):
        assert "--json" in parser._option_string_actions, f"{name} cannot state a failure a machine reads"
    # The two readings keep their own, which promise the whole reading rather
    # than the failure channel — the verbs where the flag already means
    # something, and where the sweep adding a second one would be argparse
    # refusing the duplicate rather than either promise quietly winning.
    for reading in cli._WHOLE_READING_VERBS:
        assert verbs[reading]._option_string_actions["--json"].help == "print the same shape as JSON"


def test_a_failure_states_its_family_without_its_sentence(
    config: evolution.EvolutionConfig, repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """What an adapter gets: the two attributes it routes on, and no prose to
    parse for them.

    The sentence rides along because a surface has a person to show it to, and
    it is deliberately not the routing key — `message` is the field that moves
    when an operation's explanation gets sharper, which is why `code` exists.
    """

    fill_pool(config, feed_root, TARGET)

    code, out, err = run(["evolution", "freeze", "--repo", str(repo), "--json"], capsys)
    stated = envelope(err)

    assert code == exits(evolution.NothingToActOn)
    # The exact key set, because that is what the version promises: a key added,
    # removed or repurposed is a caller reading a shape nobody told it about.
    assert set(stated) == {"schema_version", "verb", "kind", "code", "message"}
    assert stated["schema_version"] == cli.EVOLUTION_FAILURE_SCHEMA_VERSION
    assert stated["verb"] == "freeze"
    assert stated["kind"] == evolution.NothingToActOn.kind
    assert stated["code"] == evolution.NothingToActOn.code
    assert "no batch proposal stands" in str(stated["message"])
    # stdout is what a run produced, and this run produced nothing.
    assert out == ""


def test_the_flag_changes_the_shape_and_not_the_answer(
    config: evolution.EvolutionConfig, repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The same refusal, twice: one sentence for a person, one envelope for a
    caller, and nothing else different.

    The exit status is the half that must not move — a shell already branching
    on the number keeps branching on it — and the sentence is the half that must
    survive verbatim, since it is the only thing either channel offers a human.
    """

    fill_pool(config, feed_root, TARGET)
    where = ["evolution", "freeze", "--repo", str(repo)]

    human_code, human_out, human_err = run(where, capsys)
    machine_code, machine_out, machine_err = run([*where, "--json"], capsys)

    assert human_code == machine_code
    assert human_out == machine_out == ""
    assert human_err == f"error: {envelope(machine_err)['message']}\n"


def test_a_request_nobody_answered_reaches_a_machine_as_the_family_it_is(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The third response family through the machine channel.

    A start with nothing stated is neither the success it is not nor a failure:
    the request is durable and what is missing is the half only a person states.
    The envelope says exactly that in `kind`, which is why there is no second
    field claiming this is an error — a caller reading one would report a
    request it is holding as one it never made.

    What it does not carry is the request itself. That is this verb's success
    projection and a later contract; the pinned integration is already in
    `status --json`, which is where a surface reads what a verb did.
    """

    config = evolution.load_config(lineage_repo)
    sealed(config, feed_root, capsys)
    where = ["--repo", str(lineage_repo)]
    asked = ["--source-ref", RELEASE_REF, "--expectation", "fewer remediation rounds"]

    code, out, err = run(["evolution", "replay-start", *asked, *where, "--json"], capsys)
    stated = envelope(err)

    assert code == exits(harness.Unanswered)
    assert code not in {0, exits(evolution.BatchError), exits(evolution.EvolutionError)}
    assert stated["kind"] == errors.KIND_UNANSWERED and stated["code"] == harness.Unanswered.code
    assert stated["verb"] == "replay-start"
    assert out == "", "the request is not composed twice; a machine reads it from `status --json`"

    experiment = lineage.describe(config).current.open_experiment
    pending = replay.read_replays(config, experiment).pending
    assert pending is not None, "the request is durable either way"
    reading = phase.describe(config, now=NOW).to_json()
    assert reading["replay"]["request"]["tree"] == pending.integration.tree


def test_the_one_answer_with_no_envelope_is_the_one_with_no_family(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A caller has to know where the shape stops.

    Exit 2 is a command the parser rejected or a failure carrying no family, and
    neither has a `kind` to state — so `--json` prints nothing to parse and the
    status is the whole answer. Stated here rather than left implicit, because an
    adapter that parses stderr on every non-zero exit breaks on the one case it
    cannot be handed a payload for.
    """

    with pytest.raises(SystemExit) as usage:
        cli.main(["evolution", "freeze", "--repo", str(repo), "--json", "--no-such-flag"])
    captured = capsys.readouterr()

    assert usage.value.code == cli.UNCLASSIFIED_EXIT
    assert captured.out == ""
    assert "unrecognized arguments" in captured.err
    with pytest.raises(json.JSONDecodeError):
        envelope(captured.err)


def test_the_freezes_refusals_are_three_answers_and_not_five_sentences(
    config: evolution.EvolutionConfig, repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """What a caller does about each of the freeze's refusals, which is what the
    classes are drawn at.

    Four of its sentences here and three answers behind them: build an offer,
    rebuild the one that stands, or fetch into this checkout. Two of the four are
    the same command run for two reasons, so they are one class and the reason
    stays in the sentence — splitting them would be a code an adapter routes on
    and then does the same thing with. (The fifth, a base whose canonical tree
    this checkout cannot read, is the same `checkout-unusable` answer and is
    reached through the poll: `test_evolution_batches.py`.)

    Three codes and two numbers, which is the taxonomy working rather than
    leaking: an offer nobody built and a line nobody fetched are both a state
    that permits nothing, so a shell branching on the status treats them alike
    and a caller choosing a next command reads `code`. The stale pair is the one
    that does move the number — a proposal that no longer describes the pool is a
    reading to retake, which is what an `--expect` refusal already is, and the
    contract has said so since before the freeze raised it.
    """

    where = ["--repo", str(repo), "--json"]
    fill_pool(config, feed_root, TARGET)

    nothing, _, nothing_err = run(["evolution", "freeze", *where], capsys)

    evolution.build_proposal(config, now=NOW)
    git_update_ref(config.repo_root, RELEASE_REF, git_commit(config.repo_root, "the line moves"))
    moved, _, moved_err = run(["evolution", "freeze", *where], capsys)

    evolution.build_proposal(config, now=NOW)
    evolution.sync(config, write_feed(feed_root, records(config, TARGET + 1)))
    outgrown, _, outgrown_err = run(["evolution", "freeze", *where], capsys)

    elsewhere = releasing_from(config, "refs/heads/never-fetched")
    unfetched, _, unfetched_err = run(["evolution", "freeze", "--repo", str(elsewhere.repo_root), "--json"], capsys)

    stated = [envelope(err) for err in (nothing_err, moved_err, outgrown_err, unfetched_err)]
    assert [answer["code"] for answer in stated] == [
        evolution.NothingToActOn.code,
        evolution.ProposalError.code,
        evolution.ProposalError.code,
        evolution.CheckoutUnusable.code,
    ]
    assert len({answer["code"] for answer in stated}) == 3
    assert nothing == unfetched == exits(evolution.NothingToActOn)
    assert moved == outgrown == exits(evolution.ProposalError) == exits(evolution.StaleStateError)
    assert nothing != moved
    # One class, two reasons — which is what the sentences are for.
    assert "rather than re-based" in str(stated[1]["message"])
    assert "never considered" in str(stated[2]["message"])
    assert "build one with `mandrel evolution start`" in str(stated[0]["message"])
    assert "fetch that line" in str(stated[3]["message"])


def test_a_selection_no_state_could_admit_is_the_command_and_not_the_lifecycle(
    config: evolution.EvolutionConfig, repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The line the exit-code contract draws between 3 and 4.

    A draft id that is not a kebab-case slug is refused by every state this
    lifecycle can reach, so telling an operator to change the state sends them to
    read a status that will never name what they typed. A well-formed id the gate
    does not hold is the opposite: the state is exactly what is wrong, and the
    sentence names what it holds instead.
    """

    gate(config, feed_root, "loader-fallback")
    where = ["--repo", str(repo)]

    malformed, _, malformed_err = run(["evolution", "create", "Not A Slug", *where, "--json"], capsys)
    absent, _, absent_err = run(["evolution", "create", "no-such-draft", *where, "--json"], capsys)

    assert malformed == exits(evolution.InputError)
    assert absent == exits(evolution.UnknownSubject)
    assert malformed != absent
    assert envelope(malformed_err)["code"] == evolution.InputError.code
    assert envelope(absent_err)["code"] == evolution.UnknownSubject.code


# --- the change lineage, as commands -----------------------------------------
#
# The console's other half: `status` says what may be done next, and these are
# the verbs that do it. Two properties are under test here and neither is about
# argparse. The first is that the whole lineage of a batch is reachable from the
# command line alone — no operation exists only in a library caller or only in
# orch-hub. The second is what a run reports: every one of these operations is
# redoable by being run again, so a command that found the work already done has
# to say so rather than claim the decision it read.
#
# The expectation token is the third. A surface that is not the only writer acts
# on a reading somebody else may have moved, and `--expect` is what makes that
# safe: the operation compares it under its own lock, which is the only place the
# answer holds until the write.


@pytest.fixture
def lineage_repo(repo: Path) -> Path:
    """The workspace, named for what the lineage tests need of it: an experiment
    is created at its batch's base and puts a durable ref there. `repo` is
    already under Git, because a formation reads the source line."""

    return repo


def gate(config: evolution.EvolutionConfig, feed_root: Path, *drafts: str) -> str:
    """A frozen batch whose analysis ended, with drafts waiting at the human
    admission gate — the state every verb below acts from."""

    fill_pool(config, feed_root, TARGET)
    frozen = freeze(config)
    batch_id = frozen.batch_id or ""
    close_batch(config, batch_id, frozen.analysis_task_id or "")
    for draft_id in drafts:
        draft(config, batch_id, draft_id)
    return batch_id


def token(config: evolution.EvolutionConfig) -> str:
    """What `status` publishes for a caller about to act on this reading."""

    return phase.describe(config, now=NOW).state_revision


def finish_admitted_tasks(config: evolution.EvolutionConfig) -> None:
    """Every task of the open round completed on this machine, and work
    committed on the experiment's ref — what a seal observes and pins."""

    experiment = lineage.describe(config).current.open_experiment
    for task in experiment.last_round.tasks:
        complete_task(config, task.task_id)
    git_update_ref(config.repo_root, experiment.ref, git_commit(config.repo_root, "candidate work"))


def test_the_whole_change_lineage_is_reachable_from_the_command_line(
    lineage_repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The acceptance for this half of the console: one batch driven from its
    admission gate to its outcome with `mandrel` and nothing else — a grouped
    admission, a round filled, sealed and revised, a draft declined, the attempt
    abandoned, and the batch concluded having changed nothing."""

    config = evolution.load_config(lineage_repo)
    batch_id = gate(config, feed_root, "loader-fallback", "prefetch-injection", "wider-rubric")
    where = ["--repo", str(lineage_repo)]

    code, out, _ = run(["evolution", "create", "loader-fallback", *where], capsys)
    assert code == 0
    assert f"{batch_id}-exp-01 created, round 1" in out
    assert phase.describe(config, now=NOW).phase == phase.PHASE_IMPLEMENTING

    code, out, _ = run(["evolution", "add-tasks", "prefetch-injection", *where], capsys)
    assert code == 0
    assert "already open, round 1" in out and "prefetch-injection" in out

    finish_admitted_tasks(config)
    code, out, _ = run(["evolution", "seal-round", *where], capsys)
    assert code == 0
    assert "round 1 is candidate-ready" in out
    assert "2 task(s) complete" in out
    assert phase.describe(config, now=NOW).phase == phase.PHASE_CANDIDATE_READY

    code, out, _ = run(["evolution", "revise", "--reason", "the loader fallback measured worse", *where], capsys)
    assert code == 0
    assert "round 2 open" in out
    assert phase.describe(config, now=NOW).phase == phase.PHASE_IMPLEMENTING

    code, out, _ = run(["evolution", "reject", "wider-rubric", "--reason", "not this batch's evidence", *where], capsys)
    assert code == 0
    assert "1 draft(s) declined" in out

    code, out, _ = run(
        ["evolution", "abandon", "--reason", "the approach was wrong", "--experiment", f"{batch_id}-exp-01", *where],
        capsys,
    )
    assert code == 0
    assert f"abandoned: {batch_id}-exp-01, round 2 of {batch_id}" in out
    assert phase.describe(config, now=NOW).phase == phase.PHASE_CONCLUSION_PENDING

    code, out, _ = run(["evolution", "conclude-no-change", "--reason", "no protocol change justified", *where], capsys)
    assert code == 0
    assert f"{batch_id} ended with no change" in out
    assert lineage.describe(config).current is None


def test_supersede_creates_the_successor_and_the_command_names_it(
    lineage_repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The successor is the operation's to create — the CLI has none to allocate
    or name, and what it reports is the one that came back."""

    config = evolution.load_config(lineage_repo)
    batch_id = gate(config, feed_root, "loader-fallback")
    where = ["--repo", str(lineage_repo)]
    run(["evolution", "create", "loader-fallback", *where], capsys)

    code, out, _ = run(
        ["evolution", "supersede", "--reason", "a narrower change answers the same finding", *where], capsys
    )

    assert code == 0
    assert f"{batch_id}-exp-02 (created now)" in out
    assert "empty round 1" in out
    assert lineage.describe(config).current.open_experiment.experiment_id == f"{batch_id}-exp-02"


def test_a_verb_run_again_reports_the_record_rather_than_a_second_decision(
    lineage_repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every operation here is redoable, and the difference between doing a thing
    and finding it done is the whole of what these commands report. A seal run
    twice states the pin on record; a rejection run twice writes nothing."""

    config = evolution.load_config(lineage_repo)
    gate(config, feed_root, "loader-fallback", "wider-rubric")
    where = ["--repo", str(lineage_repo)]
    run(["evolution", "create", "loader-fallback", *where], capsys)
    finish_admitted_tasks(config)

    run(["evolution", "seal-round", *where], capsys)
    code, out, _ = run(["evolution", "seal-round", *where], capsys)
    assert code == 0
    assert "was already candidate-ready" in out
    assert "this run wrote nothing: the pin above is the one on record" in out

    run(["evolution", "reject", "wider-rubric", "--reason", "answered by the loader work", *where], capsys)
    before = snapshot(lineage_repo)
    code, out, _ = run(["evolution", "reject", "wider-rubric", "--reason", "answered by the loader work", *where], capsys)
    assert code == 0
    assert "already declined" in out
    assert "this run wrote nothing" in out
    assert snapshot(lineage_repo) == before


def test_admitting_further_drafts_is_not_the_redo_of_an_admission(
    lineage_repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both leave `created` False and only one of them wrote nothing, so that
    flag cannot be what the report is derived from: `add-tasks` never creates an
    experiment and admits into one every time it is not a redo. The result says
    `recorded` for exactly this, and an adapter that guessed instead would tell an
    operator their drafts were already admitted."""

    config = evolution.load_config(lineage_repo)
    gate(config, feed_root, "loader-fallback", "prefetch-injection")
    where = ["--repo", str(lineage_repo)]
    run(["evolution", "create", "loader-fallback", *where], capsys)

    _, redone, _ = run(["evolution", "create", "loader-fallback", *where], capsys)
    assert "this run wrote nothing" in redone

    _, added, _ = run(["evolution", "add-tasks", "prefetch-injection", *where], capsys)
    assert "this run wrote nothing" not in added
    assert "prefetch-injection" in added
    admitted = {task.draft_id for task in lineage.describe(config).current.open_experiment.last_round.tasks}
    assert admitted == {"loader-fallback", "prefetch-injection"}

    _, again, _ = run(["evolution", "add-tasks", "prefetch-injection", *where], capsys)
    assert "this run wrote nothing" in again


def test_a_verb_refuses_the_reading_another_writer_moved_under(
    lineage_repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """What `--expect` is for. The token describes the state a decision was made
    against; a lifecycle that moved since is a different one, and acting on it is
    what a surface reading a stale console would otherwise do."""

    config = evolution.load_config(lineage_repo)
    gate(config, feed_root, "loader-fallback", "wider-rubric")
    where = ["--repo", str(lineage_repo)]
    stale = token(config)

    # Another writer, between the reading and the verb.
    run(["evolution", "reject", "wider-rubric", "--reason", "answered elsewhere", *where], capsys)
    before = snapshot(lineage_repo)

    code, _, err = run(["evolution", "create", "loader-fallback", "--expect", stale, *where], capsys)

    assert code == exits(evolution.StaleStateError)
    assert f"the evolution records moved since {stale} was read" in err
    assert snapshot(lineage_repo) == before

    code, out, _ = run(["evolution", "create", "loader-fallback", "--expect", token(config), *where], capsys)
    assert code == 0
    assert "created, round 1" in out


def test_a_commit_moving_no_watched_ref_leaves_an_outstanding_token_usable(
    lineage_repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The A-to-B false positive that used to exist here.

    `HEAD` was watched, so *any* commit — a docs commit, a test commit, one
    touching no canonical byte and no evolution record — moved the token and
    refused the freeze. An interactive operator never met it because `--expect`
    is optional; a script, the Web adapter and orch-hub always pass one and were
    always refused. Nothing this tool writes resolves the checkout now, so the
    commit below is not a state change and the outstanding token still runs.
    """

    config = evolution.load_config(lineage_repo)
    write_feed(feed_root, records(config, TARGET))
    where = ["--repo", str(lineage_repo), "--feed-dir", str(feed_root)]
    outstanding = token(config)

    git_commit(lineage_repo, "documentation that deploys nothing")

    assert token(config) == outstanding, "the reading is of the same state it was of"
    code, out, _ = run(["evolution", "start", "--expect", outstanding, *where], capsys)

    assert code == 0
    assert "proposal" in out
    assert evolution.load_config(lineage_repo).proposal_path.is_file()


def test_a_freeze_is_refused_on_a_token_taken_before_the_offer_was_built(
    lineage_repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--expect` matters most here, because the lock does not span the human
    window: the offer is built by one command and fixed by another, with a
    person and unbounded time in between."""

    config = evolution.load_config(lineage_repo)
    write_feed(feed_root, records(config, TARGET))
    where = ["--repo", str(lineage_repo), "--feed-dir", str(feed_root)]
    before = token(config)

    run(["evolution", "start", *where], capsys)

    assert token(config) != before, "building the offer is a write"
    code, _, err = run(["evolution", "freeze", "--repo", str(lineage_repo), "--expect", before], capsys)

    assert code == exits(evolution.StaleStateError)
    assert "the evolution records moved" in err
    assert evolution.load_batches(config) == []

    code, out, _ = run(["evolution", "freeze", "--repo", str(lineage_repo), "--expect", token(config)], capsys)

    assert code == 0 and "frozen" in out
    assert len(evolution.load_batches(config)) == 1


def test_a_token_from_another_scheme_is_refused_rather_than_compared(
    lineage_repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """"This does not describe this repository" and "this repository moved" are
    different facts, and a caller reading them as one retries the wrong thing
    forever."""

    config = evolution.load_config(lineage_repo)
    gate(config, feed_root, "loader-fallback")
    where = ["--repo", str(lineage_repo)]

    code, _, err = run(["evolution", "create", "loader-fallback", "--expect", "99-deadbeefdeadbeef", *where], capsys)

    assert code == exits(evolution.InputError)
    assert "is not a state revision this build computes" in err
    assert lineage.describe(config).current.open_experiment is None


def test_a_refused_expectation_stops_before_the_operation_writes_anything(
    lineage_repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The check is first inside the lock, before the preamble that publishes this
    machine's analysis closures — itself a write, and one that would change the
    very digest being compared. So a batch whose analysis finished and whose
    closure record is still owed stays exactly as it was."""

    config = evolution.load_config(lineage_repo)
    fill_pool(config, feed_root, TARGET)
    frozen = freeze(config)
    batch_id = frozen.batch_id or ""
    record_findings(config, batch_id)
    complete_analysis_task(config, frozen.analysis_task_id or "")
    draft(config, batch_id, "loader-fallback")
    closure = config.batches_root / batch_id / batches.CLOSURE_FILENAME
    assert not closure.exists()
    before = snapshot(lineage_repo)

    code, _, err = run(
        ["evolution", "create", "loader-fallback", "--expect", "1-0000000000000000", "--repo", str(lineage_repo)],
        capsys,
    )

    assert code == exits(evolution.StaleStateError)
    assert "the evolution records moved since" in err
    assert not closure.exists()
    assert snapshot(lineage_repo) == before


def test_the_verbs_this_cli_offers_are_the_ones_the_gate_names(
    lineage_repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A command the gate has no verb for is a lifecycle only the console
    believes in, so the names come from `phase.ACTION_*` on both sides and this
    is what holds them together. The other direction — a verb `status` emits that
    is not a command yet — is what remains of the console rather than a defect,
    so it is not asserted here.

    Every one of them also takes the state token and the workspace, which is what
    lets a surface pass the action it read from the gate straight to a command
    line.
    """

    config = evolution.load_config(lineage_repo)
    gate(config, feed_root, "loader-fallback")
    emitted = {item["action"] for item in phase.describe(config, now=NOW).to_json()["allowed_actions"]}

    wired = cli._wired_verbs(phase)

    assert wired
    assert wired <= emitted
    parser = cli.build_parser()
    for verb in sorted(wired):
        parsed = parser.parse_args(["evolution", verb, *_minimal(verb), "--expect", "1-abc", "--repo", "/tmp/x"])
        assert parsed.evolution_command == verb
        assert parsed.expect == "1-abc"


def _takes_expectation() -> set[str]:
    """The verbs that offer `--expect`."""

    return {
        name
        for name, subparser in _evolution_verbs().items()
        if any("--expect" in action.option_strings for action in subparser._actions)
    }


def test_the_token_is_offered_by_every_verb_that_decides_anything() -> None:
    """Which verbs take `--expect`, settled against the parser rather than a
    count somebody wrote down.

    The lifecycle verbs come from the gate, and the two formation verbs are
    named here because the human membership window is exactly where a reading
    goes stale — `start` offers a batch and `freeze` fixes it, with a person's
    include and exclude decisions in between and no lock spanning them.

    The four left out are left out for one reason each and none of them is an
    omission: `list` and the two readings — `status` and `portfolio` — write
    nothing, and `sync` decides nothing about the lifecycle; it imports what the
    feed has, which is the same import whatever the records did meanwhile.
    Naming them is what makes a fifth one impossible to add by accident.
    """

    takes = _takes_expectation()
    every = takes | {"list", "status", "portfolio", "sync"}

    assert takes == cli._wired_verbs(phase) | {"start", "freeze"}
    assert every == set(_evolution_verbs())


def test_a_token_from_another_scheme_and_a_token_that_moved_are_two_answers_everywhere(
    lineage_repo: Path, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every verb that takes the token tells its two bad tokens apart.

    They are two different next steps: one says take a token from this
    checkout, the other says read the lifecycle again and decide over what is
    there now — and an automated caller, which is who passes a token at all,
    retries the wrong one forever if the two arrive as one answer. Swept over
    the whole set rather than sampled, because this only ever breaks one verb
    at a time: the check is a single call at the top of each operation, and a
    verb that forgot to make it looks exactly like every other one until a
    token is actually wrong.

    The workspace is a batch at its admission gate and the arguments are only
    what each verb needs to parse. What each verb would then refuse about does
    not matter and is the point: the token is compared first thing under the
    lock, before the state is read, so the answer is the same from any state.
    `start` alone needs one more, and it is the adapter's rather than the
    operation's — a poll is named by the feed it polls, so the console resolves
    one before there is an operation for a token to be checked in at all.
    """

    config = evolution.load_config(lineage_repo)
    gate(config, feed_root, "loader-fallback")
    where = ["--repo", str(lineage_repo)]
    before = snapshot(lineage_repo)

    moved = f"{guards.STATE_REVISION_VERSION}-{'0' * 16}"
    foreign = "99-deadbeefdeadbeef"
    assert moved != token(config), "the moved token has to describe a reading that is not the one standing"

    answers = {}
    for verb in sorted(_takes_expectation()):
        feed = ["--feed-dir", str(feed_root)] if verb == "start" else []
        asked = ["evolution", verb, *_minimal(verb), *feed, *where]
        stale, _, _ = run([*asked, "--expect", moved], capsys)
        invalid, _, _ = run([*asked, "--expect", foreign], capsys)
        answers[verb] = (stale, invalid)

    assert answers == {
        verb: (exits(evolution.StaleStateError), exits(evolution.InputError)) for verb in sorted(_takes_expectation())
    }
    assert snapshot(lineage_repo) == before, "a token nobody can act on is answered before anything is written"


def test_the_documented_console_is_the_console_this_build_offers() -> None:
    """The standalone claim is the documentation's to keep: a verb an operator
    cannot find is one orch-hub is needed for, whatever the command tree says.

    Asserted against the operational reference rather than a doc of its own,
    because that is where an operator looks — and asserted verb by verb, since
    the way this drifts is one command added without its row. The states a
    target's receipt reads as go the same way: they are what an operator acts
    on, and a state nothing documents is a word with no meaning attached.
    """

    text = (REPO_ROOT / "docs" / "operations.md").read_text(encoding="utf-8")

    for verb in sorted(cli._wired_verbs(phase)):
        # Followed by a space or the closing backtick, so `assess` is not
        # documented by `assess-measure`'s row happening to start with it.
        assert f"| `{verb} " in text or f"| `{verb}`" in text, f"{verb} is wired and not documented"
    for verb in ("list", "sync", "status", "start", "freeze"):
        assert f"evolution {verb}" in text

    for holding in (
        deployment.HOLDING_CARRIES,
        deployment.HOLDING_REVERSED,
        deployment.HOLDING_BEHIND,
        deployment.HOLDING_UNPLACEABLE,
        deployment.HOLDING_UNSTATED,
        deployment.HOLDING_NO_RECEIPT,
        deployment.HOLDING_UNREADABLE,
        deployment.HOLDING_UNREGISTERED,
        deployment.HOLDING_AMBIGUOUS,
    ):
        assert f"| `{holding}` |" in text, f"{holding} is a state this reading emits and nothing documents"

    for kind in errors.ERROR_KINDS:
        # The family and the number it exits with, in one row: an operator
        # reading a status they cannot look up is reading a number.
        assert f"| `{kind}` |" in text, f"{kind} is a failure family and nothing documents it"
        assert f"| {cli.EVOLUTION_EXIT_CODES[kind]} | `{kind}` |" in text, f"{kind} is documented at another exit"

    # The two fields a surface acts on, and the shape of the token it passes
    # back.
    assert "state_revision" in text and "allowed_actions" in text
    assert f"`{phase.STATE_REVISION_VERSION}-<16 hex>`" in text
    assert f"`schema_version: {phase.SCHEMA_VERSION}`" in text


def _documented_cycle() -> list[list[str]]:
    """`operations.md`'s end-to-end cycle, one argv per documented command.

    Read as shell rather than as prose: continuations joined, `&&` split at the
    token level so a quoted one could not be mistaken for a separator, and
    comments dropped by the same lexer the reader's shell would use. Only
    `./bin/mandrel` lines are returned — the rest of the block is narration.
    """

    text = (REPO_ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
    section = text.split("#### One cycle, end to end", 1)[1].split("\n#### ", 1)[0]
    commands: list[list[str]] = []
    for block in re.findall(r"```bash\n(.*?)```", section, flags=re.S):
        for line in block.replace("\\\n", " ").splitlines():
            argv: list[str] = []
            for token in [*shlex.split(line, comments=True), "&&"]:
                if token != "&&":
                    argv.append(token)
                    continue
                if argv[:1] == ["./bin/mandrel"]:
                    commands.append(argv[1:])
                argv = []
    return commands


def test_the_documented_cycle_is_a_sequence_this_build_still_runs() -> None:
    """The one place an operator is handed commands to type, held to the parser.

    Documented against a build that has moved on, a cycle fails at the first
    verb whose arguments changed under it — and a required argument added to one
    of these verbs is exactly the change that leaves the sentence around it
    reading correctly. So every documented line is parsed rather than matched.

    Its reading half is held to more than that, because a command can parse and
    still be refused: a directional verdict rests on evidence that can carry one,
    and the sides never can while `task-shape` goes unstated on every manifest
    version (`test_placed_sides_name_the_facet_no_manifest_states`). A cycle
    documenting `assess --verdict improved` therefore hands an operator a command
    the controller refuses, which is what this asserts against: the direction
    arrives on `assess-resolve`, after the counterfactual it rests on.
    """

    commands = _documented_cycle()
    parser = cli.build_parser()

    by_verb: dict[str, list[argparse.Namespace]] = {}
    for argv in commands:
        try:
            parsed = parser.parse_args(argv)
        except SystemExit as refused:  # argparse reports a bad command line by exiting
            raise AssertionError(f"documented command does not parse: ./bin/mandrel {' '.join(argv)}") from refused
        if argv[:1] == ["evolution"]:
            by_verb.setdefault(parsed.evolution_command, []).append(parsed)

    # The cycle is the whole of one, so the verbs that end each half are in it.
    assert {"freeze", "seal-round", "promote", "assess", "assess-resolve", "settle"} <= set(by_verb)

    assert all(parsed.verdict not in assessment.DIRECTIONAL_VERDICTS for parsed in by_verb["assess"])
    assert any(parsed.verdict in assessment.DIRECTIONAL_VERDICTS for parsed in by_verb["assess-resolve"])
    # Why the first reading cannot be directional, from the predicate rather than
    # from this test's opinion: incomparable sides carry no direction whatever
    # else they came to.
    assert (
        assessment.directional_admissible(coherent=False, before_task_count=99, after_task_count=99, minimum=1)
        is False
    )


# --- the release, as commands ------------------------------------------------
#
# What the change lineage produces and what is done about it afterwards: a
# candidate carried onto the source line, a run that measured it ended or given
# up, the next batch's reading of that release, and the gate the next first
# experiment waits on. The same two properties are under test as for the lineage
# verbs — every operation reachable, and a run that found the work already done
# saying so — with one more that belongs to this half alone: a `rolled-back`
# settlement performs the reversal itself, so the console offers one verb where
# an operator might otherwise compose two.


@pytest.fixture
def release(lineage_repo: Path) -> str:
    """The source line, where it stood before anything was promoted."""

    sha = git_rev(lineage_repo, "HEAD")
    git_update_ref(lineage_repo, RELEASE_REF, sha)
    return sha


# What an operator states their harness resolved and answered. The values are
# the shared fixture harness's own, so a record written from the command line and
# one written by a library caller are the same record.
CASE_SET = ("loader-regressions", "c" * 64, "12")
EVALUATOR = ("claude", "claude-opus-5")
HARNESS = ("local-replay", "0.1.0", "d" * 64)


def running(handle: str, cases: tuple[str, str, str] = CASE_SET) -> list[str]:
    """What the harness is running, as the operator states it.

    `cases` is overridden by the one test that needs a run to state a cohort
    other than the one the fixture harness resolves: a rerun asked to reproduce a
    completed attempt.
    """

    return [
        "--case-set",
        *cases,
        "--exclude",
        "case-9",
        "needs a credentialed backend",
        "--evaluator",
        *EVALUATOR,
        "--rubric",
        "r7",
        "--harness",
        *HARNESS,
        "--handle",
        handle,
    ]


def reported(outcome: str = replay.RESULT_COMPLETED) -> list[str]:
    """What the harness answered, as the operator states it.

    A failure states its reason and nothing else: partial numbers read as a
    result nobody produced, which the record refuses.
    """

    measured_numbers = [
        "--elapsed",
        "1820.5",
        "--metric",
        "remediation-rounds",
        "rounds",
        "2.4",
        "1.6",
        replay.BETTER_LOWER,
    ]
    return [
        "--outcome",
        outcome,
        "--detail",
        "convergence improved; no regression outside the excluded case",
        *(measured_numbers if outcome == replay.RESULT_COMPLETED else []),
    ]


def sealed(config: evolution.EvolutionConfig, feed_root: Path, capsys: pytest.CaptureFixture[str]) -> str:
    """A batch with a candidate-ready round: the state a replay is started from,
    reached by the commands wired for it."""

    batch_id = gate(config, feed_root, "loader-fallback")
    where = ["--repo", str(config.repo_root)]
    run(["evolution", "create", "loader-fallback", *where], capsys)
    finish_admitted_tasks(config)
    run(["evolution", "seal-round", *where], capsys)
    return batch_id


def measured(config: evolution.EvolutionConfig, feed_root: Path, capsys: pytest.CaptureFixture[str]) -> str:
    """A batch driven to the state a promotion is argued from, by the commands
    that were wired for it: admitted, completed, sealed, and then measured."""

    batch_id = sealed(config, feed_root, capsys)
    where = ["--repo", str(config.repo_root)]
    # Nothing said about the line: configuration names it, and this is the
    # ordinary way the verb is run.
    asked = ["--expectation", "fewer remediation rounds"]
    run(["evolution", "replay-start", *asked, *running("run-0101"), *where], capsys)
    run(["evolution", "replay-conclude", *reported(), *where], capsys)
    return batch_id


def test_a_promotion_and_its_rollback_are_reachable_from_the_command_line(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The acceptance for this half: the measured candidate goes onto the source
    line and comes back off it with `mandrel` and nothing else. The rollback leaves
    the promotion exactly what it was — a new commit takes the change back out,
    and nothing about the experiment or the batch outcome is edited."""

    config = evolution.load_config(lineage_repo)
    batch_id = measured(config, feed_root, capsys)
    where = ["--repo", str(lineage_repo)]

    code, out, _ = run(
        [
            "evolution",
            "promote",
            "--reason",
            "the replay showed fewer rounds",
            "--target",
            "orch-hub",
            "--source-ref",
            RELEASE_REF,
            *where,
        ],
        capsys,
    )

    assert code == 0
    assert f"{batch_id}-exp-01 round 1 is on {RELEASE_REF}, ending {batch_id}" in out
    assert "planned" in out and "orch-hub" in out
    assert "this deployed nothing" in out
    promoted = phase.describe(config, now=NOW).last_promotion
    assert git_rev(lineage_repo, RELEASE_REF) == promoted.revision
    assert lineage.describe(config).current is None

    code, out, _ = run(
        ["evolution", "rollback", "--reason", "the next batch measured worse", "--source-ref", RELEASE_REF, *where],
        capsys,
    )

    assert code == 0
    assert f"is off {RELEASE_REF}" in out
    assert f"{batch_id}-exp-01 stays promoted and the batch stays concluded" in out
    assert lineage.describe(config).last_promoted.promotion_effective is False
    line = git_rev(lineage_repo, RELEASE_REF)
    assert line != promoted.revision

    code, out, _ = run(["evolution", "rollback", "--reason", "the next batch measured worse", *where], capsys)

    assert code == 0
    assert "this run wrote nothing: the rollback above is the one on record" in out
    assert git_rev(lineage_repo, RELEASE_REF) == line


def test_the_stated_line_reaches_the_operations_that_take_theirs_from_evidence(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--source-ref` is an assertion on both of these, and it is wired to the
    line their evidence names rather than to the configured one. A mismatch
    refuses at the surface and writes nothing, which is what makes stating it
    worth doing."""

    config = evolution.load_config(lineage_repo)
    measured(config, feed_root, capsys)
    where = ["--repo", str(lineage_repo)]
    reason = ["--reason", "the replay showed fewer rounds"]

    code, _, err = run(["evolution", "promote", *reason, "--source-ref", "refs/heads/elsewhere", *where], capsys)

    assert code == exits(evolution.BatchError)
    assert "moves the line its evidence names" in err
    assert git_rev(lineage_repo, RELEASE_REF) == release

    run(["evolution", "promote", *reason, *where], capsys)
    promoted = git_rev(lineage_repo, RELEASE_REF)

    code, _, err = run(
        ["evolution", "rollback", "--reason", "worse", "--source-ref", "refs/heads/elsewhere", *where], capsys
    )

    assert code == exits(evolution.BatchError)
    assert "puts its inverse on the line" in err
    assert git_rev(lineage_repo, RELEASE_REF) == promoted


def test_the_console_reads_what_a_planned_target_holds_beside_the_plan(
    lineage_repo: Path,
    release: str,
    feed_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The acceptance this slice is for: a promotion plans targets and deploys
    nothing, so the console reads each one's own receipt and prints the two
    lists apart. The whole cycle behind it runs through `mandrel` and nothing
    else."""

    monkeypatch.delenv("MANDREL_REGISTRY", raising=False)
    config = evolution.load_config(lineage_repo)
    measured(config, feed_root, capsys)
    where = ["--repo", str(lineage_repo)]
    run(["evolution", "promote", "--reason", "the replay showed fewer rounds", "--target", "orch-hub", *where], capsys)
    promoted = git_rev(lineage_repo, RELEASE_REF)

    target = tmp_path / "orch-hub"
    target.mkdir()
    (target / ".ai-deploy-lock.json").write_text(
        json.dumps({"schema_version": 1, "source_git_commit": release}), encoding="utf-8"
    )
    registry = lineage_repo / ".registry" / "repos.local.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps([{"name": "orch-hub", "path": str(target), "manifest": str(target / ".ai-deploy-manifest.json")}]),
        encoding="utf-8",
    )

    code, out, _ = run(["evolution", "status", *where], capsys)

    assert code == 0
    assert "planned targets: orch-hub — the plan this promotion recorded, not what they hold" in out
    assert f"orch-hub at {release[:12]} — does not carry this promotion" in out

    code, out, _ = run(["evolution", "status", "--json", *where], capsys)
    block = json.loads(out)["deployment"]

    assert code == 0
    assert block["promotion"] == promoted
    assert block["targets"] == [
        {"target": "orch-hub", "path": str(target), "revision": release, "state": "behind", "detail": None}
    ]

    # Redeployed, and the same command says so — the plan never moved.
    (target / ".ai-deploy-lock.json").write_text(
        json.dumps({"schema_version": 1, "source_git_commit": promoted}), encoding="utf-8"
    )
    code, out, _ = run(["evolution", "status", *where], capsys)

    assert code == 0
    assert f"orch-hub at {promoted[:12]} — carries this promotion" in out


def test_a_promotion_run_again_reports_the_merge_rather_than_making_a_second(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The redo every operation here has, on the one verb whose Git half cannot
    be taken back by running it twice: what a second run reports is the promotion
    on record, and the source line does not move again."""

    config = evolution.load_config(lineage_repo)
    measured(config, feed_root, capsys)
    where = ["--repo", str(lineage_repo)]
    reason = ["--reason", "the replay showed fewer rounds"]
    run(["evolution", "promote", *reason, "--target", "orch-hub", *where], capsys)
    line = git_rev(lineage_repo, RELEASE_REF)

    code, out, _ = run(["evolution", "promote", *reason, "--target", "orch-hub", *where], capsys)

    assert code == 0
    assert "was already on" in out
    assert "this run wrote nothing: the promotion above is the one on record" in out
    assert git_rev(lineage_repo, RELEASE_REF) == line


def test_a_run_and_a_request_are_ended_from_the_command_line(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The two replay verbs that need no harness, which are the two that exist
    for a harness that cannot answer: a run going is recorded as failed, and a
    request that never became one is given up. Their redo is the difference —
    ending a run reports the failure on record, while a withdrawal over nothing
    outstanding is how it reports having already landed."""

    config = evolution.load_config(lineage_repo)
    gate(config, feed_root, "loader-fallback")
    where = ["--repo", str(lineage_repo)]
    run(["evolution", "create", "loader-fallback", *where], capsys)
    finish_admitted_tasks(config)
    run(["evolution", "seal-round", *where], capsys)
    replay.start(config, FakeHarness(report=None), source_ref=RELEASE_REF, expectation="fewer rounds", now=NOW)

    code, out, _ = run(["evolution", "replay-abandon", "--reason", "the harness host was reclaimed", *where], capsys)

    assert code == 0
    assert "round 1 attempt 1 ended failed" in out
    assert "the harness host was reclaimed" in out

    code, out, _ = run(["evolution", "replay-abandon", "--reason", "the harness host was reclaimed", *where], capsys)
    assert code == 0
    assert "this run wrote nothing: the same failure was already on record" in out

    with pytest.raises(RuntimeError):
        replay.start(config, DeadHarness(), source_ref=RELEASE_REF, expectation="fewer rounds", now=NOW)

    code, out, _ = run(["evolution", "replay-withdraw", *where], capsys)

    assert code == 0
    assert "round 1 attempt 2 given up" in out
    assert "the position stays allocated" in out
    assert "stopping it is done there" in out

    code, out, _ = run(["evolution", "replay-withdraw", *where], capsys)
    assert code == 0
    assert "nothing outstanding" in out


class DeadHarness:
    """A harness that never answers, which is what leaves a request outstanding:
    the request is written before the harness is asked anything, so a start that
    dies here leaves a run that may be going and no record naming it."""

    def start(self, request: object) -> object:
        raise RuntimeError("the harness host was reclaimed before it answered")

    def poll(self, handle: str) -> object | None:
        return None


def assessing(config: evolution.EvolutionConfig, feed_root: Path, capsys: pytest.CaptureFixture[str]) -> str:
    """A promotion on the line and the batch that owes the reading of it.

    The promotion is made by the command under test in the tests above; here it
    is the state rather than the subject, so the shared cycle makes it — and the
    second batch's reports state the promoted revision, which is what places
    them after the release.
    """

    line = git_rev(config.repo_root, RELEASE_REF)
    promotion = promote_candidate(
        config,
        batch_id=FIRST,
        drafts=("loader-fallback",),
        at=NOW,
        reports=[_report(f"b{index}", index, f"2026-07-0{index}-task", line) for index in (1, 2)],
    )
    git_follow(config.repo_root, promotion.promotion_revision)
    write_manifest(
        config.batches_root,
        SECOND,
        [],
        analysis_task_id=f"2026-08-10-{SECOND}",
        reports=[
            _report(f"a{index}", index, f"2026-08-0{index}-task", promotion.promotion_revision) for index in (1, 2)
        ],
    )
    return promotion.promotion_revision


def _report(key: str, sequence: int, task_id: str, effective_revision: str) -> dict:
    """One frozen membership entry, stating the revision its target held.

    Which side of the release a report falls on is read from that revision and
    nothing else, so a report is placed by what this states rather than by when
    the batch was frozen.
    """

    return make_manifest_report(
        key=key, sequence=sequence, task_id=task_id, effective_revision=effective_revision
    )


def test_the_release_reading_and_its_gate_are_one_verb_each(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """What the batch after a promotion owes: a reading of that release, and an
    answer to the gate the next first experiment waits on. The quantities are the
    operator's — read off machine-local artifacts nothing here can re-derive —
    and everything else about the reading is the lineage's own."""

    config = evolution.load_config(lineage_repo)
    promoted = assessing(config, feed_root, capsys)
    where = ["--repo", str(lineage_repo)]

    code, out, _ = run(
        [
            "evolution",
            "assess",
            "--verdict",
            assessment.VERDICT_INCONCLUSIVE,
            "--confidence",
            assessment.CONFIDENCE_LOW,
            "--rationale",
            "no frozen manifest states what kind of work either side did",
            "--recommend",
            assessment.SETTLEMENT_RETAIN,
            "--metric",
            "review-rounds",
            "rounds",
            "1.8",
            "1.2",
            assessment.BETTER_LOWER,
            *where,
        ],
        capsys,
    )

    assert code == 0
    assert f"reads {promoted[:12]} as {assessment.VERDICT_INCONCLUSIVE}" in out
    assert "review-rounds" in out
    assert "the gate is unanswered" in out
    reading = assessment.read(config, assessment.describe_current(config).batch)
    assert reading.verdict == assessment.VERDICT_INCONCLUSIVE
    assert reading.metrics[0].before == 1.8

    code, out, _ = run(
        ["evolution", "settle", "--settlement", assessment.SETTLEMENT_RETAIN, "--reason", "nothing measured against it", *where],
        capsys,
    )

    assert code == 0
    assert f"settled {assessment.SETTLEMENT_RETAIN}" in out
    assert "the release stays the line this batch's alternatives are built on" in out
    assert git_rev(lineage_repo, RELEASE_REF) == promoted


def test_a_rolled_back_settlement_runs_the_reversal_itself(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one place this console offers a single verb where an operator might
    compose two. `assessment.settle` sequences the reversal itself — the rollback
    lands before the decision is written, and never after it — so a
    rollback-then-settle sequence of the CLI's own would be a second ordering of
    the same two writes."""

    config = evolution.load_config(lineage_repo)
    promoted = assessing(config, feed_root, capsys)
    where = ["--repo", str(lineage_repo)]
    run(
        [
            "evolution",
            "assess",
            "--verdict",
            assessment.VERDICT_INCONCLUSIVE,
            "--confidence",
            assessment.CONFIDENCE_LOW,
            "--rationale",
            "the sides place nothing on either side",
            "--recommend",
            assessment.SETTLEMENT_RETAIN,
            *where,
        ],
        capsys,
    )

    code, out, _ = run(
        [
            "evolution",
            "settle",
            "--settlement",
            assessment.SETTLEMENT_ROLLED_BACK,
            "--reason",
            "the release is not worth the risk it carries",
            *where,
        ],
        capsys,
    )

    assert code == 0
    assert f"settled {assessment.SETTLEMENT_ROLLED_BACK}" in out
    assert "committed by this settlement" in out
    line = git_rev(lineage_repo, RELEASE_REF)
    assert line != promoted
    assert lineage.describe(config).last_promoted.promotion_effective is False

    code, out, _ = run(
        [
            "evolution",
            "settle",
            "--settlement",
            assessment.SETTLEMENT_ROLLED_BACK,
            "--reason",
            "the release is not worth the risk it carries",
            *where,
        ],
        capsys,
    )
    assert code == 0
    assert "this run wrote nothing: the same answer was already on record" in out
    assert git_rev(lineage_repo, RELEASE_REF) == line


def test_a_settlement_finishes_a_reversal_the_line_had_already_taken(
    lineage_repo: Path,
    release: str,
    feed_root: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The composed reversal has a recovery state of its own, and what this run
    did to the line is not what its presence says.

    A rollback interrupted between the ref move and the record saying the line
    carries it leaves the promotion reading as effective, so the settlement still
    composes the rollback — which recognises its own commit on the line, moves
    nothing, and finishes the record (`RollbackResult.reverted` is False). The
    operator is told that rather than told this settlement committed an inverse,
    which would be a move that did not happen.
    """

    config = evolution.load_config(lineage_repo)
    promoted = assessing(config, feed_root, capsys)
    where = ["--repo", str(lineage_repo)]
    # The rollback is finished as it was prepared, so this is the sentence both
    # runs give (`rollback._require_same_rollback`).
    why = "the release is not worth the risk it carries"
    run(
        [
            "evolution",
            "assess",
            "--verdict",
            assessment.VERDICT_INCONCLUSIVE,
            "--confidence",
            assessment.CONFIDENCE_LOW,
            "--rationale",
            "the sides place nothing on either side",
            "--recommend",
            assessment.SETTLEMENT_RETAIN,
            *where,
        ],
        capsys,
    )

    with monkeypatch.context() as stopped:
        stopped.setattr(rollback, "_finish", _interrupted)
        with pytest.raises(KeyboardInterrupt):
            run(["evolution", "rollback", "--reason", why, *where], capsys)

    inverse = git_rev(lineage_repo, RELEASE_REF)
    assert inverse != promoted
    # The line took it and nothing recorded that, which is why the settlement
    # below reaches the rollback at all rather than adopting a finished one.
    assert lineage.describe(config).last_promoted.promotion_effective is True

    code, out, _ = run(
        ["evolution", "settle", "--settlement", assessment.SETTLEMENT_ROLLED_BACK, "--reason", why, *where], capsys
    )

    assert code == 0
    assert f"settled {assessment.SETTLEMENT_ROLLED_BACK}" in out
    assert "found on the line" in out
    assert "committed by this settlement" not in out
    assert git_rev(lineage_repo, RELEASE_REF) == inverse
    assert lineage.describe(config).last_promoted.promotion_effective is False


def _interrupted(*args: object, **kwargs: object) -> None:
    """Die the way a killed process does, leaving whatever landed before it."""

    raise KeyboardInterrupt("stopped before the record")


def test_the_counterfactual_is_ended_given_up_and_read_from_the_command_line(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The three verbs that act on the pinned run without asking a harness. Each
    answers a state the harness left: a request it never answered for, a run it
    stopped reporting, and a completed comparison the reading is revised on.

    The runs themselves are started through the library for `measured`'s reason.
    """

    config = evolution.load_config(lineage_repo)
    assessing(config, feed_root, capsys)
    where = ["--repo", str(lineage_repo)]
    reading = [
        "--verdict",
        assessment.VERDICT_INCONCLUSIVE,
        "--confidence",
        assessment.CONFIDENCE_LOW,
        "--rationale",
        "the sides place nothing on either side",
        "--recommend",
        assessment.SETTLEMENT_RETAIN,
    ]
    run(["evolution", "assess", *reading, *where], capsys)

    with pytest.raises(RuntimeError):
        assessment.measure(config, DeadHarness(), expectation="the release converges in fewer rounds", now=NOW)

    code, out, _ = run(["evolution", "assess-withdraw", *where], capsys)
    assert code == 0
    assert "counterfactual attempt 1 given up" in out
    assert "the position stays allocated" in out

    code, out, _ = run(["evolution", "assess-withdraw", *where], capsys)
    assert code == 0
    assert "nothing outstanding" in out

    assessment.measure(config, FakeHarness(report=None), expectation="the release converges in fewer rounds", now=NOW)

    code, out, _ = run(["evolution", "assess-abandon", "--reason", "the harness lost the handle", *where], capsys)
    assert code == 0
    assert "counterfactual attempt 2 ended failed" in out
    assert "another run answers it, from `assess-measure`" in out

    harness = FakeHarness(report=completed_report())
    assessment.measure(config, harness, expectation="the release converges in fewer rounds", now=NOW)
    assessment.conclude(config, harness, now=NOW)

    code, out, _ = run(
        [
            "evolution",
            "assess-resolve",
            "--verdict",
            assessment.VERDICT_IMPROVED,
            "--confidence",
            assessment.CONFIDENCE_HIGH,
            "--rationale",
            "the pinned run measured fewer rounds over one case set",
            "--recommend",
            assessment.SETTLEMENT_RETAIN,
            *where,
        ],
        capsys,
    )

    assert code == 0
    assert f"reads {git_rev(lineage_repo, RELEASE_REF)[:12]} as {assessment.VERDICT_IMPROVED}" in out
    assert assessment.read(config, assessment.describe_current(config).batch).verdict == assessment.VERDICT_IMPROVED


def test_a_release_verb_refuses_the_reading_another_writer_moved_under(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--expect` reaches this half too, and on the settlement it guards the one
    operation here that composes another: the check is the first statement inside
    the single hold the whole settlement — reversal included — runs under."""

    config = evolution.load_config(lineage_repo)
    assessing(config, feed_root, capsys)
    where = ["--repo", str(lineage_repo)]
    stale = token(config)

    run(
        [
            "evolution",
            "assess",
            "--verdict",
            assessment.VERDICT_INCONCLUSIVE,
            "--confidence",
            assessment.CONFIDENCE_LOW,
            "--rationale",
            "the sides place nothing on either side",
            "--recommend",
            assessment.SETTLEMENT_RETAIN,
            *where,
        ],
        capsys,
    )
    before = snapshot(lineage_repo)

    code, _, err = run(
        [
            "evolution",
            "settle",
            "--settlement",
            assessment.SETTLEMENT_ROLLED_BACK,
            "--reason",
            "the release is not worth the risk it carries",
            "--expect",
            stale,
            *where,
        ],
        capsys,
    )

    assert code == exits(evolution.StaleStateError)
    assert f"the evolution records moved since {stale} was read" in err
    assert snapshot(lineage_repo) == before
    assert git_rev(lineage_repo, RELEASE_REF) == phase.describe(config, now=NOW).last_promotion.revision


def test_a_measurement_the_record_cannot_hold_is_refused_before_anything_runs(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """What this adapter checks itself, and what it deliberately does not.

    The count is the argument's own shape and is argparse's; the two quantities
    are checked here, because a side is a number or nothing at all and an empty
    one is a side that measured nothing — a different fact from zero, and the
    reason the values are not simply required. The name and the unit are carried
    whole: the record takes any non-empty string for either, delimiters included,
    so five values are what keeps this console able to say everything the library
    can. Whether a one-sided quantity may be called better in some direction is
    the record's own rule, asked where every other reader of it is.
    """

    config = evolution.load_config(lineage_repo)
    assessing(config, feed_root, capsys)
    where = ["--repo", str(lineage_repo)]
    reading = [
        "--verdict",
        assessment.VERDICT_INCONCLUSIVE,
        "--confidence",
        assessment.CONFIDENCE_LOW,
        "--rationale",
        "the sides place nothing on either side",
        "--recommend",
        assessment.SETTLEMENT_RETAIN,
    ]

    # Last on the line, which is where the count is the whole of what is wrong:
    # an option following a short `--metric` is taken as its fifth value, and
    # what refuses then is the leftover argument or the direction vocabulary.
    with pytest.raises(SystemExit) as exit_code:
        run(["evolution", "assess", *reading, *where, "--metric", "review-rounds", "rounds", "1.8", "lower"], capsys)
    assert exit_code.value.code == 2
    assert "expected 5 arguments" in capsys.readouterr().err

    code, _, err = run(
        ["evolution", "assess", *reading, "--metric", "review-rounds", "rounds", "some", "1.2", "lower", *where],
        capsys,
    )
    assert code == exits(evolution.InputError)
    assert "where a measurement holds a number" in err
    assert assessment.read(config, assessment.describe_current(config).batch) is None

    code, out, _ = run(
        [
            "evolution",
            "assess",
            *reading,
            "--metric",
            "review-rounds",
            "rounds",
            "",
            "1.2",
            assessment.BETTER_NEITHER,
            # A name and a unit the schema takes and a packed field could not
            # carry: the delimiter is part of the value, not a boundary in it.
            "--metric",
            "latency:p95",
            "requests:s",
            "1.8",
            "1.2",
            assessment.BETTER_LOWER,
            *where,
        ],
        capsys,
    )
    assert code == 0
    reading_record = assessment.read(config, assessment.describe_current(config).batch)
    assert reading_record.metrics[0].before is None
    assert reading_record.metrics[0].after == 1.2
    assert reading_record.metrics[1].metric == "latency:p95"
    assert reading_record.metrics[1].unit == "requests:s"
    assert "latency:p95" in out


# --- the harness boundary, as commands ---------------------------------------
#
# The four verbs that ask something outside this checkout to run a case suite,
# which is the one thing this console cannot do itself. Nothing here implements
# `replay.ReplayHarness`, so the harness is the operator: they run the evaluation
# however they run it, and state what it is running and what it answered
# (`evolution/harness.py`).
#
# Three properties are under test. A run can be started and concluded from the
# command line at all, which is this slice's acceptance. What it records is what
# was stated — the cohort, the evaluator, the configuration, the numbers — since
# a console that quietly substituted any of it would put a measurement nobody
# made under this one's provenance. And a start is two commands over one request:
# what the controller pinned is printed while nothing is running, and the run
# named afterwards takes that pin rather than a second one.


def test_a_replay_is_started_and_concluded_from_the_command_line(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The acceptance for this slice, on the replay half: a sealed round measured
    with `mandrel` and nothing else.

    Two commands rather than one, and the domain was already built that way. The
    integration a harness has to exercise is computed as the request is written,
    so the first command records that request and prints the tree, and the same
    verb run again — naming the run the harness began for it — records the run.
    """

    config = evolution.load_config(lineage_repo)
    sealed(config, feed_root, capsys)
    where = ["--repo", str(lineage_repo)]
    asked = ["--source-ref", RELEASE_REF, "--expectation", "fewer remediation rounds"]
    experiment = lineage.describe(config).current.open_experiment

    code, out, _ = run(["evolution", "replay-start", *asked, *where], capsys)

    # Neither the success it is not — nothing is running — nor a failure: the
    # request is durable, and what comes back is what to go and run.
    assert code == exits(harness.Unanswered)
    assert "attempt 1 requested — nothing is running yet" in out
    pending = replay.read_replays(config, experiment).pending
    assert pending is not None
    assert not replay.read_replays(config, experiment).replays
    assert pending.integration.tree[:12] in out
    assert pending.integration.merge_input_revision[:12] in out

    code, out, _ = run(["evolution", "replay-start", *asked, *running("run-0101"), *where], capsys)

    assert code == 0
    assert "attempt 1 resumed" in out and "run-0101" in out
    started = replay.read_replays(config, experiment).replays[-1]
    assert started.running
    # The run took the request's own pin: a resume answers what was asked for
    # rather than pinning the source line a second time.
    assert started.integration == pending.integration
    assert started.expectation == "fewer remediation rounds"

    # And what it recorded of the harness is what the library path records. The
    # shared fixture harness answers with exactly these selections, so asking it
    # the same request states the other side of this console's own claim: the
    # answers arrive from a different place and the record is the same record.
    library = FakeHarness(report=completed_report())
    answered = library.start(started.request)
    assert (started.cases, started.evaluator, started.harness) == (
        answered.cases,
        answered.evaluator,
        answered.harness,
    )

    code, out, _ = run(["evolution", "replay-conclude", *reported(), "--handle", "run-0101", *where], capsys)

    assert code == 0
    assert "attempt 1 concluded completed" in out
    assert "remediation-rounds 2.4→1.6 rounds" in out
    concluded = replay.read_replays(config, experiment).replays[-1].result
    reported_by_the_library = library.poll("run-0101")
    assert concluded == replay.Result(
        outcome=reported_by_the_library.outcome,
        # The one field neither side takes from the harness: when a run was
        # observed to have ended is recorded by whoever observed it.
        concluded_at=concluded.concluded_at,
        detail=reported_by_the_library.detail,
        elapsed_seconds=reported_by_the_library.elapsed_seconds,
        metrics=reported_by_the_library.metrics,
        regressions=reported_by_the_library.regressions,
        ambiguity=reported_by_the_library.ambiguity,
    )
    assert replay.describe_evidence(config, experiment).promotable

    code, out, _ = run(["evolution", "replay-conclude", *reported(), *where], capsys)

    assert code == 0
    assert "had already concluded completed" in out
    assert "this run wrote nothing: the result above is the one on record" in out


def test_nothing_is_pinned_until_what_is_running_is_stated_in_full(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A harness states all of what it is running or none of it. None is the
    ordinary first half of a start; some of it describes a run no later reader
    could repeat, and is refused before a position is allocated — a request
    written for a typo would hold a position forever, since positions are never
    reissued."""

    config = evolution.load_config(lineage_repo)
    sealed(config, feed_root, capsys)
    where = ["--repo", str(lineage_repo)]
    asked = ["--source-ref", RELEASE_REF, "--expectation", "fewer remediation rounds"]
    before = snapshot(lineage_repo)

    code, _, err = run(["evolution", "replay-start", *asked, "--handle", "run-0101", *where], capsys)

    assert code == exits(evolution.InputError)
    assert "states all of what it is running or none of it" in err
    assert "--case-set" in err and "--evaluator" in err and "--harness" in err
    assert snapshot(lineage_repo) == before
    experiment = lineage.describe(config).current.open_experiment
    assert replay.read_replays(config, experiment).pending is None

    # The optional halves are a partial statement too. An evaluator states a
    # rubric revision only where it has one, so it cannot be required — but a
    # command naming one is an operator describing a run, and reading it as the
    # silence that records a request would allocate a position and drop what they
    # said.
    code, _, err = run(["evolution", "replay-start", *asked, "--rubric", "r7", *where], capsys)

    assert code == exits(evolution.InputError)
    assert "this states --rubric and not --case-set, --evaluator, --harness, --handle" in err
    assert snapshot(lineage_repo) == before
    assert replay.read_replays(config, experiment).pending is None


def test_a_rerun_states_what_the_attempt_it_repeats_measured(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The console is the harness, so the Protocol's reproduce-or-refuse rule is
    the console's to hold up (`replay.ReplayHarness.start`).

    What makes it a refusal rather than a note: a second attempt of a round
    replaces the first as that round's evidence, so a substituted cohort does not
    stand beside the run it was asked to repeat — it stands where that run's
    numbers stood, and is what a promotion would be argued from. The request
    survives the refusal, which is the point of writing it before the harness is
    asked: the position is held, and the same verb stating the right cohort
    resumes it.
    """

    config = evolution.load_config(lineage_repo)
    measured(config, feed_root, capsys)
    where = ["--repo", str(lineage_repo)]
    asked = ["--source-ref", RELEASE_REF, "--expectation", "fewer remediation rounds"]
    experiment = lineage.describe(config).current.open_experiment
    another_cohort = ("loader-regressions-trimmed", "e" * 64, "9")

    code, _, err = run(["evolution", "replay-start", *asked, *running("run-0102", another_cohort), *where], capsys)

    assert code == exits(evolution.BatchError)
    assert "round 1 attempt 2 reruns an attempt that completed" in err
    assert "loader-regressions-trimmed" in err and CASE_SET[0] in err
    history = replay.read_replays(config, experiment)
    # Refused before it became a run, and after the request that holds the
    # position: nothing measured this attempt, and the position is not freed.
    assert [item.attempt for item in history.replays] == [1]
    assert history.pending is not None and history.pending.attempt == 2

    code, out, _ = run(["evolution", "replay-start", *asked, *running("run-0102"), *where], capsys)

    assert code == 0
    assert "attempt 2 resumed" in out
    repeated = replay.read_replays(config, experiment).replays[-1]
    assert repeated.attempt == 2 and repeated.running
    # The whole of what a rerun reproduces, and the one thing it does not: this
    # run is the earlier one's cohort, evaluator and configuration under a name
    # of its own.
    first = replay.read_replays(config, experiment).replays[0]
    assert (repeated.cases, repeated.evaluator) == (first.cases, first.evaluator)
    assert repeated.harness.handle == "run-0102" and first.harness.handle == "run-0101"


def test_numbers_are_recorded_against_the_run_they_were_read_from(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one thing a stated harness can check about a handle. It cannot know
    whether the operator started the run they name — nothing on record has a
    handle until something is running — but it can refuse to answer for a run it
    was not asked about, which is what keeps one run's numbers off another run's
    record."""

    config = evolution.load_config(lineage_repo)
    sealed(config, feed_root, capsys)
    where = ["--repo", str(lineage_repo)]
    asked = ["--source-ref", RELEASE_REF, "--expectation", "fewer remediation rounds"]
    run(["evolution", "replay-start", *asked, *running("run-0101"), *where], capsys)
    experiment = lineage.describe(config).current.open_experiment

    code, _, err = run(["evolution", "replay-conclude", *reported(), "--handle", "run-0207", *where], capsys)

    assert code == exits(evolution.BatchError)
    assert "the run on record is 'run-0101' and this states numbers for 'run-0207'" in err
    assert replay.read_replays(config, experiment).replays[-1].running

    code, out, _ = run(["evolution", "replay-conclude", *reported(), "--handle", "run-0101", *where], capsys)

    assert code == 0
    assert "concluded completed" in out
    assert replay.read_replays(config, experiment).replays[-1].completed


def test_a_harness_verb_refuses_the_reading_another_writer_moved_under(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--expect` on the verbs that ask a harness, where it matters most: the
    check is first inside the lock and therefore before the request is written,
    so a caller whose reading has moved never leaves a position allocated for a
    run it did not decide on."""

    config = evolution.load_config(lineage_repo)
    gate(config, feed_root, "loader-fallback", "wider-rubric")
    where = ["--repo", str(lineage_repo)]
    run(["evolution", "create", "loader-fallback", *where], capsys)
    finish_admitted_tasks(config)
    run(["evolution", "seal-round", *where], capsys)
    asked = ["--source-ref", RELEASE_REF, "--expectation", "fewer remediation rounds"]
    stale = token(config)

    # Another writer, between the reading and the verb.
    run(["evolution", "reject", "wider-rubric", "--reason", "answered by the loader work", *where], capsys)
    before = snapshot(lineage_repo)

    code, _, err = run(["evolution", "replay-start", *asked, *running("run-0101"), "--expect", stale, *where], capsys)

    assert code == exits(evolution.StaleStateError)
    assert f"the evolution records moved since {stale} was read" in err
    assert snapshot(lineage_repo) == before
    experiment = lineage.describe(config).current.open_experiment
    assert replay.read_replays(config, experiment).pending is None

    code, out, _ = run(
        ["evolution", "replay-start", *asked, *running("run-0101"), "--expect", token(config), *where], capsys
    )

    assert code == 0
    assert "attempt 1 started" in out


def test_a_counterfactual_is_measured_and_concluded_from_the_command_line(
    lineage_repo: Path, release: str, feed_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The acceptance for this slice's other half, and the order the record
    keeps: the reading exists before a run is started, the run is pinned before
    the harness is asked, and only a completed run settles a direction.

    The numbers cross one boundary on the way in — a run states a baseline and a
    candidate, and this record states a before and an after — so what is asserted
    here is that the two sides arrive where the reading of the sides can be read
    against them.
    """

    config = evolution.load_config(lineage_repo)
    promoted = assessing(config, feed_root, capsys)
    where = ["--repo", str(lineage_repo)]
    read = [
        "--verdict",
        assessment.VERDICT_INCONCLUSIVE,
        "--confidence",
        assessment.CONFIDENCE_LOW,
        "--rationale",
        "the sides place nothing on either side",
        "--recommend",
        assessment.SETTLEMENT_RETAIN,
    ]
    run(["evolution", "assess", *read, *where], capsys)

    code, out, _ = run(["evolution", "assess-measure", "--expectation", "no change either way", *where], capsys)

    assert code == exits(harness.Unanswered)
    assert "counterfactual attempt 1 requested — nothing is running yet" in out
    assert promoted[:12] in out
    reading = assessment.read(config, assessment.describe_current(config).batch)
    assert reading.requested is not None and reading.counterfactual is None

    code, out, _ = run(
        ["evolution", "assess-measure", "--expectation", "no change either way", *running("cf-0201"), *where], capsys
    )

    assert code == 0
    assert "counterfactual attempt 1 resumed" in out and "cf-0201" in out
    started = assessment.read(config, assessment.describe_current(config).batch)
    assert started.requested is None and started.counterfactual.running
    assert started.counterfactual.harness.handle == "cf-0201"
    assert started.counterfactual.integration.candidate_revision == promoted

    code, out, _ = run(["evolution", "assess-conclude", *reported(), "--handle", "cf-0201", *where], capsys)

    assert code == 0
    assert "counterfactual attempt 1 concluded completed" in out
    run_result = assessment.read(config, assessment.describe_current(config).batch).counterfactual.result
    assert run_result.metrics[0].before == 2.4 and run_result.metrics[0].after == 1.6

    code, out, _ = run(
        [
            "evolution",
            "assess-resolve",
            "--verdict",
            assessment.VERDICT_IMPROVED,
            "--confidence",
            assessment.CONFIDENCE_HIGH,
            "--rationale",
            "the counterfactual measured fewer remediation rounds on the release",
            "--recommend",
            assessment.SETTLEMENT_RETAIN,
            *where,
        ],
        capsys,
    )

    assert code == 0
    assert f"reads {promoted[:12]} as {assessment.VERDICT_IMPROVED}" in out


def _minimal(verb: str) -> list[str]:
    """The arguments a verb needs to parse — not to run."""

    task = ["repo-alpha", "2026-07-01-task"] if verb in ("batch-include", "batch-exclude") else []
    drafts = ["a-draft"] if verb in ("create", "add-tasks", "reject") else []
    actor = ["--actor", "operator"] if verb in ("batch-include", "batch-exclude") else []
    reason = (
        ["--reason", "why"]
        if verb
        in (
            "batch-include",
            "batch-exclude",
            "reject",
            "revise",
            "abandon",
            "supersede",
            "conclude-no-change",
            "promote",
            "rollback",
            "replay-abandon",
            "assess-abandon",
            "settle",
        )
        else []
    )
    reading = (
        [
            "--verdict",
            assessment.VERDICT_INCONCLUSIVE,
            "--confidence",
            assessment.CONFIDENCE_LOW,
            "--rationale",
            "why",
            "--recommend",
            assessment.SETTLEMENT_RETAIN,
        ]
        if verb in ("assess", "assess-resolve")
        else []
    )
    settlement = ["--settlement", assessment.SETTLEMENT_RETAIN] if verb == "settle" else []
    # What the harness verbs need before they can run at all: what a run is
    # expected to show, or what one answered. The line is not among them —
    # configuration names it, and `--source-ref` only asserts.
    expectation = ["--expectation", "what this run should show"] if verb in ("replay-start", "assess-measure") else []
    report = (
        ["--outcome", replay.RESULT_COMPLETED, "--detail", "what the harness said"]
        if verb in ("replay-conclude", "assess-conclude")
        else []
    )
    return task + drafts + actor + reason + reading + settlement + expectation + report
