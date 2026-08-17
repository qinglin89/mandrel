"""The task-completion closeout is triggered from four places — the claude,
cursor and codex stop hooks, and the orchestrator loop — and every one of them
has to reach the same deployed skill. A pointer that stops resolving fails
quietly: the session is told to read a file that is not there, at the one
moment the protocol depends on it, and nothing else in the run notices."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from mandrel import deploy

REPO_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = REPO_ROOT / "canonical" / "orchestrator" / "orchestrator.py"

CLOSEOUT_SKILL = "ai-sync-v2"
DEPLOYED_CLOSEOUT_SKILL = f".claude/skills/{CLOSEOUT_SKILL}/SKILL.md"

# The two backends whose hook renders a path into the block message. Claude Code
# is deliberately absent: it names the skill and lets native discovery find it.
PATH_HOOKS = (
    REPO_ROOT / "canonical" / "cursor" / "hooks" / "stop-context-check.sh",
    REPO_ROOT / "canonical" / "codex" / "hooks" / "stop-context-check.sh",
)
CLAUDE_HOOK = REPO_ROOT / "canonical" / "claude" / "hooks" / "stop-context-check.sh"

SYNC_SKILL_ASSIGNMENT = re.compile(r'^SYNC_SKILL="([^"]*)"', re.MULTILINE)
SKILL_FRONTMATTER_NAME = re.compile(r"^name:\s*(\S+)\s*$", re.MULTILINE)


def orchestrator_closeout_skill() -> str:
    """The orchestrator's close-out skill path, relative to the repo it is
    deployed into. Read out of the module itself rather than the source text so
    the `REPO` anchoring is exercised, not just the literal."""
    code = (
        "import orchestrator as o; "
        "print(o.SYNC_SKILL.relative_to(o.REPO).as_posix())"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ORCHESTRATOR.parent,
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def test_deploy_places_the_closeout_skill_where_the_hooks_look() -> None:
    """Ties the pointer to the deployment mapping: the path the hooks name is
    the path `deploy` actually writes, not a matching-looking string."""
    deployed = {item.target_relative_path for item in deploy.iter_deployment_items(REPO_ROOT)}

    assert DEPLOYED_CLOSEOUT_SKILL in deployed


def test_every_backend_points_at_the_same_deployed_closeout_skill() -> None:
    for hook in PATH_HOOKS:
        assignments = SYNC_SKILL_ASSIGNMENT.findall(hook.read_text(encoding="utf-8"))
        assert assignments == [DEPLOYED_CLOSEOUT_SKILL], f"{hook.name} points at {assignments}"

    assert orchestrator_closeout_skill() == DEPLOYED_CLOSEOUT_SKILL


def test_claude_hook_relies_on_native_discovery_of_the_declared_skill_name() -> None:
    """Claude Code resolves `/ai-sync-v2` by the skill's declared name, so the
    hook carries no path — and the frontmatter name is what makes that work."""
    text = CLAUDE_HOOK.read_text(encoding="utf-8")

    assert not SYNC_SKILL_ASSIGNMENT.findall(text)
    assert f"/{CLOSEOUT_SKILL}" in text

    skill = REPO_ROOT / "canonical" / DEPLOYED_CLOSEOUT_SKILL.replace(".claude/", "claude/", 1)
    declared = SKILL_FRONTMATTER_NAME.search(skill.read_text(encoding="utf-8"))
    assert declared and declared.group(1) == CLOSEOUT_SKILL
