"""The operator console: every durable state the lifecycle can be in, on one
read model.

`status` is the surface an operator and orch-hub both act on, and the property
this suite is about is that nothing durable is reachable only as a refusal. Three
states used to be: a promotion prepared and not finished, a replay request the
harness never answered for, and the reading a release owes. Each of them narrows
what may be done next, and an operator who cannot see one meets it as a verb that
refuses for a reason nothing on the surface said.

The other half is what the absences mean. A null here is a state and not a gap,
and the pairs that would collapse into one are what these tests hold apart: a
round nothing replayed against a batch with no round at all, an empty cohort
against a cohort that disagrees, a promotion still on the line against one an
inverse commit took back off it, and — the one an operator would act wrongly on —
"no note" against "nothing outstanding".

The gate is the same property asked about verbs rather than fields: every one of
them is named in every state, the ones this state accepts are exactly the ones
its operations accept, and each of the rest carries the reason it does not. The
case that makes it more than a menu is the reading that acts differently from how
it reads — a recorded rollback this checkout cannot recompute — where offering
"run it again" would send an operator at a verb that refuses.

Everything runs against a real repository with real operations behind it, for the
reason the promotion and assessment suites do: which side of a release a report
falls on is a question about Git ancestry, and a fixture standing in for it would
prove only that the package agrees with itself.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pytest
from evolution_fixtures import (
    RELEASE_REF,
    FakeHarness,
    complete_task,
    completed_report,
    experiment_decision,
    experiment_round,
    git_commit,
    git_repo,
    git_rev,
    git_update_ref,
    make_manifest_report,
    make_repo,
    prepared_promotion,
    promote_candidate,
    snapshot,
    write_closure,
    write_draft,
    write_experiment,
    write_manifest,
    write_rollback,
)

from ai_native_deployment import evolution
from ai_native_deployment.evolution import assessment, experiments, phase, render, replay, rollback

FIRST = "evolution-batch-0001"
SECOND = "evolution-batch-0002"
THIRD = "evolution-batch-0003"
EXP_01 = f"{SECOND}-exp-01"

PROMOTED_AT = datetime(2026, 8, 8, 9, 0, 0, tzinfo=timezone.utc)
FROZEN_AT = datetime(2026, 8, 10, 9, 0, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 8, 12, 9, 0, 0, tzinfo=timezone.utc)

EXPECTATION = "fewer remediation rounds, with quality and elapsed time unchanged"


@pytest.fixture
def config(tmp_path: Path) -> evolution.EvolutionConfig:
    """A repository with the real contract files and an admission policy low
    enough to freeze a cohort in a test."""

    root = git_repo(make_repo(tmp_path), tag="v2.2.0")
    path = root / "evolution" / "config.toml"
    text = path.read_text(encoding="utf-8")
    for old, new in (
        ("target_task_count = 20", "target_task_count = 3"),
        ("minimum_task_count = 10", "minimum_task_count = 3"),
    ):
        assert old in text
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")
    return evolution.load_config(root)


@pytest.fixture
def release(config: evolution.EvolutionConfig) -> str:
    """The source line where it stood before anything was promoted — the
    effective revision every pre-release report was produced at."""

    sha = git_rev(config.repo_root, "HEAD")
    git_update_ref(config.repo_root, RELEASE_REF, sha)
    return sha


@pytest.fixture
def promoted(config: evolution.EvolutionConfig, release: str) -> experiments.PromotionResult:
    """One whole change cycle ending in a merge on the source line, with its own
    reports standing as the cohort produced before it."""

    return promote_candidate(
        config,
        batch_id=FIRST,
        at=PROMOTED_AT,
        reports=[
            make_manifest_report(
                key=f"b{index}",
                sequence=index,
                task_id=f"2026-07-0{index}-task",
                effective_revision=release,
            )
            for index in (1, 2, 3)
        ],
    )


def freeze_cohort(
    config: evolution.EvolutionConfig,
    batch_id: str,
    *,
    effective: str | None,
    prefix: str = "a",
) -> None:
    """A frozen cohort whose reports were produced at `effective`.

    `None` is the ordinary shape of everything imported before orch-hub began
    publishing the protocol identity: no report states what its target held, so
    no report can be placed on either side of a release.
    """

    write_manifest(
        config.batches_root,
        batch_id,
        [],
        analysis_task_id=f"2026-08-10-{batch_id}",
        reports=[
            make_manifest_report(
                key=f"{prefix}{index}",
                sequence=index,
                task_id=f"2026-08-0{index}-task",
                effective_revision=effective,
            )
            for index in (1, 2, 3)
        ],
    )


def analyzed(config: evolution.EvolutionConfig, batch_id: str, *, drafts: tuple[str, ...] = ()) -> None:
    """The analysis stage ended, as every clone but the one that ran it reads
    it: the closure record, and whatever drafts it left at the gate."""

    (config.batches_root / batch_id / "findings.md").write_text("# Findings\n", encoding="utf-8")
    write_closure(config.batches_root, batch_id, analysis_task_id=f"2026-08-10-{batch_id}")
    for draft_id in drafts:
        write_draft(config.batches_root, batch_id, draft_id)


def status(config: evolution.EvolutionConfig) -> phase.LifecycleStatus:
    return phase.describe(config, now=NOW)


def verbs(config: evolution.EvolutionConfig) -> dict:
    """Every verb the gate emitted, by name — the shape a console indexes."""

    emitted = payload(config)["allowed_actions"]
    by_name = {item["action"]: item for item in emitted}
    assert len(by_name) == len(emitted), "a verb is emitted once"
    return by_name


def allows(config: evolution.EvolutionConfig) -> set[str]:
    return {name for name, item in verbs(config).items() if item["allowed"]}


def rendered(config: evolution.EvolutionConfig) -> str:
    return render.format_status(status(config))


def payload(config: evolution.EvolutionConfig) -> dict:
    return status(config).to_json()


# --- the prepared promotion --------------------------------------------------


def prepare_promotion(config: evolution.EvolutionConfig, release: str) -> str:
    """An experiment carrying a promotion prepared and not finished.

    Written rather than interrupted, because what an interruption leaves is
    exactly this record and nothing else: the merge unit is written before the
    source line moves, so a run that stops there leaves the record standing with
    the line untouched.
    """

    freeze_cohort(config, FIRST, effective=None)
    analyzed(config, FIRST, drafts=("loader-fallback",))
    candidate = git_rev(config.repo_root, "HEAD")
    write_experiment(
        config.experiments_root,
        f"{FIRST}-exp-01",
        base_revision=candidate,
        rounds=[experiment_round(1, candidate_revision=candidate)],
        promotion=prepared_promotion(
            round=1,
            candidate_revision=candidate,
            merge_input_revision=release,
            merge_input_ref=RELEASE_REF,
            planned_targets=["orch-hub"],
        ),
    )
    return candidate


def test_a_prepared_promotion_is_a_field_rather_than_a_refusal(
    config: evolution.EvolutionConfig, release: str
) -> None:
    """The narrowest state here: while it stands, `promote` is the only verb that
    experiment accepts, because the merge may already be on the source line with
    only its records missing. An operator meeting that as a refusal has nothing
    to read it from."""

    candidate = prepare_promotion(config, release)

    prepared = status(config).prepared_promotion
    assert prepared is not None
    assert prepared.candidate_revision == candidate
    assert prepared.merge_input_ref == RELEASE_REF

    block = payload(config)["experiments"]["open"]["prepared_promotion"]
    assert block["revision"] == "f" * 40
    assert block["planned_targets"] == ["orch-hub"]

    text = rendered(config)
    assert "prepared     promotion of ffffffffffff" in text
    assert "promote finishes it, or discards it" in text


def test_an_experiment_with_nothing_prepared_says_so_by_saying_nothing(
    config: evolution.EvolutionConfig, release: str
) -> None:
    """Null there means one thing and only one — no promotion prepared. The other
    absence a prepared promotion has, a version-1 record that kept no merge unit,
    belongs to an attempt that already ended, and this is asked of the open one."""

    freeze_cohort(config, FIRST, effective=None)
    analyzed(config, FIRST, drafts=("loader-fallback",))
    experiments.create(config, ["loader-fallback"], now=FROZEN_AT)

    assert status(config).prepared_promotion is None
    assert payload(config)["experiments"]["open"]["prepared_promotion"] is None
    assert "prepared " not in rendered(config)


# --- replay work in flight ---------------------------------------------------


class DeadHarness:
    """A harness that never answers, which is what leaves a request outstanding:
    the request is written before the harness is asked anything, so a start that
    dies here leaves a run that may be going and no record naming it."""

    def start(self, request: object) -> object:
        raise RuntimeError("the harness host was reclaimed before it answered")

    def poll(self, handle: str) -> object | None:
        return None


def sealed_round(config: evolution.EvolutionConfig) -> str:
    """A batch, an experiment, and a round whose candidate is pinned — the state
    a run is started from. Returns the experiment's ref."""

    freeze_cohort(config, FIRST, effective=None)
    analyzed(config, FIRST, drafts=("loader-fallback",))
    admission = experiments.create(config, ["loader-fallback"], now=FROZEN_AT)
    for item in admission.admitted:
        complete_task(config, item.task_id)
    git_update_ref(config.repo_root, admission.ref, git_commit(config.repo_root, "candidate work"))
    experiments.seal_round(config, now=FROZEN_AT)
    return admission.ref


def move_ref(config: evolution.EvolutionConfig, ref: str) -> None:
    """The experiment's ref advanced past the candidate its record pins.

    Work committed under a round that was already measured, which is the state
    every operation that pins something refuses over — and, until the gate asked
    each operation rather than assuming the preamble, the state that withheld the
    verbs answering for a run already going.
    """

    git_update_ref(config.repo_root, ref, git_commit(config.repo_root, "work after the seal"))


def measured_round(config: evolution.EvolutionConfig, release: str) -> None:
    """A batch, an experiment, a sealed round, and a completed run on it — the
    state a promotion is argued from."""

    sealed_round(config)
    harness = FakeHarness(report=completed_report())
    replay.start(config, harness, source_ref=RELEASE_REF, expectation=EXPECTATION, now=FROZEN_AT)
    replay.conclude(config, harness, now=FROZEN_AT)


def test_an_outstanding_request_is_a_field_even_where_the_evidence_is_promotable(
    config: evolution.EvolutionConfig, release: str
) -> None:
    """The one place the derivation deliberately says nothing: an outstanding
    request is left out of `drift` exactly when the evidence supports a
    promotion, because whether work in flight holds one back is the promotion
    gate's question. So a surface reading "no note" as "nothing outstanding"
    would offer a promotion over a run it has never heard about."""

    measured_round(config, release)
    with pytest.raises(RuntimeError):
        replay.start(config, DeadHarness(), source_ref=RELEASE_REF, expectation=EXPECTATION, now=NOW)

    block = payload(config)["replay"]
    assert block["state"] == replay.EVIDENCE_COMPLETE
    assert block["promotable"] is True
    assert block["drift"] == [] and block["unverified"] == []
    assert block["request"]["round"] == 1 and block["request"]["attempt"] == 2

    text = rendered(config)
    assert "request      round 1 attempt 2 outstanding since" in text
    assert "start the replay again to record it, or withdraw the request" in text


def test_a_run_that_is_going_names_the_harness_and_the_handle_it_answers_to(
    config: evolution.EvolutionConfig, release: str
) -> None:
    """The one thing in this reading that reaches the work itself. A run is
    concluded by asking that harness about that name, so a surface offering the
    conclusion — and an operator answering for a run at a harness they drive
    themselves — has to be able to say which run it is. Everything else here can
    be derived again; a handle is opaque and exists only on this record."""

    sealed_round(config)
    replay.start(config, FakeHarness(report=None), source_ref=RELEASE_REF, expectation=EXPECTATION, now=FROZEN_AT)

    block = payload(config)["replay"]
    assert block["state"] == replay.EVIDENCE_RUNNING
    assert block["run"]["harness"] == "local-replay"
    assert block["run"]["handle"] == "run-0101"
    assert "at local-replay, handle 'run-0101'" in rendered(config)


def test_a_run_going_beside_a_promotable_reading_is_named_as_well(
    config: evolution.EvolutionConfig, release: str
) -> None:
    """The reading and the run in flight are two questions, and one state pulls
    them apart: a completed attempt whose merge input has not moved stays the
    round's evidence when a second attempt is started beside it, because evidence
    that is still exact stays exact — while the verbs that answer for a run act on
    the newest one.

    So a surface reading the evidence alone offers `replay-conclude` over attempt
    1's handle and is answered about attempt 2. The field this asserts is what
    tells it which run that conclusion is for.
    """

    measured_round(config, release)
    going = FakeHarness(report=None)
    replay.start(config, going, source_ref=RELEASE_REF, expectation=EXPECTATION, now=NOW)

    block = payload(config)["replay"]
    assert block["state"] == replay.EVIDENCE_COMPLETE and block["promotable"] is True
    assert (block["run"]["attempt"], block["run"]["handle"]) == (1, "run-0101")
    assert (block["running"]["attempt"], block["running"]["handle"]) == (2, "run-0102")

    text = rendered(config)
    assert "round 1 attempt 2 is running at local-replay, handle 'run-0102'" in text
    assert "the reading above is the earlier attempt, and a conclusion answers for this one" in text

    # And the run named there is the run the offered verb acts on, which is the
    # whole of what the field is for.
    assert phase.ACTION_REPLAY_CONCLUDE in allows(config)
    going.report = completed_report()
    concluded = replay.conclude(config, going, now=NOW)
    assert (concluded.attempt, concluded.replay.harness.handle) == (2, "run-0102")


def test_a_withdrawn_position_is_reported_rather_than_lost(
    config: evolution.EvolutionConfig, release: str
) -> None:
    """A withdrawal is over as far as this controller is concerned and is exactly
    not over at the harness: the position stays allocated forever because a run
    may have been started under it that nothing here will ever hear about."""

    measured_round(config, release)
    with pytest.raises(RuntimeError):
        replay.start(config, DeadHarness(), source_ref=RELEASE_REF, expectation=EXPECTATION, now=NOW)
    replay.withdraw(config, now=NOW)

    block = payload(config)["replay"]
    assert block["request"] is None
    assert [(item["round"], item["attempt"]) for item in block["withdrawn"]] == [(1, 2)]
    assert "withdrawn    round 1 attempt 2 — given up without becoming runs" in rendered(config)


# --- the series --------------------------------------------------------------


def test_the_batch_history_is_the_series_and_each_promotion_it_made(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """Which release a cohort follows is a position in the series, not something
    a batch states about itself — so the whole series is emitted, and a promoted
    batch carries the merge unit its own outcome states."""

    freeze_cohort(config, SECOND, effective=promoted.promotion_revision)

    history = payload(config)["batches"]["history"]
    assert [item["batch_id"] for item in history] == [FIRST, SECOND]
    assert history[0]["current"] is False and history[0]["outcome"] == "promoted"
    assert history[0]["promotion"]["revision"] == promoted.promotion_revision
    assert history[0]["promotion"]["planned_targets"] == ["orch-hub"]
    assert history[1]["current"] is True
    # Null because the batch has not ended, which is a different absence from an
    # outcome nobody recorded.
    assert history[1]["outcome"] is None and history[1]["promotion"] is None

    assert f"concluded    {FIRST} promoted {promoted.promotion_revision[:12]}" in rendered(config)


def test_a_rolled_back_promotion_is_still_the_promotion_it_was(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """A rollback is a fact about the promotion rather than its absence: the
    commit was promoted and a later commit took the change back out, and a
    surface reporting only the second would lose the batch's own outcome."""

    freeze_cohort(config, SECOND, effective=promoted.promotion_revision)
    rollback.reverse(config, reason="the counterfactual confirmed the regression", now=NOW)

    entry = payload(config)["batches"]["history"][0]
    assert entry["outcome"] == "promoted"
    assert entry["promotion"]["rollback"]["reverted_at"] is not None
    assert f"concluded    {FIRST} promoted {promoted.promotion_revision[:12]}" in rendered(config)
    assert "(rolled back)" in rendered(config)


# --- the release reading -----------------------------------------------------


def test_the_reading_of_a_release_is_owed_by_the_cohort_frozen_after_it(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """The gate the next first experiment base waits on (invariant 17), and the
    surface names the line the base is expected on rather than letting an
    operator discover it from a refusal."""

    freeze_cohort(config, SECOND, effective=promoted.promotion_revision)

    block = payload(config)["release"]
    assert block["owner_batch_id"] == SECOND
    assert block["owned_here"] is True and block["owed"] is True
    assert block["settled"] is False
    assert block["assessed"]["revision"] == promoted.promotion_revision
    assert block["assessed"]["standing"] is True
    assert block["cohorts"]["before"]["task_count"] == 3
    assert block["cohorts"]["after"]["task_count"] == 3
    assert block["reading"] is None and block["decision"] is None

    text = rendered(config)
    assert f"release      {promoted.promotion_revision[:12]} from {FIRST}" in text
    assert "the reading of it is owed by this batch" in text
    assert "3 unique task(s) before the release, 3 after" in text
    assert "no reading recorded" in text


def test_the_obligation_stays_with_the_cohort_that_owes_it(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """A cohort that ends without answering does not hand the obligation on. The
    batch after it still follows the same release, and the record being waited on
    is not its own — so the surface says whose it is."""

    freeze_cohort(config, SECOND, effective=promoted.promotion_revision)
    analyzed(config, SECOND)
    experiments.conclude_no_change(config, reason="no cluster reached recurrence", now=NOW)
    freeze_cohort(config, THIRD, effective=promoted.promotion_revision, prefix="c")

    block = payload(config)["release"]
    assert block["owner_batch_id"] == SECOND
    assert block["owned_here"] is False
    assert f"owed by {SECOND}, which has already concluded" in rendered(config)


def test_empty_cohorts_read_as_absent_evidence_and_not_as_a_verdict(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """The ordinary shape of everything imported before orch-hub began publishing
    the protocol identity: no report states what its target held, so none of them
    can be placed and the cohort they would have formed is empty. An empty cohort
    also fails every comparability facet, so a surface reaching for the facet list
    here would name a provenance mismatch when what happened is that nothing could
    be placed at all."""

    freeze_cohort(config, SECOND, effective=None)

    block = payload(config)["release"]
    assert block["cohorts"]["before"]["task_count"] == 3
    assert block["cohorts"]["after"]["task_count"] == 0
    assert {item["reason"] for item in block["cohorts"]["excluded"]} == {"effective-revision-absent"}
    assert block["comparability"]["cohorts_support_direction"] is False

    text = rendered(config)
    assert "3 of 6 report(s) excluded: effective-revision-absent (3)" in text
    assert "an empty cohort carries no evidence either way" in text
    assert "not a defect and not a reading against the release" in text
    # The facet list is the wrong absence to name over a cohort nothing reached.
    assert "task-shape" not in text


def test_placed_cohorts_name_the_facet_no_manifest_states(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """Where reports can be placed, the cohorts still carry no direction — no
    manifest says what kind of work either side judged. Rendered bare that reads
    as broken provenance, so the facet is named and the counterfactual is where a
    direction is said to rest."""

    freeze_cohort(config, SECOND, effective=promoted.promotion_revision)

    text = rendered(config)
    assert "no manifest states task-shape" in text
    assert "one rests on the pinned counterfactual" in text


def test_a_settled_reading_names_the_commit_the_next_base_must_carry(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """What the answer selects is a commit as well as a direction: the first
    experiment of the next cohort has to stand on the line the gate chose, and
    discovering that from a refusal is the miss this line prevents."""

    freeze_cohort(config, SECOND, effective=promoted.promotion_revision)
    assessment.form(
        config,
        verdict=assessment.VERDICT_INCONCLUSIVE,
        confidence=assessment.CONFIDENCE_LOW,
        rationale="no frozen manifest states what kind of work either cohort did",
        now=NOW,
    )

    unsettled = rendered(config)
    assert "reading: inconclusive (low) formed" in unsettled
    assert "not settled — retain the release or roll it back" in unsettled

    assessment.settle(
        config,
        settlement=assessment.SETTLEMENT_RETAIN,
        reason="nothing measured against the release",
        now=NOW,
    )

    block = payload(config)["release"]
    assert block["settled"] is True
    assert block["decision"]["settlement"] == assessment.SETTLEMENT_RETAIN
    assert block["reading"]["verdict"] == assessment.VERDICT_INCONCLUSIVE
    settled = rendered(config)
    assert "settled retain at" in settled
    assert f"the next first experiment base must contain {promoted.promotion_revision[:12]}" in settled


def test_a_rolled_back_settlement_keeps_the_question_the_reading_answered(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """Two facts that a settled `rolled-back` reading would otherwise collapse
    into one: the reading was formed while the release stood on the source line —
    which is the question it answered — and the line no longer carries it,
    because settling `rolled-back` lands the inverse commit after the record is
    written. Reporting the line's state as the reading's would say this cohort
    assessed a reversal it never saw, and it is the ordinary end of a regression
    finding rather than a corner."""

    freeze_cohort(config, SECOND, effective=promoted.promotion_revision)
    assessment.form(
        config,
        verdict=assessment.VERDICT_INCONCLUSIVE,
        confidence=assessment.CONFIDENCE_LOW,
        rationale="no frozen manifest states what kind of work either cohort did",
        now=NOW,
    )
    standing = payload(config)["release"]["assessed"]
    assert standing["standing"] is True and standing["source_line"]["standing"] is True

    settled = assessment.settle(
        config,
        settlement=assessment.SETTLEMENT_ROLLED_BACK,
        reason="the release is taken back off the line while the shortfall is understood",
        now=NOW,
    )
    inverse = settled.decision.rollback_revision
    assert inverse is not None

    block = payload(config)["release"]
    # The release as the record is about it: standing, with no reversal named.
    assert block["assessed"]["standing"] is True
    assert block["assessed"]["rollback_revision"] is None
    # The same release as the line now holds it, which the settlement changed.
    assert block["assessed"]["source_line"]["standing"] is False
    assert block["assessed"]["source_line"]["rollback_revision"] == inverse

    text = rendered(config)
    assert f"reversed by {inverse[:12]}" in text
    assert "read while the release was still on the source line" in text
    assert f"the next first experiment base must contain {inverse[:12]}" in text


def test_the_counterfactual_is_four_states_and_a_request_is_the_first(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """They read like the replay block's states and are not it: this run is on
    the assessment record, keyed on a position no experiment holds. A request
    outstanding is the state a recorded run has no answer for — it is a
    comparison that may be going at a harness this repository will never hear
    from again, and the two things to do about it are neither of the things a run
    offers."""

    freeze_cohort(config, SECOND, effective=promoted.promotion_revision)
    assessment.form(
        config,
        verdict=assessment.VERDICT_INCONCLUSIVE,
        confidence=assessment.CONFIDENCE_LOW,
        rationale="no frozen manifest states what kind of work either cohort did",
        now=NOW,
    )

    with pytest.raises(RuntimeError):
        assessment.measure(config, DeadHarness(), expectation=EXPECTATION, now=NOW)
    outstanding = payload(config)["release"]["counterfactual"]
    assert outstanding["state"] == phase.COUNTERFACTUAL_REQUESTED
    assert outstanding["run"] is None and outstanding["request"] is not None
    waiting = rendered(config)
    assert "counterfactual: requested at" in waiting
    assert "ask again to record it, or withdraw the request" in waiting
    # Nothing may be added once the gate answers, so the settlement is refused
    # over a measurement still in flight — and the surface says so first.
    assert "conclude the run, end it, or withdraw the request first" in waiting

    # Giving the request up leaves no run, no request, and a comparison that may
    # still be going — the state the position would go missing from.
    assessment.withdraw(config, now=NOW)
    given_up = payload(config)["release"]["counterfactual"]
    assert given_up["state"] == phase.COUNTERFACTUAL_NONE
    assert given_up["request"] is None and given_up["run"] is None
    assert len(given_up["withdrawn"]) == 1
    assert "positions given up:" in rendered(config)

    harness = FakeHarness(report=None)
    assessment.measure(config, harness, expectation=EXPECTATION, now=NOW)
    going = payload(config)["release"]["counterfactual"]
    assert going["state"] == phase.COUNTERFACTUAL_RUNNING
    assert going["request"] is None and going["run"]["outcome"] is None
    # Which run it is, in both forms. `assess-conclude` polls that harness about
    # that handle, and nothing else on this record names the work — so a surface
    # given the harness alone can offer the verb and not say what it answers for.
    handle = assessment.read(config, assessment.describe_current(config).batch).counterfactual.harness.handle
    assert handle and going["run"]["handle"] == handle
    assert "counterfactual: running since" in rendered(config)
    assert f"at local-replay, handle {handle!r}" in rendered(config)

    harness.report = completed_report()
    assessment.conclude(config, harness, now=NOW)
    done = payload(config)["release"]["counterfactual"]
    assert done["state"] == phase.COUNTERFACTUAL_COMPLETED
    assert done["run"]["outcome"] == replay.RESULT_COMPLETED
    assert "counterfactual: completed" in rendered(config)


def test_a_batch_with_no_release_before_it_has_nothing_to_read(
    config: evolution.EvolutionConfig, release: str
) -> None:
    """Absent for most batches, and ordinary: only a promotion produces something
    to assess, and `no-change` produces nothing (invariant 7)."""

    freeze_cohort(config, FIRST, effective=None)

    assert status(config).release is None
    assert payload(config)["release"] is None
    assert "release " not in rendered(config)


# --- the surface itself ------------------------------------------------------


def test_the_json_and_the_human_form_come_from_one_read_model(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """One derivation behind both surfaces, and a payload that survives the wire
    it is going over."""

    freeze_cohort(config, SECOND, effective=promoted.promotion_revision)
    analyzed(config, SECOND, drafts=("loader-fallback",))

    described = status(config)
    emitted = described.to_json()

    assert emitted["schema_version"] == phase.SCHEMA_VERSION == 7
    assert json.loads(json.dumps(emitted)) == emitted
    text = render.format_status(described)
    assert emitted["summary"] in text.splitlines()[0]
    assert emitted["release"]["assessed"]["revision"][:12] in text
    assert emitted["batches"]["history"][0]["batch_id"] in text


# --- the gate ----------------------------------------------------------------


def test_every_verb_is_named_and_the_refused_ones_carry_a_reason(
    config: evolution.EvolutionConfig, release: str
) -> None:
    """A batch working its first round: what can be advanced is ending the attempt,
    because the round's task is not finished and nothing has measured anything —
    beside the two verbs whose work this state already holds, which run again to
    finish the copies that admission owes. Every other verb is still named, with
    the reason it is not one of them: a menu that listed only the legal verbs
    would leave "why not" to be discovered by running them."""

    freeze_cohort(config, FIRST, effective=None)
    analyzed(config, FIRST, drafts=("loader-fallback",))
    admission = experiments.create(config, ["loader-fallback"], now=FROZEN_AT)

    named = verbs(config)
    assert {name for name, item in named.items() if item["allowed"]} == {
        "abandon",
        "supersede",
        "create",
        "add-tasks",
        # Never a refusal: with no request outstanding the operation reports that
        # and writes nothing, which is also how one interrupted before its answer
        # is finished.
        "replay-withdraw",
    }
    assert all(item["reason"] for item in named.values() if not item["allowed"])
    assert all(item["reason"] is None for item in named.values() if item["allowed"])
    # Which of the allowed verbs are the fresh operation and which are its redo,
    # so a surface does not offer the repair as though it were work outstanding.
    assert {name for name, item in named.items() if item["recovers"]} == {
        "create",
        "add-tasks",
        "replay-withdraw",
    }
    assert named["abandon"]["recovers"] is None
    # The id a decision is given, which is the point of the object: a console
    # holding it already has the value `abandon --experiment-id` takes.
    assert named["abandon"]["object"] == {"type": "experiment", "id": admission.experiment_id}
    assert "no batch has promoted anything" in named["rollback"]["reason"]
    assert named["rollback"]["object"] is None
    assert "follows no promotion" in named["assess"]["reason"]

    text = rendered(config)
    assert f"abandon — {admission.experiment_id}" in text
    assert f"create — {admission.experiment_id} (redo)" in text
    assert "other verb(s) refuse here" in text

    # The one the seal is waiting for is the task, and it is read where the task
    # is: `.ai-tasks/` is machine-local, so this is the machine that may seal.
    assert "not ready to seal" in named["seal-round"]["reason"]
    for item in admission.admitted:
        complete_task(config, item.task_id)
    assert "seal-round" in allows(config)


def test_a_prepared_promotion_leaves_promote_the_only_verb_on_that_attempt(
    config: evolution.EvolutionConfig, release: str
) -> None:
    """The narrowest state the lifecycle has: the merge may already be on the
    source line with only its records missing, so the three verbs that would move
    the experiment out from under it refuse and the one that finishes it does
    not."""

    prepare_promotion(config, release)

    named = verbs(config)
    assert named["promote"]["allowed"] is True
    assert named["promote"]["object"] == {"type": "experiment", "id": f"{FIRST}-exp-01"}
    for verb in ("revise", "abandon", "supersede"):
        assert not named[verb]["allowed"]
        assert "prepared onto" in named[verb]["reason"]


def test_a_supersession_owing_its_successor_accepts_only_its_own_redo(
    config: evolution.EvolutionConfig, release: str
) -> None:
    """The decision landed and the attempt it names does not exist, so the batch
    has nothing to work in. Every verb but the redo refuses, and the redo is given
    the attempt that ended — the id that tells it from an untouched successor
    superseded in its turn."""

    freeze_cohort(config, FIRST, effective=None)
    analyzed(config, FIRST, drafts=("loader-fallback",))
    write_experiment(
        config.experiments_root,
        f"{FIRST}-exp-01",
        base_revision=git_rev(config.repo_root, "HEAD"),
        rounds=[experiment_round(1)],
        decision=experiment_decision("superseded", superseded_by=f"{FIRST}-exp-02"),
    )

    assert status(config).phase == phase.PHASE_SUPERSEDE_PENDING
    named = verbs(config)
    assert {name for name, item in named.items() if item["allowed"]} == {"supersede"}
    assert named["supersede"]["object"] == {"type": "experiment", "id": f"{FIRST}-exp-01"}
    assert "which does not exist" in named["abandon"]["reason"]


def test_the_release_verbs_are_legal_while_the_analysis_stage_runs(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """The reading of a release is the generated analysis task's own second
    question, taken before the dispositions close. Gating it on the stage having
    ended — as every verb that writes into the change lineage is — would refuse it
    exactly when it is meant to be used."""

    freeze_cohort(config, SECOND, effective=promoted.promotion_revision)

    named = verbs(config)
    assert named["assess"]["allowed"] is True
    assert named["assess"]["object"] == {"type": "release", "id": SECOND}
    assert "still in its analysis stage" in named["create"]["reason"]
    assert "still in its analysis stage" in named["seal-round"]["reason"]
    # The other six act on a reading, and there is none yet.
    assert "recorded no reading" in named["settle"]["reason"]


def test_a_first_base_freeze_names_the_settlement_it_waits_on(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """Invariant 17 from the operator's side: an admission that would freeze this
    batch's base is refused until the release before it is settled, and the
    refusal says whose record that is rather than leaving it to be met at the
    freeze."""

    freeze_cohort(config, SECOND, effective=promoted.promotion_revision)
    analyzed(config, SECOND, drafts=("loader-fallback",))

    named = verbs(config)
    assert not named["create"]["allowed"]
    assert "recorded no reading" in named["create"]["reason"]
    # Which line the base is expected on, said here rather than met at the freeze.
    assert "the line with it taken back out" in named["create"]["reason"]
    assert "invariant 17" in named["create"]["reason"]
    # Declining a draft is not what the settlement gates — only the base is.
    assert named["reject"]["allowed"] is True

    assessment.form(
        config,
        verdict=assessment.VERDICT_INCONCLUSIVE,
        confidence=assessment.CONFIDENCE_LOW,
        rationale="no frozen manifest states what kind of work either cohort did",
        now=NOW,
    )
    assert "nobody has settled it" in verbs(config)["create"]["reason"]

    assessment.settle(
        config,
        settlement=assessment.SETTLEMENT_RETAIN,
        reason="nothing measured against the release",
        now=NOW,
    )
    assert "create" in allows(config)


def test_an_in_flight_rollback_is_offered_only_where_this_checkout_can_finish_it(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """The one reading here that acts differently from how it reads. Both records
    say the same thing — an inverse commit exists and the line has not been
    recorded as carrying it — and the rollback operation refuses on the one whose
    commit this checkout cannot recompute, so a surface deriving "run it again"
    from the record alone offers a verb that will not run."""

    freeze_cohort(config, SECOND, effective=promoted.promotion_revision)
    rollback.reverse(config, reason="the counterfactual confirmed the regression", now=NOW)
    record = json.loads(promoted_rollback_path(config).read_text(encoding="utf-8"))

    # A run interrupted between the commit landing and the record saying so.
    promoted_rollback_path(config).write_text(json.dumps({**record, "reverted_at": None}), encoding="utf-8")
    assert "rollback" in allows(config)
    assert "run the rollback again to finish it" in rendered(config)

    # The same state, recorded against commits this checkout does not hold — the
    # ordinary shape of a rollback prepared on another machine.
    write_rollback(
        config.batches_root,
        FIRST,
        experiment_id=f"{FIRST}-exp-01",
        promotion_revision=promoted.promotion_revision,
        reverted_at=None,
    )
    refusal = verbs(config)["rollback"]
    assert not refusal["allowed"]
    assert "cannot be confirmed here" in refusal["reason"]
    text = rendered(config)
    assert "this checkout cannot confirm that commit" in text
    assert "run the rollback again to finish it" not in text


def promoted_rollback_path(config: evolution.EvolutionConfig) -> Path:
    return config.batches_root / FIRST / "rollback.json"


# --- the two forms of every verb ---------------------------------------------
#
# Every operation here is redoable by being run again with the same arguments,
# and that redo is how a durable interruption is repaired. A gate projecting only
# the fresh form would refuse exactly the verb that repairs it, so each of these
# asks the gate and then runs the operation it was answering for.


def test_a_recorded_admission_offers_the_redo_that_writes_the_copies_it_owes(
    config: evolution.EvolutionConfig, release: str
) -> None:
    """The one recovery that still writes. The record is what makes an admission
    real and the task copies come after it, so a run interrupted between the two
    leaves a copy owed — and the operation that writes it is the same admission
    run again, which the gate has to offer rather than refuse as a second
    experiment."""

    freeze_cohort(config, FIRST, effective=None)
    analyzed(config, FIRST, drafts=("loader-fallback",))
    admission = experiments.create(config, ["loader-fallback"], now=FROZEN_AT)
    admitted = admission.admitted[0]
    # What an interruption after the record leaves behind.
    admitted.task_path.unlink()

    offered = verbs(config)["create"]
    assert offered["allowed"] is True
    assert offered["object"] == {"type": "experiment", "id": admission.experiment_id}
    assert "still owes" in offered["recovers"]

    redone = experiments.create(config, ["loader-fallback"], now=FROZEN_AT)
    assert redone.created is False
    assert admitted.task_path.exists(), "the redo wrote the copy the admission owed"


def test_a_sealed_round_and_an_empty_revision_offer_the_runs_that_recorded_them(
    config: evolution.EvolutionConfig, release: str
) -> None:
    """Two retries that write nothing: a seal reports the pin on record, and a
    revision reports the round it opened. Both are what an operator reaches for
    after losing the response to a run that had already landed."""

    freeze_cohort(config, FIRST, effective=None)
    analyzed(config, FIRST, drafts=("loader-fallback",))
    admission = experiments.create(config, ["loader-fallback"], now=FROZEN_AT)
    for item in admission.admitted:
        complete_task(config, item.task_id)
    git_update_ref(config.repo_root, admission.ref, git_commit(config.repo_root, "candidate work"))
    sealed = experiments.seal_round(config, now=FROZEN_AT)

    offered = verbs(config)["seal-round"]
    assert offered["allowed"] is True
    assert "already sealed" in offered["recovers"]
    again = experiments.seal_round(config, now=FROZEN_AT)
    assert again.sealed is False and again.candidate_revision == sealed.candidate_revision

    experiments.revise(config, reason="the loader fallback needs a second pass", now=FROZEN_AT)
    offered = verbs(config)["revise"]
    assert offered["allowed"] is True
    assert "admitted nothing" in offered["recovers"]
    redone = experiments.revise(config, reason="the loader fallback needs a second pass", now=FROZEN_AT)
    assert redone.opened is False and redone.round_number == 2


def test_a_concluded_batch_offers_the_conclusion_that_ended_it(
    config: evolution.EvolutionConfig, release: str
) -> None:
    """The redo whose own state has no current batch at all: the outcome record is
    what ends one, and it lands before the audit line — so the run that finishes an
    interrupted conclusion is asking about a batch this reading calls history."""

    freeze_cohort(config, FIRST, effective=None)
    analyzed(config, FIRST)
    reason = "the reports recur on evaluation noise rather than on protocol"
    experiments.conclude_no_change(config, reason=reason, now=FROZEN_AT)

    offered = verbs(config)["conclude-no-change"]
    assert offered["allowed"] is True
    assert offered["object"] == {"type": "batch", "id": FIRST}
    assert "already concluded" in offered["recovers"]

    redone = experiments.conclude_no_change(config, reason=reason, now=FROZEN_AT)
    assert redone.recorded is False and redone.batch_id == FIRST


def test_a_finished_rollback_offers_the_request_that_recorded_it(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """A reversal is recorded before its audit line like everything else here, so
    the same request run again reports it. The gate used to call that terminal —
    which is true of the reversal and not of the verb."""

    freeze_cohort(config, SECOND, effective=promoted.promotion_revision)
    reason = "the counterfactual confirmed the regression"
    reversal = rollback.reverse(config, reason=reason, now=NOW)

    offered = verbs(config)["rollback"]
    assert offered["allowed"] is True
    assert offered["object"] == {"type": "promotion", "id": promoted.promotion_revision}
    assert "run again for the reason on record" in offered["recovers"]

    redone = rollback.reverse(config, reason=reason, now=NOW)
    assert redone.recorded is False and redone.revision == reversal.revision


def test_a_settled_release_offers_the_answer_that_settled_it(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """The settlement's redo is the one that finishes a two-write operation: a
    `rolled-back` answer lands its inverse commit before its decision record, so an
    operator who lost the response between them has this verb and nothing else."""

    freeze_cohort(config, SECOND, effective=promoted.promotion_revision)
    assessment.form(
        config,
        verdict=assessment.VERDICT_INCONCLUSIVE,
        confidence=assessment.CONFIDENCE_LOW,
        rationale="no frozen manifest states what kind of work either cohort did",
        now=NOW,
    )
    reason = "nothing measured against the release"
    assessment.settle(config, settlement=assessment.SETTLEMENT_RETAIN, reason=reason, now=NOW)

    named = verbs(config)
    assert named["settle"]["allowed"] is True
    assert "answer redone reports the decision" in named["settle"]["recovers"]
    # The reading itself is still its own formation's redo; the four verbs that
    # would add to it are closed by the answer.
    assert named["assess"]["allowed"] is True
    assert not named["assess-measure"]["allowed"]
    assert not named["assess-resolve"]["allowed"]

    redone = assessment.settle(
        config, settlement=assessment.SETTLEMENT_RETAIN, reason=reason, now=NOW
    )
    assert redone.recorded is False

    formed_again = assessment.form(
        config,
        verdict=assessment.VERDICT_INCONCLUSIVE,
        confidence=assessment.CONFIDENCE_LOW,
        rationale="no frozen manifest states what kind of work either cohort did",
        now=NOW,
    )
    assert formed_again.recorded is False


# --- one statement of every refusal ------------------------------------------
#
# The gate reads the owning modules' own predicates rather than restating what
# they check (`guards.stage_refusal` and the preamble beside it,
# `experiments.sealable_refusal` and its neighbours, `replay.conclude_refusal`,
# `assessment.settleable_refusal`, …). These ask the gate and then run the very
# operation it was answering for, and hold the two answers to being the same
# sentence — a rule written down twice is a gate that comes to refuse what the
# operation allows, or offer what it refuses.


def refusal(config: evolution.EvolutionConfig, verb: str) -> str:
    offered = verbs(config)[verb]
    assert offered["allowed"] is False, f"{verb} is refused here"
    return offered["reason"]


def raised(operation: Callable[[], object]) -> str:
    with pytest.raises(evolution.BatchError) as error:
        operation()
    return str(error.value)


def test_the_change_verbs_refuse_in_the_words_their_operations_refuse_with(
    config: evolution.EvolutionConfig, release: str
) -> None:
    """One walk through a batch, asking the gate at each state and then running
    the operation it refused. Every sentence is the operation's own."""

    freeze_cohort(config, FIRST, effective=None)
    write_draft(config.batches_root, FIRST, "loader-fallback")

    # The analysis stage, which the preamble every guarded operation runs stops
    # every one of them at.
    assert refusal(config, "create") == raised(
        lambda: experiments.create(config, ["loader-fallback"], now=FROZEN_AT)
    )
    assert refusal(config, "seal-round") == raised(lambda: experiments.seal_round(config, now=FROZEN_AT))

    # The stage has ended and nothing is admitted: the attempt these act on is
    # not there, in each verb's own words.
    analyzed(config, FIRST, drafts=("loader-fallback",))
    assert refusal(config, "seal-round") == raised(lambda: experiments.seal_round(config, now=FROZEN_AT))
    assert refusal(config, "add-tasks") == raised(
        lambda: experiments.add_tasks(config, ["loader-fallback"], now=FROZEN_AT)
    )
    assert refusal(config, "replay-start") == raised(
        lambda: replay.start(
            config, FakeHarness(), source_ref=RELEASE_REF, expectation=EXPECTATION, now=FROZEN_AT
        )
    )

    # A round with work in it: what it is not is a round to revise, to measure,
    # or to end a batch over.
    admission = experiments.create(config, ["loader-fallback"], now=FROZEN_AT)
    revision = "the loader fallback needs a second pass"
    assert refusal(config, "revise") == raised(lambda: experiments.revise(config, reason=revision, now=FROZEN_AT))
    assert refusal(config, "replay-start") == raised(
        lambda: replay.start(
            config, FakeHarness(), source_ref=RELEASE_REF, expectation=EXPECTATION, now=FROZEN_AT
        )
    )
    assert refusal(config, "conclude-no-change") == raised(
        lambda: experiments.conclude_no_change(config, reason="the evidence justified nothing", now=FROZEN_AT)
    )

    # Sealed: the round no longer takes work, and nothing has measured it.
    for item in admission.admitted:
        complete_task(config, item.task_id)
    git_update_ref(config.repo_root, admission.ref, git_commit(config.repo_root, "candidate work"))
    experiments.seal_round(config, now=FROZEN_AT)
    assert refusal(config, "add-tasks") == raised(
        lambda: experiments.add_tasks(config, ["loader-fallback"], now=FROZEN_AT)
    )
    assert refusal(config, "replay-conclude") == raised(lambda: replay.conclude(config, FakeHarness(), now=FROZEN_AT))
    assert refusal(config, "replay-abandon") == raised(
        lambda: replay.abandon(config, reason="the harness lost the handle", now=FROZEN_AT)
    )
    assert refusal(config, "promote") == raised(
        lambda: experiments.promote(config, reason="the evidence held", targets=(), now=FROZEN_AT)
    )


def test_the_release_verbs_refuse_in_the_words_their_operations_refuse_with(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """The same property on the seven verbs whose guard is not the shared
    preamble: what the reading is at, said once by the module that owns it."""

    freeze_cohort(config, SECOND, effective=promoted.promotion_revision)

    # A cohort that owes a reading and has recorded none: six of the seven have
    # nothing to act on.
    assert refusal(config, "settle") == raised(
        lambda: assessment.settle(config, settlement=assessment.SETTLEMENT_RETAIN, reason="nothing found", now=NOW)
    )
    assert refusal(config, "assess-resolve") == raised(
        lambda: assessment.resolve(
            config,
            verdict=assessment.VERDICT_IMPROVED,
            confidence=assessment.CONFIDENCE_LOW,
            rationale="the counterfactual moved the goal metric",
            now=NOW,
        )
    )
    assert refusal(config, "assess-conclude") == raised(lambda: assessment.conclude(config, FakeHarness(), now=NOW))

    assessment.form(
        config,
        verdict=assessment.VERDICT_INCONCLUSIVE,
        confidence=assessment.CONFIDENCE_LOW,
        rationale="no frozen manifest states what kind of work either cohort did",
        now=NOW,
    )
    # A reading with no run under it: the two that answer for one, and the
    # resolution that a completed one settles.
    assert refusal(config, "assess-abandon") == raised(
        lambda: assessment.abandon(config, reason="the harness never reported", now=NOW)
    )
    assert refusal(config, "assess-resolve") == raised(
        lambda: assessment.resolve(
            config,
            verdict=assessment.VERDICT_IMPROVED,
            confidence=assessment.CONFIDENCE_LOW,
            rationale="the counterfactual moved the goal metric",
            now=NOW,
        )
    )

    # A run going is a measurement in flight, which the gate is not answered over.
    assessment.measure(config, FakeHarness(report=None), expectation=EXPECTATION, now=NOW)
    assert refusal(config, "assess-measure") == raised(
        lambda: assessment.measure(config, FakeHarness(), expectation=EXPECTATION, now=NOW)
    )
    assert refusal(config, "settle") == raised(
        lambda: assessment.settle(config, settlement=assessment.SETTLEMENT_RETAIN, reason="nothing found", now=NOW)
    )


def test_a_withdrawal_over_nothing_outstanding_is_offered_rather_than_refused(
    config: evolution.EvolutionConfig, release: str
) -> None:
    """The verb the shared predicates found: both withdrawals report that nothing
    was outstanding rather than refusing, because a single write with nothing
    after it either landed or did not — so a gate refusing them would withhold the
    verb that finishes an interrupted one."""

    freeze_cohort(config, FIRST, effective=None)
    analyzed(config, FIRST, drafts=("loader-fallback",))
    experiments.create(config, ["loader-fallback"], now=FROZEN_AT)

    offered = verbs(config)["replay-withdraw"]
    assert offered["allowed"] is True
    assert "no replay request is outstanding" in offered["recovers"]
    assert replay.withdraw(config, now=FROZEN_AT).withdrawn is False


def test_a_moved_ref_leaves_the_verbs_that_answer_for_a_run_alone(
    config: evolution.EvolutionConfig, release: str
) -> None:
    """The ref hold belongs to the operations that pin something. A run that is
    going was pinned when the ref agreed, and what a conclusion writes is how that
    run ended — so a ref moved since makes the evidence stale, which the reading
    says, rather than unrecordable, which withholding the verb would make it."""

    ref = sealed_round(config)
    replay.start(config, FakeHarness(report=None), source_ref=RELEASE_REF, expectation=EXPECTATION, now=FROZEN_AT)
    move_ref(config, ref)

    named = verbs(config)
    assert named["replay-conclude"]["allowed"] is True
    assert named["replay-abandon"]["allowed"] is True

    # The same ref, still held where the operation holds it: a seal pins the next
    # candidate, so it refuses here in its own words.
    assert refusal(config, "seal-round") == raised(lambda: experiments.seal_round(config, now=NOW))

    concluded = replay.conclude(config, FakeHarness(report=completed_report()), now=NOW)
    assert concluded.recorded is True and concluded.outcome == replay.RESULT_COMPLETED


def test_an_outstanding_request_is_resumed_over_the_ref_that_moved_under_it(
    config: evolution.EvolutionConfig, release: str
) -> None:
    """A request the harness never answered for is the state a run may be going
    in with nothing here naming it. Its two ways out are resuming the start and
    withdrawing the request, and neither asks the ref — a resume pins nothing, it
    records what the request already pinned."""

    ref = sealed_round(config)
    with pytest.raises(RuntimeError):
        replay.start(config, DeadHarness(), source_ref=RELEASE_REF, expectation=EXPECTATION, now=FROZEN_AT)
    move_ref(config, ref)

    named = verbs(config)
    assert named["replay-start"]["allowed"] is True
    assert "resumes that request" in named["replay-start"]["recovers"]
    # Allowed and not a redo: a request is outstanding, so this one has something
    # to give up rather than a landed write to report.
    assert named["replay-withdraw"]["allowed"] is True and named["replay-withdraw"]["recovers"] is None

    resumed = replay.start(
        config, FakeHarness(report=None), source_ref=RELEASE_REF, expectation=EXPECTATION, now=NOW
    )
    assert resumed.resumed is True, "the operation recorded the run the request had asked for"

    # And with that request answered, a start is a fresh one again — the single
    # form here that pins an integration, and the ref is its own hold.
    assert refusal(config, "replay-start") == raised(
        lambda: replay.start(config, FakeHarness(), source_ref=RELEASE_REF, expectation=EXPECTATION, now=NOW)
    )


def test_a_request_outstanding_over_a_revised_round_is_still_the_one_to_resume(
    config: evolution.EvolutionConfig, release: str
) -> None:
    """The other half of what a resume does not ask. `replay.start_refusal` is
    about making a request, and a round opened since is a reason to make no new
    one — never a reason to withhold the verb that records the run the harness
    was already asked for."""

    sealed_round(config)
    with pytest.raises(RuntimeError):
        replay.start(config, DeadHarness(), source_ref=RELEASE_REF, expectation=EXPECTATION, now=FROZEN_AT)
    experiments.revise(config, reason="the loader fallback needs a second pass", now=FROZEN_AT)

    offered = verbs(config)["replay-start"]
    assert offered["allowed"] is True
    assert "round 1 attempt 1" in offered["recovers"]

    resumed = replay.start(
        config, FakeHarness(report=None), source_ref=RELEASE_REF, expectation=EXPECTATION, now=NOW
    )
    assert resumed.resumed is True and resumed.round_number == 1


def test_the_state_revision_follows_the_artifacts_and_not_the_clock(
    config: evolution.EvolutionConfig, release: str
) -> None:
    """What the token is for: a mutation handed it can refuse rather than act on a
    lifecycle another writer moved. So it moves when a record does — and not with
    time, since a token that expired overnight would refuse operations over a
    repository nobody wrote to."""

    freeze_cohort(config, FIRST, effective=None)
    analyzed(config, FIRST, drafts=("loader-fallback", "hook-side-loader"))

    first = status(config).state_revision
    assert first.startswith(f"{phase.STATE_REVISION_VERSION}-")
    assert phase.describe(config, now=NOW + timedelta(days=30)).state_revision == first

    experiments.reject(config, ["hook-side-loader"], reason="one report is not recurrence", now=NOW)
    assert status(config).state_revision != first


def test_the_state_revision_covers_the_commit_a_first_base_would_freeze(
    config: evolution.EvolutionConfig, release: str
) -> None:
    """The one revision an operation resolves that no record names yet. A batch
    with no experiment has no base, no candidate and no promotion to stand on, and
    the grouped admission that opens its first attempt freezes `HEAD` — so a
    checkout that moved between the reading and the write would freeze a base
    nobody read, on a token that never noticed."""

    freeze_cohort(config, FIRST, effective=None)
    analyzed(config, FIRST, drafts=("loader-fallback",))
    assert status(config).revisions.base is None

    before = status(config).state_revision
    git_commit(config.repo_root, "work an operator committed after reading the status")
    assert status(config).state_revision != before


def test_a_reading_is_never_published_with_a_token_from_after_it(
    config: evolution.EvolutionConfig, release: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pair a stale-state guard exists to refuse, and the one shape that would
    slip past it: a reading of the repository before a write, carrying the token of
    the repository after it. The mutation given that token compares it against a
    freshly derived one, finds it current, and acts on a reading of a lifecycle
    that has since moved — so the reading is taken again instead."""

    freeze_cohort(config, FIRST, effective=None)
    analyzed(config, FIRST, drafts=("loader-fallback", "hook-side-loader"))

    read = phase._read
    taken: list[phase.LifecycleStatus] = []

    def racing(cfg: evolution.EvolutionConfig, moment: datetime) -> tuple:
        reading, lineage = read(cfg, moment)
        if not taken:
            # A writer finishing while the lifecycle was being read: the record
            # lands after this reading saw the draft waiting, and before the
            # token is taken.
            experiments.reject(cfg, ["hook-side-loader"], reason="one report is not recurrence", now=NOW)
        taken.append(reading)
        return reading, lineage

    monkeypatch.setattr(phase, "_read", racing)
    reading = status(config)

    assert len(taken) == 2, "the reading the write landed under is taken again rather than published"
    assert taken[0].gate is not None and len(taken[0].gate.waiting) == 2
    assert reading.gate is not None and reading.gate.waiting == ("loader-fallback",)
    # And the token published is of the state that was read: the same one a
    # mutation derives for itself a moment later.
    assert reading.state_revision == status(config).state_revision


def test_the_state_revision_follows_the_refs_the_lifecycle_stands_on(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """A source line that moved is not visible in any record, and it is what makes
    replay evidence stale — so the refs in play are part of what this reading is
    of."""

    freeze_cohort(config, SECOND, effective=promoted.promotion_revision)
    before = status(config).state_revision

    git_update_ref(config.repo_root, RELEASE_REF, git_commit(config.repo_root, "later work on the source line"))
    assert status(config).state_revision != before


def test_status_writes_nothing(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """A pure read over the authoritative artifacts, durable refs, and Git —
    including the release frame, which resolves revisions and places reports
    without recording either."""

    freeze_cohort(config, SECOND, effective=promoted.promotion_revision)
    before = snapshot(config.repo_root)

    render.format_status(status(config))
    json.dumps(payload(config))

    assert snapshot(config.repo_root) == before


# --- what the targets hold ---------------------------------------------------
#
# The plan a promotion recorded and the revisions its targets carry are two
# lists of the same names, and everything here is about not letting the first
# stand in for the second. A promoted commit reaches a target when that target
# is redeployed and not before, so the reading comes from each target's own
# receipt — and every reason it could not be read is a state of that target
# rather than a defect in this lifecycle.


@pytest.fixture
def managed(
    config: evolution.EvolutionConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., Path]:
    """A machine managing target repositories, and nothing outside this test.

    The registry is machine-local and its location is overridable, so the
    override is cleared: a developer who exported it would otherwise have their
    own targets read for these assertions.
    """

    monkeypatch.delenv("AI_NATIVE_DEPLOYMENT_REGISTRY", raising=False)
    targets = tmp_path / "targets"

    def register(name: str, *, revision: str | None = None, receipt: bool = True, path: Path | None = None) -> Path:
        root = path or (targets / name)
        root.mkdir(parents=True, exist_ok=True)
        if receipt:
            (root / ".ai-deploy-lock.json").write_text(
                json.dumps({"schema_version": 1, "source_git_commit": revision}), encoding="utf-8"
            )
        registry = config.repo_root / ".registry" / "repos.local.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        entries = json.loads(registry.read_text(encoding="utf-8")) if registry.is_file() else []
        entries.append({"name": name, "path": str(root), "manifest": str(root / ".ai-deploy-manifest.json")})
        registry.write_text(json.dumps(entries), encoding="utf-8")
        return root

    return register


def deployed(config: evolution.EvolutionConfig) -> dict:
    return payload(config)["deployment"]


def test_what_a_target_holds_is_read_from_its_receipt_and_never_from_the_plan(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
    managed: Callable[..., Path],
) -> None:
    """The two lists side by side: the names the promotion planned for, and the
    revision each of those repositories actually carries."""

    target = managed("orch-hub", revision=promoted.promotion_revision)
    freeze_cohort(config, SECOND, effective=promoted.promotion_revision)

    block = deployed(config)
    assert block["promotion"] == promoted.promotion_revision and block["rollback"] is None
    assert block["targets"] == [
        {
            "target": "orch-hub",
            "path": str(target),
            "revision": promoted.promotion_revision,
            "state": "carries",
            "detail": None,
        }
    ]
    # The plan is still the plan, in its own place and unchanged by the reading.
    assert payload(config)["last_promotion"]["planned_targets"] == ["orch-hub"]

    text = rendered(config)
    assert "planned targets: orch-hub — the plan this promotion recorded, not what they hold" in text
    assert "deployed     what each planned target holds now, from its own .ai-deploy-lock.json:" in text
    assert f"orch-hub at {promoted.promotion_revision[:12]} — carries this promotion" in text


def test_a_target_deployed_before_the_promotion_is_the_redeploy_it_is_owed(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
    release: str,
    managed: Callable[..., Path],
) -> None:
    """The state the plan would have hidden: the promotion is on the source line
    and that repository is still running what came before it."""

    managed("orch-hub", revision=release)
    freeze_cohort(config, SECOND, effective=promoted.promotion_revision)

    assert [item["state"] for item in deployed(config)["targets"]] == ["behind"]
    assert f"orch-hub at {release[:12]} — does not carry this promotion" in rendered(config)


def test_the_reasons_a_target_cannot_be_placed_are_not_one_state(
    config: evolution.EvolutionConfig, release: str, tmp_path: Path, managed: Callable[..., Path]
) -> None:
    """Five different next steps, and none of them is "redeploy": a name this
    machine manages no repository for, a repository nothing has deployed to, a
    receipt tied to no source commit, a revision this checkout does not hold, and
    a name two registered repositories share. Reporting any of them as `behind`
    would name work that is not owed."""

    promotion = promote_candidate(
        config,
        batch_id=FIRST,
        at=PROMOTED_AT,
        targets=("elsewhere", "fresh", "untied", "far-off", "twice-over"),
    )
    managed("fresh", receipt=False)
    managed("untied", revision=None)
    managed("far-off", revision="f" * 40)
    managed("twice-over", revision=promotion.promotion_revision)
    managed("twice-over", revision=release, path=tmp_path / "elsewhere" / "twice-over")
    freeze_cohort(config, SECOND, effective=promotion.promotion_revision)

    targets = deployed(config)["targets"]
    states = {item["target"]: item for item in targets}
    assert [item["target"] for item in targets] == ["elsewhere", "fresh", "untied", "far-off", "twice-over"]
    assert states["elsewhere"]["state"] == "unregistered" and states["elsewhere"]["path"] is None
    assert states["fresh"]["state"] == "no-receipt"
    assert states["untied"]["state"] == "unstated" and states["untied"]["revision"] is None
    assert states["far-off"]["state"] == "unplaceable"
    # A plan names one target, and picking between two repositories registered
    # under that name would answer for one the plan may not have meant.
    assert states["twice-over"]["state"] == "ambiguous" and states["twice-over"]["path"] is None

    text = rendered(config)
    assert "elsewhere — no repository of that name is registered on this machine, so this machine cannot say" in text
    assert "fresh — no deploy receipt at" in text
    assert "untied — the receipt ties its payload to no source commit" in text
    assert "far-off at ffffffffffff — this checkout does not hold that commit" in text
    assert "twice-over — 2 repositories are registered under that name" in text


def test_a_target_carrying_a_reversed_promotion_is_not_a_target_up_to_date(
    config: evolution.EvolutionConfig, release: str, managed: Callable[..., Path]
) -> None:
    """The one state whose meaning turns on the rollback: with an inverse commit
    on the line, a target still carrying the promotion is running the change that
    was taken back, and the redeploy it is owed is the reversal. The target that
    took the reversal reads as the different state it is."""

    promotion = promote_candidate(config, batch_id=FIRST, at=PROMOTED_AT, targets=("orch-hub", "shipped-back"))
    freeze_cohort(config, SECOND, effective=promotion.promotion_revision)
    reversal = rollback.reverse(config, reason="the counterfactual confirmed the regression", now=NOW)
    managed("orch-hub", revision=promotion.promotion_revision)
    managed("shipped-back", revision=reversal.revision)

    block = deployed(config)
    assert block["promotion"] == promotion.promotion_revision and block["rollback"] == reversal.revision
    assert {item["target"]: item["state"] for item in block["targets"]} == {
        "orch-hub": "carries",
        "shipped-back": "reversed",
    }

    text = rendered(config)
    assert "orch-hub at" in text and "carries this promotion and not the commit that took it back out" in text
    assert "shipped-back at" in text and "carries the inverse commit as well" in text


def test_a_file_this_machine_cannot_read_is_that_target_and_not_the_console(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
    managed: Callable[..., Path],
) -> None:
    """The opposite of how this package treats its own malformed records, and
    deliberately: an evolution record that will not read back is damage to the
    lifecycle, while a file in somebody else's repository — or in a local
    inventory — is a question this reading could not answer. Taking the gate,
    the pool and the experiment away from an operator over it would be the
    wrong trade."""

    target = managed("orch-hub", receipt=False)
    (target / ".ai-deploy-lock.json").write_text("{ not json", encoding="utf-8")
    freeze_cohort(config, SECOND, effective=promoted.promotion_revision)

    assert deployed(config)["targets"][0]["state"] == "unreadable"
    assert "orch-hub — unreadable: " in rendered(config)
    # The rest of the reading is untouched by it.
    assert payload(config)["phase"] == "batch-frozen"

    (config.repo_root / ".registry" / "repos.local.json").write_text("{}", encoding="utf-8")
    assert [item["state"] for item in deployed(config)["targets"]] == ["unreadable"]
    assert payload(config)["phase"] == "batch-frozen"


def test_an_inventory_this_process_cannot_read_at_all_is_still_every_targets_state(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
    managed: Callable[..., Path],
) -> None:
    """The registry's own complaint about a document that is not a registry is
    one of three ways it fails to answer, and the other two do not arrive named:
    a file this process cannot read at all, and bytes that are not text. Letting
    either escape would fail the whole console over a machine-local inventory
    that holds no lifecycle state — which is the opposite of what this reading
    promises, and invisible until the file is one of those two things."""

    managed("orch-hub", revision=promoted.promotion_revision)
    freeze_cohort(config, SECOND, effective=promoted.promotion_revision)
    registry = config.repo_root / ".registry" / "repos.local.json"

    # Bytes that are not UTF-8 text: a `UnicodeDecodeError`, which is a
    # `ValueError` and not the registry module's own error.
    registry.write_bytes(b"\xff\xfe not text")
    holdings = deployed(config)["targets"]
    assert [item["state"] for item in holdings] == ["unreadable"]
    # The path is stated by this reading, since a decode error names a byte
    # offset and no file at all.
    assert str(registry) in holdings[0]["detail"]
    assert payload(config)["phase"] == "batch-frozen"

    # A file this process cannot read: the path is a directory, which is an
    # `OSError` on the way to the bytes.
    registry.unlink()
    registry.mkdir()
    assert [item["state"] for item in deployed(config)["targets"]] == ["unreadable"]
    assert payload(config)["phase"] == "batch-frozen"
    assert "orch-hub — unreadable: " in rendered(config)


def test_a_receipt_is_placed_only_where_it_states_a_commit_this_build_wrote(
    config: evolution.EvolutionConfig,
    release: str,
    managed: Callable[..., Path],
) -> None:
    """The receipt is a file in a repository this tool does not own, and the
    revision in it is resolved *here* — so a document that is not the one this
    deploy contract writes is that target's `unreadable` state rather than a
    reading.

    The symbolic case is the one that would be silent: `HEAD` resolves in this
    checkout, so it would report a target as carrying a promotion nothing was
    ever deployed to, and would change answer whenever this checkout moved. An
    abbreviation is refused for the other half of the same reason — it names one
    commit today and may name two tomorrow — and an unsupported schema because a
    field this build knows by name may mean something else in the document that
    wrote it."""

    promotion = promote_candidate(
        config,
        batch_id=FIRST,
        at=PROMOTED_AT,
        targets=("symbolic", "abbreviated", "later-schema"),
    )
    freeze_cohort(config, SECOND, effective=promotion.promotion_revision)
    receipts = {
        "symbolic": {"schema_version": 1, "source_git_commit": "HEAD"},
        "abbreviated": {"schema_version": 1, "source_git_commit": promotion.promotion_revision[:12]},
        "later-schema": {"schema_version": 999, "source_git_commit": promotion.promotion_revision},
    }
    for target, receipt in receipts.items():
        root = managed(target, receipt=False)
        (root / ".ai-deploy-lock.json").write_text(json.dumps(receipt), encoding="utf-8")

    targets = {item["target"]: item for item in deployed(config)["targets"]}
    assert [item["state"] for item in targets.values()] == ["unreadable"] * 3
    # Nothing of a refused receipt is reported as a reading: no revision, and a
    # detail naming the file and what the deploy contract said about it.
    assert [item["revision"] for item in targets.values()] == [None] * 3
    assert all(".ai-deploy-lock.json:" in item["detail"] for item in targets.values())
    assert "is not a commit id" in targets["symbolic"]["detail"]
    assert "'HEAD'" in targets["symbolic"]["detail"]
    assert "is not a commit id" in targets["abbreviated"]["detail"]
    assert "schema_version 999" in targets["later-schema"]["detail"]
    assert "unreadable: " in rendered(config)

    # The proof that the symbolic one is not a formatting preference: this
    # checkout's `HEAD` does carry that promotion, so a receipt read as stating
    # it would have read as `carries` here and as something else in the next
    # clone.
    assert git_rev(config.repo_root, "HEAD") == promotion.promotion_revision


def test_a_receipt_that_states_no_source_commit_is_not_one_stating_none(
    config: evolution.EvolutionConfig,
    release: str,
    managed: Callable[..., Path],
) -> None:
    """The two absences of one field, and they are two different next steps.

    A receipt stating `null` is the deploy contract's own answer — that deploy
    could not tie the payload it wrote to a commit, so nothing places what that
    target holds and the operator's move is to commit the canonical work and
    redeploy. A receipt with no such field never came from a deploy this build
    reads: it answers nothing, and the operator's move is to look at the file.
    Both asked in one reading, because the way they collapse is a reader taking
    the second for the first and reporting a document it could not read as a
    target it merely could not place.
    """

    promotion = promote_candidate(config, batch_id=FIRST, at=PROMOTED_AT, targets=("truncated", "untied"))
    freeze_cohort(config, SECOND, effective=promotion.promotion_revision)
    root = managed("truncated", receipt=False)
    (root / ".ai-deploy-lock.json").write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    managed("untied", revision=None)

    targets = {item["target"]: item for item in deployed(config)["targets"]}
    assert targets["truncated"]["state"] == "unreadable"
    assert targets["untied"]["state"] == "unstated"
    # Neither states a revision, which is exactly why the state is what tells
    # them apart and the detail is what an operator acts on.
    assert [targets["truncated"]["revision"], targets["untied"]["revision"]] == [None, None]
    assert ".ai-deploy-lock.json: source_git_commit is absent" in targets["truncated"]["detail"]
    assert "the receipt ties its payload to no source commit" in targets["untied"]["detail"]

    # The human form says the same two things, the unreadable one naming the file
    # it could not read as well as what was wrong with it.
    text = rendered(config)
    assert f"truncated — unreadable: {targets['truncated']['detail']}" in text
    assert "source_git_commit is absent" in text
    assert "untied — the receipt ties its payload to no source commit" in text


def test_a_repository_that_promoted_nothing_reads_no_targets(
    config: evolution.EvolutionConfig, managed: Callable[..., Path]
) -> None:
    """Null rather than an empty list: the plan is where the targets in play come
    from, so with no promotion there is nothing to answer for — which is a
    different thing from a promotion whose targets all read as absent."""

    managed("orch-hub", revision=git_rev(config.repo_root, "HEAD"))
    freeze_cohort(config, FIRST, effective=None)

    assert payload(config)["deployment"] is None
    assert "deployed" not in rendered(config)
