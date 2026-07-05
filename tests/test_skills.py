from __future__ import annotations

import stat
from pathlib import Path

import pytest

from ai_native_deployment import cli, skills


def write(path: Path, content: str = "content\n", *, mode: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if mode is not None:
        path.chmod(mode)
    return path


def make_skill(root: Path, name: str, content: str = "skill\n") -> Path:
    skill_root = root / "skills-backup" / name
    write(
        skill_root / "SKILL.md",
        f"---\nname: {name}\ndescription: Test skill.\n---\n\n{content}",
    )
    return skill_root


def test_sync_claude_global_dry_run_does_not_write(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "claude-skills"
    make_skill(source, "alpha")

    result = skills.sync_claude_global(
        dry_run=True,
        root=source,
        target_root=target,
        skill_names=("alpha",),
    )

    assert [(change.name, change.action) for change in result.changes] == [("alpha", "add")]
    assert not (target / "alpha").exists()
    assert "would add alpha" in skills.format_sync_result(result)
    assert "not target repo deployment" in skills.format_sync_result(result)


def test_sync_claude_global_copies_updates_and_keeps_extra_skills(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "claude-skills"
    make_skill(source, "alpha", "new\n")
    beta_source = make_skill(source, "beta", "beta\n")
    write(beta_source / "scan.sh", "#!/bin/sh\n", mode=0o755)

    make_skill(tmp_path / "existing-source", "alpha", "old\n")
    write(target / "alpha" / "SKILL.md", "old\n")
    write(target / "extra" / "SKILL.md", "extra\n")

    result = skills.sync_claude_global(
        root=source,
        target_root=target,
        skill_names=("alpha", "beta"),
    )

    assert [(change.name, change.action) for change in result.changes] == [
        ("alpha", "update"),
        ("beta", "add"),
    ]
    assert "new" in (target / "alpha" / "SKILL.md").read_text(encoding="utf-8")
    assert (target / "extra" / "SKILL.md").is_file()
    assert stat.S_IMODE((target / "beta" / "scan.sh").stat().st_mode) == 0o755


def test_sync_claude_global_reports_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "claude-skills"
    make_skill(source, "alpha", "same\n")
    make_skill(tmp_path / "target-source", "alpha", "same\n")
    (target / "alpha").mkdir(parents=True)
    (target / "alpha" / "SKILL.md").write_bytes((source / "skills-backup" / "alpha" / "SKILL.md").read_bytes())

    result = skills.sync_claude_global(
        root=source,
        target_root=target,
        skill_names=("alpha",),
    )

    assert [(change.name, change.action) for change in result.changes] == [("alpha", "unchanged")]


def test_sync_claude_global_rejects_forbidden_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "claude-skills"
    skill_root = make_skill(source, "alpha")
    write(skill_root / ".env", "SECRET=1\n")

    with pytest.raises(skills.SkillsSyncError, match="forbidden file"):
        skills.sync_claude_global(root=source, target_root=target, skill_names=("alpha",))


def test_skills_cli_parser_has_temporary_sync_command() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["skills", "sync-claude-global", "--dry-run"])

    assert args.command == "skills"
    assert args.skills_command == "sync-claude-global"
    assert args.dry_run is True
