"""The onboarding path is four documents wide — README → getting-started →
one of the two walkthroughs, with lifecycle-annotated as the reference layer
under all of them — and most of the jumps between them are anchors into a
heading another document owns. Markdown resolves nothing, so a renamed heading
breaks the jump silently: the reader lands at the top of the right page and
never learns that the section they were sent to has moved.

getting-started.md also has a size budget. It is short on purpose — a reader
should be able to finish it before a first task, with the worked examples one
link away. Nothing but a check keeps prose from drifting back into it one
clarification at a time.

The material that moved out is checked for having exactly one home: a section
duplicated into two documents is the failure this restructure exists to undo,
and a section dropped from both is worse.

The last group holds the action/result grammar in place. A tutorial is only
scannable while a reader can tell, without reading a word, which fence they are
supposed to type and which one is the machine answering. That distinction lives
entirely in the surrounding markup, so it decays the moment one step is written
in the old prose-and-a-fence style. The markup also has to stay one action to
one consequence: the cheapest way to write a step is to stack three commands and
explain them once at the bottom, which is exactly what the grammar exists to
prevent.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

README = REPO_ROOT / "README.md"
SHORT_PATH = REPO_ROOT / "docs" / "getting-started.md"
ANNOTATED = REPO_ROOT / "docs" / "lifecycle-annotated.md"
OPERATIONS = REPO_ROOT / "docs" / "operations.md"
GREENFIELD = REPO_ROOT / "docs" / "walkthroughs" / "greenfield.md"
BROWNFIELD = REPO_ROOT / "docs" / "walkthroughs" / "brownfield.md"

DOCS = (
    README,
    REPO_ROOT / "ARCHITECTURE.md",
    REPO_ROOT / "CHARTER.md",
    *sorted((REPO_ROOT / "docs").rglob("*.md")),
)

# The three documents that carry the action/result grammar. The README is not
# one of them: it lists the path, it does not walk a reader down it.
GRAMMAR_DOCS = (SHORT_PATH, GREENFIELD, BROWNFIELD)

# Inline links only; reference-style definitions and bare URLs are not jumps a
# heading rename can break.
LINK = re.compile(r"\[[^\]]*\]\((?!https?://|mailto:)([^)\s]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$", re.MULTILINE)
FENCE = re.compile(r"^```(\w*)\s*$")

ACTION = "> **Your action"
EXPECTED = re.compile(r"^\*\*Expected (terminal|agent) output\*\*$")
# What an action is allowed to reach: literal output, or the state it left
# behind. `**What Mandrel did`, a heading and a `⚠` warning are none of those.
OUTCOME = re.compile(r"^\*\*(?:Expected (?:terminal|agent) output|Result)")
# `/ai-init`, `/invoke dev <id>` — but not `/Users/you/src/repo: in sync`, which
# is a path a command printed.
SLASH_COMMAND = re.compile(r"^/[a-z][a-z0-9-]*(\s|$)")

# The short path exists to be readable in one sitting. MAX_WORDS is the reading
# budget and the binding one. MAX_LINES is deliberately looser than the words
# imply: the action/result grammar spends a blockquote, a fence and two blank
# lines on every step, so the same prose occupies more lines here than it would
# as running text, and squeezing the line count would mean deleting steps rather
# than tightening writing.
MAX_LINES = 450
MAX_WORDS = 2500

# The README is a landing page: explain the product and carry one complete path,
# then route tutorial and reference detail to the documents that own it. Leave
# modest headroom for real interface changes without letting clarification grow
# it back into a second getting-started guide.
README_MAX_LINES = 320
README_MAX_WORDS = 2100

# Material with exactly one home, and the marker that identifies it.
MOVED_MATERIAL = {
    "the turn-selection table": "| Task file says | Next turn |",
    "the per-tool `jq` degradation table": "| Tool | Without `jq` |",
    "the tracked-path collision recovery": "git rm -r --cached",
    "the target-project surface exclusion list": "That list covers the whole payload",
    "the greenfield worked example": "2026-03-02-crawler-core",
    "the brownfield worked example": "2026-03-09-webhook-rate-limit",
}

# Every beat the README's single newcomer workflow has to carry, since it is the
# only version of the path a reader who never opens docs/ will see.
QUICKSTART_BEATS = {
    "the clean-tree requirement": "clean working tree",
    "the dry run": "deploy --dry-run",
    "the deploy": "./bin/mandrel deploy /path/to/your-repo",
    "the receipt commit": ".ai-deploy-lock.json",
    "the Git HEAD it provides": "git rev-parse HEAD",
    "Codex hook trust": "/hooks",
    "initialization": "/ai-init",
    "intake confirmation": "waits for your",
    "the dev turn": "/invoke dev",
    "the review turn": "/invoke review",
    "automatic closeout": "stop hook",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _anchors(body: str) -> set[str]:
    """GitHub's heading slugs: lowercase, drop punctuation, spaces to hyphens.

    Each space becomes its own hyphen, so a heading containing an em dash keeps
    the doubled hyphen the surrounding spaces leave behind.
    """
    slugs = set()
    for heading in HEADING.findall(body):
        slug = re.sub(r"[`*_]", "", heading.lower())
        slug = re.sub(r"[^\w\s-]", "", slug)
        slugs.add(re.sub(r"\s", "-", slug.strip()))
    return slugs


def _section(body: str, heading: str) -> str:
    """One `## ` section's body, up to the next heading of the same level."""
    span = re.search(
        rf"^## {re.escape(heading)}\n(.*?)(?=^## )", body, re.DOTALL | re.MULTILINE
    )
    assert span, f"no `## {heading}` section"
    return span.group(1)


def _fences(body: str) -> list[tuple[int, str, list[str]]]:
    """Every fenced block as (line index of the opening fence, language, lines)."""
    blocks, opening, language, content = [], None, "", []
    for index, line in enumerate(body.splitlines()):
        match = FENCE.match(line)
        if opening is None:
            if match:
                opening, language, content = index, match.group(1), []
            continue
        if line.startswith("```"):
            blocks.append((opening, language, content))
            opening = None
            continue
        content.append(line)
    assert opening is None, "unclosed fence"
    return blocks


def _preceding_action_blockquote(lines: list[str], fence: int) -> bool:
    """Whether the fence is introduced by a `Your action` blockquote.

    The blockquote may wrap over several lines, so the whole contiguous quoted
    block immediately above the fence counts, not just its last line.
    """
    index = fence - 1
    while index >= 0 and not lines[index].strip():
        index -= 1
    quoted = []
    while index >= 0 and lines[index].startswith(">"):
        quoted.append(lines[index])
        index -= 1
    return any(line.startswith(ACTION) for line in quoted)


def _skip_blank(lines: list[str], index: int) -> int:
    while index < len(lines) and not lines[index].strip():
        index += 1
    return index


def _action_units(body: str) -> list[tuple[int, str]]:
    """Every `Your action` block paired with what the document reaches next.

    A unit is the blockquote, the fences it introduces, and then the first line
    that is neither — the one the grammar requires to be the outcome.
    """
    lines = body.splitlines()
    closing = {
        start: start + len(content) + 1 for start, _language, content in _fences(body)
    }
    fenced = {
        index for start, close in closing.items() for index in range(start, close + 1)
    }
    units = []
    for index, line in enumerate(lines):
        if index in fenced or not line.startswith(ACTION):
            continue
        cursor = index
        while cursor < len(lines) and lines[cursor].startswith(">"):
            cursor += 1
        cursor = _skip_blank(lines, cursor)
        while cursor in closing:
            cursor = _skip_blank(lines, closing[cursor] + 1)
        units.append((index, lines[cursor] if cursor < len(lines) else "end of document"))
    return units


def test_internal_doc_links_and_anchors_resolve() -> None:
    broken = []
    for doc in DOCS:
        for target in LINK.findall(_read(doc)):
            path_part, _, fragment = target.partition("#")
            destination = (doc.parent / path_part).resolve() if path_part else doc
            if not destination.exists():
                broken.append(f"{doc.name} → {target} (no such file)")
                continue
            if fragment and destination.suffix == ".md":
                if fragment not in _anchors(_read(destination)):
                    broken.append(f"{doc.name} → {target} (no such heading)")
    assert not broken, "internal documentation links point at nothing:\n" + "\n".join(broken)


def test_short_path_stays_short() -> None:
    body = _read(SHORT_PATH)
    lines, words = len(body.splitlines()), len(body.split())
    assert lines <= MAX_LINES and words <= MAX_WORDS, (
        f"{SHORT_PATH.name} is {lines} lines / {words} words, over its "
        f"{MAX_LINES}/{MAX_WORDS} budget; a worked example belongs in "
        f"{GREENFIELD.name} or {BROWNFIELD.name}, and reference detail in "
        f"{ANNOTATED.name} or {OPERATIONS.name}, none of which have a budget"
    )


def test_readme_stays_landing_page_sized() -> None:
    body = _read(README)
    lines, words = len(body.splitlines()), len(body.split())
    assert lines <= README_MAX_LINES and words <= README_MAX_WORDS, (
        f"README is {lines} lines / {words} words, over its "
        f"{README_MAX_LINES}/{README_MAX_WORDS} landing-page budget; move "
        f"tutorial detail to {SHORT_PATH.name} or a walkthrough, and operator "
        f"detail to {OPERATIONS.name}"
    )


def test_short_path_routes_to_the_material_it_no_longer_carries() -> None:
    body = _read(SHORT_PATH)
    for destination in (
        ANNOTATED.name,
        OPERATIONS.name,
        f"walkthroughs/{GREENFIELD.name}",
        f"walkthroughs/{BROWNFIELD.name}",
    ):
        assert destination in body, (
            f"{SHORT_PATH.name} no longer links {destination}; the detail it "
            "stops short of became unreachable from the reader's entry point"
        )


def test_readme_carries_one_newcomer_workflow() -> None:
    """`How you use it` and `Quickstart` used to state the same lifecycle at
    different levels of completeness, which is how they drifted apart."""
    body = _read(README)
    headings = HEADING.findall(body)
    workflow_sections = [h for h in headings if h in ("Quickstart", "How you use it")]
    assert workflow_sections == ["Quickstart"], (
        f"README states its newcomer workflow in {workflow_sections}; it gets "
        "exactly one section, or the two versions drift apart again"
    )

    quickstart = _section(body, "Quickstart")
    missing = sorted(
        description
        for description, marker in QUICKSTART_BEATS.items()
        if marker not in quickstart
    )
    assert not missing, (
        f"the README Quickstart omits {missing}; it is the only version of the "
        "path a reader who never opens docs/ will follow, so it has to run end "
        "to end"
    )


def test_readme_explains_the_product_before_the_newcomer_workflow() -> None:
    """A reader should know what Mandrel is before being asked to install it."""
    body = _read(README)
    explanation = body.find("## What it actually is")
    quickstart = body.find("## Quickstart")
    assert 0 < explanation < quickstart, (
        "README asks the reader to install Mandrel before explaining what it is"
    )


def test_readme_separates_init_modes_from_complete_walkthroughs() -> None:
    """Greenfield and brownfield change how initialization works, but their
    walkthroughs demonstrate the complete lifecycle. Linking those examples
    inside initialization falsely scopes them to that one step."""
    quickstart = _section(_read(README), "Quickstart")
    initialization = quickstart.find("### 2. Initialize")
    intake = quickstart.find("### 3. Intake a task")
    closeout = quickstart.find("### 5. Closeout")
    assert 0 <= initialization < intake < closeout, (
        "README does not keep initialization, intake, and closeout in order"
    )

    init_step = quickstart[initialization:intake]
    for marker in ("**New project:**", "**Existing codebase:**"):
        assert marker in init_step, f"README initialization omits {marker}"

    closing = quickstart[closeout:]
    for destination in (
        f"docs/walkthroughs/{GREENFIELD.name}",
        f"docs/walkthroughs/{BROWNFIELD.name}",
    ):
        assert destination not in init_step, (
            f"README scopes the complete {destination} walkthrough to initialization"
        )
        assert destination in closing, (
            f"README does not route to complete {destination} after closeout"
        )


def test_readme_presents_manual_and_unattended_as_one_execution_choice() -> None:
    """Manual invocation and the scheduler are interchangeable executors for
    one lifecycle. Separating them makes manual dispatch look mandatory and the
    unattended experience look like an unrelated advanced feature."""
    quickstart = _section(_read(README), "Quickstart")
    execution = quickstart.find("### 4. Run the task")
    closeout = quickstart.find("### 5. Closeout")
    assert 0 <= execution < closeout, "README has no bounded task-execution step"

    choice = quickstart[execution:closeout]
    unattended = choice.find("**Unattended.**")
    manual = choice.find("**Manual.**")
    assert 0 <= unattended < manual, (
        "README should lead with the one-command unattended path, then expose "
        "manual invocation for boundary-level control"
    )
    assert re.search(r"same\s+lifecycle", choice), (
        "README task-execution choice does not establish one shared lifecycle"
    )
    for marker in ("/invoke dev", "/invoke review"):
        assert marker in choice, (
            f"README task-execution choice does not establish {marker!r}"
        )


def test_readme_offers_the_short_path_and_the_reference_layer() -> None:
    body = _read(README)
    for destination in (f"docs/{SHORT_PATH.name}", f"docs/{ANNOTATED.name}"):
        assert destination in body, f"README does not link {destination}"


def test_moved_material_has_exactly_one_home() -> None:
    for description, marker in MOVED_MATERIAL.items():
        homes = [doc.name for doc in DOCS if marker in _read(doc)]
        assert len(homes) == 1, (
            f"{description} appears in {homes or 'no document'}; it belongs in "
            "exactly one, or two documents restate each other and drift apart"
        )


def test_onboarding_documents_carry_no_decorative_rules() -> None:
    """A `---` between sections is noise a heading already carries. The only
    ones left are frontmatter delimiters inside the example task files."""
    for doc in (README, SHORT_PATH, ANNOTATED, GREENFIELD, BROWNFIELD):
        body = _read(doc)
        fenced = {
            index
            for start, _language, content in _fences(body)
            for index in range(start + 1, start + 1 + len(content))
        }
        decorative = [
            index + 1
            for index, line in enumerate(body.splitlines())
            if line == "---" and index not in fenced
        ]
        assert not decorative, (
            f"{doc.name} has document-level horizontal rules at lines "
            f"{decorative}; the section headings already separate the content"
        )


def test_every_typed_fence_is_marked_as_an_action() -> None:
    """A shell command or a slash command is something the reader types. If it
    is not headed `Your action`, the page stops being skimmable: input and
    output become two identical grey boxes."""
    unmarked = []
    for doc in GRAMMAR_DOCS:
        body = _read(doc)
        lines = body.splitlines()
        for start, language, content in _fences(body):
            typed = language == "bash" or (content and SLASH_COMMAND.match(content[0]))
            if not typed:
                continue
            if not _preceding_action_blockquote(lines, start):
                unmarked.append(f"{doc.name}:{start + 1} ({content[0]!r})")
    assert not unmarked, (
        "these fences hold something the reader types, but no `Your action` "
        "blockquote introduces them:\n" + "\n".join(unmarked)
    )


def test_no_fence_mixes_input_with_commentary() -> None:
    """`mandrel deploy <target>   # what this does` is three things in one box:
    a command to copy, an explanation to read, and a shape the reader has to
    edit before running. The explanation belongs in the prose around it."""
    mixed = []
    for doc in GRAMMAR_DOCS:
        for start, language, content in _fences(_read(doc)):
            if language != "bash":
                continue
            for offset, line in enumerate(content):
                if line.lstrip().startswith("#") or re.search(r"\S\s{2,}#", line):
                    mixed.append(f"{doc.name}:{start + 2 + offset} ({line.strip()!r})")
    assert not mixed, (
        "these shell fences mix commands with explanatory comments:\n"
        + "\n".join(mixed)
    )


def test_expected_output_is_labelled_and_then_fenced_separately() -> None:
    """The label names what follows as literal output. It is only true while a
    fence actually follows it, and while that fence is not shell input."""
    wrong = []
    for doc in GRAMMAR_DOCS:
        body = _read(doc)
        lines = body.splitlines()
        openings = {start: language for start, language, _ in _fences(body)}
        for index, line in enumerate(lines):
            if not EXPECTED.match(line):
                continue
            following = next(
                (i for i in range(index + 1, min(index + 6, len(lines))) if lines[i].strip()),
                None,
            )
            if following is None or following not in openings:
                wrong.append(f"{doc.name}:{index + 1} (no fence follows the label)")
            elif openings[following] != "text":
                wrong.append(
                    f"{doc.name}:{index + 1} (labelled output in a "
                    f"`{openings[following]}` fence)"
                )
    assert not wrong, (
        "expected-output labels must be followed by their own `text` fence:\n"
        + "\n".join(wrong)
    )


def test_every_action_reaches_its_own_outcome() -> None:
    """`Your action` promises a consequence, so the consequence has to be the
    next thing on the page.

    Two actions before one shared `Result` leave the reader to work out which
    command produced it, and a warning or a `What Mandrel did` reached first
    explains a mechanism whose effect they have not been shown. The other
    grammar checks all look at what comes *before* a fence; this is the only one
    that looks at what the document does once the action is over.
    """
    dangling = []
    for doc in GRAMMAR_DOCS:
        for index, following in _action_units(_read(doc)):
            if not OUTCOME.match(following):
                dangling.append(f"{doc.name}:{index + 1} (reaches {following.strip()[:56]!r})")
    assert not dangling, (
        "these actions run on to the next action, heading, warning or "
        "explanation without saying what happened; each one needs its own "
        "`Expected ... output` or `Result` directly after it:\n" + "\n".join(dangling)
    )


def test_walkthroughs_stand_on_their_own() -> None:
    """Each is linked directly from the README, so a reader can arrive without
    having read anything else: it has to say where it starts, carry one task to
    the archive, and route back to the shared path and the rules."""
    for doc in (GREENFIELD, BROWNFIELD):
        body = _read(doc)
        assert "**Starting state.**" in body, (
            f"{doc.name} does not state the repository state it starts from"
        )
        for destination in ("../getting-started.md", "../lifecycle-annotated.md"):
            assert destination in body, (
                f"{doc.name} does not link {destination}; a reader who arrived "
                "from the README has no route back to the shared path"
            )
        for marker in ("status: completed", ".ai-tasks/archive/"):
            assert marker in body, (
                f"{doc.name} stops before {marker!r}; each walkthrough carries "
                "one concrete task all the way through closeout"
            )
