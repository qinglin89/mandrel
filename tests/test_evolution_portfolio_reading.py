"""The portfolio reading: one derivation, two tokens, and the series they describe.

The schema and the worked documents live beside this in
`tests/test_evolution_portfolio.py`; what this suite is about is the reading that
produces one — against a real repository with real operations behind it, for the
reason the console and promotion suites run that way: which side of a release a
report falls on, whether a pin chain holds, and what a target carries are all
questions about Git, and a fixture standing in for them would prove only that the
package agrees with itself.

Two properties, and the second is why the module exists at all.

**The index is the series, and each row says what the next-action reading
drops.** When a batch's membership stopped moving, the commit its attempts
started from, the denominator it was drawn out of, and how far its own reading of
the release before it has got — the last of which stays on the batch that owes it
even after that batch ends.

**The volatile token moves for exactly the readings the durable one leaves
out.** The contract names six (`evolution/README.md`, Machine surface) and each
has a regression here: a task file completing, the analysis stage ending, a
bundle staged, a target redeployed, a day passing, and an object arriving in this
clone. Every one of them asserts the same pair — `observations_revision` moves,
`state_revision` does not — because a token computed over a narrower set than its
own definition is a guarantee nothing keeps, and the way that happens is one of
these six quietly not being asked.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from evolution_fixtures import (
    RELEASE_REF,
    at_line,
    complete_task,
    experiment_round,
    git_canonical_commit,
    git_repo,
    git_rev,
    git_update_ref,
    make_manifest_report,
    make_record,
    make_repo,
    settle_release,
    promote_candidate,
    write_closure,
    write_draft,
    write_experiment,
    write_feed,
    write_manifest,
)

from mandrel import cli, evolution, lockfile
from mandrel.evolution import experiments, phase, portfolio, rollback, state
from mandrel.evolution.schema import load_schema, validate

FIRST = "evolution-batch-0001"
SECOND = "evolution-batch-0002"
THIRD = "evolution-batch-0003"
# A draft id of this batch's own: the promotion before it admitted the two the
# shared fixture names, and admission never overwrites a task file.
DRAFT = "index-router"

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "evolution" / "schemas" / "portfolio.schema.json"

PROMOTED_AT = datetime(2026, 8, 8, 9, 0, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 8, 12, 9, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def config(tmp_path: Path) -> evolution.EvolutionConfig:
    """A repository with the real contract files and a policy low enough to
    freeze a batch in a test."""

    root = git_repo(make_repo(tmp_path), tag="v2.2.0")
    path = root / "evolution" / "config.toml"
    text = path.read_text(encoding="utf-8")
    for old, new in (
        ("target_task_count = 20", "target_task_count = 3"),
        ("minimum_task_count = 10", "minimum_task_count = 3"),
    ):
        assert old in text
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")
    return evolution.load_config(root)


@pytest.fixture
def release(config: evolution.EvolutionConfig) -> str:
    """The source line where it stood before anything was promoted."""

    sha = git_rev(config.repo_root, "HEAD")
    git_update_ref(config.repo_root, RELEASE_REF, sha)
    return sha


@pytest.fixture
def feed_root(tmp_path: Path) -> Path:
    return tmp_path / "feed"


HUMAN_MOVE = {
    "justification": "the finding recurs here under another protocol",
    "actor": "maintainer",
    "moved_at": "2026-08-10T09:00:00Z",
}


def freeze_batch(
    config: evolution.EvolutionConfig,
    batch_id: str,
    *,
    effective: str | None = None,
    prefix: str = "a",
    left_out: int = 0,
    human_included: int = 0,
) -> None:
    """A frozen batch of three members whose reports were produced at
    `effective`, drawn out of a denominator this test chose.

    Formed at the source line as it stands, which is what a real formation reads
    and the base every experiment of this batch then starts from. The
    denominator is stated coherently — members plus non-selections, counted over
    unique completed tasks — because a manifest that contradicts itself is
    refused as it is read (`manifests.require_coherent_selection`).
    """

    members = [
        make_manifest_report(
            key=f"{prefix}{index}",
            sequence=index,
            task_id=f"2026-08-0{index}-task",
            effective_revision=effective,
            selection={"mode": "human-included", **HUMAN_MOVE}
            if index <= human_included
            else {"mode": "auto", "justification": None, "actor": None, "moved_at": None},
        )
        for index in (1, 2, 3)
    ]
    write_manifest(
        config.batches_root,
        batch_id,
        [],
        analysis_task_id=f"2026-08-10-{batch_id}",
        runner_protocol_revision=git_rev(config.repo_root, RELEASE_REF),
        reports=members,
        selection={
            "considered": len(members) + left_out,
            "auto_selected": len(members) - human_included,
            "human_included": human_included,
            "not_selected": [
                {
                    "repo_id": "repo-alpha",
                    "task_id": f"2026-08-1{index}-task",
                    "report_keys": [f"{prefix}x{index}"],
                    "reason": "identity-differs",
                    "justification": None,
                    "actor": None,
                    "moved_at": None,
                }
                for index in range(1, left_out + 1)
            ],
        },
    )


def analyzed(config: evolution.EvolutionConfig, batch_id: str, *, drafts: tuple[str, ...] = ()) -> None:
    """The analysis stage ended, as every clone but the one that ran it reads it."""

    (config.batches_root / batch_id / "findings.md").write_text("# Findings\n", encoding="utf-8")
    write_closure(config.batches_root, batch_id, analysis_task_id=f"2026-08-10-{batch_id}")
    for draft_id in drafts:
        write_draft(config.batches_root, batch_id, draft_id)


def read(config: evolution.EvolutionConfig, *, now: datetime | None = None) -> portfolio.Portfolio:
    return portfolio.index(config, now=now or NOW)


def payload(config: evolution.EvolutionConfig, *, now: datetime | None = None) -> dict:
    return read(config, now=now).to_json()


def rows(document: dict) -> dict[str, dict]:
    return {row["batch_id"]: row for row in document["index"]["batches"]}


def tokens(config: evolution.EvolutionConfig, *, now: datetime | None = None) -> tuple[str, str]:
    """The pair a surface re-reads on: durable first, volatile second."""

    snapshot = read(config, now=now).snapshot
    return snapshot.state_revision, snapshot.observations_revision


def registered(config: evolution.EvolutionConfig, name: str, root: Path, revision: str | None) -> None:
    """One machine-local target with a deploy receipt, as this machine's
    inventory names it."""

    root.mkdir(parents=True, exist_ok=True)
    if revision is not None:
        lockfile.write_lock(root, {"schema_version": 1, "source_git_commit": revision})
    entry = {"name": name, "path": str(root), "manifest": str(root / ".ai-deploy-manifest.json")}
    inventory = config.repo_root / ".registry" / "repos.local.json"
    inventory.parent.mkdir(parents=True, exist_ok=True)
    inventory.write_text(json.dumps([entry]), encoding="utf-8")


def fill_pool(config: evolution.EvolutionConfig, feed_root: Path, count: int) -> None:
    """Import `count` distinct completed tasks, each produced at this line."""

    records = [
        make_record(key=f"r{index}", sequence=index, task_id=f"2026-07-{index:02d}-task")
        for index in range(1, count + 1)
    ]
    evolution.sync(config, write_feed(feed_root, at_line(config, records)))


def with_target(config: evolution.EvolutionConfig, target: int) -> evolution.EvolutionConfig:
    """The same workspace with a different admission target, read back through
    the real loader so the policy under test is the one on disk."""

    path = config.repo_root / "evolution" / "config.toml"
    text = path.read_text(encoding="utf-8")
    assert "target_task_count = 3" in text
    path.write_text(text.replace("target_task_count = 3", f"target_task_count = {target}"), encoding="utf-8")
    return evolution.load_config(config.repo_root)


def fetch(repo_root: Path, source: Path, ref: str) -> None:
    """Bring another clone's objects into this one, onto a ref no record names."""

    subprocess.run(
        ["git", "-C", str(repo_root), "fetch", "--quiet", str(source), f"HEAD:{ref}"],
        check=True,
    )


def _chain(config: evolution.EvolutionConfig) -> bool | None:
    """Whether this checkout can say the open attempt's pins form one history."""

    lineage = phase.describe(config, now=NOW)
    assert lineage.ref is not None
    return lineage.ref.chain


def backdate(config: evolution.EvolutionConfig, moment: datetime) -> None:
    """Age every pooled task, so the wait rule can be reached without waiting."""

    persisted = evolution.load_state(config)
    for entry in persisted.pending:
        entry.first_imported_at = moment.isoformat().replace("+00:00", "Z")
    evolution.save_state(config, persisted)


@pytest.fixture
def series(config: evolution.EvolutionConfig, release: str) -> str:
    """A promoted batch and the batch frozen after it.

    The shape every question here needs: a series with an ending in it, a current
    batch that owes a reading of the release before it, and a promotion whose
    planned target something can be asked about.
    """

    promotion = promote_candidate(
        config,
        batch_id=FIRST,
        at=PROMOTED_AT,
        canonical=True,
        reports=[
            make_manifest_report(
                key=f"b{index}",
                sequence=index,
                task_id=f"2026-07-0{index}-task",
                effective_revision=release,
            )
            for index in (1, 2, 3)
        ],
    )
    freeze_batch(config, SECOND, effective=promotion.promotion_revision, left_out=2, human_included=1)
    analyzed(config, SECOND, drafts=(DRAFT,))
    return promotion.promotion_revision


# --- the series --------------------------------------------------------------


def test_the_index_is_every_batch_in_freeze_order(config: evolution.EvolutionConfig, series: str) -> None:
    """The series and not the batch in play: which batch is current is a position
    in this list, and so is which release a batch follows — neither is a fact a
    batch states about itself."""

    document = payload(config)

    validate(document, load_schema(SCHEMA_PATH))
    assert document["schema_version"] == portfolio.SCHEMA_VERSION
    assert document["document"] == portfolio.DOCUMENT_INDEX
    assert document["detail"] is None
    assert [row["batch_id"] for row in document["index"]["batches"]] == [FIRST, SECOND]
    assert [row["current"] for row in document["index"]["batches"]] == [False, True]
    assert document["index"]["phase"] == phase.describe(config, now=NOW).phase


def test_a_repository_that_has_frozen_nothing_reads_as_an_empty_series(
    config: evolution.EvolutionConfig,
) -> None:
    """The state every clone starts in, and the one a document has to be able to
    say: no batch, no offer, and no promotion to answer for any target about.

    `deployment` is null rather than an empty list of targets, which would read
    as a promotion that planned none."""

    document = payload(config)

    validate(document, load_schema(SCHEMA_PATH))
    assert document["index"]["phase"] == phase.PHASE_IDLE
    assert document["index"]["batches"] == []
    assert document["index"]["proposal"] is None
    assert document["index"]["deployment"] is None
    assert document["index"]["pool"]["task_count"] == 0


def test_a_row_states_what_the_next_action_reading_drops(
    config: evolution.EvolutionConfig, series: str
) -> None:
    """The three facts a series needs and a next-action projection has no use
    for: when membership stopped moving, the commit every attempt of the batch
    started from, and the denominator it was drawn out of.

    A batch of three drawn from five is a different batch from one drawn from
    three, and a recurrence claim is about the set it was drawn from."""

    row = rows(payload(config))[SECOND]
    manifest = json.loads((config.batches_root / SECOND / "manifest.json").read_text(encoding="utf-8"))

    assert row["frozen_at"] == manifest["created_at"]
    assert row["base_revision"] == manifest["runner_protocol_revision"]
    assert (row["task_count"], row["considered"], row["human_included"]) == (3, 5, 1)
    assert row["report_count"] == 3
    assert row["outcome"] is None and row["decided_at"] is None, "a current batch has not ended"


def test_a_promoted_row_carries_the_merge_unit_it_went_as(
    config: evolution.EvolutionConfig, series: str
) -> None:
    """A commit on its own is something an operator cannot check against
    anything. With the round and the candidate it names what was measured, and
    with the merge input it names the line it went onto."""

    row = rows(payload(config))[FIRST]

    assert row["outcome"] == "promoted"
    promotion = row["promotion"]
    assert promotion["revision"] == series
    assert promotion["experiment_id"].startswith(FIRST)
    assert promotion["merge_input_ref"] == RELEASE_REF
    assert promotion["planned_targets"] == ["orch-hub"]
    assert promotion["rollback"] is None
    assert set(promotion) == {
        "batch_id",
        "experiment_id",
        "revision",
        "round",
        "candidate_revision",
        "merge_input_revision",
        "merge_input_ref",
        "tree",
        "planned_targets",
        "rollback",
    }


def test_a_reversed_promotion_is_stated_on_the_row_it_belongs_to(
    config: evolution.EvolutionConfig, series: str
) -> None:
    """A rollback is not a second promotion and not an absence of the first: the
    commit stays on the line and stays recorded, and the row says a later commit
    took the change back out. The batch that was formed to judge that release
    ends with it, having decided nothing itself."""

    reversal = rollback.rollback(config, reason="the release regressed remediation", now=NOW)

    ended = rows(payload(config))
    reverted = ended[FIRST]["promotion"]["rollback"]
    assert reverted["revision"] == reversal.revision
    assert reverted["reverted_from"] == series
    assert reverted["reverted_at"] is not None and reverted["unconfirmed"] is None
    assert ended[SECOND]["outcome"] == "rolled-back"
    assert payload(config)["index"]["deployment"]["rollback"] == reversal.revision


def test_the_release_reading_sits_on_the_batch_that_owes_it(
    config: evolution.EvolutionConfig, series: str
) -> None:
    """An obligation belongs to the first batch frozen after the promotion and
    stays there (invariant 17). A later batch follows the same release and owes
    nothing of its own, so a reader looking for the outstanding reading finds one
    row and not three."""

    assert rows(payload(config))[SECOND]["release"] == portfolio.RELEASE_OWED
    assert rows(payload(config))[FIRST]["release"] == portfolio.RELEASE_NONE

    settle_release(config, at=NOW)

    assert rows(payload(config))[SECOND]["release"] == portfolio.RELEASE_SETTLED


def test_an_ended_batch_keeps_the_obligation_it_never_answered(
    config: evolution.EvolutionConfig, series: str
) -> None:
    """A batch that ends without answering does not hand the obligation on: the
    batch after it follows the same release and the record being waited on is not
    its own. So the outstanding reading is found as the row whose release is
    `owed`, wherever that row sits in the series."""

    experiments.reject(config, [DRAFT], reason="one report is not recurrence", now=NOW)
    experiments.conclude_no_change(config, reason="no cluster reached recurrence", now=NOW)
    freeze_batch(config, THIRD, effective=series, prefix="c")

    ended = rows(payload(config))
    assert ended[SECOND]["outcome"] == "no-change"
    assert ended[SECOND]["release"] == portfolio.RELEASE_OWED
    assert ended[THIRD]["release"] == portfolio.RELEASE_NONE, "a later batch owes no reading of its own"


def test_the_offer_is_a_row_with_two_counts(
    config: evolution.EvolutionConfig, release: str, feed_root: Path
) -> None:
    """What a freeze would form is not what the pool holds, and a reader given a
    single number cannot tell a short pool from a pool whose evidence is about
    another protocol."""

    fill_pool(config, feed_root, 3)
    backdate(config, NOW - timedelta(days=2))
    evolution.build_proposal(config, now=NOW)

    index = payload(config)["index"]

    assert index["phase"] == phase.PHASE_BATCH_PROPOSED
    assert index["pool"]["task_count"] == 3
    assert index["pool"]["max_wait_days"] == config.batch.max_wait_days
    offer = index["proposal"]
    assert (offer["task_count"], offer["considered"]) == (3, 3)
    assert offer["source_line_ref"] == config.source_line.ref
    assert offer["waited_days"] == 2
    assert offer["not_selected"] == []


def test_what_a_planned_target_holds_carries_no_machine_path(
    config: evolution.EvolutionConfig, series: str, tmp_path: Path
) -> None:
    """Where a repository sits on the machine that took the reading is no fact
    about the lifecycle, and a portfolio published to a surface should not carry
    one — which is the one field this projection drops from the reading `status`
    publishes."""

    registered(config, "orch-hub", tmp_path / "orch-hub", series)

    block = payload(config)["index"]["deployment"]

    assert block["promotion"] == series and block["rollback"] is None
    assert block["targets"] == [
        {"target": "orch-hub", "revision": series, "state": "carries", "detail": None}
    ]


def test_the_index_offers_no_action(config: evolution.EvolutionConfig, series: str) -> None:
    """An action offered from here would be offered by a reading that never asked
    whether the state permits it. The verbs are `status`'s answer, and this
    reading deliberately carries none of them."""

    document = payload(config)

    assert set(document) == {"schema_version", "document", "snapshot", "index", "detail"}
    assert set(document["index"]) == {"phase", "pool", "proposal", "batches", "deployment"}
    assert "allowed_actions" not in json.dumps(document)


def test_a_document_states_one_reading_and_carries_it(config: evolution.EvolutionConfig) -> None:
    """Exactly one of the two halves is stated and the other is null, paired with
    `document`. Both are nullable in the schema — the implemented subset cannot
    say "one of these" — so a document stating one and carrying neither validates,
    and the controller is what refuses it."""

    snapshot = portfolio.Snapshot(taken_at="2026-08-12T09:00:00Z", state_revision="1-a", observations_revision="1-b")

    with pytest.raises(evolution.BatchError):
        portfolio.Portfolio(document=portfolio.DOCUMENT_INDEX, snapshot=snapshot)
    with pytest.raises(evolution.BatchError):
        portfolio.Portfolio(
            document=portfolio.DOCUMENT_DETAIL,
            snapshot=snapshot,
            index=read(config).index,
        )


def test_the_two_readings_pair_on_the_durable_token(
    config: evolution.EvolutionConfig, series: str
) -> None:
    """What a client may check across the two payloads: status v11 publishes
    `state_revision` and no volatile token, so the pairing a caller can follow is
    over the durable one — read `status`, read this, read `status` again."""

    before = phase.describe(config, now=NOW).state_revision
    snapshot = read(config).snapshot
    after = phase.describe(config, now=NOW).state_revision

    assert before == snapshot.state_revision == after
    assert snapshot.state_revision.startswith(f"{phase.STATE_REVISION_VERSION}-")
    assert snapshot.observations_revision.startswith(f"{portfolio.OBSERVATIONS_REVISION_VERSION}-")
    assert snapshot.taken_at == "2026-08-12T09:00:00Z"


def test_a_repository_nobody_wrote_to_reads_the_same_twice(
    config: evolution.EvolutionConfig, series: str
) -> None:
    """Both tokens are functions of what was read and of nothing else — not of
    the moment, which is why a reading taken an hour later over an untouched
    repository is the same reading."""

    assert tokens(config) == tokens(config)


# --- the volatile token, one regression per input ----------------------------


def admitted(config: evolution.EvolutionConfig) -> experiments.AdmissionResult:
    """A round with one admitted task, on a batch whose release gate has
    answered."""

    settle_release(config, at=NOW)
    return experiments.create(config, [DRAFT], now=NOW)


def test_completing_an_admitted_task_moves_the_volatile_token_alone(
    config: evolution.EvolutionConfig, series: str
) -> None:
    """The gap the durable token leaves, and the reason the second one exists.

    `seal-round` is accepted exactly when every admitted task of the open round
    is observed complete, and that observation is a file in `.ai-tasks/` this
    lifecycle never writes. So a reading's offered actions move while the token a
    caller would re-read on does not — correctly, since nothing was written."""

    admission = admitted(config)
    durable, volatile = tokens(config)

    for item in admission.admitted:
        complete_task(config, item.task_id)

    after = tokens(config)
    assert after[0] == durable, "no record moved, so the durable token holds"
    assert after[1] != volatile


def test_the_analysis_task_completing_moves_the_volatile_token_alone(
    config: evolution.EvolutionConfig, release: str, feed_root: Path
) -> None:
    """What tells `batch-frozen` from every stage after it is a task file on the
    machine that ran the analysis. The closure record travels with the
    repository; this reading is that machine's own, and it moves the phase with
    nothing written under `evolution/`.

    Frozen through the real formation, because the task whose status is read has
    to be the one the manifest names — a file standing at that path is not this
    batch's analysis task (`batches._analysis_completion`)."""

    fill_pool(config, feed_root, 3)
    evolution.build_proposal(config, now=NOW)
    frozen = evolution.freeze(config, now=NOW)
    (config.batches_root / frozen.batch_id / "findings.md").write_text("# Findings\n", encoding="utf-8")

    durable, volatile = tokens(config)
    assert payload(config)["index"]["phase"] == phase.PHASE_DISPOSITIONS_READY

    complete_task(config, frozen.analysis_task_id)

    assert payload(config)["index"]["phase"] == phase.PHASE_CONCLUSION_PENDING
    after = tokens(config)
    assert after[0] == durable
    assert after[1] != volatile


def test_staging_a_bundle_moves_the_volatile_token_alone(
    config: evolution.EvolutionConfig, series: str
) -> None:
    """A frozen batch owns its reports wherever it was frozen, so which of them
    are staged here is this machine's own reading — and a detail read of that
    batch states it."""

    durable, volatile = tokens(config)

    (config.artifacts_root / state.artifacts_dir_name("a1")).mkdir(parents=True)

    assert tokens(config)[0] == durable
    assert tokens(config)[1] != volatile


def test_redeploying_a_target_moves_the_volatile_token_alone(
    config: evolution.EvolutionConfig, series: str, tmp_path: Path, release: str
) -> None:
    """A target's receipt is a file in a repository this system does not own, and
    a redeploy is not a lifecycle write. The reading moves anyway, which is what
    a console rendering the deployment block re-reads on."""

    target = tmp_path / "orch-hub"
    registered(config, "orch-hub", target, release)
    durable, volatile = tokens(config)
    assert payload(config)["index"]["deployment"]["targets"][0]["state"] == "behind"

    lockfile.write_lock(target, {"schema_version": 1, "source_git_commit": series})

    assert payload(config)["index"]["deployment"]["targets"][0]["state"] == "carries"
    assert tokens(config)[0] == durable
    assert tokens(config)[1] != volatile


def test_a_day_passing_moves_the_volatile_token_alone(
    config: evolution.EvolutionConfig, release: str, feed_root: Path
) -> None:
    """`max_wait_days` past its oldest member has the policy release a batch the
    same offer needed an argued force for the day before — over a repository
    nobody wrote to. The durable token is stable across that on purpose: one that
    expired overnight would refuse operations for the passage of time."""

    # An offer between the minimum and the target, which is the only state the
    # wait rule decides: at the target a freeze is released whatever the clock
    # says, and below the minimum no wait releases one.
    config = with_target(config, 4)
    fill_pool(config, feed_root, 3)
    backdate(config, NOW - timedelta(days=config.batch.max_wait_days - 1))
    evolution.build_proposal(config, now=NOW)

    durable, volatile = tokens(config)
    later = NOW + timedelta(days=1)

    assert phase.describe(config, now=NOW).decision.freeze is False
    assert phase.describe(config, now=later).decision.freeze is True
    assert tokens(config, now=later)[0] == durable
    assert tokens(config, now=later)[1] != volatile


def test_an_object_arriving_moves_the_volatile_token_alone(
    config: evolution.EvolutionConfig, release: str, tmp_path: Path
) -> None:
    """What this checkout can resolve is the sixth reading, and the one nothing
    else would notice: the revisions a record names are held by no watched ref,
    so a fetch that moves no tip still settles a pin chain that read unverified
    an hour ago.

    Written as a fetch from another clone, because that is the only honest way to
    have an object arrive: the record names a revision made elsewhere, this
    repository cannot say whether its pins form one history, and then it can. The
    ref the fetch lands on is named by no record, so the durable token — which
    watches the configured line and the refs its records name — does not move."""

    freeze_batch(config, FIRST)
    analyzed(config, FIRST)
    elsewhere = git_repo(make_repo(tmp_path / "elsewhere"), tag=None)
    unheld = git_canonical_commit(elsewhere, "candidate work done in another clone")
    write_experiment(
        config.experiments_root,
        f"{FIRST}-exp-01",
        base_revision=git_rev(config.repo_root, RELEASE_REF),
        rounds=[experiment_round(1, candidate_revision=unheld)],
    )

    durable, volatile = tokens(config)
    assert _chain(config) is None, "this clone does not hold the objects to say"

    fetch(config.repo_root, elsewhere, "refs/heads/arrived")

    assert _chain(config) is False, "the pins are answerable now, and they are not one history"
    after = tokens(config)
    assert after[0] == durable
    assert after[1] != volatile


# --- the verb ----------------------------------------------------------------


def test_the_verb_states_the_document(
    config: evolution.EvolutionConfig, series: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--json` is the whole reading at its own schema version, which is why the
    verb declares the flag itself instead of inheriting the failure channel's."""

    code = cli.main(["evolution", "portfolio", "--repo", str(config.repo_root), "--json"])
    out = capsys.readouterr().out

    assert code == 0
    stated = json.loads(out)
    validate(stated, load_schema(SCHEMA_PATH))
    assert stated["document"] == portfolio.DOCUMENT_INDEX
    assert [row["batch_id"] for row in stated["index"]["batches"]] == [FIRST, SECOND]


def test_the_verb_prints_the_series_for_a_person(
    config: evolution.EvolutionConfig, series: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without the flag it is a person reading it, and what it shows is the
    series rather than a second `status`."""

    code = cli.main(["evolution", "portfolio", "--repo", str(config.repo_root)])
    out = capsys.readouterr().out

    assert code == 0
    assert "evolution portfolio: 2 batch(es) frozen" in out
    assert FIRST in out and SECOND in out
    assert "release owed" in out
    # The promotion the deployment states are relative to, above them: every one
    # of those states is a claim about that commit.
    assert f"promoted {series[:12]}" in out
    assert "what each planned target holds now" in out
