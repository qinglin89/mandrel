"""The versioned contracts of the batch/experiment lineage.

Schema files are the contract (`schema.py`), so these are the tests that keep
one from claiming a rule nothing enforces: every keyword a schema uses has to be
inside the implemented validator subset, or the validator raises the first time
anything is checked against it — at write time, in an operation that has already
started.

The instances here are deliberately hand-written rather than produced by the
package. Nothing writes experiment records yet, and a test that built its
fixture from the writer would only prove the writer agrees with itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from evolution_fixtures import REPO_ROOT

from ai_native_deployment.evolution import ledger, schema

SCHEMAS = REPO_ROOT / "evolution" / "schemas"

EXPERIMENT_ID = "evolution-batch-0007-exp-02"
BATCH_ID = "evolution-batch-0007"
BASE = "a" * 40
CANDIDATE = "b" * 40
PROMOTION = "c" * 40
DRAFT_SHA = "d" * 64


def experiment(**overrides: Any) -> dict[str, Any]:
    """An open experiment on its second round: round 1 closed with a pinned
    candidate, round 2 still taking tasks."""

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
                    }
                ],
                "closed_at": "2026-08-03T09:00:00Z",
                "candidate_revision": CANDIDATE,
            },
            {
                "round": 2,
                "opened_at": "2026-08-03T09:00:00Z",
                "reason": "replay showed no convergence gain",
                "tasks": [],
                "closed_at": None,
                "candidate_revision": None,
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


# --- experiment record -------------------------------------------------------


def test_open_experiment_with_a_closed_and_an_open_round_validates() -> None:
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


def test_a_round_is_numbered_from_one() -> None:
    record = experiment()
    record["rounds"][0]["round"] = 0
    assert errors(record, "experiment.schema.json")


def test_a_closed_round_pins_a_revision_shaped_candidate() -> None:
    record = experiment()
    record["rounds"][0]["candidate_revision"] = "HEAD"
    assert errors(record, "experiment.schema.json")


# --- batch outcome -----------------------------------------------------------


def test_no_change_outcome_validates_without_a_candidate() -> None:
    assert errors(outcome(), "batch-outcome.schema.json") == []


def test_promoted_outcome_names_the_experiment_and_the_promotion_revision() -> None:
    promoted = outcome(
        outcome="promoted",
        reason="replay showed fewer remediation rounds",
        experiment_id=EXPERIMENT_ID,
        promotion_revision=PROMOTION,
    )
    assert errors(promoted, "batch-outcome.schema.json") == []


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
