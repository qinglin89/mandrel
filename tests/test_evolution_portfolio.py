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

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Mapping

import pytest

from mandrel.evolution.schema import SUPPORTED_KEYWORDS, load_schema, validate

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "evolution" / "schemas" / "portfolio.schema.json"
SCHEMA_DIR = SCHEMA_PATH.parent
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


# One field of a persisted record that this reading projects, the edge its own
# contract accepts, and where that contract states it. The value is the same
# empty string in every row on purpose: it is the variant a nullable field
# acquires by being written rather than by being designed, which is exactly why
# narrowing it to `null | non-empty` reads as tidying and is not.
SOURCE_VALID_EDGES: tuple[tuple[list[Any], Any, str, tuple[str, ...], Any], ...] = (
    (
        ["detail", "batch", "reports", 0, "evaluator", "rubric_revision"],
        "",
        "batch-manifest.schema.json",
        ("$defs", "evaluator"),
        {"backend": "claude", "model": "opus-5", "schema_version": 1, "rubric_revision": ""},
    ),
    (
        ["detail", "batch", "experiments", 0, "replays", "runs", 0, "evaluator", "rubric_revision"],
        "",
        "experiment-replays.schema.json",
        ("$defs", "evaluator"),
        {"backend": "claude", "model": "claude-opus-5", "rubric_revision": ""},
    ),
    (
        ["detail", "batch", "release", "counterfactual", "run", "evaluator", "rubric_revision"],
        "",
        "release-assessment.schema.json",
        ("$defs", "counterfactual", "properties", "evaluator"),
        {"backend": "claude", "model": "claude-opus-5", "rubric_revision": ""},
    ),
    (
        ["detail", "batch", "reports", 0, "protocol", "effective_revision"],
        "",
        "batch-manifest.schema.json",
        ("$defs", "provenance", "properties", "effective_revision"),
        "",
    ),
    (
        ["detail", "batch", "reports", 0, "protocol", "deploy_lock_hash"],
        "",
        "batch-manifest.schema.json",
        ("$defs", "provenance", "properties", "deploy_lock_hash"),
        "",
    ),
    (
        ["detail", "batch", "experiments", 0, "base_release_ref"],
        "",
        "experiment.schema.json",
        ("properties", "base_release_ref"),
        "",
    ),
)


def read(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def place(payload: dict[str, Any], route: list[Any], value: Any) -> dict[str, Any]:
    """The payload with one field replaced, addressed the way a finding is."""

    node: Any = payload
    for step in route[:-1]:
        node = node[step]
    node[route[-1]] = value
    return payload


def source_subschema(filename: str, route: tuple[str, ...]) -> dict[str, Any]:
    """One field or block of a source contract, validatable on its own.

    `$defs` travels with it because the validator resolves every `$ref` from the
    root it is handed, and these blocks reference their own file's.
    """

    root = load_schema(SCHEMA_DIR / filename)
    node: Any = root
    for step in route:
        node = node[step]
    return {**node, "$defs": root.get("$defs", {})}


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
        subjects = {key for key in ("batch", "proposal", "pool") if detail[key] is not None}
        assert subjects == {detail["kind"]}
        # A batch is named by the id the index stated it under; the other two
        # are the one offer and the one pool, and neither has an id to state.
        assert (detail["id"] is None) == (detail["kind"] in {"proposal", "pool"})


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
    assert [run["result"]["outcome"] for run in runs] == ["completed", "failed", "completed"]
    assert promoted["replays"]["withdrawn"]

    superseded = read("experiment-superseded.json")["detail"]["batch"]["experiments"][0]
    assert superseded["replays"]["request"] is not None, "a request outstanding when an attempt ended is still a fact"


def test_a_run_carries_what_would_reproduce_it() -> None:
    """The evidence, not a verdict on it.

    A promotion is argued from numbers, and a reader asking what was measured
    needs the cohort they were measured over, who judged them, what drove the
    run and how it was configured. Without those the reading names an outcome
    and sends its consumer to `replays.json` for everything that outcome means —
    which is the second reader of the lineage this document exists to remove.
    """

    runs = read("batch-promoted.json")["detail"]["batch"]["experiments"][1]["replays"]["runs"]
    measured, failed = runs[0], runs[1]

    assert measured["cases"]["count"] == 24 and measured["cases"]["case_set_sha256"]
    assert measured["evaluator"]["backend"] and measured["evaluator"]["model"]
    assert measured["harness"]["revision"] and measured["harness"]["config_sha256"]
    assert measured["expectation"], "what it was expected to show, written before it showed anything"
    assert [item["metric"] for item in measured["result"]["metrics"]] == [
        "review-turns",
        "convergence",
        "quota",
    ]
    assert measured["result"]["regressions"] == [], "an empty array is the claim that there were none"

    # A failed run measured no cohort, so it carries no measurement and says why
    # in `detail` — the half a reader otherwise has to infer from an absence.
    assert failed["result"]["outcome"] == "failed"
    assert failed["result"]["metrics"] == [] and failed["result"]["detail"]

    # And the last one says what it could not settle, which is the thing a
    # promotion decided from close numbers is read back against.
    assert runs[2]["result"]["ambiguity"]

    withdrawn = read("batch-promoted.json")["detail"]["batch"]["experiments"][1]["replays"]["withdrawn"][0]
    assert withdrawn["tree"] and withdrawn["merge_input_revision"], (
        "a position given up still names the integration it pinned: the run may be going at a harness "
        "this repository will never hear from"
    )


def test_the_valid_nullable_histories_are_worked_documents() -> None:
    """Two shapes the records permit and a reading must not reject.

    A run driven from a detached revision records the commit and no ref, and a
    harness that issued no name for a run it finished leaves the handle null. A
    projection that required either would be a history no reading could publish
    — and the way that is found is a promotion nobody can read, long after the
    run itself.
    """

    runs = read("batch-promoted.json")["detail"]["batch"]["experiments"][1]["replays"]["runs"]
    detached = [run for run in runs if run["merge_input_ref"] is None]
    nameless = [run for run in runs if run["harness"]["handle"] is None and run["result"] is not None]
    assert detached and nameless

    counterfactual = read("batch-rolled-back.json")["detail"]["batch"]["release"]["counterfactual"]["run"]
    assert counterfactual["harness"]["handle"] is None and counterfactual["result"]["outcome"] == "completed"


@pytest.mark.parametrize(
    "route, value, filename, source_route, instance",
    SOURCE_VALID_EDGES,
    ids=[".".join(str(step) for step in row[0]) for row in SOURCE_VALID_EDGES],
)
def test_a_source_valid_value_is_not_narrowed(
    route: list[Any], value: Any, filename: str, source_route: tuple[str, ...], instance: Any
) -> None:
    """Every variant a record holds is a variant this reading can publish.

    A projection may say less than the record it projects; it may not accept
    less. A nullable field the source writes empty and this one refuses is a
    history that exists on disk and has no reading — found, months later, as a
    batch a console cannot render, rather than as the line that narrowed it.

    So the premise is asserted rather than assumed: each edge is validated
    against the contract it is projected from and then against this one. A row
    that stops holding because a source tightened is a row to delete, and one
    that stops holding because this narrowed is the finding.
    """

    assert validate(instance, source_subschema(filename, source_route)) == [], (
        f"premise: {filename} accepts this, so the projection has to"
    )
    payload = read("batch-no-change.json")
    assert validate(place(payload, route, value), load_schema(SCHEMA_PATH)) == []


def test_a_zero_byte_draft_is_a_reading_and_not_a_refusal() -> None:
    """The gate lists a file, not a valid taskfile.

    `lineage` reports every `*.md` under `proposed-tasks/` whose stem could be a
    draft id as waiting, and reads nothing of the bytes; admission is what asks
    whether they are a taskfile at all. So an empty file is a waiting draft, and
    the operator holding a proposal nothing can be admitted from is exactly the
    one who has to see it.
    """

    payload = read("batch-no-change.json")
    payload["detail"]["batch"]["gate"]["drafts"].append(
        {
            "draft_id": "zero-byte-proposal",
            "state": "waiting",
            "experiment_id": None,
            "decided_at": None,
            "reason": None,
            "recorded_sha256": None,
            "content": "",
            "content_sha256": hashlib.sha256(b"").hexdigest(),
        }
    )
    assert validate(payload, load_schema(SCHEMA_PATH)) == []


def test_identity_fields_are_checked_and_not_merely_present() -> None:
    """The identities a consumer is told it may rely on are enforced here.

    A hash-shaped field that accepts any non-empty string protects nothing: the
    records these project require 64 hex characters for a digest and 40 for a
    Git object id, and a reading that accepted `t000` would publish an identity
    nothing can be looked up by. Asserted as refusals, because a pattern that is
    present and wrong fails no fixture.
    """

    schema = load_schema(SCHEMA_PATH)
    assert validate(read("batch-promoted.json"), schema) == []

    weakened = [
        (["detail", "batch", "experiments", 1, "replays", "runs", 0, "tree"], "t000"),
        (["detail", "batch", "outcome", "promotion", "tree"], "not-a-tree"),
        (["detail", "batch", "reports", 0, "bundle_sha256"], "deadbeef"),
        (["detail", "batch", "experiments", 0, "rounds", 0, "tasks", 0, "draft_sha256"], "1234"),
        (["detail", "batch", "gate", "drafts", 0, "content_sha256"], "abc"),
        (["detail", "batch", "experiments", 1, "replays", "runs", 0, "cases", "case_set_sha256"], "aa"),
        (["detail", "batch", "experiments", 1, "replays", "runs", 0, "harness", "config_sha256"], "aa"),
        (["detail", "batch", "experiments", 1, "replays", "withdrawn", 0, "merge_input_revision"], "beef"),
    ]
    for route, value in weakened:
        payload = read("batch-promoted.json")
        node = payload
        for step in route[:-1]:
            node = node[step]
        node[route[-1]] = value
        assert validate(payload, schema), f"{'.'.join(str(step) for step in route)} accepted {value!r}"


def test_the_gate_carries_the_drafts_themselves() -> None:
    """What a person is asked to decide, rather than the name of it.

    A console showing an operator a draft id and asking for an admission has
    sent them to `proposed-tasks/` to read the proposal — a second reader of the
    batch directory, and the one place in this lifecycle where a human decision
    is actually taken.
    """

    gate = read("batch-promoted.json")["detail"]["batch"]["gate"]
    by_id = {item["draft_id"]: item for item in gate["drafts"]}
    assert {item["state"] for item in gate["drafts"]} == {"consumed", "declined"}

    admitted = by_id["prefetch-injection"]
    assert admitted["content"].startswith("---\n"), "the taskfile itself"
    assert admitted["content_sha256"] == admitted["recorded_sha256"], "unedited since it was admitted"

    # The decided draft whose file is gone: reported rather than refused, and the
    # record still pins what was decided.
    gone = by_id["handoff-shape"]
    assert gone["content"] is None and gone["content_sha256"] is None
    assert gone["recorded_sha256"] and gone["state"] == "consumed"

    # And the one edited after the decision that turned it down, which is only
    # visible because the two hashes are stated apart.
    edited = by_id["loader-rewrite"]
    assert edited["state"] == "declined" and edited["reason"]
    assert edited["content_sha256"] != edited["recorded_sha256"]

    # What an admission pinned is one fact, and the two places it appears agree.
    pinned = {
        task["draft_id"]: task["draft_sha256"]
        for experiment in read("batch-promoted.json")["detail"]["batch"]["experiments"]
        for rnd in experiment["rounds"]
        for task in rnd["tasks"]
    }
    assert all(by_id[draft_id]["recorded_sha256"] == sha for draft_id, sha in pinned.items())

    superseded = read("experiment-superseded.json")["detail"]["batch"]["gate"]
    assert superseded["unusable"] == ["notes.txt"], "a file that could never be a draft waits for no decision"


def test_the_pool_is_a_subject_of_its_own() -> None:
    """The entries behind the counts, and nothing that grows with history.

    The index says how many tasks are waiting and what the policy asks for; a
    surface rendering the pool needs which tasks those are and what evidence
    each stands on. It is bounded by the pool rather than by history — a freeze
    claims its members and the pool empties behind them — which is why the
    permanent rejections and the superseded packs, both of which accumulate with
    every sync forever, are deliberately not here.
    """

    entries = read("pool.json")["detail"]["pool"]["entries"]
    assert len({(item["repo_id"], item["task_id"]) for item in entries}) == len(entries), "one entry per task"
    assert all(len(item["bundle_sha256"]) == 64 for item in entries)
    assert {item["staged"] for item in entries} == {True, False}, (
        "whether the body is on this machine is the volatile half; the hash is not"
    )
    # The pool's own counts stay on the index row, and the entries stay here.
    assert set(read("index.json")["index"]["pool"]) == {
        "task_count",
        "target",
        "minimum",
        "max_wait_days",
        "complete",
    }


def test_the_offer_states_the_age_its_freeze_turns_on() -> None:
    """The one fact in this document the clock alone moves.

    `max_wait_days` past its oldest member, the policy releases a batch the same
    offer needed an argued force for the day before — with no writer having
    touched anything, which is why the clock is covered by
    `observations_revision` and not by the durable token.

    The age is the offer's and never the pool's: the pool's oldest entry here is
    a task the identity test left out, and measuring from it would release every
    later batch on the day that one report arrived.
    """

    index = read("index-batch-proposed.json")
    proposal = index["index"]["proposal"]
    entries = {
        (item["repo_id"], item["task_id"]): item["first_imported_at"]
        for item in read("pool.json")["detail"]["pool"]["entries"]
    }
    members = {
        (item["repo_id"], item["task_id"]) for item in read("proposal.json")["detail"]["proposal"]["selected"]
    }

    assert proposal["oldest_member_imported_at"] == min(entries[key] for key in members)
    assert proposal["oldest_member_imported_at"] > min(entries.values()), "the pool holds an older non-member"

    def moment(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    taken = moment(index["snapshot"]["taken_at"])
    assert (taken - moment(proposal["oldest_member_imported_at"])).days == proposal["waited_days"]
    assert proposal["waited_days"] < index["index"]["pool"]["max_wait_days"], "not released by waiting yet"


def test_one_reading_assembles_from_several_documents() -> None:
    """The index and the details a console reads beside it are one reading or
    they are not, and the snapshot is what says which.

    Three documents of one state here — the series, the offer standing in it,
    and the pool behind that offer — carrying one snapshot between them. The
    series taken of a different state carries a different token, which is the
    only thing that stops a console rendering two moments as one.
    """

    offer = [read(name)["snapshot"] for name in ("index-batch-proposed.json", "proposal.json", "pool.json")]
    assert offer[0] == offer[1] == offer[2]
    assert read("index.json")["snapshot"]["state_revision"] != offer[0]["state_revision"], (
        "a series with no offer standing is a different state from one with"
    )


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
