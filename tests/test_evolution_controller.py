"""The evolution controller's import path.

Everything here runs against a temporary repository and a directory-backed
feed: no network, no orch-hub, and no evaluation call of any kind — the
contract forbids listing or importing from starting one, so the suite has
nothing to mock away.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from evolution_fixtures import (
    ARTIFACT_BODIES,
    REPO_ROOT,
    make_record,
    make_repo,
    snapshot,
    write_feed,
    write_manifest,
)

from mandrel import evolution
from mandrel.evolution import config as config_module
from mandrel.evolution import feed as feed_module
from mandrel.evolution import importer, ledger, reports, schema, state

# --- fixtures ----------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path)


@pytest.fixture
def config(repo: Path) -> evolution.EvolutionConfig:
    return evolution.load_config(repo)


# --- config ------------------------------------------------------------------


def test_config_loads_the_shipped_policy_and_hashes_the_file(config: evolution.EvolutionConfig, repo: Path) -> None:
    from mandrel.hashing import sha256_bytes

    assert config.batch.target_task_count == 20
    assert config.batch.minimum_task_count == 10
    assert config.eligibility.deduplicate_by == config_module.SUPPORTED_DEDUPLICATION
    assert config.source_line.ref.startswith("refs/")
    assert config.sha256 == sha256_bytes((repo / "evolution" / "config.toml").read_bytes())
    assert config.runtime_root == repo / ".ai-evolution"
    assert config.state_path == repo / ".ai-evolution" / "state.json"
    assert config.ledger_path == repo / "evolution" / "ledger.jsonl"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("schema_version = 1", "schema_version = 2"),
        ("include_reports_without_findings = true", "include_reports_without_findings = false"),
        ("require_static_metrics = true", "require_static_metrics = false"),
        ("allow_no_change = true", "allow_no_change = false"),
        ('deduplicate_by = "source-repo-and-task"', 'deduplicate_by = "source-repo"'),
        ("minimum_task_count = 10", "minimum_task_count = 40"),
        ('runtime_root = ".ai-evolution"', 'runtime_root = "../elsewhere"'),
        ("[source_line]", "[unused_section]"),
    ],
)
def test_config_fails_closed_on_policy_it_cannot_honour(repo: Path, old: str, new: str) -> None:
    """Each of these would otherwise run a controller that quietly does
    something other than what the contract says."""
    path = repo / "evolution" / "config.toml"
    text = path.read_text(encoding="utf-8")
    # A substitution that matches nothing leaves the shipped policy standing, and
    # a shipped policy loads — so the case would report the config failing to
    # fail closed when what actually happened is that the case stopped editing it.
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")

    with pytest.raises(evolution.ConfigError):
        evolution.load_config(repo)


def set_source_line_ref(repo: Path, value: str) -> None:
    """Rewrite `[source_line].ref` to `value`, whatever the file currently holds.

    Keyed on the setting rather than on the ref this repository releases from
    today, because the substitution has to survive that ref moving: a key spelled
    as the current value stops matching the day the policy names another line,
    and stops matching silently, since the config it leaves behind is the valid
    one. Scoped to the section as well as the key, so a `ref` setting added to
    some other section later is not what gets rewritten.
    """

    path = repo / "evolution" / "config.toml"
    section: str | None = None
    rewritten = 0
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
        if section == "[source_line]" and stripped.startswith("ref ="):
            line = f'ref = "{value}"\n'
            rewritten += 1
        out.append(line)
    assert rewritten == 1, f"rewrote [source_line].ref {rewritten} times, not once"
    path.write_text("".join(out), encoding="utf-8")


@pytest.mark.parametrize(
    "ref",
    [
        # The source line has to mean one commit in every clone. A bare branch, a
        # tag, `HEAD`, and a revision expression all resolve somewhere in most
        # checkouts, which is the failure rather than the fix. Each of these is
        # refused for its *form*; which branch a case happens to name is not what
        # is under test, and none of them is the line this repository configures.
        "main",
        "HEAD",
        "refs/heads/main@{1}",
        "refs/heads",
        "",
    ],
)
def test_config_fails_closed_on_a_source_line_that_does_not_name_one_commit(repo: Path, ref: str) -> None:
    """The same refusal as above for the one setting whose value moves. Stated
    against the field so that a policy naming a third line changes which ref is
    overwritten and nothing else about what these cases prove."""

    set_source_line_ref(repo, ref)

    with pytest.raises(evolution.ConfigError):
        evolution.load_config(repo)


def test_the_source_line_cases_hold_for_a_policy_naming_another_line(repo: Path) -> None:
    """The property the rewrite exists for, asserted rather than assumed: point
    the shipped policy at a line it has never named, and the refusals above are
    still refusals of that line rather than no-ops against a valid config."""

    set_source_line_ref(repo, "refs/heads/some-other-line")
    assert evolution.load_config(repo).source_line.ref == "refs/heads/some-other-line"

    set_source_line_ref(repo, "some-other-line")
    with pytest.raises(evolution.ConfigError):
        evolution.load_config(repo)


def rewrite_config(repo: Path, edits: list[tuple[str, str]]) -> None:
    path = repo / "evolution" / "config.toml"
    text = path.read_text(encoding="utf-8")
    for old, new in edits:
        assert old in text
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")


ARTIFACTS_SETTING = 'imported_artifacts = ".ai-evolution/imported-artifacts"'


@pytest.mark.parametrize(
    "edits",
    [
        # Inside the repository, and tracked: raw bundles would land in Git.
        pytest.param(
            [
                ('runtime_root = ".ai-evolution"', 'runtime_root = "evolution/cases"'),
                (ARTIFACTS_SETTING, 'imported_artifacts = "evolution/cases/raw"'),
            ],
            id="runtime-root-in-the-versioned-tree",
        ),
        pytest.param(
            [(ARTIFACTS_SETTING, 'imported_artifacts = "evolution/cases"')],
            id="artifacts-outside-the-runtime-root",
        ),
        pytest.param(
            [('ledger = "evolution/ledger.jsonl"', 'ledger = ".ai-evolution/ledger.jsonl"')],
            id="audit-outside-the-versioned-tree",
        ),
        pytest.param([('batches = "evolution/batches"', 'batches = "batches"')], id="batches-outside-the-versioned-tree"),
        pytest.param([('runtime_root = ".ai-evolution"', 'runtime_root = "."')], id="runtime-root-is-the-repository"),
        pytest.param([('cases = "evolution/cases"', 'cases = "evolution/batches"')], id="two-locations-overlap"),
        pytest.param(
            [('runtime_root = ".ai-evolution"', 'runtime_root = "evolution/../.ai-evolution"')],
            id="path-that-does-not-mean-what-it-says",
        ),
    ],
)
def test_config_refuses_storage_that_crosses_the_ownership_boundary(repo: Path, edits: list[tuple[str, str]]) -> None:
    """Repository containment is not the invariant. Ignored runtime state and
    committed sanitized records own separate trees (contract invariant 11), and
    a config that mixes them writes raw evidence into Git."""
    rewrite_config(repo, edits)

    with pytest.raises(evolution.ConfigError):
        evolution.load_config(repo)


@pytest.mark.parametrize(
    "edits",
    [
        # Staging here creates `state.json` as a directory, and the atomic state
        # commit that ends every page then fails against it.
        pytest.param(
            [(ARTIFACTS_SETTING, 'imported_artifacts = ".ai-evolution/state.json/raw"')],
            id="artifacts-below-the-state-file",
        ),
        pytest.param(
            [(ARTIFACTS_SETTING, 'imported_artifacts = ".ai-evolution/state.json"')],
            id="artifacts-are-the-state-file",
        ),
        # The lock is taken before the first bundle is staged, so the directory
        # cannot be created at all.
        pytest.param(
            [(ARTIFACTS_SETTING, 'imported_artifacts = ".ai-evolution/lock/raw"')],
            id="artifacts-below-the-lock",
        ),
        pytest.param(
            [('ledger = "evolution/ledger.jsonl"', 'ledger = "evolution/config.toml"')],
            id="ledger-is-the-policy-file",
        ),
        pytest.param([('batches = "evolution/batches"', 'batches = "evolution/schemas"')], id="batches-are-the-schemas"),
    ],
)
def test_config_refuses_storage_that_claims_a_fixed_controller_path(repo: Path, edits: list[tuple[str, str]]) -> None:
    """Each tree holds paths the controller writes by fixed name. A configurable
    location that lands on one is a config that loads and then cannot run, or —
    for the versioned side — one that appends audit lines over its own
    contract."""
    rewrite_config(repo, edits)

    with pytest.raises(evolution.ConfigError):
        evolution.load_config(repo)


# --- schema validation -------------------------------------------------------


def test_shipped_schemas_use_only_implemented_keywords(config: evolution.EvolutionConfig) -> None:
    """The validator fails closed on anything it does not implement, so this
    also asserts that the shipped contracts are actually enforceable."""
    for filename in (
        config_module.IMPORT_SCHEMA_FILENAME,
        config_module.BATCH_SCHEMA_FILENAME,
        config_module.LEDGER_SCHEMA_FILENAME,
    ):
        loaded = schema.load_schema(config.schema_path(filename))
        assert schema.validate({}, loaded), f"{filename} accepted an empty object"


def test_validator_reports_each_kind_of_violation(config: evolution.EvolutionConfig) -> None:
    import_schema = schema.load_schema(config.schema_path(config_module.IMPORT_SCHEMA_FILENAME))
    record = make_record(key="r1", sequence=1)
    assert schema.validate(record, import_schema) == []

    record["artifacts"]["evidence"]["sha256"] = "not-a-digest"
    record["sequence"] = 0
    record["source"]["archived"] = False
    record["unexpected"] = 1
    del record["generated_at"]
    errors = schema.validate(record, import_schema)

    assert any("sha256" in error and "does not match" in error for error in errors)
    assert any("below minimum" in error for error in errors)
    assert any("expected True" in error for error in errors)
    assert any("unexpected property" in error for error in errors)
    assert any("missing required property 'generated_at'" in error for error in errors)


def test_validator_refuses_a_keyword_it_does_not_implement() -> None:
    with pytest.raises(evolution.SchemaError):
        schema.validate({"a": 1}, {"type": "object", "propertyNames": {"pattern": "^a$"}})


def test_validator_rejects_a_boolean_where_an_integer_is_required() -> None:
    assert schema.validate(True, {"type": "integer"})


@pytest.mark.parametrize(
    "value",
    [0, 1, -1, 1.5, 10**400, -(10**400)],
    ids=["zero", "one", "negative", "float", "huge", "huge-negative"],
)
def test_a_json_number_has_no_range(value: object) -> None:
    """`number` is JSON's number, and JSON does not bound one. An `int` is finite
    whatever its magnitude, but `math.isfinite` converts before it answers, so
    asking it of one raises `OverflowError` — out of validation entirely, past
    every caller that handles a malformed document."""

    assert schema.validate(value, {"type": "number"}) == []
    assert schema.validate(10**400, {"type": "number", "minimum": 0}) == []


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_validator_rejects_a_float_json_has_no_literal_for(value: float) -> None:
    """The three Python hands over and JSON cannot spell. Naming what arrived
    matters here: reporting `nan` as "not a number" reads as a type confusion
    rather than the value it was."""

    (error,) = schema.validate(value, {"type": "number"})
    assert repr(value) in error


@pytest.mark.parametrize(
    "value",
    ["abc\n", "abc\n\n", "\nabc", "abcd", "ABC"],
    ids=["trailing-newline", "two-newlines", "leading-newline", "suffix", "case"],
)
def test_an_anchored_pattern_means_the_whole_string(value: str) -> None:
    """Python's `$` also matches just before a final newline; the ECMA-262 `$`
    a schema author writes does not. Every `^...$` identity in
    `evolution/schemas/` — draft slugs that must be one path segment, ids,
    40-hex revisions — depends on this being the strict reading."""

    anchored = {"type": "string", "pattern": "^abc$"}
    assert schema.validate("abc", anchored) == []
    assert schema.validate(value, anchored)


def test_an_unanchored_pattern_still_matches_anywhere() -> None:
    """JSON Schema `pattern` is a search, not a full match. Only the meaning of
    `$` was corrected, so a pattern that never anchored keeps its reach."""

    assert schema.validate("xxabcxx", {"type": "string", "pattern": "abc"}) == []


@pytest.mark.parametrize(
    "pattern",
    ["^a[$]b$", r"^a\$b$"],
    ids=["character-class", "escaped"],
)
def test_a_literal_dollar_is_left_alone(pattern: str) -> None:
    """`$` is an anchor only where it is one; inside a class or behind a
    backslash it is the character, and rewriting it there would turn a pattern
    that matches into one that cannot."""

    assert schema.validate("a$b", {"type": "string", "pattern": pattern}) == []


def test_a_pattern_reads_its_classes_as_ascii() -> None:
    """ECMA-262 without the `u` flag scopes `\\d` to 0-9. Arabic-Indic digits
    parse as a sequence number, a digest, or a batch ordinal nowhere
    downstream."""

    assert schema.validate("٧", {"type": "string", "pattern": r"^\d$"})


@pytest.mark.parametrize(
    "value",
    [
        "2026-07-30T10:00:00Z",
        "2026-07-30t10:00:00z",
        "2026-07-30T10:00:00.123456+02:00",
        "2026-07-30T10:00:00-05:30",
    ],
)
def test_rfc3339_timestamps_are_accepted(value: str) -> None:
    assert schema.is_rfc3339_date_time(value)


@pytest.mark.parametrize(
    "value",
    [
        "2026-07-30 10:00:00Z",  # space separator: ISO 8601, not RFC 3339
        "2026-W31-4T10:00:00Z",  # week date
        "2026-07-30T10:00:00+00:00:30",  # offset carrying seconds
        "20260730T100000Z",  # basic format
        "2026-07-30T10:00:00",  # no offset at all
        "2026-07-30",
        "2026-13-30T10:00:00Z",  # grammatical, impossible
        "2026-07-30T10:00:00+99:00",
        "2026-07-30T10:00:00Z ",
        "",
    ],
)
def test_non_rfc3339_timestamps_are_refused(value: str) -> None:
    """`datetime.fromisoformat` accepts several of these. A timestamp the
    versioned schema calls `date-time` and cannot be compared as one would skew
    every age and ordering decision built on it."""
    assert not schema.is_rfc3339_date_time(value)
    assert schema.validate(value, {"type": "string", "format": "date-time"})


def test_a_report_whose_timestamp_is_not_rfc3339_never_enters_the_pool(
    config: evolution.EvolutionConfig, tmp_path: Path
) -> None:
    record = make_record(key="bad", sequence=1)
    record["generated_at"] = "2026-07-30 10:00:00Z"
    feed = write_feed(tmp_path / "feed", [record])

    result = evolution.sync(config, feed)

    assert result.rejected == (("bad", reports.REASON_SCHEMA_INVALID),)
    assert result.pool_size == 0


# --- listing is read-only ----------------------------------------------------


def test_list_writes_nothing_at_all(config: evolution.EvolutionConfig, repo: Path, tmp_path: Path) -> None:
    feed = write_feed(tmp_path / "feed", [make_record(key="r1", sequence=1)])
    before = snapshot(repo)

    result = evolution.list_candidates(config, feed)

    assert [item.status for item in result.candidates] == [importer.STATUS_NEW]
    assert result.new_task_count == 1
    assert not config.runtime_root.exists()
    assert snapshot(repo) == before


def test_list_classifies_known_pooled_and_ineligible_records(
    config: evolution.EvolutionConfig, tmp_path: Path
) -> None:
    first = make_record(key="r1", sequence=1)
    rerun = make_record(key="r2", sequence=2, evaluation_id="eval-rerun")
    other = make_record(key="r3", sequence=3, repo_id="repo-beta", task_id="2026-07-02-task")
    unarchived = make_record(key="r4", sequence=4, task_id="2026-07-03-task")
    unarchived["source"]["archived"] = False
    feed = write_feed(tmp_path / "feed", [first, rerun, other, unarchived])

    evolution.sync(config, evolution.DirectoryFeed(tmp_path / "feed"), page_size=1, max_pages=1)

    # From the stored cursor: r1 was consumed, so the feed does not offer it.
    by_key = {item.report_key: item for item in evolution.list_candidates(config, feed).candidates}
    assert set(by_key) == {"r2", "r3", "r4"}
    assert by_key["r2"].status == importer.STATUS_RERUN
    assert by_key["r3"].status == importer.STATUS_NEW
    assert by_key["r4"].status == importer.STATUS_REJECTED
    assert by_key["r4"].reason == reports.REASON_NOT_ARCHIVED
    assert evolution.list_candidates(config, feed).new_task_count == 1

    # An already-imported report is recognised, not re-offered as new, when a
    # re-fetched page carries it again.
    rewound = evolution.load_state(config)
    rewound.cursor = None
    evolution.save_state(config, rewound)
    replayed = {item.report_key: item for item in evolution.list_candidates(config, feed).candidates}
    assert replayed["r1"].status == importer.STATUS_KNOWN


# --- sync --------------------------------------------------------------------


def test_sync_imports_verified_reports_into_the_pending_pool(
    config: evolution.EvolutionConfig, tmp_path: Path
) -> None:
    feed = write_feed(
        tmp_path / "feed",
        [
            make_record(key="r1", sequence=1),
            make_record(key="r2", sequence=2, repo_id="repo-beta", task_id="2026-07-02-task"),
        ],
    )

    result = evolution.sync(config, feed)

    assert result.imported == ("r1", "r2")
    assert result.pool_size == 2
    assert result.exhausted
    assert result.cursor_before is None
    assert json.loads(result.cursor_after) == [2, "r2", "r2.json"]

    pool = evolution.load_state(config).pending
    entry = pool[0]
    assert entry.dedup_key == ("repo-alpha", "2026-07-01-task")
    assert entry.report.bundle_sha256 == reports.normalize(
        make_record(key="r1", sequence=1), config, reports.load_import_schema(config)
    ).bundle_sha256
    staged = config.repo_root / entry.report.artifacts_path
    assert (staged / state.REPORT_JSON_FILENAME).is_file()
    for name, body in ARTIFACT_BODIES.items():
        assert (staged / state.ARTIFACTS_SUBDIR / name).read_bytes() == body


def test_sync_is_idempotent_over_an_unchanged_feed(config: evolution.EvolutionConfig, tmp_path: Path) -> None:
    feed = write_feed(tmp_path / "feed", [make_record(key="r1", sequence=1)])
    evolution.sync(config, feed)
    ledger_after_first = config.ledger_path.read_text(encoding="utf-8")

    again = evolution.sync(config, feed)

    assert again.imported == ()
    assert again.skipped == ()  # the cursor means the feed offers nothing to skip
    assert again.pool_size == 1
    assert config.ledger_path.read_text(encoding="utf-8") == ledger_after_first


def test_a_rewound_cursor_still_imports_each_report_once(
    config: evolution.EvolutionConfig, tmp_path: Path
) -> None:
    """The cursor says how far the feed was inspected; the pool says what was
    imported. Losing the first does not corrupt the second."""
    feed = write_feed(tmp_path / "feed", [make_record(key="r1", sequence=1)])
    evolution.sync(config, feed)

    rewound = evolution.load_state(config)
    rewound.cursor = None
    evolution.save_state(config, rewound)
    again = evolution.sync(config, feed)

    assert again.skipped == ("r1",)
    assert again.imported == ()
    assert again.pool_size == 1
    assert evolution.load_state(config).superseded == {}


def test_a_second_evaluation_takes_the_slot_and_the_replaced_pack_is_kept(
    config: evolution.EvolutionConfig, tmp_path: Path
) -> None:
    """One task owns one pack. A second evaluation does not raise the task count
    (invariant 1); the pack it displaces moves to `superseded` whole, so its
    identity, hash and staged bundle are all still named (invariant 4)."""
    feed = write_feed(
        tmp_path / "feed",
        [
            make_record(key="r1", sequence=1, evaluation_id="eval-first", generated_at="2026-07-30T10:00:00Z"),
            make_record(key="r2", sequence=5, evaluation_id="eval-second", generated_at="2026-07-31T10:00:00Z"),
        ],
    )

    result = evolution.sync(config, feed)

    assert result.imported == ("r1",)
    assert result.reruns == ("r2",)
    assert result.pool_size == 1

    loaded = evolution.load_state(config)
    entry = loaded.pending[0]
    assert entry.report.report_key == "r2"  # the later evaluation is the task's evidence
    assert entry.report_keys() == {"r2"}
    replaced = loaded.superseded["r1"]
    assert replaced["superseded_by"] == "r2"
    assert replaced["repo_id"] == "repo-alpha"
    assert replaced["report"]["evaluation_id"] == "eval-first"
    assert (config.repo_root / replaced["report"]["artifacts_path"] / state.REPORT_JSON_FILENAME).is_file()


def test_a_superseded_pack_is_never_imported_again(
    config: evolution.EvolutionConfig, tmp_path: Path
) -> None:
    """Dropping the replaced report's key would take it out of
    `known_report_keys`, and every later sync that reaches it again would fetch
    and stage it as new. Losing the slot is a decision about the report, so the
    record of that decision is what keeps it decided."""
    feed = write_feed(
        tmp_path / "feed",
        [
            make_record(key="r1", sequence=1, generated_at="2026-07-30T10:00:00Z"),
            make_record(key="r2", sequence=5, generated_at="2026-07-31T10:00:00Z"),
        ],
    )
    evolution.sync(config, feed)

    rewound = evolution.load_state(config)
    assert "r1" in rewound.known_report_keys()
    rewound.cursor = None
    evolution.save_state(config, rewound)
    again = evolution.sync(config, feed)

    assert again.imported == ()
    assert again.reruns == ()
    assert sorted(again.skipped) == ["r1", "r2"]
    assert evolution.load_state(config).pending[0].report_keys() == {"r2"}


def test_the_survivor_is_the_later_evaluation_not_the_later_publication(
    config: evolution.EvolutionConfig, tmp_path: Path
) -> None:
    """orch-hub publishes a report only once the task it evaluated is archived,
    so a pack written while that task was still active is withheld and enters
    the feed *behind* packs generated after it. `sequence` is that release
    order. Where the two disagree the pool keeps the evaluation, not the
    release: ordering on `sequence` would hand the slot to whichever pack the
    publisher's gate happened to let out last."""
    feed = write_feed(
        tmp_path / "feed",
        [
            # Generated second, published first: its task was already archived.
            make_record(key="prompt", sequence=4, generated_at="2026-07-25T09:00:00Z"),
            # Generated first, withheld, and published at the position where it
            # became publishable.
            make_record(key="withheld", sequence=9, generated_at="2026-07-20T09:00:00Z"),
        ],
    )

    evolution.sync(config, feed)

    loaded = evolution.load_state(config)
    assert loaded.pending[0].report.report_key == "prompt"
    assert loaded.superseded["withheld"]["superseded_by"] == "prompt"


def test_generated_at_is_compared_as_an_instant_and_not_as_a_string(
    config: evolution.EvolutionConfig, tmp_path: Path
) -> None:
    """`generated_at` carries whatever offset its producer wrote — this
    repository's own pool holds `+00:00` where the fixtures hold `Z` — and
    lexical order across two offsets is not time order. Here the string
    comparison and the `sequence` tie-break both point at the arrival, and it is
    still half an hour older than what is pooled."""
    feed = write_feed(
        tmp_path / "feed",
        [
            make_record(key="pooled", sequence=1, generated_at="2026-07-25T09:00:00Z"),
            make_record(key="arrival", sequence=2, generated_at="2026-07-25T10:30:00+02:00"),
        ],
    )

    evolution.sync(config, feed)

    loaded = evolution.load_state(config)
    assert loaded.pending[0].report.report_key == "pooled"
    assert loaded.superseded["arrival"]["superseded_by"] == "pooled"


def test_two_packs_generated_at_one_instant_are_settled_by_sequence(
    config: evolution.EvolutionConfig, tmp_path: Path
) -> None:
    """`generated_at` decides, and `sequence` is the tie-break — so when the
    instants match, the tie-break is the whole answer. It has to be, in both
    directions: an answer that held only where the winner arrived second would
    be arrival order wearing a rule's name, which is the accident this pool
    stopped recording.

    A directory feed serves ascending sequences, so the reverse direction is a
    second repository whose pool already holds the higher position before the
    lower one reaches it."""

    together = "2026-07-30T10:00:00Z"
    early = make_record(key="published-early", sequence=2, generated_at=together)
    late = make_record(key="published-late", sequence=7, generated_at=together)

    evolution.sync(config, write_feed(tmp_path / "ascending", [early, late]))

    ascending = evolution.load_state(config)
    assert ascending.pending[0].report.report_key == "published-late"
    assert ascending.superseded["published-early"]["superseded_by"] == "published-late"

    mirror = evolution.load_config(make_repo(tmp_path / "mirror"))
    evolution.sync(mirror, write_feed(tmp_path / "late-only", [late]))
    rewound = evolution.load_state(mirror)
    rewound.cursor = None
    evolution.save_state(mirror, rewound)
    evolution.sync(mirror, write_feed(tmp_path / "descending", [early, late]))

    descending = evolution.load_state(mirror)
    assert descending.pending[0].report.report_key == "published-late"
    assert descending.superseded["published-early"]["superseded_by"] == "published-late"


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda record: record["source"].update(archived=False), reports.REASON_NOT_ARCHIVED),
        (lambda record: record["source"].update(completed=False), reports.REASON_NOT_COMPLETED),
        (lambda record: record["artifacts"].pop("semantic_report"), reports.REASON_MISSING_ARTIFACT),
        (lambda record: record.update(schema_version=99), reports.REASON_UNSUPPORTED_SCHEMA_VERSION),
        (lambda record: record["evaluator"].pop("model"), reports.REASON_SCHEMA_INVALID),
        (lambda record: record["artifacts"]["evidence"].update(sha256="b" * 64), reports.REASON_ARTIFACT_HASH_MISMATCH),
    ],
)
def test_ineligible_reports_are_recorded_and_kept_out_of_the_pool(
    config: evolution.EvolutionConfig, tmp_path: Path, mutate, reason: str
) -> None:
    record = make_record(key="bad", sequence=1)
    mutate(record)
    feed = write_feed(tmp_path / "feed", [record])

    result = evolution.sync(config, feed)

    assert result.rejected == (("bad", reason),)
    assert result.pool_size == 0
    persisted = evolution.load_state(config)
    assert persisted.rejected["bad"]["reason"] == reason
    assert json.loads(persisted.cursor) == [1, "bad", "bad.json"], "an inspected report still advances discovery"

    audit = ledger.read_records(config)
    assert audit[-1]["record_type"] == importer.RECORD_REJECTED
    assert audit[-1]["report_key"] == "bad"
    assert audit[-1]["detail"] == reason


def test_a_rejected_report_is_not_re_examined_after_a_cursor_rewind(
    config: evolution.EvolutionConfig, tmp_path: Path
) -> None:
    record = make_record(key="bad", sequence=1)
    record["source"]["archived"] = False
    feed = write_feed(tmp_path / "feed", [record])
    evolution.sync(config, feed)

    rewound = evolution.load_state(config)
    rewound.cursor = None
    evolution.save_state(config, rewound)
    again = evolution.sync(config, feed)

    assert again.rejected == ()
    assert again.skipped == ("bad",)


def test_a_feed_failure_leaves_the_cursor_where_the_last_page_committed(
    config: evolution.EvolutionConfig, tmp_path: Path
) -> None:
    """One page is one commit: an interrupted run re-fetches the page it never
    finished, and the already-known reports on it are skipped."""

    class FailsOnSecondPage(feed_module.DirectoryFeed):
        pages = 0

        def fetch_page(self, cursor, limit):
            type(self).pages += 1
            if type(self).pages > 1:
                raise evolution.FeedError("feed unavailable")
            return super().fetch_page(cursor, limit)

    write_feed(
        tmp_path / "feed",
        [make_record(key="r1", sequence=1), make_record(key="r2", sequence=2, task_id="2026-07-02-task")],
    )

    with pytest.raises(evolution.FeedError):
        evolution.sync(config, FailsOnSecondPage(tmp_path / "feed"), page_size=1)

    persisted = evolution.load_state(config)
    assert json.loads(persisted.cursor) == [1, "r1", "r1.json"]
    assert len(persisted.pending) == 1
    assert not config.lock_path.exists(), "the lock is released even when a run fails"

    finished = evolution.sync(config, feed_module.DirectoryFeed(tmp_path / "feed"), page_size=1)
    assert finished.imported == ("r2",)
    assert finished.pool_size == 2


# --- feed paging -------------------------------------------------------------


def test_records_sharing_a_sequence_are_all_served(config: evolution.EvolutionConfig, tmp_path: Path) -> None:
    """Sequence alone is not a position. Two records that repeat one would sit
    at the same cursor, and everything after the first would be skipped while
    the cursor advanced past it — reports dropped with no rejection recorded."""
    feed = write_feed(
        tmp_path / "feed",
        [
            make_record(key="r1", sequence=7),
            make_record(key="r2", sequence=7, repo_id="repo-beta", task_id="2026-07-02-task"),
        ],
    )

    result = evolution.sync(config, feed, page_size=1)

    assert sorted(result.imported) == ["r1", "r2"]
    assert result.pool_size == 2
    assert result.exhausted


def test_unusable_sequences_do_not_collapse_onto_one_position(
    config: evolution.EvolutionConfig, tmp_path: Path
) -> None:
    """Every record the schema rejects has sequence 0 for ordering purposes, so
    a whole page of them must still be served one by one and each rejection
    recorded."""
    first = make_record(key="bad-a", sequence=1)
    second = make_record(key="bad-b", sequence=1, task_id="2026-07-02-task")
    first["sequence"] = "not-a-sequence"
    second["sequence"] = 0
    feed = write_feed(tmp_path / "feed", [first, second])

    result = evolution.sync(config, feed, page_size=1)

    assert [key for key, _ in result.rejected] == ["bad-a", "bad-b"]
    assert result.exhausted
    assert set(evolution.load_state(config).rejected) == {"bad-a", "bad-b"}


def test_a_cursor_this_feed_never_issued_is_refused(tmp_path: Path) -> None:
    """Silently reinterpreting a foreign cursor is how a run resumes from a
    position that means something else."""
    feed = write_feed(tmp_path / "feed", [make_record(key="r1", sequence=1)])

    for cursor in ("1", '"1"', "[1,2,3]", "not json"):
        with pytest.raises(evolution.FeedError):
            feed.fetch_page(cursor, 10)


# --- state and lock ----------------------------------------------------------


def valid_state() -> dict:
    """The smallest complete current-version state. Written out rather than
    produced by a sync so each corruption case below differs in exactly one
    way."""

    reference = {
        "report_key": "r1",
        "sequence": 4,
        "evaluation_id": "eval-r1",
        "generated_at": "2026-07-30T10:00:00Z",
        "bundle_sha256": "a" * 64,
        "artifacts_path": ".ai-evolution/imported-artifacts/r1-0123456789abcdef",
    }
    return {
        "schema_version": 3,
        "cursor": None,
        "feed_exhausted": True,
        "pending": [
            {
                "repo_id": "repo-alpha",
                "task_id": "2026-07-01-task",
                "first_imported_at": "2026-07-30T10:00:01Z",
                "report": reference,
            }
        ],
        "rejected": {"r9": a_rejection()},
        "processed": {},
        "superseded": {},
    }


def legacy_state() -> dict:
    """The complete version-2 shape: two report slots per pooled task, and no
    `superseded` map to put the loser of the pair in. Kept as its own fixture
    because the upgrade is a supported read path, not a corruption case."""

    current = valid_state()
    entry = current["pending"][0]
    return {
        "schema_version": 2,
        "cursor": current["cursor"],
        "feed_exhausted": current["feed_exhausted"],
        "pending": [
            {
                "repo_id": entry["repo_id"],
                "task_id": entry["task_id"],
                "first_imported_at": entry["first_imported_at"],
                "primary": entry["report"],
                "reruns": [],
            }
        ],
        "rejected": current["rejected"],
        "processed": current["processed"],
    }


def a_supersession(**overrides) -> dict:
    """The record `state.superseded_record` writes for a replaced report."""

    return {
        "repo_id": "repo-alpha",
        "task_id": "2026-07-01-task",
        "report": other_report(key="r0", sequence=2),
        "superseded_by": "r1",
        "recorded_at": "2026-07-30T10:00:04Z",
        **overrides,
    }


def a_rejection(**overrides) -> dict:
    """The record `_record_rejection` writes for a refused report."""

    return {
        "reason": reports.REASON_NOT_ARCHIVED,
        "detail": "source task is not archived",
        "recorded_at": "2026-07-30T10:00:02Z",
        **overrides,
    }


def a_claim(**overrides) -> dict:
    """The record `batches._claim_reports` writes for a batched report."""

    return {
        "batch_id": "evolution-batch-0001",
        "recorded_at": "2026-07-30T10:00:03Z",
        **overrides,
    }


def other_report(*, key: str, sequence: int) -> dict:
    reference = {
        "report_key": "r1",
        "sequence": 4,
        "evaluation_id": "eval-r1",
        "generated_at": "2026-07-30T10:00:00Z",
        "bundle_sha256": "a" * 64,
        "artifacts_path": ".ai-evolution/imported-artifacts/r1-0123456789abcdef",
    }
    return {**reference, "report_key": key, "sequence": sequence}


def corrupt(mutate) -> str:
    data = valid_state()
    mutate(data)
    return json.dumps(data)


def corrupt_legacy(mutate) -> str:
    data = legacy_state()
    mutate(data)
    return json.dumps(data)


@pytest.mark.parametrize(
    "content",
    [
        "{ not json",
        json.dumps({"schema_version": 4, "cursor": None}),
        json.dumps({"schema_version": 3, "pending": [{"repo_id": "a"}]}),
        json.dumps({"schema_version": 3, "pending": "everything"}),
        # A state file reduced to its version header: every field that carries
        # evidence is gone, and reading the gaps as empty is the silent reset.
        json.dumps({"schema_version": 3}),
        corrupt(lambda data: data.pop("cursor")),
        corrupt(lambda data: data.pop("feed_exhausted")),
        corrupt(lambda data: data.pop("pending")),
        corrupt(lambda data: data.pop("rejected")),
        corrupt(lambda data: data.pop("processed")),
        corrupt(lambda data: data.pop("superseded")),
        corrupt(lambda data: data.update(unknown_field=1)),
        corrupt(lambda data: data.update(feed_exhausted="yes")),
        # Each version has its own complete shape, and a file mixing two says
        # nothing certain about either: version 1 has no `feed_exhausted`, and
        # neither 1 nor 2 has anywhere to put a `superseded` map.
        corrupt(lambda data: data.update(schema_version=1)),
        corrupt(lambda data: data.update(schema_version=2)),
        # Shapes that are the right Python types and still cannot be true.
        corrupt(lambda data: data["pending"][0]["report"].update(sequence=0)),
        corrupt(lambda data: data["pending"][0]["report"].update(bundle_sha256="not-a-digest")),
        corrupt(lambda data: data["pending"][0]["report"].update(generated_at="30 July 2026")),
        corrupt(lambda data: data["pending"][0]["report"].update(artifacts_path="../../etc")),
        corrupt(lambda data: data["pending"][0]["report"].update(artifacts_path="/etc/passwd")),
        # Repository-relative but outside the configured bundle root: a
        # reference the importer could not have written, aiming a later evidence
        # read at committed content.
        corrupt(lambda data: data["pending"][0]["report"].update(artifacts_path="evolution/cases")),
        # A sibling whose name merely starts the same way is not a child.
        corrupt(
            lambda data: data["pending"][0]["report"].update(
                artifacts_path=".ai-evolution/imported-artifacts-elsewhere/r1"
            )
        ),
        corrupt(lambda data: data["pending"][0]["report"].update(artifacts_path=".ai-evolution/imported-artifacts")),
        corrupt(lambda data: data["pending"][0].update(first_imported_at="yesterday")),
        # A superseded record holds a reference, and it is checked exactly like
        # a pooled one: the replaced pack is still staged evidence.
        corrupt(lambda data: data["superseded"].update(r0="r1")),
        corrupt(lambda data: data["superseded"].update(r0={})),
        corrupt(lambda data: data["superseded"].update(r0=a_supersession(note="extra"))),
        corrupt(lambda data: data["superseded"].update(r0=a_supersession(recorded_at="yesterday"))),
        corrupt(
            lambda data: data["superseded"].update(
                r0=a_supersession(report={**other_report(key="r0", sequence=2), "artifacts_path": "evolution/cases"})
            )
        ),
        # The map key is the identity; a record describing some other report
        # suppresses one key from discovery while accounting for another.
        corrupt(lambda data: data["superseded"].update(r0=a_supersession(report=other_report(key="r7", sequence=2)))),
        corrupt(lambda data: data["superseded"].update(r0=a_supersession(superseded_by="r0"))),
        # A replacement this state holds no report for: the record removes r0
        # from every future sync while nothing on disk holds what displaced it.
        corrupt(lambda data: data["superseded"].update(r0=a_supersession(superseded_by="r404"))),
        # Two records naming each other reach no pending or claimed report at
        # all, so both packs are out of the pool and out of the feed.
        corrupt(
            lambda data: data["superseded"].update(
                r0=a_supersession(superseded_by="r5"),
                r5=a_supersession(report=other_report(key="r5", sequence=3), superseded_by="r0"),
            )
        ),
        # One report holds one decision: a key that is both refused and replaced
        # says two contradictory things about why it left discovery.
        corrupt(lambda data: data["superseded"].update(r9=a_supersession(report=other_report(key="r9", sequence=2)))),
        corrupt(lambda data: data["rejected"].update(r1={"reason": "schema-invalid"})),
        corrupt(lambda data: data["rejected"].update(r9="schema-invalid")),
        # A rejection that kept its key and lost its evidence: the report stays
        # skipped forever with nothing left to say why it was refused.
        corrupt(lambda data: data["rejected"].update(r9={})),
        corrupt(lambda data: data["rejected"].update(r9={k: v for k, v in a_rejection().items() if k != "detail"})),
        corrupt(lambda data: data["rejected"].update(r9=a_rejection(reason="a-code-this-build-never-writes"))),
        corrupt(lambda data: data["rejected"].update(r9=a_rejection(recorded_at="yesterday"))),
        corrupt(lambda data: data["rejected"].update(r9=a_rejection(note="extra"))),
        # A claimed report names the batch that claimed it and when. An entry
        # that kept the key and lost either one removes the report from
        # discovery while no longer saying which cohort analyzed it.
        corrupt(lambda data: data["processed"].update(r8={})),
        corrupt(lambda data: data["processed"].update(r8={"batch_id": "b1", "recorded_at": "2026-07-30T10:00:03Z"})),
        corrupt(lambda data: data["processed"].update(r8=a_claim(batch_id="evolution-batch-1"))),
        corrupt(lambda data: data["processed"].update(r8={k: v for k, v in a_claim().items() if k != "recorded_at"})),
        corrupt(lambda data: data["processed"].update(r8=a_claim(recorded_at="yesterday"))),
        corrupt(lambda data: data["processed"].update(r8=a_claim(note="extra"))),
        corrupt(lambda data: data["processed"].update(r8="evolution-batch-0001")),
        # Well-formed, and still backed by nothing: no manifest under
        # `evolution/batches/` names r8, so the claim suppresses the report from
        # every future sync while no cohort accounts for it.
        corrupt(lambda data: data["processed"].update(r8=a_claim())),
        # The version-2 shape is upgraded on read, which is not the same as
        # being read loosely: what it says has to be true before it is reshaped.
        corrupt_legacy(
            lambda data: data["pending"][0]["reruns"].append(
                {**other_report(key="r2", sequence=2), "artifacts_path": "evolution/cases"}
            )
        ),
        # A rerun outranking `primary` meant the old file was already describing
        # its own pool wrongly; upgrading it would launder that away into a
        # shape where the disagreement can no longer be seen.
        corrupt_legacy(lambda data: data["pending"][0]["reruns"].append(other_report(key="r2", sequence=9))),
        corrupt_legacy(lambda data: data["pending"][0]["reruns"].append(other_report(key="r1", sequence=2))),
        corrupt_legacy(lambda data: data["pending"][0].update(reruns="none")),
        corrupt_legacy(lambda data: data["pending"][0].update(superseded={})),
    ],
)
def test_corrupt_state_is_reported_never_silently_reset(
    config: evolution.EvolutionConfig, content: str
) -> None:
    config.runtime_root.mkdir(parents=True, exist_ok=True)
    config.state_path.write_text(content, encoding="utf-8")

    with pytest.raises(evolution.StateError):
        evolution.load_state(config)


def test_the_complete_state_shape_loads(config: evolution.EvolutionConfig) -> None:
    """Guards the corruption cases above from passing vacuously."""
    config.runtime_root.mkdir(parents=True, exist_ok=True)
    config.state_path.write_text(json.dumps(valid_state()), encoding="utf-8")

    loaded = evolution.load_state(config)

    assert loaded.cursor is None
    assert loaded.feed_exhausted is True
    assert [entry.dedup_key for entry in loaded.pending] == [("repo-alpha", "2026-07-01-task")]
    assert loaded.to_json() == valid_state()


def test_a_superseded_record_loads_and_round_trips(config: evolution.EvolutionConfig) -> None:
    """Guards the superseded corruption cases above from passing vacuously, and
    pins that a replaced report is still a report this repository has decided
    about — the whole point of keeping the record."""
    config.runtime_root.mkdir(parents=True, exist_ok=True)
    holding = valid_state()
    holding["superseded"] = {"r0": a_supersession()}
    config.state_path.write_text(json.dumps(holding), encoding="utf-8")

    loaded = evolution.load_state(config)

    assert loaded.superseded == {"r0": a_supersession()}
    assert "r0" in loaded.known_report_keys()
    assert loaded.to_json() == holding


def test_a_version_1_state_is_upgraded_rather_than_refused(config: evolution.EvolutionConfig) -> None:
    """Version 1 predates `feed_exhausted`, so it cannot say whether its
    discovery drained the feed. The upgrade assumes it did not — one sync
    corrects that, where refusing the file would cost the cursor and the pool
    behind it, and re-import evidence the feed may no longer serve."""
    config.runtime_root.mkdir(parents=True, exist_ok=True)
    version_1 = {key: value for key, value in legacy_state().items() if key != "feed_exhausted"}
    version_1["schema_version"] = 1
    version_1["cursor"] = '[7, "r1", "r1.json"]'
    config.state_path.write_text(json.dumps(version_1), encoding="utf-8")

    loaded = evolution.load_state(config)

    assert loaded.cursor == '[7, "r1", "r1.json"]'
    assert loaded.feed_exhausted is False
    assert [entry.dedup_key for entry in loaded.pending] == [("repo-alpha", "2026-07-01-task")]
    # Rewritten at the current version on the next save, with nothing lost.
    evolution.save_state(config, loaded)
    assert json.loads(config.state_path.read_text(encoding="utf-8")) == {
        **valid_state(),
        "cursor": '[7, "r1", "r1.json"]',
        "feed_exhausted": False,
    }


def test_a_version_2_entry_keeps_both_its_reports_across_the_upgrade(
    config: evolution.EvolutionConfig,
) -> None:
    """Version 2 held two report slots per task and version 3 holds one, so the
    upgrade has a report to place. Dropping it would take its key out of
    `known_report_keys`, and the next sync that reaches it would fetch and stage
    it again — forever, since every later sync would do the same."""
    config.runtime_root.mkdir(parents=True, exist_ok=True)
    version_2 = legacy_state()
    version_2["pending"][0]["reruns"] = [other_report(key="r0", sequence=2)]
    config.state_path.write_text(json.dumps(version_2), encoding="utf-8")

    loaded = evolution.load_state(config)

    entry = loaded.pending[0]
    assert entry.report.report_key == "r1"
    assert loaded.superseded["r0"]["superseded_by"] == "r1"
    assert loaded.superseded["r0"]["report"] == other_report(key="r0", sequence=2)
    assert loaded.superseded["r0"]["repo_id"] == "repo-alpha"
    assert loaded.known_report_keys() == {"r0", "r1", "r9"}

    evolution.save_state(config, loaded)
    rewritten = json.loads(config.state_path.read_text(encoding="utf-8"))
    assert rewritten["schema_version"] == 3
    assert rewritten["pending"] == valid_state()["pending"]
    assert set(rewritten["superseded"]) == {"r0"}


def test_the_upgrade_re_picks_the_survivor_by_generated_at(config: evolution.EvolutionConfig) -> None:
    """Version 2 filed by `sequence`, which is publication order, so a v2 file
    can hold a `primary` that is the later-*published* half of a pair and the
    earlier-generated one. The upgrade applies the current rule rather than
    preserving that slot, which is what makes it a correction."""
    config.runtime_root.mkdir(parents=True, exist_ok=True)
    version_2 = legacy_state()
    # Published last, generated first — orch-hub withheld it while its task was
    # still active, so version 2 gave it the slot.
    version_2["pending"][0]["primary"] = {
        **other_report(key="withheld", sequence=9),
        "generated_at": "2026-07-20T09:00:00Z",
    }
    version_2["pending"][0]["reruns"] = [
        {**other_report(key="prompt", sequence=4), "generated_at": "2026-07-25T09:00:00Z"}
    ]
    config.state_path.write_text(json.dumps(version_2), encoding="utf-8")

    loaded = evolution.load_state(config)

    assert loaded.pending[0].report.report_key == "prompt"
    assert loaded.superseded["withheld"]["superseded_by"] == "prompt"


def test_two_legacy_entries_cannot_both_claim_one_report(config: evolution.EvolutionConfig) -> None:
    """One report has one decision, and version 2 stated that by putting every
    key an entry held in front of the global check — two tasks claiming one
    report was a file that would not load.

    Version 3 keeps only the survivor on the entry, so the upgrade carries the
    replaced packs to that check itself. Merging them into one map by key would
    let the second record for a contested key overwrite the first: the check
    would then be handed a state that no longer contradicts itself, because one
    task's claim to the evidence had already been dropped on the way in."""

    config.runtime_root.mkdir(parents=True, exist_ok=True)
    version_2 = legacy_state()
    first = version_2["pending"][0]
    first["reruns"] = [other_report(key="contested", sequence=2)]
    version_2["pending"].append(
        {
            "repo_id": first["repo_id"],
            "task_id": "2026-07-02-task",
            "first_imported_at": first["first_imported_at"],
            "primary": other_report(key="r2", sequence=4),
            "reruns": [other_report(key="contested", sequence=2)],
        }
    )
    config.state_path.write_text(json.dumps(version_2), encoding="utf-8")

    with pytest.raises(evolution.StateError) as refused:
        evolution.load_state(config)

    message = str(refused.value)
    assert "contested" in message
    # Both claims named: the point of the refusal is that neither was dropped.
    assert "2026-07-01-task" in message and "2026-07-02-task" in message


def test_a_processed_claim_loads_only_when_a_manifest_names_the_report(
    config: evolution.EvolutionConfig,
) -> None:
    """A claim is what removes a report from discovery, and the manifests are
    the membership record. A claim the manifests do not back would skip the
    report on every future sync while nothing on disk says which cohort
    analyzed it — so the claim is resolved, not merely parsed."""
    config.runtime_root.mkdir(parents=True, exist_ok=True)
    claimed = valid_state()
    claimed["processed"] = {"r8": a_claim()}
    config.state_path.write_text(json.dumps(claimed), encoding="utf-8")

    # A manifest that exists but names other reports is no more of a backing
    # than no manifest at all.
    write_manifest(config.batches_root, "evolution-batch-0001", ["r5", "r6"])
    with pytest.raises(evolution.StateError, match="names this report"):
        evolution.load_state(config)

    # And one that names r8 under a different id does not answer for this claim.
    write_manifest(config.batches_root, "evolution-batch-0002", ["r8"])
    with pytest.raises(evolution.StateError, match="evolution-batch-0002"):
        evolution.load_state(config)

    shutil.rmtree(config.batches_root / "evolution-batch-0001")
    write_manifest(config.batches_root, "evolution-batch-0001", ["r8"])

    loaded = evolution.load_state(config)

    assert loaded.processed == {"r8": a_claim()}
    assert "r8" in loaded.known_report_keys()


def test_a_damaged_rejection_is_refused_instead_of_skipping_the_report_forever(
    config: evolution.EvolutionConfig, tmp_path: Path
) -> None:
    """A rejection is evidence: the reason, the diagnostic, and when it was
    decided. An entry that keeps only the key still removes the report from
    discovery on the next cursor rewind, so a loader that accepts one has thrown
    the evidence away and hidden that it did."""
    record = make_record(key="bad", sequence=1)
    record["source"]["archived"] = False
    feed = write_feed(tmp_path / "feed", [record])
    evolution.sync(config, feed)

    # What the importer writes is exactly what the loader requires; this round
    # trip is what keeps the two in step.
    assert set(evolution.load_state(config).rejected["bad"]) == set(a_rejection())

    damaged = json.loads(config.state_path.read_text(encoding="utf-8"))
    damaged["rejected"]["bad"] = {}
    config.state_path.write_text(json.dumps(damaged), encoding="utf-8")

    with pytest.raises(evolution.StateError):
        evolution.load_state(config)


def test_state_round_trips_through_disk(config: evolution.EvolutionConfig, tmp_path: Path) -> None:
    feed = write_feed(tmp_path / "feed", [make_record(key="r1", sequence=1)])
    evolution.sync(config, feed)

    persisted = evolution.load_state(config)

    assert persisted.to_json() == json.loads(config.state_path.read_text(encoding="utf-8"))


def test_a_second_writer_is_refused_while_a_run_holds_the_lock(config: evolution.EvolutionConfig) -> None:
    with evolution.single_writer_lock(config):
        with pytest.raises(evolution.LockError) as raised:
            with evolution.single_writer_lock(config):
                pass
        assert "pid" in str(raised.value)

    assert not config.lock_path.exists()


def test_artifact_directory_names_never_escape_the_runtime_root() -> None:
    name = state.artifacts_dir_name("../../etc/passwd")

    assert "/" not in name and not name.startswith(".")
    assert name != state.artifacts_dir_name("../../etc/passwdX")


# --- ledger ------------------------------------------------------------------


def test_ledger_appends_schema_valid_records_and_keeps_the_existing_shape(
    config: evolution.EvolutionConfig, tmp_path: Path
) -> None:
    shutil.copy(REPO_ROOT / "evolution" / "ledger.jsonl", config.ledger_path)
    original = config.ledger_path.read_text(encoding="utf-8")

    ledger.append_records(config, [ledger.build_record(importer.RECORD_IMPORTED, report_key="r1", task_id="t1")])

    text = config.ledger_path.read_text(encoding="utf-8")
    assert text.startswith(original)
    assert text.endswith("\n")
    appended = json.loads(text.splitlines()[-1])
    assert list(appended) == ["record_type", "schema_version", "recorded_at", "report_key", "task_id"]


def test_ledger_refuses_a_record_the_schema_does_not_define(config: evolution.EvolutionConfig) -> None:
    with pytest.raises(evolution.ValidationError):
        ledger.append_records(config, [ledger.build_record("report-invented")])

    assert config.ledger_path.read_text(encoding="utf-8") == ""


def test_ledger_refuses_to_append_onto_a_truncated_final_record(config: evolution.EvolutionConfig) -> None:
    config.ledger_path.write_text('{"record_type":"report-imported"', encoding="utf-8")

    with pytest.raises(evolution.LedgerError):
        ledger.append_records(config, [ledger.build_record(importer.RECORD_IMPORTED, report_key="r1")])


# --- nothing raw reaches Git -------------------------------------------------


def test_the_runtime_root_is_ignored_by_this_repository(config: evolution.EvolutionConfig) -> None:
    """Raw bundles and the pending pool are runtime data (invariant 11). The
    ignore rule is what keeps them out of Git, so it is asserted against the
    storage location the config actually resolves."""
    ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert f"/{config.storage.runtime_root}/" in ignored
    assert config.storage.imported_artifacts.startswith(config.storage.runtime_root + "/")


def test_sync_touches_only_the_ledger_inside_the_versioned_tree(
    config: evolution.EvolutionConfig, repo: Path, tmp_path: Path
) -> None:
    feed = write_feed(tmp_path / "feed", [make_record(key="r1", sequence=1)])
    before = snapshot(repo / "evolution")

    evolution.sync(config, feed)

    after = snapshot(repo / "evolution")
    assert set(after) == set(before)
    assert [path for path in after if after[path] != before[path]] == ["ledger.jsonl"]


def test_no_rejected_feed_value_reaches_the_versioned_ledger(
    config: evolution.EvolutionConfig, tmp_path: Path
) -> None:
    """A rejection diagnostic quotes the value that caused it, and the feed is
    not this repository's to publish (invariant 11). The ledger gets the
    bounded reason code; the diagnostic stays in ignored runtime state, where
    the operator can still read it."""
    record = make_record(key="r1", sequence=1)
    record["artifacts"]["evidence"]["sha256"] = "SECRET-TOKEN-abc123"
    feed = write_feed(tmp_path / "feed", [record])

    evolution.sync(config, feed)

    assert b"SECRET-TOKEN" not in config.ledger_path.read_bytes()
    assert "SECRET-TOKEN" in evolution.load_state(config).rejected["r1"]["detail"]
    audit = ledger.read_records(config)
    assert audit[-1]["detail"] == reports.REASON_SCHEMA_INVALID


def test_an_import_record_carries_only_locally_authored_detail(
    config: evolution.EvolutionConfig, tmp_path: Path
) -> None:
    feed = write_feed(tmp_path / "feed", [make_record(key="r1", sequence=1, repo_id="repo-alpha")])

    evolution.sync(config, feed)

    audit = ledger.read_records(config)
    assert audit[-1]["detail"] == importer.STATUS_NEW
    assert reports.publishable_reason("invented-by-nobody") == reports.UNKNOWN_REASON


def test_no_report_body_reaches_the_versioned_ledger(config: evolution.EvolutionConfig, tmp_path: Path) -> None:
    secret = b'{"layer": "L2", "findings": ["SECRET-TOKEN-abc123"]}'
    bodies = {**ARTIFACT_BODIES, "semantic_report": secret}
    feed = write_feed(tmp_path / "feed", [make_record(key="r1", sequence=1, bodies=bodies)], bodies=bodies)

    evolution.sync(config, feed)

    assert b"SECRET-TOKEN" not in config.ledger_path.read_bytes()
    assert b"SECRET-TOKEN" in (config.repo_root / evolution.load_state(config).pending[0].report.artifacts_path
                               / state.ARTIFACTS_SUBDIR / "semantic_report").read_bytes()
