"""Which line a target is standing on, read from receipts rather than trees.

Two questions live here, and the suite is about keeping them apart. *What a
target holds* is a walk through files nobody here owns — a machine-local
inventory, then a repository's own deploy receipt — and every step of it can be
the end of the road for a different reason an operator would act on differently.
*Which lines it may hold* is a fact about this repository: the commit the
configured source line names right now, and the sealed candidate of an open
experiment, if there is one.

The property under all of it is that neither answer moves with the maintainer's
working tree. A base resolved from a ref is the same commit whichever branch this
checkout is on and whatever is uncommitted in it, and a receipt is a file in
another repository that nothing here rewrites. That is the whole point of asking
identity rather than comparing bytes: the comparison the deploy layer used to
make named the checkout, and a checkout is not something any target can be
deployed from twice.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from evolution_fixtures import (
    REPO_ROOT,
    RELEASE_REF,
    admitted_task,
    experiment_decision,
    experiment_round,
    git_commit,
    git_repo,
    git_rev,
    git_update_ref,
    make_manifest_report,
    make_repo,
    write_experiment,
    write_manifest,
)

from mandrel import lockfile, placement
from mandrel.evolution import config as config_module
from mandrel.evolution import deployment

BATCH = "evolution-batch-0001"
EXPERIMENT = f"{BATCH}-exp-01"


def receipt(root: Path, revision: str | None, *, body: object | None = None) -> Path:
    """A target directory carrying a deploy receipt.

    Written through the lock module's own writer wherever it is a receipt at all,
    so what these tests read back is the document the deploy contract produces
    rather than a shape only the suite believes in. `body` is for the documents
    that are deliberately not receipts.
    """

    root.mkdir(parents=True, exist_ok=True)
    if body is not None:
        (root / ".ai-deploy-lock.json").write_text(json.dumps(body), encoding="utf-8")
        return root
    lockfile.write_lock(root, {"schema_version": 1, "source_git_commit": revision})
    return root


def registry(path: Path, entries: dict[str, list[Path]]) -> Path:
    """A machine-local inventory naming each repository, a name at a time."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [
                {"name": name, "path": str(root), "manifest": str(root / ".ai-deploy-manifest.json")}
                for name, roots in entries.items()
                for root in roots
            ]
        ),
        encoding="utf-8",
    )
    return path


def read(names: list[str], *, root: Path, registry_file: Path) -> dict[str, placement.TargetRevision]:
    readings = placement.registered_revisions(names, root=root, registry_file=registry_file)
    return dict(zip(names, readings))


# --- what a target holds -----------------------------------------------------


def test_the_revision_a_target_holds_is_the_one_its_own_receipt_states(tmp_path: Path) -> None:
    """The whole chain, and the reason it is one chain: a name resolves to a
    repository this machine manages, that repository's receipt states the commit
    its payload was proved to match, and the reading is that string — never this
    checkout's opinion of it."""

    commit = "a" * 40
    target = receipt(tmp_path / "targets" / "orch-hub", commit)
    registry_file = registry(tmp_path / "registry.json", {"orch-hub": [target]})

    named = read(["orch-hub"], root=tmp_path, registry_file=registry_file)["orch-hub"]
    assert (named.revision, named.state, named.path) == (commit, None, str(target))
    assert named.stated

    # The same answer for a caller that already has the directory, which is what
    # `status` is: an operator naming a path resolves no inventory.
    direct = placement.stated_revision(target)
    assert (direct.revision, direct.state, direct.path) == (commit, None, str(target))


def test_the_reasons_a_target_states_no_revision_are_not_one_state(tmp_path: Path) -> None:
    """Five dead ends, five next steps. A name this machine manages no repository
    for is not work owed here; a directory nothing was deployed to is a deploy; a
    receipt that will not read is a file to look at; a receipt tied to no commit
    is a canonical tree to commit and redeploy from; and a name two repositories
    share is an inventory to correct. Reporting any of them as "on another line"
    would name a redeploy that is not owed."""

    fresh = tmp_path / "targets" / "fresh"
    fresh.mkdir(parents=True)
    twice_over = receipt(tmp_path / "elsewhere" / "twice-over", "c" * 40)
    registry_file = registry(
        tmp_path / "registry.json",
        {
            "fresh": [fresh],
            "untied": [receipt(tmp_path / "targets" / "untied", None)],
            "garbled": [receipt(tmp_path / "targets" / "garbled", None, body="not a receipt")],
            "twice-over": [receipt(tmp_path / "targets" / "twice-over", "b" * 40), twice_over],
        },
    )

    states = read(
        ["elsewhere", "fresh", "untied", "garbled", "twice-over"],
        root=tmp_path,
        registry_file=registry_file,
    )
    assert {name: reading.state for name, reading in states.items()} == {
        "elsewhere": placement.UNPLACED_UNREGISTERED,
        "fresh": placement.UNPLACED_NO_RECEIPT,
        "untied": placement.UNPLACED_UNSTATED,
        "garbled": placement.UNPLACED_UNREADABLE,
        "twice-over": placement.UNPLACED_AMBIGUOUS,
    }
    assert not any(reading.stated for reading in states.values())
    assert all(reading.revision is None for reading in states.values())
    # A name that resolved to no single repository carries no path either: that
    # is the state itself rather than a field this reading forgot.
    assert states["elsewhere"].path is None and states["twice-over"].path is None
    assert str(twice_over) in (states["twice-over"].detail or "")
    assert states["fresh"].path == str(fresh)


def test_a_receipt_naming_anything_but_a_commit_is_unreadable_rather_than_placed(tmp_path: Path) -> None:
    """A revision is placed by resolving it in a repository of this reading's
    own, so a receipt naming `HEAD`, a branch, or an abbreviation would be
    answered by wherever the reader is standing and would move whenever that did.
    An unsupported schema goes the same way: a field this build knows by name may
    mean something else in the document that wrote it."""

    for index, body in enumerate(
        (
            {"schema_version": 1, "source_git_commit": "HEAD"},
            {"schema_version": 1, "source_git_commit": "main"},
            {"schema_version": 1, "source_git_commit": "a" * 7},
            # A receipt that states the field nowhere is not the receipt that
            # states none: every deploy writes it, so its absence is a document
            # that answers nothing at all.
            {"schema_version": 1},
            {"schema_version": 2, "source_git_commit": "a" * 40},
        )
    ):
        target = receipt(tmp_path / f"target-{index}", None, body=body)
        reading = placement.stated_revision(target)
        assert reading.state == placement.UNPLACED_UNREADABLE, body
        assert reading.revision is None


def test_an_inventory_that_will_not_read_is_every_name_at_once(tmp_path: Path) -> None:
    """The registry is what says which repository a name is, so a registry that
    does not read leaves this machine unable to answer for any of them. Named
    against every target rather than raised: a machine-local inventory holds no
    part of anybody's lifecycle, and refusing the whole reading over it would
    take a surface away from an operator over a file none of it is about."""

    registry_file = tmp_path / "registry.json"
    registry_file.write_text("{ not a registry", encoding="utf-8")

    states = read(["orch-hub", "mandrel"], root=tmp_path, registry_file=registry_file)
    assert [reading.state for reading in states.values()] == [placement.UNPLACED_UNREADABLE] * 2
    assert all(str(registry_file) in (reading.detail or "") for reading in states.values())
    assert all(reading.path is None for reading in states.values())


def test_the_reading_keeps_the_order_it_was_asked_in(tmp_path: Path) -> None:
    """A caller's list of targets is a record's list — a promotion's planned
    names, an operator's arguments — and a reading that sorted it would stop
    being the list beside it."""

    names = ["zulu", "alpha", "mike"]
    registry_file = registry(
        tmp_path / "registry.json", {name: [receipt(tmp_path / name, "a" * 40)] for name in names}
    )

    readings = placement.registered_revisions(names, root=tmp_path, registry_file=registry_file)
    assert [reading.path for reading in readings] == [str(tmp_path / name) for name in names]


# --- which lines a target may be standing on ---------------------------------


def test_the_base_is_the_configured_line_and_not_the_checkout(tmp_path: Path) -> None:
    """The property the whole change exists for. `status` used to judge a target
    against the bytes in the maintainer's working tree, which no target can match
    while anything is uncommitted and which changes under an ordinary
    `git checkout`. A ref resolves to the same commit either way."""

    root = git_repo(tmp_path / "source", tag=None)
    released = git_rev(root, "HEAD")

    # The checkout moves on, twice over: a commit on the working branch, then an
    # uncommitted edit on top of it. Neither is the line this project releases.
    moved = git_commit(root, "work in progress")
    (root / "file.txt").write_text("uncommitted\n", encoding="utf-8")
    assert moved != released

    lines = placement.source_lines(root, ref=RELEASE_REF)
    assert lines.base == released and lines.unresolved == ()
    assert lines.place(released) == placement.LINE_BASE and lines.accepts(released)
    assert lines.place(moved) is None and not lines.accepts(moved)


def test_a_target_at_an_ancestor_of_the_base_is_not_at_the_base(tmp_path: Path) -> None:
    """Identity, never ancestry. A target deployed before the line moved is
    running the protocol that commit carried, which is the entire fact its
    receipt exists to state; reading "contained in" as "current" would clear
    every target that ever ran any ancestor of today's release."""

    root = git_repo(tmp_path / "source", tag=None)
    older = git_rev(root, "HEAD")
    git_update_ref(root, RELEASE_REF, git_commit(root, "canonical work"))

    lines = placement.source_lines(root, ref=RELEASE_REF)
    assert lines.base != older
    assert lines.place(older) is None


def test_a_sealed_candidate_is_a_line_a_target_may_stand_on(tmp_path: Path) -> None:
    """Two accepted identities and no more: a target on the release, and a target
    running the change being measured. Anything else is a placement stated rather
    than a comparison made."""

    root = git_repo(tmp_path / "source", tag=None)
    candidate = git_commit(root, "candidate work")

    lines = placement.source_lines(root, ref=RELEASE_REF, candidate=candidate)
    assert lines.base is not None and lines.base != candidate
    assert lines.place(lines.base) == placement.LINE_BASE
    assert lines.place(candidate) == placement.LINE_CANDIDATE
    assert lines.place("f" * 40) is None


def test_a_line_this_checkout_cannot_resolve_is_stated_rather_than_guessed(tmp_path: Path) -> None:
    """A ref this repository does not hold is a fact about the reading checkout,
    not about any target. So nothing is named, the reason is carried, and
    `known` is what tells a caller it has no line to judge against — a caller
    reporting this as a target's drift would blame the target for it."""

    root = git_repo(tmp_path / "source", tag=None)

    lines = placement.source_lines(root, ref="refs/heads/nowhere")
    assert lines.base is None and not lines.known
    assert lines.place(git_rev(root, "HEAD")) is None
    assert len(lines.unresolved) == 1 and "refs/heads/nowhere" in lines.unresolved[0]


def test_a_source_root_inside_another_repository_borrows_no_line(tmp_path: Path) -> None:
    """`git -C` answers from the enclosing repository for any path inside one, so
    a canonical source that is not itself the top of a work tree would be judged
    against a release line it has nothing to do with — and every target deployed
    from it with it."""

    root = git_repo(tmp_path / "source", tag=None)
    inside = root / "vendored"
    inside.mkdir()

    assert placement.source_lines(inside, ref=RELEASE_REF).base is None
    assert placement.source_lines(root, ref=RELEASE_REF).base is not None


def test_the_default_line_is_the_line_this_repository_releases_on() -> None:
    """The deploy layer names a line for a checkout with no policy to consult,
    and that fallback has one job: to be the same line this repository's policy
    configures. A default left behind after the policy moved would judge every
    target against a line nobody releases on — silently, since both are valid
    refs and only one of them is anybody's release."""

    assert placement.DEFAULT_SOURCE_LINE == config_module.load_config(REPO_ROOT).source_line.ref


# --- what this repository states the lines are -------------------------------


@pytest.fixture
def config(tmp_path: Path) -> config_module.EvolutionConfig:
    """A repository carrying the shipped policy, checked out on a branch that is
    deliberately not the line it releases from."""

    return config_module.load_config(git_repo(make_repo(tmp_path), tag=None))


def frozen(config: config_module.EvolutionConfig) -> None:
    """One current cohort: frozen, and with no outcome recorded."""

    write_manifest(
        config.batches_root,
        BATCH,
        [],
        analysis_task_id=f"2026-08-10-{BATCH}",
        reports=[
            make_manifest_report(key=f"a{index}", sequence=index, task_id=f"2026-08-0{index}-task")
            for index in (1, 2, 3)
        ],
    )


def test_a_checkout_with_no_evolution_policy_still_answers_against_its_base(tmp_path: Path) -> None:
    """`status` is a deploy-layer verb and the evolution workspace gates none of
    it, so a checkout that runs no experiments is not a checkout that cannot say
    where its targets stand. It has one line, which is the answer."""

    root = git_repo(tmp_path / "source", tag=None)
    assert not (root / "evolution" / "config.toml").exists()

    lines = deployment.source_lines(root)
    assert lines.base == git_rev(root, RELEASE_REF) and lines.candidate is None
    assert lines.unresolved == ()


def test_a_policy_that_will_not_read_names_no_line_at_all(config: config_module.EvolutionConfig) -> None:
    """The one degradation that must not guess. A file that is present and
    unreadable may name any line, so falling back to the default would judge
    every target against a line the operator did not configure — quietly, and in
    exactly the situation where nobody can check."""

    (config.repo_root / "evolution" / "config.toml").write_text("not = [toml", encoding="utf-8")

    lines = deployment.source_lines(config.repo_root)
    assert (lines.ref, lines.base, lines.candidate) == (None, None, None)
    assert not lines.known and len(lines.unresolved) == 1


def test_with_no_experiment_open_the_base_is_the_whole_answer(config: config_module.EvolutionConfig) -> None:
    frozen(config)

    lines = deployment.source_lines(config.repo_root)
    assert lines.base == git_rev(config.repo_root, RELEASE_REF)
    assert lines.candidate is None and lines.unresolved == ()


def test_the_candidate_is_the_latest_sealed_round_and_never_an_open_one(
    config: config_module.EvolutionConfig,
) -> None:
    """Invariant 16, read from the deployment side. A sealed round pins the exact
    revision anything may be measured at; an open round's tip moves, so a target
    deployed from it produces reports naming a tree nothing can afterwards
    identify. Accepting the tip here would bless precisely the deployment the
    measurement layer then throws away."""

    frozen(config)
    base = git_rev(config.repo_root, RELEASE_REF)
    sealed = git_commit(config.repo_root, "candidate work")
    write_experiment(
        config.experiments_root,
        EXPERIMENT,
        base_revision=base,
        rounds=[experiment_round(1, candidate_revision=sealed)],
    )
    assert deployment.source_lines(config.repo_root).candidate == sealed

    # A second round, opened and still taking work: the experiment is the same
    # experiment and there is nothing sealed to deploy from.
    write_experiment(
        config.experiments_root,
        EXPERIMENT,
        base_revision=base,
        rounds=[
            experiment_round(1, candidate_revision=sealed),
            experiment_round(2, tasks=[admitted_task("router-note")], reason="review findings"),
        ],
    )
    lines = deployment.source_lines(config.repo_root)
    assert lines.candidate is None and lines.base == base and lines.unresolved == ()


def test_an_experiment_that_ended_leaves_no_candidate(config: config_module.EvolutionConfig) -> None:
    """A terminal decision is history. Its sealed revision stays the tree its own
    evidence describes, and going on to accept it would keep clearing targets
    deployed from an attempt this batch abandoned."""

    frozen(config)
    base = git_rev(config.repo_root, RELEASE_REF)
    write_experiment(
        config.experiments_root,
        EXPERIMENT,
        base_revision=base,
        rounds=[experiment_round(1, candidate_revision=git_commit(config.repo_root, "candidate work"))],
        decision=experiment_decision("abandoned"),
    )

    assert deployment.source_lines(config.repo_root).candidate is None


def test_a_lineage_that_will_not_read_keeps_the_base_and_drops_the_candidate(
    config: config_module.EvolutionConfig,
) -> None:
    """The conservative direction of the same refusal to guess. A damaged
    experiment record cannot make a target standing on the release anything other
    than current, so the base stands; what it does take away is the second
    accepted identity, which costs a redeploy on a candidate-deployed target and
    never a clearance nothing checked."""

    frozen(config)
    (config.experiments_root / EXPERIMENT).mkdir(parents=True)
    (config.experiments_root / EXPERIMENT / "experiment.json").write_text("{ not json", encoding="utf-8")

    lines = deployment.source_lines(config.repo_root)
    assert lines.base == git_rev(config.repo_root, RELEASE_REF)
    assert lines.candidate is None and len(lines.unresolved) == 1
