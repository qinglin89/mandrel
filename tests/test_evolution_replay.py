"""Replay evidence: its versioned contract, and what it is read to mean.

Two halves, the way the lineage suite is built.

The first is the schema file, which is the contract (`schema.py`) — so the
instances here are hand-written rather than produced by the package. Nothing
writes replay records yet, and a fixture built from the writer would only prove
the writer agrees with itself.

The second is `replay.py`: the reader that checks a run against the round it
claims to have measured, and the derivation that says whether any of it still
describes the tree a promotion would carry. The hostile cases are the ones a
well-formed record can still be wrong in — a candidate that is not the one the
round's seal pinned, a report carried across a `revise`, a source line that moved
after the numbers were taken — because each of them reads as ordinary evidence
right up to the promotion it would justify.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from evolution_fixtures import (
    REPO_ROOT,
    admitted_task,
    experiment_round,
    git_commit,
    git_repo,
    git_rev,
    git_update_ref,
    make_repo,
    measurement,
    replay_entry,
    replay_integration,
    replay_result,
    write_experiment,
    write_replays,
)

from ai_native_deployment import evolution
from ai_native_deployment.evolution import lineage, replay, schema

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


@pytest.mark.parametrize("ref", ["refs/heads/a..b", "refs/heads/.hidden", "refs/heads/release.lock"])
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
