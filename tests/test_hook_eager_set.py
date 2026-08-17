"""The eager memory set is assembled three times over — by the deployment tool
when it renders CLAUDE.md, and by the cursor and codex session-start hooks at
load time. They must agree on which topics can carry a directory-form
entrypoint, or a §4 upgrade will land on some surfaces and not others."""

from __future__ import annotations

import re
from pathlib import Path

from mandrel import deploy

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS = (
    REPO_ROOT / "canonical" / "cursor" / "hooks" / "session-start.sh",
    REPO_ROOT / "canonical" / "codex" / "hooks" / "session-start.sh",
)
CLAUDE_HOOK = REPO_ROOT / "canonical" / "claude" / "hooks" / "session-start-housekeeping-check.sh"
LOADER = REPO_ROOT / "canonical" / "repo-root" / "CLAUDE.md"

ENTRYPOINT_CALL = re.compile(r'add_eager_entrypoint\s+"([^"]+)"\s+"([^"]+)"\s+"([^"]+)"')
STATIC_EAGER_ARRAY = re.compile(r"^EAGER_FILES=\((.*?)^\)", re.DOTALL | re.MULTILINE)


def hook_entrypoint_calls(hook: Path) -> list[tuple[str, str, str]]:
    return ENTRYPOINT_CALL.findall(hook.read_text(encoding="utf-8"))


def test_hooks_resolve_every_upgradeable_topic() -> None:
    for hook in HOOKS:
        calls = hook_entrypoint_calls(hook)
        topics = [label.lower() for label, _flat, _dir_index in calls]

        assert topics == list(deploy.CLAUDE_MD_MEMORY_IMPORT_TOPICS), (
            f"{hook.name} resolves {topics}, deploy resolves "
            f"{list(deploy.CLAUDE_MD_MEMORY_IMPORT_TOPICS)}"
        )
        for label, flat, dir_index in calls:
            assert (flat, dir_index) == deploy.memory_entrypoint_forms(label.lower())


def test_hooks_do_not_also_load_topics_statically() -> None:
    """A topic in the static array would load alongside the resolved entrypoint,
    silently duplicating the pre-upgrade document."""
    for hook in HOOKS:
        match = STATIC_EAGER_ARRAY.search(hook.read_text(encoding="utf-8"))
        assert match, f"{hook.name} has no EAGER_FILES array"
        static_entries = match.group(1).split()

        for topic in deploy.CLAUDE_MD_MEMORY_IMPORT_TOPICS:
            for form in deploy.memory_entrypoint_forms(topic):
                assert form not in static_entries, f"{hook.name} loads {form} statically"


def test_every_hook_resolves_through_the_router() -> None:
    for hook in (*HOOKS, CLAUDE_HOOK):
        assert "resolve_ai_router_path" in hook.read_text(encoding="utf-8"), (
            f"{hook.name} does not resolve entrypoints through .ai/index.md routing"
        )


def test_claude_hook_checks_every_upgradeable_topic() -> None:
    text = CLAUDE_HOOK.read_text(encoding="utf-8")
    match = re.search(r"^TOPICS=\(([^)]*)\)", text, re.MULTILINE)
    assert match, "claude session-start hook has no TOPICS list"

    assert match.group(1).split() == list(deploy.CLAUDE_MD_MEMORY_IMPORT_TOPICS)


def test_canonical_loader_carries_one_single_file_import_per_topic() -> None:
    """Canonical stays on the single-file form for every topic; per-target
    resolution to the directory form is the deployment's job. Two import lines
    for one topic would mean a dual-write, which resolves to a dangling
    reference on whichever form is absent."""
    imports = [line.strip() for line in LOADER.read_text(encoding="utf-8").splitlines() if line.startswith("@.ai/")]

    for topic in deploy.CLAUDE_MD_MEMORY_IMPORT_TOPICS:
        flat, dir_index = deploy.memory_entrypoint_forms(topic)
        assert imports.count(f"@{flat}") == 1, f"canonical loader must import @{flat} exactly once"
        assert f"@{dir_index}" not in imports
