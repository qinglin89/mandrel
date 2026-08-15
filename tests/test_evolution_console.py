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


def measured_round(config: evolution.EvolutionConfig, release: str) -> None:
    """A batch, an experiment, a sealed round, and a completed run on it — the
    state a promotion is argued from."""

    freeze_cohort(config, FIRST, effective=None)
    analyzed(config, FIRST, drafts=("loader-fallback",))
    admission = experiments.create(config, ["loader-fallback"], now=FROZEN_AT)
    for item in admission.admitted:
        complete_task(config, item.task_id)
    git_update_ref(config.repo_root, admission.ref, git_commit(config.repo_root, "candidate work"))
    experiments.seal_round(config, now=FROZEN_AT)
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
    assert "counterfactual: running since" in rendered(config)

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
    """A batch working its first round: the only things to do are end the attempt,
    because the round's task is not finished, nothing has measured anything, and
    the gate is empty. Every other verb is still named, with the reason it is not
    one of them — a menu that listed only the legal verbs would leave "why not" to
    be discovered by running them."""

    freeze_cohort(config, FIRST, effective=None)
    analyzed(config, FIRST, drafts=("loader-fallback",))
    admission = experiments.create(config, ["loader-fallback"], now=FROZEN_AT)

    named = verbs(config)
    assert {name for name, item in named.items() if item["allowed"]} == {"abandon", "supersede"}
    assert all(item["reason"] for item in named.values() if not item["allowed"])
    assert all(item["reason"] is None for item in named.values() if item["allowed"])
    # The id a decision is given, which is the point of the object: a console
    # holding it already has the value `abandon --experiment-id` takes.
    assert named["abandon"]["object"] == {"type": "experiment", "id": admission.experiment_id}
    assert "no batch has promoted anything" in named["rollback"]["reason"]
    assert named["rollback"]["object"] is None
    assert "follows no promotion" in named["assess"]["reason"]

    text = rendered(config)
    assert f"actions      abandon — {admission.experiment_id}" in text
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
    assert "which was never created" in named["abandon"]["reason"]


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
    assert "nothing has read" in named["create"]["reason"]
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
