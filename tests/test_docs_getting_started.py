"""The beginner tutorial restates three sets whose authority lives elsewhere:
the initialization exclusion list, the `mandrel status` vocabulary, and the
runbook's turn-selection order. A tutorial is the surface least likely to be
re-derived when one of those sources moves, so the drift is silent by default.

The exclusion list has an authority above the contract, too: it exists to hide
the deployed payload from mode detection, so `deploy.PAYLOADS` — not prose — is
what decides whether it is complete. `.mandrel/orchestrator/` was added to the
payload while the list still named only `.cursor/**`, which left a deployment
looking like an existing Python codebase to a literal brownfield scan.
"""

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path

from mandrel import deploy, paths

REPO_ROOT = Path(__file__).resolve().parents[1]

INIT_CONTRACT = REPO_ROOT / "canonical" / "meta" / "init.md"
INIT_SKILL = REPO_ROOT / "canonical" / "claude" / "skills" / "ai-init" / "SKILL.md"
RUNBOOK = REPO_ROOT / "canonical" / "workflow" / "runbook.md"
REPO_ROOT_BUCKET = REPO_ROOT / "canonical" / "repo-root"

TUTORIAL = REPO_ROOT / "docs" / "getting-started.md"
OPERATIONS = REPO_ROOT / "docs" / "operations.md"
README = REPO_ROOT / "README.md"

BACKTICKED = re.compile(r"`([^`]+)`")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalize_exclusion(token: str) -> str:
    """`.claude/**`, `.claude/` and `.claude` all name the same excluded tree."""
    token = token.strip().strip("`")
    if token.endswith("/**"):
        return token[:-3]
    return token.removesuffix("/")


def _contract_exclusions() -> set[str]:
    body = _read(INIT_CONTRACT)
    section = re.search(
        r"^## Target-Project Surface\n(.*?)^## ", body, re.DOTALL | re.MULTILINE
    )
    assert section, f"{INIT_CONTRACT.name} has no Target-Project Surface section"
    found = set()
    for line in section.group(1).splitlines():
        if not line.startswith("- "):
            continue
        token = BACKTICKED.search(line)
        assert token, f"exclusion bullet without a path: {line!r}"
        found.add(_normalize_exclusion(token.group(1)))
    return found


def _skill_exclusions() -> set[str]:
    for line in _read(INIT_SKILL).splitlines():
        if "First exclude deployed AI protocol/tooling paths:" in line:
            return {_normalize_exclusion(t) for t in BACKTICKED.findall(line)}
    raise AssertionError(f"{INIT_SKILL.name} states no exclusion list")


def _tutorial_exclusions() -> set[str]:
    span = re.search(r"deploy owns: (.*?)That list covers", _read(TUTORIAL), re.DOTALL)
    assert span, f"{TUTORIAL.name} no longer enumerates the excluded payload"
    return {_normalize_exclusion(t) for t in BACKTICKED.findall(span.group(1))}


def _deployed_root_level_paths() -> set[str]:
    """Every top-level target entry the deploy owns, plus its two receipts."""
    owned = set()
    for _bucket, target_prefix in deploy.PAYLOADS:
        if target_prefix:
            owned.add(target_prefix.split("/", 1)[0])
            continue
        # The root bucket lands files directly in the target; each one is its
        # own top-level entry.
        owned.update(
            source.relative_to(REPO_ROOT_BUCKET).parts[0]
            for source in REPO_ROOT_BUCKET.rglob("*")
            if source.is_file()
        )
    owned.add(paths.MANIFEST_FILENAME)
    owned.add(paths.LOCK_FILENAME)
    return owned


def _is_excluded(entry: str, exclusions: set[str]) -> bool:
    return any(entry == rule or fnmatch(entry, rule) for rule in exclusions)


def test_init_exclusions_agree_across_contract_skill_and_tutorial() -> None:
    contract = _contract_exclusions()
    assert _skill_exclusions() == contract, (
        "the ai-init skill and the init contract disagree on the "
        "target-project surface; a session following one would classify a "
        "repository differently from a session following the other"
    )
    assert _tutorial_exclusions() == contract, (
        "docs/getting-started.md describes a different exclusion list from "
        f"{INIT_CONTRACT.name}, so its greenfield/brownfield claim is not what "
        "the binding procedure actually does"
    )


def test_init_exclusions_hide_the_whole_deployed_payload() -> None:
    """Mode detection must never see the payload as target-project code."""
    exclusions = _contract_exclusions()
    unhidden = sorted(
        entry
        for entry in _deployed_root_level_paths()
        if not _is_excluded(entry, exclusions)
    )
    assert not unhidden, (
        f"the deploy writes {unhidden} into the target, but the init contract "
        "does not exclude them from the target-project surface; a literal scan "
        "reads deployed tooling as the user's own codebase"
    )


def test_status_drift_kinds_cover_every_kind_deploy_constructs() -> None:
    """`format_status` prints only the listed kinds. One missing from the tuple
    would be counted in the drift header and then never named."""
    constructed = set(re.findall(r'Drift\(\s*"([^"]+)"', _read(REPO_ROOT / "mandrel" / "deploy.py")))
    missing = sorted(constructed - set(deploy.STATUS_DRIFT_KINDS))
    assert not missing, f"deploy.py reports {missing}, which format_status never prints"
    unused = sorted(set(deploy.STATUS_DRIFT_KINDS) - constructed)
    assert not unused, f"STATUS_DRIFT_KINDS names {unused}, which nothing constructs"


def test_operator_docs_list_the_whole_status_vocabulary() -> None:
    vocabulary = (deploy.STATUS_IN_SYNC, *deploy.STATUS_DRIFT_KINDS)
    for doc in (README, OPERATIONS, TUTORIAL):
        body = _read(doc)
        missing = [value for value in vocabulary if f"`{value}`" not in body]
        assert not missing, (
            f"{doc.relative_to(REPO_ROOT)} presents the status vocabulary but "
            f"omits {missing}; a reader treats the list as exhaustive"
        )


def test_tutorial_turn_selection_matches_the_runbook_order() -> None:
    runbook = _read(RUNBOOK)
    order = re.search(
        r"^## 2\. Turn selection\n(.*?)^## ", runbook, re.DOTALL | re.MULTILINE
    )
    assert order, "the runbook has no Turn selection section"
    rules = re.findall(r"^(\d+)\. (.*)$", order.group(1), re.MULTILINE)
    assert rules[0][1].startswith("`status: completed` → **close-out**")
    assert rules[1][1].startswith("`status: blocked`")

    rows = re.findall(
        r"^\| (.*?) \| (.*?) \|$",
        re.search(
            r"^\| Task file says \| Next turn \|\n(.*?)\n\n", _read(TUTORIAL), re.DOTALL | re.MULTILINE
        ).group(1),
        re.MULTILINE,
    )
    conditions = [condition for condition, _action in rows if condition != "---"]
    assert conditions[0] == "`status: completed`"
    assert conditions[1] == "`status: blocked`"

    assert not rows[0][1].lstrip().lower().startswith("nothing"), (
        "`completed` is the close-out trigger, not a terminal state with no "
        "next turn (runbook §2 rule 1)"
    )
    assert "closeout" in rows[0][1] and "ai-sync-v2" in rows[0][1], (
        "the tutorial must route a task still sitting at `completed` into "
        "close-out; it is the close-out trigger, not a finished state"
    )
