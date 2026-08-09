"""The optional pre-push installer writes into `.git/hooks`, which is neither
tracked nor backed up: a hook it overwrites is gone with no diff to notice it
in. Its one dangerous case is a hook of the user's own that calls the gate
among other commands — plausible, since the refusal message suggests exactly
that arrangement — so ownership has to be decided by content, not by whether
the file mentions `scripts/check.sh`.

These run the real script against throwaway repositories; nothing here touches
the developer's own `.git/hooks`."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "scripts" / "install-git-hooks.sh"

# A hook the user wrote: it calls the gate, and it also does things the gate
# knows nothing about.
FOREIGN_HOOK_CALLING_THE_GATE = """\
#!/usr/bin/env bash
# Mine: the shared gate, plus what this checkout needs.
set -euo pipefail
"$(git rev-parse --show-toplevel)"/scripts/check.sh
./scripts/my-license-audit.sh
npm run e2e
"""

FOREIGN_HOOK_IGNORING_THE_GATE = """\
#!/bin/sh
echo "my own pre-push"
"""


def installer_repo(tmp_path: Path) -> Path:
    """A throwaway Git repository holding a copy of the real installer.

    The script resolves the repository from its own location, so a copy under
    `<tmp>/scripts/` installs into `<tmp>/.git/hooks` — the actual script bytes
    are exercised, against a repository that is not this one."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(INSTALLER, repo / "scripts" / INSTALLER.name)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


def run_installer(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(repo / "scripts" / INSTALLER.name)],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def hook_path(repo: Path) -> Path:
    return repo / ".git" / "hooks" / "pre-push"


def write_hook(repo: Path, text: str) -> Path:
    hook = hook_path(repo)
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(text, encoding="utf-8")
    hook.chmod(0o755)
    return hook


def test_installing_into_a_fresh_repository_writes_an_executable_hook(tmp_path: Path) -> None:
    repo = installer_repo(tmp_path)

    proc = run_installer(repo)

    assert proc.returncode == 0, proc.stderr
    hook = hook_path(repo)
    assert "scripts/check.sh" in hook.read_text(encoding="utf-8")
    assert os.access(hook, os.X_OK), "an unexecutable hook is silently never run"


def test_reinstalling_over_its_own_hook_changes_nothing(tmp_path: Path) -> None:
    """Idempotence: the installer recognises the hook it wrote and leaves it
    alone, rather than depending on a rewrite happening to be identical."""
    repo = installer_repo(tmp_path)
    assert run_installer(repo).returncode == 0
    installed = hook_path(repo).read_bytes()

    proc = run_installer(repo)

    assert proc.returncode == 0, proc.stderr
    assert hook_path(repo).read_bytes() == installed
    assert os.access(hook_path(repo), os.X_OK)


def test_a_foreign_hook_that_calls_the_gate_survives(tmp_path: Path) -> None:
    """The regression this file exists for. Calling `scripts/check.sh` is not
    a claim of ownership — the rest of the hook is the user's work."""
    repo = installer_repo(tmp_path)
    hook = write_hook(repo, FOREIGN_HOOK_CALLING_THE_GATE)
    before = hook.read_bytes()

    proc = run_installer(repo)

    assert proc.returncode == 1, proc.stdout
    assert hook.read_bytes() == before
    assert "my-license-audit" in hook.read_text(encoding="utf-8")
    assert "pre-push" in proc.stderr


def test_a_foreign_hook_that_ignores_the_gate_survives(tmp_path: Path) -> None:
    repo = installer_repo(tmp_path)
    hook = write_hook(repo, FOREIGN_HOOK_IGNORING_THE_GATE)
    before = hook.read_bytes()

    proc = run_installer(repo)

    assert proc.returncode == 1, proc.stdout
    assert hook.read_bytes() == before


def test_an_edited_copy_of_the_managed_hook_survives(tmp_path: Path) -> None:
    """Carrying the installer's marker is not proof either: the file may be
    the managed hook with a line added, and that line is the user's."""
    repo = installer_repo(tmp_path)
    assert run_installer(repo).returncode == 0
    hook = hook_path(repo)
    edited = hook.read_text(encoding="utf-8").replace(
        "exec ", "./scripts/my-license-audit.sh\nexec ", 1
    )
    hook.write_text(edited, encoding="utf-8")

    proc = run_installer(repo)

    assert proc.returncode == 1, proc.stdout
    assert hook.read_text(encoding="utf-8") == edited
