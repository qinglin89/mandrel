"""The onboarding documents restate three sets whose authority lives elsewhere:
the initialization exclusion list, the `mandrel status` vocabulary, and the
runbook's turn-selection order. Tutorial prose is the surface least likely to be
re-derived when one of those sources moves, so the drift is silent by default.

The restatements live in the reference layer (docs/lifecycle-annotated.md).
The worked examples that used to sit beside them are now their own documents
under docs/walkthroughs/, so the classification list has exactly one home and
both walkthroughs point at it; the short path links to the reference instead of
repeating it, and that link is what this module holds it to.

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

from mandrel import deploy, paths, placement

REPO_ROOT = Path(__file__).resolve().parents[1]

INIT_CONTRACT = REPO_ROOT / "canonical" / "meta" / "init.md"
INIT_SKILL = REPO_ROOT / "canonical" / "claude" / "skills" / "ai-init" / "SKILL.md"
RUNBOOK = REPO_ROOT / "canonical" / "workflow" / "runbook.md"
REPO_ROOT_BUCKET = REPO_ROOT / "canonical" / "repo-root"

TUTORIAL = REPO_ROOT / "docs" / "getting-started.md"
LIFECYCLE = REPO_ROOT / "docs" / "lifecycle-annotated.md"
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


def _annotated_exclusions() -> set[str]:
    span = re.search(r"deploy owns: (.*?)That list covers", _read(LIFECYCLE), re.DOTALL)
    assert span, f"{LIFECYCLE.name} no longer enumerates the excluded payload"
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


def test_init_exclusions_agree_across_contract_skill_and_reference() -> None:
    contract = _contract_exclusions()
    assert _skill_exclusions() == contract, (
        "the ai-init skill and the init contract disagree on the "
        "target-project surface; a session following one would classify a "
        "repository differently from a session following the other"
    )
    assert _annotated_exclusions() == contract, (
        "docs/lifecycle-annotated.md describes a different exclusion list from "
        f"{INIT_CONTRACT.name}, so the greenfield/brownfield claim both "
        "walkthroughs route to is not what the binding procedure actually does"
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


def test_operations_lists_the_whole_status_vocabulary() -> None:
    """The operator reference presents the vocabulary as an exhaustive set;
    the README links it instead of duplicating that reference material."""
    vocabulary = (deploy.STATUS_IN_SYNC, *deploy.STATUS_DRIFT_KINDS)
    body = _read(OPERATIONS)
    missing = [value for value in vocabulary if f"`{value}`" not in body]
    assert not missing, (
        f"{OPERATIONS.relative_to(REPO_ROOT)} presents the status vocabulary "
        f"but omits {missing}; a reader treats the list as exhaustive"
    )
    assert "docs/operations.md" in _read(README), (
        "README removes status reference detail without linking the operator "
        "reference that owns it"
    )


def _operations_section(heading: str) -> str:
    """One `docs/operations.md` subsection, up to the next heading of any depth."""

    body = _read(OPERATIONS)
    assert heading in body, f"{OPERATIONS.name} has no {heading!r} section"
    rest = body[body.index(heading) + len(heading) :]
    following = re.search(r"^#{2,6} ", rest, re.MULTILINE)
    return rest[: following.start()] if following else rest


def test_operations_labels_every_status_kind_with_the_origin_the_payload_publishes() -> None:
    """`origin` is what a consumer routes on, and the document is where a
    consumer learns what each value means. A kind labelled one way in the payload
    and another in the reference is worse than an unlabelled one: the reader acts
    on the reference and the scheduler acts on the payload."""

    documented: dict[str, str] = {}
    for origin, _meaning, kinds in re.findall(
        r"^\| `(\w+)` \| (.*?) \| (.*?) \|$", _operations_section("#### Origin"), re.MULTILINE
    ):
        for kind in BACKTICKED.findall(kinds):
            documented[kind] = origin
    assert documented == deploy.DRIFT_ORIGINS, (
        f"{OPERATIONS.relative_to(REPO_ROOT)} and deploy.DRIFT_ORIGINS disagree "
        "about which origin a status kind carries"
    )


def test_operations_lists_every_reason_a_deployment_places_nothing() -> None:
    """One kind and four next steps. A reason the payload emits and the document
    does not name is one a consumer has no route to act on."""

    documented = set(
        re.findall(r"^\| `([\w-]+)` \|", _operations_section("#### `placement.reason`"), re.MULTILINE)
    )
    assert documented == set(deploy.UNPLACEABLE_REASONS), (
        f"{OPERATIONS.relative_to(REPO_ROOT)} presents the unplaceable reasons "
        "as an exhaustive set and disagrees with deploy.UNPLACEABLE_REASONS"
    )


def test_operations_names_both_lines_a_target_can_be_placed_on() -> None:
    """`placed_line` is the payload's positive answer, and the document is where
    a consumer learns what its values mean. A line the surface can publish and
    the reference does not name is one a reader treats as an unexpected value."""

    documented = set(
        re.findall(r"^\| `([\w-]+)` \|", _operations_section("#### `placed_line`"), re.MULTILINE)
    )
    assert documented == {placement.LINE_BASE, placement.LINE_CANDIDATE}, (
        f"{OPERATIONS.relative_to(REPO_ROOT)} presents the lines a target can stand on "
        "as an exhaustive set and disagrees with placement's own names for them"
    )


def test_operations_shows_the_payload_version_it_documents() -> None:
    """The example is what a consumer reads the shape off. Shown at a version
    other than the one the surface emits, it describes a payload nobody gets —
    and the version is the promise of the key set, so the two move together."""

    shown = re.findall(r'^  "schema_version": (\d+),$', _operations_section("### The machine shape"), re.MULTILINE)
    assert shown == [str(deploy.STATUS_SCHEMA_VERSION)], (
        f"{OPERATIONS.relative_to(REPO_ROOT)} shows schema_version {shown} where "
        f"the payload emits {deploy.STATUS_SCHEMA_VERSION}"
    )


def test_tutorial_routes_to_the_full_status_vocabulary() -> None:
    """The first-task path names one drift state and stops. That is only safe
    while it sends the reader to the document holding the whole set."""
    body = _read(TUTORIAL)
    listed = [
        value
        for value in (deploy.STATUS_IN_SYNC, *deploy.STATUS_DRIFT_KINDS)
        if f"`{value}`" in body
    ]
    assert "operations.md#status" in body, (
        f"{TUTORIAL.name} names {listed} without linking operations.md#status, "
        "so a beginner has no route to the states it does not name"
    )


def test_annotated_turn_selection_matches_the_runbook_order() -> None:
    runbook = _read(RUNBOOK)
    order = re.search(
        r"^## 2\. Turn selection\n(.*?)^## ", runbook, re.DOTALL | re.MULTILINE
    )
    assert order, "the runbook has no Turn selection section"
    rules = re.findall(r"^(\d+)\. (.*)$", order.group(1), re.MULTILINE)
    prefixes = (
        "`status: completed` without a Split transfer",
        "`status: blocked`",
        "A recorded `## Split`",
        "A finished review boundary",
        "Any dev session-log entry",
        "`status: final_review`",
        "Otherwise",
    )
    assert [number for number, _rule in rules] == [str(n) for n in range(1, 8)]
    assert all(rule.startswith(prefix) for (_number, rule), prefix in zip(rules, prefixes)), (
        "runbook turn selection changed; update the annotated routing table "
        "alongside the authoritative order"
    )

    rows = re.findall(
        r"^\| (.*?) \| (.*?) \|$",
        re.search(
            r"^\| Task file says \| Next turn \|\n(.*?)\n\n", _read(LIFECYCLE), re.DOTALL | re.MULTILINE
        ).group(1),
        re.MULTILINE,
    )
    conditions = [condition for condition, _action in rows if condition != "---"]
    assert conditions == [
        "`status: completed` without a Split transfer",
        "`status: blocked`",
        "recorded `## Split` with `Phase: preparing` or `ready`",
        "finished review boundary, `in_progress`, latest `pass`, current at least `session-limit`",
        "a dev entry that no `review of <sid>` entry names, or an open remediation fix set",
        "`status: final_review`, all reviewed, no verdict-driven handback",
        "otherwise: `pending` / `in_progress`, or all reviewed with latest verdict `changes-requested`",
    ]

    assert not rows[0][1].lstrip().lower().startswith("nothing"), (
        "`completed` is the close-out trigger, not a terminal state with no "
        "next turn (runbook §2 rule 1)"
    )
    assert "closeout" in rows[0][1] and "ai-sync-v2" in rows[0][1], (
        "the walkthrough must route a task still sitting at `completed` into "
        "close-out; it is the close-out trigger, not a finished state"
    )
    assert all("/split-task <id>" in rows[index][1] for index in (2, 3)), (
        "both interrupted transfer recovery and the completed-review budget "
        "boundary must dispatch split before ordinary dev/review work"
    )
    assert "`in_progress`" in rules[3][1] and "`pass`" in rules[3][1], (
        "new splitting requires an interim pass with the task still in progress; "
        "final-review and failed-review work must follow ordinary routing"
    )
    assert "fix-set: open" in rows[4][1] and "/invoke dev <id>" in rows[4][1], (
        "the review-boundary budget must preserve open-fix-set remediation "
        "continuation instead of splitting or reviewing a partial fix set"
    )
