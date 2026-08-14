from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import unicodedata
from dataclasses import replace
from pathlib import Path

import pytest

from ai_native_deployment import cli, deploy, hashing, lockfile, manifest, paths


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
    source = make_source(tmp_path)
    write(source / "canonical" / "repo-root" / "CLAUDE.md", CLAUDE_MD_WITH_MEMORY_IMPORTS)
    return source


def deploy_to_tmp(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    source = make_source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    deployed = deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")
    return source, target, deployed


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
    and the commit it is at — the only state in which a deploy may state one."""

    git(source, "init", "-q")
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
    assert ".cursor/orchestrator/orchestrator.toml" in saved["files"]

    rendered_config = (target / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert "{{REPO_ROOT}}" not in rendered_config
    assert str(target.resolve()) in rendered_config

    config_record = saved["files"][".codex/config.toml"]
    assert config_record["sha256"] == hashing.sha256_file(target / ".codex" / "config.toml")
    assert deployed["files"] == saved["files"]

    lock = lockfile.read_lock(target)
    lock_text = (target / ".ai-deploy-lock.json").read_text(encoding="utf-8")
    assert lock["source_repo"] == "ai-native-deployment"
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
    path `codex/config.toml.template` is rendered to. `canonical/orchestrator/x`
    and `canonical/cursor/orchestrator/x` collide the same way."""

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
    """Status and dry-run answer from the same mapping and would otherwise answer
    as though the dropped file never existed: both key by target path, so the
    collision collapses into one entry and a target short a canonical file's
    bytes reads as in sync. The deployment, both receipts and the reading agree
    because the mapping never hands any of them a payload it cannot resolve."""

    source = make_source(tmp_path)
    target = deploy_from(source, tmp_path)
    assert deploy.check_status(target, root=source).in_sync

    write(source / "canonical" / "repo-root" / ".codex" / "config.toml", "a second claim on the codex config\n")

    with pytest.raises(deploy.PayloadError):
        deploy.check_status(target, root=source)
    with pytest.raises(deploy.PayloadError):
        deploy.preview_deploy(target, root=source)


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
    monkeypatch.setenv("AI_NATIVE_DEPLOYMENT_SOURCE_ROOT", str(source))
    monkeypatch.setenv("AI_NATIVE_DEPLOYMENT_REGISTRY", str(tmp_path / "registry.json"))

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
    write(source / "ai_native_deployment" / "deploy.py", "# edited tooling, uncommitted\n")

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
    monkeypatch.setenv("AI_NATIVE_DEPLOYMENT_SOURCE_ROOT", str(source))
    monkeypatch.setenv("AI_NATIVE_DEPLOYMENT_REGISTRY", str(tmp_path / "registry.json"))

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

    result = deploy.check_status(target, root=source)

    assert result.in_sync
    assert not result.drifts


def test_status_target_modified(tmp_path: Path) -> None:
    source, target, _deployed = deploy_to_tmp(tmp_path)
    write(target / "CLAUDE.md", "local edit\n")

    result = deploy.check_status(target, root=source)

    assert "target modified" in drift_kinds(result)
    assert any(drift.target_relative_path == "CLAUDE.md" for drift in result.drifts)


def test_status_sees_a_deployed_file_stripped_of_its_executable_bit(tmp_path: Path) -> None:
    """A deployed file is bytes and a mode. A hook chmod'd back to 0644 holds
    every byte the receipt hashes and does not run, so a check that reads only
    content calls it in sync while the target no longer runs the payload the
    receipt describes — the state `aii-2 status` is asked about when a release
    assessment asks whether a target still matches its receipt."""

    source, target, _deployed = deploy_to_tmp(tmp_path)
    script = ".claude/skills/demo-skill/scan.sh"
    (target / script).chmod(0o644)

    result = deploy.check_status(target, root=source)

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

    result = deploy.check_status(target, root=source)
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

    result = deploy.check_status(target, root=source)

    drifts = [drift for drift in result.drifts if drift.target_relative_path == script]
    assert [(drift.kind, drift.detail) for drift in drifts] == [("target modified", "content and mode differ")]


def test_status_blames_the_canonical_side_when_only_the_canonical_mode_moved(tmp_path: Path) -> None:
    """Why the expected mode comes from the receipt and not from the canonical
    item `check_status` already has in hand: with the source as the expectation,
    a canonical file made executable after a deploy reads as a target somebody
    edited, and the operator is sent to look at the wrong side."""

    source, target, _deployed = deploy_to_tmp(tmp_path)
    runbook = ".ai-protocol/workflow/runbook.md"
    (source / "canonical" / "workflow" / "runbook.md").chmod(0o755)

    result = deploy.check_status(target, root=source)

    drifts = [drift for drift in result.drifts if drift.target_relative_path == runbook]
    assert [(drift.kind, drift.detail) for drift in drifts] == [("canonical changed", "mode differs")]


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

    result = deploy.check_status(target, root=source)

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

    result = deploy.check_status(target, root=source)

    assert not [drift for drift in result.drifts if drift.target_relative_path == script]
    assert [drift.detail for drift in result.drifts if drift.target_relative_path == paths.MANIFEST_FILENAME] == [
        f"1 of {result.total_files} records state no deployed mode; redeploy so status can check modes"
    ]


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
    source = make_source(tmp_path)
    write(source / "canonical" / "repo-root" / "CLAUDE.md", CLAUDE_MD_WITH_MEMORY_IMPORTS)
    target = tmp_path / "target"
    target.mkdir()
    deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")

    target_text = (target / "CLAUDE.md").read_text(encoding="utf-8")
    target_text = target_text.replace("@.ai/design.md", "@.ai/design/index.md")
    target_text = target_text.replace("@.ai/conventions.md", "@.ai/conventions/index.md")
    write(target / "CLAUDE.md", target_text)

    result = deploy.check_status(target, root=source)

    assert result.in_sync
    assert not result.drifts


def test_status_allows_claude_md_canonical_memory_import_index_variants(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    write(source / "canonical" / "repo-root" / "CLAUDE.md", CLAUDE_MD_WITH_MEMORY_IMPORTS)
    target = tmp_path / "target"
    target.mkdir()
    deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")

    canonical_text = CLAUDE_MD_WITH_MEMORY_IMPORTS.replace("@.ai/design.md", "@.ai/design/index.md")
    write(source / "canonical" / "repo-root" / "CLAUDE.md", canonical_text)

    result = deploy.check_status(target, root=source)

    assert result.in_sync
    assert not result.drifts


def test_status_allows_claude_md_memory_import_variants_with_legacy_manifest(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    write(source / "canonical" / "repo-root" / "CLAUDE.md", CLAUDE_MD_WITH_MEMORY_IMPORTS)
    target = tmp_path / "target"
    target.mkdir()
    deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")
    saved = manifest.read_manifest(target)
    saved["files"]["CLAUDE.md"].pop(deploy.NORMALIZED_SHA256_FIELD)
    manifest.write_manifest(target, saved)

    target_text = (target / "CLAUDE.md").read_text(encoding="utf-8")
    target_text = target_text.replace("@.ai/overview.md", "@.ai/overview/index.md")
    write(target / "CLAUDE.md", target_text)

    result = deploy.check_status(target, root=source)

    assert result.in_sync
    assert not result.drifts


def test_status_rejects_claude_md_non_topic_memory_import_variant(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    write(source / "canonical" / "repo-root" / "CLAUDE.md", CLAUDE_MD_WITH_MEMORY_IMPORTS)
    target = tmp_path / "target"
    target.mkdir()
    deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")

    target_text = (target / "CLAUDE.md").read_text(encoding="utf-8")
    target_text = target_text.replace("@.ai/map.md", "@.ai/map/index.md")
    write(target / "CLAUDE.md", target_text)

    result = deploy.check_status(target, root=source)

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
    assert deploy.check_status(target, root=source).in_sync


def test_deploy_repairs_loader_left_on_pre_upgrade_path(tmp_path: Path) -> None:
    source = make_loader_source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    write_snapshot(target)
    deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")
    upgrade_to_directory_form(target, "architecture")

    stale = deploy.check_status(target, root=source)
    assert "stale eager import" in drift_kinds(stale)

    deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")

    assert loader_import(target, "architecture") == ".ai/architecture/index.md"
    assert deploy.check_status(target, root=source).in_sync


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
    result = deploy.check_status(target, root=source)

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
    result = deploy.check_status(target, root=source)

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

    result = deploy.check_status(target, root=source, user_skills_root=user_skills)

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

    assert deploy.check_status(target, root=source, user_skills_root=user_skills).in_sync


def test_status_silent_without_a_personal_skills_root(tmp_path: Path) -> None:
    source, target, _deployed = deploy_to_tmp(tmp_path)

    result = deploy.check_status(target, root=source, user_skills_root=tmp_path / "absent")

    assert result.in_sync
    assert deploy.shadowed_skill_drifts(["demo-skill"], user_skills_root=tmp_path / "absent") == ()


def test_status_silent_for_personal_directory_that_is_not_a_skill(tmp_path: Path) -> None:
    """A bare directory is not discoverable as a skill, so it shadows nothing;
    reporting it would train operators to ignore the check."""
    source, target, _deployed = deploy_to_tmp(tmp_path)
    user_skills = tmp_path / "home-skills"
    (user_skills / "demo-skill").mkdir(parents=True)

    assert deploy.check_status(target, root=source, user_skills_root=user_skills).in_sync


def test_status_cli_exits_nonzero_for_shadowed_skill(tmp_path: Path, monkeypatch, capsys) -> None:
    source, target, _deployed = deploy_to_tmp(tmp_path)
    user_skills = tmp_path / "home-skills"
    personal_skill(user_skills, "demo-skill")
    monkeypatch.setenv("AI_NATIVE_DEPLOYMENT_SOURCE_ROOT", str(source))
    monkeypatch.setenv("AI_NATIVE_DEPLOYMENT_CLAUDE_SKILLS_ROOT", str(user_skills))

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

    result = deploy.check_status(target, root=source)

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

    result = deploy.check_status(target, root=source)

    assert any(
        drift.kind == "stale eager import" and "does not exist" in drift.detail for drift in result.drifts
    )


def test_status_clean_for_target_without_snapshot(tmp_path: Path) -> None:
    source = make_loader_source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    deploy.deploy_canonical(target, root=source, registry_file=tmp_path / "registry.json")

    assert deploy.check_status(target, root=source).in_sync
    assert deploy.eager_import_drifts(target) == ()


def test_resolve_memory_entrypoint_matches_hook_fallback_order(tmp_path: Path) -> None:
    target = tmp_path / "target"
    write_snapshot(target, directory_form=("design",))

    assert deploy.resolve_memory_entrypoint(target, "design") == ".ai/design/index.md"
    assert deploy.resolve_memory_entrypoint(target, "conventions") == ".ai/conventions.md"
    # Unknown snapshot shape falls back to the single-file default.
    assert deploy.resolve_memory_entrypoint(tmp_path / "empty", "design") == ".ai/design.md"


def test_status_canonical_changed(tmp_path: Path) -> None:
    source, target, _deployed = deploy_to_tmp(tmp_path)
    write(source / "canonical" / "repo-root" / "CLAUDE.md", "canonical edit\n")

    result = deploy.check_status(target, root=source)

    assert "canonical changed" in drift_kinds(result)
    assert any(drift.target_relative_path == "CLAUDE.md" for drift in result.drifts)


def test_status_missing_target_file(tmp_path: Path) -> None:
    source, target, _deployed = deploy_to_tmp(tmp_path)
    (target / ".ai-protocol" / "protocols" / "dev.md").unlink()

    result = deploy.check_status(target, root=source)

    assert "missing target file" in drift_kinds(result)
    assert any(drift.target_relative_path == ".ai-protocol/protocols/dev.md" for drift in result.drifts)


def test_status_extra_deployed_file_when_canonical_removed(tmp_path: Path) -> None:
    source, target, _deployed = deploy_to_tmp(tmp_path)
    (source / "canonical" / "protocols" / "dev.md").unlink()

    result = deploy.check_status(target, root=source)

    assert "extra deployed file" in drift_kinds(result)
    assert any(drift.target_relative_path == ".ai-protocol/protocols/dev.md" for drift in result.drifts)


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
        ".cursor/orchestrator/.env",
        ".cursor/orchestrator/.env.local",
        ".cursor/orchestrator/logs/run.log",
        ".cursor/orchestrator/.venv/bin/python",
        ".cursor/orchestrator/__pycache__/orchestrator.pyc",
        ".claude/settings.local.json",
        ".claude/projects/local.json",
        ".codex/sessions.json",
        ".codex/.env.prod",
    ]
    for relative_path in forbidden_targets:
        assert not (target / relative_path).exists()
        assert relative_path not in deployed["files"]

    assert (target / ".cursor" / "orchestrator" / ".env.example").is_file()


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
                ".cursor/orchestrator/.venv/",
                deploy.GITIGNORE_END,
                "",
            ]
        ),
    )

    deploy.append_gitignore_block(target)

    content = (target / ".gitignore").read_text(encoding="utf-8")
    assert content.count(deploy.GITIGNORE_BEGIN) == 1
    assert "/.cursor/" in content
    assert ".cursor/orchestrator/.venv/" not in content
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
        if command == ["python3.14", "-m", "venv", str(target.resolve() / ".cursor" / "orchestrator" / ".venv")]:
            python_path = target / ".cursor" / "orchestrator" / ".venv" / "bin" / "python"
            write(python_path, "#!/bin/sh\n")

    monkeypatch.setattr(deploy.subprocess, "run", fake_run)

    result = deploy.bootstrap_orchestrator(target)

    venv_path = target.resolve() / ".cursor" / "orchestrator" / ".venv"
    python_path = venv_path / "bin" / "python"
    requirements_path = target.resolve() / ".cursor" / "orchestrator" / "requirements.txt"
    assert result.venv_path == venv_path
    assert result.python_path == python_path
    assert result.requirements_path == requirements_path
    assert result.env_created is True
    assert (target / ".cursor" / "orchestrator" / ".env").read_text(encoding="utf-8") == "TOKEN=\n"
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
    write(target / ".cursor" / "orchestrator" / ".env", "CURSOR_API_KEY=local\n")
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, cwd: Path, check: bool) -> None:
        calls.append(command)
        if command[:3] == ["python3.13", "-m", "venv"]:
            write(target / ".cursor" / "orchestrator" / ".venv" / "bin" / "python", "#!/bin/sh\n")

    monkeypatch.setattr(deploy.subprocess, "run", fake_run)

    result = deploy.bootstrap_orchestrator(target, python_executable="python3.13")

    assert result.env_created is False
    assert (target / ".cursor" / "orchestrator" / ".env").read_text(encoding="utf-8") == "CURSOR_API_KEY=local\n"
    assert calls[0] == ["python3.13", "-m", "venv", str(target.resolve() / ".cursor" / "orchestrator" / ".venv")]


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
