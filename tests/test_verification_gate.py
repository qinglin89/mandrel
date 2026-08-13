"""The repository has exactly one verification entrypoint, scripts/check.sh,
and two of the suites it runs are invisible to pytest: the orchestrator's
mock-loop scenarios live outside `testpaths`, and boundary lint is a shell
script. Nothing else notices if either drops out of the gate — the remaining
checks still go green, and the loss looks exactly like a passing run.

These are structural assertions over the gate's own source. They deliberately
do not execute it: the gate runs pytest, so a test that shelled out to it would
recurse."""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE = REPO_ROOT / "scripts" / "check.sh"
HOOK_INSTALLER = REPO_ROOT / "scripts" / "install-git-hooks.sh"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
SMOKE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "smoke.yml"
ORCH_HUB_PROBE = REPO_ROOT / "scripts" / "probe-orch-hub.py"
MOCK_LOOP = REPO_ROOT / "canonical" / "orchestrator" / "test_loop_mock.py"
PYPROJECT = REPO_ROOT / "pyproject.toml"

# The checks the gate must carry. Ids, not command lines: how a check is
# implemented is the script's business, but losing one of these is a silent
# hole in the merge gate.
REQUIRED_CHECKS = frozenset(
    {
        "whitespace",
        "ruff",
        "shellcheck",
        "boundary-lint",
        "pytest",
        "orchestrator-mock-loop",
        "package-build",
        "tree-unchanged",
    }
)

RUN_CHECK = re.compile(r'^run_check\s+(\S+)\s+"[^"]*"\s+(\S+)\s*$', re.MULTILINE)
SCENARIO_DEF = re.compile(r"^def (scenario_\d+_\w+)\(", re.MULTILINE)
SCENARIO_CALL = re.compile(r"^\s+(scenario_\d+_\w+)\(repo\)$", re.MULTILINE)
PREFLIGHT_MODULES = re.compile(r"^for module in ([^;]+); do", re.MULTILINE)
REQUIREMENT_NAME = re.compile(r"^[A-Za-z0-9_.-]+")


def code(path: Path) -> str:
    """File text with whole-line comments blanked out, line numbering intact.

    Both the gate and the workflows document what they deliberately do NOT run,
    naming those checks in prose. Asserting over the raw text would read those
    exclusion notes as the checks themselves."""
    return "".join(
        "\n" if line.lstrip().startswith("#") else line + "\n"
        for line in path.read_text(encoding="utf-8").splitlines()
    )


def gate_text() -> str:
    return GATE.read_text(encoding="utf-8")


def declared_checks() -> dict[str, str]:
    """{check id: the shell function it runs}."""
    return dict(RUN_CHECK.findall(gate_text()))


def dev_extra() -> set[str]:
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    requirements = config["project"]["optional-dependencies"]["dev"]
    return {REQUIREMENT_NAME.match(item).group(0) for item in requirements}


def test_the_entrypoint_is_executable() -> None:
    """Git tracks the mode bit; a gate that lost it stops being callable from
    CI and the hooks at the same time."""
    for script in (GATE, HOOK_INSTALLER):
        assert os.access(script, os.X_OK), f"{script} is not executable"


def test_the_gate_runs_every_required_check() -> None:
    declared = declared_checks()

    assert REQUIRED_CHECKS <= set(declared), (
        f"missing from {GATE.name}: {sorted(REQUIRED_CHECKS - set(declared))}"
    )


def test_every_declared_check_resolves_to_a_function_in_the_gate() -> None:
    """`run_check` calls its third argument. A typo there fails at run time as
    'command not found' — nonzero, but reported as the check itself failing."""
    text = gate_text()

    for check_id, function in declared_checks().items():
        assert f"\n{function}() {{" in text, (
            f"check {check_id} runs {function}, which the gate does not define"
        )


def test_the_suites_pytest_cannot_see_are_run_by_the_gate() -> None:
    """The two checks this whole file exists for. `testpaths` keeps the
    mock-loop scenarios out of pytest collection, so the gate is their only
    runner; boundary lint is a shell script pytest was never going to run."""
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    testpaths = config["tool"]["pytest"]["ini_options"]["testpaths"]
    collected = {
        path
        for testpath in testpaths
        for path in (REPO_ROOT / testpath).rglob("test_*.py")
    }
    assert MOCK_LOOP not in collected, (
        "pytest now collects the mock loop; this test's premise changed"
    )

    gate = code(GATE)
    assert "canonical/orchestrator/test_loop_mock.py" in gate
    assert "scripts/boundary-lint.sh" in gate


def test_every_mock_loop_scenario_is_wired_into_its_main() -> None:
    """The scenarios are plain functions; main() calls them one by one. A new
    scenario that is defined but never called passes vacuously."""
    text = MOCK_LOOP.read_text(encoding="utf-8")
    defined = SCENARIO_DEF.findall(text)
    called = SCENARIO_CALL.findall(text)

    assert defined, "no scenarios found — the parser drifted from the file"
    assert called == defined, (
        f"defined but never called: {sorted(set(defined) - set(called))}; "
        f"called but not defined: {sorted(set(called) - set(defined))}"
    )


def test_the_preflight_requires_what_the_dev_extra_installs() -> None:
    """The gate fails closed on a missing tool and points at
    `pip install -e '.[dev]'`. That advice has to actually supply the tool."""
    modules = PREFLIGHT_MODULES.search(gate_text())
    assert modules, "preflight module loop not found in the gate"
    declared = dev_extra()

    for module in modules.group(1).split():
        assert module in declared, f"gate requires {module}; dev extra lacks it"
    assert "shellcheck-py" in declared


def test_ci_calls_the_entrypoint_and_inlines_no_check() -> None:
    """Single-sourcing: CI installs dependencies and calls the gate. A check
    copied into the workflow drifts from the local one the day it is edited."""
    text = code(CI_WORKFLOW)

    assert "scripts/check.sh" in text
    for inlined in ("pytest", "boundary-lint", "test_loop_mock", "ruff", "shellcheck"):
        assert inlined not in text, f"CI inlines the {inlined} check"


def test_ci_needs_no_secret_and_asks_for_no_extra_permission() -> None:
    text = code(CI_WORKFLOW)

    assert "secrets." not in text
    assert "permissions:\n  contents: read\n" in text
    for trigger in ("pull_request:", "workflow_dispatch:", "branches: [main]"):
        assert trigger in text, f"CI does not trigger on {trigger}"
    for version in ('"3.11"', '"3.14"'):
        assert version in text, f"CI does not cover Python {version}"


def test_the_live_probe_is_manual_and_outside_the_gate() -> None:
    """Credentialed and network-dependent, so it cannot gate a merge — but it
    still has to be reachable, or 'not in the gate' quietly becomes 'gone'."""
    assert "smoke_hooks" not in code(GATE)
    assert "smoke_hooks" not in code(CI_WORKFLOW)

    smoke = code(SMOKE_WORKFLOW)
    assert "canonical/orchestrator/smoke_hooks.py" in smoke
    assert "workflow_dispatch:" in smoke
    for automatic in ("pull_request:", "\n  push:", "schedule:"):
        assert automatic not in smoke, f"the live probe runs on {automatic.strip()}"


def test_the_orch_hub_probe_is_manual_and_outside_the_gate() -> None:
    """The feed usually lives on the operator's own machine, so this probe can
    reach it and no CI runner can. It stays runnable by hand and out of every
    automatic caller — and, like the hook probe, has to remain findable, or
    'outside the gate' quietly becomes 'deleted'."""
    assert ORCH_HUB_PROBE.is_file(), "the live orch-hub probe is gone"
    assert os.access(ORCH_HUB_PROBE, os.X_OK), "the live orch-hub probe is not executable"

    for automatic in (GATE, CI_WORKFLOW, HOOK_INSTALLER):
        assert ORCH_HUB_PROBE.name not in code(automatic), f"{automatic.name} runs the credentialed orch-hub probe"

    # Documented where an operator looks for it, since nothing executes it.
    assert ORCH_HUB_PROBE.name in (REPO_ROOT / "README.md").read_text(encoding="utf-8")


def test_the_git_hook_defers_to_the_entrypoint() -> None:
    """An optional hook that re-lists checks is a second entrypoint wearing a
    disguise."""
    text = code(HOOK_INSTALLER)

    assert "scripts/check.sh" in text
    for inlined in ("pytest", "boundary-lint", "test_loop_mock", "ruff"):
        assert inlined not in text, f"the hook installer inlines the {inlined} check"
