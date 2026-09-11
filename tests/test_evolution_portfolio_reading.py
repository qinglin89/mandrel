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

The sixth is the one with several questions inside it, so it has one regression
each: an unanswerable pin chain settling, a first broken link moving under an
unchanged chain reading, the base a `create` is offered or refused on becoming
placeable, why a report places nowhere changing under an unchanged reason and an
unchanged unplaceable set, and a line found already carrying an inverse its
record does not claim. And the bracket that carries both tokens is held to the
two ways it breaks — a write or an observation landing between the two
readings, and a repository that never stops moving.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from evolution_fixtures import (
    RELEASE_REF,
    admitted_task,
    at_line,
    complete_task,
    draft_sha256,
    experiment_round,
    git_canonical_commit,
    git_delete_ref,
    git_repo,
    git_rev,
    git_sibling_commit,
    git_update_ref,
    make_manifest_report,
    make_record,
    make_repo,
    settle_release,
    promote_candidate,
    rejection,
    write_closure,
    write_draft,
    write_experiment,
    write_feed,
    write_manifest,
    write_rejected_drafts,
)

from mandrel import cli, evolution, lockfile
from mandrel.evolution import assessment, experiments, guards, phase, portfolio, rollback, state
from mandrel.evolution.lineage import REF_ABSENT, RefState
from mandrel.evolution.schema import format_rfc3339, load_schema, validate

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
    base: str | None = None,
) -> None:
    """A frozen batch of three members whose reports were produced at
    `effective`, drawn out of a denominator this test chose.

    Formed at the source line as it stands unless `base` says otherwise, which is
    what a real formation reads and the base every experiment of this batch then
    starts from — stated apart only where the point is a base this checkout reads
    differently from the clone that froze it. The denominator is stated
    coherently — members plus non-selections, counted over unique completed
    tasks — because a manifest that contradicts itself is refused as it is read
    (`manifests.require_coherent_selection`).
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
        runner_protocol_revision=base if base is not None else git_rev(config.repo_root, RELEASE_REF),
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


def fetch(repo_root: Path, source: Path, ref: str, *, source_ref: str = "HEAD") -> None:
    """Bring another clone's objects into this one, onto a ref no record names."""

    subprocess.run(
        ["git", "-C", str(repo_root), "fetch", "--quiet", str(source), f"{source_ref}:{ref}"],
        check=True,
    )


def clone(repo_root: Path, into: Path) -> Path:
    """Another working copy of this repository, holding its objects.

    What a colleague's checkout is, and the only honest way to have a commit made
    *on this history* that this repository does not yet hold: a revision built
    here would already be in its object store, and the question under test is
    what arrives.
    """

    subprocess.run(["git", "clone", "--quiet", str(repo_root), str(into)], check=True)
    return into


def _ref_state(config: evolution.EvolutionConfig) -> RefState:
    """How this checkout reads the open attempt's ref against its record's pins."""

    reading = phase.describe(config, now=NOW)
    assert reading.ref is not None
    return reading.ref


def _chain(config: evolution.EvolutionConfig) -> bool | None:
    """Whether this checkout can say the open attempt's pins form one history."""

    return _ref_state(config).chain


def _action(config: evolution.EvolutionConfig, name: str) -> phase.Action:
    """One verb as the next-action reading offers it, which is where every
    observation this suite is about is finally spent."""

    reading = phase.describe(config, now=NOW)
    return next(action for action in reading.actions if action.action == name)


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


def test_an_earlier_pin_arriving_moves_the_broken_link_and_the_token_with_it(
    config: evolution.EvolutionConfig, release: str, tmp_path: Path
) -> None:
    """The same reading one level down, and the reason a ref is digested as the
    whole diagnosis rather than as its headline.

    A record pinning base → an unheld candidate → two candidates on histories of
    their own reads as a broken chain whose first break is the pair this checkout
    could compare. The missing object arriving moves that break to the earlier
    pair — which is the revision an operator is sent to repair
    (`guards.ref_refusal`) — while `state` and `chain` say exactly what they said
    before. A token over those two alone would hold the two readings equal."""

    freeze_batch(config, FIRST)
    analyzed(config, FIRST)
    base = git_rev(config.repo_root, RELEASE_REF)
    elsewhere = git_repo(make_repo(tmp_path / "elsewhere"), tag=None)
    unheld = git_canonical_commit(elsewhere, "a candidate sealed in another clone")
    # Two commits off the batch's base and off each other: neither descends from
    # the other, so the pair between them is a break this clone can already see.
    side = git_sibling_commit(config.repo_root, base, "one\n", "a candidate on its own line")
    later = git_sibling_commit(config.repo_root, base, "two\n", "another, unrelated to it")
    write_experiment(
        config.experiments_root,
        f"{FIRST}-exp-01",
        base_revision=base,
        rounds=[
            # One draft per round: an attempt admits a draft once, and a record
            # admitting the same one twice is refused as it is read.
            experiment_round(1, candidate_revision=unheld, tasks=[admitted_task("loader-fallback")]),
            experiment_round(2, candidate_revision=side, tasks=[admitted_task("hook-side-loader")]),
            experiment_round(3, candidate_revision=later, tasks=[admitted_task(DRAFT)]),
        ],
    )

    before = _ref_state(config)
    durable, volatile = tokens(config)
    assert before.chain is False and before.chain_break == (side, later)

    fetch(config.repo_root, elsewhere, "refs/heads/arrived")

    after = _ref_state(config)
    assert after.chain_break == (base, unheld), "the first link this checkout can answer is the earlier one now"
    assert (after.state, after.chain) == (before.state, before.chain), "the headline is unchanged, by construction"
    assert tokens(config)[0] == durable
    assert tokens(config)[1] != volatile


def test_an_object_arriving_settles_the_base_the_next_admission_would_build_on(
    config: evolution.EvolutionConfig, release: str, tmp_path: Path
) -> None:
    """The reading `create` is offered or refused on, and the one no record and no
    watched ref carries.

    A batch is formed at the source line as it stands, so its base has the release
    before it in its history (invariant 17) — and whether *this* checkout can see
    that is a fact about the clone. Until it can, the admission refuses rather
    than admitting onto a base it cannot place; once the objects are here, the
    same records offer the same verb. Two different action readings, and before
    this was observed they carried one token pair."""

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
    # The line as it stands after the promotion, moved on by a commit this
    # repository does not hold: the batch below is formed at that commit, which is
    # what a freeze run in the other clone would have recorded.
    elsewhere = clone(config.repo_root, tmp_path / "elsewhere")
    unheld = git_sibling_commit(
        elsewhere, promotion.promotion_revision, "carried\n", "the line moved on in another clone"
    )
    git_update_ref(elsewhere, "refs/heads/carried", unheld)
    freeze_batch(config, SECOND, effective=promotion.promotion_revision, base=unheld)
    analyzed(config, SECOND, drafts=(DRAFT,))
    settle_release(config, at=NOW)

    durable, volatile = tokens(config)
    refused = _action(config, phase.ACTION_CREATE).refusal
    assert refused is not None and "cannot be answered in this checkout" in refused

    fetch(config.repo_root, elsewhere, "refs/heads/arrived", source_ref="refs/heads/carried")

    assert _action(config, phase.ACTION_CREATE).refusal is None, "the base places now, and the verb is offered"
    after = tokens(config)
    assert after[0] == durable
    assert after[1] != volatile


def withheld(config: evolution.EvolutionConfig, revision: str) -> None:
    """Take one commit object out of this repository, whose records still name it.

    A clone fetched to a depth that stopped short of it: the promotion is
    described by records that all stand, and the commit the release was merged
    into is not here to read a protocol off. Written rather than cloned, because
    a clone holds whatever its refs reach and the object this needs missing is
    reachable from the source line itself.
    """

    loose = config.repo_root / ".git" / "objects" / revision[:2] / revision[2:]
    # A repository this small keeps its objects loose; a packed one would need
    # unpacking first, and deleting nothing would make the test pass for the
    # wrong reason.
    assert loose.is_file()
    loose.unlink()


def _exclusions(config: evolution.EvolutionConfig) -> dict[str, tuple[str, str]]:
    """Why this checkout places each report of the release frame nowhere, as a
    surface publishes it (`status --json`, `release.cohorts.excluded`)."""

    stated = phase.describe(config, now=NOW).to_json()["release"]
    return {item["report_key"]: (item["reason"], item["detail"]) for item in stated["cohorts"]["excluded"]}


def test_an_object_arriving_moves_why_a_report_places_nowhere(
    config: evolution.EvolutionConfig, release: str, tmp_path: Path
) -> None:
    """The placement reading is where every report of a release frame goes and
    why, and not the set this clone could place nowhere.

    A truncated clone holding neither the side before the release nor the
    revision the reports after it state excludes them naming their own revision:
    that is the question `assessment._place` asks first. The revision arriving
    answers that one and leaves the same reports excluded all the same — for the
    missing *side* now, which is a different published sentence under an
    unchanged reason, and the unplaceable set is identical across the two. A
    token over that set alone holds the two readings equal, and a console
    re-reading on it would keep rendering the first explanation over the second
    one's repository."""

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
    withheld(config, promotion.merge_input_revision)
    # The revision this batch's reports state: a protocol built where that target
    # ran, which this checkout has no object for until it arrives.
    elsewhere = git_repo(make_repo(tmp_path / "elsewhere"), tag=None)
    unheld = git_canonical_commit(elsewhere, "the protocol a target elsewhere was running")
    freeze_batch(config, SECOND, effective=unheld)
    analyzed(config, SECOND, drafts=(DRAFT,))

    before = _exclusions(config)
    durable, volatile = tokens(config)
    reason, detail = before["a1"]
    assert reason == assessment.EXCLUDED_REVISION_UNRESOLVABLE
    assert unheld in detail, "the report's own revision is what this clone cannot read"

    fetch(config.repo_root, elsewhere, "refs/heads/arrived")

    after = _exclusions(config)
    assert {key: item[0] for key, item in after.items()} == {
        key: item[0] for key, item in before.items()
    }, "the same reports are unplaceable, for the same reason, in both readings"
    assert promotion.merge_input_revision in after["a1"][1], "the side nobody here holds is what excludes it now"
    assert after["a1"][1] != before["a1"][1]
    assert tokens(config)[0] == durable
    assert tokens(config)[1] != volatile


def test_a_line_carrying_an_unrecorded_inverse_is_a_reading_this_snapshot_takes(
    config: evolution.EvolutionConfig, release: str
) -> None:
    """The last clone-dependent answer the next-action reading spends: whether
    the source line has already taken an inverse commit whose record does not say
    so yet.

    That window is invisible to the records — the promotion still reads as
    effective — and what it changes is what `rollback` run again would do. The
    answer is Git's, so it is one of this snapshot's own: a truncated clone
    answers it differently from a whole one over records nobody touched."""

    promotion = promote_candidate(config, batch_id=FIRST, at=PROMOTED_AT, canonical=True)
    reversal = rollback.rollback(config, reason="the release regressed the loader", now=NOW)
    # The interrupted state itself: the inverse is on the line and the record was
    # written before it could say so (`rollback._finish`).
    path = config.batches_root / FIRST / "rollback.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["reverted_at"] = None
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    stated = dict(portfolio._read(config, NOW).observations.answers())
    offered = _action(config, phase.ACTION_ROLLBACK)

    assert stated[f"reversal:{FIRST}"] == "yes"
    assert offered.refusal is None and offered.recovery is not None
    assert f"already carries {reversal.revision[:12]}" in offered.recovery
    assert git_rev(config.repo_root, RELEASE_REF) == reversal.revision
    row = rows(payload(config))[FIRST]
    assert row["promotion"]["revision"] == promotion.promotion_revision
    assert row["promotion"]["rollback"]["reverted_at"] is None, "which the records alone cannot say"


# --- the bracket -------------------------------------------------------------


def interleaved(monkeypatch: pytest.MonkeyPatch, move) -> list[int]:
    """Take every reading with `move` applied straight after it, and count them.

    A race needs an interleaving, and a suite running in one process has to place
    the other writer itself. This places it exactly where the bracket exists to
    catch one: after a reading has been taken and before the next is, which is
    the window a reading published unbracketed would straddle.

    `move` is handed the ordinal of the reading it follows, so a test can move
    something once or on every reading — the difference between a writer that
    finished and a repository being written to continuously, which the bracket
    owes two different answers.
    """

    ordinals: list[int] = []
    taken = portfolio._read

    def interrupted(config: evolution.EvolutionConfig, moment: datetime):
        ordinals.append(len(ordinals) + 1)
        reading = taken(config, moment)
        move(len(ordinals))
        return reading

    monkeypatch.setattr(portfolio, "_read", interrupted)
    return ordinals


def test_a_record_written_between_the_two_readings_is_read_again(
    config: evolution.EvolutionConfig, series: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The durable half of the bracket: the token is read, the reading is taken,
    and the token is read again — so a writer that finished in between is caught
    by the inputs no longer matching, and the pair published is of the state that
    is actually there rather than of two."""

    ordinals = interleaved(
        monkeypatch,
        lambda ordinal: write_draft(config.batches_root, SECOND, "late-proposal") if ordinal == 1 else None,
    )

    document = read(config)

    assert ordinals == [1, 2, 3], "the first pair straddled the write, so the reading was taken again"
    monkeypatch.undo()
    assert document.snapshot.state_revision == phase.describe(config, now=NOW).state_revision


def test_an_observation_moving_between_the_two_readings_is_read_again(
    config: evolution.EvolutionConfig, series: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The volatile half, which the durable bracket cannot catch: nothing was
    written, so the inputs match on both sides of a task file finishing. What
    catches it is the reading being taken twice and its answers compared — the
    reason this module takes one derivation more than `status` does."""

    admission = admitted(config)
    task_id = admission.admitted[0].task_id
    before = read(config).snapshot.observations_revision

    ordinals = interleaved(
        monkeypatch, lambda ordinal: complete_task(config, task_id) if ordinal == 1 else None
    )
    document = read(config)

    assert ordinals == [1, 2, 3]
    assert document.snapshot.observations_revision != before, "the published reading is the one after it"
    monkeypatch.undo()
    assert (document.snapshot.state_revision, document.snapshot.observations_revision) == tokens(config)


def test_a_repository_observed_moving_on_every_reading_is_reported(
    config: evolution.EvolutionConfig, series: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And the third losing as well is a state to report rather than to keep
    retrying through: a pair naming a moment nothing was ever read at is the one
    thing a caller handing these tokens back is entitled to rely on not getting,
    so the reading fails instead of publishing one."""

    staging = iter(("b1", "b2", "b3", "a1"))

    def stage(_ordinal: int) -> None:
        (config.artifacts_root / state.artifacts_dir_name(next(staging))).mkdir(parents=True)

    ordinals = interleaved(monkeypatch, stage)

    with pytest.raises(evolution.BatchError, match="changed under the portfolio reading"):
        read(config)

    assert len(ordinals) == 1 + portfolio._SNAPSHOT_ATTEMPTS, "bounded, and every attempt spent"


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


# --- one subject, expanded ---------------------------------------------------


def expand(
    config: evolution.EvolutionConfig,
    kind: str,
    subject_id: str | None = None,
    *,
    now: datetime | None = None,
) -> portfolio.Portfolio:
    return portfolio.detail(config, kind, subject_id, now=now or NOW)


def expanded(
    config: evolution.EvolutionConfig,
    kind: str,
    subject_id: str | None = None,
    *,
    now: datetime | None = None,
) -> dict:
    """One detail document, validated against the schema it is published at.

    Validated here rather than in one test of its own, because every question
    below is about a document a console would be handed: a field this reading
    gets right in a shape the schema refuses is not an answer anybody receives.
    """

    document = expand(config, kind, subject_id, now=now).to_json()
    validate(document, load_schema(SCHEMA_PATH))
    return document


def subject(
    config: evolution.EvolutionConfig,
    kind: str,
    subject_id: str | None = None,
    *,
    now: datetime | None = None,
) -> dict:
    return expanded(config, kind, subject_id, now=now)["detail"][kind]


def test_a_detail_states_one_subject_and_carries_it(
    config: evolution.EvolutionConfig, series: str
) -> None:
    """`kind` and the slot are one statement. Every other slot is a stated null,
    which is the union the schema subset cannot express and this controller
    checks in both directions."""

    document = expanded(config, portfolio.DETAIL_BATCH, SECOND)

    assert document["document"] == portfolio.DOCUMENT_DETAIL
    assert document["index"] is None
    detail = document["detail"]
    assert (detail["kind"], detail["id"]) == (portfolio.DETAIL_BATCH, SECOND)
    assert set(detail) == {"kind", "id", *portfolio.DETAIL_KINDS}
    assert detail["proposal"] is None and detail["pool"] is None
    assert detail["batch"] is not None


def test_a_batch_detail_is_the_membership_its_manifest_fixed(
    config: evolution.EvolutionConfig, series: str
) -> None:
    """The frozen denominator, member by member, with what a human argued in
    attributed on the member they argued for.

    Read off the manifest and never recomputed from the pool, which is
    machine-local and has moved: a batch's recurrence claim is about the set it
    was drawn from.
    """

    batch = subject(config, portfolio.DETAIL_BATCH, SECOND)

    assert batch["batch_id"] == SECOND
    assert batch["base_revision"] == series
    assert (batch["forced"], batch["force_justification"]) == (False, None)
    assert batch["selection"] == {"considered": 5, "auto_selected": 2, "human_included": 1}
    assert [report["report_key"] for report in batch["reports"]] == ["a1", "a2", "a3"]
    argued, automatic = batch["reports"][0], batch["reports"][1]
    assert argued["mode"] == "human-included"
    assert (argued["actor"], argued["justification"]) == (HUMAN_MOVE["actor"], HUMAN_MOVE["justification"])
    assert automatic["mode"] == "auto"
    assert (automatic["actor"], automatic["justification"], automatic["moved_at"]) == (None, None, None)
    # What produced the evaluation and what protocol it ran under, per report:
    # recorded facts the analysis states, never membership gates.
    assert argued["protocol"]["effective_revision"] == series
    assert set(argued["evaluator"]) == {"backend", "model", "rubric_revision"}
    assert [entry["reason"] for entry in batch["not_selected"]] == ["identity-differs"] * 2
    assert batch["not_selected"][0]["report_keys"] == ["ax1"]


def test_a_batch_detail_states_its_stage_and_what_this_machine_holds(
    config: evolution.EvolutionConfig, series: str
) -> None:
    """The analysis stage and the staged evidence are two different kinds of
    fact: one ends a phase, and the other is this machine's own answer about
    bundles a clone may never have seen."""

    batch = subject(config, portfolio.DETAIL_BATCH, SECOND)

    assert batch["current"] is True and batch["outcome"] is None
    assert batch["analysis"] == {
        "task_id": f"2026-08-10-{SECOND}",
        "findings_recorded": True,
        "complete": True,
    }
    # A manifest with no bundles behind it is the ordinary state of every clone
    # but the one that imported them, and the hashes still say what was analyzed.
    assert batch["evidence"] == {"report_count": 3, "local": 0}


def test_a_batch_detail_carries_the_drafts_themselves(
    config: evolution.EvolutionConfig, series: str
) -> None:
    """A console that had to open `proposed-tasks/` to show an operator what they
    are being asked to admit is a second reader of the batch directory, and a
    decision made from an id is not a decision."""

    write_draft(config.batches_root, SECOND, "loader-rewrite")
    write_rejected_drafts(
        config.batches_root,
        SECOND,
        [rejection("loader-rewrite", reason="the deploy-time rendering it proposes is a payload change")],
    )

    gate = subject(config, portfolio.DETAIL_BATCH, SECOND)["gate"]

    drafts = {draft["draft_id"]: draft for draft in gate["drafts"]}
    assert set(drafts) == {DRAFT, "loader-rewrite"}
    waiting = drafts[DRAFT]
    assert (waiting["state"], waiting["experiment_id"], waiting["decided_at"]) == ("waiting", None, None)
    # Nothing has pinned anything yet, and the bytes are here to read.
    assert waiting["recorded_sha256"] is None
    assert waiting["content_sha256"] == draft_sha256(DRAFT)
    assert waiting["content"].startswith("---\n")
    declined = drafts["loader-rewrite"]
    assert declined["state"] == "declined"
    assert declined["reason"].startswith("the deploy-time rendering")
    assert declined["decided_at"] == "2026-08-02T09:00:00Z"
    assert declined["recorded_sha256"] == declined["content_sha256"] == draft_sha256("loader-rewrite")
    assert gate["unusable"] == []


def test_a_draft_edited_after_the_decision_that_took_it_reads_as_two_hashes(
    config: evolution.EvolutionConfig, series: str
) -> None:
    """The two hashes are stated apart rather than assumed equal: that difference
    is the only thing showing the file on disk is no longer what was decided."""

    write_draft(config.batches_root, SECOND, "loader-rewrite")
    write_rejected_drafts(config.batches_root, SECOND, [rejection("loader-rewrite")])
    path = config.batches_root / SECOND / "proposed-tasks" / "loader-rewrite.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nedited after the decision\n", encoding="utf-8")

    gate = subject(config, portfolio.DETAIL_BATCH, SECOND)["gate"]

    declined = next(draft for draft in gate["drafts"] if draft["draft_id"] == "loader-rewrite")
    assert declined["recorded_sha256"] == draft_sha256("loader-rewrite")
    assert declined["content_sha256"] != declined["recorded_sha256"]


def test_a_decided_draft_whose_file_is_gone_keeps_what_was_decided(
    config: evolution.EvolutionConfig, series: str
) -> None:
    """Reported rather than refused: the record keeps the id and the hash of what
    was decided, and the bytes are recoverable from history."""

    (config.batches_root / FIRST / "proposed-tasks" / "loader-fallback.md").unlink()

    gate = subject(config, portfolio.DETAIL_BATCH, FIRST)["gate"]

    gone = next(draft for draft in gate["drafts"] if draft["draft_id"] == "loader-fallback")
    assert gone["state"] == "consumed"
    assert (gone["content"], gone["content_sha256"]) == (None, None)
    assert gone["recorded_sha256"] == draft_sha256("loader-fallback")


def test_a_consumed_draft_names_the_attempt_that_admitted_it(
    config: evolution.EvolutionConfig, series: str
) -> None:
    """A draft appears once, in the state it is actually in — rather than in
    whichever of four id lists a reader thought to look at."""

    gate = subject(config, portfolio.DETAIL_BATCH, FIRST)["gate"]

    consumed = {draft["draft_id"]: draft for draft in gate["drafts"]}
    assert set(consumed) == {"loader-fallback", "hook-side-loader"}
    taken = consumed["loader-fallback"]
    assert taken["state"] == "consumed"
    assert taken["experiment_id"] == f"{FIRST}-exp-01"
    assert taken["decided_at"] is not None
    # An admission's reason belongs to the round it opened, not to the draft.
    assert taken["reason"] is None


def test_every_attempt_reads_in_one_shape(config: evolution.EvolutionConfig, series: str) -> None:
    """A reader reconstructing what happened needs the same fields from a
    terminal attempt it needs from the one running, and `decision` is what tells
    them apart."""

    batch = subject(config, portfolio.DETAIL_BATCH, FIRST)

    (attempt,) = batch["experiments"]
    assert attempt["experiment_id"] == f"{FIRST}-exp-01"
    assert attempt["open"] is False
    assert attempt["decision"]["outcome"] == "promoted"
    assert attempt["decision"]["promotion_revision"] == series
    assert attempt["prepared_promotion"]["revision"] == series
    (round_,) = attempt["rounds"]
    assert round_["number"] == 1
    assert [task["draft_id"] for task in round_["tasks"]] == ["hook-side-loader", "loader-fallback"]
    assert round_["seal"]["candidate_revision"] == attempt["prepared_promotion"]["candidate_revision"]
    # The whole diagnosis this checkout took of the attempt's ref — the same
    # reading the volatile token digests, published field by field.
    assert attempt["ref_state"]["ref"] == attempt["ref"]
    assert attempt["ref_state"]["state"] == "at-pinned-revision"


def test_a_terminal_attempts_replay_history_is_readable(
    config: evolution.EvolutionConfig, series: str
) -> None:
    """What measured an attempt is what says why it ended that way, and the
    next-action reading holds the open attempt's evidence only — so before this
    document a terminal attempt's runs were read by nothing at all."""

    assert phase.describe(config, now=NOW).replays is None

    (attempt,) = subject(config, portfolio.DETAIL_BATCH, FIRST)["experiments"]

    replays = attempt["replays"]
    assert (replays["request"], replays["withdrawn"]) == (None, [])
    (run,) = replays["runs"]
    assert (run["round"], run["attempt"]) == (1, 1)
    assert run["tree"] == attempt["prepared_promotion"]["tree"]
    # The cohort, the evaluator and the harness: a run reported as an outcome and
    # a date cannot be asked what measured it.
    assert run["cases"]["case_set_id"] == "loader-regressions"
    assert [item["case_id"] for item in run["cases"]["excluded"]] == ["case-9"]
    assert run["evaluator"]["backend"] == "claude"
    assert run["harness"]["id"] == "local-replay"
    assert run["result"]["outcome"] == "completed"
    assert [item["metric"] for item in run["result"]["metrics"]] == ["remediation-rounds"]


def test_an_open_attempts_admitted_tasks_state_both_completion_answers(
    config: evolution.EvolutionConfig, series: str
) -> None:
    """The durable observation and this machine's own, side by side. Null where
    nothing was asked — a task the record shows complete is not consulted at
    all, which is a different absence from a clone that has no task tree."""

    # The release gate first: the frozen base is held to its answer before
    # anything may be admitted on it (invariant 17).
    settle_release(config, at=NOW)
    admission = experiments.create(config, [DRAFT], now=NOW)

    (task,) = subject(config, portfolio.DETAIL_BATCH, SECOND)["experiments"][0]["rounds"][0]["tasks"]
    assert (task["completion_observed_at"], task["file_complete"]) == (None, False)

    complete_task(config, admission.admitted[0].task_id)

    (task,) = subject(config, portfolio.DETAIL_BATCH, SECOND)["experiments"][0]["rounds"][0]["tasks"]
    assert (task["completion_observed_at"], task["file_complete"]) == (None, True)


def test_the_release_reading_a_batch_owes_sits_in_its_own_detail(
    config: evolution.EvolutionConfig, series: str
) -> None:
    """The frame is stated whether or not anything was recorded: two empty sides
    are absent evidence rather than a reading against the release, and only the
    exclusions say which of the two it is."""

    release = subject(config, portfolio.DETAIL_BATCH, SECOND)["release"]

    assert release["assessed"]["revision"] == series
    assert release["assessed"]["standing"] is True
    assert release["assessed"]["source_line"] == {"standing": True, "rollback_revision": None}
    assert release["sides"]["before"]["report_keys"] == ["b1", "b2", "b3"]
    assert release["sides"]["after"]["report_keys"] == ["a1", "a2", "a3"]
    assert release["sides"]["minimum_task_count"] == config.batch.minimum_task_count
    assert release["sides"]["unverified"] == []
    # `unstated` and `differing` are two different answers: no manifest states
    # the shape of the work, so the sides say nothing about the release either
    # way and a direction has to rest on the counterfactual instead.
    assert release["comparability"]["coherent"] is False
    assert release["comparability"]["unstated"] == ["task-shape"]
    assert release["comparability"]["differing"] == []
    assert release["comparability"]["sides_support_direction"] is False
    assert release["counterfactual"]["state"] == "none"
    # Nothing recorded yet, which with the frame stated is an obligation
    # outstanding rather than an answer of no.
    assert (release["reading"], release["decision"]) == (None, None)
    assert subject(config, portfolio.DETAIL_BATCH, FIRST)["release"] is None


def test_a_settled_release_carries_the_reading_and_the_answer(
    config: evolution.EvolutionConfig, series: str
) -> None:
    """The reading recommends and the human settles, and the two are allowed to
    disagree — which is why both are published."""

    settle_release(config, at=NOW)

    release = subject(config, portfolio.DETAIL_BATCH, SECOND)["release"]

    assert release["reading"]["verdict"] == assessment.VERDICT_INCONCLUSIVE
    assert release["reading"]["recommendation"] == assessment.SETTLEMENT_RETAIN
    assert release["reading"]["directional"] is False
    assert release["decision"]["settlement"] == assessment.SETTLEMENT_RETAIN
    assert release["decision"]["rollback_revision"] is None


def test_a_batch_that_ended_states_how(config: evolution.EvolutionConfig, series: str) -> None:
    """The ending and the merge unit it went as. Taken from the batch's outcome
    rather than from the attempt, because a version-1 experiment record kept no
    merge unit at all."""

    batch = subject(config, portfolio.DETAIL_BATCH, FIRST)

    assert batch["current"] is False
    assert batch["outcome"]["outcome"] == "promoted"
    assert batch["outcome"]["experiment_id"] == f"{FIRST}-exp-01"
    promotion = batch["outcome"]["promotion"]
    assert promotion["revision"] == series
    assert promotion["planned_targets"] == ["orch-hub"]
    assert promotion["rollback"] is None


def test_the_standing_offer_expands_member_by_member(
    config: evolution.EvolutionConfig, release: str, feed_root: Path
) -> None:
    """The offer is the one thing in this reading that can say a human has been
    offered a membership and has not answered, and either move takes the two
    identity halves exactly as they stand here."""

    fill_pool(config, feed_root, 3)
    evolution.build_proposal(config, now=NOW)
    evolution.exclude(
        config,
        "repo-alpha",
        "2026-07-03-task",
        justification="the evaluation ran against a rubric nothing else in the pool used",
        actor="maintainer",
        now=NOW,
    )

    offer = subject(config, portfolio.DETAIL_PROPOSAL)

    assert offer["source_line_ref"] == config.source_line.ref
    assert offer["base_revision"] == release
    assert [member["task_id"] for member in offer["selected"]] == ["2026-07-01-task", "2026-07-02-task"]
    assert {member["mode"] for member in offer["selected"]} == {"auto"}
    (left_out,) = offer["not_selected"]
    assert left_out["task_id"] == "2026-07-03-task"
    assert left_out["reason"] == "human-excluded"
    assert (left_out["actor"], left_out["moved_at"]) == ("maintainer", format_rfc3339(NOW))
    assert left_out["justification"].startswith("the evaluation ran against a rubric")


def test_the_offer_and_the_series_it_stands_before_are_one_reading(
    config: evolution.EvolutionConfig, release: str, feed_root: Path
) -> None:
    """Both documents carry the same snapshot, so a console assembling an index
    and several details knows whether it assembled one reading or several. The
    question set cannot depend on what was asked for, or the pair could never say
    that."""

    fill_pool(config, feed_root, 3)
    evolution.build_proposal(config, now=NOW)

    series_document = payload(config)
    offer_document = expanded(config, portfolio.DETAIL_PROPOSAL)

    assert offer_document["snapshot"] == series_document["snapshot"]


def test_a_batch_detail_and_the_series_are_one_reading(
    config: evolution.EvolutionConfig, series: str
) -> None:
    """The same property across the subject whose detail is the expensive one: a
    batch expanded is read at the same moment as the row that named it."""

    assert expanded(config, portfolio.DETAIL_BATCH, SECOND)["snapshot"] == payload(config)["snapshot"]


def test_a_batch_no_freeze_ever_made_is_refused(
    config: evolution.EvolutionConfig, series: str
) -> None:
    """A detail carrying no subject would be a document with nothing in it and no
    way to say so, so what the state does not hold is refused and the sentence
    names what it does hold."""

    with pytest.raises(evolution.UnknownSubject) as raised:
        expand(config, portfolio.DETAIL_BATCH, "evolution-batch-0009")

    assert FIRST in str(raised.value) and SECOND in str(raised.value)


def test_an_offer_that_does_not_stand_is_refused(
    config: evolution.EvolutionConfig, series: str
) -> None:
    """The offer is not a thing that exists in every state: a batch being current
    means there is nothing to offer, and an empty document would read as an offer
    that selected nothing."""

    with pytest.raises(evolution.NothingToActOn) as raised:
        expand(config, portfolio.DETAIL_PROPOSAL)

    assert "no batch proposal stands" in str(raised.value)


def test_a_subject_whose_builder_has_not_landed_is_refused(
    config: evolution.EvolutionConfig, series: str
) -> None:
    """The pool's shape is published and its builder is not here yet. Refused by
    name rather than answered with an empty document, so a console reading the
    vocabulary learns which subjects this build actually takes."""

    with pytest.raises(evolution.UnknownSubject) as raised:
        expand(config, portfolio.DETAIL_POOL)

    assert portfolio.DETAIL_POOL in str(raised.value)


def test_the_verb_expands_one_subject(
    config: evolution.EvolutionConfig, series: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """What bounds the payload, at the command line: no subject is the series,
    and one subject is that subject expanded."""

    code = cli.main(
        ["evolution", "portfolio", "--repo", str(config.repo_root), "--batch", FIRST, "--json"]
    )
    out = capsys.readouterr().out

    assert code == 0
    stated = json.loads(out)
    validate(stated, load_schema(SCHEMA_PATH))
    assert stated["document"] == portfolio.DOCUMENT_DETAIL
    assert stated["detail"]["kind"] == portfolio.DETAIL_BATCH
    assert stated["detail"]["batch"]["batch_id"] == FIRST


def test_the_verb_summarises_an_expanded_subject_for_a_person(
    config: evolution.EvolutionConfig, series: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """A summary and never the document: the subject of a detail read is every
    draft's Markdown and every run's metrics, and printing it would be a page of
    JSON without the brackets."""

    code = cli.main(["evolution", "portfolio", "--repo", str(config.repo_root), "--batch", FIRST])
    out = capsys.readouterr().out

    assert code == 0
    assert f"evolution portfolio: batch {FIRST}" in out
    assert f"{FIRST}-exp-01 — 1 round(s), 1 replay run(s), promoted" in out
    assert "no release before this batch" in out


def test_the_verb_takes_one_subject_at_a_time(config: evolution.EvolutionConfig) -> None:
    """Two subjects would be two readings, and one snapshot could not say which
    of them it was taken over."""

    with pytest.raises(SystemExit):
        cli.main(["evolution", "portfolio", "--repo", str(config.repo_root), "--batch", FIRST, "--proposal"])


def test_an_attempt_whose_ref_this_checkout_never_fetched_still_reads(
    config: evolution.EvolutionConfig, series: str
) -> None:
    """The ordinary state of any clone that never fetched the experiment
    namespace: the record pins a revision and the ref is not here.

    A fact about the checkout and never a finding about the record — and never a
    reason to refuse the history either, which is what a document published for
    a console to render has to hold.
    """

    git_delete_ref(config.repo_root, f"refs/evolution/experiments/{FIRST}-exp-01")

    (attempt,) = subject(config, portfolio.DETAIL_BATCH, FIRST)["experiments"]

    assert attempt["ref_state"]["state"] == REF_ABSENT
    assert attempt["ref_state"]["tip"] is None
    # The record's own pin is durable and still stated beside the absence.
    assert attempt["ref_state"]["pinned"] == attempt["rounds"][0]["seal"]["candidate_revision"]
    assert attempt["decision"]["outcome"] == "promoted"


# --- the bracket, over what a detail opens for itself -------------------------


def bracketed(monkeypatch: pytest.MonkeyPatch, move) -> list[int]:
    """Write straight after each durable-input comparison, and count them.

    The other placement a race needs, and `interleaved`'s complement: that one
    puts the other writer between the two readings, where the bracket is built to
    catch it, and this one puts it at the moment the bracket closes — the window
    a projection performed after that comparison would read in, and no comparison
    would ever see.

    `move` is handed the ordinal of the comparison it follows, so a test can
    choose the side of the boundary it is about: after the opening read is under
    the projection, after the closing read is outside the bracket entirely.
    """

    ordinals: list[int] = []
    taken = guards.read_inputs

    def watched(config: evolution.EvolutionConfig):
        inputs = taken(config)
        ordinals.append(len(ordinals) + 1)
        move(len(ordinals))
        return inputs

    monkeypatch.setattr(guards, "read_inputs", watched)
    return ordinals


def test_a_draft_written_after_the_bracket_closed_is_not_in_the_document(
    config: evolution.EvolutionConfig, series: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A detail opens records the derivation has no use for — the drafts' own
    bytes among them — and those are `state_revision`'s inputs like any other,
    since `guards` walks `evolution/`. Projected after the closing comparison,
    they would be published under a token taken before they were written: bytes
    no comparison ever saw, beside a pair claiming to describe them.

    So the projection runs inside the bracket, and a write landing after it is
    simply the next reading's."""

    path = config.batches_root / SECOND / "proposed-tasks" / f"{DRAFT}.md"
    before = path.read_text(encoding="utf-8")
    durable = tokens(config)[0]

    ordinals = bracketed(
        monkeypatch,
        lambda ordinal: path.write_text(before + "\nappended after the bracket\n", encoding="utf-8")
        if ordinal == 2
        else None,
    )
    document = expanded(config, portfolio.DETAIL_BATCH, SECOND)

    assert ordinals == [1, 2], "the bracket closed once, and the write landed outside it"
    monkeypatch.undo()
    waiting = next(
        draft for draft in document["detail"]["batch"]["gate"]["drafts"] if draft["draft_id"] == DRAFT
    )
    assert waiting["content"] == before, "the bytes the published token was taken over"
    assert waiting["content_sha256"] == draft_sha256(DRAFT)
    assert document["snapshot"]["state_revision"] == durable
    assert tokens(config)[0] != durable, "and the write did move the durable token"


def test_a_rejection_recorded_after_the_bracket_closed_is_not_in_the_document(
    config: evolution.EvolutionConfig, series: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same boundary for the record the gate is assembled from. A decision
    read after the closing comparison would combine the `Gate` this reading
    derived with a record written after it — one draft in two states, under a
    token that knows about neither."""

    durable = tokens(config)[0]

    ordinals = bracketed(
        monkeypatch,
        lambda ordinal: write_rejected_drafts(
            config.batches_root,
            SECOND,
            [rejection(DRAFT, reason="the loader change it proposes belongs to the payload task")],
        )
        if ordinal == 2
        else None,
    )
    document = expanded(config, portfolio.DETAIL_BATCH, SECOND)

    assert ordinals == [1, 2]
    monkeypatch.undo()
    draft = next(
        item for item in document["detail"]["batch"]["gate"]["drafts"] if item["draft_id"] == DRAFT
    )
    assert (draft["state"], draft["decided_at"]) == ("waiting", None), "the gate the token was taken over"
    assert document["snapshot"]["state_revision"] == durable
    assert tokens(config)[0] != durable
    assert subject(config, portfolio.DETAIL_BATCH, SECOND)["gate"]["drafts"][0]["state"] == "declined", (
        "which the next reading states, with the token that goes with it"
    )


def test_a_replay_restated_after_the_bracket_closed_is_not_in_the_document(
    config: evolution.EvolutionConfig, series: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And the third record only a detail opens: a terminal attempt's replay
    history, which nothing else in this package reads at all. Being read by one
    caller is exactly why it would be the last place a stale reading showed up,
    so it is held to the same boundary as the other two."""

    path = config.experiments_root / f"{FIRST}-exp-01" / "replays.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    before = record["replays"][0]["result"]["detail"]
    durable = tokens(config)[0]

    def restate(ordinal: int) -> None:
        if ordinal != 2:
            return
        record["replays"][0]["result"]["detail"] = "re-read against the excluded case, and it regressed"
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    ordinals = bracketed(monkeypatch, restate)
    document = expanded(config, portfolio.DETAIL_BATCH, FIRST)

    assert ordinals == [1, 2]
    monkeypatch.undo()
    (run,) = document["detail"]["batch"]["experiments"][0]["replays"]["runs"]
    assert run["result"]["detail"] == before
    assert document["snapshot"]["state_revision"] == durable
    assert tokens(config)[0] != durable


def test_a_draft_written_under_the_projection_is_built_again(
    config: evolution.EvolutionConfig, series: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other side of that boundary, and the one the retry is for: a write
    landing while the gate is being projected is caught by the comparison after
    it, and what a console receives is the document built at the reading that
    won — never the one assembled over the state that lost, republished with the
    newer token."""

    path = config.batches_root / SECOND / "proposed-tasks" / f"{DRAFT}.md"
    edited = path.read_text(encoding="utf-8") + "\nwritten under the projection\n"

    ordinals = bracketed(
        monkeypatch, lambda ordinal: path.write_text(edited, encoding="utf-8") if ordinal == 1 else None
    )
    document = expanded(config, portfolio.DETAIL_BATCH, SECOND)

    assert ordinals == [1, 2, 3], "the first pair straddled the write, so both were taken again"
    monkeypatch.undo()
    waiting = next(
        draft for draft in document["detail"]["batch"]["gate"]["drafts"] if draft["draft_id"] == DRAFT
    )
    assert waiting["content"] == edited
    assert document["snapshot"]["state_revision"] == tokens(config)[0], "the pair that goes with them"
