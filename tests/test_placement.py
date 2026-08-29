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
import subprocess
from pathlib import Path

import pytest
from evolution_fixtures import (
    GIT_IDENTITY,
    REPO_ROOT,
    RELEASE_REF,
    admitted_task,
    experiment_decision,
    experiment_round,
    git_canonical_commit,
    git_commit,
    git_repo,
    git_rev,
    git_update_ref,
    make_manifest_report,
    make_repo,
    write_experiment,
    write_manifest,
)

from mandrel import deploy, lockfile, placement
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


def canonical_symlink(root: Path, message: str) -> str:
    """A commit whose deployable canonical tree holds something that is not a
    file, and its sha.

    Committed here rather than in the shared fixtures because it is the one shape
    this suite needs and no other does: a deploy reads bytes *through* a symlink
    and copies whatever it resolved to, so a tree naming one says nothing about
    what a payload could have come from — which is a commit whose protocol no
    checkout can name.
    """

    link = root / "canonical" / "protocols" / "dev.md"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.unlink(missing_ok=True)
    link.symlink_to("elsewhere.md")
    subprocess.run(["git", "-C", str(root), "add", "--", "canonical/protocols/dev.md"], check=True)
    subprocess.run(["git", "-C", str(root), *GIT_IDENTITY, "commit", "-q", "-m", message], check=True)
    return git_rev(root, "HEAD")


def git_tree(root: Path, revision: str) -> str:
    """The whole tree a commit holds — what a commit identity actually is, and
    what a protocol identity deliberately is not."""

    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", f"{revision}^{{tree}}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def lines_of(root: Path, **stated: object) -> placement.SourceLines:
    """The lines a target deployed from `root` may be standing on.

    Through the payload mapping's own predicate, never a second one this suite
    believes in: which canonical files reach a target is what a protocol identity
    is keyed on, and a fixture with its own idea of that would agree with the
    production reading even where both are wrong about the payload.
    """

    return placement.source_lines(root, deploys=deploy.deploys_canonical_path, **stated)


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
    released = git_canonical_commit(root, "the contract this project releases")
    git_update_ref(root, RELEASE_REF, released)

    # The checkout moves on, twice over: a commit carrying a different contract,
    # then an uncommitted edit on top of it. Neither is the line this project
    # releases.
    moved = git_canonical_commit(root, "work in progress")
    (root / "canonical" / "protocols" / "dev.md").write_text("uncommitted\n", encoding="utf-8")
    assert moved != released

    lines = lines_of(root, ref=RELEASE_REF)
    assert lines.base == released and lines.unresolved == ()
    assert lines.place(released).line == placement.LINE_BASE
    assert lines.place(moved) == placement.Placement(line=None)


def test_a_commit_carrying_the_same_payload_is_the_same_deployment(tmp_path: Path) -> None:
    """The live defect, in the shape it was measured in. A target's receipt named
    a commit the source line had since built on top of, and the two carried
    byte-identical deployable canonical trees: everything between them was work
    under `mandrel/`, `tests/`, `docs/` and `.ai/` that no target ever receives,
    plus a canonical file the mapping carries nowhere. Comparing commit strings
    called that a redeploy owed; comparing what a target actually receives calls
    it what it is.

    Pinned for its blast radius rather than for the one target. Base moves on
    every commit to the source line, so under string equality the first ordinary
    commit after a redeploy wave takes every target on the fleet off the line at
    once — and readiness is what consumes that answer."""

    root = git_repo(tmp_path / "source", tag=None)
    deployed_from = git_canonical_commit(root, "the contract every target holds")

    # Work no payload carries: a file outside `canonical/` altogether, and a
    # canonical file the mapping deploys nowhere. Both committed, since a file
    # left in the working tree would make this pass for the wrong reason.
    local = root / "canonical" / "codex" / "config.toml"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text('model = "local"\n', encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "--", "canonical/codex/config.toml"], check=True)
    base = git_commit(root, "a doc and a local config no target receives")
    git_update_ref(root, RELEASE_REF, base)
    assert git_tree(root, base) != git_tree(root, deployed_from), "the commits differ"

    lines = lines_of(root, ref=RELEASE_REF)
    assert lines.base == base and base != deployed_from
    assert lines.place(deployed_from).line == placement.LINE_BASE


def test_a_target_a_release_behind_is_not_on_the_base(tmp_path: Path) -> None:
    """Identity, and never ancestry. A target deployed before the *protocol*
    moved is running the contract that commit carried, which is the entire fact
    its receipt exists to state; reading "contained in" as "current" would clear
    every target that ever ran any ancestor of today's release."""

    root = git_repo(tmp_path / "source", tag=None)
    older = git_canonical_commit(root, "the contract of the previous release")
    git_update_ref(root, RELEASE_REF, git_canonical_commit(root, "the contract being released"))

    lines = lines_of(root, ref=RELEASE_REF)
    assert lines.base != older
    assert lines.place(older) == placement.Placement(line=None)


def test_a_sealed_candidate_is_a_line_a_target_may_stand_on(tmp_path: Path) -> None:
    """Two accepted protocols and no more: a target on the release, and a target
    running the change being measured. Anything else is a placement stated rather
    than a comparison made."""

    root = git_repo(tmp_path / "source", tag=None)
    git_update_ref(root, RELEASE_REF, git_canonical_commit(root, "the released contract"))
    candidate = git_canonical_commit(root, "the change under measurement")

    lines = lines_of(root, ref=RELEASE_REF, candidate=candidate)
    assert lines.base is not None and lines.base != candidate
    assert lines.place(lines.base).line == placement.LINE_BASE
    assert lines.place(candidate).line == placement.LINE_CANDIDATE


def test_a_revision_this_checkout_cannot_read_is_a_question_left_open(tmp_path: Path) -> None:
    """A receipt naming a commit this clone holds no tree for is not a target
    found somewhere else — nothing was compared at all. It is carried as the
    unanswered question it is, because the next step is a fetch and the other
    answer would send an operator to overwrite a target that may well be standing
    on the line."""

    root = git_repo(tmp_path / "source", tag=None)
    git_update_ref(root, RELEASE_REF, git_canonical_commit(root, "the released contract"))

    standing = lines_of(root, ref=RELEASE_REF).place("f" * 40)

    assert not standing.placed and standing.line is None
    assert standing.unreadable is not None and "f" * 40 in standing.unreadable


def test_a_line_this_checkout_cannot_resolve_is_stated_rather_than_guessed(tmp_path: Path) -> None:
    """A ref this repository does not hold is a fact about the reading checkout,
    not about any target. So nothing is named, the reason is carried, and
    `known` is what tells a caller it has no line to judge against — a caller
    reporting this as a target's drift would blame the target for it."""

    root = git_repo(tmp_path / "source", tag=None)

    lines = lines_of(root, ref="refs/heads/nowhere")
    assert lines.base is None and not lines.known
    # No question asked of Git about the target either: a checkout that can name
    # no line has already said the whole of what it can say, and a lookup here
    # would turn its own silence into a finding about the target.
    assert lines.place(git_rev(root, "HEAD")) == placement.Placement(line=None)
    assert len(lines.unresolved) == 1 and "refs/heads/nowhere" in lines.unresolved[0]


def test_a_line_whose_payload_this_checkout_cannot_read_is_dropped_with_its_reason(
    tmp_path: Path,
) -> None:
    """The other way a line goes unnamed, and the same answer for it. A ref can
    resolve perfectly to a commit whose deployable tree says nothing a payload
    could have come from — here an entry a deploy would read *through* rather
    than copy — and a line whose protocol is unknown is one nothing may be placed
    against. Dropped with the reason stated, exactly as an unresolvable ref is,
    because both are the reading repository's state and neither is any target's:
    the alternative places every target against a protocol nobody read."""

    root = git_repo(tmp_path / "source", tag=None)
    released = git_canonical_commit(root, "the released contract")
    git_update_ref(root, RELEASE_REF, released)
    unreadable = canonical_symlink(root, "a canonical entry that is not a file")

    # As a candidate: the base stands, and the second accepted protocol is the
    # only thing lost.
    lines = lines_of(root, ref=RELEASE_REF, candidate=unreadable)
    assert lines.base == released and lines.candidate is None
    assert len(lines.unresolved) == 1 and unreadable in lines.unresolved[0]

    # As the base itself: this checkout names no line at all, and says why.
    git_update_ref(root, RELEASE_REF, unreadable)
    lines = lines_of(root, ref=RELEASE_REF)
    assert lines.base is None and not lines.known
    assert len(lines.unresolved) == 1 and RELEASE_REF in lines.unresolved[0]


def test_a_source_root_inside_another_repository_borrows_no_line(tmp_path: Path) -> None:
    """`git -C` answers from the enclosing repository for any path inside one, so
    a canonical source that is not itself the top of a work tree would be judged
    against a release line it has nothing to do with — and every target deployed
    from it with it."""

    root = git_repo(tmp_path / "source", tag=None)
    inside = root / "vendored"
    inside.mkdir()

    assert lines_of(inside, ref=RELEASE_REF).base is None
    assert lines_of(root, ref=RELEASE_REF).base is not None


def test_the_default_line_is_stated_here_and_is_a_ref_that_means_one_commit() -> None:
    """Asserted as the literal it is, because it is a value no caller passes and
    no test would otherwise notice being edited — a deploy-layer constant that
    decides how every unconfigured repository's targets are judged. Fully
    qualified for the reason the policy pattern requires: a bare branch or a
    revision expression resolves against whatever the reading checkout sits on."""

    assert placement.DEFAULT_SOURCE_LINE == "refs/heads/main"
    assert placement.DEFAULT_SOURCE_LINE.startswith("refs/heads/")


def test_the_default_line_and_the_configured_one_are_independent(tmp_path: Path) -> None:
    """They answer different questions and are allowed to differ, which is this
    repository's own case: its policy promotes onto a line that is not the
    default, and both readings are correct. A repository that configures a line
    is judged by that line; one that configures none is judged by the constant.
    Tying them would ship one project's release branch to every project without
    one — and would move it whenever that project moved."""

    configured = config_module.load_config(REPO_ROOT).source_line.ref
    assert configured != placement.DEFAULT_SOURCE_LINE

    # Carrying the shipped policy: the line it names, never the default.
    assert deployment.source_lines(git_repo(make_repo(tmp_path), tag=None)).ref == configured
    # Carrying no policy at all: the default, and no reading of the shipped one.
    assert deployment.source_lines(git_repo(tmp_path / "unconfigured", tag=None)).ref == placement.DEFAULT_SOURCE_LINE


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


def test_a_checkout_with_no_evolution_workspace_still_answers_against_its_base(tmp_path: Path) -> None:
    """`status` is a deploy-layer verb and the evolution workspace gates none of
    it, so a checkout that runs no experiments is not a checkout that cannot say
    where its targets stand. It has one line, which is the answer."""

    root = git_repo(tmp_path / "source", tag=None)
    # The workspace itself, because that is what the fallback is decided on: a
    # repository keeping one and unable to produce its policy is the case below.
    assert not (root / "evolution").exists()
    # The default line, created here rather than by the shared fixture: what a
    # repository with no policy holds is its own business, and `git_repo` builds
    # the line the *policy* names for the suites that read one. Widening it to
    # build both would put a second source-line ref in every temporary
    # repository, which those suites would then be asserting against by accident.
    #
    # Deliberately at a different commit from the one the fixture built, so that
    # reading the shipped policy here would be a visibly wrong answer rather than
    # the same answer by coincidence.
    default = git_commit(root, "work the default line carries")
    git_update_ref(root, placement.DEFAULT_SOURCE_LINE, default)
    assert git_rev(root, RELEASE_REF) != default

    lines = deployment.source_lines(root)
    assert lines.ref == placement.DEFAULT_SOURCE_LINE
    assert lines.base == default and lines.candidate is None
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


@pytest.mark.parametrize("damage", ("deleted", "directory", "dangling-link"))
def test_a_workspace_that_keeps_a_policy_it_cannot_produce_names_no_line(
    config: config_module.EvolutionConfig, damage: str
) -> None:
    """The absence the default line answers is the absence of a *workspace*, and
    these are not it. A workspace whose policy file was deleted, replaced by a
    directory, or left as a dangling link says nothing about which line it
    configured — so taking the default would clear every target standing on
    `refs/heads/main` against a line this operator may never have released from.
    That is the same silent guess the unreadable-policy case refuses, arriving by
    the path where the policy fails to be a readable file rather than failing to
    parse as one."""

    policy = config.repo_root / "evolution" / "config.toml"
    policy.unlink()
    if damage == "directory":
        policy.mkdir()
    elif damage == "dangling-link":
        policy.symlink_to("gone.toml")
    assert (config.repo_root / "evolution").is_dir()

    lines = deployment.source_lines(config.repo_root)
    assert (lines.ref, lines.base, lines.candidate) == (None, None, None)
    assert not lines.known and len(lines.unresolved) == 1
    # Named, since a reading that says a line could not be found without saying
    # which file failed leaves an operator nothing to repair.
    assert str(policy) in lines.unresolved[0]


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
