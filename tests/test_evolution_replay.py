"""Replay evidence: its versioned contract, what it is read to mean, and the two
operations that write it.

Three parts, the way the lineage suite is built.

The first is the schema file, which is the contract (`schema.py`) — so the
instances there are hand-written rather than produced by the package. A fixture
built from the writer would only prove the writer agrees with itself.

The second is the reader that checks a run against the round it claims to have
measured, and the derivation that says whether any of it still describes the tree
a promotion would carry. The hostile cases are the ones a well-formed record can
still be wrong in — a candidate that is not the one the round's seal pinned, a
report carried across a `revise`, a source line that moved after the numbers were
taken — because each of them reads as ordinary evidence right up to the promotion
it would justify.

The third is `start` and `conclude`, and there the records are the package's own,
written against a real batch, a real admission, a real seal, and a real Git
merge. What those tests are mostly about is order: every refusal lands before the
harness is asked for anything, so a retry costs nothing, and the one refusal that
cannot — a plan the record will not hold — names the run it left going.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from evolution_fixtures import (
    REPO_ROOT,
    admitted_task,
    experiment_round,
    git_commit,
    git_delete_ref,
    git_repo,
    git_rev,
    git_sibling_commit,
    git_tree,
    git_unrelated_commit,
    git_update_ref,
    make_repo,
    measurement,
    replay_entry,
    replay_integration,
    replay_result,
    write_closure,
    write_draft,
    write_experiment,
    write_manifest,
    write_replays,
)

from ai_native_deployment import evolution
from ai_native_deployment.evolution import analysis_task, experiments, lineage, phase, render, replay, schema

SCHEMAS = REPO_ROOT / "evolution" / "schemas"
REPLAY_SCHEMA = "experiment-replays.schema.json"

BATCH_ID = "evolution-batch-0007"
EXPERIMENT_ID = f"{BATCH_ID}-exp-02"
BASE = "a" * 40
CANDIDATE = "b" * 40
SECOND_CANDIDATE = "9" * 40
RELEASE_REF = "refs/heads/release"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return git_repo(make_repo(tmp_path), tag="v2.2.0")


@pytest.fixture
def config(repo: Path) -> evolution.EvolutionConfig:
    return evolution.load_config(repo)


@pytest.fixture
def release(repo: Path) -> str:
    """The source line a candidate is integrated onto — a ref of its own rather
    than whatever branch happens to be checked out, since the merge input is a
    property of the release line and not of this working copy."""

    sha = git_rev(repo, "HEAD")
    git_update_ref(repo, RELEASE_REF, sha)
    return sha


def sealed_experiment(
    config: evolution.EvolutionConfig,
    *,
    rounds: list[dict] | None = None,
    **overrides: Any,
) -> lineage.Experiment:
    """One experiment, read back through the parser that reads the real ones.

    Its single round is candidate-ready by default: an open round is what
    nothing may measure, so it is the exception these fixtures state explicitly.
    """

    directory = write_experiment(
        config.experiments_root,
        EXPERIMENT_ID,
        base_revision=BASE,
        rounds=rounds if rounds is not None else [experiment_round(1, candidate_revision=CANDIDATE)],
        **overrides,
    )
    record = json.loads((directory / "experiment.json").read_text(encoding="utf-8"))
    return lineage.parse_experiment(config, record, directory)


def errors(instance: Any) -> list[str]:
    return schema.validate(instance, schema.load_schema(SCHEMAS / REPLAY_SCHEMA))


def record(replays: list[dict], **overrides: Any) -> dict[str, Any]:
    return {"schema_version": 1, "experiment_id": EXPERIMENT_ID, "replays": replays, **overrides}


def harness_of(handle: str | None, harness_id: str = "local-replay") -> dict:
    return {"id": harness_id, "revision": "0.1.0", "config_sha256": "d" * 64, "handle": handle}


def numeric_result(field: str, value: float) -> dict:
    """One result whose only oddity is `field` carrying `value` — the three
    places this contract holds a number rather than an integer."""

    if field == "elapsed_seconds":
        return replay_result(elapsed_seconds=value)
    return replay_result(metrics=[measurement(**{field: value})])


# --- the versioned contract --------------------------------------------------


def test_a_run_that_is_still_going_and_one_that_finished_both_validate() -> None:
    """Both are ordinary states of the same record. A run with no result is not
    an incomplete record — it is the durable form of "started, not concluded",
    which is the whole reason the state does not live in a process."""

    assert errors(record([replay_entry(1, 1, running=True)])) == []
    assert errors(record([replay_entry(1, 1)])) == []


@pytest.mark.parametrize(
    ("description", "instance"),
    [
        ("unknown top-level property", record([], note="why not")),
        ("unknown replay property", record([{**replay_entry(), "note": "why not"}])),
        ("wrong schema version", record([], schema_version=2)),
        ("experiment id that is not one", record([], experiment_id="exp-02")),
        ("round below 1", record([replay_entry(0, 1)])),
        ("attempt below 1", record([replay_entry(1, 0)])),
        ("start time that is not RFC 3339", record([replay_entry(started_at="2026-08-04 09:00:00")])),
        ("expectation left empty", record([replay_entry(expectation="")])),
        (
            "candidate revision that is not a full sha",
            record([replay_entry(integration=replay_integration(candidate_revision="b" * 12))]),
        ),
        (
            "merge input revision that is not a full sha",
            record([replay_entry(integration=replay_integration(merge_input_revision="not a revision"))]),
        ),
        (
            "integration tree that is not a full sha",
            record([replay_entry(integration=replay_integration(tree="f" * 39))]),
        ),
        (
            "case set hash that is not a sha256",
            record(
                [
                    replay_entry(
                        cases={
                            "case_set_id": "loader-regressions",
                            "case_set_sha256": "c" * 63,
                            "count": 1,
                            "excluded": [],
                        }
                    )
                ]
            ),
        ),
        (
            "cohort of no cases",
            record(
                [
                    replay_entry(
                        cases={
                            "case_set_id": "loader-regressions",
                            "case_set_sha256": "c" * 64,
                            "count": 0,
                            "excluded": [],
                        }
                    )
                ]
            ),
        ),
        (
            "exclusion with no reason",
            record(
                [
                    replay_entry(
                        cases={
                            "case_set_id": "loader-regressions",
                            "case_set_sha256": "c" * 64,
                            "count": 1,
                            "excluded": [{"case_id": "case-7"}],
                        }
                    )
                ]
            ),
        ),
        (
            "harness id that is not a slug",
            record(
                [
                    replay_entry(
                        harness={
                            "id": "Local Replay",
                            "revision": "0.1.0",
                            "config_sha256": "d" * 64,
                            "handle": None,
                        }
                    )
                ]
            ),
        ),
        (
            "merge input ref that is the empty string rather than absent",
            record([replay_entry(integration=replay_integration(merge_input_ref=""))]),
        ),
        (
            "merge input named by a pseudo-ref, which follows this checkout",
            record([replay_entry(integration=replay_integration(merge_input_ref="HEAD"))]),
        ),
        (
            "merge input named by a bare branch name",
            record([replay_entry(integration=replay_integration(merge_input_ref="release"))]),
        ),
        (
            "merge input named by a revision expression",
            record([replay_entry(integration=replay_integration(merge_input_ref="refs/heads/release~1"))]),
        ),
        (
            "merge input named by a reflog expression",
            record(
                [replay_entry(integration=replay_integration(merge_input_ref="refs/heads/release@{yesterday}"))]
            ),
        ),
        (
            "harness handle that is the empty string rather than absent",
            record(
                [
                    replay_entry(
                        harness={"id": "local-replay", "revision": "0.1.0", "config_sha256": "d" * 64, "handle": ""}
                    )
                ]
            ),
        ),
        ("ambiguity claimed and left blank", record([replay_entry(result=replay_result(ambiguity=""))])),
        ("outcome outside the enum", record([replay_entry(result=replay_result("cancelled"))])),
        ("direction outside the enum", record([replay_entry(result=replay_result(metrics=[measurement(better="up")]))])),
        (
            "negative elapsed time",
            record([replay_entry(result=replay_result(elapsed_seconds=-1))]),
        ),
    ],
)
def test_the_schema_refuses_a_record_it_cannot_read_as_evidence(description: str, instance: dict) -> None:
    assert errors(instance), f"{description} was accepted"


@pytest.mark.parametrize("field", ["ambiguity", "elapsed_seconds", "regressions"])
def test_a_result_states_every_field_even_when_it_has_nothing_to_say(field: str) -> None:
    """Invariant 4 keeps missing fields explicit. An absent `ambiguity` and a
    null one are different claims — "nothing was close" and "nobody said" — and
    a promotion is argued from the first."""

    result = replay_result()
    result.pop(field)
    assert errors(record([replay_entry(result=result)]))


# --- reading one experiment's evidence ---------------------------------------


def test_an_experiment_nobody_has_measured_reads_as_no_evidence(config: evolution.EvolutionConfig) -> None:
    """The ordinary state of every round before its first replay: the absence of
    a file and a file listing nothing are the same fact about the evidence."""

    assert replay.read_replays(config, sealed_experiment(config)) == ()


def test_the_pinned_inputs_survive_the_round_trip(config: evolution.EvolutionConfig) -> None:
    experiment = sealed_experiment(config)
    write_replays(
        config.experiments_root,
        EXPERIMENT_ID,
        [replay_entry(1, 1, integration=replay_integration(candidate_revision=CANDIDATE))],
    )

    (run,) = replay.read_replays(config, experiment)
    assert run.round_number == 1 and run.attempt == 1
    assert run.integration.candidate_revision == CANDIDATE
    assert run.integration.base_revision == BASE
    assert run.completed and not run.running
    assert run.result is not None and run.result.metrics[0].goal
    # The record is the durable form of the request, so a later process polls
    # from what is written down rather than from what started the run.
    assert run.request.integration == run.integration
    assert run.request.round_number == 1


def test_the_record_is_a_request_that_would_run_the_same_thing_again(
    config: evolution.EvolutionConfig,
) -> None:
    """Reproducible from pinned inputs, which the integration alone is not: the
    cohort, the evaluator, and the configuration are the harness's own
    selections, so a request naming only the integration is answered by whatever
    it would select today — a different measurement under this one's
    provenance."""

    experiment = sealed_experiment(config)
    write_replays(config.experiments_root, EXPERIMENT_ID, [replay_entry(1, 1)])

    (run,) = replay.read_replays(config, experiment)
    assert run.request.reproduce == replay.ReplayPlan(
        cases=run.cases, evaluator=run.evaluator, harness=run.harness
    )
    assert run.request.reproduce.cases.case_set_sha256 == run.cases.case_set_sha256
    assert run.request.reproduce.harness.config_sha256 == run.harness.config_sha256
    assert run.request.reproduce.evaluator.rubric_revision == run.evaluator.rubric_revision
    # A first run has nothing to reproduce: the harness selects, and the record
    # is what makes that selection askable-for afterwards.
    first = replay.ReplayRequest(
        experiment_id=EXPERIMENT_ID, round_number=1, attempt=1, integration=run.integration
    )
    assert first.reproduce is None


@pytest.mark.parametrize(
    ("description", "text"),
    [
        ("not JSON at all", "{"),
        ("a JSON array", "[]"),
    ],
)
def test_an_unreadable_record_stops_the_read(
    config: evolution.EvolutionConfig, description: str, text: str
) -> None:
    experiment = sealed_experiment(config)
    replay.replays_path(experiment).write_text(text, encoding="utf-8")

    with pytest.raises(evolution.BatchError):
        replay.read_replays(config, experiment)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field", ["elapsed_seconds", "baseline", "candidate"])
def test_a_number_nothing_can_be_compared_against_is_not_a_measurement(
    config: evolution.EvolutionConfig, field: str, value: float
) -> None:
    """Python writes and reads three literals JSON does not have, and each of
    them passes every check a schema makes of a number: `isinstance` says float,
    and `minimum` — like every comparison against NaN — is false. A completed run
    whose metrics are not quantities would otherwise read as promotable."""

    experiment = sealed_experiment(config)
    replay.replays_path(experiment).write_text(
        json.dumps(record([replay_entry(1, 1, result=numeric_result(field, value))])), encoding="utf-8"
    )

    with pytest.raises(evolution.ValidationError, match="not a number JSON has"):
        replay.read_replays(config, experiment)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field", ["elapsed_seconds", "baseline", "candidate"])
def test_a_writer_cannot_publish_a_number_the_reader_would_refuse(
    config: evolution.EvolutionConfig, field: str, value: float
) -> None:
    """The other door. A writer validates the next state of the file through
    this parser as an object, where there is no JSON text for a literal to be
    refused in — so the value itself is refused instead."""

    experiment = sealed_experiment(config)
    instance = record([replay_entry(1, 1, result=numeric_result(field, value))])

    with pytest.raises(evolution.ValidationError, match="expected type"):
        replay.parse_replays(config, instance, experiment)


@pytest.mark.parametrize("field", ["elapsed_seconds", "baseline", "candidate"])
def test_an_integer_too_large_for_a_float_is_still_a_measurement(
    config: evolution.EvolutionConfig, field: str
) -> None:
    """The other side of the same boundary, at both doors. JSON's numbers have
    no range, and this one is spelled out in digits — `1e400` is the float that
    is not finite, `10**400` is an ordinary integer literal. Refusing it, or
    letting the finiteness check convert it to a float and raise out of
    validation, makes a conforming record unreadable rather than refused."""

    experiment = sealed_experiment(config)
    huge = 10**400
    instance = record([replay_entry(1, 1, result=numeric_result(field, huge))])
    replay.replays_path(experiment).write_text(json.dumps(instance), encoding="utf-8")

    (from_disk,) = replay.read_replays(config, experiment)
    (from_writer,) = replay.parse_replays(config, instance, experiment)
    assert from_disk == from_writer
    measured = (
        from_disk.result.elapsed_seconds
        if field == "elapsed_seconds"
        else getattr(from_disk.result.metrics[0], field)
    )
    assert measured == huge


def test_evidence_cannot_name_another_experiment(config: evolution.EvolutionConfig) -> None:
    experiment = sealed_experiment(config)
    write_replays(
        config.experiments_root,
        EXPERIMENT_ID,
        [replay_entry()],
        declares=f"{BATCH_ID}-exp-01",
    )

    with pytest.raises(evolution.BatchError, match="cannot measure another"):
        replay.read_replays(config, experiment)


# --- what binds a run to the round it measured -------------------------------


def test_a_run_cannot_measure_a_round_the_experiment_does_not_have(
    config: evolution.EvolutionConfig,
) -> None:
    experiment = sealed_experiment(config)
    write_replays(config.experiments_root, EXPERIMENT_ID, [replay_entry(2, 1)])

    with pytest.raises(evolution.BatchError, match="does not have"):
        replay.read_replays(config, experiment)


def test_nothing_measures_a_round_before_its_seal(config: evolution.EvolutionConfig) -> None:
    """Invariant 16 from the evidence side. An open round's tip moves, so a
    result taken against it describes a tree the record cannot afterwards
    identify — and the record here would be claiming a candidate revision no
    seal ever pinned."""

    experiment = sealed_experiment(config, rounds=[experiment_round(1)])
    write_replays(config.experiments_root, EXPERIMENT_ID, [replay_entry(1, 1)])

    with pytest.raises(evolution.BatchError, match="carries no seal"):
        replay.read_replays(config, experiment)


def test_a_run_exercises_exactly_the_revision_its_round_pinned(
    config: evolution.EvolutionConfig,
) -> None:
    experiment = sealed_experiment(config)
    write_replays(
        config.experiments_root,
        EXPERIMENT_ID,
        [replay_entry(1, 1, integration=replay_integration(candidate_revision="c" * 40))],
    )

    with pytest.raises(evolution.BatchError, match="round 1 pinned"):
        replay.read_replays(config, experiment)


def test_a_later_round_cannot_reuse_an_earlier_rounds_report(
    config: evolution.EvolutionConfig,
) -> None:
    """The one shape this binding exists to refuse. Round 2 wants round 1's
    numbers, so it claims them under its own number — and is caught by the
    candidate revision, which is the thing the two rounds do not share."""

    experiment = sealed_experiment(
        config,
        rounds=[
            experiment_round(1, candidate_revision=CANDIDATE),
            experiment_round(
                2,
                tasks=[admitted_task("loader-second")],
                candidate_revision=SECOND_CANDIDATE,
                opened_at="2026-08-05T09:00:00Z",
                sealed_at="2026-08-06T09:00:00Z",
            ),
        ],
    )
    write_replays(
        config.experiments_root,
        EXPERIMENT_ID,
        [replay_entry(2, 1, integration=replay_integration(candidate_revision=CANDIDATE))],
    )

    with pytest.raises(evolution.BatchError, match="round 2 pinned"):
        replay.read_replays(config, experiment)


def test_evidence_carries_the_base_its_batch_froze(config: evolution.EvolutionConfig) -> None:
    experiment = sealed_experiment(config)
    write_replays(
        config.experiments_root,
        EXPERIMENT_ID,
        [replay_entry(1, 1, integration=replay_integration(base_revision="7" * 40))],
    )

    with pytest.raises(evolution.BatchError, match="was created on"):
        replay.read_replays(config, experiment)


# --- runs only append --------------------------------------------------------


def two_rounds(config: evolution.EvolutionConfig) -> lineage.Experiment:
    return sealed_experiment(
        config,
        rounds=[
            experiment_round(1, candidate_revision=CANDIDATE),
            experiment_round(
                2,
                tasks=[admitted_task("loader-second")],
                candidate_revision=SECOND_CANDIDATE,
                opened_at="2026-08-05T09:00:00Z",
                sealed_at="2026-08-06T09:00:00Z",
            ),
        ],
    )


def second_round_entry(attempt: int = 1, **kwargs: Any) -> dict:
    return replay_entry(
        2,
        attempt,
        integration=replay_integration(candidate_revision=SECOND_CANDIDATE),
        started_at="2026-08-07T09:00:00Z",
        **kwargs,
    )


@pytest.mark.parametrize(
    ("description", "entries", "match"),
    [
        (
            "an attempt whose record is gone",
            [replay_entry(1, 1), replay_entry(1, 3)],
            "attempts",
        ),
        (
            "one position recorded twice",
            [replay_entry(1, 1), replay_entry(1, 1)],
            "appended one at a time",
        ),
        (
            "a round measured again after a later one",
            [replay_entry(1, 1), "second", replay_entry(1, 2)],
            "appended one at a time",
        ),
    ],
)
def test_runs_are_appended_in_order(
    config: evolution.EvolutionConfig, description: str, entries: list, match: str
) -> None:
    experiment = two_rounds(config)
    resolved = [second_round_entry() if entry == "second" else entry for entry in entries]
    write_replays(config.experiments_root, EXPERIMENT_ID, resolved)

    with pytest.raises(evolution.BatchError, match=match):
        replay.read_replays(config, experiment)


def test_one_round_is_measured_against_one_integration_at_a_time(
    config: evolution.EvolutionConfig,
) -> None:
    experiment = sealed_experiment(config)
    write_replays(
        config.experiments_root,
        EXPERIMENT_ID,
        [replay_entry(1, 1, running=True), replay_entry(1, 2, running=True)],
    )

    with pytest.raises(evolution.BatchError, match="all recorded as still running"):
        replay.read_replays(config, experiment)


def test_a_run_that_was_overtaken_is_one_nothing_will_conclude(
    config: evolution.EvolutionConfig,
) -> None:
    experiment = sealed_experiment(config)
    write_replays(
        config.experiments_root,
        EXPERIMENT_ID,
        [replay_entry(1, 1, running=True), replay_entry(1, 2)],
    )

    with pytest.raises(evolution.BatchError, match="overtaken"):
        replay.read_replays(config, experiment)


def test_a_running_record_names_the_run_something_can_poll(
    config: evolution.EvolutionConfig,
) -> None:
    """The handle is the only thing connecting this record to the work. Without
    it the round is permanently unmeasurable behind evidence that never
    arrives."""

    experiment = sealed_experiment(config)
    write_replays(
        config.experiments_root,
        EXPERIMENT_ID,
        [
            replay_entry(
                1,
                1,
                running=True,
                harness={"id": "local-replay", "revision": "0.1.0", "config_sha256": "d" * 64, "handle": None},
            )
        ],
    )

    with pytest.raises(evolution.BatchError, match="no harness handle"):
        replay.read_replays(config, experiment)


def test_a_concluded_run_may_keep_its_handle_as_provenance(
    config: evolution.EvolutionConfig,
) -> None:
    experiment = sealed_experiment(config)
    write_replays(
        config.experiments_root,
        EXPERIMENT_ID,
        [
            replay_entry(
                1,
                1,
                harness={"id": "local-replay", "revision": "0.1.0", "config_sha256": "d" * 64, "handle": None},
            )
        ],
    )

    (run,) = replay.read_replays(config, experiment)
    assert run.harness.handle is None


def test_one_handle_names_one_run(config: evolution.EvolutionConfig) -> None:
    """The retry recorded under the handle it was retrying. `poll` takes the
    handle and nothing else, so the second run would be concluded from the first
    one's report — and the first would acquire a second record stating an
    integration it never measured."""

    experiment = sealed_experiment(config)
    write_replays(
        config.experiments_root,
        EXPERIMENT_ID,
        [replay_entry(1, 1, harness=harness_of("run-0001")), replay_entry(1, 2, harness=harness_of("run-0001"))],
    )

    with pytest.raises(evolution.BatchError, match="handle 'run-0001'"):
        replay.read_replays(config, experiment)


def test_a_retry_across_rounds_cannot_reuse_the_handle_either(
    config: evolution.EvolutionConfig,
) -> None:
    experiment = two_rounds(config)
    write_replays(
        config.experiments_root,
        EXPERIMENT_ID,
        [replay_entry(1, 1, harness=harness_of("run-0001")), second_round_entry(harness=harness_of("run-0001"))],
    )

    with pytest.raises(evolution.BatchError, match="handle 'run-0001'"):
        replay.read_replays(config, experiment)


def test_two_harnesses_may_number_their_runs_the_same_way(
    config: evolution.EvolutionConfig,
) -> None:
    """A handle only ever means anything to the harness that issued it, so the
    collision is per harness id."""

    experiment = sealed_experiment(config)
    write_replays(
        config.experiments_root,
        EXPERIMENT_ID,
        [
            replay_entry(1, 1, harness=harness_of("run-1")),
            replay_entry(1, 2, harness=harness_of("run-1", "hosted-replay")),
        ],
    )

    first, second = replay.read_replays(config, experiment)
    assert first.harness.handle == second.harness.handle
    assert first.harness.id != second.harness.id


def test_a_harness_that_issued_no_name_leaves_nothing_to_collide(
    config: evolution.EvolutionConfig,
) -> None:
    """Null is absence rather than identity: it records that there is no handle,
    which two concluded runs may both do."""

    experiment = sealed_experiment(config)
    write_replays(
        config.experiments_root,
        EXPERIMENT_ID,
        [replay_entry(1, 1, harness=harness_of(None)), replay_entry(1, 2, harness=harness_of(None))],
    )

    assert len(replay.read_replays(config, experiment)) == 2


# --- what a result may say ---------------------------------------------------


@pytest.mark.parametrize(
    ("description", "result", "match"),
    [
        (
            "completed having measured nothing",
            replay_result(metrics=[]),
            "measured nothing",
        ),
        (
            "failed with numbers attached",
            replay_result("failed", metrics=[measurement()]),
            "still states",
        ),
        (
            "failed while naming a regression",
            replay_result("failed", metrics=[], regressions=[{"case_id": "case-3", "summary": "slower"}]),
            "still states",
        ),
        (
            "failed while claiming ambiguity",
            replay_result("failed", metrics=[], ambiguity="two cases were close"),
            "still states",
        ),
        (
            "one quantity measured twice",
            replay_result(metrics=[measurement(), measurement(candidate=1.9)]),
            "twice",
        ),
        (
            "an improvement over no baseline",
            replay_result(metrics=[measurement(baseline=None)]),
            "no baseline",
        ),
    ],
)
def test_a_result_that_cannot_be_read_as_one_measurement_stops_the_read(
    config: evolution.EvolutionConfig, description: str, result: dict, match: str
) -> None:
    experiment = sealed_experiment(config)
    write_replays(config.experiments_root, EXPERIMENT_ID, [replay_entry(1, 1, result=result)])

    with pytest.raises(evolution.BatchError, match=match):
        replay.read_replays(config, experiment)


def test_an_observation_needs_no_baseline(config: evolution.EvolutionConfig) -> None:
    """Invariant 13 wants quota and elapsed time recorded without making them
    the score, and a quantity measured only on the candidate says so by claiming
    no direction."""

    experiment = sealed_experiment(config)
    write_replays(
        config.experiments_root,
        EXPERIMENT_ID,
        [
            replay_entry(
                1,
                1,
                result=replay_result(
                    metrics=[measurement("candidate-tokens", unit="tokens", baseline=None, candidate=910000, better="neither")]
                ),
            )
        ],
    )

    (run,) = replay.read_replays(config, experiment)
    assert run.result is not None and not run.result.metrics[0].goal


def test_a_failed_run_records_why_and_nothing_else(config: evolution.EvolutionConfig) -> None:
    experiment = sealed_experiment(config)
    write_replays(
        config.experiments_root,
        EXPERIMENT_ID,
        [replay_entry(1, 1, result=replay_result("failed", detail="the harness lost its worker", metrics=[]))],
    )

    (run,) = replay.read_replays(config, experiment)
    assert run.failed and run.result is not None and run.result.detail == "the harness lost its worker"


def test_a_case_is_held_out_once(config: evolution.EvolutionConfig) -> None:
    experiment = sealed_experiment(config)
    write_replays(
        config.experiments_root,
        EXPERIMENT_ID,
        [
            replay_entry(
                1,
                1,
                cases={
                    "case_set_id": "loader-regressions",
                    "case_set_sha256": "c" * 64,
                    "count": 11,
                    "excluded": [
                        {"case_id": "case-7", "reason": "needs a credentialed backend"},
                        {"case_id": "case-7", "reason": "flaky"},
                    ],
                },
            )
        ],
    )

    with pytest.raises(evolution.BatchError, match="twice"):
        replay.read_replays(config, experiment)


@pytest.mark.parametrize(
    "ref",
    ["refs/heads/a..b", "refs/heads/.hidden", "refs/heads/release.lock", "refs/heads/release."],
)
def test_a_merge_input_ref_git_could_never_hold_is_refused_with_the_reason(
    config: evolution.EvolutionConfig, ref: str
) -> None:
    """The schema keeps the name fully qualified; what it cannot say is the rest
    of `git check-ref-format`. A name no ref can have resolves nowhere in every
    checkout, so the drift check would read as one nobody could make — which is a
    different fact, and the one that says "try again from a clone that has it"."""

    experiment = sealed_experiment(config)
    write_replays(
        config.experiments_root,
        EXPERIMENT_ID,
        [replay_entry(1, 1, integration=replay_integration(merge_input_ref=ref))],
    )

    with pytest.raises(evolution.BatchError, match="check-ref-format"):
        replay.read_replays(config, experiment)


def test_a_dot_ending_a_component_before_the_last_is_a_name_git_holds(
    config: evolution.EvolutionConfig,
) -> None:
    """The trailing-`.` rule is about the whole name and not about each of its
    parts: `git check-ref-format` refuses `refs/heads/release.` and accepts
    `refs/heads/re./lease`. Reading the rule per component instead would refuse
    provenance a checkout can resolve — the drift check answered by a name this
    reader threw away."""

    experiment = sealed_experiment(config)
    ref = "refs/heads/re./lease"
    write_replays(
        config.experiments_root,
        EXPERIMENT_ID,
        [replay_entry(1, 1, integration=replay_integration(merge_input_ref=ref))],
    )

    (run,) = replay.read_replays(config, experiment)
    assert run.integration.merge_input_ref == ref


# --- what the evidence currently means ---------------------------------------


def current_run(release_revision: str, **kwargs: Any) -> dict:
    """A run of round 1 integrated onto the source line where it now stands."""

    return replay_entry(
        1,
        1,
        integration=replay_integration(
            candidate_revision=CANDIDATE,
            merge_input_revision=release_revision,
            merge_input_ref=RELEASE_REF,
        ),
        **kwargs,
    )


def test_a_round_nobody_has_replayed_is_incomplete(config: evolution.EvolutionConfig) -> None:
    evidence = replay.describe_evidence(config, sealed_experiment(config))

    assert evidence.state == replay.EVIDENCE_INCOMPLETE
    assert evidence.replay is None
    assert not evidence.promotable
    assert evidence.drift


def test_an_open_round_says_why_nothing_has_measured_it(config: evolution.EvolutionConfig) -> None:
    evidence = replay.describe_evidence(config, sealed_experiment(config, rounds=[experiment_round(1)]))

    assert evidence.state == replay.EVIDENCE_INCOMPLETE
    assert any("invariant 16" in line for line in evidence.drift)


def test_a_completed_run_on_the_standing_source_line_is_promotable(
    config: evolution.EvolutionConfig, release: str
) -> None:
    experiment = sealed_experiment(config)
    write_replays(config.experiments_root, EXPERIMENT_ID, [current_run(release)])

    evidence = replay.describe_evidence(config, experiment)
    assert evidence.state == replay.EVIDENCE_COMPLETE
    assert evidence.promotable
    assert evidence.drift == () and evidence.unverified == ()
    assert evidence.replay is not None and evidence.replay.attempt == 1


def test_a_source_line_that_moved_makes_the_result_stale(
    config: evolution.EvolutionConfig, repo: Path, release: str
) -> None:
    """The drift the pinned candidate cannot show. Nothing about the experiment
    changed; what a promotion would carry did."""

    experiment = sealed_experiment(config)
    write_replays(config.experiments_root, EXPERIMENT_ID, [current_run(release)])
    git_update_ref(repo, RELEASE_REF, git_commit(repo, "unrelated release work"))

    evidence = replay.describe_evidence(config, experiment)
    assert evidence.state == replay.EVIDENCE_STALE
    assert not evidence.promotable
    assert any(RELEASE_REF in line for line in evidence.drift)


def test_a_source_line_this_checkout_does_not_hold_is_unverified_rather_than_fresh(
    config: evolution.EvolutionConfig,
) -> None:
    """A clone that never fetched the release ref cannot say whether the merge
    input moved. That is not agreement, so the run reads as completed and the
    gate stays shut."""

    experiment = sealed_experiment(config)
    write_replays(config.experiments_root, EXPERIMENT_ID, [current_run("5" * 40)])

    evidence = replay.describe_evidence(config, experiment)
    assert evidence.state == replay.EVIDENCE_COMPLETE
    assert not evidence.promotable
    assert evidence.unverified and RELEASE_REF in evidence.unverified[0]


def test_a_detached_merge_input_leaves_the_question_unanswerable(
    config: evolution.EvolutionConfig, release: str
) -> None:
    experiment = sealed_experiment(config)
    write_replays(
        config.experiments_root,
        EXPERIMENT_ID,
        [
            replay_entry(
                1,
                1,
                integration=replay_integration(
                    candidate_revision=CANDIDATE,
                    merge_input_revision=release,
                    merge_input_ref=None,
                ),
            )
        ],
    )

    evidence = replay.describe_evidence(config, experiment)
    assert evidence.state == replay.EVIDENCE_COMPLETE
    assert not evidence.promotable
    assert any("detached" in line for line in evidence.unverified)


def test_a_run_still_going_is_reported_as_such(config: evolution.EvolutionConfig, release: str) -> None:
    experiment = sealed_experiment(config)
    write_replays(config.experiments_root, EXPERIMENT_ID, [current_run(release, running=True)])

    evidence = replay.describe_evidence(config, experiment)
    assert evidence.state == replay.EVIDENCE_RUNNING
    assert not evidence.promotable


def test_the_newest_run_having_failed_is_what_the_round_reports(
    config: evolution.EvolutionConfig, release: str
) -> None:
    experiment = sealed_experiment(config)
    write_replays(
        config.experiments_root,
        EXPERIMENT_ID,
        [current_run(release, result=replay_result("failed", detail="the harness lost its worker", metrics=[]))],
    )

    evidence = replay.describe_evidence(config, experiment)
    assert evidence.state == replay.EVIDENCE_FAILED
    assert any("lost its worker" in line for line in evidence.drift)


def test_exact_evidence_is_not_unmade_by_a_second_run_beside_it(
    config: evolution.EvolutionConfig, release: str
) -> None:
    """A broader sweep started after a result that still matches does not
    withdraw it: the earlier run measured the tree in question, and it is still
    that tree."""

    experiment = sealed_experiment(config)
    write_replays(
        config.experiments_root,
        EXPERIMENT_ID,
        [current_run(release), replay_entry(1, 2, running=True, integration=replay_integration(
            candidate_revision=CANDIDATE, merge_input_revision=release, merge_input_ref=RELEASE_REF
        ))],
    )

    evidence = replay.describe_evidence(config, experiment)
    assert evidence.state == replay.EVIDENCE_COMPLETE
    assert evidence.promotable
    assert evidence.replay is not None and evidence.replay.attempt == 1


def test_a_failed_retry_outranks_the_stale_result_it_was_retrying(
    config: evolution.EvolutionConfig, repo: Path, release: str
) -> None:
    experiment = sealed_experiment(config)
    moved = git_commit(repo, "unrelated release work")
    git_update_ref(repo, RELEASE_REF, moved)
    write_replays(
        config.experiments_root,
        EXPERIMENT_ID,
        [
            current_run(release),
            replay_entry(
                1,
                2,
                integration=replay_integration(
                    candidate_revision=CANDIDATE, merge_input_revision=moved, merge_input_ref=RELEASE_REF
                ),
                result=replay_result("failed", detail="the integration did not build", metrics=[]),
            ),
        ],
    )

    evidence = replay.describe_evidence(config, experiment)
    assert evidence.state == replay.EVIDENCE_FAILED


def test_revising_leaves_the_previous_rounds_evidence_naming_the_round_it_measured(
    config: evolution.EvolutionConfig, release: str
) -> None:
    """What makes evidence stale is the next round existing, not anyone
    remembering to invalidate it."""

    experiment = two_rounds(config)
    write_replays(config.experiments_root, EXPERIMENT_ID, [current_run(release)])

    evidence = replay.describe_evidence(config, experiment)
    assert evidence.round_number == 2
    assert evidence.state == replay.EVIDENCE_STALE
    assert not evidence.promotable
    assert evidence.replay is not None and evidence.replay.round_number == 1
    assert any("round 1" in line and "round 2" in line for line in evidence.drift)


# --- starting and concluding a run -------------------------------------------
#
# From here on the records are written by the package rather than by hand, and
# everything runs against a real batch, a real admission, and a real seal: what
# `start` pins is a candidate Git actually holds, integrated onto a source line
# Git actually resolves, and the tree it records is one `git merge-tree`
# produced. A fixture standing in for any of the three would leave the one thing
# these operations exist to establish — that the evidence names a tree somebody
# can check out — proved only against itself.

LIVE_EXPERIMENT = f"{BATCH_ID}-exp-01"
ANALYSIS_TASK = "2026-07-31-evolution-batch-0007-analysis"
DRAFT = "loader-fallback"
NEXT_DRAFT = "hook-side-loader"
EXPECTED = "fewer remediation rounds, with quality and elapsed time unchanged"

CREATED = datetime(2026, 8, 5, 9, 0, 0, tzinfo=timezone.utc)
SEALED = datetime(2026, 8, 6, 9, 0, 0, tzinfo=timezone.utc)
STARTED = datetime(2026, 8, 7, 9, 0, 0, tzinfo=timezone.utc)
ENDED = datetime(2026, 8, 7, 17, 30, 0, tzinfo=timezone.utc)


class FakeHarness:
    """A harness that answers, and remembers what it was asked.

    The controller may assume exactly two things about the real one — that it
    starts a run and names it, and that polling that name eventually reports —
    so this implements those two and records the requests, which is what the
    tests check the controller pinned.
    """

    def __init__(self, *, report: replay.ReplayReport | None = None, plan: replay.ReplayPlan | None = None) -> None:
        self.requests: list[replay.ReplayRequest] = []
        self.polled: list[str] = []
        self.report = report
        self.plan = plan

    def start(self, request: replay.ReplayRequest) -> replay.ReplayPlan:
        self.requests.append(request)
        if self.plan is not None:
            return self.plan
        return replay.ReplayPlan(
            cases=replay.CaseSet(
                case_set_id="loader-regressions",
                case_set_sha256="c" * 64,
                count=12,
                excluded=(replay.Exclusion(case_id="case-9", reason="needs a credentialed backend"),),
            ),
            evaluator=replay.Evaluator(backend="claude", model="claude-opus-5", rubric_revision="r7"),
            harness=replay.Harness(
                id="local-replay",
                revision="0.1.0",
                config_sha256="d" * 64,
                handle=f"run-{request.round_number:02d}{request.attempt:02d}",
            ),
        )

    def poll(self, handle: str) -> replay.ReplayReport | None:
        self.polled.append(handle)
        return self.report


def completed_report(**overrides: Any) -> replay.ReplayReport:
    fields: dict[str, Any] = {
        "outcome": replay.RESULT_COMPLETED,
        "detail": "convergence improved; no regression outside the excluded case",
        "elapsed_seconds": 1820.5,
        "metrics": (
            replay.Measurement(
                metric="remediation-rounds", unit="rounds", baseline=2.4, candidate=1.6, better="lower"
            ),
        ),
        "regressions": (),
        "ambiguity": None,
    }
    fields.update(overrides)
    return replay.ReplayReport(**fields)


@pytest.fixture
def admitted(config: evolution.EvolutionConfig) -> None:
    """A current batch whose analysis has closed, with one proposal waiting."""

    write_manifest(
        config.batches_root,
        BATCH_ID,
        ["r1", "r2"],
        analysis_task_id=ANALYSIS_TASK,
        runner_protocol_revision="v2.2.0",
    )
    # `findings.md` gates the closure: dispositions written beside an
    # unfinished analysis task are not a completed analysis.
    (config.batches_root / BATCH_ID / "findings.md").write_text("# Findings\n", encoding="utf-8")
    write_closure(config.batches_root, BATCH_ID, analysis_task_id=ANALYSIS_TASK)
    write_draft(config.batches_root, BATCH_ID, DRAFT)
    write_draft(config.batches_root, BATCH_ID, NEXT_DRAFT)


def pinned(config: evolution.EvolutionConfig) -> str:
    """Admit the proposal, do the work, seal round 1 — the candidate-ready state
    a replay is the next thing to happen in."""

    admission = experiments.create(config, [DRAFT], now=CREATED)
    task = analysis_task.task_path(config, admission.admitted[0].task_id)
    task.write_text(task.read_text(encoding="utf-8").replace("status: pending", "status: completed"), encoding="utf-8")
    git_update_ref(config.repo_root, admission.ref, git_commit(config.repo_root, "candidate work"))
    return experiments.seal_round(config, now=SEALED).candidate_revision


def live(config: evolution.EvolutionConfig) -> lineage.Experiment:
    experiment = lineage.describe(config).current
    assert experiment is not None and experiment.open_experiment is not None
    return experiment.open_experiment


def written(config: evolution.EvolutionConfig) -> dict[str, Any]:
    return json.loads((config.experiments_root / LIVE_EXPERIMENT / "replays.json").read_text(encoding="utf-8"))


def audit(config: evolution.EvolutionConfig) -> list[str]:
    return [item["record_type"] for item in evolution.read_records(config)]


def run(config: evolution.EvolutionConfig, harness: FakeHarness, **overrides: Any) -> replay.RunStarted:
    fields: dict[str, Any] = {"source_ref": RELEASE_REF, "expectation": EXPECTED, "now": STARTED}
    fields.update(overrides)
    return replay.start(config, harness, **fields)


def test_starting_a_run_pins_the_integration_the_harness_is_asked_to_measure(
    config: evolution.EvolutionConfig, repo: Path, admitted: None, release: str
) -> None:
    """The controller owns three commits and the tree they make; the harness owns
    the cohort, the evaluator, and its own name for the run. The record states
    both halves, which is what makes it enough to run again."""

    candidate = pinned(config)
    harness = FakeHarness()

    result = run(config, harness)

    assert result.round_number == 1 and result.attempt == 1
    assert result.integration.base_revision == git_rev(repo, "HEAD~1")
    assert result.integration.candidate_revision == candidate
    assert result.integration.merge_input_revision == release
    assert result.integration.merge_input_ref == RELEASE_REF
    # The source line is an ancestor here, so integrating is a fast-forward and
    # the tree it produces is the candidate's own — asserted against Git rather
    # than against what the controller wrote.
    assert result.integration.tree == git_tree(repo, candidate)

    [request] = harness.requests
    assert request.experiment_id == LIVE_EXPERIMENT
    assert (request.round_number, request.attempt) == (1, 1)
    assert request.integration == result.integration
    # A first run: nothing to reproduce, so the harness selects the cohort.
    assert request.reproduce is None

    [entry] = written(config)["replays"]
    assert entry["result"] is None
    assert entry["started_at"] == "2026-08-07T09:00:00Z"
    assert entry["harness"]["handle"] == "run-0101"
    assert entry["expectation"] == EXPECTED
    assert entry["cases"]["excluded"] == [{"case_id": "case-9", "reason": "needs a credentialed backend"}]
    # Nothing is audited yet: the record already says a run started, and the
    # event with something to report is how it ended.
    assert audit(config) == ["experiment-created", "tasks-admitted", "round-sealed"]


def test_a_started_run_reads_as_the_rounds_running_evidence(
    config: evolution.EvolutionConfig, admitted: None, release: str
) -> None:
    """The writer publishes through the reader, so what `status` derives from the
    record is what the operation just wrote — on this machine or another."""

    pinned(config)
    run(config, FakeHarness())

    evidence = replay.describe_evidence(config, live(config))
    assert evidence.state == replay.EVIDENCE_RUNNING
    assert not evidence.promotable
    assert evidence.replay is not None and evidence.replay.harness.handle == "run-0101"


def test_an_expectation_is_collapsed_and_a_missing_one_refuses(
    config: evolution.EvolutionConfig, admitted: None, release: str
) -> None:
    """A prediction travels in a versioned record and is read back beside the
    numbers; two spellings differing only in how they were wrapped would read as
    two different predictions, and none at all leaves the numbers to be read
    against whatever they turn out to be."""

    pinned(config)
    harness = FakeHarness()

    run(config, harness, expectation="fewer remediation rounds,\n  quality unchanged")
    assert written(config)["replays"][0]["expectation"] == "fewer remediation rounds, quality unchanged"
    # Deliberately not handed to the thing being measured: a prediction given to
    # the harness is an instruction.
    assert not hasattr(harness.requests[0], "expectation")

    with pytest.raises(evolution.BatchError, match="expected to show"):
        run(config, harness, expectation="   \n ")


def test_a_run_refuses_a_round_whose_candidate_is_not_pinned(
    config: evolution.EvolutionConfig, admitted: None, release: str
) -> None:
    """Invariant 16 from the writer's side: an open round's tip moves, so a run
    started against one would describe a tree the record cannot afterwards
    identify. The refusal comes before the harness is asked for anything."""

    experiments.create(config, [DRAFT], now=CREATED)
    harness = FakeHarness()

    with pytest.raises(evolution.BatchError, match="still open"):
        run(config, harness)

    assert harness.requests == []
    assert not (config.experiments_root / LIVE_EXPERIMENT / "replays.json").exists()


def test_a_second_run_refuses_while_one_is_still_going(
    config: evolution.EvolutionConfig, admitted: None, release: str
) -> None:
    """One round is measured against one integration at a time. The refusal is
    what makes a retried `start` safe: it costs nothing, because the harness is
    never reached."""

    pinned(config)
    harness = FakeHarness()
    run(config, harness)

    with pytest.raises(evolution.BatchError, match="still running"):
        run(config, harness)

    assert len(harness.requests) == 1
    assert len(written(config)["replays"]) == 1


def test_a_run_refuses_a_candidate_that_does_not_merge_onto_the_source_line(
    config: evolution.EvolutionConfig, repo: Path, admitted: None, release: str
) -> None:
    """A candidate that cannot be integrated is not one a promotion could carry,
    so the conflict is resolved in a further round rather than measured around —
    and never measured against the half-merged tree `git merge-tree` still
    writes for a conflict."""

    pinned(config)
    git_update_ref(repo, RELEASE_REF, git_sibling_commit(repo, release, "conflicting\n", "release work"))
    harness = FakeHarness()

    with pytest.raises(evolution.BatchError, match="CONFLICT|conflict"):
        run(config, harness)

    assert harness.requests == []
    assert not (config.experiments_root / LIVE_EXPERIMENT / "replays.json").exists()


def test_a_run_refuses_a_source_line_this_checkout_does_not_hold(
    config: evolution.EvolutionConfig, admitted: None
) -> None:
    """No `refs/heads/release` here, so there is nothing to integrate onto — the
    replay is run where the source line is, rather than against the candidate on
    its own."""

    pinned(config)
    harness = FakeHarness()

    with pytest.raises(evolution.BatchError, match="not in this checkout"):
        run(config, harness)

    assert harness.requests == []


@pytest.mark.parametrize(
    "source_ref, complaint",
    [
        ("HEAD", "does not match"),
        ("release", "does not match"),
        ("refs/heads/release@{1}", "does not match"),
        ("refs/heads/release.", "not a name Git can hold"),
    ],
)
def test_a_run_refuses_a_source_line_that_is_not_a_ref_meaning_one_thing_everywhere(
    config: evolution.EvolutionConfig, admitted: None, release: str, source_ref: str, complaint: str
) -> None:
    """The recorded name is what a later reader resolves to ask whether the
    source line moved. `HEAD`, a bare branch, and a revision expression answer
    from whichever working copy is asking; a name `git check-ref-format` refuses
    answers nowhere at all. Both are refused before a run exists to record."""

    pinned(config)
    harness = FakeHarness()

    with pytest.raises((evolution.BatchError, evolution.ValidationError), match=complaint):
        run(config, harness, source_ref=source_ref)

    assert harness.requests == []


def test_a_run_refuses_a_lineage_whose_ref_left_its_recorded_history(
    config: evolution.EvolutionConfig, repo: Path, admitted: None, release: str
) -> None:
    """The same guarded preamble every other write runs. A ref standing off the
    history its record pins is a lineage no operation writes into, and a run
    started there would measure a candidate against a record that disagrees with
    the repository."""

    pinned(config)
    git_update_ref(repo, f"refs/evolution/experiments/{LIVE_EXPERIMENT}", git_unrelated_commit(repo, "elsewhere"))
    harness = FakeHarness()

    with pytest.raises(evolution.BatchError, match="does not descend|not on the history"):
        run(config, harness)

    assert harness.requests == []


def test_concluding_records_what_the_harness_reports_and_audits_the_outcome(
    config: evolution.EvolutionConfig, admitted: None, release: str
) -> None:
    """The numbers are the harness's; when they were observed is the
    controller's, the way a round's completion observation is. The audit line is
    the outcome, which is the event there is something to report."""

    candidate = pinned(config)
    harness = FakeHarness(report=completed_report())
    run(config, harness)

    result = replay.conclude(config, harness, now=ENDED)

    assert result.recorded is True
    assert result.outcome == replay.RESULT_COMPLETED
    assert result.round_number == 1 and result.attempt == 1
    assert harness.polled == ["run-0101"]

    [entry] = written(config)["replays"]
    assert entry["result"]["concluded_at"] == "2026-08-07T17:30:00Z"
    assert entry["result"]["elapsed_seconds"] == 1820.5
    assert entry["result"]["metrics"][0]["metric"] == "remediation-rounds"
    assert entry["result"]["regressions"] == []

    assert audit(config)[-1] == "replay-completed"
    line = evolution.read_records(config)[-1]
    assert line["revision"] == candidate
    assert line["round"] == 1
    assert line["detail"] == replay.RESULT_COMPLETED
    assert line["experiment_id"] == LIVE_EXPERIMENT

    evidence = replay.describe_evidence(config, live(config))
    assert evidence.state == replay.EVIDENCE_COMPLETE
    assert evidence.promotable


def test_concluding_a_run_that_is_still_going_writes_nothing(
    config: evolution.EvolutionConfig, admitted: None, release: str
) -> None:
    """Polling is the ordinary case and not an error, so it can be repeated as
    often as an operator likes."""

    pinned(config)
    harness = FakeHarness()
    run(config, harness)

    result = replay.conclude(config, harness, now=ENDED)

    assert result.recorded is False
    assert result.running is True
    assert result.outcome is None
    assert written(config)["replays"][0]["result"] is None
    assert "replay-completed" not in audit(config)


def test_concluding_again_reports_the_result_already_on_record(
    config: evolution.EvolutionConfig, admitted: None, release: str
) -> None:
    """A conclusion whose record landed and whose audit line did not is finished
    by the same conclusion run again — which reports what is there rather than
    polling for a second report or appending a second line."""

    pinned(config)
    harness = FakeHarness(report=completed_report())
    run(config, harness)
    replay.conclude(config, harness, now=ENDED)

    again = replay.conclude(config, harness, now=ENDED)

    assert again.recorded is False
    assert again.outcome == replay.RESULT_COMPLETED
    assert harness.polled == ["run-0101"]
    assert audit(config).count("replay-completed") == 1


def test_a_failed_run_records_why_it_stopped_and_no_numbers(
    config: evolution.EvolutionConfig, admitted: None, release: str
) -> None:
    """A partial sweep reads as a cohort result nobody produced, so a failure
    carries its reason alone — and the round is left with evidence that supports
    no promotion."""

    pinned(config)
    harness = FakeHarness(
        report=completed_report(
            outcome=replay.RESULT_FAILED, detail="the integration did not build", metrics=(), elapsed_seconds=None
        )
    )
    run(config, harness)

    result = replay.conclude(config, harness, now=ENDED)

    assert result.outcome == replay.RESULT_FAILED
    assert written(config)["replays"][0]["result"]["metrics"] == []
    evidence = replay.describe_evidence(config, live(config))
    assert evidence.state == replay.EVIDENCE_FAILED
    assert not evidence.promotable


def test_a_report_the_record_cannot_hold_leaves_the_run_recorded_as_going(
    config: evolution.EvolutionConfig, admitted: None, release: str
) -> None:
    """A harness answering `completed` with nothing measured is refused by the
    reader the writer publishes through. Nothing lands, the run stays pollable,
    and the ledger is not told a run finished."""

    pinned(config)
    harness = FakeHarness(report=completed_report(metrics=()))
    run(config, harness)

    with pytest.raises(evolution.BatchError, match="measured nothing"):
        replay.conclude(config, harness, now=ENDED)

    assert written(config)["replays"][0]["result"] is None
    assert "replay-completed" not in audit(config)


def test_a_plan_the_record_cannot_hold_names_the_run_it_left_going(
    config: evolution.EvolutionConfig, admitted: None, release: str
) -> None:
    """The harness is asked before the record can be written, because the record
    needs the handle that answer carries. So the one thing a refusal after that
    call must do is name the run nothing is left pointing at."""

    pinned(config)
    first = FakeHarness()
    run(config, first)
    replay.conclude(config, FakeHarness(report=completed_report()), now=ENDED)

    reuses_the_handle = FakeHarness(plan=_plan_of(first))
    with pytest.raises(evolution.BatchError, match="run-0101"):
        run(config, reuses_the_handle, now=ENDED)

    assert len(written(config)["replays"]) == 1


def _plan_of(harness: FakeHarness) -> replay.ReplayPlan:
    """The plan a harness already issued, handed back for a second run — which is
    the collision `_require_distinct_handles` refuses."""

    return harness.start(
        replay.ReplayRequest(
            experiment_id=LIVE_EXPERIMENT,
            round_number=1,
            attempt=1,
            integration=replay.Integration(
                base_revision="a" * 40,
                candidate_revision="b" * 40,
                merge_input_revision="e" * 40,
                merge_input_ref=RELEASE_REF,
                tree="f" * 40,
            ),
        )
    )


def test_a_source_line_that_moved_is_answered_by_a_new_attempt(
    config: evolution.EvolutionConfig, repo: Path, admitted: None, release: str
) -> None:
    """The candidate is immutable and the source line is not, so evidence that
    was exact yesterday describes nothing today. The answer is another run of the
    same round — a new attempt, never an edit of the one that measured the older
    integration."""

    candidate = pinned(config)
    harness = FakeHarness(report=completed_report())
    run(config, harness)
    replay.conclude(config, harness, now=ENDED)

    git_update_ref(repo, RELEASE_REF, candidate)
    stale = replay.describe_evidence(config, live(config))
    assert stale.state == replay.EVIDENCE_STALE
    assert any("has moved" in note for note in stale.drift)

    second = run(config, harness, now=ENDED)
    assert (second.round_number, second.attempt) == (1, 2)
    assert second.integration.merge_input_revision == candidate
    replay.conclude(config, harness, now=ENDED)

    fresh = replay.describe_evidence(config, live(config))
    assert fresh.state == replay.EVIDENCE_COMPLETE
    assert fresh.promotable
    assert fresh.replay is not None and fresh.replay.attempt == 2
    assert [entry["attempt"] for entry in written(config)["replays"]] == [1, 2]


def test_a_run_after_a_revision_measures_the_round_that_revision_opened(
    config: evolution.EvolutionConfig, admitted: None, release: str
) -> None:
    """A round is the unit evidence names. The new round's first run is attempt 1
    of round 2, and it exercises what round 2's seal pinned — the earlier report
    goes on naming the round it actually measured."""

    pinned(config)
    harness = FakeHarness(report=completed_report())
    run(config, harness)
    replay.conclude(config, harness, now=ENDED)

    experiments.revise(config, reason="the candidate regressed on two cases", now=ENDED)
    experiments.add_tasks(config, [NEXT_DRAFT], now=ENDED)
    task = analysis_task.task_path(config, written_task_id(config))
    task.write_text(task.read_text(encoding="utf-8").replace("status: pending", "status: completed"), encoding="utf-8")
    git_update_ref(
        config.repo_root,
        f"refs/evolution/experiments/{LIVE_EXPERIMENT}",
        git_commit(config.repo_root, "second candidate"),
    )
    second = experiments.seal_round(config, now=ENDED).candidate_revision

    started = run(config, harness, now=ENDED)

    assert (started.round_number, started.attempt) == (2, 1)
    assert started.integration.candidate_revision == second
    assert [(entry["round"], entry["attempt"]) for entry in written(config)["replays"]] == [(1, 1), (2, 1)]


def written_task_id(config: evolution.EvolutionConfig) -> str:
    """The task id of the admission just made into the open round."""

    return live(config).last_round.tasks[-1].task_id


def test_status_reports_what_the_current_round_has_been_measured_by(
    config: evolution.EvolutionConfig, admitted: None, release: str
) -> None:
    """One derivation behind both surfaces: `status` reads the same evidence the
    promotion gate will refuse on, and shows what this checkout established apart
    from what it could not answer."""

    pinned(config)
    harness = FakeHarness(report=completed_report())
    run(config, harness)

    going = phase.describe(config, now=ENDED)
    assert going.evidence is not None and going.evidence.state == replay.EVIDENCE_RUNNING
    payload = going.to_json()
    assert payload["schema_version"] == phase.SCHEMA_VERSION == 4
    assert payload["replay"]["state"] == replay.EVIDENCE_RUNNING
    assert payload["replay"]["promotable"] is False
    assert payload["replay"]["run"]["attempt"] == 1
    assert payload["replay"]["run"]["outcome"] is None
    assert "replay" in render.format_status(going)

    replay.conclude(config, harness, now=ENDED)
    done = phase.describe(config, now=ENDED).to_json()
    assert done["replay"]["promotable"] is True
    assert done["replay"]["run"]["outcome"] == replay.RESULT_COMPLETED
    assert done["replay"]["drift"] == [] and done["replay"]["unverified"] == []


def test_a_clone_without_the_source_line_ref_reports_the_check_it_could_not_make(
    config: evolution.EvolutionConfig, repo: Path, admitted: None, release: str
) -> None:
    """A question Git cannot answer here is not agreement. The evidence is still
    `completed` — that is what the run reported — and it supports no promotion
    from this checkout."""

    pinned(config)
    harness = FakeHarness(report=completed_report())
    run(config, harness)
    replay.conclude(config, harness, now=ENDED)
    git_delete_ref(repo, RELEASE_REF)

    payload = phase.describe(config, now=ENDED).to_json()
    assert payload["replay"]["state"] == replay.EVIDENCE_COMPLETE
    assert payload["replay"]["promotable"] is False
    assert payload["replay"]["drift"] == []
    assert any("not in this checkout" in note for note in payload["replay"]["unverified"])


def test_a_harness_that_issues_no_handle_leaves_nothing_recorded(
    config: evolution.EvolutionConfig, admitted: None, release: str
) -> None:
    """A run still going is a run something can still ask about. A harness that
    named nothing has started work this controller could never poll, conclude, or
    find — so the record refuses, and the refusal says what is loose."""

    pinned(config)
    nameless = FakeHarness(
        plan=replay.ReplayPlan(
            cases=replay.CaseSet(case_set_id="loader-regressions", case_set_sha256="c" * 64, count=12, excluded=()),
            evaluator=replay.Evaluator(backend="claude", model="claude-opus-5", rubric_revision="r7"),
            harness=replay.Harness(id="local-replay", revision="0.1.0", config_sha256="d" * 64, handle=None),
        )
    )

    with pytest.raises(evolution.BatchError, match="carries no harness handle"):
        run(config, nameless)

    assert len(nameless.requests) == 1
    assert not (config.experiments_root / LIVE_EXPERIMENT / "replays.json").exists()


def test_a_run_whose_harness_cannot_answer_is_ended_with_a_reason(
    config: evolution.EvolutionConfig, admitted: None, release: str
) -> None:
    """Age concludes nothing, so a harness that died would leave the run going —
    and with it the whole experiment, since a round is measured against one
    integration at a time. The reason is the operator's precisely because the
    harness is what could not give one, and what is recorded is the `failed` the
    run was: no numbers, and the story in `detail`."""

    pinned(config)
    harness = FakeHarness()
    run(config, harness)

    result = replay.abandon(config, reason="the harness host was rebuilt;\n the run is unrecoverable", now=ENDED)

    assert result.recorded is True
    assert result.outcome == replay.RESULT_FAILED
    assert harness.polled == []
    entry = written(config)["replays"][0]
    assert entry["result"]["detail"] == "the harness host was rebuilt; the run is unrecoverable"
    assert entry["result"]["metrics"] == [] and entry["result"]["elapsed_seconds"] is None
    assert audit(config)[-1] == "replay-completed"

    evidence = replay.describe_evidence(config, live(config))
    assert evidence.state == replay.EVIDENCE_FAILED

    # And the round is measurable again: what a failure is answered by is another
    # attempt, which is what a failed run means everywhere else.
    second = run(config, FakeHarness(), now=ENDED)
    assert (second.round_number, second.attempt) == (1, 2)


def test_ending_a_run_again_reports_the_failure_already_on_record(
    config: evolution.EvolutionConfig, admitted: None, release: str
) -> None:
    """The redo rule the other operations follow: the same statement finishes an
    interrupted one, and a run that ended some other way is reported back rather
    than overwritten."""

    pinned(config)
    harness = FakeHarness(report=completed_report())
    run(config, harness)
    replay.abandon(config, reason="the harness host was rebuilt", now=ENDED)

    again = replay.abandon(config, reason="the harness host was rebuilt", now=ENDED)
    assert again.recorded is False
    assert audit(config).count("replay-completed") == 1

    with pytest.raises(evolution.BatchError, match="already ended"):
        replay.abandon(config, reason="a different story about the same run", now=ENDED)


def test_a_run_that_outlived_its_round_still_concludes_and_reads_as_stale(
    config: evolution.EvolutionConfig, admitted: None, release: str
) -> None:
    """A conclusion is about the run, not about the round the experiment has
    since moved to: the numbers are recorded where they belong, and what makes
    them no longer evidence for a promotion is the round they name."""

    pinned(config)
    harness = FakeHarness(report=completed_report())
    run(config, harness)
    experiments.revise(config, reason="the candidate regressed on two cases", now=ENDED)

    result = replay.conclude(config, harness, now=ENDED)

    assert result.recorded is True
    assert result.round_number == 1
    evidence = replay.describe_evidence(config, live(config))
    assert evidence.round_number == 2
    assert evidence.state == replay.EVIDENCE_STALE
    assert not evidence.promotable
