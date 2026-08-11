"""The batch/experiment lineage: its versioned contracts, and the derivation.

Two halves, in that order.

Schema files are the contract (`schema.py`), so the first half keeps one from
claiming a rule nothing enforces: every keyword a schema uses has to be inside
the implemented validator subset, or the validator raises the first time
anything is checked against it — at write time, in an operation that has already
started. Those instances are deliberately hand-written rather than produced by
the package; nothing writes experiment records yet, and a test built from the
writer would only prove the writer agrees with itself.

The second half is `lineage.py`, which reads those records back and derives the
current batch, the open experiment, its rounds, and the candidate. What it must
never depend on is what the first half cannot express: a checked-out revision,
a local `.ai-tasks/`, or a ref namespace a clone was never going to fetch. So
the fixtures here are deliberately hostile in exactly those ways.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from evolution_fixtures import (
    REPO_ROOT,
    admitted_task,
    experiment_decision,
    experiment_round,
    git_checkout,
    git_commit,
    git_repo,
    git_rev,
    git_unrelated_commit,
    git_update_ref,
    make_repo,
    promotion_of,
    rejection,
    write_draft,
    write_experiment,
    write_manifest,
    write_outcome,
    write_rejected_drafts,
)

from ai_native_deployment import evolution
from ai_native_deployment.evolution import ledger, lineage, manifests, revisions, schema

SCHEMAS = REPO_ROOT / "evolution" / "schemas"

EXPERIMENT_ID = "evolution-batch-0007-exp-02"
BATCH_ID = "evolution-batch-0007"
BASE = "a" * 40
CANDIDATE = "b" * 40
PROMOTION = "c" * 40
DRAFT_SHA = "d" * 64


def experiment(**overrides: Any) -> dict[str, Any]:
    """An open experiment on its second round: round 1 candidate-ready with its
    tasks observed complete and its candidate pinned, round 2 open with a task
    still running."""

    record = {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "batch_id": BATCH_ID,
        "created_at": "2026-08-01T09:00:00Z",
        "base_revision": BASE,
        "base_release_ref": "v2.2.0",
        "ref": f"refs/evolution/experiments/{EXPERIMENT_ID}",
        "rounds": [
            {
                "round": 1,
                "opened_at": "2026-08-01T09:00:00Z",
                "reason": "grouped admission of the loader dispositions",
                "tasks": [
                    {
                        "task_id": "2026-08-01-loader-fallback",
                        "draft_id": "loader-fallback",
                        "draft_sha256": DRAFT_SHA,
                        "admitted_at": "2026-08-01T09:00:00Z",
                        "completion_observed_at": "2026-08-03T08:00:00Z",
                    }
                ],
                "seal": {
                    "sealed_at": "2026-08-03T09:00:00Z",
                    "candidate_revision": CANDIDATE,
                },
            },
            {
                "round": 2,
                "opened_at": "2026-08-03T09:00:00Z",
                "reason": "replay showed no convergence gain",
                "tasks": [
                    {
                        "task_id": "2026-08-03-loader-fallback-hook-side",
                        "draft_id": "loader-fallback-hook-side",
                        "draft_sha256": DRAFT_SHA,
                        "admitted_at": "2026-08-03T09:30:00Z",
                        "completion_observed_at": None,
                    }
                ],
                "seal": None,
            },
        ],
        "decision": None,
    }
    record.update(overrides)
    return record


def outcome(**overrides: Any) -> dict[str, Any]:
    record = {
        "schema_version": 1,
        "batch_id": BATCH_ID,
        "outcome": "no-change",
        "decided_at": "2026-08-09T09:00:00Z",
        "reason": "no cluster reached the minimum unique-task count",
        "experiment_id": None,
        "promotion_revision": None,
        "promotion": None,
    }
    record.update(overrides)
    return record


def rejections(**overrides: Any) -> dict[str, Any]:
    record = {
        "schema_version": 1,
        "batch_id": BATCH_ID,
        "rejected": [
            {
                "draft_id": "hook-rewrite",
                "draft_sha256": DRAFT_SHA,
                "rejected_at": "2026-08-02T09:00:00Z",
                "reason": "one report is not recurrence (invariant 1)",
            }
        ],
    }
    record.update(overrides)
    return record


def load(filename: str) -> dict[str, Any]:
    return schema.load_schema(SCHEMAS / filename)


def errors(instance: Any, filename: str) -> list[str]:
    return schema.validate(instance, load(filename))


# --- the validator subset ----------------------------------------------------


def keywords(node: Any) -> set[str]:
    """Every schema keyword used anywhere in one schema document.

    Walks only the positions that hold subschemas, so property *names* are never
    mistaken for keywords — which is the whole difficulty of checking this
    mechanically.
    """

    if not isinstance(node, dict):
        return set()
    used = set(node)
    for name in ("properties", "$defs"):
        for child in (node.get(name) or {}).values():
            used |= keywords(child)
    used |= keywords(node.get("items"))
    return used


@pytest.mark.parametrize("path", sorted(SCHEMAS.glob("*.json")), ids=lambda path: path.name)
def test_every_schema_stays_inside_the_implemented_subset(path: Path) -> None:
    unsupported = keywords(schema.load_schema(path)) - schema.SUPPORTED_KEYWORDS
    assert not unsupported, f"{path.name} uses keywords {sorted(unsupported)} that schema.py does not implement"


# --- pattern semantics -------------------------------------------------------
#
# `pattern` is an ECMA-262 regex, and Python's `re` reads several of its
# constructs as different sets. Every expectation below is ECMA-262's, taken
# from a JavaScriptCore run of the same pattern against the same string, so
# these are the cases a validator that hands the pattern straight to `re` — with
# `re.ASCII` or without it — gets wrong. Values are written as code points: as
# characters, most of them are invisible.


def matches(pattern: str, value: str) -> bool:
    return schema.validate(value, {"type": "string", "pattern": pattern}) == []


@pytest.mark.parametrize(
    ("code_point", "is_whitespace"),
    [
        (0x09, True),
        (0x0A, True),
        (0x20, True),
        (0x1C, False),
        (0x85, False),
        (0xA0, True),
        (0x1680, True),
        (0x2028, True),
        (0x200B, False),
        (0x3000, True),
        (0xFEFF, True),
    ],
    ids=[
        "tab",
        "line-feed",
        "space",
        "file-separator",
        "next-line",
        "no-break-space",
        "ogham-space-mark",
        "line-separator",
        "zero-width-space",
        "ideographic-space",
        "zero-width-no-break-space",
    ],
)
def test_the_whitespace_class_is_the_set_ecma_262_names(code_point: int, is_whitespace: bool) -> None:
    """Python's `\\s` disagrees with ECMA-262 in both directions at once: it
    matches NEXT LINE and FILE SEPARATOR, which ECMA-262 excludes, and misses
    ZWNBSP, which it includes. `re.ASCII` does not repair that — it drops
    NO-BREAK SPACE and every other Unicode space separator instead. Neither is
    the set a schema author writing `\\s` asked for."""

    assert matches(r"^\s$", chr(code_point)) is is_whitespace


@pytest.mark.parametrize(
    ("pattern", "value", "accepted"),
    [
        (r"^\d$", "5", True),
        (r"^\d$", chr(0x0661), False),
        (r"^[\d]$", chr(0x0661), False),
        (r"^\D$", chr(0x0661), True),
        (r"^\D$", "5", False),
        (r"^\w$", "a", True),
        (r"^\w$", "_", True),
        (r"^\w$", chr(0x00E1), False),
        (r"^[\w-]+$", "a-b_c", True),
        (r"^\W$", chr(0x00E1), True),
        (r"^\S$", "x", True),
        (r"^\S$", chr(0x00A0), False),
        (r"^[\s]$", chr(0x00A0), True),
    ],
    ids=[
        "digit",
        "arabic-indic-digit-is-no-digit",
        "arabic-indic-digit-is-no-digit-in-a-class",
        "arabic-indic-digit-is-a-non-digit",
        "digit-is-no-non-digit",
        "word-character",
        "underscore-is-a-word-character",
        "a-acute-is-no-word-character",
        "class-keeps-its-other-members",
        "a-acute-is-a-non-word-character",
        "non-space",
        "no-break-space-is-no-non-space",
        "class-whitespace",
    ],
)
def test_shorthand_classes_name_their_ecma_262_sets(pattern: str, value: str, accepted: bool) -> None:
    assert matches(pattern, value) is accepted


@pytest.mark.parametrize("pattern", [r"^[\D]$", r"^[\W]$", r"^[a\S]$"], ids=["digits", "word", "space"])
def test_a_negated_shorthand_inside_a_class_is_refused_not_approximated(pattern: str) -> None:
    """`re` cannot subtract one set from another, so there is no Python pattern
    that means `[\\D]`. The validator says so instead of substituting something
    close: a pattern nobody can honour is a schema defect, and this module's
    rule is that what it does not implement fails loudly rather than passing
    data nobody checked."""

    with pytest.raises(schema.SchemaError):
        matches(pattern, "x")


def test_a_word_boundary_is_the_ascii_one_ecma_262_defines() -> None:
    """`\\b` sits on the same word characters as `\\w`, so it is ASCII in
    ECMA-262 and Unicode-wide in Python: between LATIN SMALL LETTER E WITH ACUTE
    and `f` there is a boundary in one dialect and none in the other. This is
    the one construct `re.ASCII` still governs once the classes are spelled
    out — the flag is why this case holds."""

    assert matches(r"\bfoo$", chr(0x00E9) + "foo")


def test_a_dollar_that_is_not_an_anchor_stays_a_literal() -> None:
    """Rewriting `$` to `\\Z` has to tell an anchor from an ordinary character,
    or a pattern that spells `$` literally stops matching what it names."""

    assert matches(r"^[$]\$$", "$$")
    assert not matches(r"^[$]\$$", "$$\n")


def test_an_escaped_bracket_does_not_close_a_character_class() -> None:
    """The same scan decides what is inside a class, and a class it thinks ended
    early would take the next `$` for an anchor and rewrite it."""

    assert matches(r"^[\]$]{2}$", "]$")


# --- experiment record -------------------------------------------------------


def test_open_experiment_with_a_candidate_ready_and_an_open_round_validates() -> None:
    assert errors(experiment(), "experiment.schema.json") == []


@pytest.mark.parametrize(
    "decision",
    [
        {
            "outcome": "abandoned",
            "decided_at": "2026-08-05T09:00:00Z",
            "reason": "the approach needs a loader change this batch cannot justify",
            "superseded_by": None,
            "promotion_revision": None,
        },
        {
            "outcome": "superseded",
            "decided_at": "2026-08-05T09:00:00Z",
            "reason": "replaced by the hook-side approach",
            "superseded_by": "evolution-batch-0007-exp-03",
            "promotion_revision": None,
        },
        {
            "outcome": "promoted",
            "decided_at": "2026-08-05T09:00:00Z",
            "reason": "replay showed fewer remediation rounds with no regressions",
            "superseded_by": None,
            "promotion_revision": PROMOTION,
        },
    ],
    ids=["abandoned", "superseded", "promoted"],
)
def test_every_terminal_decision_validates(decision: dict[str, Any]) -> None:
    assert errors(experiment(decision=decision), "experiment.schema.json") == []


def test_a_decision_states_its_unused_fields_as_null() -> None:
    """Explicit nulls, never omission: a reader must not have to distinguish
    "no successor" from "nobody recorded one" (contract invariant 4)."""

    partial = {
        "outcome": "abandoned",
        "decided_at": "2026-08-05T09:00:00Z",
        "reason": "dropped",
    }
    assert errors(experiment(decision=partial), "experiment.schema.json")


@pytest.mark.parametrize(
    "overrides",
    [
        {"experiment_id": "evolution-batch-0007-exp"},
        {"experiment_id": "exp-02"},
        {"batch_id": "batch-0007"},
        {"base_revision": "not-a-sha"},
        {"base_revision": BASE.upper()},
        {"ref": f"refs/heads/{EXPERIMENT_ID}"},
        {"ref": "refs/evolution/experiments/../../heads/main"},
        {"rounds": []},
        {"schema_version": 2},
    ],
    ids=[
        "truncated-experiment-id",
        "experiment-id-without-batch",
        "malformed-batch-id",
        "base-revision-not-hex",
        "base-revision-uppercase",
        "ref-outside-the-namespace",
        "ref-escaping-the-namespace",
        "no-rounds",
        "unknown-schema-version",
    ],
)
def test_malformed_experiment_identity_is_refused(overrides: dict[str, Any]) -> None:
    assert errors(experiment(**overrides), "experiment.schema.json")


def test_an_unrecorded_experiment_field_is_refused() -> None:
    """`additionalProperties: false` everywhere: a lineage fact spelled slightly
    wrong must not read as a fact that was recorded."""

    assert errors(experiment(promoted=True), "experiment.schema.json")


@pytest.mark.parametrize(
    "draft_id",
    ["../../../etc/passwd", "loader/fallback", ".hidden", "loader-fallback.md", "Loader-Fallback", ""],
    ids=["traversal", "path-segment", "dot-file", "extension", "uppercase", "empty"],
)
def test_an_unsafe_draft_id_is_refused(draft_id: str) -> None:
    record = experiment()
    record["rounds"][0]["tasks"][0]["draft_id"] = draft_id
    assert errors(record, "experiment.schema.json")


def test_a_re_proposal_suffix_is_an_ordinary_draft_id() -> None:
    """Consuming a draft is final, so proposing the idea again means a new id —
    which the pattern has to keep accepting."""

    record = experiment()
    record["rounds"][0]["tasks"][0]["draft_id"] = "loader-fallback-v2"
    assert errors(record, "experiment.schema.json") == []


def test_an_admitted_task_states_the_bytes_it_was_admitted_from() -> None:
    record = experiment()
    del record["rounds"][0]["tasks"][0]["draft_sha256"]
    assert errors(record, "experiment.schema.json")


def test_an_admitted_task_states_whether_it_was_seen_through_to_completion() -> None:
    """`.ai-tasks/` is machine-local, so the observation recorded here is the
    only durable form of it. Omitting the field would make "not finished" and
    "nobody looked" the same record — and sealing turns on that difference."""

    record = experiment()
    del record["rounds"][1]["tasks"][0]["completion_observed_at"]
    assert errors(record, "experiment.schema.json")


def test_a_round_is_numbered_from_one() -> None:
    record = experiment()
    record["rounds"][0]["round"] = 0
    assert errors(record, "experiment.schema.json")


def test_a_candidate_ready_round_pins_a_revision_shaped_candidate() -> None:
    record = experiment()
    record["rounds"][0]["seal"]["candidate_revision"] = "HEAD"
    assert errors(record, "experiment.schema.json")


@pytest.mark.parametrize(
    "seal",
    [
        {"sealed_at": "2026-08-03T09:00:00Z"},
        {"candidate_revision": CANDIDATE},
        {"sealed_at": "2026-08-03T09:00:00Z", "candidate_revision": None},
        {"sealed_at": None, "candidate_revision": CANDIDATE},
    ],
    ids=["sealed-with-no-candidate", "candidate-nobody-sealed", "null-candidate", "null-seal-time"],
)
def test_a_half_pinned_round_is_not_a_representable_shape(seal: dict[str, Any]) -> None:
    """The seal is one object rather than two nullable fields precisely so that
    candidate-ready is all-or-nothing (contract invariant 16): a round that
    claims a seal without a revision, or a revision nobody sealed, is refused by
    the schema itself instead of by a controller rule that has to remember to
    look."""

    record = experiment()
    record["rounds"][0]["seal"] = seal
    assert errors(record, "experiment.schema.json")


# --- end-of-string strictness ------------------------------------------------
#
# Every identity and digest below is anchored `^...$`, and Python's `$` also
# matches just before a final newline. So each of these is a value the pattern
# was written to refuse and a naive validator accepts: a draft slug that is no
# longer one safe path segment, an experiment id that is not the id it names, a
# revision that is not a revision.


@pytest.mark.parametrize(
    "mutate",
    [
        lambda record: record.update(experiment_id=EXPERIMENT_ID + "\n"),
        lambda record: record.update(batch_id=BATCH_ID + "\n"),
        lambda record: record.update(base_revision=BASE + "\n"),
        lambda record: record.update(ref=f"refs/evolution/experiments/{EXPERIMENT_ID}\n"),
        lambda record: record["rounds"][0]["tasks"][0].update(draft_id="loader-fallback\n"),
        lambda record: record["rounds"][0]["tasks"][0].update(draft_sha256=DRAFT_SHA + "\n"),
        lambda record: record["rounds"][0]["seal"].update(candidate_revision=CANDIDATE + "\n"),
    ],
    ids=[
        "experiment-id",
        "batch-id",
        "base-revision",
        "ref",
        "draft-id",
        "draft-digest",
        "candidate-revision",
    ],
)
def test_a_trailing_newline_is_refused_by_every_experiment_pattern(mutate: Any) -> None:
    record = experiment()
    mutate(record)
    assert errors(record, "experiment.schema.json")


@pytest.mark.parametrize(
    "overrides",
    [
        {"batch_id": BATCH_ID + "\n"},
        {"experiment_id": EXPERIMENT_ID + "\n"},
        {"promotion_revision": PROMOTION + "\n"},
    ],
    ids=["batch-id", "experiment-id", "promotion-revision"],
)
def test_a_trailing_newline_is_refused_by_every_outcome_pattern(overrides: dict[str, Any]) -> None:
    assert errors(outcome(outcome="promoted", **overrides), "batch-outcome.schema.json")


@pytest.mark.parametrize(
    "overrides",
    [
        {"draft_id": "hook-rewrite\n"},
        {"draft_sha256": DRAFT_SHA + "\n"},
    ],
    ids=["draft-id", "draft-digest"],
)
def test_a_trailing_newline_is_refused_by_every_rejection_pattern(overrides: dict[str, Any]) -> None:
    record = rejections()
    record["rejected"][0].update(overrides)
    assert errors(record, "rejected-drafts.schema.json")


# --- batch outcome -----------------------------------------------------------


def test_no_change_outcome_validates_without_a_candidate() -> None:
    assert errors(outcome(), "batch-outcome.schema.json") == []


def test_promoted_outcome_names_the_experiment_the_revision_and_the_merge_unit() -> None:
    promoted = outcome(
        outcome="promoted",
        reason="replay showed fewer remediation rounds",
        experiment_id=EXPERIMENT_ID,
        promotion_revision=PROMOTION,
        promotion=promotion_of(),
    )
    assert errors(promoted, "batch-outcome.schema.json") == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"round": 0},
        {"candidate_revision": "v2.3.0"},
        {"merge_input_revision": PROMOTION + "\n"},
        {"merge_input_ref": "release"},
        {"merge_input_ref": "HEAD"},
        {"tree": "not-a-tree"},
        {"planned_targets": ["/Users/someone/checkouts/target"]},
        {"planned_targets": ["~/target"]},
        {"planned_targets": ["a/b"]},
        {"planned_targets": [""]},
    ],
    ids=[
        "round-zero",
        "tag-as-candidate",
        "trailing-newline",
        "bare-branch",
        "pseudo-ref",
        "free-form-tree",
        "absolute-path",
        "home-relative-path",
        "relative-path",
        "empty-name",
    ],
)
def test_a_merge_unit_that_names_nothing_checkable_is_refused(overrides: dict[str, Any]) -> None:
    """The merge unit is what holds a promotion revision to the evidence, and a
    planned target is a name this repository can carry — a machine-local path in
    a committed record describes a checkout the next reader does not have."""

    promoted = outcome(
        outcome="promoted",
        experiment_id=EXPERIMENT_ID,
        promotion_revision=PROMOTION,
        promotion=promotion_of(**overrides),
    )
    assert errors(promoted, "batch-outcome.schema.json")


def test_a_promoted_outcome_states_every_field_of_its_merge_unit() -> None:
    """No optional halves: a record missing the round or the tree leaves a reader
    guessing whether the value is absent or was never recorded."""

    for field in ("round", "candidate_revision", "merge_input_revision", "merge_input_ref", "tree", "planned_targets"):
        merge = promotion_of()
        del merge[field]
        promoted = outcome(
            outcome="promoted",
            experiment_id=EXPERIMENT_ID,
            promotion_revision=PROMOTION,
            promotion=merge,
        )
        assert errors(promoted, "batch-outcome.schema.json"), field


@pytest.mark.parametrize(
    "overrides",
    [
        {"outcome": "concluded"},
        {"outcome": "abandoned"},
        {"promotion_revision": "v2.3.0"},
        {"experiment_id": "some-branch"},
        {"reason": ""},
    ],
    ids=["invented-outcome", "experiment-outcome-on-a-batch", "tag-as-revision", "free-form-experiment", "empty-reason"],
)
def test_malformed_batch_outcome_is_refused(overrides: dict[str, Any]) -> None:
    assert errors(outcome(**overrides), "batch-outcome.schema.json")


def test_an_outcome_states_its_unused_fields_as_null() -> None:
    record = outcome()
    del record["experiment_id"]
    assert errors(record, "batch-outcome.schema.json")


def test_a_conclusion_written_before_the_merge_unit_existed_still_validates() -> None:
    """The merge unit arrived after this version shipped, and every record
    written without it is a `no-change` conclusion — promotion had no operation
    then. A version is bumped when a reader needs something old records cannot
    supply; requiring this one would instead stop a conclusion loading that was
    valid when it ended its batch, on content nothing may edit."""

    record = outcome()
    del record["promotion"]
    assert errors(record, "batch-outcome.schema.json") == []


# --- drafts declined at the gate ---------------------------------------------


def test_a_rejection_record_validates() -> None:
    assert errors(rejections(), "rejected-drafts.schema.json") == []


def test_a_batch_with_nothing_declined_is_representable() -> None:
    """The empty list is a real answer — "nobody has declined anything" — and it
    has to be writable before the first rejection, not only after."""

    assert errors(rejections(rejected=[]), "rejected-drafts.schema.json") == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"draft_id": "../loader-fallback"},
        {"draft_sha256": "unknown"},
        {"reason": ""},
    ],
    ids=["unsafe-draft-id", "unhashed-bytes", "unexplained-rejection"],
)
def test_a_malformed_rejection_is_refused(overrides: dict[str, Any]) -> None:
    record = rejections()
    record["rejected"][0].update(overrides)
    assert errors(record, "rejected-drafts.schema.json")


def test_a_rejection_without_the_bytes_it_declined_is_refused() -> None:
    """A later draft proposing the same thing is a different proposal; the hash
    is what distinguishes the two after the fact."""

    record = rejections()
    del record["rejected"][0]["draft_sha256"]
    assert errors(record, "rejected-drafts.schema.json")


# --- ledger vocabulary -------------------------------------------------------


def test_lineage_fields_survive_the_record_writer() -> None:
    """`build_record` keeps only the fields it knows, so a schema field missing
    from `FIELD_ORDER` is dropped silently on the way out."""

    record = ledger.build_record(
        "experiment-created",
        recorded_at="2026-08-01T09:00:00Z",
        batch_id=BATCH_ID,
        experiment_id=EXPERIMENT_ID,
        round=1,
        draft_id="loader-fallback",
        task_id="2026-08-01-loader-fallback",
        revision=BASE,
    )
    assert record["experiment_id"] == EXPERIMENT_ID
    assert record["round"] == 1
    assert record["draft_id"] == "loader-fallback"
    assert errors(record, "ledger-record.schema.json") == []


def test_the_committed_ledger_still_validates() -> None:
    """The record-type vocabulary was narrowed to the events this controller
    writes. Nothing already appended may have been invalidated by that — an
    audit line that stops validating is an audit line nobody can append after.
    """

    ledger_schema = load("ledger-record.schema.json")
    lines = (REPO_ROOT / "evolution" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(lines, start=1):
        if line.strip():
            assert schema.validate(json.loads(line), ledger_schema) == [], f"ledger.jsonl:{number}"


# --- the derivation ----------------------------------------------------------
#
# Everything below runs against a temporary repository holding this project's
# real schemas and config. `.ai-tasks/` is never created and the experiment refs
# are only created where the test is about them, because those are the two
# conditions every clone but one is in.

SECOND_BATCH = "evolution-batch-0008"
EXP_01 = f"{BATCH_ID}-exp-01"
EXP_02 = f"{BATCH_ID}-exp-02"
EXP_03 = f"{BATCH_ID}-exp-03"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return git_repo(make_repo(tmp_path), tag="v2.2.0")


@pytest.fixture
def config(repo: Path) -> evolution.EvolutionConfig:
    return evolution.load_config(repo)


@pytest.fixture
def batch(config: evolution.EvolutionConfig) -> Path:
    return write_manifest(config.batches_root, BATCH_ID, ["r1", "r2"])


def only(config: evolution.EvolutionConfig) -> lineage.BatchLineage:
    """The lineage of the one batch these fixtures create."""

    derived = lineage.describe(config)
    assert len(derived.batches) == 1
    return derived.batches[0]


def test_a_batch_with_no_experiments_is_current_and_has_no_base(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The freeze deliberately pins no base: it happens before anyone knows a
    change is warranted, so a base pinned there would be pinned to evidence
    rather than to work (invariant 15)."""

    derived = lineage.describe(config)
    assert derived.current is not None and derived.current.batch_id == BATCH_ID
    assert derived.current.experiments == ()
    assert derived.current.base_revision is None
    assert derived.current.candidate_revision is None
    assert derived.current.ref is None


def test_history_and_one_open_alternative_is_an_ordinary_state(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """A batch carrying three attempts, two of them over, is not damage: only a
    promotion ends the batch, and terminal experiments block nothing."""

    write_experiment(
        config.experiments_root,
        EXP_01,
        rounds=[experiment_round(1, tasks=[admitted_task("loader-fallback")])],
        decision=experiment_decision("abandoned"),
    )
    write_experiment(
        config.experiments_root,
        EXP_02,
        rounds=[experiment_round(1, tasks=[admitted_task("loader-fallback-v2")])],
        decision=experiment_decision("superseded", superseded_by=EXP_03),
    )
    write_experiment(
        config.experiments_root,
        EXP_03,
        rounds=[experiment_round(1, tasks=[admitted_task("hook-side-loader")], candidate_revision=CANDIDATE)],
    )

    derived = only(config)
    assert derived.current is True
    assert [experiment.experiment_id for experiment in derived.experiments] == [EXP_01, EXP_02, EXP_03]
    assert [experiment.experiment_id for experiment in derived.terminal_experiments] == [EXP_01, EXP_02]
    assert derived.open_experiment is not None and derived.open_experiment.experiment_id == EXP_03
    assert derived.candidate_revision == CANDIDATE
    assert derived.base_revision == BASE


def test_a_second_open_experiment_is_refused(config: evolution.EvolutionConfig, batch: Path) -> None:
    write_experiment(config.experiments_root, EXP_01)
    write_experiment(config.experiments_root, EXP_02, rounds=[experiment_round(1, tasks=[admitted_task("other")])])

    with pytest.raises(evolution.BatchError) as error:
        lineage.describe(config)
    assert EXP_01 in str(error.value) and EXP_02 in str(error.value)


def test_experiments_on_two_bases_are_not_alternatives(config: evolution.EvolutionConfig, batch: Path) -> None:
    """Attempts against different sources answer different questions, and no
    reading of the repository can say which one the evidence meant."""

    write_experiment(config.experiments_root, EXP_01, decision=experiment_decision("abandoned"))
    write_experiment(
        config.experiments_root,
        EXP_02,
        base_revision="e" * 40,
        rounds=[experiment_round(1, tasks=[admitted_task("other")])],
    )

    with pytest.raises(evolution.BatchError, match="more than one base revision"):
        lineage.describe(config)


def test_a_supersession_that_never_created_its_successor_is_reported_not_refused(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Superseding writes the decision first and the successor second, so this is
    what its interruption leaves — and it has to stay readable, or the operation
    that finishes it could not run either. The reader names the state; the
    guarded operations are what refuse on it."""

    write_experiment(
        config.experiments_root,
        EXP_01,
        decision=experiment_decision("superseded", superseded_by=EXP_02),
    )

    derived = only(config)
    assert derived.pending_successor == EXP_02
    assert derived.open_experiment is None
    assert [experiment.experiment_id for experiment in derived.terminal_experiments] == [EXP_01]


def test_a_successor_that_exists_is_owed_by_nobody(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The completed supersession, for contrast: the successor is the newest
    experiment, so nothing is pending."""

    write_experiment(
        config.experiments_root,
        EXP_01,
        decision=experiment_decision("superseded", superseded_by=EXP_02),
    )
    write_experiment(
        config.experiments_root,
        EXP_02,
        rounds=[experiment_round(1, tasks=[admitted_task("hook-side-loader")])],
    )

    assert only(config).pending_successor is None


@pytest.mark.parametrize("successor", [EXP_01, EXP_03], ids=["itself", "past-the-next-one"])
def test_a_superseded_decision_names_the_successor_it_created(
    config: evolution.EvolutionConfig, batch: Path, successor: str
) -> None:
    """The replacement is allocated one past the attempt it replaces, because
    ending one and creating the other is a single operation. Any other name is a
    replacement this controller could not have made, and leaves the reason the
    attempt ended pointing at another experiment's evidence."""

    write_experiment(
        config.experiments_root,
        EXP_01,
        rounds=[experiment_round(1, tasks=[admitted_task("loader-fallback")])],
        decision=experiment_decision("superseded", superseded_by=successor),
    )
    write_experiment(
        config.experiments_root,
        EXP_02,
        rounds=[experiment_round(1, tasks=[admitted_task("hook-side-loader")])],
        decision=experiment_decision("abandoned"),
    )
    write_experiment(
        config.experiments_root,
        EXP_03,
        rounds=[experiment_round(1, tasks=[admitted_task("prompt-budget")])],
    )
    with pytest.raises(evolution.BatchError, match="next experiment in the series"):
        lineage.describe(config)


def test_the_lineage_derives_with_no_local_task_pool(config: evolution.EvolutionConfig, batch: Path) -> None:
    """The acceptance this whole model exists for: `.ai-tasks/` is machine-local
    and close-out archives finished tasks away, so the record has to name its own
    task selections."""

    write_experiment(
        config.experiments_root,
        EXP_01,
        rounds=[
            experiment_round(1, tasks=[admitted_task("loader-fallback"), admitted_task("hook-side-loader")]),
        ],
    )
    assert not (config.repo_root / ".ai-tasks").exists()

    open_experiment = only(config).open_experiment
    assert open_experiment is not None
    assert [task.task_id for task in open_experiment.admitted_tasks] == [
        "2026-08-01-loader-fallback",
        "2026-08-01-hook-side-loader",
    ]


# --- experiment identity -----------------------------------------------------


@pytest.mark.parametrize("ordinal", [1, 2, 42, 100])
def test_an_experiment_id_round_trips_through_its_two_spellings(ordinal: int) -> None:
    """The formatter and the parser sit together for the reason `format_batch_id`
    and `batch_id_number` do: an id spelled two ways in two modules drifts."""

    experiment_id = lineage.format_experiment_id(BATCH_ID, ordinal)
    assert lineage.experiment_ordinal(experiment_id) == ordinal
    assert lineage.experiment_batch_id(experiment_id) == BATCH_ID
    pattern = load("experiment.schema.json")["properties"]["experiment_id"]["pattern"]
    assert schema.validate(experiment_id, {"type": "string", "pattern": pattern}) == []


def test_a_directory_that_is_not_an_experiment_identifier_is_refused(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Skipping it would let an allocation reuse an ordinal a record claims."""

    (config.experiments_root / "scratch").mkdir(parents=True)
    with pytest.raises(evolution.BatchError, match="not an experiment identifier"):
        lineage.describe(config)


def test_the_layout_note_beside_the_experiments_is_not_one(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """One experiment is one directory; the tree ships with a README."""

    config.experiments_root.mkdir(parents=True, exist_ok=True)
    (config.experiments_root / "README.md").write_text("# Evolution experiments\n", encoding="utf-8")
    assert only(config).experiments == ()


def test_an_experiment_directory_without_its_record_is_refused(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    (config.experiments_root / EXP_01).mkdir(parents=True)
    with pytest.raises(evolution.BatchError, match="has no experiment.json"):
        lineage.describe(config)


def test_a_record_naming_another_experiment_is_refused(config: evolution.EvolutionConfig, batch: Path) -> None:
    directory = write_experiment(config.experiments_root, EXP_01)
    directory.rename(config.experiments_root / EXP_02)
    with pytest.raises(evolution.BatchError, match="directory is its identity"):
        lineage.describe(config)


def test_an_experiment_id_outside_its_own_batch_is_refused(config: evolution.EvolutionConfig, batch: Path) -> None:
    write_manifest(config.batches_root, SECOND_BATCH, ["r3"])
    write_experiment(config.experiments_root, EXP_01, batch_id=SECOND_BATCH)
    with pytest.raises(evolution.BatchError, match="does not belong to batch"):
        lineage.describe(config)


def test_an_experiment_of_a_batch_that_does_not_exist_is_refused(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Either a cohort somebody deleted or an id that was mistyped — both are
    histories this controller must not quietly stop showing."""

    write_experiment(config.experiments_root, f"{SECOND_BATCH}-exp-01")
    with pytest.raises(evolution.BatchError, match="no frozen manifest"):
        lineage.describe(config)


def test_a_record_claiming_another_experiments_ref_is_refused(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """One experiment, one durable ref: a shared one would let two histories
    fast-forward over each other."""

    write_experiment(config.experiments_root, EXP_01, ref=f"refs/evolution/experiments/{EXP_02}")
    with pytest.raises(evolution.BatchError, match="one experiment, one durable ref"):
        lineage.describe(config)


@pytest.mark.parametrize(
    "ordinal",
    ["00", "000", "001", "0", "1"],
    ids=["zero", "padded-zero", "a-second-spelling-of-one", "unpadded-zero", "unpadded-one"],
)
def test_an_ordinal_has_exactly_one_spelling(
    config: evolution.EvolutionConfig, batch: Path, ordinal: str
) -> None:
    """`exp-00` names no attempt, and `exp-01` and `exp-001` are one position in
    the series with two directories — which an allocation counting one past the
    highest would hand out twice.

    Refused at the directory, before the record is read: an id this build cannot
    account for might be an experiment a later one writes, and skipping it is how
    an ordinal already claimed gets reused.
    """

    write_experiment(config.experiments_root, f"{BATCH_ID}-exp-{ordinal}")
    with pytest.raises(evolution.BatchError, match="not an experiment identifier"):
        lineage.describe(config)


def test_the_schema_and_the_reader_agree_on_which_ids_exist() -> None:
    """Two spellings of one rule, in two dialects. The reader gates directories
    and the schema gates records, so an id either of them accepts alone reaches
    the other as a state it has no reading for."""

    pattern = load("experiment.schema.json")["properties"]["experiment_id"]["pattern"]
    for ordinal in ("00", "000", "001", "0", "1", "0a"):
        candidate = f"{BATCH_ID}-exp-{ordinal}"
        assert lineage.experiment_ordinal(candidate) is None
        assert schema.validate(candidate, {"type": "string", "pattern": pattern}) != []


def test_experiment_ordinals_run_from_one_with_none_missing(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """An id is allocated one past the highest the batch ever used, so a gap is
    an attempt whose record is missing — its base, its task selections, and its
    candidates gone with it — rather than one that never existed."""

    write_experiment(
        config.experiments_root,
        EXP_01,
        rounds=[experiment_round(1, tasks=[admitted_task("loader-fallback")])],
        decision=experiment_decision("abandoned"),
    )
    write_experiment(
        config.experiments_root,
        EXP_03,
        rounds=[experiment_round(1, tasks=[admitted_task("hook-side-loader")])],
    )
    with pytest.raises(evolution.BatchError, match=r"experiment ordinals \[1, 3\]"):
        lineage.describe(config)


def test_only_the_newest_experiment_may_be_open(config: evolution.EvolutionConfig, batch: Path) -> None:
    """A history that could not have happened: the later experiment could only
    have been created once the earlier one ended (invariant 14). Left standing,
    the derivation reports the earlier attempt as current while the evidence
    being collected belongs to the later one."""

    write_experiment(
        config.experiments_root,
        EXP_01,
        rounds=[experiment_round(1, tasks=[admitted_task("loader-fallback")])],
    )
    write_experiment(
        config.experiments_root,
        EXP_02,
        rounds=[experiment_round(1, tasks=[admitted_task("hook-side-loader")])],
        decision=experiment_decision("abandoned"),
    )
    with pytest.raises(evolution.BatchError, match="the open one is the newest"):
        lineage.describe(config)


# --- rounds ------------------------------------------------------------------


@pytest.mark.parametrize(
    "rounds",
    [
        [experiment_round(2, candidate_revision=CANDIDATE)],
        [
            experiment_round(1, candidate_revision=CANDIDATE),
            experiment_round(3, tasks=[admitted_task("other")]),
        ],
        [
            experiment_round(1, candidate_revision=CANDIDATE),
            experiment_round(1, tasks=[admitted_task("other")]),
        ],
    ],
    ids=["starts-at-two", "gap", "repeated-number"],
)
def test_rounds_are_appended_one_at_a_time_from_one(
    config: evolution.EvolutionConfig, batch: Path, rounds: list[dict]
) -> None:
    """A gap takes a round's task selection and its candidate out of the history
    (invariant 15: rounds only add)."""

    write_experiment(config.experiments_root, EXP_01, rounds=rounds)
    with pytest.raises(evolution.BatchError, match="numbered"):
        lineage.describe(config)


def test_work_never_resumes_under_a_round_something_already_measured(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    write_experiment(
        config.experiments_root,
        EXP_01,
        rounds=[
            experiment_round(1),
            experiment_round(2, tasks=[admitted_task("other")], candidate_revision=CANDIDATE),
        ],
    )
    with pytest.raises(evolution.BatchError, match="carry no seal while later rounds exist"):
        lineage.describe(config)


def test_a_sealed_round_whose_task_was_never_observed_complete_is_refused(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """A candidate that does not contain the change it was admitted for is not
    what anyone means to measure (invariant 16)."""

    write_experiment(
        config.experiments_root,
        EXP_01,
        rounds=[
            experiment_round(
                1,
                tasks=[admitted_task("loader-fallback", complete=False)],
                candidate_revision=CANDIDATE,
            )
        ],
    )
    with pytest.raises(evolution.BatchError, match="no completion observation"):
        lineage.describe(config)


def test_a_sealed_round_that_admitted_nothing_is_refused(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """An open round admitting nothing is the ordinary state right after a
    revision. Sealing one is where it stops being harmless: the pin is a
    candidate evidence names and a promotion could carry, so a round with no
    admission behind it is canonical work that passed no gate."""

    write_experiment(
        config.experiments_root,
        EXP_01,
        rounds=[experiment_round(1, tasks=[], candidate_revision=CANDIDATE)],
    )
    with pytest.raises(evolution.BatchError, match="pins a candidate but admitted nothing"):
        lineage.describe(config)


def test_an_open_round_that_admitted_nothing_is_the_state_a_revision_leaves(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The other half of the same rule: work resumes when the round opens, not
    when the drafts answering the revision happen to be written."""

    write_experiment(
        config.experiments_root,
        EXP_01,
        rounds=[experiment_round(1, candidate_revision=CANDIDATE), experiment_round(2, tasks=[])],
    )

    experiment = only(config).open_experiment
    assert experiment is not None
    assert experiment.open_round is not None and experiment.open_round.number == 2
    assert experiment.open_round.tasks == ()
    assert experiment.pinned_revision == CANDIDATE


def test_an_open_round_has_nothing_pinned_to_measure(config: evolution.EvolutionConfig, batch: Path) -> None:
    """Revising makes the previous round's evidence stale by construction: the
    new round has no candidate, and the old evidence goes on naming the round it
    actually measured."""

    write_experiment(
        config.experiments_root,
        EXP_01,
        rounds=[
            experiment_round(1, candidate_revision=CANDIDATE),
            experiment_round(2, tasks=[admitted_task("hook-side-loader", complete=False)]),
        ],
    )

    experiment = only(config).open_experiment
    assert experiment is not None
    assert experiment.candidate_revision is None
    assert experiment.pinned_revision == CANDIDATE
    assert experiment.open_round is not None and experiment.open_round.number == 2
    assert experiment.open_round.unfinished == ("2026-08-01-hook-side-loader",)


def test_a_sealed_last_round_leaves_no_open_round_and_the_experiment_open(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Through replay the experiment has no open round at all, and stays
    non-terminal because nothing has decided it."""

    write_experiment(config.experiments_root, EXP_01, rounds=[experiment_round(1, candidate_revision=CANDIDATE)])

    experiment = only(config).open_experiment
    assert experiment is not None
    assert experiment.open is True
    assert experiment.open_round is None
    assert experiment.candidate_revision == CANDIDATE


def test_a_draft_is_never_readmitted_by_the_same_experiment(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    write_experiment(
        config.experiments_root,
        EXP_01,
        rounds=[
            experiment_round(1, candidate_revision=CANDIDATE),
            experiment_round(2, tasks=[admitted_task("loader-fallback", task_id="2026-08-04-again")]),
        ],
    )
    with pytest.raises(evolution.BatchError, match="admitted more than once"):
        lineage.describe(config)


def test_a_draft_is_never_readmitted_by_another_experiment(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    write_experiment(config.experiments_root, EXP_01, decision=experiment_decision("abandoned"))
    write_experiment(config.experiments_root, EXP_02)
    with pytest.raises(evolution.BatchError, match="consumed once"):
        lineage.describe(config)


@pytest.mark.parametrize(
    "where",
    ["same-round", "later-round", "later-experiment"],
)
def test_one_task_answers_for_one_admission(
    config: evolution.EvolutionConfig, batch: Path, where: str
) -> None:
    """Two drafts, one task id. Admission copies each draft to the task its
    record names, so this is two proposals with a single file between them: the
    record can say neither which bytes that task implemented nor whose
    completion its one observation is — and that observation is what seals the
    round."""

    shared = "2026-08-01-loader-fallback"
    first = admitted_task("loader-fallback", task_id=shared)
    second = admitted_task("hook-side-loader", task_id=shared)
    if where == "same-round":
        write_experiment(config.experiments_root, EXP_01, rounds=[experiment_round(1, tasks=[first, second])])
    elif where == "later-round":
        write_experiment(
            config.experiments_root,
            EXP_01,
            rounds=[
                experiment_round(1, tasks=[first], candidate_revision=CANDIDATE),
                experiment_round(2, tasks=[second]),
            ],
        )
    else:
        write_experiment(
            config.experiments_root,
            EXP_01,
            rounds=[experiment_round(1, tasks=[first])],
            decision=experiment_decision("abandoned"),
        )
        write_experiment(config.experiments_root, EXP_02, rounds=[experiment_round(1, tasks=[second])])

    with pytest.raises(evolution.BatchError, match="one task implements one proposal"):
        lineage.describe(config)


# --- terminal decisions ------------------------------------------------------


def test_a_promotion_names_the_revision_a_round_pinned(config: evolution.EvolutionConfig, batch: Path) -> None:
    decision = experiment_decision("promoted", promotion_revision=PROMOTION)
    write_experiment(
        config.experiments_root,
        EXP_01,
        rounds=[experiment_round(1, candidate_revision=CANDIDATE)],
        decision=decision,
    )
    write_outcome(
        config.batches_root,
        BATCH_ID,
        outcome="promoted",
        experiment_id=EXP_01,
        promotion_revision=PROMOTION,
        # The same merge unit and the same reason the experiment records: two
        # records of one event, which is what the reader holds them to.
        promotion=promotion_of(candidate_revision=CANDIDATE),
        reason=decision["reason"],
    )

    derived = only(config)
    assert derived.current is False
    assert derived.open_experiment is None
    assert derived.experiments[0].decision is not None
    assert derived.experiments[0].decision.promotion_revision == PROMOTION
    assert lineage.describe(config).current is None


def test_a_promotion_from_an_open_round_is_refused(config: evolution.EvolutionConfig, batch: Path) -> None:
    """What a promotion carries to the source line is the revision a
    candidate-ready round pinned, never an open round's tip."""

    write_experiment(
        config.experiments_root,
        EXP_01,
        rounds=[experiment_round(1)],
        decision=experiment_decision("promoted", promotion_revision=PROMOTION),
    )
    with pytest.raises(evolution.BatchError, match="never sealed"):
        lineage.describe(config)


@pytest.mark.parametrize(
    "decision",
    [
        experiment_decision("abandoned", promotion_revision=PROMOTION),
        experiment_decision("abandoned", superseded_by=EXP_02),
        experiment_decision("superseded"),
        experiment_decision("promoted"),
    ],
    ids=["abandoned-with-a-promotion", "abandoned-with-a-successor", "superseded-with-nobody", "promoted-with-nothing"],
)
def test_a_decision_carries_exactly_the_fields_its_outcome_means(
    config: evolution.EvolutionConfig, batch: Path, decision: dict
) -> None:
    """The schema keeps every field present and nullable; which of them may be
    non-null is the pairing only the reader can check."""

    write_experiment(
        config.experiments_root,
        EXP_01,
        rounds=[experiment_round(1, candidate_revision=CANDIDATE)],
        decision=decision,
    )
    with pytest.raises(evolution.BatchError, match="only a"):
        lineage.describe(config)


def test_an_abandoned_experiment_records_no_candidate_at_all(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """An attempt dropped before it produced anything records nothing, rather
    than having a candidate invented to stand for it (invariant 7's rule applied
    to experiments)."""

    write_experiment(
        config.experiments_root,
        EXP_01,
        rounds=[experiment_round(1, tasks=[admitted_task("loader-fallback", complete=False)])],
        decision=experiment_decision("abandoned"),
    )

    derived = only(config)
    assert derived.open_experiment is None
    assert derived.experiments[0].candidate_revision is None
    assert derived.current is True


# --- batch outcome -----------------------------------------------------------


def test_the_batch_stays_current_through_admission_and_the_decision(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """`analysis-complete.json` is a stage boundary inside a current batch, not
    the end of one."""

    (config.batches_root / BATCH_ID / "findings.md").write_text("# Findings\n", encoding="utf-8")
    assert only(config).current is True

    write_outcome(config.batches_root, BATCH_ID, reason="the evidence justified no change")
    assert only(config).current is False


def test_two_current_batches_are_refused(config: evolution.EvolutionConfig, batch: Path) -> None:
    write_manifest(config.batches_root, SECOND_BATCH, ["r3"])
    with pytest.raises(evolution.BatchError, match="more than one current batch"):
        lineage.describe(config)


def test_a_batch_cannot_conclude_over_an_open_experiment(config: evolution.EvolutionConfig, batch: Path) -> None:
    write_experiment(config.experiments_root, EXP_01)
    write_outcome(config.batches_root, BATCH_ID, reason="calling it here")
    with pytest.raises(evolution.BatchError, match="carries no decision"):
        lineage.describe(config)


def test_a_batch_cannot_conclude_over_a_successor_nobody_created(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The same rule for the attempt that does not exist yet, and the harder half
    to see: an attempt nobody created has not ended either.

    The relaxation that keeps an interrupted supersession readable is for a batch
    whose cycle is still running, where the redo can still finish it. An outcome
    over one is where that stops: `status` would look for a pending successor
    only in the current batch and find none, no operation could act on a batch
    that has concluded, and the record ending the cycle would release the next
    cohort over an attempt that was never started."""

    write_experiment(
        config.experiments_root,
        EXP_01,
        rounds=[experiment_round(1, tasks=[admitted_task("loader-fallback")])],
        decision=experiment_decision("superseded", superseded_by=EXP_02),
    )
    # While the batch is current the state is readable, which is what the redo
    # depends on.
    derived = only(config)
    assert derived.current is True
    assert derived.pending_successor == EXP_02

    write_outcome(config.batches_root, BATCH_ID, reason="calling it here")
    with pytest.raises(evolution.BatchError, match=f"superseded by {EXP_02}, which does not exist"):
        lineage.describe(config)


@pytest.mark.parametrize(
    "overrides",
    [
        {"outcome": "no-change", "promotion_revision": PROMOTION},
        {"outcome": "no-change", "experiment_id": EXP_01},
        {"outcome": "promoted", "experiment_id": EXP_01},
        {"outcome": "promoted", "promotion_revision": PROMOTION},
    ],
    ids=["no-change-with-a-revision", "no-change-with-an-experiment", "promoted-with-no-revision", "promoted-with-no-experiment"],
)
def test_an_outcome_pairs_its_fields_with_what_it_concluded(
    config: evolution.EvolutionConfig, batch: Path, overrides: dict
) -> None:
    """A `no-change` record naming a promotion is the fabrication invariant 7
    forbids; a `promoted` record naming nothing ends the batch while saying
    nothing about what reached the source line."""

    write_outcome(config.batches_root, BATCH_ID, reason="recorded", **overrides)
    with pytest.raises(evolution.BatchError):
        lineage.describe(config)


@pytest.mark.parametrize(
    ("decision", "overrides", "message"),
    [
        (None, {"experiment_id": EXP_02}, "no such experiment"),
        (experiment_decision("abandoned"), {}, "state it the same way"),
        (
            experiment_decision("promoted", promotion_revision="f" * 40),
            {},
            "one commit on the source line",
        ),
    ],
    ids=["unknown-experiment", "experiment-says-abandoned", "two-promotion-revisions"],
)
def test_a_promoted_batch_and_the_experiment_it_names_state_one_event(
    config: evolution.EvolutionConfig,
    batch: Path,
    decision: dict | None,
    overrides: dict,
    message: str,
) -> None:
    """Two records, one event. Left unchecked they can disagree about which
    attempt reached the source line, or with which commit."""

    write_experiment(
        config.experiments_root,
        EXP_01,
        rounds=[experiment_round(1, candidate_revision=CANDIDATE)],
        decision=decision if decision is not None else experiment_decision("abandoned"),
    )
    write_outcome(
        config.batches_root,
        BATCH_ID,
        outcome="promoted",
        reason="promoted",
        **{"experiment_id": EXP_01, "promotion_revision": PROMOTION, **overrides},
    )
    with pytest.raises(evolution.BatchError, match=message):
        lineage.describe(config)


def test_a_no_change_batch_over_a_promoted_experiment_is_refused(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The same contradiction as the disagreeing pair above, read from the side
    that names nothing — which is why checking only the experiment an outcome
    names never sees it. This record says both that the source line moved and
    that it did not."""

    write_experiment(
        config.experiments_root,
        EXP_01,
        rounds=[experiment_round(1, candidate_revision=CANDIDATE)],
        decision=experiment_decision("promoted", promotion_revision=PROMOTION),
    )
    write_outcome(config.batches_root, BATCH_ID, reason="the evidence justified no change")
    with pytest.raises(evolution.BatchError, match="record a promotion"):
        lineage.describe(config)


def test_one_batch_promotes_one_candidate(config: evolution.EvolutionConfig, batch: Path) -> None:
    """A promoted outcome names which attempt reached the source line; a second
    experiment claiming the same leaves that trail two answers deep."""

    for experiment_id, draft_id in ((EXP_01, "loader-fallback"), (EXP_02, "hook-side-loader")):
        write_experiment(
            config.experiments_root,
            experiment_id,
            rounds=[experiment_round(1, tasks=[admitted_task(draft_id)], candidate_revision=CANDIDATE)],
            decision=experiment_decision("promoted", promotion_revision=PROMOTION),
        )
    write_outcome(
        config.batches_root,
        BATCH_ID,
        outcome="promoted",
        reason="promoted",
        experiment_id=EXP_02,
        promotion_revision=PROMOTION,
    )
    with pytest.raises(evolution.BatchError, match="one batch promotes one candidate"):
        lineage.describe(config)


def promoted_batch(config: evolution.EvolutionConfig, **overrides: Any) -> dict:
    """A batch whose experiment was promoted, with the three records that make a
    promotion one history: the decision, the merge unit the experiment was
    prepared as, and the completed run that measured it. `overrides` change what
    the *outcome* states, which is the record the others are checked against."""

    decision = experiment_decision("promoted", promotion_revision=PROMOTION)
    write_experiment(
        config.experiments_root,
        EXP_01,
        rounds=[experiment_round(1, candidate_revision=CANDIDATE)],
        decision=decision,
    )
    merge = promotion_of(**{"candidate_revision": CANDIDATE, **overrides})
    write_outcome(
        config.batches_root,
        BATCH_ID,
        outcome="promoted",
        reason=decision["reason"],
        experiment_id=EXP_01,
        promotion_revision=PROMOTION,
        promotion=merge,
    )
    return merge


def test_a_promoted_batch_reads_back_when_all_three_records_agree(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The baseline the disagreements below are read against."""

    promoted_batch(config)
    assert lineage.describe(config).current is None
    assert only(config).outcome is not None


@pytest.mark.parametrize(
    "overrides",
    [
        {"round": 2},
        {"candidate_revision": "d" * 40},
        {"merge_input_revision": "9" * 40},
        {"merge_input_ref": "refs/heads/other"},
        {"tree": "8" * 40},
        {"planned_targets": ["orch-hub"]},
    ],
    ids=["round", "candidate", "merge-input", "source-line", "tree", "planned-targets"],
)
def test_an_outcome_states_the_merge_unit_the_experiment_was_promoted_as(
    config: evolution.EvolutionConfig, batch: Path, overrides: dict
) -> None:
    """The outcome's merge unit is what holds the promotion revision to the
    evidence, and a claim nothing checks is one a schema-valid record makes
    freely. Every field of it is the promoted experiment's own, which is the
    record written before the source line moved."""

    promoted_batch(config, **overrides)
    with pytest.raises(evolution.BatchError, match="differently from the promotion"):
        lineage.describe(config)


def test_a_batch_is_concluded_by_the_reason_it_was_promoted_for(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    write_experiment(
        config.experiments_root,
        EXP_01,
        rounds=[experiment_round(1, candidate_revision=CANDIDATE)],
        decision=experiment_decision("promoted", promotion_revision=PROMOTION),
    )
    write_outcome(
        config.batches_root,
        BATCH_ID,
        outcome="promoted",
        reason="something else entirely",
        experiment_id=EXP_01,
        promotion_revision=PROMOTION,
        promotion=promotion_of(candidate_revision=CANDIDATE),
    )
    with pytest.raises(evolution.BatchError, match="one decision, and the batch it ends"):
        lineage.describe(config)


def test_a_promotion_no_run_measured_is_refused(config: evolution.EvolutionConfig, batch: Path) -> None:
    """What reaches the source line is a tree a replay measured (invariant 10),
    and the records that say so are checked rather than believed: two hand-made
    records agreeing with each other still describe a promotion no evidence
    justified."""

    promoted_batch(config)
    replays = config.experiments_root / EXP_01 / "replays.json"
    written = json.loads(replays.read_text(encoding="utf-8"))
    written["replays"][0]["integration"]["tree"] = "8" * 40
    written["replays"][0]["result"]["outcome"] = "failed"
    written["replays"][0]["result"]["metrics"] = []
    written["replays"][0]["result"]["elapsed_seconds"] = None
    replays.write_text(json.dumps(written, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(evolution.BatchError, match="no completed run of that round measured that integration"):
        lineage.describe(config)


def test_the_commit_on_the_source_line_is_what_the_record_is_held_to(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The one check outside this controller's own records, made wherever the
    checkout holds the commit: a promotion revision naming a commit that carries
    another tree, or was made from other commits, is a record Git contradicts."""

    real = git_commit(config.repo_root, "a commit that is not a merge of anything")
    promoted_batch(config)
    outcome_path = config.batches_root / BATCH_ID / "outcome.json"
    written = json.loads(outcome_path.read_text(encoding="utf-8"))
    written["promotion_revision"] = real
    outcome_path.write_text(json.dumps(written, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rewrite_decision(config, promotion_revision=real)

    with pytest.raises(evolution.BatchError, match="is what the record is held to"):
        lineage.describe(config)


def rewrite_decision(config: evolution.EvolutionConfig, **changes: Any) -> None:
    """Restate the promotion an experiment records, keeping its two halves in
    agreement — the decision names the revision and the merge unit carries it."""

    path = config.experiments_root / EXP_01 / "experiment.json"
    written = json.loads(path.read_text(encoding="utf-8"))
    written["decision"].update(changes)
    if "promotion_revision" in changes:
        written["promotion"]["revision"] = changes["promotion_revision"]
    path.write_text(json.dumps(written, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_a_conclusion_recorded_before_the_merge_unit_existed_still_ends_its_batch(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The reader's side of the same compatibility: a `no-change` record from
    before the field existed reads as one stating it null, rather than failing
    closed and leaving a batch that ended long ago current forever."""

    path = write_outcome(config.batches_root, BATCH_ID, reason="no cluster reached recurrence")
    written = json.loads(path.read_text(encoding="utf-8"))
    del written["promotion"]
    path.write_text(json.dumps(written, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert lineage.describe(config).current is None
    assert only(config).outcome is not None


def test_a_promoted_outcome_still_has_to_state_its_merge_unit(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Optional in the schema is not optional in the record: what makes the merge
    unit required for a `promoted` conclusion is the pairing rule, which no
    version of this schema could express anyway."""

    promoted_batch(config)
    path = config.batches_root / BATCH_ID / "outcome.json"
    written = json.loads(path.read_text(encoding="utf-8"))
    del written["promotion"]
    path.write_text(json.dumps(written, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(evolution.BatchError, match="states the experiment it promoted"):
        lineage.describe(config)


def test_an_outcome_naming_another_batch_is_refused(config: evolution.EvolutionConfig, batch: Path) -> None:
    write_manifest(config.batches_root, SECOND_BATCH, ["r3"])
    write_outcome(config.batches_root, SECOND_BATCH, reason="over here")
    (config.batches_root / SECOND_BATCH / "outcome.json").rename(config.batches_root / BATCH_ID / "outcome.json")
    with pytest.raises(evolution.BatchError, match="cannot end another"):
        lineage.describe(config)


def test_an_unreadable_outcome_does_not_quietly_leave_the_batch_current(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    (config.batches_root / BATCH_ID / "outcome.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(evolution.BatchError, match="unreadable"):
        lineage.describe(config)


# --- the admission gate ------------------------------------------------------


def test_a_draft_waits_until_something_takes_it_or_turns_it_down(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Admission copies and leaves the draft in place, so the directory holds
    every proposal ever made rather than the ones still to decide."""

    for draft_id in ("loader-fallback", "hook-side-loader", "prompt-budget"):
        write_draft(config.batches_root, BATCH_ID, draft_id)
    write_experiment(config.experiments_root, EXP_01, rounds=[experiment_round(1, tasks=[admitted_task("loader-fallback")])])
    write_rejected_drafts(config.batches_root, BATCH_ID, [rejection("hook-side-loader")])

    gate = only(config).gate
    assert gate.waiting == ("prompt-budget",)
    assert gate.consumed == {"loader-fallback": EXP_01}
    assert gate.declined == ("hook-side-loader",)
    assert (config.batches_root / BATCH_ID / "proposed-tasks" / "loader-fallback.md").is_file()


def test_a_draft_declined_and_admitted_is_refused(config: evolution.EvolutionConfig, batch: Path) -> None:
    """Admitting and declining are both terminal for a proposal."""

    write_draft(config.batches_root, BATCH_ID, "loader-fallback")
    write_experiment(config.experiments_root, EXP_01)
    write_rejected_drafts(config.batches_root, BATCH_ID, [rejection("loader-fallback")])
    with pytest.raises(evolution.BatchError, match="both terminal"):
        lineage.describe(config)


def test_a_draft_declined_twice_is_refused(config: evolution.EvolutionConfig, batch: Path) -> None:
    """Two decisions about the same bytes, or one recorded twice — the record
    does not say which, while a re-proposal legitimately has a new id."""

    write_rejected_drafts(
        config.batches_root,
        BATCH_ID,
        [rejection("hook-side-loader"), rejection("hook-side-loader", reason="again")],
    )
    with pytest.raises(evolution.BatchError, match="declined twice"):
        lineage.describe(config)


def test_a_spent_draft_whose_file_was_deleted_is_reported_not_refused(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The record still holds the id and the hash of what was admitted, so the
    lineage is intact and the bytes are recoverable from history."""

    write_experiment(config.experiments_root, EXP_01)
    gate = only(config).gate
    assert gate.missing == ("loader-fallback",)
    assert gate.waiting == ()


def test_a_file_that_could_never_be_a_draft_id_is_not_waiting(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """No admission could record `Notes.md` as a draft id, so it is not a
    proposal anyone is waiting on."""

    write_draft(config.batches_root, BATCH_ID, "prompt-budget")
    (config.batches_root / BATCH_ID / "proposed-tasks" / "Notes.md").write_text("scratch\n", encoding="utf-8")

    gate = only(config).gate
    assert gate.waiting == ("prompt-budget",)
    assert gate.unusable == ("Notes.md",)


def test_the_gate_of_a_batch_with_no_drafts_is_empty(config: evolution.EvolutionConfig, batch: Path) -> None:
    gate = only(config).gate
    assert gate == lineage.Gate(waiting=(), consumed={}, declined=(), missing=(), unusable=())


# --- the experiment ref ------------------------------------------------------


@pytest.fixture
def refs(config: evolution.EvolutionConfig, batch: Path) -> dict[str, str]:
    """A repository whose experiment record pins a real commit, so the ref can
    be moved around it."""

    base = git_rev(config.repo_root, "v2.2.0")
    candidate = git_commit(config.repo_root, "round-1 work")
    write_experiment(
        config.experiments_root,
        EXP_01,
        base_revision=base,
        rounds=[experiment_round(1, candidate_revision=candidate)],
    )
    return {"base": base, "candidate": candidate}


def test_a_clone_without_the_ref_namespace_still_derives_the_lineage(
    config: evolution.EvolutionConfig, refs: dict[str, str]
) -> None:
    """`refs/evolution/experiments/*` is outside the default fetch refspec, so
    this is the ordinary state of every clone. The pinned revision identifies the
    tree; the ref only keeps it reachable where it exists."""

    state = only(config).ref
    assert state is not None
    assert state.state == lineage.REF_ABSENT
    assert state.tip is None
    assert state.consistent is None
    assert only(config).candidate_revision == refs["candidate"]


def test_a_ref_at_the_pinned_candidate_is_consistent(
    config: evolution.EvolutionConfig, refs: dict[str, str]
) -> None:
    git_update_ref(config.repo_root, lineage.experiment_ref(EXP_01), refs["candidate"])

    state = only(config).ref
    assert state is not None
    assert state.state == lineage.REF_AT_PIN
    assert state.consistent is True


def test_a_ref_ahead_of_a_candidate_ready_round_is_reported(
    config: evolution.EvolutionConfig, refs: dict[str, str]
) -> None:
    """While the last round is candidate-ready the ref stays where it was
    pinned; work resumes by opening a new round. An operation finding it ahead
    stops rather than guessing which of the two the evidence meant."""

    git_update_ref(config.repo_root, lineage.experiment_ref(EXP_01), git_commit(config.repo_root, "later work"))

    state = only(config).ref
    assert state is not None
    assert state.state == lineage.REF_AHEAD
    assert state.pinned_expected is True
    assert state.consistent is False


def test_a_ref_ahead_of_an_open_round_is_the_ordinary_state(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """While a round is open the ref fast-forwards under whoever is working on
    it — which is exactly why nothing may measure it yet."""

    base = git_rev(config.repo_root, "v2.2.0")
    write_experiment(config.experiments_root, EXP_01, base_revision=base, rounds=[experiment_round(1)])
    git_update_ref(config.repo_root, lineage.experiment_ref(EXP_01), git_commit(config.repo_root, "in progress"))

    state = only(config).ref
    assert state is not None
    assert state.state == lineage.REF_AHEAD
    assert state.pinned_expected is False
    assert state.consistent is True


def test_a_first_candidate_off_the_frozen_base_is_not_a_consistent_ref(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """The tip against the latest pin is only the last link. Checked alone, an
    experiment whose very first candidate sits on a history unrelated to the
    batch's base reads as exactly what a well-behaved one looks like: the ref
    resting on the revision its record names."""

    base = git_rev(config.repo_root, "v2.2.0")
    unrelated = git_unrelated_commit(config.repo_root, "an approach built somewhere else")
    write_experiment(
        config.experiments_root,
        EXP_01,
        base_revision=base,
        rounds=[experiment_round(1, candidate_revision=unrelated)],
    )
    git_update_ref(config.repo_root, lineage.experiment_ref(EXP_01), unrelated)

    state = only(config).ref
    assert state is not None
    assert state.state == lineage.REF_AT_PIN
    assert state.chain is False
    assert state.chain_break == (base, unrelated)
    assert state.consistent is False


def test_a_round_that_does_not_build_on_the_one_before_it_is_not_consistent(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Invariant 15's "each round's candidate stays reachable from the next
    one's": a round 2 that does not contain round 1's candidate has rewritten it,
    leaving round 1's evidence describing a tree its own record can no longer
    produce."""

    base = git_rev(config.repo_root, "v2.2.0")
    git_checkout(config.repo_root, base)
    first = git_commit(config.repo_root, "round-1 work")
    git_checkout(config.repo_root, base)
    second = git_commit(config.repo_root, "round-2 work, off round 1")
    write_experiment(
        config.experiments_root,
        EXP_01,
        base_revision=base,
        rounds=[
            experiment_round(1, tasks=[admitted_task("loader-fallback")], candidate_revision=first),
            experiment_round(2, tasks=[admitted_task("hook-side-loader")], candidate_revision=second),
        ],
    )
    git_update_ref(config.repo_root, lineage.experiment_ref(EXP_01), second)

    state = only(config).ref
    assert state is not None
    assert state.state == lineage.REF_AT_PIN
    assert state.chain is False
    assert state.chain_break == (first, second)
    assert state.consistent is False


def test_a_pin_chain_this_checkout_cannot_walk_is_unknown_not_broken(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """Half a history checked is enough to know one is wrong and never enough to
    call it right — but a link nobody here can answer says nothing either way."""

    write_experiment(
        config.experiments_root,
        EXP_01,
        rounds=[experiment_round(1, candidate_revision=CANDIDATE)],
    )
    git_update_ref(config.repo_root, lineage.experiment_ref(EXP_01), git_rev(config.repo_root, "HEAD"))

    state = only(config).ref
    assert state is not None
    assert state.chain is None
    assert state.chain_break is None
    assert state.consistent is None


def test_a_ref_that_no_longer_contains_its_pinned_candidate_is_diverged(
    config: evolution.EvolutionConfig, refs: dict[str, str]
) -> None:
    """The fast-forward-only rule broken: a rewritten round leaves the revisions
    its own record pins unreachable."""

    git_update_ref(config.repo_root, lineage.experiment_ref(EXP_01), refs["base"])

    state = only(config).ref
    assert state is not None
    assert state.state == lineage.REF_DIVERGED
    assert state.consistent is False


def test_a_pinned_revision_this_checkout_does_not_hold_is_unknown_not_diverged(
    config: evolution.EvolutionConfig, batch: Path
) -> None:
    """A partial checkout says nothing about the lineage, and must not be
    reported as a broken ref."""

    write_experiment(config.experiments_root, EXP_01, rounds=[experiment_round(1, candidate_revision=CANDIDATE)])
    git_update_ref(config.repo_root, lineage.experiment_ref(EXP_01), git_rev(config.repo_root, "HEAD"))

    state = only(config).ref
    assert state is not None
    assert state.state == lineage.REF_UNKNOWN
    assert state.consistent is None


def test_the_lineage_does_not_change_with_the_checked_out_revision(
    config: evolution.EvolutionConfig, refs: dict[str, str]
) -> None:
    """`HEAD` against the release tag answers "is this checkout on the release
    line", which names no experiment and changes with a `git checkout`. This
    derivation reads records, so it does not move."""

    git_update_ref(config.repo_root, lineage.experiment_ref(EXP_01), refs["candidate"])
    before = lineage.describe(config)

    git_checkout(config.repo_root, refs["base"])
    assert lineage.describe(config) == before


def test_a_directory_nested_in_another_repository_reads_no_refs_of_its_own(
    tmp_path: Path, config: evolution.EvolutionConfig, refs: dict[str, str]
) -> None:
    """The same protection the release-line reading has: a workspace inside
    someone else's checkout must not adopt its refs."""

    git_update_ref(config.repo_root, lineage.experiment_ref(EXP_01), refs["candidate"])
    inner = config.repo_root / "nested"
    inner.mkdir()
    assert revisions.ref_tip(inner, lineage.experiment_ref(EXP_01)) is None


# --- readers used on their own -----------------------------------------------


def test_an_absent_record_is_a_legal_answer(config: evolution.EvolutionConfig, batch: Path) -> None:
    """A stage or a batch that has not ended yet, and a gate nobody has declined
    anything at."""

    frozen = manifests.load_batches(config)[0]
    assert manifests.read_outcome(config, frozen) is None
    assert manifests.read_rejected_drafts(config, frozen) == ()
    assert lineage.load_experiments(config) == {}
