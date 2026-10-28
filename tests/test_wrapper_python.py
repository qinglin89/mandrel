"""bin/mandrel has to start on machines that have no unversioned `python`.

A stock Mac ships only `/usr/bin/python3` (too old for tomllib), Homebrew adds
only versioned and `python3` names, and a launchd service such as orch-hub sees
a minimal PATH. The wrapper therefore resolves an interpreter instead of
running `python`; these tests pin that order with stub interpreters on a PATH
the test fully controls, then prove the real CLI starts through it.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = REPO_ROOT / "bin" / "mandrel"
SH = shutil.which("sh") or "/bin/sh"


def _executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _stub_python(path: Path, *, new_enough: bool) -> Path:
    """Answers the wrapper's version probe, then reports how it was run."""
    probe_exit = 0 if new_enough else 1
    return _executable(
        path,
        "#!/bin/sh\n"
        f'if [ "$1" = "-I" ]; then exit {probe_exit}; fi\n'
        f'printf "ran %s %s\\n" "{path.name}" "$*"\n',
    )


def _checkout(tmp_path: Path) -> Path:
    """A copy of the wrapper whose REPO_ROOT (for .venv) the test owns."""
    root = tmp_path / "mandrel"
    wrapper = root / "bin" / "mandrel"
    wrapper.parent.mkdir(parents=True)
    shutil.copy2(WRAPPER, wrapper)
    return root


def _tools(tmp_path: Path) -> Path:
    """The only external command the wrapper needs besides an interpreter."""
    tools = tmp_path / "tools"
    if not tools.is_dir():
        tools.mkdir()
        dirname = shutil.which("dirname")
        assert dirname, "dirname is required to run the wrapper"
        (tools / "dirname").symlink_to(dirname)
    return tools


def _run(root: Path, path_dirs: list[Path], *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    environment = {"PATH": os.pathsep.join(str(d) for d in path_dirs), "HOME": str(root)}
    environment.update(env)
    return subprocess.run(
        [SH, str(root / "bin" / "mandrel"), *args],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_old_python3_and_no_python_fails_with_an_actionable_line(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    stubs = tmp_path / "stubs"
    _stub_python(stubs / "python3", new_enough=False)

    result = _run(root, [_tools(tmp_path), stubs], "registry", "list", "--json")

    assert result.returncode == 1
    assert result.stdout == ""
    assert "needs Python 3.11 or newer" in result.stderr
    assert "MANDREL_PYTHON" in result.stderr
    assert "Traceback" not in result.stderr


def test_versioned_name_wins_without_an_unversioned_python(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    stubs = tmp_path / "stubs"
    _stub_python(stubs / "python3", new_enough=False)
    _stub_python(stubs / "python3.12", new_enough=True)
    _stub_python(stubs / "python3.13", new_enough=True)

    result = _run(root, [_tools(tmp_path), stubs], "registry", "list", "--json")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "ran python3.13 -m mandrel.cli registry list --json\n"


def test_unversioned_names_are_used_only_when_new_enough(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    stubs = tmp_path / "stubs"
    _stub_python(stubs / "python3", new_enough=False)
    _stub_python(stubs / "python", new_enough=True)

    result = _run(root, [_tools(tmp_path), stubs], "status", "--all")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "ran python -m mandrel.cli status --all\n"


def test_a_versioned_shim_that_does_not_run_is_skipped(tmp_path: Path) -> None:
    """pyenv/asdf shims exist for inactive versions and fail when run."""
    root = _checkout(tmp_path)
    stubs = tmp_path / "stubs"
    _stub_python(stubs / "python3.14", new_enough=False)
    _stub_python(stubs / "python3.12", new_enough=True)

    result = _run(root, [_tools(tmp_path), stubs], "status", "--all")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "ran python3.12 -m mandrel.cli status --all\n"


def test_checkout_venv_beats_path_and_an_old_venv_is_skipped(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    stubs = tmp_path / "stubs"
    _stub_python(stubs / "python3.13", new_enough=True)
    venv_python = _stub_python(root / ".venv" / "bin" / "python", new_enough=True)

    result = _run(root, [_tools(tmp_path), stubs], "status", "--all")
    assert result.stdout == "ran python -m mandrel.cli status --all\n"

    _stub_python(venv_python, new_enough=False)
    result = _run(root, [_tools(tmp_path), stubs], "status", "--all")
    assert result.stdout == "ran python3.13 -m mandrel.cli status --all\n"


def test_mandrel_python_is_honoured_and_never_silently_replaced(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    stubs = tmp_path / "stubs"
    tools = _tools(tmp_path)
    _stub_python(stubs / "python3.13", new_enough=True)
    chosen = _stub_python(tmp_path / "elsewhere" / "py", new_enough=True)

    result = _run(root, [tools, stubs], "status", "--all", MANDREL_PYTHON=str(chosen))
    assert result.stdout == "ran py -m mandrel.cli status --all\n"

    _stub_python(chosen, new_enough=False)
    result = _run(root, [tools, stubs], "status", "--all", MANDREL_PYTHON=str(chosen))
    assert result.returncode == 1
    assert result.stdout == ""
    assert f"MANDREL_PYTHON={chosen} is not a runnable Python 3.11 or newer" in result.stderr


def test_real_cli_starts_through_a_versioned_name_only(tmp_path: Path) -> None:
    """End to end: the shipped wrapper, the real package, no `python` on PATH."""
    interpreters = tmp_path / "interpreters"
    interpreters.mkdir()
    (interpreters / f"python3.{sys.version_info.minor}").symlink_to(sys.executable)

    result = subprocess.run(
        [SH, str(WRAPPER), "registry", "list", "--json"],
        env={
            "PATH": os.pathsep.join([str(_tools(tmp_path)), str(interpreters)]),
            "HOME": str(tmp_path),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert isinstance(json.loads(result.stdout), list)
