from __future__ import annotations

import hashlib
import json
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
    write(canonical / "orchestrator" / "orchestrator.py", 'print("ok")\n')
    write(canonical / "orchestrator" / "orchestrator.toml",
          'schema_version = 1\n')
    write(canonical / "orchestrator" / ".env.example", "TOKEN=\n")
    write(canonical / "orchestrator" / "requirements.txt", "cursor-sdk\n")
    return root


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
