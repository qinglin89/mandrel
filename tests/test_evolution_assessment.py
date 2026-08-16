"""Whether a promoted release actually improved the work that came after it.

Everything here runs against a real Git repository with a real promotion on it,
for the reason the promotion and rollback suites do: which cohort a report
belongs to is a question about ancestry — did the line that target held carry the
change — and a fixture standing in for Git would prove only that the package
agrees with itself.

Two properties get most of the attention, because they are what this artifact
exists for:

- **A directional claim needs evidence that can carry one.** Mixed provenance,
  work whose shape no manifest states, a cohort below the minimum unique-task
  count, an unmeasured quantity, and a regression nobody counterfactually
  measured are each refused — in the record as well as in the derivation, since a
  rule only the writer keeps is one any file written beside it escapes. What the
  cohorts cannot carry, a completed counterfactual can: it is the one comparison
  in which the release is the only difference.
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
    FakeHarness,
    completed_report,
    experiment_decision,
    experiment_round,
    git_file_commit,
    git_repo,
    git_rev,
    git_update_ref,
    make_manifest_report,
    make_record,
    make_repo,
    promote_candidate,
    write_closure,
    write_draft,
    write_experiment,
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
# When the run that finishes an interrupted rollback finds the line.
FINISHED_AT = datetime(2026, 8, 9, 11, 0, 0, tzinfo=timezone.utc)
FROZEN_AT = datetime(2026, 8, 10, 9, 0, 0, tzinfo=timezone.utc)
FORMED_AT = "2026-08-11T09:00:00Z"
SETTLED_AT = "2026-08-11T10:00:00Z"
# When the counterfactual was pinned, when its numbers came back, and when the
# reading they settle was recorded — three moments, because the record states
# each of them and the order between them is what makes it evidence.
MEASURED_AT = datetime(2026, 8, 11, 7, 0, 0, tzinfo=timezone.utc)
CONCLUDED_AT = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)
RESOLVED_AT = datetime(2026, 8, 11, 13, 0, 0, tzinfo=timezone.utc)

# A commit id of the right shape that no checkout here holds: the case where
# placement fails on this clone rather than on the record. Spelled as a full
# object id on purpose — a shorter string would be refused for its shape and
# would never reach the question these tests are asking.
ABSENT_COMMIT = "b1" + "0" * 38

WHY = "the cohort produced at the promoted revision converged in fewer rounds"
EXPECTATION = "fewer remediation rounds, with quality and elapsed time unchanged"
REVERSAL = "the counterfactual confirmed the regression"
DIED = "the harness host was reclaimed mid-run and never reported"


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


# What a run measuring the release doing harm came to: the same quantity, the
# other way round. Spelled once because a regression is what the settlement gate
# exists for, and every test that reaches it needs a run that actually found one.
SLOWER = (measurement(before=1.6, after=2.4),)
WORSE = "the promoted revision took more rounds over the same cases"


def slower(**overrides) -> replay.Measurement:
    """The same quantity as a harness reports it: a baseline and a candidate,
    which this record reads as the line before the release and the release. The
    release doing harm, because that is the reading the settlement gate exists
    for."""

    fields = {"metric": "remediation-rounds", "unit": "rounds", "baseline": 1.6, "candidate": 2.4, "better": "lower"}
    fields.update(overrides)
    return replay.Measurement(**fields)


def counterfactual(
    frame: assessment.Frame,
    *,
    metrics: tuple[assessment.Measurement, ...] | None = None,
    detail: str | None = None,
    **overrides,
) -> assessment.Counterfactual:
    """The pinned two-revision run, on the pair the release's own outcome states.

    Its position is a round beyond the promoted experiment's last: that
    experiment is terminal, so no run or withdrawal will ever hold it, and a
    harness keyed on it cannot answer this comparison with an experiment's run.

    `metrics` is what the run came to, and it is a knob rather than a constant
    because the verdict a record may carry is read off exactly these numbers: the
    default measures the release improving, and a test asserting any other
    direction says so here.
    """

    subject = frame.subject
    result = assessment.RunResult(
        outcome=replay.RESULT_COMPLETED,
        concluded_at="2026-08-11T08:00:00Z",
        detail=detail if detail is not None else "the promoted revision converged in fewer rounds over the same cases",
        elapsed_seconds=1800.0,
        metrics=metrics if metrics is not None else (measurement(),),
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
    # Every respect a frozen manifest states agrees. The one it does not state is
    # what kind of work each cohort did, and that is not a detail: these are two
    # different task sets, so the numbers they came to are explained by the work
    # unless something pins it. The cohorts raise the question; the counterfactual
    # answers it.
    assert frame.comparability.incoherent == (assessment.FACET_TASK_SHAPE,)
    assert frame.cohorts_support_direction is False


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
    assert frame.placement("a1") == assessment.SIDE_AFTER


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
    assert frame.cohorts_support_direction is False


def test_an_unresolvable_effective_revision_is_unverified_rather_than_placed(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """A revision this checkout cannot resolve is a fact about the clone. It is
    reported as an exclusion nobody can check, never guessed at."""

    second = freeze_second(config, promoted, effective=ABSENT_COMMIT)
    frame = assessment.describe(config, second)

    assert frame is not None
    assert frame.after.report_keys == ()
    assert frame.unverified == ("a1", "a2", "a3")
    assert {item.reason for item in frame.excluded} == {assessment.EXCLUDED_REVISION_UNRESOLVABLE}
    assert frame.cohorts_support_direction is False
    # An empty side is not "every facet agrees": the one facet that asks for an
    # overlap is what says there is nothing to compare.
    assert frame.comparability.incoherent == (
        assessment.FACET_TASK_SHAPE,
        assessment.FACET_REPOSITORY_COVERAGE,
    )


@pytest.mark.parametrize(
    "state",
    [
        pytest.param(lambda promotion: "HEAD", id="head"),
        pytest.param(lambda promotion: RELEASE_REF, id="a-ref"),
        pytest.param(lambda promotion: promotion.promotion_revision[:12], id="an-abbreviation"),
    ],
)
def test_a_revision_that_is_not_a_commit_id_is_excluded_rather_than_placed(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
    state,
) -> None:
    """The field is a deploy lock's `source_git_commit`, and every one of these
    resolves here — to the promotion, in this checkout, today.

    Which is the whole objection: what they resolve to is a reading of where
    *this* repository stands, so the placement would follow a `git checkout` made
    in it and would say that targets carried a release on the strength of it. The
    report is excluded instead, naming what it stated, and Git is never asked —
    `unverified` stays empty, because nothing here failed to resolve.
    """

    stated = state(promoted)
    second = freeze_second(config, promoted, effective=stated)
    frame = assessment.describe(config, second)

    assert frame is not None
    # Resolvable, and resolving to the side this refuses to place it on.
    assert git_rev(config.repo_root, stated) == promoted.promotion_revision
    assert frame.after.report_keys == ()
    assert frame.unverified == ()
    assert {item.reason for item in frame.excluded} == {assessment.EXCLUDED_REVISION_MALFORMED}
    assert repr(stated) in frame.exclusion("a1").detail
    # Absent evidence, not a reading against the release: the cohort is empty and
    # nothing directional may be claimed from it.
    assert frame.cohorts_support_direction is False


def test_a_symbolic_revision_a_feed_published_reaches_the_freeze_and_is_excluded(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
    tmp_path: Path,
) -> None:
    """The other half of the same rule, over a real import rather than a manifest
    written by hand.

    Nothing on the way in touches the string: the client copies what the feed
    published, the pool stages the record whole, and the freeze copies its
    provenance into the immutable manifest (invariant 4 keeps it unrepaired). So
    the fresh-import path and a cohort frozen before the rule existed arrive at
    the same place carrying the same string, and are answered there.
    """

    evolution.sync(
        config,
        write_feed(
            tmp_path / "feed",
            [
                make_record(
                    key=f"k{index}",
                    sequence=index,
                    task_id=f"2026-08-0{index}-task",
                    effective_revision="HEAD",
                )
                for index in (1, 2, 3)
            ],
        ),
    )
    result = evolution.freeze(config, now=FROZEN_AT, runner_revision="v2.2.0")

    assert result.manifest_path is not None
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert {report["provenance"]["effective_revision"] for report in manifest["reports"]} == {"HEAD"}

    second = next(batch for batch in evolution.load_batches(config) if batch.batch_id == SECOND)
    frame = assessment.describe(config, second)

    assert frame is not None
    assert frame.after.report_keys == ()
    assert {item.reason for item in frame.excluded} == {assessment.EXCLUDED_REVISION_MALFORMED}
    assert "'HEAD'" in frame.exclusion("k1").detail


def test_a_record_placing_a_revision_that_is_not_a_commit_id_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """A malformed revision is a fact about the record, so it is checked in the
    strict direction — unlike an unresolvable one, which is a fact about the
    clone. A record placing such a report is refused in every checkout rather
    than only where the name happens not to resolve."""

    second = freeze_second(config, promoted, effective="HEAD")
    frame = assessment.describe(config, second)
    assert frame is not None
    after = ("a1", "a2", "a3")
    publish(
        second,
        build(
            frame,
            after=after,
            after_task_count=3,
            excluded=(),
            metrics=(),
            comparability=assessment._stated_comparability(frame, frame.before.report_keys, after),
            verdict=assessment.VERDICT_INCONCLUSIVE,
        ),
    )

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert assessment.EXCLUDED_REVISION_MALFORMED in str(error.value)


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
    assert frame.comparability.incoherent == (
        assessment.FACET_EVALUATOR_RUBRIC,
        assessment.FACET_TASK_SHAPE,
    )
    assert frame.cohorts_support_direction is False
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
    assert frame.comparability.incoherent == (
        assessment.FACET_TASK_SHAPE,
        assessment.FACET_REPOSITORY_COVERAGE,
    )
    assert frame.cohorts_support_direction is False


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
    assert frame.cohorts_support_direction is False


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
    publish(second, build(frame, verdict=assessment.VERDICT_IMPROVED))

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "not comparable" in str(error.value)
    assert assessment.FACET_EVALUATOR_RUBRIC in str(error.value)
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


def test_a_direction_the_cohorts_cannot_carry_rests_on_the_counterfactual(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """Two cohorts agreeing in every respect a frozen manifest states, and the one
    it does not state is what kind of work each of them did.

    They are two different task sets by construction — one report per completed
    task — so a difference in their numbers is explained by the work at least as
    well as by the release, and repository coverage is coverage rather than a
    task-shape match. What that evidence supports is `inconclusive`; the pinned
    two-revision run is what turns it into a direction.
    """

    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    assert frame.comparability.incoherent == (assessment.FACET_TASK_SHAPE,)
    publish(second, build(frame, verdict=assessment.VERDICT_IMPROVED))

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert assessment.FACET_TASK_SHAPE in str(error.value)
    assert assessment.VERDICT_INCONCLUSIVE in str(error.value)

    publish(second, build(frame, verdict=assessment.VERDICT_IMPROVED, counterfactual=counterfactual(frame)))
    read = assessment.read(config, second)
    assert read is not None and read.verdict == assessment.VERDICT_IMPROVED


def test_a_summary_that_disagrees_with_its_own_facets_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The stated `coherent` is what the verdict is judged against, so a record
    cannot claim it over a facet list that says otherwise."""

    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    record = build(frame).to_json()
    record["comparability"]["coherent"] = True
    second.assessment_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "comparability is recorded True while its own facets say False" in str(error.value)
    assert assessment.FACET_TASK_SHAPE in str(error.value)


def test_invented_comparability_facets_are_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The facet list is derived, never taken on the record's word.

    Two rubric revisions in the after cohort is exactly the mixed provenance a
    directional claim may not rest on. A record that drops the derived facets and
    states one coherent one of its own would otherwise carry `improved` past every
    remaining check — the summary agrees with the list it came with, the cohorts
    are frozen members, the counts are right, and Git places every report where
    the record says.
    """

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
    assert assessment.FACET_EVALUATOR_RUBRIC in frame.comparability.incoherent
    record = build(frame).to_json()
    record["comparability"] = {
        "coherent": True,
        "facets": [
            {
                "facet": assessment.FACET_EVALUATOR_RUBRIC,
                "coherent": True,
                "before": ["r7"],
                "after": ["r7"],
            }
        ],
    }
    second.assessment_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "the facets are derived from the frozen manifests" in str(error.value)


def test_a_facet_recorded_against_its_own_manifests_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The whole list is there and one entry lies about what the manifests hold.

    Checkable everywhere: what makes a facet coherent is evaluator and provenance
    metadata a manifest committed, so a clone that can place no report at all
    still reads this the same way.
    """

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
    record = build(frame, verdict=assessment.VERDICT_INCONCLUSIVE, metrics=()).to_json()
    for facet in record["comparability"]["facets"]:
        if facet["facet"] == assessment.FACET_EVALUATOR_RUBRIC:
            facet.update({"coherent": True, "after": ["r7"]})
    second.assessment_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert f"the {assessment.FACET_EVALUATOR_RUBRIC!r} facet is recorded coherent=True" in str(error.value)
    assert "['r7', 'r8']" in str(error.value)


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
    """The pinned pair measured, and measured the promoted revision doing worse.

    Both halves matter: the run reached the end of both revisions, and what it
    came to is the direction the record claims.
    """

    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    publish(
        second,
        build(
            frame,
            verdict=assessment.VERDICT_REGRESSED,
            metrics=(measurement(before=1.6, after=2.4),),
            counterfactual=counterfactual(frame, metrics=SLOWER, detail=WORSE),
        ),
    )

    read = assessment.read(config, second)
    assert read is not None and read.verdict == assessment.VERDICT_REGRESSED


def test_a_counterfactual_that_measured_no_goal_carries_no_direction(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """A run that reached the end of both revisions and recorded only observations
    settles nothing: the direction has to be in what the run measured, since the
    cohorts are not what the claim is resting on."""

    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    observed = counterfactual(
        frame,
        result=assessment.RunResult(
            outcome=replay.RESULT_COMPLETED,
            concluded_at="2026-08-11T08:00:00Z",
            detail="both revisions ran the case set; nothing the release aimed at was measured",
            elapsed_seconds=1800.0,
            metrics=(measurement(metric="elapsed", unit="seconds", better=replay.BETTER_NEITHER),),
            regressions=(),
            ambiguity=None,
        ),
    )
    publish(second, build(frame, verdict=assessment.VERDICT_REGRESSED, counterfactual=observed))

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "measured no goal quantity on both revisions" in str(error.value)


def test_a_direction_the_counterfactual_measured_the_other_way_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The run is what the claim rests on, so it is also what the claim is held to.

    Both signs, because both are ways of being wrong about the same numbers: a
    release the pinned pair measured doing worse is not `improved`, and one it
    measured doing better is not `regressed` — and `regressed` is the reading that
    costs somebody a promoted change.
    """

    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None

    publish(
        second,
        build(
            frame,
            verdict=assessment.VERDICT_IMPROVED,
            counterfactual=counterfactual(frame, metrics=SLOWER, detail=WORSE),
        ),
    )
    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "the run it records measured 'regressed'" in str(error.value)
    assert "remediation-rounds 1.6 → 2.4 with 'lower' better: regressed" in str(error.value)

    publish(
        second,
        build(
            frame,
            verdict=assessment.VERDICT_REGRESSED,
            metrics=(measurement(before=1.6, after=2.4),),
            counterfactual=counterfactual(frame),
        ),
    )
    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "the run it records measured 'improved'" in str(error.value)


def test_neutral_is_a_measured_reading_rather_than_an_unmeasured_one(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """`neutral` claims the release changed nothing, which is a measurement like
    any other: the run has to have found the quantity unmoved, and a run that
    found it moved says something else."""

    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None

    publish(
        second,
        build(frame, verdict=assessment.VERDICT_NEUTRAL, counterfactual=counterfactual(frame)),
    )
    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "the run it records measured 'improved'" in str(error.value)

    unmoved = (measurement(before=2.0, after=2.0),)
    publish(
        second,
        build(
            frame,
            verdict=assessment.VERDICT_NEUTRAL,
            metrics=unmoved,
            rationale="the pinned run converged in the same number of rounds at both revisions",
            counterfactual=counterfactual(
                frame,
                metrics=unmoved,
                detail="both revisions converged in the same number of rounds over the same cases",
            ),
        ),
    )
    read = assessment.read(config, second)
    assert read is not None and read.verdict == assessment.VERDICT_NEUTRAL


def test_goals_the_run_moved_both_ways_settle_no_direction(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """More than one goal, and the rule that goes with it.

    A goal that did not move neither adds to a direction nor stands against one,
    so a run that improved one quantity and left another alone still carries
    `improved`. Two goals pointing opposite ways carry nothing: which of them the
    release is judged on is chosen when the run is configured — invariant 13
    records the rest as observations — and a reader weighing them afterwards would
    be making that choice for the operator.
    """

    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None

    contested = (measurement(), measurement(metric="review-findings", unit="findings", before=1.0, after=3.0))
    publish(
        second,
        build(
            frame,
            verdict=assessment.VERDICT_IMPROVED,
            counterfactual=counterfactual(
                frame,
                metrics=contested,
                detail="fewer rounds at the promoted revision, and more findings raised in them",
            ),
        ),
    )
    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "its goal quantities point both ways" in str(error.value)
    assert "review-findings 1.0 → 3.0 with 'lower' better: regressed" in str(error.value)
    assert assessment.VERDICT_INCONCLUSIVE in str(error.value)

    agreed = (measurement(), measurement(metric="review-findings", unit="findings", before=2.0, after=2.0))
    publish(
        second,
        build(
            frame,
            verdict=assessment.VERDICT_IMPROVED,
            counterfactual=counterfactual(
                frame,
                metrics=agreed,
                detail="fewer rounds at the promoted revision, with the same findings raised in them",
            ),
        ),
    )
    read = assessment.read(config, second)
    assert read is not None and read.verdict == assessment.VERDICT_IMPROVED


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
            # The facets that machine derived: over an empty after cohort, since
            # the reports it could not place are reports it could not compare
            # either. Read here through the same derivation the reader uses,
            # because the point of the test is the placement rule and the record
            # has to be the one that machine would have written.
            comparability=assessment._stated_comparability(frame, frame.before.report_keys, ()),
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


def test_a_denominator_no_checkout_could_place_is_still_counted(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The count comes off the frozen manifests, never off what this checkout
    placed.

    Three reports of one completed task at an effective revision nothing here
    resolves. A reader that counted only the reports it could place would have no
    opinion about this cohort at all and would take the record's own `3` — one
    task's evidence stated as three, and over the minimum a directional verdict is
    admissible against. `(repo_id, task_id)` is committed content, so the answer
    is the same on every clone.
    """

    second = freeze_second(
        config,
        promoted,
        reports=[
            make_manifest_report(
                key=f"a{index}",
                sequence=index,
                task_id="2026-08-01-task",
                effective_revision=ABSENT_COMMIT,
            )
            for index in (1, 2, 3)
        ],
    )
    frame = assessment.describe(config, second)
    assert frame is not None
    assert frame.unverified == ("a1", "a2", "a3")
    after = ("a1", "a2", "a3")
    publish(
        second,
        build(
            frame,
            after=after,
            after_task_count=3,
            excluded=(),
            metrics=(),
            comparability=assessment._stated_comparability(frame, frame.before.report_keys, after),
            verdict=assessment.VERDICT_INCONCLUSIVE,
        ),
    )

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
            metrics=(),
            verdict=assessment.VERDICT_INCONCLUSIVE,
            confidence=assessment.CONFIDENCE_LOW,
            rationale="no frozen manifest states what kind of work either cohort did",
            decision=assessment.Decision(
                settlement=assessment.SETTLEMENT_RETAIN,
                decided_at=SETTLED_AT,
                reason="nothing measured said the release did harm, so it stays on the line",
                rollback_revision=None,
            ),
        ),
    )

    read = assessment.read(config, second)
    assert read is not None and read.settled is True
    assert read.decision is not None and read.decision.settlement == assessment.SETTLEMENT_RETAIN


def test_a_rolled_back_settlement_reads_against_the_rollback_record(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The ordinary flow of a regression finding, end to end.

    The assessment is formed while the release still stands, so its `assessed`
    block says so and keeps saying so — re-deriving that field would make the
    rollback contradict the finding that caused it. The settlement is appended
    afterwards, and what it is held to is the rollback record: the reading the
    lineage has *now*, not the state the assessment was formed in.
    """

    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    assert frame.subject.standing is True and frame.subject.rollback_revision is None
    built = build(
        frame,
        verdict=assessment.VERDICT_REGRESSED,
        metrics=(measurement(before=1.6, after=2.4),),
        counterfactual=counterfactual(frame, metrics=SLOWER, detail=WORSE),
        rationale="the pinned run converged more slowly at the promoted revision",
    )
    publish(second, built)
    assert assessment.read(config, second) is not None

    reversal = rollback.rollback(config, reason=REVERSAL, now=REVERSED_AT)
    publish(
        second,
        dataclasses.replace(
            built,
            decision=assessment.Decision(
                settlement=assessment.SETTLEMENT_ROLLED_BACK,
                decided_at=SETTLED_AT,
                reason=REVERSAL,
                rollback_revision=reversal.revision,
            ),
        ),
    )

    read = assessment.read(config, second)
    assert read is not None and read.settled is True
    assert read.decision is not None and read.decision.rollback_revision == reversal.revision
    # Still the release as it stood when it was judged: the reading is what
    # justified the reversal, so it cannot be rewritten by it.
    assert read.subject.standing is True and read.subject.rollback_revision is None


def interrupt_before_the_line_moves(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave a rollback in the durable state between its two writes: the inverse
    commit made and recorded, the source line not yet moved onto it.

    The state a killed process leaves, and one the next run finishes rather than
    starting over — so a record beside it is read while it lasts.
    """

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt("stopped before the source line moved")

    monkeypatch.setattr(rollback, "_land", interrupted)


def test_a_settlement_beside_a_prepared_rollback_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rollback record names its commit before the line takes it.

    So the commit's identity is not the answer to whether the release came off:
    in this window the record already states the revision and the promotion is
    still what the line has. A settlement read on the revision alone would call an
    interrupted rollback a completed one — and the settlement is what the next
    base freeze is gated on, so it would gate on a release still standing.
    """

    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    built = build(
        frame,
        verdict=assessment.VERDICT_REGRESSED,
        metrics=(measurement(before=1.6, after=2.4),),
        counterfactual=counterfactual(frame, metrics=SLOWER, detail=WORSE),
        rationale="the pinned run converged more slowly at the promoted revision",
    )
    publish(second, built)

    interrupt_before_the_line_moves(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        rollback.rollback(config, reason=REVERSAL, now=REVERSED_AT)

    prepared = json.loads((config.batches_root / FIRST / "rollback.json").read_text(encoding="utf-8"))
    assert prepared["reverted_at"] is None
    interrupted = assessment.describe(config, second)
    assert interrupted is not None
    assert interrupted.subject.rollback_revision == prepared["revision"]
    assert interrupted.subject.standing is True
    # The reading itself is unaffected: what the cohorts held did not change, and
    # the record says nothing about a reversal yet.
    assert assessment.read(config, second) is not None

    settled = dataclasses.replace(
        built,
        decision=assessment.Decision(
            settlement=assessment.SETTLEMENT_ROLLED_BACK,
            decided_at=SETTLED_AT,
            reason=REVERSAL,
            rollback_revision=prepared["revision"],
        ),
    )
    publish(second, settled)
    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "prepared without the source line recorded as carrying it" in str(error.value)

    # The same record, once the operation finishes the rollback it prepared: the
    # commit is unchanged and what changed is the line.
    monkeypatch.undo()
    reversal = rollback.rollback(config, reason=REVERSAL, now=FINISHED_AT)
    assert reversal.revision == prepared["revision"]

    read = assessment.read(config, second)
    assert read is not None and read.settled is True
    assert read.decision is not None and read.decision.rollback_revision == prepared["revision"]


def test_a_reading_of_a_reversal_the_line_never_took_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same rule where the record makes the claim about itself.

    `assessed.standing` stays historical — a rollback the reading caused may not
    contradict the reading — but a record asserting the release was already off
    the line when it was judged is asserting something the repository has to
    have. A prepared inverse commit is not that, and the exception costs nothing:
    a promotion a completed rollback reversed is never effective again, so a
    record legitimately formed against a reversed release still reads against one.
    """

    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None

    interrupt_before_the_line_moves(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        rollback.rollback(config, reason=REVERSAL, now=REVERSED_AT)
    prepared = json.loads((config.batches_root / FIRST / "rollback.json").read_text(encoding="utf-8"))

    publish(
        second,
        build(
            frame,
            metrics=(),
            verdict=assessment.VERDICT_INCONCLUSIVE,
            subject=dataclasses.replace(
                frame.subject, standing=False, rollback_revision=prepared["revision"]
            ),
        ),
    )
    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "prepared without the source line recorded as carrying it" in str(error.value)


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


# --- recording a reading ----------------------------------------------------


INCONCLUSIVE_WHY = "no frozen manifest states what kind of work either cohort did"


def ledger_records(config: evolution.EvolutionConfig, record_type: str) -> list[dict]:
    return [item for item in evolution.read_records(config) if item["record_type"] == record_type]


def test_forming_derives_everything_but_the_judgement(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """A caller supplies the numbers and the reading; the cohorts, denominators,
    exclusions and facets come from the frozen manifests and Git."""

    second = freeze_second(config, promoted)

    formed = assessment.form(
        config,
        verdict=assessment.VERDICT_INCONCLUSIVE,
        confidence=assessment.CONFIDENCE_LOW,
        rationale=INCONCLUSIVE_WHY,
        metrics=(measurement(),),
    )

    assert formed.recorded is True
    assert formed.batch_id == SECOND
    assert formed.record_path == second.assessment_path
    recorded = formed.assessment
    assert recorded.before == ("b1", "b2", "b3") and recorded.before_task_count == 3
    assert recorded.after == ("a1", "a2", "a3") and recorded.after_task_count == 3
    assert recorded.excluded == ()
    assert recorded.comparability.incoherent == (assessment.FACET_TASK_SHAPE,)
    assert recorded.subject.revision == promoted.promotion_revision
    assert recorded.rationale == INCONCLUSIVE_WHY
    assert recorded.counterfactual is None and recorded.decision is None

    # On disk exactly as the reader loads it back.
    read = assessment.read(config, second)
    assert read is not None and read.to_json() == recorded.to_json()
    assert json.loads(second.assessment_path.read_text(encoding="utf-8")) == recorded.to_json()


def test_forming_audits_the_release_it_read(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The audit line names the cohort that read it, the release it was about,
    and the verdict — which is a bounded code this package authored."""

    freeze_second(config, promoted)
    assessment.form(
        config,
        verdict=assessment.VERDICT_INCONCLUSIVE,
        confidence=assessment.CONFIDENCE_LOW,
        rationale=INCONCLUSIVE_WHY,
    )

    lines = ledger_records(config, assessment.RECORD_RELEASE_ASSESSED)
    assert len(lines) == 1
    assert lines[0]["batch_id"] == SECOND
    assert lines[0]["revision"] == promoted.promotion_revision
    assert lines[0]["experiment_id"] == promoted.experiment_id
    assert lines[0]["detail"] == assessment.VERDICT_INCONCLUSIVE


def test_forming_states_a_prepared_reversal_as_no_reversal_at_all(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rollback whose inverse commit exists and has not reached the line leaves
    the promotion standing, and a reading formed then says so.

    The derived subject carries that commit — it has to, since a report produced
    at a line that took it belongs to neither cohort — but `assessed` is about
    what the line held, and stating a reversal beside `standing: true` is the
    contradiction the reader refuses.
    """

    second = freeze_second(config, promoted)
    interrupt_before_the_line_moves(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        rollback.rollback(config, reason=REVERSAL, now=REVERSED_AT)
    prepared = json.loads((config.batches_root / FIRST / "rollback.json").read_text(encoding="utf-8"))

    derived = assessment.describe(config, second)
    assert derived is not None
    assert derived.subject.standing is True and derived.subject.rollback_revision == prepared["revision"]

    formed = assessment.form(
        config,
        verdict=assessment.VERDICT_INCONCLUSIVE,
        confidence=assessment.CONFIDENCE_LOW,
        rationale=INCONCLUSIVE_WHY,
    )

    assert formed.assessment.subject.standing is True
    assert formed.assessment.subject.rollback_revision is None
    assert assessment.read(config, second) is not None


def test_a_verdict_the_evidence_cannot_carry_writes_nothing(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The rule the reader applies is the rule the writer passes. A directional
    claim with no counterfactual behind it never reaches the disk, so nothing has
    to be discovered by whoever reads next."""

    second = freeze_second(config, promoted)

    with pytest.raises(evolution.BatchError) as error:
        assessment.form(
            config,
            verdict=assessment.VERDICT_IMPROVED,
            confidence=assessment.CONFIDENCE_HIGH,
            rationale="the cohort after the release converged faster",
            metrics=(measurement(),),
        )

    assert "resting on the cohorts alone" in str(error.value)
    assert assessment.FACET_TASK_SHAPE in str(error.value)
    assert second.assessment_path.exists() is False
    assert ledger_records(config, assessment.RECORD_RELEASE_ASSESSED) == []


def test_a_reading_with_no_reason_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    freeze_second(config, promoted)

    with pytest.raises(evolution.BatchError) as error:
        assessment.form(
            config,
            verdict=assessment.VERDICT_INCONCLUSIVE,
            confidence=assessment.CONFIDENCE_LOW,
            rationale="   ",
        )
    assert "why its verdict is that verdict" in str(error.value)


def test_the_same_formation_run_again_reports_what_it_wrote(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """An interrupted formation is finished by running it again: the record it
    already wrote is the one being asked for, and no second audit line is
    appended for an event that happened once."""

    freeze_second(config, promoted)
    first = assessment.form(
        config,
        verdict=assessment.VERDICT_INCONCLUSIVE,
        confidence=assessment.CONFIDENCE_LOW,
        rationale=INCONCLUSIVE_WHY,
        metrics=(measurement(),),
    )

    again = assessment.form(
        config,
        verdict=assessment.VERDICT_INCONCLUSIVE,
        confidence=assessment.CONFIDENCE_LOW,
        # The same sentence, wrapped differently — a reason travels in a versioned
        # record and is compared there.
        rationale="no frozen manifest\n  states what kind of work either cohort did",
        metrics=(measurement(),),
    )

    assert again.recorded is False
    assert again.assessment.to_json() == first.assessment.to_json()
    assert len(ledger_records(config, assessment.RECORD_RELEASE_ASSESSED)) == 1


def test_a_second_reading_of_one_release_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    freeze_second(config, promoted)
    assessment.form(
        config,
        verdict=assessment.VERDICT_INCONCLUSIVE,
        confidence=assessment.CONFIDENCE_LOW,
        rationale=INCONCLUSIVE_WHY,
    )

    with pytest.raises(evolution.BatchError) as error:
        assessment.form(
            config,
            verdict=assessment.VERDICT_INCONCLUSIVE,
            confidence=assessment.CONFIDENCE_MEDIUM,
            rationale="the after cohort was produced at a revision that carries the change",
        )
    assert "reads a release once" in str(error.value)


def test_nothing_is_formed_where_there_is_no_release(
    config: evolution.EvolutionConfig,
    release: str,
) -> None:
    write_manifest(config.batches_root, FIRST, ["r1"], analysis_task_id="2026-07-31-first")

    with pytest.raises(evolution.BatchError) as error:
        assessment.form(
            config,
            verdict=assessment.VERDICT_INCONCLUSIVE,
            confidence=assessment.CONFIDENCE_LOW,
            rationale=INCONCLUSIVE_WHY,
        )
    assert "follows no promotion" in str(error.value)
    assert (config.batches_root / FIRST / "release-assessment.json").exists() is False


def test_a_later_cohort_may_not_reread_a_release_that_was_answered(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The obligation belongs to the first cohort after the promotion and is
    discharged there. A later one derives the same release — that is how it reads
    the record — and is told whose reading it is.

    The refusal is about an obligation that was *answered*, not about who is
    standing at the keyboard: one the owning cohort left outstanding is followed
    to where it sits, because a gate nobody could answer would stop the lineage
    for good.
    """

    second = freeze_second(config, promoted)
    form_reading(config)
    assessment.settle(config, settlement=assessment.SETTLEMENT_RETAIN, reason=KEPT, now=RESOLVED_AT)
    write_outcome(config.batches_root, SECOND, outcome="no-change")
    third = freeze_second(config, promoted, batch_id=THIRD)

    with pytest.raises(evolution.BatchError) as error:
        assessment.form(
            config,
            verdict=assessment.VERDICT_INCONCLUSIVE,
            confidence=assessment.CONFIDENCE_LOW,
            rationale=INCONCLUSIVE_WHY,
        )
    assert f"the first batch frozen after that promotion is {SECOND}" in str(error.value)
    assert third.assessment_path.exists() is False
    assert read_reading(config, second).settled is True


def test_no_current_cohort_reads_nothing(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """Every batch concluded: there is a release on the line and no cohort whose
    reading it would be."""

    with pytest.raises(evolution.BatchError) as error:
        assessment.form(
            config,
            verdict=assessment.VERDICT_INCONCLUSIVE,
            confidence=assessment.CONFIDENCE_LOW,
            rationale=INCONCLUSIVE_WHY,
        )
    assert "no batch is current" in str(error.value)


def test_a_reading_is_formed_while_the_analysis_is_still_being_written(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
    tmp_path: Path,
) -> None:
    """The generated analysis task asks for this reading, so it is written before
    that task closes — which is exactly the state every other guarded operation
    refuses to act in."""

    fill_pool(config, tmp_path / "feed")
    result = evolution.freeze(config, now=FROZEN_AT, runner_revision="v2.2.0")
    assert result.batch_id == SECOND
    awaiting = evolution.batch_awaiting_analysis(config)
    assert awaiting is not None and awaiting.batch_id == SECOND

    formed = assessment.form(
        config,
        verdict=assessment.VERDICT_INCONCLUSIVE,
        confidence=assessment.CONFIDENCE_LOW,
        rationale=INCONCLUSIVE_WHY,
    )

    assert formed.recorded is True
    assert formed.batch_id == SECOND


def test_a_naive_moment_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    freeze_second(config, promoted)

    with pytest.raises(evolution.BatchError) as error:
        assessment.form(
            config,
            verdict=assessment.VERDICT_INCONCLUSIVE,
            confidence=assessment.CONFIDENCE_LOW,
            rationale=INCONCLUSIVE_WHY,
            now=datetime(2026, 8, 11, 9, 0, 0),
        )
    assert "timezone-aware" in str(error.value)


# --- the counterfactual -----------------------------------------------------


def read_reading(config: evolution.EvolutionConfig, batch: lineage.Batch) -> assessment.Assessment:
    """The record as the reader loads it back — what every assertion below is
    made against, rather than the value an operation happened to return."""

    read = assessment.read(config, batch)
    assert read is not None
    return read


def form_reading(config: evolution.EvolutionConfig) -> assessment.Formed:
    """The cohort reading a counterfactual is started from.

    `inconclusive` because that is what a real one is: no manifest states the
    shape of the work, so the two task sets suspect and the pinned run settles.
    """

    return assessment.form(
        config,
        verdict=assessment.VERDICT_INCONCLUSIVE,
        confidence=assessment.CONFIDENCE_LOW,
        rationale=INCONCLUSIVE_WHY,
        metrics=(measurement(),),
    )


class Unanswering(FakeHarness):
    """A harness that cannot say what it is running. Whether it began anything is
    exactly what nobody here can know, which is what the durable request is for."""

    def start(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        raise RuntimeError("the harness process died before it named the run")


def test_the_counterfactual_pins_the_release_and_moves_nothing(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The pair is the merge unit the outcome states, handed to the replay
    boundary as one integration: the promotion is the candidate already merged
    onto the line at its own first parent, so nothing is merged again here.

    And nothing moves. The release ref, the checkout and the frozen membership
    are exactly what they were — the harness exercises both revisions wherever it
    likes, and this controller's whole part is pinning what it exercises.
    """

    second = freeze_second(config, promoted)
    form_reading(config)
    before = (git_rev(config.repo_root, RELEASE_REF), git_rev(config.repo_root, "HEAD"))
    membership = second.manifest_path.read_bytes()
    harness = FakeHarness()

    started = assessment.measure(config, harness, expectation=EXPECTATION, now=MEASURED_AT)

    request = harness.requests[0]
    assert request.integration == replay.Integration(
        base_revision=promoted.merge_input_revision,
        candidate_revision=promoted.promotion_revision,
        merge_input_revision=promoted.merge_input_revision,
        merge_input_ref=RELEASE_REF,
        tree=promoted.tree,
    )
    # Nothing to reproduce: one run measures both revisions, so the cohort, the
    # evaluator and the configuration are one selection governing both halves.
    assert request.reproduce is None

    run = read_reading(config, second).counterfactual
    assert run is not None and run.running is True
    assert run.integration.candidate_revision == promoted.promotion_revision
    assert run.integration.base_revision == promoted.merge_input_revision
    assert run.cases == started.plan.cases and run.evaluator == started.plan.evaluator
    assert run.harness == started.plan.harness
    assert run.expectation == EXPECTATION
    assert started.resumed is False

    assert (git_rev(config.repo_root, RELEASE_REF), git_rev(config.repo_root, "HEAD")) == before
    assert second.manifest_path.read_bytes() == membership


def test_the_position_is_one_the_promoted_experiment_can_never_hold(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """A harness answers one key with one run, so a counterfactual keyed inside
    the experiment's own rounds would be answered with that experiment's run.

    Checked against the real replay record rather than against the rule: the
    positions an experiment holds are its runs *and* the requests it withdrew,
    and every one of them names a round it has.
    """

    second = freeze_second(config, promoted)
    form_reading(config)
    assessment.measure(config, FakeHarness(), expectation=EXPECTATION, now=MEASURED_AT)

    first = next(item for item in lineage.describe(config).batches if item.batch_id == FIRST)
    experiment = next(item for item in first.experiments if item.experiment_id == promoted.experiment_id)
    history = replay.read_replays(config, experiment)
    held = {(item.round_number, item.attempt) for item in history.replays} | {
        (item.round_number, item.attempt) for item in history.withdrawn
    }
    run = read_reading(config, second).counterfactual
    assert run is not None
    assert run.position.experiment_id == promoted.experiment_id
    assert run.position.round_number == max(round_.number for round_ in experiment.rounds) + 1
    assert (run.position.round_number, run.position.attempt) not in held


def test_a_key_inside_the_experiments_rounds_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The rule the writer keeps, kept by the reader too: a record written beside
    this one by hand escapes nothing."""

    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    run = counterfactual(frame)
    publish(
        second,
        build(
            frame,
            counterfactual=dataclasses.replace(
                run,
                position=dataclasses.replace(run.position, round_number=frame.subject.round_number),
            ),
        ),
    )

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "answered with the run that round was measured by" in str(error.value)


def test_the_request_is_durable_before_the_harness_answers(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The window the request exists for: the harness may be running something
    this record does not describe yet, so what names that run is written first.

    A resume asks for the same run at the same key — a conforming harness answers
    with the one it already began — and the run is recorded as having started
    when its pair was pinned, not when the resume finally heard back.
    """

    second = freeze_second(config, promoted)
    form_reading(config)

    with pytest.raises(RuntimeError):
        assessment.measure(config, Unanswering(), expectation=EXPECTATION, now=MEASURED_AT)

    requested = read_reading(config, second).requested
    assert requested is not None
    assert requested.integration.candidate_revision == promoted.promotion_revision
    assert requested.requested_at == "2026-08-11T07:00:00Z"
    assert read_reading(config, second).counterfactual is None

    harness = FakeHarness()
    resumed = assessment.measure(config, harness, expectation=EXPECTATION, now=CONCLUDED_AT)

    assert resumed.resumed is True
    assert harness.requests[0].attempt == requested.position.attempt
    run = read_reading(config, second).counterfactual
    assert run is not None
    assert run.position == requested.position
    assert run.started_at == requested.requested_at
    assert read_reading(config, second).requested is None


def test_a_resume_may_not_restate_the_prediction(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """An expectation is recorded before the numbers exist and the run may
    already be producing them, so the one on record stands."""

    freeze_second(config, promoted)
    form_reading(config)
    with pytest.raises(RuntimeError):
        assessment.measure(config, Unanswering(), expectation=EXPECTATION, now=MEASURED_AT)

    with pytest.raises(evolution.BatchError) as error:
        assessment.measure(config, FakeHarness(), expectation="no change either way", now=CONCLUDED_AT)
    assert "outstanding, expected to show" in str(error.value)


def test_a_run_started_under_one_still_going_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    freeze_second(config, promoted)
    form_reading(config)
    assessment.measure(config, FakeHarness(), expectation=EXPECTATION, now=MEASURED_AT)

    with pytest.raises(evolution.BatchError) as error:
        assessment.measure(config, FakeHarness(), expectation=EXPECTATION, now=CONCLUDED_AT)
    assert "still running" in str(error.value)
    assert "conclude that run first" in str(error.value)


def test_the_release_is_measured_once(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    freeze_second(config, promoted)
    form_reading(config)
    harness = FakeHarness(report=completed_report())
    assessment.measure(config, harness, expectation=EXPECTATION, now=MEASURED_AT)
    assessment.conclude(config, harness, now=CONCLUDED_AT)

    with pytest.raises(evolution.BatchError) as error:
        assessment.measure(config, FakeHarness(), expectation=EXPECTATION, now=RESOLVED_AT)
    assert "the release is measured once" in str(error.value)


def test_concluding_records_the_numbers_in_this_records_vocabulary(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """A run states a baseline and a candidate; here the two sides are the line
    before the release and the release, so they are recorded as before and after
    — the same names the cohort comparison uses, which is what lets the two be
    read together at all."""

    second = freeze_second(config, promoted)
    form_reading(config)
    harness = FakeHarness(report=completed_report())
    assessment.measure(config, harness, expectation=EXPECTATION, now=MEASURED_AT)

    concluded = assessment.conclude(config, harness, now=CONCLUDED_AT)

    assert concluded.recorded is True and concluded.running is False
    assert concluded.outcome == replay.RESULT_COMPLETED
    run = read_reading(config, second).counterfactual
    assert run is not None and run.completed is True
    assert run.result is not None
    assert run.result.metrics == (
        assessment.Measurement(metric="remediation-rounds", unit="rounds", before=2.4, after=1.6, better="lower"),
    )
    assert run.result.concluded_at == "2026-08-11T12:00:00Z"

    lines = ledger_records(config, assessment.RECORD_COUNTERFACTUAL_COMPLETED)
    assert len(lines) == 1
    assert lines[0]["batch_id"] == SECOND
    assert lines[0]["revision"] == promoted.promotion_revision
    assert lines[0]["round"] == run.position.round_number
    assert lines[0]["detail"] == replay.RESULT_COMPLETED


def test_polling_a_run_that_is_going_writes_nothing(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The ordinary case and not an error, so it can be called as often as an
    operator likes; and a conclusion run again reports what it already wrote
    rather than polling for a second result."""

    second = freeze_second(config, promoted)
    form_reading(config)
    harness = FakeHarness(report=None)
    assessment.measure(config, harness, expectation=EXPECTATION, now=MEASURED_AT)
    written = second.assessment_path.read_bytes()

    still = assessment.conclude(config, harness, now=CONCLUDED_AT)

    assert still.recorded is False and still.running is True
    assert second.assessment_path.read_bytes() == written
    assert ledger_records(config, assessment.RECORD_COUNTERFACTUAL_COMPLETED) == []

    harness.report = completed_report()
    assessment.conclude(config, harness, now=CONCLUDED_AT)
    again = assessment.conclude(config, harness, now=RESOLVED_AT)
    assert again.recorded is False and again.running is False
    assert len(ledger_records(config, assessment.RECORD_COUNTERFACTUAL_COMPLETED)) == 1


def test_a_report_this_record_cannot_hold_leaves_the_run_where_it_was(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """A harness that reached the end of the cohort has numbers to state, and one
    that did not is a failure with a reason — a report claiming both is neither.

    Nothing is lost by refusing it: the run stays recorded as going, so it can be
    asked again or ended with a reason.
    """

    second = freeze_second(config, promoted)
    form_reading(config)
    harness = FakeHarness(report=None)
    assessment.measure(config, harness, expectation=EXPECTATION, now=MEASURED_AT)
    harness.report = completed_report(outcome=replay.RESULT_FAILED, metrics=(slower(),))

    with pytest.raises(evolution.BatchError) as error:
        assessment.conclude(config, harness, now=CONCLUDED_AT)

    assert "end the run and record why" in str(error.value)
    run = read_reading(config, second).counterfactual
    assert run is not None and run.running is True
    assert ledger_records(config, assessment.RECORD_COUNTERFACTUAL_COMPLETED) == []


def test_a_run_whose_harness_cannot_report_is_ended_and_answered_by_another(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """A run is going until something records that it stopped, and a harness that
    died would otherwise leave the one comparison the release can be settled by
    unmeasurable behind a run nothing will ever conclude.

    What answers a failure is another attempt, at the next key: the harness is
    keyed on the position, so reissuing the failed one would be answered with the
    run that failed.
    """

    second = freeze_second(config, promoted)
    form_reading(config)
    assessment.measure(config, FakeHarness(report=None), expectation=EXPECTATION, now=MEASURED_AT)

    ended = assessment.abandon(config, reason=DIED, now=CONCLUDED_AT)

    assert ended.recorded is True and ended.outcome == replay.RESULT_FAILED
    assert ended.run.result is not None and ended.run.result.metrics == ()
    assert assessment.abandon(config, reason=DIED, now=RESOLVED_AT).recorded is False
    assert len(ledger_records(config, assessment.RECORD_COUNTERFACTUAL_COMPLETED)) == 1

    harness = FakeHarness(report=completed_report())
    again = assessment.measure(config, harness, expectation=EXPECTATION, now=RESOLVED_AT)

    assert again.run.position.attempt == 2
    assert harness.requests[0].attempt == 2
    run = read_reading(config, second).counterfactual
    assert run is not None and run.position.attempt == 2


def test_a_running_run_with_no_handle_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The handle is the whole of what connects this record to the work: a
    running run without one can never be concluded or ended."""

    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    run = counterfactual(frame)
    publish(
        second,
        build(
            frame,
            verdict=assessment.VERDICT_INCONCLUSIVE,
            counterfactual=dataclasses.replace(
                run,
                harness=dataclasses.replace(run.harness, handle=None),
                result=None,
            ),
        ),
    )

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "can never be concluded" in str(error.value)


def test_a_request_beside_a_recorded_run_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The write that records the run a request became clears it in the same
    file, so a record holding both leaves a reader guessing which of the two the
    harness is running."""

    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    run = counterfactual(frame)
    publish(
        second,
        build(
            frame,
            counterfactual=run,
            requested=assessment.CounterfactualRequest(
                position=dataclasses.replace(run.position, attempt=2),
                integration=run.integration,
                expectation=EXPECTATION,
                requested_at="2026-08-11T09:30:00Z",
            ),
        ),
    )

    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "clears it in the same file" in str(error.value)


def nameless_harness() -> FakeHarness:
    """A harness that begins a run and gives it no name — the one answer this
    record cannot hold, since the handle is the whole of what a later process
    polls."""

    return FakeHarness(
        plan=replay.ReplayPlan(
            cases=replay.CaseSet(case_set_id="loader-regressions", case_set_sha256="c" * 64, count=12, excluded=()),
            evaluator=replay.Evaluator(backend="claude", model="claude-opus-5", rubric_revision="r7"),
            harness=replay.Harness(id="local-replay", revision="0.1.0", config_sha256="d" * 64, handle=None),
        )
    )


def withdrawal(frame: assessment.Frame, attempt: int, **overrides) -> assessment.WithdrawnRequest:
    """A position a request held and gave up, on this release's own pair."""

    subject = frame.subject
    fields = {
        "position": assessment.Position(
            experiment_id=subject.experiment_id,
            round_number=subject.round_number + 1,
            attempt=attempt,
        ),
        "integration": assessment.Pinned(
            base_revision=subject.merge_input_revision,
            candidate_revision=subject.revision,
            source_ref=subject.merge_input_ref,
            tree=subject.tree,
        ),
        "requested_at": "2026-08-11T07:00:00Z",
        "withdrawn_at": "2026-08-11T07:30:00Z",
    }
    fields.update(overrides)
    return assessment.WithdrawnRequest(**fields)


def failed_run(frame: assessment.Frame, **overrides) -> assessment.Counterfactual:
    """A run that measured nothing and said why — the one state another attempt
    answers."""

    return counterfactual(
        frame,
        result=assessment.RunResult(
            outcome=replay.RESULT_FAILED,
            concluded_at="2026-08-11T08:00:00Z",
            detail=DIED,
            elapsed_seconds=None,
            metrics=(),
            regressions=(),
            ambiguity=None,
        ),
        **overrides,
    )


def test_a_request_the_harness_cannot_describe_is_taken_back(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The way out of the window the request covers, and the only one there is.

    A harness that cannot say what it is running leaves `measure` unable to
    finish, and asking again does not help: a conforming harness answers one key
    with the run it already began, so the same unrecordable answer comes back for
    as long as that request stands. Nothing else clears it either — the two
    operations that end a measurement act on a run, and there is none. Without
    this the release would stay permanently unmeasurable, and with it the only
    comparison a regression can be settled by.
    """

    second = freeze_second(config, promoted)
    form_reading(config)
    nameless = nameless_harness()

    with pytest.raises(evolution.BatchError) as error:
        assessment.measure(config, nameless, expectation=EXPECTATION, now=MEASURED_AT)
    assert "withdraw the request" in str(error.value)

    # The same key, the same answer: the request is what a harness recognises, so
    # re-asking cannot be the repair.
    with pytest.raises(evolution.BatchError):
        assessment.measure(config, nameless, expectation=EXPECTATION, now=CONCLUDED_AT)
    assert [request.attempt for request in nameless.requests] == [1, 1]
    assert read_reading(config, second).counterfactual is None
    # And neither ending operation reaches it: both act on a recorded run.
    for ending in (
        lambda: assessment.conclude(config, FakeHarness(report=completed_report()), now=CONCLUDED_AT),
        lambda: assessment.abandon(config, reason=DIED, now=CONCLUDED_AT),
    ):
        with pytest.raises(evolution.BatchError) as error:
            ending()
        assert "no run recorded for it" in str(error.value)

    taken = assessment.withdraw(config, now=CONCLUDED_AT)

    assert taken.withdrawn is True
    assert taken.request is not None and taken.request.position.attempt == 1
    reading = read_reading(config, second)
    assert reading.requested is None
    # The position is not handed back with the request. What is on record is what
    # an operator needs to find the run that may be going under it: the window it
    # would have started in, and the pair it was pinned to.
    [given_up] = reading.withdrawn
    assert given_up.position.attempt == 1
    assert given_up.requested_at == "2026-08-11T07:00:00Z"
    assert given_up.withdrawn_at == "2026-08-11T12:00:00Z"
    assert given_up.integration.candidate_revision == promoted.promotion_revision
    # Nothing measured, so nothing is audited: the ledger records outcomes, and a
    # request that never became a run produced none.
    assert ledger_records(config, assessment.RECORD_COUNTERFACTUAL_COMPLETED) == []

    again = assessment.withdraw(config, now=RESOLVED_AT)
    assert again.withdrawn is False and again.request is None
    assert len(read_reading(config, second).withdrawn) == 1

    # The next attempt takes the position after it. Attempt 1 is spoken for: the
    # harness may have begun a run keyed to it, and issuing it again would ask
    # that harness for a second run at a name it already answers for.
    harness = FakeHarness(report=completed_report())
    started = assessment.measure(config, harness, expectation=EXPECTATION, now=RESOLVED_AT)

    assert started.run.position.attempt == 2
    assert harness.requests[0].attempt == 2
    run = read_reading(config, second).counterfactual
    assert run is not None and run.position.attempt == 2


def test_a_position_a_withdrawal_holds_is_never_asked_for_again(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The rule the allocation keeps, kept by the reader too.

    A request wearing a position a withdrawal holds would be answered with that
    withdrawal's run, and one skipping past the allocation leaves a key nothing
    accounts for. Both are records no operation here writes.
    """

    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    given_up = withdrawal(frame, 1)
    request = assessment.CounterfactualRequest(
        position=given_up.position,
        integration=given_up.integration,
        expectation=EXPECTATION,
        requested_at="2026-08-11T09:30:00Z",
    )

    # `inconclusive` throughout: nothing has measured this release while a request
    # stands, which is exactly what the reading says.
    def reading(**overrides) -> assessment.Assessment:
        return build(frame, verdict=assessment.VERDICT_INCONCLUSIVE, withdrawn=(given_up,), **overrides)

    publish(second, reading(requested=request))
    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "next attempt is 2" in str(error.value)

    skipped = dataclasses.replace(request, position=dataclasses.replace(request.position, attempt=3))
    publish(second, reading(requested=skipped))
    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "next attempt is 2" in str(error.value)

    # And the position after it reads: a withdrawal is an allocation, so the next
    # request is numbered past it.
    retried = dataclasses.replace(request, position=dataclasses.replace(request.position, attempt=2))
    publish(second, reading(requested=retried))
    assert read_reading(config, second).requested == retried


def test_a_retry_is_numbered_past_the_run_and_the_withdrawals_together(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """A request may stand over a failed run — that is what a retry is — and its
    position counts every attempt the round has handed out, whether it became a
    run or was given up before it could."""

    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    ended = failed_run(frame)
    given_up = withdrawal(frame, 2)
    request = assessment.CounterfactualRequest(
        position=dataclasses.replace(ended.position, attempt=3),
        integration=ended.integration,
        expectation=EXPECTATION,
        requested_at="2026-08-11T09:30:00Z",
    )

    def reading(**overrides) -> assessment.Assessment:
        return build(
            frame,
            verdict=assessment.VERDICT_INCONCLUSIVE,
            counterfactual=ended,
            withdrawn=(given_up,),
            **overrides,
        )

    publish(second, reading(requested=request))
    read = read_reading(config, second)
    assert read.requested == request and read.withdrawn == (given_up,)

    early = dataclasses.replace(request, position=dataclasses.replace(request.position, attempt=2))
    publish(second, reading(requested=early))
    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "next attempt is 3" in str(error.value)

    # A run and a withdrawal wearing one position is the same failure with no
    # request in it: the harness answers that key with one of the two.
    publish(
        second,
        build(
            frame,
            verdict=assessment.VERDICT_INCONCLUSIVE,
            counterfactual=ended,
            withdrawn=(withdrawal(frame, ended.position.attempt),),
        ),
    )
    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "a position is allocated once" in str(error.value)


def test_nothing_is_measured_before_the_cohorts_are_read(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """What a pinned run answers is a suspicion the cohorts raised, so the
    reading is where the run is recorded and there has to be one."""

    second = freeze_second(config, promoted)

    with pytest.raises(evolution.BatchError) as error:
        assessment.measure(config, FakeHarness(), expectation=EXPECTATION, now=MEASURED_AT)
    assert "recorded no reading" in str(error.value)
    assert second.assessment_path.exists() is False


def test_the_verdict_the_run_settles_is_recorded(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The whole arc: an inconclusive reading of two task sets, a run measuring
    the release doing harm, and the reading that run settles."""

    second = freeze_second(config, promoted)
    form_reading(config)
    harness = FakeHarness(report=completed_report(metrics=(slower(),)))
    assessment.measure(config, harness, expectation=EXPECTATION, now=MEASURED_AT)
    assessment.conclude(config, harness, now=CONCLUDED_AT)
    assert read_reading(config, second).verdict == assessment.VERDICT_INCONCLUSIVE

    settled = assessment.resolve(
        config,
        verdict=assessment.VERDICT_REGRESSED,
        confidence=assessment.CONFIDENCE_HIGH,
        rationale=WORSE,
        now=RESOLVED_AT,
    )

    assert settled.recorded is True
    recorded = read_reading(config, second)
    assert recorded.verdict == assessment.VERDICT_REGRESSED
    assert recorded.confidence == assessment.CONFIDENCE_HIGH and recorded.rationale == WORSE
    assert recorded.formed_at == "2026-08-11T13:00:00Z"
    # The cohort reading and the run both stand: the numbers the session judged
    # are what `confidence` and `rationale` are read against.
    assert recorded.metrics == (measurement(),)
    assert recorded.counterfactual is not None and recorded.counterfactual.completed

    lines = ledger_records(config, assessment.RECORD_RELEASE_ASSESSED)
    assert [line["detail"] for line in lines] == [
        assessment.VERDICT_INCONCLUSIVE,
        assessment.VERDICT_REGRESSED,
    ]
    again = assessment.resolve(
        config,
        verdict=assessment.VERDICT_REGRESSED,
        confidence=assessment.CONFIDENCE_HIGH,
        rationale=WORSE,
        now=FINISHED_AT,
    )
    assert again.recorded is False
    assert len(ledger_records(config, assessment.RECORD_RELEASE_ASSESSED)) == 2


def test_a_verdict_the_run_contradicts_reaches_no_record(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The run is the evidence the claim rests on, so a direction it measured the
    other way is an opinion with a run attached."""

    second = freeze_second(config, promoted)
    form_reading(config)
    harness = FakeHarness(report=completed_report(metrics=(slower(),)))
    assessment.measure(config, harness, expectation=EXPECTATION, now=MEASURED_AT)
    assessment.conclude(config, harness, now=CONCLUDED_AT)

    with pytest.raises(evolution.BatchError) as error:
        assessment.resolve(
            config,
            verdict=assessment.VERDICT_IMPROVED,
            confidence=assessment.CONFIDENCE_HIGH,
            rationale="the release converged faster",
            now=RESOLVED_AT,
        )

    assert "the run it records measured 'regressed'" in str(error.value)
    assert read_reading(config, second).verdict == assessment.VERDICT_INCONCLUSIVE
    assert len(ledger_records(config, assessment.RECORD_RELEASE_ASSESSED)) == 1


def test_nothing_is_settled_by_a_run_that_measured_nothing(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """A failed counterfactual is why a suspected regression stays inconclusive:
    the comparison it was going to settle was not made."""

    freeze_second(config, promoted)
    form_reading(config)

    with pytest.raises(evolution.BatchError) as error:
        assessment.resolve(
            config,
            verdict=assessment.VERDICT_REGRESSED,
            confidence=assessment.CONFIDENCE_HIGH,
            rationale=WORSE,
            now=RESOLVED_AT,
        )
    assert "none has been started" in str(error.value)

    assessment.measure(config, FakeHarness(report=None), expectation=EXPECTATION, now=MEASURED_AT)
    assessment.abandon(config, reason=DIED, now=CONCLUDED_AT)

    with pytest.raises(evolution.BatchError) as error:
        assessment.resolve(
            config,
            verdict=assessment.VERDICT_REGRESSED,
            confidence=assessment.CONFIDENCE_HIGH,
            rationale=WORSE,
            now=RESOLVED_AT,
        )
    assert f"ended {replay.RESULT_FAILED!r}" in str(error.value)


def test_nothing_is_added_to_a_reading_the_gate_has_answered(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """A settlement stands on the evidence it stood on: a run started or a
    reading revised afterwards would rewrite the record the decision was made
    from."""

    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    publish(
        second,
        build(
            frame,
            metrics=(),
            verdict=assessment.VERDICT_INCONCLUSIVE,
            confidence=assessment.CONFIDENCE_LOW,
            rationale=INCONCLUSIVE_WHY,
            decision=assessment.Decision(
                settlement=assessment.SETTLEMENT_RETAIN,
                decided_at=SETTLED_AT,
                reason="nothing measured said the release did harm, so it stays on the line",
                rollback_revision=None,
            ),
        ),
    )

    with pytest.raises(evolution.BatchError) as error:
        assessment.measure(config, FakeHarness(), expectation=EXPECTATION, now=MEASURED_AT)
    assert "was settled 'retain'" in str(error.value)

    with pytest.raises(evolution.BatchError) as error:
        assessment.resolve(
            config,
            verdict=assessment.VERDICT_INCONCLUSIVE,
            confidence=assessment.CONFIDENCE_MEDIUM,
            rationale="reread after the gate had answered",
            now=RESOLVED_AT,
        )
    assert "was settled 'retain'" in str(error.value)


RETAINED = assessment.Decision(
    settlement=assessment.SETTLEMENT_RETAIN,
    decided_at=SETTLED_AT,
    reason="nothing measured said the release did harm, so it stays on the line",
    rollback_revision=None,
)


def settled_reading(frame: assessment.Frame, **overrides) -> assessment.Assessment:
    """A reading its gate has answered: inconclusive, retained, and closed."""

    return build(
        frame,
        metrics=(),
        verdict=assessment.VERDICT_INCONCLUSIVE,
        confidence=assessment.CONFIDENCE_LOW,
        rationale=INCONCLUSIVE_WHY,
        decision=RETAINED,
        **overrides,
    )


def test_a_settlement_over_a_measurement_still_in_flight_is_refused(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """Nothing is added to a reading its gate has answered, so a decision taken
    over a run still going is one whose own evidence could never be recorded: the
    numbers would arrive with nowhere to go, and the run would stay going forever
    under a reading that is closed.

    A request the harness never answered for is the same state one field over —
    something may be measuring this release right now — and the states a
    settlement does stand over are the ones that are over: a completed run, a
    failed one, and no run at all.
    """

    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    run = counterfactual(frame)

    publish(second, settled_reading(frame, counterfactual=dataclasses.replace(run, result=None)))
    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "is still going" in str(error.value)

    publish(
        second,
        settled_reading(
            frame,
            requested=assessment.CounterfactualRequest(
                position=run.position,
                integration=run.integration,
                expectation=EXPECTATION,
                requested_at="2026-08-11T07:00:00Z",
            ),
        ),
    )
    with pytest.raises(evolution.BatchError) as error:
        assessment.read(config, second)
    assert "is outstanding" in str(error.value)

    publish(second, settled_reading(frame, counterfactual=run))
    assert read_reading(config, second).settled is True
    publish(second, settled_reading(frame, counterfactual=failed_run(frame)))
    assert read_reading(config, second).settled is True


def test_a_settled_reading_is_neither_concluded_nor_ended(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The gate closes the record to every operation, not only to the two that
    add a reading to it: a run concluded or ended after the settlement would
    rewrite the evidence the decision was made from."""

    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    publish(second, settled_reading(frame, counterfactual=counterfactual(frame)))
    written = second.assessment_path.read_bytes()
    harness = FakeHarness(report=completed_report())

    with pytest.raises(evolution.BatchError) as error:
        assessment.conclude(config, harness, now=RESOLVED_AT)
    assert "was settled 'retain'" in str(error.value)

    with pytest.raises(evolution.BatchError) as error:
        assessment.abandon(config, reason=DIED, now=RESOLVED_AT)
    assert "was settled 'retain'" in str(error.value)

    with pytest.raises(evolution.BatchError) as error:
        assessment.withdraw(config, now=RESOLVED_AT)
    assert "was settled 'retain'" in str(error.value)

    # Nothing was written and nothing was even asked: the refusal is made before
    # the harness is polled.
    assert second.assessment_path.read_bytes() == written
    assert harness.polled == []
    assert ledger_records(config, assessment.RECORD_COUNTERFACTUAL_COMPLETED) == []


def build_absent_release(config: evolution.EvolutionConfig) -> lineage.Batch:
    """A promoted batch whose revisions this repository has never held.

    Every record a promotion leaves, and no objects behind them — the ordinary
    state of a clone that holds the evolution history and not the release line.
    The lineage reads such a promotion without complaint (what the commit carries
    is checked only where the commit can be described), which is exactly why the
    run that is about to be handed that pair asks for itself.
    """

    experiment_id = f"{FIRST}-exp-01"
    write_manifest(config.batches_root, FIRST, ["b1"], analysis_task_id="2026-07-31-first")
    write_closure(config.batches_root, FIRST, analysis_task_id="2026-07-31-first")
    write_experiment(
        config.experiments_root,
        experiment_id,
        rounds=[experiment_round(1, candidate_revision="c" * 40)],
        decision=experiment_decision("promoted", promotion_revision="f" * 40),
    )
    write_outcome(
        config.batches_root,
        FIRST,
        outcome="promoted",
        experiment_id=experiment_id,
        promotion_revision="f" * 40,
        reason="the approach needs a loader change this batch cannot justify",
    )
    write_manifest(config.batches_root, SECOND, ["a1"], analysis_task_id="2026-08-10-second")
    return next(batch for batch in evolution.load_batches(config) if batch.batch_id == SECOND)


def test_a_release_this_checkout_does_not_hold_is_not_measured(
    config: evolution.EvolutionConfig,
) -> None:
    """A harness cannot exercise what this repository does not have, and the
    refusal an operator can act on is `fetch the release line` rather than
    whatever a missing object looks like from inside somebody else's harness.

    Nothing is written by the refusal: no request stands, so no harness was ever
    asked and there is no run anywhere to go looking for.
    """

    absent = build_absent_release(config)
    form_reading(config)

    with pytest.raises(evolution.BatchError) as error:
        assessment.measure(config, FakeHarness(), expectation=EXPECTATION, now=MEASURED_AT)
    assert "does not hold" in str(error.value)
    assert f"fetch {RELEASE_REF}" in str(error.value)
    assert read_reading(config, absent).requested is None
    assert read_reading(config, absent).counterfactual is None


# --- the settlement, and the base freeze it releases -------------------------


KEPT = "the reading found nothing measured against the release, so it stays on the line"


def suspected(config: evolution.EvolutionConfig, batch: lineage.Batch) -> assessment.Assessment:
    """A reading whose pinned run measured the release doing harm.

    The state the settlement gate exists for: `regressed` rests on a completed
    counterfactual in every case, so this is what a rollback is decided from.
    """

    frame = assessment.describe(config, batch)
    assert frame is not None
    built = build(
        frame,
        verdict=assessment.VERDICT_REGRESSED,
        metrics=(measurement(before=1.6, after=2.4),),
        counterfactual=counterfactual(frame, metrics=SLOWER, detail=WORSE),
        rationale="the pinned run converged more slowly at the promoted revision",
    )
    publish(batch, built)
    return built


def open_second(config: evolution.EvolutionConfig, batch: lineage.Batch, *drafts: str) -> list[str]:
    """Everything the assessing cohort needs before its first experiment: its
    analysis stage ended, and drafts waiting at the admission gate.

    Draft ids of its own, because a draft's id is its task's: the promoted batch
    admitted `loader-fallback` and `hook-side-loader`, and one task implements one
    proposal.
    """

    (config.batches_root / batch.batch_id / "findings.md").write_text("# Findings\n", encoding="utf-8")
    write_closure(config.batches_root, batch.batch_id, analysis_task_id=f"2026-08-10-{batch.batch_id}")
    chosen = list(drafts) or ["status-orphans"]
    for draft_id in chosen:
        write_draft(config.batches_root, batch.batch_id, draft_id)
    return chosen


def test_retaining_a_release_leaves_it_on_the_line(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The ordinary answer: the reading found nothing that costs the release, so
    the line keeps it and the next base is frozen on it.

    Nothing about the source line changes, and the audit says which way the gate
    went — a `retain` that left no trace would be indistinguishable from a gate
    nobody answered, which is the state the base freeze waits on.
    """

    second = freeze_second(config, promoted)
    form_reading(config)
    line = git_rev(config.repo_root, RELEASE_REF)

    answered = assessment.settle(
        config, settlement=assessment.SETTLEMENT_RETAIN, reason=KEPT, now=RESOLVED_AT
    )

    assert answered.recorded is True and answered.reversal is None
    assert answered.decision.settlement == assessment.SETTLEMENT_RETAIN
    assert answered.decision.rollback_revision is None
    read = read_reading(config, second)
    assert read.settled is True and read.decision is not None
    assert read.decision.reason == KEPT
    assert git_rev(config.repo_root, RELEASE_REF) == line
    assert (config.batches_root / FIRST / "rollback.json").exists() is False

    lines = ledger_records(config, assessment.RECORD_RELEASE_SETTLED)
    assert len(lines) == 1
    assert lines[0]["batch_id"] == SECOND
    assert lines[0]["experiment_id"] == promoted.experiment_id
    assert lines[0]["revision"] == promoted.promotion_revision
    assert lines[0]["detail"] == assessment.SETTLEMENT_RETAIN


def test_rolling_back_composes_the_reversal_rather_than_repeating_it(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The costly answer, end to end: the inverse commit lands first and the
    decision names it.

    The commit, the line and the record beside the promoted batch's outcome are
    the rollback operation's whole self — this settlement runs it and then states
    what it did, rather than spelling any of it a second time. What the record
    keeps saying is what the line held when the reading was taken: re-deriving
    that would make the reversal contradict the finding that caused it.
    """

    second = freeze_second(config, promoted)
    suspected(config, second)
    promotion = git_rev(config.repo_root, RELEASE_REF)

    answered = assessment.settle(
        config, settlement=assessment.SETTLEMENT_ROLLED_BACK, reason=REVERSAL, now=RESOLVED_AT
    )

    reversal = answered.reversal
    assert reversal is not None and reversal.reverted is True
    assert reversal.promotion_revision == promoted.promotion_revision
    assert git_rev(config.repo_root, RELEASE_REF) == reversal.revision
    assert answered.decision.rollback_revision == reversal.revision

    read = read_reading(config, second)
    assert read.decision is not None
    assert read.decision.settlement == assessment.SETTLEMENT_ROLLED_BACK
    assert read.decision.rollback_revision == reversal.revision
    assert read.subject.standing is True and read.subject.rollback_revision is None
    assert git_rev(config.repo_root, RELEASE_REF) != promotion

    assert [line["detail"] for line in ledger_records(config, assessment.RECORD_RELEASE_SETTLED)] == [
        assessment.SETTLEMENT_ROLLED_BACK
    ]
    assert len(ledger_records(config, rollback.RECORD_PROMOTION_ROLLED_BACK)) == 1


def test_a_reversal_made_outside_the_gate_is_adopted(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """A release already off the line is settled by naming the commit that took
    it off, not by making a second one.

    The operator may have reversed it themselves, and the reason on the rollback
    record is that operation's while the settlement's is the gate's — two
    records, two sentences, one commit.
    """

    second = freeze_second(config, promoted)
    suspected(config, second)
    reversal = rollback.rollback(config, reason=REVERSAL, now=REVERSED_AT)
    line = git_rev(config.repo_root, RELEASE_REF)

    answered = assessment.settle(
        config,
        settlement=assessment.SETTLEMENT_ROLLED_BACK,
        reason="the assessment agrees with the reversal that was already made",
        now=RESOLVED_AT,
    )

    assert answered.reversal is None
    assert answered.decision.rollback_revision == reversal.revision
    assert git_rev(config.repo_root, RELEASE_REF) == line
    recorded = json.loads((config.batches_root / FIRST / "rollback.json").read_text(encoding="utf-8"))
    assert recorded["reason"] == REVERSAL
    assert len(ledger_records(config, rollback.RECORD_PROMOTION_ROLLED_BACK)) == 1
    assert read_reading(config, second).settled is True


def test_a_release_already_off_the_line_is_not_retained(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """`retain` says the release stays the line the next base is frozen on, and
    that line now carries the reversal instead."""

    second = freeze_second(config, promoted)
    suspected(config, second)
    rollback.rollback(config, reason=REVERSAL, now=REVERSED_AT)

    with pytest.raises(evolution.BatchError) as error:
        assessment.settle(config, settlement=assessment.SETTLEMENT_RETAIN, reason=KEPT, now=RESOLVED_AT)
    assert "is no longer on" in str(error.value)
    assert read_reading(config, second).settled is False


def test_a_settlement_interrupted_after_the_reversal_is_finished_by_repeating_it(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reversal and the decision are two writes, so a run can stop between
    them: the release is off the line and nothing says why.

    Running the same settlement again finishes it, and finishes it by adopting
    the commit that is already there rather than making another — which is what
    keeps the redo from reversing a reversal.
    """

    second = freeze_second(config, promoted)
    suspected(config, second)

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt("stopped after the line moved and before the decision landed")

    monkeypatch.setattr(assessment, "_record_settlement", interrupted)
    with pytest.raises(KeyboardInterrupt):
        assessment.settle(
            config, settlement=assessment.SETTLEMENT_ROLLED_BACK, reason=REVERSAL, now=RESOLVED_AT
        )
    monkeypatch.undo()

    landed = json.loads((config.batches_root / FIRST / "rollback.json").read_text(encoding="utf-8"))
    assert landed["reverted_at"] is not None
    assert git_rev(config.repo_root, RELEASE_REF) == landed["revision"]
    assert read_reading(config, second).settled is False

    answered = assessment.settle(
        config, settlement=assessment.SETTLEMENT_ROLLED_BACK, reason=REVERSAL, now=RESOLVED_AT
    )

    assert answered.recorded is True and answered.reversal is None
    assert answered.decision.rollback_revision == landed["revision"]
    assert git_rev(config.repo_root, RELEASE_REF) == landed["revision"]
    assert len(ledger_records(config, rollback.RECORD_PROMOTION_ROLLED_BACK)) == 1


def test_a_settlement_finishes_a_rollback_that_was_only_prepared(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An inverse commit made and not landed leaves the promotion standing, so
    the gate is not settled `rolled-back` beside one.

    Composing the whole operation is what answers that: the rollback finishes the
    record it prepared — the same commit, now on the line — and the settlement
    names it afterwards. The alternative, reading the prepared revision off the
    record, would call an interrupted rollback a completed one and gate the next
    base freeze on a release still standing.
    """

    second = freeze_second(config, promoted)
    suspected(config, second)
    interrupt_before_the_line_moves(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        rollback.rollback(config, reason=REVERSAL, now=REVERSED_AT)
    prepared = json.loads((config.batches_root / FIRST / "rollback.json").read_text(encoding="utf-8"))
    assert prepared["reverted_at"] is None
    assert git_rev(config.repo_root, RELEASE_REF) == promoted.promotion_revision
    monkeypatch.undo()

    answered = assessment.settle(
        config, settlement=assessment.SETTLEMENT_ROLLED_BACK, reason=REVERSAL, now=RESOLVED_AT
    )

    assert answered.reversal is not None
    assert answered.reversal.revision == prepared["revision"]
    assert git_rev(config.repo_root, RELEASE_REF) == prepared["revision"]
    assert answered.decision.rollback_revision == prepared["revision"]
    assert read_reading(config, second).settled is True


def test_the_gate_answers_once(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The same answer run again reports what is on record and audits nothing
    twice; a different answer is refused, because what this gate releases is the
    base every experiment of the batch is frozen on."""

    second = freeze_second(config, promoted)
    form_reading(config)
    first = assessment.settle(
        config, settlement=assessment.SETTLEMENT_RETAIN, reason=KEPT, now=RESOLVED_AT
    )
    written = second.assessment_path.read_bytes()

    again = assessment.settle(
        config, settlement=assessment.SETTLEMENT_RETAIN, reason=KEPT, now=CONCLUDED_AT
    )
    assert again.recorded is False
    assert again.decision.decided_at == first.decision.decided_at
    assert second.assessment_path.read_bytes() == written
    assert len(ledger_records(config, assessment.RECORD_RELEASE_SETTLED)) == 1

    with pytest.raises(evolution.BatchError) as error:
        assessment.settle(
            config,
            settlement=assessment.SETTLEMENT_RETAIN,
            reason="a second sentence about the same decision",
            now=CONCLUDED_AT,
        )
    assert "answers once" in str(error.value)

    with pytest.raises(evolution.BatchError) as error:
        assessment.settle(
            config, settlement=assessment.SETTLEMENT_ROLLED_BACK, reason=REVERSAL, now=CONCLUDED_AT
        )
    assert "answers once" in str(error.value)
    # The refusal came before the reversal: the release is still on the line.
    assert (config.batches_root / FIRST / "rollback.json").exists() is False


def test_nothing_is_settled_over_evidence_still_in_flight(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """A run still going and a request the harness never answered for are both
    measurements that may still arrive, and nothing is added to a reading once
    its gate answers.

    Refused before the reversal rather than at the write, which is the whole
    point of asking here: a settlement that refused afterwards would have taken
    the release off the line for a decision nobody could record.
    """

    second = freeze_second(config, promoted)
    frame = assessment.describe(config, second)
    assert frame is not None
    run = counterfactual(frame)
    unsettled = dataclasses.replace(settled_reading(frame), decision=None)
    publish(second, dataclasses.replace(unsettled, counterfactual=dataclasses.replace(run, result=None)))

    with pytest.raises(evolution.BatchError) as error:
        assessment.settle(
            config, settlement=assessment.SETTLEMENT_ROLLED_BACK, reason=REVERSAL, now=RESOLVED_AT
        )
    assert "is still going" in str(error.value)
    assert (config.batches_root / FIRST / "rollback.json").exists() is False

    publish(
        second,
        dataclasses.replace(
            unsettled,
            requested=assessment.CounterfactualRequest(
                position=run.position,
                integration=run.integration,
                expectation=EXPECTATION,
                requested_at="2026-08-11T07:00:00Z",
            ),
        ),
    )
    with pytest.raises(evolution.BatchError) as error:
        assessment.settle(config, settlement=assessment.SETTLEMENT_RETAIN, reason=KEPT, now=RESOLVED_AT)
    assert "of its counterfactual outstanding" in str(error.value)
    assert read_reading(config, second).settled is False


def test_a_release_no_cohort_has_read_is_not_settled(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The gate answers a reading. A cohort that has taken none has nothing for a
    decision to be made from."""

    freeze_second(config, promoted)

    with pytest.raises(evolution.BatchError) as error:
        assessment.settle(config, settlement=assessment.SETTLEMENT_RETAIN, reason=KEPT, now=RESOLVED_AT)
    assert "has recorded no reading" in str(error.value)


def test_the_gate_takes_one_of_two_answers_and_a_reason(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """`retain` or `rolled-back`, and the operator's sentence for why — an
    `inconclusive` reading is an ordinary ground for either."""

    second = freeze_second(config, promoted)
    form_reading(config)

    with pytest.raises(evolution.BatchError) as error:
        assessment.settle(config, settlement="deferred", reason=KEPT, now=RESOLVED_AT)
    assert "is not an answer to a release assessment" in str(error.value)

    with pytest.raises(evolution.BatchError) as error:
        assessment.settle(config, settlement=assessment.SETTLEMENT_RETAIN, reason="  ", now=RESOLVED_AT)
    assert "a settlement records why" in str(error.value)
    assert read_reading(config, second).settled is False


def test_only_the_last_promotion_is_reversed_by_this_gate(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """A rollback takes the newest promotion off the line and no other, so a
    reading of an earlier release cannot be settled by running one.

    The state is a corrupted history — a later cohort promoted while this one is
    still current, which no freeze allows — and the refusal is here because of
    what would otherwise happen: a commit reversing somebody else's release,
    followed by a settlement the reader refuses for naming it.
    """

    second = freeze_second(config, promoted)
    form_reading(config)
    later = f"{THIRD}-exp-01"
    write_manifest(config.batches_root, THIRD, ["c1"], analysis_task_id=f"2026-08-11-{THIRD}")
    write_closure(config.batches_root, THIRD, analysis_task_id=f"2026-08-11-{THIRD}")
    write_experiment(
        config.experiments_root,
        later,
        batch_id=THIRD,
        rounds=[experiment_round(1, candidate_revision="c" * 40)],
        decision=experiment_decision("promoted", promotion_revision="f" * 40),
    )
    write_outcome(
        config.batches_root,
        THIRD,
        outcome="promoted",
        experiment_id=later,
        promotion_revision="f" * 40,
        reason="the approach needs a loader change this batch cannot justify",
    )

    with pytest.raises(evolution.BatchError) as error:
        assessment.settle(
            config, settlement=assessment.SETTLEMENT_ROLLED_BACK, reason=REVERSAL, now=RESOLVED_AT
        )
    assert "reverses the newest promotion" in str(error.value)
    assert (config.batches_root / FIRST / "rollback.json").exists() is False
    assert read_reading(config, second).settled is False


def test_no_base_is_frozen_before_the_release_is_judged(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """Invariant 17 from the other side. The first experiment of the assessing
    cohort freezes the commit every alternative in it starts from, and the two
    settlements leave two different commits on the line — so a freeze taken
    before the answer is the answer made by accident.

    Two refusals, because they are two different states for an operator: nobody
    has read the release, and nobody has decided what to do about the reading.
    """

    second = freeze_second(config, promoted)
    drafts = open_second(config, second)

    with pytest.raises(evolution.BatchError) as error:
        experiments.create(config, drafts, base=RELEASE_REF, now=RESOLVED_AT)
    assert "has recorded no reading" in str(error.value)

    form_reading(config)
    with pytest.raises(evolution.BatchError) as error:
        experiments.create(config, drafts, base=RELEASE_REF, now=RESOLVED_AT)
    assert "nobody has settled it" in str(error.value)
    assert lineage.describe(config).current is not None
    assert lineage.describe(config).current.experiments == ()

    assessment.settle(config, settlement=assessment.SETTLEMENT_RETAIN, reason=KEPT, now=RESOLVED_AT)
    admitted = experiments.create(config, drafts, base=RELEASE_REF, now=RESOLVED_AT)

    assert admitted.created is True
    assert admitted.base_revision == promoted.promotion_revision


def test_a_rolled_back_release_leaves_the_next_base_on_the_line_it_made(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The sequencing the gate exists for: the inverse commit lands first, and
    the base the first experiment then freezes is the line as the decision left
    it — not the promoted revision the batch was frozen beside."""

    second = freeze_second(config, promoted)
    suspected(config, second)
    drafts = open_second(config, second)

    answered = assessment.settle(
        config, settlement=assessment.SETTLEMENT_ROLLED_BACK, reason=REVERSAL, now=RESOLVED_AT
    )
    admitted = experiments.create(config, drafts, base=RELEASE_REF, now=RESOLVED_AT)

    assert answered.reversal is not None
    assert admitted.base_revision == answered.reversal.revision
    assert admitted.base_revision != promoted.promotion_revision


def test_a_batch_that_owes_no_reading_freezes_its_base_freely(
    config: evolution.EvolutionConfig,
    release: str,
) -> None:
    """A predecessor that concluded `no-change` fabricates no revision, so there
    is no release to judge and nothing for the freeze to wait on."""

    write_manifest(config.batches_root, FIRST, ["r1"], analysis_task_id="2026-07-31-first")
    write_outcome(config.batches_root, FIRST, outcome="no-change")
    write_manifest(config.batches_root, SECOND, [], analysis_task_id=f"2026-08-10-{SECOND}", reports=[
        make_manifest_report(key=f"a{index}", sequence=index, task_id=f"2026-08-0{index}-task", effective_revision=release)
        for index in (1, 2, 3)
    ])
    second = next(batch for batch in evolution.load_batches(config) if batch.batch_id == SECOND)
    drafts = open_second(config, second)

    admitted = experiments.create(config, drafts, base=RELEASE_REF, now=RESOLVED_AT)

    assert admitted.created is True
    assert assessment.describe(config, second) is None


def test_a_later_experiment_of_the_batch_is_not_gated_again(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The gate is asked where a base is being frozen and nowhere else.

    A batch has one base and its first experiment settled it (invariant 15), so a
    later attempt takes that commit rather than resolving one — asking again
    would hold work against a decision that can no longer change what it starts
    from. The reading is stripped back to unsettled here to prove the second
    admission never looks.
    """

    second = freeze_second(config, promoted)
    reading = suspected(config, second)
    drafts = open_second(config, second, "status-orphans", "prefetch-injection")

    assessment.settle(config, settlement=assessment.SETTLEMENT_RETAIN, reason=KEPT, now=RESOLVED_AT)
    first = experiments.create(config, [drafts[0]], base=RELEASE_REF, now=RESOLVED_AT)
    experiments.abandon(config, reason="the approach needs a loader change", now=RESOLVED_AT)
    publish(second, reading)
    assert read_reading(config, second).settled is False

    again = experiments.create(config, [drafts[1]], now=CONCLUDED_AT)

    assert again.created is True
    assert again.base_revision == first.base_revision


def test_a_rollback_a_later_attempt_stands_on_is_refused_and_retaining_stays_open(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The recorded way out of a silent base realignment.

    An attempt built on the release is work whose evidence describes a line
    carrying it, so the rollback operation refuses to take that line out from
    under it — and this gate does not weaken that refusal or spell a second one.
    What it leaves is the answer that can be recorded: `retain`, whose reason
    says why a release a later attempt was already built on stays on the line.
    Reversing it anyway means ending that lineage and using ordinary Git, which
    is not an operation here and so not one this gate can record as its own.

    The state itself is a repair case rather than a sequence the gate allows: in
    the ordinary flow the settlement lands before the first experiment can freeze
    a base, so nothing is standing on the release when a rollback would run.
    """

    second = freeze_second(config, promoted)
    reading = suspected(config, second)
    drafts = open_second(config, second)
    assessment.settle(config, settlement=assessment.SETTLEMENT_RETAIN, reason=KEPT, now=RESOLVED_AT)
    standing = experiments.create(config, drafts, base=RELEASE_REF, now=RESOLVED_AT)
    publish(second, reading)

    with pytest.raises(evolution.BatchError) as error:
        assessment.settle(
            config, settlement=assessment.SETTLEMENT_ROLLED_BACK, reason=REVERSAL, now=CONCLUDED_AT
        )
    assert standing.experiment_id in str(error.value)
    assert "in its history" in str(error.value)
    assert (config.batches_root / FIRST / "rollback.json").exists() is False
    assert git_rev(config.repo_root, RELEASE_REF) == promoted.promotion_revision

    kept = assessment.settle(
        config,
        settlement=assessment.SETTLEMENT_RETAIN,
        reason=f"{standing.experiment_id} is already built on the release",
        now=CONCLUDED_AT,
    )
    assert kept.recorded is True
    assert read_reading(config, second).settled is True


def ended_without_settling(
    config: evolution.EvolutionConfig,
    promotion: experiments.PromotionResult,
    *,
    reading: bool,
) -> lineage.Batch:
    """The assessing cohort ends its own batch without answering the gate.

    Legal at every step and reachable through the real operations: concluding
    `no-change` asks that nothing about this batch is still open, and an
    unanswered reading of the release *before* it is not about this batch at all.
    What it leaves is the state the next freeze has to find — the obligation
    still sits where it was, on a cohort that is no longer current.
    """

    (config.batches_root / SECOND / "findings.md").write_text("# Findings\n", encoding="utf-8")
    write_closure(config.batches_root, SECOND, analysis_task_id=f"2026-08-10-{SECOND}")
    if reading:
        form_reading(config)
    experiments.conclude_no_change(
        config, reason="the evidence justified no protocol change", now=RESOLVED_AT
    )
    return freeze_second(
        config,
        promotion,
        batch_id=THIRD,
        # Membership of its own: one report belongs to one batch (invariant 3).
        reports=[
            make_manifest_report(
                key=f"c{index}",
                sequence=index,
                task_id=f"2026-08-2{index}-task",
                effective_revision=promotion.promotion_revision,
            )
            for index in (1, 2, 3)
        ],
    )


def test_a_cohort_that_ended_unsettled_still_gates_the_next_base(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The obligation belongs to the first cohort after the release and does not
    lapse when that cohort ends.

    `no-change` promotes nothing, so the batch after it still follows the same
    release while owing no reading of its own. Asking only whether the *freezing*
    cohort owes one would find nothing to wait for and freeze a base on the line
    the source happened to be holding — which is invariant 17 defeated by a
    sequence every step of which is legal.

    The way out is the obligation itself: it stays answerable where it sits, so
    the gate names a decision somebody can still take rather than one nobody can.
    """

    second = freeze_second(config, promoted)
    third = ended_without_settling(config, promoted, reading=True)
    drafts = open_second(config, third)

    with pytest.raises(evolution.BatchError) as error:
        experiments.create(config, drafts, base=RELEASE_REF, now=CONCLUDED_AT)
    assert SECOND in str(error.value) and "nobody has settled it" in str(error.value)
    assert lineage.describe(config).current.experiments == ()

    answered = assessment.settle(
        config, settlement=assessment.SETTLEMENT_RETAIN, reason=KEPT, now=CONCLUDED_AT
    )
    admitted = experiments.create(config, drafts, base=RELEASE_REF, now=CONCLUDED_AT)

    # Recorded where the obligation is, not where the operator is standing.
    assert answered.batch_id == SECOND
    assert read_reading(config, second).settled is True
    assert third.assessment_path.exists() is False
    assert admitted.created is True and admitted.base_revision == promoted.promotion_revision


def test_a_cohort_that_ended_unread_still_gates_the_next_base(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The same rule one state earlier: the owing cohort recorded nothing at all.

    Two refusals rather than one, because they are two different things for an
    operator to do — read the release, then settle the reading — and both are
    still done by the cohort that owes them.
    """

    second = freeze_second(config, promoted)
    third = ended_without_settling(config, promoted, reading=False)
    drafts = open_second(config, third)

    with pytest.raises(evolution.BatchError) as error:
        experiments.create(config, drafts, base=RELEASE_REF, now=CONCLUDED_AT)
    assert SECOND in str(error.value) and "has recorded no reading" in str(error.value)

    formed = form_reading(config)
    assessment.settle(config, settlement=assessment.SETTLEMENT_RETAIN, reason=KEPT, now=CONCLUDED_AT)
    admitted = experiments.create(config, drafts, base=RELEASE_REF, now=CONCLUDED_AT)

    assert formed.assessment.batch_id == SECOND
    assert read_reading(config, second).settled is True
    assert third.assessment_path.exists() is False
    assert admitted.created is True


def test_a_settlement_taken_where_the_obligation_sits_is_redone_there_too(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """A carried-forward settlement is answerable more than once, like every
    other one.

    The redo is not a convenience: a caller that lost the response and a run
    interrupted between the record and its return are the same state, and the
    way out of it here is to ask again. Refusing the second ask because the
    first one succeeded would make the one settlement an operator cannot repeat
    the one they most need to, since it is taken from a cohort that is not even
    the record's own.
    """

    second = freeze_second(config, promoted)
    ended_without_settling(config, promoted, reading=True)

    first = assessment.settle(
        config, settlement=assessment.SETTLEMENT_RETAIN, reason=KEPT, now=RESOLVED_AT
    )
    written = second.assessment_path.read_bytes()

    again = assessment.settle(
        config, settlement=assessment.SETTLEMENT_RETAIN, reason=KEPT, now=CONCLUDED_AT
    )
    assert first.batch_id == SECOND and again.batch_id == SECOND
    assert again.recorded is False
    assert again.decision.decided_at == first.decision.decided_at
    assert second.assessment_path.read_bytes() == written
    assert len(ledger_records(config, assessment.RECORD_RELEASE_SETTLED)) == 1

    # The gate still answers once: a different answer is refused where it sits,
    # and refused before anything is reversed for it.
    with pytest.raises(evolution.BatchError) as error:
        assessment.settle(
            config, settlement=assessment.SETTLEMENT_ROLLED_BACK, reason=REVERSAL, now=CONCLUDED_AT
        )
    assert "answers once" in str(error.value)
    assert (config.batches_root / FIRST / "rollback.json").exists() is False


def test_an_answered_obligation_is_still_closed_to_everything_but_its_own_redo(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """Reaching a settled owner's record is the settlement's exception alone.

    What a redo does with a decision already on record is report it; what these
    would do is add to the reading that decision was made from. So the cohort
    that is merely standing where the obligation was answered is told whose
    reading it is, which is a different thing for an operator to hear than
    "settled" — their own cohort has recorded nothing at all.
    """

    second = freeze_second(config, promoted)
    third = ended_without_settling(config, promoted, reading=True)
    assessment.settle(config, settlement=assessment.SETTLEMENT_RETAIN, reason=KEPT, now=RESOLVED_AT)
    written = second.assessment_path.read_bytes()

    for act in (
        lambda: form_reading(config),
        lambda: assessment.measure(config, FakeHarness(), expectation=EXPECTATION, now=MEASURED_AT),
        lambda: assessment.conclude(config, FakeHarness(), now=MEASURED_AT),
        lambda: assessment.abandon(config, reason=DIED, now=MEASURED_AT),
        lambda: assessment.withdraw(config, now=MEASURED_AT),
        lambda: assessment.resolve(
            config,
            verdict=assessment.VERDICT_NEUTRAL,
            confidence=assessment.CONFIDENCE_LOW,
            rationale=INCONCLUSIVE_WHY,
            now=MEASURED_AT,
        ),
    ):
        with pytest.raises(evolution.BatchError) as error:
            act()
        assert f"the first batch frozen after that promotion is {SECOND}" in str(error.value)

    assert second.assessment_path.read_bytes() == written
    assert third.assessment_path.exists() is False


def test_nothing_else_writes_between_the_settlement_and_the_reversal_it_composes(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The composition holds one lock from the refusals to the record.

    The gap this closes is the one where a settlement checked the reading, let go
    of the lock to run the rollback, and came back to a reading somebody else had
    settled, measured or revised — with the inverse commit already on the source
    line for a decision it could no longer record. The lock is not reentrant, so
    the proof is that a writer trying to act while the reversal runs meets the
    lock rather than the reading.
    """

    second = freeze_second(config, promoted)
    suspected(config, second)
    reversing = assessment.reverse_promotion
    held: list[str] = []

    def watched(config_, **kwargs):  # type: ignore[no-untyped-def]
        with pytest.raises(evolution.LockError) as blocked:
            assessment.settle(
                config_, settlement=assessment.SETTLEMENT_RETAIN, reason=KEPT, now=CONCLUDED_AT
            )
        held.append(str(blocked.value))
        return reversing(config_, **kwargs)

    monkeypatch.setattr(assessment, "reverse_promotion", watched)
    answered = assessment.settle(
        config, settlement=assessment.SETTLEMENT_ROLLED_BACK, reason=REVERSAL, now=RESOLVED_AT
    )

    assert len(held) == 1 and "evolution lock held" in held[0]
    assert answered.reversal is not None
    read = read_reading(config, second)
    assert read.decision is not None
    assert read.decision.settlement == assessment.SETTLEMENT_ROLLED_BACK
    assert read.decision.rollback_revision == answered.reversal.revision


def test_a_reversal_that_refuses_leaves_the_line_and_the_reading_untouched(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every refusal from the preflight to the commit costs nothing.

    The settlement writes nothing before the reversal — not the decision, not the
    audit line — so a rollback that refuses leaves the release exactly as it was
    and the gate still open. The lock goes back too, which is what makes the
    refusal something an operator can answer rather than a repository to unstick.
    """

    second = freeze_second(config, promoted)
    suspected(config, second)
    line = git_rev(config.repo_root, RELEASE_REF)

    def refusing(config_, **kwargs):  # type: ignore[no-untyped-def]
        raise evolution.BatchError("the rollback refused after the settlement had checked the reading")

    monkeypatch.setattr(assessment, "reverse_promotion", refusing)
    with pytest.raises(evolution.BatchError, match="the rollback refused"):
        assessment.settle(
            config, settlement=assessment.SETTLEMENT_ROLLED_BACK, reason=REVERSAL, now=RESOLVED_AT
        )

    assert git_rev(config.repo_root, RELEASE_REF) == line
    assert (config.batches_root / FIRST / "rollback.json").exists() is False
    assert read_reading(config, second).settled is False
    assert ledger_records(config, assessment.RECORD_RELEASE_SETTLED) == []

    monkeypatch.undo()
    kept = assessment.settle(
        config, settlement=assessment.SETTLEMENT_RETAIN, reason=KEPT, now=CONCLUDED_AT
    )
    assert kept.recorded is True


def test_a_retained_release_is_not_frozen_out_of_the_base(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """What the settlement selects is a commit, not merely an answer.

    `retain` says the alternatives are built on the line carrying the release, so
    a base at the revision the line stood at *before* it is the decision undone
    by the freeze — the accident invariant 17 exists to stop, arriving one step
    later than the gate that only asks whether somebody answered.
    """

    second = freeze_second(config, promoted)
    form_reading(config)
    drafts = open_second(config, second)
    assessment.settle(config, settlement=assessment.SETTLEMENT_RETAIN, reason=KEPT, now=RESOLVED_AT)

    with pytest.raises(evolution.BatchError) as error:
        experiments.create(config, drafts, base=promoted.merge_input_revision, now=RESOLVED_AT)

    assert "does not carry" in str(error.value)
    assert promoted.promotion_revision[:12] in str(error.value)
    assert lineage.describe(config).current.experiments == ()


def test_a_reversed_release_is_not_the_base_it_was_taken_off(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """The same rule the other way round: `rolled-back` puts the inverse commit
    on the line, and the promoted revision the batch was frozen beside is the one
    commit the decision ruled out."""

    second = freeze_second(config, promoted)
    suspected(config, second)
    drafts = open_second(config, second)
    answered = assessment.settle(
        config, settlement=assessment.SETTLEMENT_ROLLED_BACK, reason=REVERSAL, now=RESOLVED_AT
    )
    assert answered.reversal is not None

    with pytest.raises(evolution.BatchError) as error:
        experiments.create(config, drafts, base=promoted.promotion_revision, now=RESOLVED_AT)
    assert "does not carry" in str(error.value)
    assert answered.reversal.revision[:12] in str(error.value)

    admitted = experiments.create(config, drafts, base=RELEASE_REF, now=RESOLVED_AT)
    assert admitted.base_revision == answered.reversal.revision


def test_a_rollback_after_a_retained_release_does_not_realign_the_base(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
) -> None:
    """A reversal nobody settled does not become the base by standing on the line.

    The rollback operation is available on its own, and after a `retain` nothing
    is yet built on the release to stop it. What must not follow is a freeze that
    quietly takes the reversed line: the recorded decision says the release
    stays, so a base carrying the inverse commit is a realignment no reading
    justified.
    """

    second = freeze_second(config, promoted)
    form_reading(config)
    drafts = open_second(config, second)
    assessment.settle(config, settlement=assessment.SETTLEMENT_RETAIN, reason=KEPT, now=RESOLVED_AT)
    reversal = rollback.rollback(config, reason="reversed outside the gate", now=RESOLVED_AT)

    with pytest.raises(evolution.BatchError) as error:
        experiments.create(config, drafts, base=RELEASE_REF, now=CONCLUDED_AT)

    assert reversal.revision[:12] in str(error.value)
    assert "carries" in str(error.value) and "has not taken the inverse commit" in str(error.value)
    assert read_reading(config, second).decision.settlement == assessment.SETTLEMENT_RETAIN


def test_a_base_this_checkout_cannot_place_on_the_settled_line_is_refused(
    config: evolution.EvolutionConfig,
    release: str,
) -> None:
    """The one Git question here answered by refusing.

    Elsewhere a relation this clone cannot resolve is reported as the fact about
    the clone that it is. This one stands between a frozen base and every
    alternative built on it, so a checkout that cannot see whether the base
    carries the decided line does not get to assume it does.
    """

    build_absent_release(config)
    # Its own analysis id rather than `open_second`'s: this batch's manifest was
    # written by a helper that names the task differently, and a closure naming
    # another one is refused before anything here is reached.
    (config.batches_root / SECOND / "findings.md").write_text("# Findings\n", encoding="utf-8")
    write_closure(config.batches_root, SECOND, analysis_task_id="2026-08-10-second")
    write_draft(config.batches_root, SECOND, "status-orphans")
    form_reading(config)
    assessment.settle(config, settlement=assessment.SETTLEMENT_RETAIN, reason=KEPT, now=RESOLVED_AT)

    with pytest.raises(evolution.BatchError) as error:
        experiments.create(config, ["status-orphans"], base=RELEASE_REF, now=RESOLVED_AT)

    assert "cannot be answered in this checkout" in str(error.value)
    assert lineage.describe(config).current.experiments == ()


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


def test_the_task_says_a_cohort_the_feed_cannot_place_carries_no_direction(
    config: evolution.EvolutionConfig,
    promoted: experiments.PromotionResult,
    tmp_path: Path,
) -> None:
    """The reading is taken by whoever opens this task, so which reports the feed
    can place has to be met there rather than in the contract alone: a report with
    no effective revision is excluded whole, a report imported before orch-hub
    published the identity is one and stays one, and the counterfactual is then
    the only directional instrument. Without it a session finds empty cohorts and
    no reason for them.
    """

    fill_pool(config, tmp_path / "feed")
    result = evolution.freeze(config, now=FROZEN_AT, runner_revision="v2.2.0")

    assert result.analysis_task_id is not None
    text = (config.repo_root / ".ai-tasks" / f"{result.analysis_task_id}.md").read_text(encoding="utf-8")
    assert "publishes the revision with its payload digest" in text
    assert "every report imported before that publication" in text
    assert "offers no direction in either sign" in text
    assert "the pinned counterfactual below is" in text
    assert "the only directional instrument available" in text


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
