"""The portfolio read model: the schema, and the documents it has to describe.

The portfolio is the reading a console renders the whole history from. Its
implementation comes later; what this suite holds is the half that has to be
settled first — the contract a surface writes against, and the worked documents
that prove the contract can describe every state the lifecycle actually reaches.

Three properties, and they are the reasons this file exists before the builder
does:

**The schema is enforceable here.** `mandrel/evolution/schema.py` implements a
subset of JSON Schema and refuses anything outside it rather than ignoring it, so
a schema using a keyword nobody implemented would report "valid" for data nothing
checked. Every subschema is walked for that, including the branches no fixture
reaches.

**Every terminal variant has a document.** A shape that describes only the states
in play when it was written is one that gets extended by whoever first hits a
state it cannot say — which is how a read model acquires a second spelling for
the same fact. The five endings the lifecycle has are each written down.

**One experiment shape serves both.** The next-action surface projects the open
attempt through one shape and history through a narrower one, which is right for
the question it answers and is exactly what a reader reconstructing history
cannot use. Here a superseded attempt and the open attempt that replaced it are
the same object with a different `decision`, and that is asserted rather than
left to the eye.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator, Mapping

import pytest

from mandrel.evolution.schema import SUPPORTED_KEYWORDS, load_schema, validate

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "evolution" / "schemas" / "portfolio.schema.json"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "portfolio"

FIXTURES = sorted(path.name for path in FIXTURE_DIR.glob("*.json"))
# The five ways something ends here, each with a document that shows it. Named
# rather than counted: a suite that only asserted "five fixtures" would stay
# green when one of them was rewritten into a second copy of another.
TERMINAL_FIXTURES = {
    "abandoned": "experiment-abandoned.json",
    "superseded": "experiment-superseded.json",
    "promoted": "batch-promoted.json",
    "no-change": "batch-no-change.json",
    "rolled-back": "batch-rolled-back.json",
}


def read(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def subschemas(node: Any, path: str = "$") -> Iterator[tuple[str, Mapping[str, Any]]]:
    """Every schema object in the document, wherever it is nested."""

    if not isinstance(node, Mapping):
        return
    if "type" in node or "$ref" in node or "enum" in node or "const" in node or "$defs" in node:
        yield path, node
    for key in ("properties", "$defs"):
        for name, child in (node.get(key) or {}).items():
            yield from subschemas(child, f"{path}.{key}.{name}")
    if isinstance(node.get("items"), Mapping):
        yield from subschemas(node["items"], f"{path}.items")


def test_schema_uses_only_implemented_keywords() -> None:
    """Nothing in the schema is a rule this repository's validator drops.

    The validator raises on an unknown keyword rather than skipping it, but only
    along the branches an instance reaches. This walks all of them, so a `oneOf`
    added to a path no fixture exercises fails here instead of silently checking
    nothing in production.
    """

    schema = load_schema(SCHEMA_PATH)
    for path, node in subschemas(schema):
        unsupported = set(node) - SUPPORTED_KEYWORDS
        assert not unsupported, f"{path} uses unimplemented keywords: {sorted(unsupported)}"


def test_every_reference_resolves() -> None:
    schema = load_schema(SCHEMA_PATH)
    defined = set(schema["$defs"])
    for path, node in subschemas(schema):
        ref = node.get("$ref")
        if ref is None:
            continue
        assert ref.startswith("#/$defs/"), f"{path}: only local $defs references are implemented, got {ref!r}"
        assert ref.rsplit("/", 1)[1] in defined, f"{path}: unresolvable reference {ref!r}"


def test_every_definition_is_reachable() -> None:
    """A `$def` nothing points at describes nothing.

    It is the shape a read model drifts into: a subject gets projected somewhere
    else, its definition stays, and the next author writes against a document
    that is never emitted.
    """

    schema = load_schema(SCHEMA_PATH)
    referenced = {node["$ref"].rsplit("/", 1)[1] for _, node in subschemas(schema) if "$ref" in node}
    assert set(schema["$defs"]) - referenced == set()


@pytest.mark.parametrize("name", FIXTURES)
def test_fixture_validates(name: str) -> None:
    assert validate(read(name), load_schema(SCHEMA_PATH)) == []


@pytest.mark.parametrize("name", FIXTURES)
def test_document_and_payload_agree(name: str) -> None:
    """Exactly one payload slot is filled, and it is the one `document` names.

    The pairing the schema states in prose and the implemented subset cannot
    express. A document carrying both would let a reader take either and two
    readers take different ones.
    """

    payload = read(name)
    kind = payload["document"]
    filled = {key for key in ("index", "detail") if payload[key] is not None}
    assert filled == {kind}
    if kind == "detail":
        detail = payload["detail"]
        subjects = {key for key in ("batch", "proposal") if detail[key] is not None}
        assert subjects == {detail["kind"]}
        assert (detail["id"] is None) == (detail["kind"] == "proposal")


@pytest.mark.parametrize("name", FIXTURES)
def test_both_tokens_are_carried(name: str) -> None:
    """Every document says what state it is of, in both halves.

    The pair is the point: the durable token alone cannot say that a reading's
    offered actions still stand, because `seal-round` turns on a task file no
    writer of this lifecycle touches.
    """

    snapshot = read(name)["snapshot"]
    assert snapshot["state_revision"] and snapshot["observations_revision"]
    assert snapshot["state_revision"] != snapshot["observations_revision"]


@pytest.mark.parametrize("ending,name", sorted(TERMINAL_FIXTURES.items()))
def test_terminal_variant_has_a_document(ending: str, name: str) -> None:
    batch = read(name)["detail"]["batch"]
    outcome = batch["outcome"]["outcome"] if batch["outcome"] else None
    decisions = {experiment["decision"]["outcome"] for experiment in batch["experiments"] if experiment["decision"]}
    assert ending in decisions | {outcome}


def test_one_experiment_shape_serves_open_and_terminal() -> None:
    """A terminal attempt and the open one that replaced it are the same object.

    Same keys, and the only difference is what happened: `decision` is null on
    the open one and states the ending on the other. A history projected through
    a narrower shape could not be asked why an attempt ended where it did.
    """

    experiments = read("experiment-superseded.json")["detail"]["batch"]["experiments"]
    terminal, open_ = experiments
    assert set(terminal) == set(open_)
    assert terminal["decision"]["outcome"] == "superseded"
    assert terminal["decision"]["superseded_by"] == open_["experiment_id"]
    assert terminal["open"] is False
    assert open_["decision"] is None and open_["open"] is True


def test_terminal_replay_history_is_readable() -> None:
    """What measured an attempt survives the attempt ending.

    Every run against every round, the request that never became one, and the
    positions given up — on attempts that are over, which is the half no other
    surface reads and exactly when somebody asks what a promotion was argued
    from.
    """

    experiments = read("batch-promoted.json")["detail"]["batch"]["experiments"]
    promoted = next(item for item in experiments if item["decision"]["outcome"] == "promoted")
    runs = promoted["replays"]["runs"]
    assert {run["round"] for run in runs} == {1, 2}
    assert [run["outcome"] for run in runs] == ["completed", "failed", "completed"]
    assert promoted["replays"]["withdrawn"]

    superseded = read("experiment-superseded.json")["detail"]["batch"]["experiments"][0]
    assert superseded["replays"]["request"] is not None, "a request outstanding when an attempt ended is still a fact"


def test_a_rolled_back_batch_reads_its_release_both_ways() -> None:
    """The reading answered while the release stood; the line no longer carries it.

    The one pair a reader must not collapse: `assessed.standing` is the question
    the reading answered, and `assessed.source_line.standing` is what the line
    holds now. A settlement of `rolled-back` is written onto a reading formed
    before the inverse commit landed, so on the path a regression finding
    ordinarily ends, the two disagree.
    """

    batch = read("batch-rolled-back.json")["detail"]["batch"]
    release = batch["release"]
    assert release["assessed"]["standing"] is True
    assert release["assessed"]["source_line"]["standing"] is False
    assert release["decision"]["settlement"] == "rolled-back"
    assert release["decision"]["rollback_revision"] == release["assessed"]["source_line"]["rollback_revision"]
    assert batch["outcome"]["outcome"] == "rolled-back"
    assert batch["outcome"]["experiment_id"] is None and batch["outcome"]["promotion"] is None
    assert batch["experiments"] == [], "a batch ended by a rollback admitted nothing"


def test_comparability_separates_a_gap_from_a_difference() -> None:
    """`unstated` and `differing` are different answers about the release.

    One is evidence that the sides differ by more than the release; the other is
    the feed not publishing a value at all, which says nothing either way. A
    reader handed only `incoherent` would report a publication gap as a finding.
    """

    comparability = read("batch-rolled-back.json")["detail"]["batch"]["release"]["comparability"]
    assert set(comparability["incoherent"]) == set(comparability["unstated"]) | set(comparability["differing"])
    assert not set(comparability["unstated"]) & set(comparability["differing"])
    assert comparability["coherent"] is False


def test_local_observations_are_marked_as_such() -> None:
    """The volatile readings are present and distinguishable from durable ones.

    `completion_observed_at` is what a seal recorded and never moves again;
    `file_complete` is what this machine sees in `.ai-tasks/` right now, and is
    null wherever nothing was asked — which is the durable observation already
    standing, or no task tree to consult.
    """

    batch = read("experiment-superseded.json")["detail"]["batch"]
    tasks = batch["experiments"][1]["rounds"][0]["tasks"]
    observed = [task for task in tasks if task["completion_observed_at"] is not None]
    assert observed and all(task["file_complete"] is None for task in observed)
    assert {task["file_complete"] for task in tasks if task["completion_observed_at"] is None} == {True, False}
    assert batch["evidence"]["local"] < batch["evidence"]["report_count"], "a clone may hold fewer bundles than the manifest claims"


def test_the_index_carries_no_actions() -> None:
    """The series says what is; it does not offer what to do.

    An action offered from here would be offered from a reading that never asked
    whether the state permits it. That question is `status`'s, and the tokens are
    what pair the two answers.
    """

    for name in FIXTURES:
        payload = read(name)
        assert "allowed_actions" not in payload
        assert "allowed_actions" not in json.dumps(payload)


def test_the_index_grows_by_one_row_per_batch() -> None:
    """The bound, asserted as the shape it is rather than as a size.

    Everything whose size is a batch's own work — its reports, its gate, its
    attempts, its rounds, its reading — is behind a detail read of that batch, so
    the series grows by one row per freeze and by nothing else.
    """

    index = read("index.json")["index"]
    per_batch = {"reports", "not_selected", "experiments", "gate", "analysis", "evidence", "selection"}
    for row in index["batches"]:
        assert per_batch & set(row) == set()
        # One fixed record is allowed through: a promotion is a property of the
        # row rather than a collection of that batch's work.
        rest = {key: value for key, value in row.items() if key != "promotion"}
        assert all(value is None or isinstance(value, (str, int, bool)) for value in rest.values())
        assert isinstance(row["release"], str), "the row says how far the reading got, never what it said"
    detail = read("batch-promoted.json")["detail"]["batch"]
    assert per_batch <= set(detail)
    assert isinstance(detail["release"], (dict, type(None)))
