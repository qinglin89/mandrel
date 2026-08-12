"""Whether a promoted release actually improved the work that came after it.

Everything here runs against a real Git repository with a real promotion on it,
for the reason the promotion and rollback suites do: which cohort a report
belongs to is a question about ancestry — did the line that target held carry the
change — and a fixture standing in for Git would prove only that the package
agrees with itself.

Two properties get most of the attention, because they are what this artifact
exists for:

- **A directional claim needs cohorts that can carry one.** Mixed provenance, a
  cohort below the minimum unique-task count, an unmeasured quantity, and a
  regression nobody counterfactually measured are each refused — in the record as
  well as in the derivation, since a rule only the writer keeps is one any file
  written beside it escapes.
- **Nothing is invented where there is nothing to assess.** A repository that
  never promoted, a `no-change` predecessor, and a batch whose predecessor's
  release was already assessed all produce no frame at all rather than an
  upgrade effect with no upgrade behind it.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from evolution_fixtures import (
    RELEASE_REF,
    git_file_commit,
    git_repo,
    git_rev,
    git_update_ref,
    make_manifest_report,
    make_record,
    make_repo,
    promote_candidate,
    write_feed,
    write_manifest,
    write_outcome,
)

from ai_native_deployment import evolution
from ai_native_deployment.evolution import assessment, batches, experiments, lineage, replay, rollback

FIRST = "evolution-batch-0001"
SECOND = "evolution-batch-0002"
THIRD = "evolution-batch-0003"

PROMOTED_AT = datetime(2026, 8, 8, 9, 0, 0, tzinfo=timezone.utc)
REVERSED_AT = datetime(2026, 8, 9, 9, 0, 0, tzinfo=timezone.utc)
FROZEN_AT = datetime(2026, 8, 10, 9, 0, 0, tzinfo=timezone.utc)
FORMED_AT = "2026-08-11T09:00:00Z"
SETTLED_AT = "2026-08-11T10:00:00Z"

WHY = "the cohort produced at the promoted revision converged in fewer rounds"
EXPECTATION = "fewer remediation rounds, with quality and elapsed time unchanged"


@pytest.fixture
def config(tmp_path: Path) -> evolution.EvolutionConfig:
    """A repository with the real contract files, a release tag, and an admission
    policy low enough to freeze a cohort in a test."""

    root = git_repo(make_repo(tmp_path), tag="v2.2.0")
    path = root / "evolution" / "config.toml"
    text = path.read_text(encoding="utf-8")
    for old, new in (("target_task_count = 20", "target_task_count = 3"), ("minimum_task_count = 10", "minimum_task_count = 3")):
        assert old in text
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")
    return evolution.load_config(root)


@pytest.fixture
def release(config: evolution.EvolutionConfig) -> str:
    """The source line, where it stood before anything was promoted — which is
    the effective revision every pre-release report was produced at."""

    sha = git_rev(config.repo_root, "HEAD")
    git_update_ref(config.repo_root, RELEASE_REF, sha)
    return sha


@pytest.fixture
def promoted(config: evolution.EvolutionConfig, release: str) -> experiments.PromotionResult:
    """One whole change cycle on the first batch, ending in a merge on the line.

    Its own three reports are the pre-release cohort: they were produced at the
    revision the line stood at before the promotion, which is what makes them the
    baseline the next cohort is compared against.
    """

    return promote_candidate(
        config,
        batch_id=FIRST,
        at=PROMOTED_AT,
        reports=[
            make_manifest_report(key=f"b{index}", sequence=index, task_id=f"2026-07-0{index}-task", effective_revision=release)
            for index in (1, 2, 3)
        ],
    )


def freeze_second(
    config: evolution.EvolutionConfig,
    promotion: experiments.PromotionResult,
    *,
    batch_id: str = SECOND,
    reports: list[dict] | None = None,
    effective: str | None = None,
) -> lineage.Batch:
    """The cohort that measures the release: a frozen batch whose reports were
    produced at the promoted revision unless a test says otherwise."""

    at = effective if effective is not None else promotion.promotion_revision
    write_manifest(
        config.batches_root,
        batch_id,
        [],
        analysis_task_id=f"2026-08-10-{batch_id}",
        reports=reports
        if reports is not None
        else [
            make_manifest_report(key=f"a{index}", sequence=index, task_id=f"2026-08-0{index}-task", effective_revision=at)
            for index in (1, 2, 3)
        ],
    )
    return next(batch for batch in evolution.load_batches(config) if batch.batch_id == batch_id)


def fill_pool(config: evolution.EvolutionConfig, feed_root: Path) -> None:
    """Import three distinct completed tasks — the lowered admission minimum — so
    a freeze has a cohort to form. The keys are the promoted batch's own
    membership, kept distinct: one report belongs to one batch."""

    evolution.sync(
        config,
        write_feed(
            feed_root,
            [
                make_record(key=f"k{index}", sequence=index, task_id=f"2026-08-0{index}-task")
                for index in (1, 2, 3)
            ],
        ),
    )


def measurement(**overrides) -> assessment.Measurement:
    fields = {"metric": "remediation-rounds", "unit": "rounds", "before": 2.4, "after": 1.6, "better": "lower"}
    fields.update(overrides)
    return assessment.Measurement(**fields)


def counterfactual(frame: assessment.Frame, **overrides) -> assessment.Counterfactual:
    """The pinned two-revision run, on the pair the release's own outcome states.

    Its position is a round beyond the promoted experiment's last: that
    experiment is terminal, so no run or withdrawal will ever hold it, and a
    harness keyed on it cannot answer this comparison with an experiment's run.
    """

    subject = frame.subject
    result = assessment.RunResult(
        outcome=replay.RESULT_COMPLETED,
        concluded_at="2026-08-11T08:00:00Z",
        detail="the promoted revision converged in fewer rounds over the same cases",
        elapsed_seconds=1800.0,
        metrics=(measurement(),),
        regressions=(),
        ambiguity=None,
    )
    fields = {
        "position": assessment.Position(
            experiment_id=subject.experiment_id,
            round_number=subject.round_number + 1,
            attempt=1,
        ),
        "integration": assessment.Pinned(
            base_revision=subject.merge_input_revision,
            candidate_revision=subject.revision,
            source_ref=subject.merge_input_ref,
            tree=subject.tree,
        ),
        "cases": replay.CaseSet(case_set_id="loader-regressions", case_set_sha256="c" * 64, count=12, excluded=()),
        "evaluator": replay.Evaluator(backend="claude", model="claude-opus-5", rubric_revision="r7"),
        "harness": replay.Harness(id="local-replay", revision="0.1.0", config_sha256="d" * 64, handle="cf-0201"),
        "expectation": EXPECTATION,
        "started_at": "2026-08-11T07:00:00Z",
        "result": result,
    }
    fields.update(overrides)
    return assessment.Counterfactual(**fields)


def build(frame: assessment.Frame, **overrides) -> assessment.Assessment:
    """The assessment a session would form from `frame`, as a value."""

    built = assessment.Assessment(
        batch_id=frame.batch_id,
        subject=frame.subject,
        before=frame.before.report_keys,
        before_task_count=frame.before.task_count,
        after=frame.after.report_keys,
        after_task_count=frame.after.task_count,
        excluded=frame.excluded,
        comparability=frame.comparability,
        metrics=(measurement(),),
        counterfactual=None,
        verdict=assessment.VERDICT_IMPROVED,
        confidence=assessment.CONFIDENCE_MEDIUM,
        rationale=WHY,
        formed_at=FORMED_AT,
        decision=None,
    )
    return dataclasses.replace(built, **overrides)


def publish(batch: lineage.Batch, built: assessment.Assessment) -> Path:
    """The record on disk, exactly as the artifact's own serializer writes it —
    so every assertion below reads what a writer would have produced."""

    path = batch.assessment_path
    path.write_text(json.dumps(built.to_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


# --- what there is to assess ------------------------------------------------


def test_no_frame_without_a_promotion(config: evolution.EvolutionConfig, release: str) -> None:
    write_manifest(config.batches_root, FIRST, ["r1"], analysis_task_id="2026-07-31-first")
    write_outcome(config.batches_root, FIRST)
    write_manifest(config.batches_root, SECOND, ["r2"], analysis_task_id="2026-08-10-second")
    second = next(item for item in evolution.load_batches(config) if item.batch_id == SECOND)

    assert assessment.describe(config, second) is None
    assert assessment.describe_current(config) is None


def test_no_frame_for_a_no_change_predecessor(config: evolution.EvolutionConfig, release: str) -> None:
    """`no-change` fabricates no revision (invariant 7), so there is nothing an
    upgrade effect could be measured against and none is invented."""

    write_manifest(config.batches_root, FIRST, ["r1"], analysis_task_id="2026-07-31-first")
    write_outcome(config.batches_root, FIRST, outcome="no-change")
    write_manifest(config.batches_root, SECOND, ["r2"], analysis_task_id="2026-08-10-second")

    assert assessment.describe_current(config) is None


def test_frame_splits_the_cohorts_by_what_each_target_held(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)

    assert frame is not None
    assert frame.owed is True
    assert frame.subject.batch_id == FIRST
    assert frame.subject.revision == promoted.promotion_revision
    # The pre-promotion revision is the merge unit's input, not a commit anybody
    # had to go looking for.
    assert frame.subject.merge_input_revision == promoted.merge_input_revision
    assert frame.subject.standing is True
    assert frame.before.report_keys == ("b1", "b2", "b3")
    assert frame.after.report_keys == ("a1", "a2", "a3")
    assert frame.before.task_count == 3
    assert frame.after.task_count == 3
    assert frame.excluded == ()
    assert frame.comparability.coherent is True
    assert frame.supports_direction is True


def test_a_later_revision_still_carries_the_release(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """A target redeployed after further work on the line still carries the
    change: ancestry is the question, never equality with the promotion."""

    later = git_file_commit(
        config.repo_root,
        promoted.promotion_revision,
        "unrelated.txt",
        "later release work\n",
        "unrelated work after the promotion",
    )
    second = freeze_second(config, promoted, effective=later)
    frame = assessment.describe(config, second)

    assert frame is not None
    assert frame.after.report_keys == ("a1", "a2", "a3")
    assert frame.comparability.coherent is True


def test_reports_without_an_effective_revision_are_excluded(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    second = freeze_second(
        config,
        promoted,
        reports=[
            make_manifest_report(key="a1", sequence=1, task_id="2026-08-01-task", effective_revision=promoted.promotion_revision),
            make_manifest_report(key="a2", sequence=2, task_id="2026-08-02-task", effective_revision=None),
        ],
    )
    frame = assessment.describe(config, second)

    assert frame is not None
    assert frame.after.report_keys == ("a1",)
    assert [(item.report_key, item.reason) for item in frame.excluded] == [
        ("a2", assessment.EXCLUDED_REVISION_ABSENT)
    ]
    # One report short of the minimum, so nothing directional may be claimed —
    # the exclusion narrows the denominator and says so.
    assert frame.supports_direction is False


def test_an_unresolvable_effective_revision_is_unverified_rather_than_placed(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """A revision this checkout cannot resolve is a fact about the clone. It is
    reported as an exclusion nobody can check, never guessed at."""

    second = freeze_second(config, promoted, effective="e1")
    frame = assessment.describe(config, second)

    assert frame is not None
    assert frame.after.report_keys == ()
    assert frame.unverified == ("a1", "a2", "a3")
    assert {item.reason for item in frame.excluded} == {assessment.EXCLUDED_REVISION_UNRESOLVABLE}
    assert frame.supports_direction is False
    # An empty side is not "every facet agrees": the one facet that asks for an
    # overlap is what says there is nothing to compare.
    assert frame.comparability.incoherent == (assessment.FACET_REPOSITORY_COVERAGE,)


def test_mixed_provenance_is_named_facet_by_facet(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    second = freeze_second(
        config,
        promoted,
        reports=[
            make_manifest_report(
                key=f"a{index}",
                sequence=index,
                task_id=f"2026-08-0{index}-task",
                effective_revision=promoted.promotion_revision,
                rubric_revision="r7" if index < 3 else "r8",
            )
            for index in (1, 2, 3)
        ],
    )
    frame = assessment.describe(config, second)

    assert frame is not None
    assert frame.after.task_count == 3
    assert frame.comparability.incoherent == (assessment.FACET_EVALUATOR_RUBRIC,)
    assert frame.supports_direction is False
    facet = next(item for item in frame.comparability.facets if item.facet == assessment.FACET_EVALUATOR_RUBRIC)
    assert facet.before == ("r7",)
    assert facet.after == ("r7", "r8")


def test_a_repository_present_on_only_one_side_is_not_a_comparison(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    second = freeze_second(
        config,
        promoted,
        reports=[
            make_manifest_report(
                key=f"a{index}",
                sequence=index,
                task_id=f"2026-08-0{index}-task",
                repo_id="repo-beta",
                effective_revision=promoted.promotion_revision,
            )
            for index in (1, 2, 3)
        ],
    )
    frame = assessment.describe(config, second)

    assert frame is not None
    assert frame.comparability.incoherent == (assessment.FACET_REPOSITORY_COVERAGE,)
    assert frame.supports_direction is False


def test_a_version_one_manifest_carries_no_cohort_provenance(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """Version 1 froze identity and hashes and nothing about what a target held,
    so those reports are placed nowhere rather than assumed comparable."""

    write_manifest(
        config.batches_root,
        SECOND,
        ["a1"],
        version=1,
        analysis_task_id=f"2026-08-10-{SECOND}",
    )
    second = next(item for item in evolution.load_batches(config) if item.batch_id == SECOND)
    frame = assessment.describe(config, second)

    assert frame is not None
    assert [(item.report_key, item.reason) for item in frame.excluded] == [
        ("a1", assessment.EXCLUDED_REVISION_ABSENT)
    ]


def test_a_reversed_promotion_is_a_different_question(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """Reports produced at a line that had already taken the change back out are
    neither cohort, and the subject says the promotion no longer stands."""

    reversal = rollback.rollback(config, reason="the release looked wrong on the first cohort", now=REVERSED_AT)
    second = freeze_second(config, promoted, effective=reversal.revision)
    frame = assessment.describe(config, second)

    assert frame is not None
    assert frame.subject.standing is False
    assert frame.subject.reversed_promotion is True
    assert frame.subject.rollback_revision == reversal.revision
    assert frame.after.report_keys == ()
    assert {item.reason for item in frame.excluded} == {assessment.EXCLUDED_POST_ROLLBACK}
    assert frame.supports_direction is False


def test_only_the_first_cohort_after_a_release_owes_the_reading(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    second = freeze_second(config, promoted)
    write_outcome(config.batches_root, SECOND, outcome="no-change")
    third = freeze_second(config, promoted, batch_id=THIRD)

    known = lineage.describe(config)
    assert assessment.owed_by(known, second) is not None
    assert assessment.owed_by(known, third) is None
    # The third batch still derives the same release — that is how it reads the
    # record the second batch left — and owes nothing itself.
    frame = assessment.describe(config, third, lineage=known)
    assert frame is not None and frame.subject.batch_id == FIRST and frame.owed is False


# --- what may be recorded --------------------------------------------------


def test_no_record_is_the_ordinary_state(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    second = freeze_second(config, promoted)

    assert assessment.read(config, second) is None


def test_a_recorded_assessment_round_trips(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    publish(second, build(frame, counterfactual=counterfactual(frame)))

    read = assessment.read(config, second)
    assert read is not None
    assert read.verdict == assessment.VERDICT_IMPROVED
    assert read.before == ("b1", "b2", "b3")
    assert read.after == ("a1", "a2", "a3")
    assert read.subject.revision == promoted.promotion_revision
    assert read.counterfactual is not None and read.counterfactual.completed is True
    assert read.settled is False
    assert read.to_json() == json.loads(second.assessment_path.read_text(encoding="utf-8"))


def test_inconclusive_is_admissible_on_mixed_provenance(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The reading a repository may record from evidence like this — and it keeps
    the exclusions and the denominators it was formed against."""

    second = freeze_second(config, promoted, effective="e1")
    frame = assessment.describe(config, second)
    assert frame is not None
    publish(
        second,
        build(
            frame,
            metrics=(),
            verdict=assessment.VERDICT_INCONCLUSIVE,
            confidence=assessment.CONFIDENCE_LOW,
            rationale="no report could be placed: this checkout resolves none of the effective revisions",
        ),
    )

    read = assessment.read(config, second)
    assert read is not None
    assert read.verdict == assessment.VERDICT_INCONCLUSIVE
    assert read.directional is False
    assert len(read.excluded) == 3


def test_a_directional_verdict_over_incomparable_cohorts_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    second = freeze_second(
        config,
        promoted,
        reports=[
            make_manifest_report(
                key=f"a{index}",
                sequence=index,
                task_id=f"2026-08-0{index}-task",
                effective_revision=promoted.promotion_revision,
                rubric_revision="r7" if index < 3 else "r8",
            )
            for index in (1, 2, 3)
        ],
    )
    frame = assessment.describe(config, second)
    assert frame is not None
    publish(second, build(frame, verdict=assessment.VERDICT_REGRESSED))

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "not comparable" in str(error.value)
    assert assessment.VERDICT_INCONCLUSIVE in str(error.value)


def test_a_directional_verdict_below_the_minimum_cohort_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    second = freeze_second(
        config,
        promoted,
        reports=[
            make_manifest_report(
                key="a1",
                sequence=1,
                task_id="2026-08-01-task",
                effective_revision=promoted.promotion_revision,
            )
        ],
    )
    frame = assessment.describe(config, second)
    assert frame is not None
    publish(second, build(frame))

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "unique completed task(s) against a minimum of 3" in str(error.value)


def test_a_summary_that_disagrees_with_its_own_facets_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The stated `coherent` is what the verdict is judged against, so a record
    cannot claim it over a facet list that says otherwise."""

    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    broken = dataclasses.replace(
        frame.comparability,
        facets=tuple(
            dataclasses.replace(facet, coherent=False) if facet.facet == assessment.FACET_EVALUATOR_MODEL else facet
            for facet in frame.comparability.facets
        ),
    )
    built = build(frame)
    record = built.to_json()
    record["comparability"] = {
        "coherent": True,
        "facets": json.loads(json.dumps(dataclasses.replace(built, comparability=broken).to_json()))["comparability"][
            "facets"
        ],
    }
    second.assessment_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "comparability is recorded True while its own facets say False" in str(error.value)


def test_a_direction_with_no_measured_goal_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    publish(second, build(frame, metrics=(measurement(better=replay.BETTER_NEITHER, before=None),)))

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "no measurement carries one" in str(error.value)


def test_a_goal_measured_only_after_the_release_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    publish(
        second,
        build(
            frame,
            verdict=assessment.VERDICT_INCONCLUSIVE,
            metrics=(measurement(before=None),),
        ),
    )

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "no before value to compare against" in str(error.value)


def test_regressed_without_a_completed_counterfactual_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """A cohort difference suspects a regression. What settles it is the pinned
    pair, measured — so a failed run leaves the reading inconclusive."""

    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    publish(second, build(frame, verdict=assessment.VERDICT_REGRESSED))

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "rests on the counterfactual, and this record has none" in str(error.value)

    failed = counterfactual(
        frame,
        result=assessment.RunResult(
            outcome=replay.RESULT_FAILED,
            concluded_at="2026-08-11T08:00:00Z",
            detail="the harness lost the case set between the two halves",
            elapsed_seconds=None,
            metrics=(),
            regressions=(),
            ambiguity=None,
        ),
    )
    publish(second, build(frame, verdict=assessment.VERDICT_REGRESSED, counterfactual=failed))

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "one that did not complete" in str(error.value)


def test_regressed_stands_on_a_completed_counterfactual(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    publish(
        second,
        build(
            frame,
            verdict=assessment.VERDICT_REGRESSED,
            metrics=(measurement(before=1.6, after=2.4),),
            counterfactual=counterfactual(frame),
        ),
    )

    read = assessment.read(config, second)
    assert read is not None and read.verdict == assessment.VERDICT_REGRESSED


def test_a_counterfactual_of_another_pair_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    elsewhere = counterfactual(
        frame,
        integration=assessment.Pinned(
            base_revision=frame.subject.merge_input_revision,
            candidate_revision=frame.subject.candidate_revision,
            source_ref=frame.subject.merge_input_ref,
            tree=frame.subject.tree,
        ),
    )
    publish(second, build(frame, counterfactual=elsewhere))

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "measured another question" in str(error.value)


def test_an_assessment_of_another_release_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    publish(second, build(frame, subject=dataclasses.replace(frame.subject, tree="9" * 40)))

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "is not this batch's reading of the one before it" in str(error.value)


def test_a_reversal_no_rollback_record_names_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    publish(
        second,
        build(frame, subject=dataclasses.replace(frame.subject, standing=False, rollback_revision="1" * 40)),
    )

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "no rollback record beside that batch's outcome names" in str(error.value)


def test_a_report_placed_against_git_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The cohorts are what the targets actually held: a pre-release report moved
    onto the after side is refused wherever the checkout can say so."""

    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    publish(second, build(frame, before=("b1", "b2"), after=("b3", "a1", "a2", "a3"), before_task_count=2, after_task_count=4))

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "places it before" in str(error.value)


def test_an_exclusion_the_manifest_contradicts_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """An exclusion states what the provenance could not say. Claiming a report
    stated no effective revision when its frozen entry states one is checkable
    from committed content alone — and is how a cohort would be narrowed to the
    reports that agreed with the change."""

    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    publish(
        second,
        build(
            frame,
            after=("a1", "a2"),
            after_task_count=2,
            excluded=(
                assessment.Excluded(
                    report_key="a3",
                    batch_id=SECOND,
                    reason=assessment.EXCLUDED_REVISION_ABSENT,
                    detail="claimed to state no effective revision",
                ),
            ),
            verdict=assessment.VERDICT_INCONCLUSIVE,
        ),
    )

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "places it in the after cohort" in str(error.value)


def test_a_reading_formed_where_the_objects_were_missing_stays_readable(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The one exclusion nothing can check, and deliberately: a clone that cannot
    resolve what a target held has learned something about itself, so a reader
    that refused there would make a valid record unreadable everywhere the
    objects were never fetched."""

    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    assert frame.after.report_keys == ("a1", "a2", "a3")
    publish(
        second,
        build(
            frame,
            after=(),
            after_task_count=0,
            metrics=(),
            excluded=tuple(
                assessment.Excluded(
                    report_key=key,
                    batch_id=SECOND,
                    reason=assessment.EXCLUDED_REVISION_UNRESOLVABLE,
                    detail=f"the machine that formed this could not resolve {key}'s effective revision",
                )
                for key in ("a1", "a2", "a3")
            ),
            verdict=assessment.VERDICT_INCONCLUSIVE,
            rationale="the forming checkout could place none of this cohort's reports",
        ),
    )

    read = assessment.read(config, second)
    assert read is not None
    assert read.after == ()
    assert len(read.excluded) == 3


def test_a_report_no_manifest_names_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    publish(second, build(frame, after=frame.after.report_keys + ("stray",)))

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "belong to no frozen manifest" in str(error.value)


def test_a_frozen_member_placed_nowhere_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The denominator stays visible: a member the record never mentions is a
    cohort quietly narrowed (invariant 2)."""

    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    publish(second, build(frame, after=("a1", "a2"), after_task_count=2))

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "places them nowhere" in str(error.value)


def test_a_denominator_counted_in_reports_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    second = freeze_second(
        config,
        promoted,
        reports=[
            make_manifest_report(
                key=f"a{index}",
                sequence=index,
                task_id="2026-08-01-task",
                effective_revision=promoted.promotion_revision,
            )
            for index in (1, 2, 3)
        ],
    )
    frame = assessment.describe(config, second)
    assert frame is not None
    # Three reports of one completed task: the cohort is one task's evidence
    # however it is counted (invariant 1).
    assert frame.after.task_count == 1
    publish(second, build(frame, after_task_count=3))

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "the denominator is unique tasks, not reports" in str(error.value)


def test_a_settlement_naming_no_inverse_commit_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    publish(
        second,
        build(
            frame,
            decision=assessment.Decision(
                settlement=assessment.SETTLEMENT_ROLLED_BACK,
                decided_at=SETTLED_AT,
                reason="the counterfactual confirmed the regression",
                rollback_revision="2" * 40,
            ),
        ),
    )

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "the rollback record stays the authority" in str(error.value)


def test_a_retained_release_settles_without_an_inverse_commit(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    publish(
        second,
        build(
            frame,
            decision=assessment.Decision(
                settlement=assessment.SETTLEMENT_RETAIN,
                decided_at=SETTLED_AT,
                reason="the release held up on the first comparable cohort",
                rollback_revision=None,
            ),
        ),
    )

    read = assessment.read(config, second)
    assert read is not None and read.settled is True
    assert read.decision is not None and read.decision.settlement == assessment.SETTLEMENT_RETAIN


def test_a_record_with_no_release_before_it_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """A well-formed reading in the directory of a batch that follows no
    promotion: the record names a release its own lineage does not have."""

    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    first = next(item for item in evolution.load_batches(config) if item.batch_id == FIRST)
    publish(first, dataclasses.replace(build(frame), batch_id=FIRST))

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, first)
    assert "follows no promotion" in str(error.value)


# --- what the generated analysis task says ---------------------------------


def test_the_first_cohort_after_a_release_is_asked_to_assess_it(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
    tmp_path: Path,
) -> None:
    """The freeze that creates the next cohort's analysis task states the release
    it follows — identity only, and the path the reading goes to."""

    fill_pool(config, tmp_path / "feed")
    result = evolution.freeze(config, now=FROZEN_AT, runner_revision="v2.2.0")

    assert result.analysis_task_id is not None
    text = (config.repo_root / ".ai-tasks" / f"{result.analysis_task_id}.md").read_text(encoding="utf-8")
    assert "### Release assessment — evolution-batch-0001" in text
    assert promoted.promotion_revision in text
    assert promoted.merge_input_revision in text
    assert f"evolution/batches/{SECOND}/release-assessment.json" in text
    assert "`improved`, `neutral`, `regressed`, or `inconclusive`" in text
    # One session for the reports, one for the second reading of the same cohort.
    assert "session-est: 0/2" in text


def test_a_first_cohort_is_asked_for_no_release_reading(
    config: evolution.EvolutionConfig,
    tmp_path: Path,
) -> None:
    fill_pool(config, tmp_path / "feed")
    result = evolution.freeze(config, now=FROZEN_AT, runner_revision="v2.2.0")

    assert result.analysis_task_id is not None
    text = (config.repo_root / ".ai-tasks" / f"{result.analysis_task_id}.md").read_text(encoding="utf-8")
    assert "Release assessment" not in text
    assert "session-est: 0/1" in text


def test_an_interrupted_freeze_writes_the_same_task(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
    tmp_path: Path,
) -> None:
    """The repair path derives the release from the same records the freeze did,
    so the task it writes is the task the freeze would have written."""

    fill_pool(config, tmp_path / "feed")
    result = evolution.freeze(config, now=FROZEN_AT, runner_revision="v2.2.0")
    assert result.analysis_task_id is not None
    task = config.repo_root / ".ai-tasks" / f"{result.analysis_task_id}.md"
    written = task.read_text(encoding="utf-8")
    task.unlink()

    again = evolution.start(config, feed=write_feed(tmp_path / "feed", []), now=FROZEN_AT)

    assert again is not None
    assert task.read_text(encoding="utf-8") == written


def test_the_batch_spec_names_the_release_only_for_the_batch_that_owes_it(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    second = freeze_second(config, promoted)
    write_outcome(config.batches_root, SECOND, outcome="no-change")
    third = freeze_second(config, promoted, batch_id=THIRD)

    owed = batches._task_spec(config, second.manifest, task_id=f"2026-08-10-{SECOND}", batch_id=SECOND)
    later = batches._task_spec(config, third.manifest, task_id=f"2026-08-11-{THIRD}", batch_id=THIRD)

    assert owed.release is not None
    assert owed.release.batch_id == FIRST
    assert owed.release.revision == promoted.promotion_revision
    assert owed.release.planned_targets == ("orch-hub",)
    assert later.release is None
