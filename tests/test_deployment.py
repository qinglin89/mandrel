from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

from ai_native_deployment import cli, deploy, hashing, lockfile, manifest


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
