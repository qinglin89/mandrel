from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_user_skills_root(monkeypatch, tmp_path: Path) -> Path:
    """Keep the shadowed-skill check off the developer's real agent home.

    `check_status` reads the personal-level skills root by default, so without
    this the suite's verdict would depend on which skills the machine running
    it happens to have installed. Points at a path that does not exist, which
    is the "nothing shadows anything" case; tests that exercise shadowing
    create it or pass `user_skills_root=` explicitly."""
    root = tmp_path / "user-skills"
    monkeypatch.setenv("MANDREL_CLAUDE_SKILLS_ROOT", str(root))
    return root
