from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import unicodedata
from dataclasses import replace
from pathlib import Path

import pytest

from mandrel import cli, deploy, hashing, lockfile, manifest, paths, placement


def write(path: Path, content: str = "content\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


CLAUDE_MD_WITH_MEMORY_IMPORTS = """# Project protocol (loader)

## Protocol imports

@.ai-protocol/protocols/conduct.md
@.ai-protocol/protocols/dev.md
@.ai-protocol/meta/taskfile.md
@.ai-protocol/meta/memory.md

## Memory (eager set)

@.ai/index.md
@.ai/map.md
@.ai/overview.md
@.ai/architecture.md
@.ai/design.md
@.ai/conventions.md

## Work tracking

@.ai-tasks/index.md
"""


EAGER_TOPICS = ("overview", "architecture", "design", "conventions")

AI_ROUTER_TEMPLATE = """---
last-updated: 2026-01-01
verified-against: 0000000000000000000000000000000000000000
---

# AI Knowledge Router

## Documents

| Document | File | Use when |
|---|---|---|
{rows}
| Map | map.md | cross-reference |
"""


def write_snapshot(
    target: Path,
    *,
    directory_form: tuple[str, ...] = (),
    router: dict[str, str] | None = None,
) -> None:
    """Write a `.ai/` snapshot: single-file entries by default, directory-form
    entries for the named topics, with routing that matches. `router` overrides
    individual routing rows to model a snapshot whose routing disagrees with
    file shape."""
    routes: dict[str, str] = {}
    for topic in EAGER_TOPICS:
        if topic in directory_form:
            write(target / ".ai" / topic / "index.md", f"{topic} index\n")
            routes[topic] = f"{topic}/index.md"
        else:
            write(target / ".ai" / f"{topic}.md", f"{topic}\n")
            routes[topic] = f"{topic}.md"
    routes.update(router or {})

    rows = "\n".join(f"| {topic.capitalize()} | {routes[topic]} | use when |" for topic in EAGER_TOPICS)
    write(target / ".ai" / "index.md", AI_ROUTER_TEMPLATE.format(rows=rows))
    write(target / ".ai" / "map.md", "map\n")


def upgrade_to_directory_form(target: Path, topic: str) -> None:
    """Simulate the memory §4 upgrade: rename `x.md` to `x/index.md` and
    re-point top-level routing. The loader is deliberately left untouched."""
    flat = target / ".ai" / f"{topic}.md"
    write(target / ".ai" / topic / "index.md", flat.read_text(encoding="utf-8"))
    flat.unlink()
    router_path = target / ".ai" / "index.md"
    router_path.write_text(
        router_path.read_text(encoding="utf-8").replace(f"| {topic}.md |", f"| {topic}/index.md |"),
        encoding="utf-8",
    )


def loader_import(target: Path, topic: str) -> str:
    """The deployed loader's eager import path for a topic. Asserts exactly one
    import line exists for it — the loader never carries both forms."""
    forms = {f"@.ai/{topic}.md", f"@.ai/{topic}/index.md"}
    found = [
        line.strip()[1:]
        for line in (target / "CLAUDE.md").read_text(encoding="utf-8").splitlines()
        if line.strip() in forms
    ]
    assert len(found) == 1, f"expected exactly one {topic} import in the loader, got {found}"
    return found[0]


def make_source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    canonical = root / "canonical"
    write(canonical / "repo-root" / "CLAUDE.md", "claude instructions\n")
    write(canonical / "protocols" / "dev.md", "dev contract\n")
    write(canonical / "workflow" / "runbook.md", "runbook\n")
    write(canonical / "meta" / "taskfile.md", "taskfile schema\n")
    write(canonical / "cursor" / "hooks" / "session-start.sh", "#!/bin/sh\n")
    write(canonical / "cursor" / "hooks.json", "{}\n")
    write(canonical / "codex" / "README.md", "codex\n")
    write(canonical / "codex" / "hooks" / "session-start.sh", "#!/bin/sh\n")
    write(
        canonical / "codex" / "config.toml.template",
        'command = "{{REPO_ROOT}}/.codex/hooks/session-start.sh"\n',
    )
    write(canonical / "claude" / "settings.json", "{}\n")
    # Workflow skills are ordinary payload in a nested directory; they exercise
    # the rglob recursion that carries them into the manifest and the lock.
    write(canonical / "claude" / "skills" / "demo-skill" / "SKILL.md", "---\nname: demo-skill\n---\n")
    write(canonical / "claude" / "skills" / "demo-skill" / "scan.sh", "#!/bin/sh\n").chmod(0o755)
    write(canonical / "orchestrator" / "orchestrator.py", 'print("ok")\n')
    write(canonical / "orchestrator" / "orchestrator.toml",
          'schema_version = 1\n')
    write(canonical / "orchestrator" / ".env.example", "TOKEN=\n")
    write(canonical / "orchestrator" / "requirements.txt", "cursor-sdk\n")
    return root


def make_loader_source(tmp_path: Path) -> Path:
    """A committed source whose loader carries the eager memory imports."""

    source = make_source(tmp_path)
    write(source / "canonical" / "repo-root" / "CLAUDE.md", CLAUDE_MD_WITH_MEMORY_IMPORTS)
    commit_source(source)
    return source


def deploy_to_tmp(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    """A deployed target whose receipt places it on this source's release line.

    Committed before the deploy, because that is the only state in which a lock
    states a source commit at all — and a receipt stating none is a target
    `status` cannot place, whatever else is true of its files.
    """

    source = make_source(tmp_path)
    commit_source(source)
    target = tmp_path / "target"
    target.mkdir()
    deployed = deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")
    return source, target, deployed


# The line every temporary source releases on. Spelled here rather than taken
# from `placement.DEFAULT_SOURCE_LINE`: what these tests assert is that `status`
# judges against the line it was handed, and a suite that borrowed the fallback
# would still pass if the parameter were ignored.
SOURCE_BRANCH = "main"
SOURCE_LINE = f"refs/heads/{SOURCE_BRANCH}"


GIT_IDENTITY = ["-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false"]


def git(source: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(source), *GIT_IDENTITY, *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def commit_source(source: Path) -> str:
    """The source checkout as a repository whose canonical payload is committed,
    and the commit it is at — the only state in which a deploy may state one.

    The branch is named rather than left to Git: `init.defaultBranch` is a
    machine setting, and the line a target is judged against has to be the same
    one on every host that runs this suite."""

    git(source, "init", "-q", "-b", SOURCE_BRANCH)
    git(source, "add", "-A")
    git(source, "commit", "-q", "-m", "canonical payload")
    return git(source, "rev-parse", "HEAD")


def deploy_from(source: Path, tmp_path: Path) -> Path:
    target = tmp_path / "target"
    target.mkdir(exist_ok=True)
    deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")
    return target


def drift_kinds(result: deploy.StatusResult) -> set[str]:
    return {drift.kind for drift in result.drifts}


def lines_of(source: Path, *, candidate: str | None = None) -> placement.SourceLines:
    """The identities a target deployed from this source may be standing on."""

    return placement.source_lines(source, ref=SOURCE_LINE, candidate=candidate)


def status_of(target: Path, source: Path, **kwargs: object) -> deploy.StatusResult:
    """Status as the CLI asks it: against the lines this source releases on,
    resolved once and handed in, never re-derived from the working tree."""

    return deploy.check_status(target, lines=lines_of(source), **kwargs)


def test_file_hashing(tmp_path: Path) -> None:
    path = write(tmp_path / "sample.txt", "hello\n")

    assert hashing.sha256_file(path) == hashlib.sha256(b"hello\n").hexdigest()


def test_manifest_creation(tmp_path: Path) -> None:
    source, target, deployed = deploy_to_tmp(tmp_path)
    saved = manifest.read_manifest(target)

    assert saved["schema_version"] == 1
    assert saved["target_repo_path"] == str(target.resolve())
    assert ".codex/config.toml" in saved["files"]
    assert ".ai-protocol/protocols/dev.md" in saved["files"]
    assert ".ai-protocol/workflow/runbook.md" in saved["files"]
    assert ".ai-protocol/meta/taskfile.md" in saved["files"]
    assert ".mandrel/orchestrator/orchestrator.toml" in saved["files"]

    rendered_config = (target / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert "{{REPO_ROOT}}" not in rendered_config
    assert str(target.resolve()) in rendered_config

    config_record = saved["files"][".codex/config.toml"]
    assert config_record["sha256"] == hashing.sha256_file(target / ".codex" / "config.toml")
    assert deployed["files"] == saved["files"]

    lock = lockfile.read_lock(target)
    lock_text = (target / ".ai-deploy-lock.json").read_text(encoding="utf-8")
    assert lock["source_repo"] == "mandrel"
    assert "target_repo_path" not in lock
    assert str(target.resolve()) not in lock_text
    assert ".codex/config.toml" in {item["target_relative_path"] for item in lock["deployed_files"]}
    config_lock_record = next(
        item for item in lock["deployed_files"] if item["target_relative_path"] == ".codex/config.toml"
    )
    assert config_lock_record["canonical_relative_path"] == "canonical/codex/config.toml.template"
    assert config_lock_record["canonical_sha256"] == hashing.sha256_file(
        source / "canonical" / "codex" / "config.toml.template"
    )


def test_deploy_places_skills_in_target_manifest_and_lock(tmp_path: Path) -> None:
    """Workflow skills deploy per repository like any other canonical file, so
    the manifest and the lock cover them — the receipt hole the machine-global
    sync command used to leave."""
    source, target, deployed = deploy_to_tmp(tmp_path)
    skill = ".claude/skills/demo-skill/SKILL.md"
    script = ".claude/skills/demo-skill/scan.sh"

    assert (target / skill).is_file()
    assert stat.S_IMODE((target / script).stat().st_mode) == 0o755

    saved = manifest.read_manifest(target)
    assert skill in saved["files"]
    assert saved["files"][skill]["canonical_relative_path"] == "canonical/claude/skills/demo-skill/SKILL.md"
    assert saved["files"][skill]["sha256"] == hashing.sha256_file(target / skill)

    lock = lockfile.read_lock(target)
    lock_record = next(item for item in lock["deployed_files"] if item["target_relative_path"] == skill)
    assert lock_record["canonical_sha256"] == hashing.sha256_file(
        source / "canonical" / "claude" / "skills" / "demo-skill" / "SKILL.md"
    )
    assert {skill, script} <= {item["target_relative_path"] for item in lock["deployed_files"]}


def test_a_receipt_states_a_source_commit_only_as_the_object_id_a_deploy_wrote(tmp_path: Path) -> None:
    """What a reader may believe a receipt about, asked here because the receipt
    is this contract's document and read in repositories it does not own.

    The shape is load-bearing rather than tidy: a reader places a target by
    resolving this field in a repository of its own (`evolution/deployment.py`),
    and `git rev-parse` answers for `HEAD`, a branch or an abbreviation as
    readily as for a commit id — so a receipt naming one of those would be placed
    against the reading checkout's own position and would move whenever that did.
    An unsupported schema is refused for the neighbouring reason: a field this
    build knows by name may mean something else in the document that wrote it.

    The field's two absences are the third thing asked, and they are not one
    answer: every receipt this contract writes states `source_git_commit`, so a
    document without it never came from a deploy this build reads, while its
    explicit null is that deploy saying the payload it wrote matches no commit.
    Reading the first as the second puts a truncated file's silence into the
    contract's mouth — a reader would report a target as merely unplaceable when
    the receipt cannot answer at all.
    """

    source = make_source(tmp_path)
    head = commit_source(source)
    target = deploy_from(source, tmp_path)

    # What a deploy writes reads back as itself, and a payload tied to no commit
    # reads back as the ordinary absence it is rather than as a complaint.
    assert lockfile.stated_source_commit(lockfile.read_lock(target)) == head
    assert lockfile.stated_source_commit({"schema_version": 1, "source_git_commit": None}) is None

    for refused in (
        [],
        "a receipt",
        {"source_git_commit": head},
        # The truncated receipt, beside the stated null above: `get` answers None
        # for both, and only one of them is an answer.
        {"schema_version": 1},
        {"schema_version": 2, "source_git_commit": head},
        {"schema_version": True, "source_git_commit": head},
        # Refused as a version rather than raised on as an unhashable one: a
        # `TypeError` out of here is a refusal no reader is catching.
        {"schema_version": [1], "source_git_commit": head},
        {"schema_version": "1", "source_git_commit": head},
        {"schema_version": 1, "source_git_commit": "HEAD"},
        {"schema_version": 1, "source_git_commit": "main"},
        {"schema_version": 1, "source_git_commit": head[:12]},
        {"schema_version": 1, "source_git_commit": head.upper()},
        {"schema_version": 1, "source_git_commit": f"{head}\n"},
        {"schema_version": 1, "source_git_commit": ""},
        {"schema_version": 1, "source_git_commit": 12},
    ):
        with pytest.raises(lockfile.LockError):
            lockfile.stated_source_commit(refused)


def test_the_lock_states_the_canonical_commit_a_clean_payload_came_from(tmp_path: Path) -> None:
    """The one state in which the receipt's revision is an account of the payload
    rather than of the moment: every canonical file the deploy read is exactly
    what that commit holds."""

    source = make_source(tmp_path)
    head = commit_source(source)

    target = deploy_from(source, tmp_path)

    assert lockfile.read_lock(target)["source_git_commit"] == head


@pytest.mark.parametrize("dirt", ("modified", "deleted", "added"))
def test_a_payload_carrying_uncommitted_canonical_work_states_no_revision(tmp_path: Path, dirt: str) -> None:
    """A deploy copies the working tree, so `HEAD` says when it ran and not what
    it carried. Downstream the revision is read as an account of those bytes — a
    release assessment places every report a target produced by it, as an
    ancestry test — so a payload no commit holds has no commit to name, and
    stating one anyway would place those reports by content the target never ran.
    """

    source = make_source(tmp_path)
    commit_source(source)
    committed = lockfile.read_lock(deploy_from(source, tmp_path))
    assert committed["source_git_commit"] is not None

    contract = source / "canonical" / "protocols" / "dev.md"
    if dirt == "modified":
        write(contract, "dev contract, edited and never committed\n")
    elif dirt == "deleted":
        contract.unlink()
    else:
        write(source / "canonical" / "protocols" / "review.md", "review contract, never committed\n")

    lock = lockfile.read_lock(deploy_from(source, tmp_path))

    assert lock["canonical_payload_sha256"] != committed["canonical_payload_sha256"], (
        "the deploy carried a payload the commit does not hold"
    )
    assert lock["source_git_commit"] is None


def test_a_deployed_file_git_ignores_states_no_revision(tmp_path: Path) -> None:
    """The case a dirty-tree check alone cannot see. An ignored canonical file is
    absent from every `status` report and still lands in the payload, so a
    revision stated on that report alone would describe a target that ran a file
    no commit holds."""

    source = make_source(tmp_path)
    write(source / ".gitignore", "canonical/protocols/local-note.md\n")
    commit_source(source)
    write(source / "canonical" / "protocols" / "local-note.md", "a note nobody committed\n")
    assert git(source, "status", "--porcelain", "--", "canonical") == "", "the tree reads clean"

    target = deploy_from(source, tmp_path)

    assert (target / ".ai-protocol" / "protocols" / "local-note.md").is_file()
    assert lockfile.read_lock(target)["source_git_commit"] is None


@pytest.mark.parametrize("suppression", ("assume-unchanged", "skip-worktree"))
def test_a_canonical_file_git_was_told_not_to_report_states_no_revision(tmp_path: Path, suppression: str) -> None:
    """A clean tree is not proof that the payload is the commit's. Either index
    flag makes Git stop looking at the working tree for that file, so `status`
    and `diff` stay silent about bytes the deploy still copies — and a revision
    resting on their silence would name content this payload does not carry."""

    source = make_source(tmp_path)
    commit_source(source)
    committed = lockfile.read_lock(deploy_from(source, tmp_path))
    assert committed["source_git_commit"] is not None

    git(source, "update-index", f"--{suppression}", "canonical/protocols/dev.md")
    write(source / "canonical" / "protocols" / "dev.md", "dev contract, changed behind a silenced index\n")
    assert git(source, "status", "--porcelain", "--", "canonical") == "", "the tree reads clean"
    assert git(source, "diff", "--", "canonical") == "", "and so does the diff"

    lock = lockfile.read_lock(deploy_from(source, tmp_path))

    assert lock["canonical_payload_sha256"] != committed["canonical_payload_sha256"], (
        "the deploy carried a payload the commit does not hold"
    )
    assert lock["source_git_commit"] is None


def test_a_mode_change_git_was_configured_not_to_see_states_no_revision(tmp_path: Path) -> None:
    """The other silence. Deployment copies the source file's mode, so an
    executable bit the commit does not hold is a payload difference like any
    other, and `core.fileMode=false` keeps every Git report quiet about it."""

    source = make_source(tmp_path)
    commit_source(source)
    assert lockfile.read_lock(deploy_from(source, tmp_path))["source_git_commit"] is not None

    git(source, "config", "core.fileMode", "false")
    (source / "canonical" / "workflow" / "runbook.md").chmod(0o755)
    assert git(source, "status", "--porcelain", "--", "canonical") == "", "the tree reads clean"

    target = deploy_from(source, tmp_path)

    deployed_mode = stat.S_IMODE((target / ".ai-protocol" / "workflow" / "runbook.md").stat().st_mode)
    assert deployed_mode & 0o111, "the deploy carried a mode the commit does not hold"
    assert lockfile.read_lock(target)["source_git_commit"] is None


def test_a_canonical_symlink_states_no_revision(tmp_path: Path) -> None:
    """The commit holds a link; `iter_deployment_items` reads bytes through it
    and deploys those. Nothing in the canonical tree is a symlink today, and if
    one arrives the payload it produces is not the commit's to claim — the
    content came from wherever the link pointed at deploy time."""

    source = make_source(tmp_path)
    (source / "canonical" / "protocols" / "review.md").symlink_to("dev.md")
    commit_source(source)
    assert git(source, "ls-files", "-s", "--", "canonical/protocols/review.md").startswith("120000")

    target = deploy_from(source, tmp_path)

    assert (target / ".ai-protocol" / "protocols" / "review.md").read_text(encoding="utf-8") == "dev contract\n"
    assert lockfile.read_lock(target)["source_git_commit"] is None


def when_the_target_file_is_written(monkeypatch: pytest.MonkeyPatch, target_relative_path: str, then) -> None:
    """Run `then` during the deploy, once the named target file has been written
    and before the receipt is built.

    The window a receipt that re-reads the source answers from: by then the
    target already carries its bytes and its mode, so anything the source says
    afterwards is a second observation of a file the payload no longer depends
    on. Hooking the manifest's hash of that target file places the mutation
    there deterministically, where a real race would not be reproducible."""

    real_file_record = hashing.file_record

    def record_then_mutate(path: Path) -> dict[str, int | str]:
        record = real_file_record(path)
        if path.as_posix().endswith(target_relative_path):
            then()
        return record

    monkeypatch.setattr(hashing, "file_record", record_then_mutate)


def when_the_lock_is_built(monkeypatch: pytest.MonkeyPatch, then) -> None:
    """Run `then` during the deploy, after every file has been deployed and
    before the receipt is built."""

    real_build_lock = lockfile.build_lock

    def mutate_then_build(**arguments: object) -> dict[str, object]:
        then()
        return real_build_lock(**arguments)

    monkeypatch.setattr(lockfile, "build_lock", mutate_then_build)


def lock_record_for(target: Path, canonical_relative_path: str) -> dict[str, object]:
    return next(
        record
        for record in lockfile.read_lock(target)["deployed_files"]
        if record["canonical_relative_path"] == canonical_relative_path
    )


def test_a_source_edit_undone_after_the_deploy_copied_it_states_no_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The receipt is an account of what was deployed, so it has to be built from
    what was deployed. A source file edited before the deploy and restored after
    the target was written leaves the target running bytes no commit holds, while
    every later look at the source agrees with the commit."""

    source = make_source(tmp_path)
    commit_source(source)
    contract = source / "canonical" / "protocols" / "dev.md"
    committed_bytes = contract.read_bytes()
    edited_bytes = write(contract, "dev contract, edited and never committed\n").read_bytes()
    when_the_target_file_is_written(
        monkeypatch,
        ".ai-protocol/protocols/dev.md",
        lambda: contract.write_bytes(committed_bytes),
    )

    target = deploy_from(source, tmp_path)

    assert (target / ".ai-protocol" / "protocols" / "dev.md").read_bytes() == edited_bytes, (
        "the deploy carried bytes the commit does not hold"
    )
    assert lock_record_for(target, "canonical/protocols/dev.md")["canonical_sha256"] == hashing.sha256_bytes(
        edited_bytes
    ), "the receipt describes the bytes that were deployed"
    assert lockfile.read_lock(target)["source_git_commit"] is None


def test_a_mode_undone_after_the_deploy_copied_it_states_no_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same window on the other property the deploy copies. The mode reaching
    the target is the one enumeration captured, so restoring the source's mode
    afterwards changes nothing about the payload and everything about a check
    that reads the source again."""

    source = make_source(tmp_path)
    commit_source(source)
    runbook = source / "canonical" / "workflow" / "runbook.md"
    runbook.chmod(0o755)
    when_the_target_file_is_written(
        monkeypatch,
        ".ai-protocol/workflow/runbook.md",
        lambda: runbook.chmod(0o644),
    )

    target = deploy_from(source, tmp_path)

    deployed_mode = stat.S_IMODE((target / ".ai-protocol" / "workflow" / "runbook.md").stat().st_mode)
    assert deployed_mode & 0o111, "the deploy carried a mode the commit does not hold"
    assert lockfile.read_lock(target)["source_git_commit"] is None


def test_a_canonical_file_restored_after_the_deploy_read_the_tree_states_no_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The path direction of the same gap. A committed canonical file missing when
    the deploy enumerated the tree is a file the target never received; restoring
    it before the receipt is built makes a source re-read see a complete tree and
    state a commit for a payload that is short a contract."""

    source = make_source(tmp_path)
    commit_source(source)
    contract = source / "canonical" / "protocols" / "dev.md"
    committed_bytes = contract.read_bytes()
    contract.unlink()
    when_the_lock_is_built(monkeypatch, lambda: contract.write_bytes(committed_bytes))

    target = deploy_from(source, tmp_path)

    assert not (target / ".ai-protocol" / "protocols" / "dev.md").exists(), (
        "the deploy carried a payload the commit does not hold"
    )
    assert lockfile.read_lock(target)["source_git_commit"] is None


def test_a_canonical_file_no_target_receives_leaves_the_revision_stated(tmp_path: Path) -> None:
    """The comparison is scoped to the files the mapping carries into a target,
    because those are the payload's bytes. A canonical file no target receives
    cannot make the payload this commit's or stop it being, and requiring it to
    match would strip provenance from every target over a file no agent loads."""

    source = make_source(tmp_path)
    notes = write(source / "canonical" / "README.md", "how the canonical buckets are laid out\n")
    head = commit_source(source)
    write(notes, "edited, never committed\n")

    target = deploy_from(source, tmp_path)

    assert deploy.target_relative_path_for("canonical/README.md") is None
    assert not (target / "README.md").exists(), "the mapping carries it nowhere"
    assert lockfile.read_lock(target)["source_git_commit"] == head


def colliding_source(tmp_path: Path) -> Path:
    """A canonical tree whose buckets carry two files to one target path.

    Reachable from the layout rather than contrived: `repo-root` maps to the
    target root, so it reaches under every other bucket's prefix — here onto the
    path `codex/config.toml.template` is rendered to."""

    source = make_source(tmp_path)
    write(source / "canonical" / "repo-root" / ".codex" / "config.toml", "a second claim on the codex config\n")
    return source


def test_two_canonical_files_carried_to_one_target_path_stop_the_deploy(tmp_path: Path) -> None:
    """A target path holds one file, so a payload claiming to carry two into it
    is not a payload. Measured before this refusal: the deploy wrote both in
    bucket order, the target kept the later one, and the lock stated `HEAD` over
    seventeen records for sixteen target files — a canonical commit vouching for
    bytes no target file carries, which placement reads as the protocol that
    target ran."""

    source = colliding_source(tmp_path)
    commit_source(source)
    target = tmp_path / "target"
    target.mkdir()

    with pytest.raises(deploy.PayloadError) as refused:
        deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")

    message = str(refused.value)
    assert ".codex/config.toml" in message
    assert "canonical/repo-root/.codex/config.toml" in message
    assert "canonical/codex/config.toml.template" in message
    assert list(target.iterdir()) == [], "refused before writing anything, so there is no receipt to trust"


def test_a_colliding_payload_is_refused_by_every_surface_that_reads_the_mapping(tmp_path: Path) -> None:
    """A dry-run would otherwise answer as though the dropped file never existed:
    it keys by target path, so the collision collapses into one entry and the
    preview describes a payload the deploy cannot carry. The deployment, both
    receipts and the preview agree because the mapping never hands any of them a
    payload it cannot resolve.

    `status` is deliberately not on this list any more. It reads the target's own
    receipts and the lines this repository releases, never the canonical tree, so
    a payload that cannot currently be resolved is not something it can be asked
    about — and a target deployed before the collision appeared is exactly as
    ready as it was."""

    source = make_source(tmp_path)
    target = deploy_from(source, tmp_path)

    write(source / "canonical" / "repo-root" / ".codex" / "config.toml", "a second claim on the codex config\n")

    with pytest.raises(deploy.PayloadError):
        deploy.preview_deploy(target, root=source)
    with pytest.raises(deploy.PayloadError):
        deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")


def test_deploy_cli_reports_a_payload_it_cannot_resolve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The refusal is a canonical-authoring fact, so it has to reach the operator
    who can fix it as a named cause rather than a traceback."""

    source = colliding_source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.setenv("MANDREL_SOURCE_ROOT", str(source))
    monkeypatch.setenv("MANDREL_REGISTRY", str(tmp_path / "registry.json"))

    assert cli.main(["deploy", str(target)]) == 2

    assert "two canonical files map to .codex/config.toml" in capsys.readouterr().err


def host_folds_target_paths(tmp_path: Path, one: str, other: str) -> bool:
    """Whether this host resolves the two names to a single file.

    Probed rather than assumed so the alias assertions below state what was
    measured here; the refusal itself is host-independent, so the tests hold on
    a case- and normalization-sensitive host too."""

    probe = tmp_path / "alias-probe"
    probe.mkdir(exist_ok=True)
    (probe / one).write_bytes(b"first\n")
    return (probe / other).exists()


def test_case_variant_target_paths_are_one_target_file_and_stop_the_deploy(tmp_path: Path) -> None:
    """`.CURSOR/hooks/x` and `.cursor/hooks/x` are two strings and one file on a
    default macOS volume, so a payload carrying both drops one exactly as an
    identical pair does — while `claimed`, the manifest map and `check_status`
    all still see two. Measured on this checkout's APFS volume before the
    identity rule: the deploy accepted both, the two paths opened one inode, the
    0755 write was replaced by the 0644 one, the lock stated `HEAD` over
    seventeen records for sixteen target files, and `check_status` read in
    sync — a canonical commit vouching for a path and mode set the target does
    not hold."""

    source = make_source(tmp_path)
    alias = write(source / "canonical" / "repo-root" / ".CURSOR" / "hooks" / "session-start.sh", "#!/bin/sh\n")
    alias.chmod(0o755)
    (source / "canonical" / "cursor" / "hooks" / "session-start.sh").chmod(0o644)
    commit_source(source)
    target = tmp_path / "target"
    target.mkdir()

    if host_folds_target_paths(tmp_path, ".cursor-probe", ".CURSOR-probe"):
        assert (target.parent / "alias-probe" / ".CURSOR-probe").exists(), "one file on this host"

    with pytest.raises(deploy.PayloadError) as refused:
        deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")

    message = str(refused.value)
    assert ".CURSOR/hooks/session-start.sh" in message and ".cursor/hooks/session-start.sh" in message
    assert "canonical/repo-root/.CURSOR/hooks/session-start.sh" in message
    assert "canonical/cursor/hooks/session-start.sh" in message
    assert list(target.iterdir()) == [], "refused before writing anything, so there is no receipt to trust"


def test_normalization_variant_target_paths_are_one_target_file(tmp_path: Path) -> None:
    """The same fold covers the other alias a default macOS volume applies:
    measured here, a name written decomposed reopens the file written composed.
    Refused on every host, including one that keeps the two apart — the payload
    is the canonical tree's and the lock is portable, so the answer cannot
    depend on the machine the deploy ran on."""

    composed = unicodedata.normalize("NFC", "hooks/café.sh")
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed, "the two spellings must differ, or this measures nothing"

    source = make_source(tmp_path)
    write(source / "canonical" / "repo-root" / ".cursor" / decomposed, "#!/bin/sh\n")
    write(source / "canonical" / "cursor" / composed, "#!/bin/sh\n")
    commit_source(source)
    target = tmp_path / "target"
    target.mkdir()

    with pytest.raises(deploy.PayloadError) as refused:
        deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")

    assert "one file on a case- or normalization-insensitive volume" in str(refused.value)
    assert list(target.iterdir()) == []


def deployed_records(source: Path) -> list[lockfile.DeployedFile]:
    """The record set a deploy of this source builds, as `deploy_canonical`
    builds it: the bytes read once and written, and the mode applied."""

    records = []
    for item in deploy.iter_deployment_items(source):
        data = item.source_path.read_bytes()
        records.append(
            lockfile.DeployedFile(
                canonical_relative_path=item.canonical_relative_path,
                target_relative_path=item.target_relative_path,
                canonical_sha256=hashing.sha256_bytes(data),
                canonical_size_bytes=len(data),
                executable=bool(item.mode & 0o111),
            )
        )
    return records


def test_the_receipt_refuses_a_record_set_whose_targets_are_one_file(tmp_path: Path) -> None:
    """The receipt's own precondition asks the same question as the mapping, so
    it has to count target files the same way. Two records the target resolves
    to one file describe a payload it cannot be holding — only the later write
    survives there — and counting them as two would let the commit be stated
    over a record the target never kept. Every other input here is exactly the
    passing one, so this measures the identity rule alone: as string equality,
    the aliased set below states the commit."""

    source = make_source(tmp_path)
    head = commit_source(source)
    records = deployed_records(source)
    assert lockfile.payload_source_commit(source, records, deploys=deploy.deploys_canonical_path) == head

    aliased = [
        replace(record, target_relative_path=".CURSOR/hooks/session-start.sh")
        if record.target_relative_path == ".codex/hooks/session-start.sh"
        else record
        for record in records
    ]
    assert len({record.target_relative_path for record in aliased}) == len(aliased), "distinct as strings"
    assert lockfile.payload_source_commit(source, aliased, deploys=deploy.deploys_canonical_path) is None


def test_work_outside_the_canonical_tree_leaves_the_revision_stated(tmp_path: Path) -> None:
    """The payload's bytes are the canonical tree's. Uncommitted work elsewhere
    in the checkout says nothing about them, and refusing a revision for it would
    strip provenance from every target deployed during ordinary development."""

    source = make_source(tmp_path)
    head = commit_source(source)
    write(source / "notes.md", "a scratch note, uncommitted\n")
    write(source / "mandrel" / "deploy.py", "# edited tooling, uncommitted\n")

    target = deploy_from(source, tmp_path)

    assert lockfile.read_lock(target)["source_git_commit"] == head


def test_deploy_reports_whether_the_payload_has_a_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unstated revision is a fact the operator can act on — commit the
    canonical work and redeploy — and one they would otherwise meet much later,
    as a report their target cannot be assessed by."""

    source = make_source(tmp_path)
    head = commit_source(source)
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.setenv("MANDREL_SOURCE_ROOT", str(source))
    monkeypatch.setenv("MANDREL_REGISTRY", str(tmp_path / "registry.json"))

    assert cli.main(["deploy", str(target)]) == 0
    assert f"source revision: {head}" in capsys.readouterr().out

    write(source / "canonical" / "protocols" / "dev.md", "dev contract, edited and never committed\n")

    assert cli.main(["deploy", str(target)]) == 0
    assert "source revision: none" in capsys.readouterr().out


def test_deploy_preview_does_not_write(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()

    preview = deploy.preview_deploy(target, root=source)

    assert preview.total_files == len(deploy.iter_deployment_items(source))
    assert {change.action for change in preview.changes} == {"add"}
    assert preview.gitignore_action == "add"
    assert not (target / "CLAUDE.md").exists()
    assert not (target / ".codex" / "config.toml").exists()
    assert not (target / ".gitignore").exists()
    assert not (target / ".ai-deploy-manifest.json").exists()
    assert not (target / ".ai-deploy-lock.json").exists()


def test_deploy_preview_after_deploy_and_target_edit(tmp_path: Path) -> None:
    source, target, _deployed = deploy_to_tmp(tmp_path)

    in_sync_preview = deploy.preview_deploy(target, root=source)
    assert {change.action for change in in_sync_preview.changes} == {"unchanged"}
    assert in_sync_preview.gitignore_action == "unchanged"

    write(target / "CLAUDE.md", "local edit\n")
    edited_preview = deploy.preview_deploy(target, root=source)

    assert any(
        change.action == "update" and change.target_relative_path == "CLAUDE.md"
        for change in edited_preview.changes
    )


def test_status_in_sync(tmp_path: Path) -> None:
    source, target, _deployed = deploy_to_tmp(tmp_path)

    result = status_of(target, source)

    assert result.in_sync
    assert not result.drifts


def test_status_target_modified(tmp_path: Path) -> None:
    source, target, _deployed = deploy_to_tmp(tmp_path)
    write(target / "CLAUDE.md", "local edit\n")

    result = status_of(target, source)

    assert "target modified" in drift_kinds(result)
    assert any(drift.target_relative_path == "CLAUDE.md" for drift in result.drifts)


def test_status_sees_a_deployed_file_stripped_of_its_executable_bit(tmp_path: Path) -> None:
    """A deployed file is bytes and a mode. A hook chmod'd back to 0644 holds
    every byte the receipt hashes and does not run, so a check that reads only
    content calls it in sync while the target no longer runs the payload the
    receipt describes — the state `mandrel status` is asked about when a release
    assessment asks whether a target still matches its receipt."""

    source, target, _deployed = deploy_to_tmp(tmp_path)
    script = ".claude/skills/demo-skill/scan.sh"
    (target / script).chmod(0o644)

    result = status_of(target, source)

    drifts = [drift for drift in result.drifts if drift.target_relative_path == script]
    assert [(drift.kind, drift.detail) for drift in drifts] == [("target modified", "mode differs")]


def test_status_and_preview_read_one_target_state_the_same_way(tmp_path: Path) -> None:
    """The two surfaces answer different questions — what the target no longer
    matches, and what a deploy would do — and disagreeing about whether a file's
    mode is part of it made the tool contradict itself about the same target."""

    source, target, _deployed = deploy_to_tmp(tmp_path)
    script = ".claude/skills/demo-skill/scan.sh"
    (target / script).chmod(0o644)
    write(target / "CLAUDE.md", "local edit\n")

    result = status_of(target, source)
    preview = deploy.preview_deploy(target, root=source)

    assert not result.in_sync
    change = next(change for change in preview.changes if change.target_relative_path == script)
    drift = next(drift for drift in result.drifts if drift.target_relative_path == script)
    assert (change.action, change.detail) == ("update", "mode differs")
    assert (drift.kind, drift.detail) == ("target modified", "mode differs")
    assert {change.target_relative_path for change in preview.changed} == {
        drift.target_relative_path for drift in result.drifts
    }


def test_status_reports_a_deployed_file_that_lost_both_its_bytes_and_its_mode(tmp_path: Path) -> None:
    """One file, one drift, and the detail says which parts moved — the same
    vocabulary the preview uses for the same state."""

    source, target, _deployed = deploy_to_tmp(tmp_path)
    script = ".claude/skills/demo-skill/scan.sh"
    write(target / script, "#!/bin/sh\nlocal edit\n").chmod(0o644)

    result = status_of(target, source)

    drifts = [drift for drift in result.drifts if drift.target_relative_path == script]
    assert [(drift.kind, drift.detail) for drift in drifts] == [("target modified", "content and mode differ")]


def test_a_canonical_mode_that_moved_after_the_deploy_is_not_this_target_drift(tmp_path: Path) -> None:
    """Why the expected mode is the receipt's: with the canonical tree as the
    expectation, a file made executable after a deploy reads as a target somebody
    edited, and the operator is sent to look at the wrong side. The target holds
    exactly what its receipt describes, so it holds the revision that receipt
    names — an uncommitted chmod in the maintainer's checkout is that checkout's
    state and reaches no target."""

    source, target, _deployed = deploy_to_tmp(tmp_path)
    runbook = ".ai-protocol/workflow/runbook.md"
    (source / "canonical" / "workflow" / "runbook.md").chmod(0o755)

    result = status_of(target, source)

    assert not [drift for drift in result.drifts if drift.target_relative_path == runbook]
    assert result.in_sync


def test_status_reports_a_receipt_that_states_no_mode_and_still_checks_content(tmp_path: Path) -> None:
    """A manifest written before modes were recorded cannot answer whether a
    target still carries the mode it was deployed with. Said once, for the
    receipt rather than for every file it lists, and the content checks it can
    still answer keep running until the next deploy writes the modes in."""

    source, target, _deployed = deploy_to_tmp(tmp_path)
    saved = manifest.read_manifest(target)
    for record in saved["files"].values():
        record.pop(deploy.MODE_FIELD)
    manifest.write_manifest(target, saved)
    script = ".claude/skills/demo-skill/scan.sh"
    (target / script).chmod(0o644)
    write(target / "CLAUDE.md", "local edit\n")

    result = status_of(target, source)

    receipt = [drift for drift in result.drifts if drift.target_relative_path == paths.MANIFEST_FILENAME]
    assert len(receipt) == 1
    assert receipt[0].kind == "invalid manifest entry"
    assert "state no deployed mode" in receipt[0].detail
    assert not [drift for drift in result.drifts if drift.target_relative_path == script]
    assert "target modified" in drift_kinds(result)
    assert any(drift.target_relative_path == "CLAUDE.md" for drift in result.drifts)


@pytest.mark.parametrize("mode", (True, 0o100755, -1, "0755", None))
def test_status_treats_an_unreadable_recorded_mode_as_no_mode(tmp_path: Path, mode: object) -> None:
    """A record whose mode is not permission bits states none — `True` because
    it is an `int` in Python, a whole `st_mode` because it is not the value a
    deploy applies. Guessing at either would vouch for a mode nothing wrote."""

    source, target, _deployed = deploy_to_tmp(tmp_path)
    saved = manifest.read_manifest(target)
    script = ".claude/skills/demo-skill/scan.sh"
    saved["files"][script][deploy.MODE_FIELD] = mode
    manifest.write_manifest(target, saved)

    result = status_of(target, source)

    assert not [drift for drift in result.drifts if drift.target_relative_path == script]
    assert [drift.detail for drift in result.drifts if drift.target_relative_path == paths.MANIFEST_FILENAME] == [
        f"1 of {result.total_files} records state no deployed mode; redeploy so status can check modes"
    ]


@pytest.mark.parametrize("spelling", ("relative", "absolute"))
def test_a_manifest_record_naming_a_file_outside_the_target_is_refused_before_it_is_read(
    tmp_path: Path, spelling: str
) -> None:
    """Integrity is the receipt against the target and nothing else now, so the
    containment the payload mapping used to imply has to be stated.

    `target_root / "../x"` reads a file above the target and an absolute path
    discards the target root outright. The record here carries the correct hash
    for what it points at, which is what makes it dangerous rather than merely
    wrong: unrefused, `status` counts a file the target does not contain as the
    target's own and clears the deployment on the strength of its bytes."""

    source, target, _deployed = deploy_to_tmp(tmp_path)
    outside = write(tmp_path / "outside.txt", "not this target's\n")
    escaping = f"../{outside.name}" if spelling == "relative" else str(outside)

    saved = manifest.read_manifest(target)
    saved["files"] = {
        escaping: {
            "canonical_relative_path": "canonical/protocols/dev.md",
            "target_relative_path": escaping,
            "sha256": hashing.sha256_file(outside),
            "size_bytes": outside.stat().st_size,
            deploy.MODE_FIELD: stat.S_IMODE(outside.stat().st_mode),
        }
    }
    manifest.write_manifest(target, saved)

    result = status_of(target, source, user_skills_root=tmp_path / "no-skills")

    assert not result.in_sync
    assert [(drift.kind, drift.target_relative_path) for drift in result.drifts] == [
        ("invalid manifest entry", escaping)
    ]


def test_a_manifest_record_that_respells_a_contained_path_is_refused_with_them(tmp_path: Path) -> None:
    """Inside the target, and still not a path a deploy wrote.

    The spelling is an identity elsewhere in `status` — which record gets the
    loader's normalized comparison, which records name deployed skills — so a
    receipt free to write one file under two spellings is a receipt whose paths
    no longer name one file. Refused rather than quietly normalized: a reader
    that tidied the path into shape would be that second spelling."""

    source, target, _deployed = deploy_to_tmp(tmp_path)
    saved = manifest.read_manifest(target)
    record = saved["files"].pop("CLAUDE.md")
    record["target_relative_path"] = "./CLAUDE.md"
    saved["files"]["./CLAUDE.md"] = record
    manifest.write_manifest(target, saved)

    result = status_of(target, source, user_skills_root=tmp_path / "no-skills")

    assert [(drift.kind, drift.target_relative_path, drift.detail) for drift in result.drifts] == [
        ("invalid manifest entry", "./CLAUDE.md", "the path is not normalized; a deploy writes 'CLAUDE.md'")
    ]


@pytest.mark.parametrize("interrupted", ("lock", "manifest"))
def test_a_deploy_interrupted_between_its_receipts_leaves_a_target_that_reads_as_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interrupted: str
) -> None:
    """The two halves of `status` read two documents, and neither document says
    which deploy wrote it — so a pair left over from two deployments would read
    as an intact target placed at a revision it is no longer running, and every
    evaluation that target then produced would be attributed to the wrong side
    of a release.

    What decides that is the order the receipts are published in. The lock lands
    first, so whichever write is interrupted, the stale document left behind is
    a manifest whose hashes the payload on disk no longer matches."""

    source, target, _deployed = deploy_to_tmp(tmp_path)
    lines = lines_of(source)
    contract = ".ai-protocol/protocols/dev.md"

    git(source, "checkout", "-q", "-b", "off-the-line")
    write(source / "canonical" / "protocols" / "dev.md", "revised dev contract\n")
    git(source, "add", "-A")
    git(source, "commit", "-q", "-m", "revised payload")

    def interrupt(*arguments: object, **keywords: object) -> None:
        raise OSError("no space left on device")

    monkeypatch.setattr(*((lockfile, "write_lock") if interrupted == "lock" else (manifest, "write_manifest")), interrupt)
    with pytest.raises(OSError):
        deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")
    monkeypatch.undo()

    assert (target / contract).read_text(encoding="utf-8") == "revised dev contract\n"
    result = deploy.check_status(target, lines=lines, user_skills_root=tmp_path / "no-skills")
    assert not result.in_sync
    assert [drift.target_relative_path for drift in result.drifts if drift.kind == "target modified"] == [contract]


def test_manifest_records_the_mode_the_deploy_applied(tmp_path: Path) -> None:
    source, target, _deployed = deploy_to_tmp(tmp_path)
    saved = manifest.read_manifest(target)

    script = saved["files"][".claude/skills/demo-skill/scan.sh"]
    manifest_page = saved["files"][".ai-protocol/workflow/runbook.md"]
    assert script[deploy.MODE_FIELD] == 0o755
    assert manifest_page[deploy.MODE_FIELD] == stat.S_IMODE(
        (source / "canonical" / "workflow" / "runbook.md").stat().st_mode
    )
    assert not manifest_page[deploy.MODE_FIELD] & 0o111


def test_status_allows_claude_md_target_memory_import_index_variants(tmp_path: Path) -> None:
    source = make_loader_source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")

    target_text = (target / "CLAUDE.md").read_text(encoding="utf-8")
    target_text = target_text.replace("@.ai/design.md", "@.ai/design/index.md")
    target_text = target_text.replace("@.ai/conventions.md", "@.ai/conventions/index.md")
    write(target / "CLAUDE.md", target_text)

    result = status_of(target, source)

    assert result.in_sync
    assert not result.drifts


def test_status_allows_claude_md_memory_import_variants_with_legacy_manifest(tmp_path: Path) -> None:
    source = make_loader_source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")
    saved = manifest.read_manifest(target)
    saved["files"]["CLAUDE.md"].pop(deploy.NORMALIZED_SHA256_FIELD)
    manifest.write_manifest(target, saved)

    target_text = (target / "CLAUDE.md").read_text(encoding="utf-8")
    target_text = target_text.replace("@.ai/overview.md", "@.ai/overview/index.md")
    write(target / "CLAUDE.md", target_text)

    result = status_of(target, source)

    assert result.in_sync
    assert not result.drifts


def test_status_rejects_claude_md_non_topic_memory_import_variant(tmp_path: Path) -> None:
    source = make_loader_source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")

    target_text = (target / "CLAUDE.md").read_text(encoding="utf-8")
    target_text = target_text.replace("@.ai/map.md", "@.ai/map/index.md")
    write(target / "CLAUDE.md", target_text)

    result = status_of(target, source)

    assert "target modified" in drift_kinds(result)
    assert any(drift.target_relative_path == "CLAUDE.md" for drift in result.drifts)


def test_deploy_keeps_single_file_form_without_snapshot(tmp_path: Path) -> None:
    source = make_loader_source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()

    deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")

    assert loader_import(target, "design") == ".ai/design.md"
    assert loader_import(target, "conventions") == ".ai/conventions.md"


def test_deploy_points_loader_at_directory_form_entrypoint(tmp_path: Path) -> None:
    source = make_loader_source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    write_snapshot(target, directory_form=("design",))

    deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")

    assert loader_import(target, "design") == ".ai/design/index.md"
    assert loader_import(target, "conventions") == ".ai/conventions.md"


def test_deploy_does_not_revert_directory_form_upgrade(tmp_path: Path) -> None:
    source = make_loader_source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    write_snapshot(target)
    deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")
    assert loader_import(target, "design") == ".ai/design.md"

    upgrade_to_directory_form(target, "design")
    deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")

    assert loader_import(target, "design") == ".ai/design/index.md"
    assert status_of(target, source).in_sync


def test_deploy_repairs_loader_left_on_pre_upgrade_path(tmp_path: Path) -> None:
    source = make_loader_source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    write_snapshot(target)
    deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")
    upgrade_to_directory_form(target, "architecture")

    stale = status_of(target, source)
    assert "stale eager import" in drift_kinds(stale)

    deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")

    assert loader_import(target, "architecture") == ".ai/architecture/index.md"
    assert status_of(target, source).in_sync


def test_deploy_entrypoint_resolution_is_idempotent(tmp_path: Path) -> None:
    source = make_loader_source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    write_snapshot(target, directory_form=("overview", "conventions"))

    deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")
    first = (target / "CLAUDE.md").read_bytes()
    deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")

    assert (target / "CLAUDE.md").read_bytes() == first
    preview = deploy.preview_deploy(target, root=source)
    assert all(change.action == "unchanged" for change in preview.changes)


def test_deploy_follows_router_over_file_shape(tmp_path: Path) -> None:
    source = make_loader_source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    # Both forms on disk: file shape alone is ambiguous, the router decides.
    write_snapshot(target, router={"design": "design/index.md"})
    write(target / ".ai" / "design" / "index.md", "design index\n")

    deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")

    assert loader_import(target, "design") == ".ai/design/index.md"


def test_deploy_ignores_router_entry_escaping_the_snapshot(tmp_path: Path) -> None:
    source = make_loader_source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    write_snapshot(target, router={"design": "../outside/design.md"})

    deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")

    assert loader_import(target, "design") == ".ai/design.md"


def test_status_flags_stale_eager_import(tmp_path: Path) -> None:
    source = make_loader_source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    write_snapshot(target)
    deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")

    upgrade_to_directory_form(target, "design")
    result = status_of(target, source)

    assert not result.in_sync
    assert "stale eager import" in drift_kinds(result)
    assert any(
        drift.target_relative_path == "CLAUDE.md" and ".ai/design/index.md" in drift.detail
        for drift in result.drifts
    )
    # The kind must be printable, otherwise the drift count reports with no detail.
    assert "stale eager import" in deploy.format_status(result)


def test_status_flags_ambiguous_entrypoint(tmp_path: Path) -> None:
    source = make_loader_source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    write_snapshot(target)
    deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")

    write(target / ".ai" / "design" / "index.md", "design index\n")
    result = status_of(target, source)

    assert "ambiguous memory entrypoint" in drift_kinds(result)
    assert "ambiguous memory entrypoint" in deploy.format_status(result)


def personal_skill(root: Path, name: str) -> Path:
    """A discoverable personal-level skill: agent tools look for
    `<root>/<name>/SKILL.md`."""
    return write(root / name / "SKILL.md", f"---\nname: {name}\n---\n")


def test_deployed_skill_names_reads_directory_names_under_the_skills_root() -> None:
    assert deploy.deployed_skill_names(
        [
            ".claude/skills/demo-skill/SKILL.md",
            ".claude/skills/demo-skill/scan.sh",
            ".claude/skills/other/reference/deep.md",
            ".claude/settings.json",
            ".claude/skills",
            "CLAUDE.md",
        ]
    ) == ("demo-skill", "other")


def test_status_flags_shadowed_skill(tmp_path: Path) -> None:
    """The hash says in sync and it is — but a personal-level copy of the same
    name is what actually runs, so the deployed contract is not the executed
    one."""
    source, target, _deployed = deploy_to_tmp(tmp_path)
    user_skills = tmp_path / "home-skills"
    personal_skill(user_skills, "demo-skill")

    result = status_of(target, source, user_skills_root=user_skills)

    assert not result.in_sync
    assert "shadowed skill" in drift_kinds(result)
    assert any(
        drift.target_relative_path == ".claude/skills/demo-skill"
        and str(user_skills / "demo-skill" / "SKILL.md") in drift.detail
        for drift in result.drifts
    )
    # The kind must be printable, otherwise the drift count reports with no detail.
    assert "shadowed skill" in deploy.format_status(result)


def test_status_silent_when_personal_skills_do_not_collide(tmp_path: Path) -> None:
    source, target, _deployed = deploy_to_tmp(tmp_path)
    user_skills = tmp_path / "home-skills"
    personal_skill(user_skills, "something-else")

    assert status_of(target, source, user_skills_root=user_skills).in_sync


def test_status_silent_without_a_personal_skills_root(tmp_path: Path) -> None:
    source, target, _deployed = deploy_to_tmp(tmp_path)

    result = status_of(target, source, user_skills_root=tmp_path / "absent")

    assert result.in_sync
    assert deploy.shadowed_skill_drifts(["demo-skill"], user_skills_root=tmp_path / "absent") == ()


def test_status_silent_for_personal_directory_that_is_not_a_skill(tmp_path: Path) -> None:
    """A bare directory is not discoverable as a skill, so it shadows nothing;
    reporting it would train operators to ignore the check."""
    source, target, _deployed = deploy_to_tmp(tmp_path)
    user_skills = tmp_path / "home-skills"
    (user_skills / "demo-skill").mkdir(parents=True)

    assert status_of(target, source, user_skills_root=user_skills).in_sync


def test_status_cli_exits_nonzero_for_shadowed_skill(tmp_path: Path, monkeypatch, capsys) -> None:
    source, target, _deployed = deploy_to_tmp(tmp_path)
    user_skills = tmp_path / "home-skills"
    personal_skill(user_skills, "demo-skill")
    monkeypatch.setenv("MANDREL_SOURCE_ROOT", str(source))
    monkeypatch.setenv("MANDREL_CLAUDE_SKILLS_ROOT", str(user_skills))

    exit_code = cli.main(["status", str(target)])

    assert exit_code == 1
    assert "shadowed skill" in capsys.readouterr().out


def test_status_flags_router_entry_outside_legal_forms(tmp_path: Path) -> None:
    source = make_loader_source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    write_snapshot(target, router={"design": "design/principles.md"})
    write(target / ".ai" / "design" / "principles.md", "principles\n")
    deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")

    result = status_of(target, source)

    assert "stale eager import" in drift_kinds(result)
    assert any(
        drift.target_relative_path == ".ai/index.md" and "expected" in drift.detail for drift in result.drifts
    )


def test_status_flags_router_entry_pointing_at_missing_file(tmp_path: Path) -> None:
    source = make_loader_source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    write_snapshot(target)
    deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")
    (target / ".ai" / "design.md").unlink()

    result = status_of(target, source)

    assert any(
        drift.kind == "stale eager import" and "does not exist" in drift.detail for drift in result.drifts
    )


def test_status_clean_for_target_without_snapshot(tmp_path: Path) -> None:
    source = make_loader_source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")

    assert status_of(target, source).in_sync
    assert deploy.eager_import_drifts(target) == ()


def test_resolve_memory_entrypoint_matches_hook_fallback_order(tmp_path: Path) -> None:
    target = tmp_path / "target"
    write_snapshot(target, directory_form=("design",))

    assert deploy.resolve_memory_entrypoint(target, "design") == ".ai/design/index.md"
    assert deploy.resolve_memory_entrypoint(target, "conventions") == ".ai/conventions.md"
    # Unknown snapshot shape falls back to the single-file default.
    assert deploy.resolve_memory_entrypoint(tmp_path / "empty", "design") == ".ai/design.md"


def test_status_missing_target_file(tmp_path: Path) -> None:
    source, target, _deployed = deploy_to_tmp(tmp_path)
    (target / ".ai-protocol" / "protocols" / "dev.md").unlink()

    result = status_of(target, source)

    assert "missing target file" in drift_kinds(result)
    assert any(drift.target_relative_path == ".ai-protocol/protocols/dev.md" for drift in result.drifts)


# --- which line the target is standing on ------------------------------------
#
# The half of `status` that used to compare the target's receipt against the
# bytes in the maintainer's checkout. That referent was unattainable by
# construction — an uncommitted tree is exactly the state a deploy refuses to
# state a commit for — and it moved under an ordinary `git checkout`. What these
# assert is that the answer is now a placement against named lines, and that it
# holds still while the source checkout does whatever it likes.


def branch_commit(source: Path, branch: str, message: str) -> str:
    """One more canonical commit, landed on a branch of its own.

    The release line is left where it was, which is what a sealed candidate
    actually is: a revision this repository is measuring and not the one it is
    releasing."""

    git(source, "checkout", "-q", "-b", branch)
    write(source / "canonical" / "protocols" / "dev.md", f"dev contract, {message}\n")
    git(source, "add", "-A")
    git(source, "commit", "-q", "-m", message)
    return git(source, "rev-parse", "HEAD")


def test_a_target_deployed_at_the_base_is_ready(tmp_path: Path) -> None:
    """The ordinary case, asserted on the receipt rather than on a comparison:
    the lock states the commit the deploy read the canonical tree at, and that
    commit is the one the source line names."""

    source, target, _deployed = deploy_to_tmp(tmp_path)

    lines = lines_of(source)
    assert lockfile.read_lock(target)["source_git_commit"] == lines.base
    assert status_of(target, source).in_sync


def test_a_target_deployed_at_a_sealed_candidate_is_ready(tmp_path: Path) -> None:
    """Two accepted identities, not one. A target running the change being
    measured is a target the evaluation layer can attribute a report to, so
    calling it drift would name a redeploy that would destroy the measurement."""

    source = make_source(tmp_path)
    base = commit_source(source)
    candidate = branch_commit(source, "candidate", "the change under measurement")
    target = deploy_from(source, tmp_path)
    assert candidate != base
    assert lockfile.read_lock(target)["source_git_commit"] == candidate

    assert deploy.check_status(target, lines=lines_of(source, candidate=candidate)).in_sync

    # And the same target, with nothing sealed, is somewhere this repository is
    # not currently releasing from.
    assert "off the release line" in drift_kinds(status_of(target, source))


def test_a_target_on_neither_line_is_not_ready_and_the_placement_is_stated(tmp_path: Path) -> None:
    """The finding has to carry both identities. "Not current" alone leaves an
    operator unable to tell a target one release behind from one deployed off
    somebody's branch, and those are different next steps."""

    source = make_source(tmp_path)
    base = commit_source(source)
    stranded = branch_commit(source, "elsewhere", "work that was never released")
    target = deploy_from(source, tmp_path)
    sealed = "e" * 40

    result = deploy.check_status(target, lines=lines_of(source, candidate=sealed))

    assert not result.in_sync
    drifts = [drift for drift in result.drifts if drift.kind == "off the release line"]
    assert [drift.target_relative_path for drift in drifts] == [paths.LOCK_FILENAME]
    assert stranded in drifts[0].detail
    assert base in drifts[0].detail and sealed in drifts[0].detail
    # Printable, or the drift is counted in the header and then never named.
    assert "off the release line" in deploy.format_status(result)


def test_uncommitted_canonical_work_changes_no_target_readiness(tmp_path: Path) -> None:
    """The property the whole change exists for. Every managed target used to go
    not-ready together the moment anything under `canonical/` was edited here —
    and stayed that way until it was committed, since a dirty tree is precisely
    what a deploy will not state a commit for."""

    source, target, _deployed = deploy_to_tmp(tmp_path)
    before = status_of(target, source)

    write(source / "canonical" / "repo-root" / "CLAUDE.md", "canonical edit, uncommitted\n")
    (source / "canonical" / "protocols" / "dev.md").unlink()
    write(source / "canonical" / "protocols" / "review.md", "a contract that was never deployed\n")

    assert before.in_sync
    assert status_of(target, source).in_sync


def test_moving_the_checkout_to_another_branch_changes_no_target_readiness(tmp_path: Path) -> None:
    """A ref resolves to the same commit whichever branch this checkout sits on.
    Reading the canonical tree instead made a maintainer's `git checkout` an
    unannounced verdict on every repository they manage."""

    source, target, _deployed = deploy_to_tmp(tmp_path)

    branch_commit(source, "somewhere-else", "work on another branch")

    assert git(source, "rev-parse", "--abbrev-ref", "HEAD") == "somewhere-else"
    assert status_of(target, source).in_sync


def test_a_target_with_no_receipt_to_place_is_not_ready(tmp_path: Path) -> None:
    """Readiness used to be answered from the manifest alone, so a target with a
    hash-correct manifest and no placeable lock passed — and then produced
    evaluation reports the assessment layer had to throw away."""

    source, target, _deployed = deploy_to_tmp(tmp_path)
    (target / paths.LOCK_FILENAME).unlink()

    result = status_of(target, source)

    assert not result.in_sync
    drifts = [drift for drift in result.drifts if drift.kind == "unplaceable deployment"]
    assert [drift.target_relative_path for drift in drifts] == [paths.LOCK_FILENAME]
    assert paths.LOCK_FILENAME in drifts[0].detail
    assert "unplaceable deployment" in deploy.format_status(result)


def test_a_receipt_that_ties_its_payload_to_no_commit_is_not_ready(tmp_path: Path) -> None:
    """The receipt is present, well-formed, and states null — the deploy's own
    answer for a payload it could not match to any commit. Nothing places what
    that target holds, and the line being resolvable does not change it."""

    source = make_source(tmp_path)
    commit_source(source)
    write(source / "canonical" / "protocols" / "dev.md", "dev contract, edited and never committed\n")
    target = deploy_from(source, tmp_path)
    assert lockfile.read_lock(target)["source_git_commit"] is None

    result = status_of(target, source)

    assert lines_of(source).known
    assert [drift.kind for drift in result.drifts] == ["unplaceable deployment"]
    assert "no source commit" in result.drifts[0].detail


def test_a_checkout_that_names_no_line_does_not_blame_the_target(tmp_path: Path) -> None:
    """A line this repository cannot resolve is this repository's state. Reported
    — a reading that cleared every target it could not judge would be the silent
    false clearance the whole placement exists to refuse — but reported against
    the line, so nobody is sent to inspect a target that is fine."""

    source, target, _deployed = deploy_to_tmp(tmp_path)

    result = deploy.check_status(target, lines=placement.source_lines(source, ref="refs/heads/nowhere"))

    assert [drift.kind for drift in result.drifts] == ["unresolved release line"]
    assert result.drifts[0].target_relative_path == "refs/heads/nowhere"
    assert "refs/heads/nowhere" in result.drifts[0].detail
    assert "unresolved release line" in deploy.format_status(result)


def test_a_line_that_could_not_be_named_is_dropped_where_it_could_not_have_mattered(tmp_path: Path) -> None:
    """A dropped candidate can only ever have added an acceptance, so it cannot
    have produced a verdict against a target already standing on the base.
    Reporting it there would take a target out of service over a reading that is
    not about it; on a target that is placed nowhere, the same reason is part of
    the finding."""

    source, target, _deployed = deploy_to_tmp(tmp_path)
    unread = placement.source_lines(source, ref=SOURCE_LINE, unresolved=("the lineage does not read",))

    assert deploy.check_status(target, lines=unread).in_sync

    moved_on = replace(unread, base="d" * 40)
    detail = deploy.check_status(target, lines=moved_on).drifts[0].detail
    assert "the lineage does not read" in detail


def test_status_asks_the_source_repository_once_for_a_whole_sweep(tmp_path: Path, monkeypatch, capsys) -> None:
    """One reading for the run. Resolving per target costs a Git call each and,
    worse, lets a sweep report two targets against two different answers if the
    source line moves underneath it."""

    source, _first, _deployed = deploy_to_tmp(tmp_path)
    second = tmp_path / "second-target"
    second.mkdir()
    registry_file = tmp_path / "registry.json"
    deploy.deploy_canonical(second, root=source, registry_file=registry_file)
    monkeypatch.setenv("MANDREL_SOURCE_ROOT", str(source))
    monkeypatch.setenv("MANDREL_REGISTRY", str(registry_file))

    readings = []
    resolve = cli._source_lines

    def record() -> placement.SourceLines:
        readings.append(resolve())
        return readings[-1]

    monkeypatch.setattr(cli, "_source_lines", record)

    assert cli.main(["status", "--all"]) == 0

    assert len(readings) == 1
    assert capsys.readouterr().out.count(deploy.STATUS_IN_SYNC) == 2


def test_status_answers_in_a_checkout_that_runs_no_evolution_workspace(tmp_path: Path, monkeypatch, capsys) -> None:
    """`status` is a deploy-layer question and may not require an evolution
    policy to answer it. A checkout with no workspace releases on the deploy
    layer's own line, and a target deployed from it is ready."""

    source = make_source(tmp_path)
    # On the branch the deploy layer's own line names, since with no policy to
    # read that fallback is the only thing saying which line this checkout is on.
    git(source, "init", "-q", "-b", placement.DEFAULT_SOURCE_LINE.rpartition("/")[2])
    git(source, "add", "-A")
    git(source, "commit", "-q", "-m", "canonical payload")
    target = deploy_from(source, tmp_path)
    monkeypatch.setenv("MANDREL_SOURCE_ROOT", str(source))
    monkeypatch.setenv("MANDREL_REGISTRY", str(tmp_path / "registry.json"))

    assert not (source / "evolution").exists()
    assert cli.main(["status", str(target)]) == 0
    assert deploy.STATUS_IN_SYNC in capsys.readouterr().out


def test_deploy_excludes_forbidden_files(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    canonical = source / "canonical"
    write(canonical / "orchestrator" / ".env", "SECRET=1\n")
    write(canonical / "orchestrator" / ".env.local", "SECRET=1\n")
    write(canonical / "orchestrator" / "logs" / "run.log", "log\n")
    write(canonical / "orchestrator" / ".venv" / "bin" / "python", "binary\n")
    write(canonical / "orchestrator" / "__pycache__" / "orchestrator.pyc", "cache\n")
    write(canonical / "claude" / "settings.local.json", "{}\n")
    write(canonical / "claude" / "projects" / "local.json", "{}\n")
    write(canonical / "codex" / "sessions.json", "{}\n")
    write(canonical / "codex" / ".env.prod", "SECRET=1\n")

    target = tmp_path / "target"
    target.mkdir()
    deployed = deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")

    forbidden_targets = [
        ".mandrel/orchestrator/.env",
        ".mandrel/orchestrator/.env.local",
        ".mandrel/orchestrator/logs/run.log",
        ".mandrel/orchestrator/.venv/bin/python",
        ".mandrel/orchestrator/__pycache__/orchestrator.pyc",
        ".claude/settings.local.json",
        ".claude/projects/local.json",
        ".codex/sessions.json",
        ".codex/.env.prod",
    ]
    for relative_path in forbidden_targets:
        assert not (target / relative_path).exists()
        assert relative_path not in deployed["files"]

    assert (target / ".mandrel" / "orchestrator" / ".env.example").is_file()


def test_gitignore_block_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    write(target / ".gitignore", "existing.log\n")

    deploy.append_gitignore_block(target)
    deploy.append_gitignore_block(target)

    content = (target / ".gitignore").read_text(encoding="utf-8")
    assert content.count(deploy.GITIGNORE_BEGIN) == 1
    assert content.count(deploy.GITIGNORE_END) == 1
    assert "existing.log" in content
    assert "/.mandrel/" in content
    assert "/.cursor/" in content
    assert "/.codex/" in content
    assert "/.claude/" in content
    assert "/CLAUDE.md" in content
    assert "/.ai-protocol/" in content
    assert "/ai-coding*.md" in content
    assert "/.ai-tasks/" in content
    assert "/.ai-deploy-manifest.json" in content
    assert "!/.ai-deploy-lock.json" in content


def test_gitignore_block_replaces_old_managed_block(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    write(
        target / ".gitignore",
        "\n".join(
            [
                "existing.log",
                deploy.GITIGNORE_BEGIN,
                ".ai-deploy-manifest.json",
                ".mandrel/orchestrator/.venv/",
                deploy.GITIGNORE_END,
                "",
            ]
        ),
    )

    deploy.append_gitignore_block(target)

    content = (target / ".gitignore").read_text(encoding="utf-8")
    assert content.count(deploy.GITIGNORE_BEGIN) == 1
    assert "/.cursor/" in content
    assert ".mandrel/orchestrator/.venv/" not in content
    assert "existing.log" in content


def test_deploy_updates_local_registry(tmp_path: Path) -> None:
    _source, target, _deployed = deploy_to_tmp(tmp_path)
    entries = json.loads((tmp_path / "registry.json").read_text(encoding="utf-8"))

    assert entries == [
        {
            "name": "target",
            "path": str(target.resolve()),
            "manifest": str(target.resolve() / ".ai-deploy-manifest.json"),
        }
    ]


def test_bootstrap_orchestrator_creates_venv_installs_requirements_and_env(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _source, target, _deployed = deploy_to_tmp(tmp_path)
    calls: list[tuple[list[str], Path, bool]] = []

    def fake_run(command: list[str], *, cwd: Path, check: bool) -> None:
        calls.append((command, cwd, check))
        if command == ["python3.14", "-m", "venv", str(target.resolve() / ".mandrel" / "orchestrator" / ".venv")]:
            python_path = target / ".mandrel" / "orchestrator" / ".venv" / "bin" / "python"
            write(python_path, "#!/bin/sh\n")

    monkeypatch.setattr(deploy.subprocess, "run", fake_run)

    result = deploy.bootstrap_orchestrator(target)

    venv_path = target.resolve() / ".mandrel" / "orchestrator" / ".venv"
    python_path = venv_path / "bin" / "python"
    requirements_path = target.resolve() / ".mandrel" / "orchestrator" / "requirements.txt"
    assert result.venv_path == venv_path
    assert result.python_path == python_path
    assert result.requirements_path == requirements_path
    assert result.env_created is True
    assert (target / ".mandrel" / "orchestrator" / ".env").read_text(encoding="utf-8") == "TOKEN=\n"
    assert calls == [
        (["python3.14", "-m", "venv", str(venv_path)], target.resolve(), True),
        ([str(python_path), "-m", "pip", "install", "-U", "pip"], target.resolve(), True),
        ([str(python_path), "-m", "pip", "install", "-r", str(requirements_path)], target.resolve(), True),
    ]


def test_bootstrap_orchestrator_keeps_existing_env_and_accepts_custom_python(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _source, target, _deployed = deploy_to_tmp(tmp_path)
    write(target / ".mandrel" / "orchestrator" / ".env", "CURSOR_API_KEY=local\n")
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, cwd: Path, check: bool) -> None:
        calls.append(command)
        if command[:3] == ["python3.13", "-m", "venv"]:
            write(target / ".mandrel" / "orchestrator" / ".venv" / "bin" / "python", "#!/bin/sh\n")

    monkeypatch.setattr(deploy.subprocess, "run", fake_run)

    result = deploy.bootstrap_orchestrator(target, python_executable="python3.13")

    assert result.env_created is False
    assert (target / ".mandrel" / "orchestrator" / ".env").read_text(encoding="utf-8") == "CURSOR_API_KEY=local\n"
    assert calls[0] == ["python3.13", "-m", "venv", str(target.resolve() / ".mandrel" / "orchestrator" / ".venv")]


def test_deploy_cli_parser_has_orchestrator_bootstrap_flags() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(
        [
            "deploy",
            "--bootstrap-orchestrator",
            "--orchestrator-python",
            "python3.13",
            "../target",
        ]
    )

    assert args.command == "deploy"
    assert args.bootstrap_orchestrator is True
    assert args.orchestrator_python == "python3.13"
    assert args.target == "../target"
