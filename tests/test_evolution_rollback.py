"""Taking a promotion back off the source line.

Everything here runs against a real Git repository with a real promotion on it,
for the reason the promotion suite does: what a rollback produces is a commit,
and a fixture standing in for it would prove only that the package agrees with
itself about the one part that leaves this repository.

Two properties get most of the attention, because they are what "roll back
without rewriting history" actually means:

- **Nothing is removed.** The promotion commit, its records, and every commit
  that landed after it are all still there afterwards; what changes is that a
  later commit takes the change back out.
- **An interrupted rollback is finishable.** The inverse commit is recorded
  before the line moves, so a second run finds its own work — by the commit it
  recorded, never by a shape a hand-made revert shares.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from evolution_fixtures import (
    RELEASE_REF,
    experiment_round,
    git_delete_ref,
    git_file,
    git_file_commit,
    git_repo,
    git_rev,
    git_sibling_commit,
    git_tree,
    git_try_update_ref,
    git_update_ref,
    git_worktree,
    make_manifest_report,
    make_repo,
    promote_candidate,
    settle_release,
    write_closure,
    write_draft,
    write_experiment,
    write_manifest,
    write_rollback,
)

from ai_native_deployment import evolution
from ai_native_deployment.evolution import experiments, lineage, phase, render, revisions, rollback

BATCH_ID = "evolution-batch-0001"
SECOND_BATCH = "evolution-batch-0002"
EXP_01 = f"{BATCH_ID}-exp-01"

PROMOTED = datetime(2026, 8, 8, 9, 0, 0, tzinfo=timezone.utc)
REVERSED = datetime(2026, 8, 9, 9, 0, 0, tzinfo=timezone.utc)
REVERSED_AT = "2026-08-09T09:00:00Z"
LATER = datetime(2026, 8, 10, 9, 0, 0, tzinfo=timezone.utc)
LATER_AT = "2026-08-10T09:00:00Z"
WHY = "the next cohort showed no improvement and two new regressions"


@pytest.fixture
def config(tmp_path: Path) -> evolution.EvolutionConfig:
    return evolution.load_config(git_repo(make_repo(tmp_path), tag="v2.2.0"))


@pytest.fixture
def release(config: evolution.EvolutionConfig) -> str:
    """The source line, standing where it did before anything was promoted."""

    sha = git_rev(config.repo_root, "HEAD")
    git_update_ref(config.repo_root, RELEASE_REF, sha)
    return sha


@pytest.fixture
def promoted(config: evolution.EvolutionConfig, release: str) -> experiments.PromotionResult:
    """One whole change cycle, ending with a merge commit on the release line."""

    return promote_candidate(config, batch_id=BATCH_ID, at=PROMOTED)


def rollback_record(config: evolution.EvolutionConfig, batch_id: str = BATCH_ID) -> dict:
    return json.loads((config.batches_root / batch_id / "rollback.json").read_text(encoding="utf-8"))


def ledger_types(config: evolution.EvolutionConfig) -> list[str]:
    return [record["record_type"] for record in evolution.read_records(config)]


def outcome_bytes(config: evolution.EvolutionConfig, batch_id: str = BATCH_ID) -> bytes:
    return (config.batches_root / batch_id / "outcome.json").read_bytes()


def experiment_bytes(config: evolution.EvolutionConfig, experiment_id: str = EXP_01) -> bytes:
    return (config.experiments_root / experiment_id / "experiment.json").read_bytes()


# --- what a rollback puts on the line ----------------------------------------


def test_rolling_back_puts_an_inverse_commit_on_the_source_line(
    config: evolution.EvolutionConfig, release: str, promoted: experiments.PromotionResult
) -> None:
    """The line ends up with the content it had before the promotion, carried
    there by a new commit whose parent is the promotion — not by the promotion
    being taken off."""

    result = rollback.rollback(config, reason=WHY, now=REVERSED)

    assert result.reverted is True and result.recorded is True
    assert result.revision == git_rev(config.repo_root, RELEASE_REF)
    assert result.revision not in (promoted.promotion_revision, release)
    assert result.reverted_from == promoted.promotion_revision
    assert revisions.commit_shape(config.repo_root, result.revision) == (
        git_tree(config.repo_root, release),
        (promoted.promotion_revision,),
    )
    assert result.tree == git_tree(config.repo_root, release)
    assert result.promotion_revision == promoted.promotion_revision
    assert result.source_ref == RELEASE_REF


def test_the_promotion_it_reverses_is_left_exactly_where_it_was(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """Invariant: a rollback adds history. The promotion stays on the line, its
    records are not edited, and the batch it ended stays ended — what changes is
    that the line no longer carries the change."""

    outcome = outcome_bytes(config)
    record = experiment_bytes(config)

    rollback.rollback(config, reason=WHY, now=REVERSED)

    assert outcome_bytes(config) == outcome
    assert experiment_bytes(config) == record
    assert revisions.contains(config.repo_root, promoted.promotion_revision, RELEASE_REF) is True
    assert lineage.current_batch(config) is None


def test_the_rollback_is_recorded_beside_the_outcome_it_reverses(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    result = rollback.rollback(config, reason=WHY, now=REVERSED)

    assert rollback_record(config) == {
        "schema_version": 1,
        "batch_id": BATCH_ID,
        "experiment_id": EXP_01,
        "promotion_revision": promoted.promotion_revision,
        "source_ref": RELEASE_REF,
        "reverted_from": promoted.promotion_revision,
        "revision": result.revision,
        "tree": result.tree,
        "reason": WHY,
        "prepared_at": REVERSED_AT,
        "reverted_at": REVERSED_AT,
    }
    assert result.record_path == config.batches_root / BATCH_ID / "rollback.json"
    assert ledger_types(config)[-1] == "promotion-rolled-back"
    audit = evolution.read_records(config)[-1]
    assert audit["batch_id"] == BATCH_ID and audit["experiment_id"] == EXP_01
    # The commit this event created, the way `experiment-promoted` names the one
    # its own event put on the line.
    assert audit["revision"] == result.revision


def test_a_commit_that_landed_after_the_promotion_is_kept(
    config: evolution.EvolutionConfig, release: str, promoted: experiments.PromotionResult
) -> None:
    """The whole reason the inverse is computed against the line as it stands
    rather than pinned to the tree the line had before: reverting is not
    rewinding, and a rollback that dropped what came after would be exactly the
    history rewrite this operation exists to avoid."""

    later = git_file_commit(
        config.repo_root, promoted.promotion_revision, "unrelated.txt", "later work\n", "meanwhile"
    )
    git_update_ref(config.repo_root, RELEASE_REF, later)

    result = rollback.rollback(config, reason=WHY, now=REVERSED)

    assert result.reverted_from == later
    assert revisions.commit_shape(config.repo_root, result.revision)[1] == (later,)
    assert revisions.contains(config.repo_root, later, RELEASE_REF) is True
    # The line keeps what arrived after the promotion and gives back what the
    # promotion carried, which is the difference between a revert and a reset.
    assert git_file(config.repo_root, result.revision, "unrelated.txt") == "later work\n"
    assert git_file(config.repo_root, result.revision, "file.txt") == git_file(
        config.repo_root, release, "file.txt"
    )
    assert result.tree != git_tree(config.repo_root, later)


def test_the_status_reports_the_promotion_and_its_reversal(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """Both are true and both are reported: the commit was promoted, and a later
    commit took the change back out. A status that dropped the promotion would
    lose the revision the next cohort's reports were produced at."""

    result = rollback.rollback(config, reason=WHY, now=REVERSED)

    status = phase.describe(config, now=LATER)
    assert status.last_promotion is not None
    assert status.last_promotion.revision == promoted.promotion_revision
    assert status.last_promotion.rollback is not None
    assert status.last_promotion.rollback.revision == result.revision
    assert status.last_promotion.rollback.reverted_at == REVERSED_AT
    assert status.to_json()["last_promotion"]["rollback"] == {
        "revision": result.revision,
        "reverted_from": promoted.promotion_revision,
        "reverted_at": REVERSED_AT,
        "reason": WHY,
    }
    text = render.format_status(status)
    assert f"rolled back by {result.revision[:12]} at {REVERSED_AT}" in text
    assert WHY in text


def test_a_promotion_nobody_reversed_reports_no_rollback(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    status = phase.describe(config, now=LATER)
    assert status.last_promotion is not None and status.last_promotion.rollback is None
    assert status.to_json()["last_promotion"]["rollback"] is None
    assert "rolled back" not in render.format_status(status)


# --- running it again --------------------------------------------------------


def test_the_same_rollback_run_again_reports_the_one_on_record(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """A promotion is reversed once. The retry reports what is on record rather
    than putting a second inverse commit on the line."""

    first = rollback.rollback(config, reason=WHY, now=REVERSED)
    tip = git_rev(config.repo_root, RELEASE_REF)

    again = rollback.rollback(config, reason=WHY, now=LATER)

    assert again.recorded is False and again.reverted is False
    assert again.revision == first.revision
    assert again.reverted_at == REVERSED_AT
    assert git_rev(config.repo_root, RELEASE_REF) == tip
    assert ledger_types(config).count("promotion-rolled-back") == 1


def test_a_second_reason_is_not_a_second_reversal(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    rollback.rollback(config, reason=WHY, now=REVERSED)
    tip = git_rev(config.repo_root, RELEASE_REF)

    with pytest.raises(evolution.BatchError, match="was already rolled back"):
        rollback.rollback(config, reason="on reflection, something else", now=LATER)

    assert git_rev(config.repo_root, RELEASE_REF) == tip


def test_rolling_back_records_why(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    with pytest.raises(evolution.BatchError, match="a rollback records why"):
        rollback.rollback(config, reason="   ", now=REVERSED)

    assert git_rev(config.repo_root, RELEASE_REF) == promoted.promotion_revision


# --- a rollback whose records did not land -----------------------------------


def stop_after(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Let this operation do its work up to `name`, then die the way a killed
    process does — the durable state is whatever landed before it."""

    original = getattr(rollback, name)

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt(f"stopped before {name}")

    assert original is not None
    monkeypatch.setattr(rollback, name, interrupted)


def test_an_inverse_the_line_never_took_is_finished_by_the_next_run(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record lands before the ref moves, so the interruption in between
    leaves a commit this controller can still name. The next run moves that same
    commit onto the line rather than making a second one."""

    stop_after(monkeypatch, "_land")
    with pytest.raises(KeyboardInterrupt):
        rollback.rollback(config, reason=WHY, now=REVERSED)

    prepared = rollback_record(config)
    assert prepared["reverted_at"] is None
    assert git_rev(config.repo_root, RELEASE_REF) == promoted.promotion_revision

    monkeypatch.undo()
    result = rollback.rollback(config, reason=WHY, now=LATER)

    assert result.revision == prepared["revision"]
    assert result.reverted is True and result.recorded is True
    assert git_rev(config.repo_root, RELEASE_REF) == prepared["revision"]
    assert rollback_record(config)["reverted_at"] == LATER_AT
    assert rollback_record(config)["prepared_at"] == REVERSED_AT


def test_a_rollback_the_line_took_and_nothing_recorded_is_finished_by_the_next_run(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other side of the same window: the move landed and the record saying
    so did not. The next run recognises its own commit on the line and writes
    what is missing rather than reversing anything again."""

    stop_after(monkeypatch, "_finish")
    with pytest.raises(KeyboardInterrupt):
        rollback.rollback(config, reason=WHY, now=REVERSED)

    prepared = rollback_record(config)
    assert prepared["reverted_at"] is None
    assert git_rev(config.repo_root, RELEASE_REF) == prepared["revision"]

    monkeypatch.undo()
    result = rollback.rollback(config, reason=WHY, now=LATER)

    assert result.reverted is False and result.recorded is True
    assert result.revision == prepared["revision"]
    assert git_rev(config.repo_root, RELEASE_REF) == prepared["revision"]
    assert rollback_record(config)["reverted_at"] == LATER_AT


def test_an_inverse_still_recognised_after_the_line_moved_on(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A line that took further commits after the rollback still carries it, so
    the identity is ancestry and not the tip — a run that could not see that
    would reverse a promotion twice."""

    stop_after(monkeypatch, "_finish")
    with pytest.raises(KeyboardInterrupt):
        rollback.rollback(config, reason=WHY, now=REVERSED)

    prepared = rollback_record(config)
    after = git_file_commit(
        config.repo_root, prepared["revision"], "unrelated.txt", "later work\n", "meanwhile"
    )
    git_update_ref(config.repo_root, RELEASE_REF, after)

    monkeypatch.undo()
    result = rollback.rollback(config, reason=WHY, now=LATER)

    assert result.reverted is False and result.revision == prepared["revision"]
    assert git_rev(config.repo_root, RELEASE_REF) == after


def test_an_inverse_the_line_left_behind_is_made_again(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Where this differs from a prepared promotion, which is discarded and
    refused: a rollback's content is computed from the line as it stands, so a
    line that moved before the inverse reached it is answered by making the
    inverse again — against the new tip, keeping what arrived."""

    stop_after(monkeypatch, "_land")
    with pytest.raises(KeyboardInterrupt):
        rollback.rollback(config, reason=WHY, now=REVERSED)
    prepared = rollback_record(config)

    later = git_file_commit(
        config.repo_root, promoted.promotion_revision, "unrelated.txt", "later work\n", "meanwhile"
    )
    git_update_ref(config.repo_root, RELEASE_REF, later)

    monkeypatch.undo()
    result = rollback.rollback(config, reason=WHY, now=LATER)

    assert result.revision != prepared["revision"]
    assert result.reverted_from == later
    assert rollback_record(config)["revision"] == result.revision
    assert git_rev(config.repo_root, RELEASE_REF) == result.revision


def test_a_line_that_moved_before_the_inverse_landed_drops_nothing(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The race the compare-and-swap exists for: a commit arriving between the
    tip being read and the inverse being put on it. The inverse was computed
    without that commit, so landing it would drop the commit — the move refuses
    instead, and the next run makes the inverse again against the line as it now
    stands, keeping what arrived."""

    made = revisions.commit_tree

    def interleaved(repo_root, tree, parents, message):
        result = made(repo_root, tree, parents, message)
        git_update_ref(
            config.repo_root,
            RELEASE_REF,
            git_file_commit(
                config.repo_root, promoted.promotion_revision, "unrelated.txt", "meanwhile\n", "meanwhile"
            ),
        )
        return result

    monkeypatch.setattr(rollback, "commit_tree", interleaved)
    with pytest.raises(evolution.BatchError, match="did not take"):
        rollback.rollback(config, reason=WHY, now=REVERSED)
    interrupted = git_rev(config.repo_root, RELEASE_REF)
    assert rollback_record(config)["reverted_at"] is None

    monkeypatch.undo()
    result = rollback.rollback(config, reason=WHY, now=LATER)

    assert result.reverted_from == interrupted
    assert git_file(config.repo_root, result.revision, "unrelated.txt") == "meanwhile\n"


def test_a_line_reset_after_the_move_leaves_the_rollback_in_flight(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gap the completion is recorded across: the inverse is put on the line,
    and before the record says so an ordinary Git updater resets the line back to
    the promotion. Writing the moment from that expired reading would leave a
    finished rollback beside a line still carrying the change — which is what
    every later reader takes for the promotion no longer standing."""

    land = rollback._land

    def interleaved(*args, **kwargs):
        land(*args, **kwargs)
        git_update_ref(config.repo_root, RELEASE_REF, promoted.promotion_revision)

    monkeypatch.setattr(rollback, "_land", interleaved)
    with pytest.raises(evolution.BatchError, match="a finished rollback says the source line carries"):
        rollback.rollback(config, reason=WHY, now=REVERSED)

    assert rollback_record(config)["reverted_at"] is None
    assert git_rev(config.repo_root, RELEASE_REF) == promoted.promotion_revision
    assert "promotion-rolled-back" not in ledger_types(config)

    # In flight rather than lost: the line stands where the inverse was made
    # from, so the next run puts the same commit on it and finishes.
    monkeypatch.undo()
    result = rollback.rollback(config, reason=WHY, now=LATER)

    assert result.reverted is True and result.revision == rollback_record(config)["revision"]
    assert git_rev(config.repo_root, RELEASE_REF) == result.revision
    assert rollback_record(config)["reverted_at"] == LATER_AT


def test_a_line_reset_before_an_interrupted_rollback_is_finished_leaves_it_in_flight(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same gap on the recovery path, where the move happened in an earlier
    run: this one only recognises its own commit on the line, and the reading it
    would record the completion from expires exactly as fast."""

    stop_after(monkeypatch, "_finish")
    with pytest.raises(KeyboardInterrupt):
        rollback.rollback(config, reason=WHY, now=REVERSED)
    prepared = rollback_record(config)
    monkeypatch.undo()

    standing = rollback._standing

    def interleaved(*args, **kwargs):
        answer = standing(*args, **kwargs)
        git_update_ref(config.repo_root, RELEASE_REF, promoted.promotion_revision)
        return answer

    monkeypatch.setattr(rollback, "_standing", interleaved)
    with pytest.raises(evolution.BatchError, match="a finished rollback says the source line carries"):
        rollback.rollback(config, reason=WHY, now=LATER)

    assert rollback_record(config)["reverted_at"] is None
    assert git_rev(config.repo_root, RELEASE_REF) == promoted.promotion_revision

    monkeypatch.undo()
    result = rollback.rollback(config, reason=WHY, now=LATER)

    assert result.revision == prepared["revision"]
    assert git_rev(config.repo_root, RELEASE_REF) == result.revision
    assert rollback_record(config)["reverted_at"] == LATER_AT


def test_nothing_outside_can_move_the_line_while_the_reversal_is_recorded(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other side of that guarantee: while the completion is being written,
    Git itself refuses the update that would open the gap."""

    finish = rollback._finish
    refused: list[str | None] = []

    def probe(*args, **kwargs):
        refused.append(git_try_update_ref(config.repo_root, RELEASE_REF, promoted.promotion_revision))
        return finish(*args, **kwargs)

    monkeypatch.setattr(rollback, "_finish", probe)
    result = rollback.rollback(config, reason=WHY, now=REVERSED)

    assert refused[0] is not None and "cannot lock ref" in refused[0]
    assert result.reverted is True and result.recorded is True
    assert git_rev(config.repo_root, RELEASE_REF) == result.revision
    assert rollback_record(config)["reverted_at"] == REVERSED_AT


def test_the_line_is_let_go_once_the_rollback_is_recorded(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """Held for the write, not beyond it. What the record says about that line is
    said, and what happens to it afterwards — a later commit, a fetch — is
    nobody's finding here."""

    result = rollback.rollback(config, reason=WHY, now=REVERSED)

    later = git_file_commit(config.repo_root, result.revision, "unrelated.txt", "later work\n", "meanwhile")
    assert git_try_update_ref(config.repo_root, RELEASE_REF, later) is None


def test_a_prepared_rollback_is_finished_as_it_was_prepared(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    """That commit may already be on the source line, so a second run naming a
    different reason is asking for a different rollback of a promotion that
    already has one on the way."""

    stop_after(monkeypatch, "_land")
    with pytest.raises(KeyboardInterrupt):
        rollback.rollback(config, reason=WHY, now=REVERSED)

    monkeypatch.undo()
    with pytest.raises(evolution.BatchError, match="prepared once and finished as it was prepared"):
        rollback.rollback(config, reason="something else entirely", now=LATER)

    assert git_rev(config.repo_root, RELEASE_REF) == promoted.promotion_revision


# --- a record that is not the promotion's inverse ------------------------------


def written_rollback(
    config: evolution.EvolutionConfig,
    *,
    promotion_revision: str,
    reverted_from: str,
    revision: str,
    reverted_at: str | None = None,
) -> Path:
    """A schema-valid rollback record of this batch's promotion, stating the
    commit's own tree so the record and the commit agree with each other.

    `reverted_at` of None is the state a run acts on by moving the ref; a
    timestamp is the record every later reading takes for the promotion no longer
    standing."""

    return write_rollback(
        config.batches_root,
        BATCH_ID,
        experiment_id=EXP_01,
        promotion_revision=promotion_revision,
        source_ref=RELEASE_REF,
        reverted_from=reverted_from,
        revision=revision,
        tree=git_tree(config.repo_root, revision),
        reverted_at=reverted_at,
    )


@pytest.mark.parametrize("reverted_at", [None, REVERSED_AT], ids=["in-flight", "finished"])
def test_a_commit_that_reverses_nothing_is_not_this_promotions_inverse(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult, reverted_at: str | None
) -> None:
    """The record agrees with its own commit — that commit carries that tree and
    was made from that revision — and agreeing with itself proves nothing about
    the promotion. Without the content being recomputed, any one-parent commit
    written into this file is a revision the next run puts on the canonical source
    line, and a finished one is a promotion retired while the line still carries
    it."""

    impostor = git_file_commit(
        config.repo_root, promoted.promotion_revision, "unrelated.txt", "not a revert\n", "unrelated work"
    )
    written_rollback(
        config,
        promotion_revision=promoted.promotion_revision,
        reverted_from=promoted.promotion_revision,
        revision=impostor,
        reverted_at=reverted_at,
    )

    with pytest.raises(evolution.BatchError, match="reverting it here produces"):
        lineage.describe(config)
    with pytest.raises(evolution.BatchError, match="reverting it here produces"):
        rollback.rollback(config, reason=WHY, now=LATER)

    assert git_rev(config.repo_root, RELEASE_REF) == promoted.promotion_revision


@pytest.mark.parametrize("reverted_at", [None, REVERSED_AT], ids=["in-flight", "finished"])
def test_a_revert_that_conflicts_is_an_answer_about_the_record(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult, reverted_at: str | None
) -> None:
    """Git saying these three commits have no clean revert is a fact about the
    record, true in every checkout holding them — not a question this one could
    not put. Read as the second, the record loads: a finished one retires the
    promotion and the run that would have refused never gets to, reporting it as
    already rolled back, while an in-flight one is left to be caught by whichever
    checkout happens to act on it."""

    conflicting = git_sibling_commit(
        config.repo_root, promoted.promotion_revision, "something else entirely\n", "rewrite the file"
    )
    impostor = git_file_commit(config.repo_root, conflicting, "unrelated.txt", "not a revert\n", "unrelated work")
    written_rollback(
        config,
        promotion_revision=promoted.promotion_revision,
        reverted_from=conflicting,
        revision=impostor,
        reverted_at=reverted_at,
    )

    with pytest.raises(evolution.BatchError, match="conflicts"):
        lineage.describe(config)
    with pytest.raises(evolution.BatchError, match="conflicts"):
        rollback.rollback(config, reason=WHY, now=LATER)

    assert git_rev(config.repo_root, RELEASE_REF) == promoted.promotion_revision


@pytest.mark.parametrize("reverted_at", [None, REVERSED_AT], ids=["in-flight", "finished"])
def test_a_commit_that_takes_nothing_back_out_is_not_this_promotions_inverse(
    config: evolution.EvolutionConfig,
    release: str,
    promoted: experiments.PromotionResult,
    reverted_at: str | None,
) -> None:
    """The refusal the writer makes, asked of a record it did not write. Once the
    promotion has been taken off the line by hand, reverting it out of that line
    genuinely produces the line's own tree — so a no-op commit passes both of the
    other questions, and without this one it is a revision the run puts on the
    canonical line while claiming somebody else's reversal as its own work."""

    unpromoted = git_file(config.repo_root, release, "file.txt")
    by_hand = git_file_commit(
        config.repo_root, promoted.promotion_revision, "file.txt", unpromoted, "revert the promotion, by hand"
    )
    git_update_ref(config.repo_root, RELEASE_REF, by_hand)
    no_op = git_file_commit(config.repo_root, by_hand, "file.txt", unpromoted, "a commit that changes nothing")
    assert git_tree(config.repo_root, no_op) == git_tree(config.repo_root, by_hand)

    written_rollback(
        config,
        promotion_revision=promoted.promotion_revision,
        reverted_from=by_hand,
        revision=no_op,
        reverted_at=reverted_at,
    )

    with pytest.raises(evolution.BatchError, match="already carries none of the promotion"):
        lineage.describe(config)
    with pytest.raises(evolution.BatchError, match="already carries none of the promotion"):
        rollback.rollback(config, reason=WHY, now=LATER)

    assert git_rev(config.repo_root, RELEASE_REF) == by_hand


def test_an_inverse_made_from_a_line_without_the_promotion_is_refused(
    config: evolution.EvolutionConfig, release: str, promoted: experiments.PromotionResult
) -> None:
    """The other half of the relationship: reverting is computed from the line as
    it stood *carrying* the change, so a record made from a revision that never
    had the promotion describes taking back something that was never there."""

    elsewhere = git_file_commit(config.repo_root, release, "unrelated.txt", "elsewhere\n", "another line")
    written_rollback(
        config,
        promotion_revision=promoted.promotion_revision,
        reverted_from=release,
        revision=elsewhere,
    )

    with pytest.raises(evolution.BatchError, match="does not have that promotion in its history"):
        lineage.describe(config)

    assert git_rev(config.repo_root, RELEASE_REF) == promoted.promotion_revision


def test_a_rollback_this_checkout_cannot_recompute_moves_nothing(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Where the reader and the operation part. A checkout that cannot recompute
    the revert — a clone holding neither side of it — reads the record and says
    so, because a history that stopped loading on a shallow clone would take every
    other reading with it. The run about to move the source line to that record's
    commit refuses instead: this is the one place the wrong answer is a revision
    nobody checked reaching the canonical line."""

    stop_after(monkeypatch, "_land")
    with pytest.raises(KeyboardInterrupt):
        rollback.rollback(config, reason=WHY, now=REVERSED)
    prepared = rollback_record(config)
    monkeypatch.undo()

    monkeypatch.setattr(
        lineage,
        "revert_tree",
        lambda *args, **kwargs: revisions.MergeAnswer(None, "the objects are not here"),
    )

    latest = lineage.describe(config).last_promoted
    assert latest is not None and latest.rollback is not None

    with pytest.raises(evolution.BatchError, match="cannot be settled here"):
        rollback.rollback(config, reason=WHY, now=LATER)

    assert git_rev(config.repo_root, RELEASE_REF) == promoted.promotion_revision
    assert rollback_record(config) == prepared

    # And with the checkout able to answer again, the same record finishes.
    monkeypatch.undo()
    result = rollback.rollback(config, reason=WHY, now=LATER)

    assert result.revision == prepared["revision"]
    assert git_rev(config.repo_root, RELEASE_REF) == result.revision


def test_a_line_this_checkout_cannot_describe_settles_no_inverse_either(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same parting, for the last of the three questions. Whether the inverse
    takes anything back out is asked of the commit it was made from, and a
    checkout that cannot describe that commit has not answered it — so this is
    reported unchecked rather than passed over, which would hand the run a record
    confirmed by a question nobody put."""

    stop_after(monkeypatch, "_land")
    with pytest.raises(KeyboardInterrupt):
        rollback.rollback(config, reason=WHY, now=REVERSED)
    prepared = rollback_record(config)
    monkeypatch.undo()

    monkeypatch.setattr(lineage, "commit_shape", lambda *args, **kwargs: None)

    latest = lineage.describe(config).last_promoted
    assert latest is not None and latest.rollback is not None

    with pytest.raises(evolution.BatchError, match="cannot be settled here"):
        rollback.rollback(config, reason=WHY, now=LATER)

    assert git_rev(config.repo_root, RELEASE_REF) == promoted.promotion_revision
    assert rollback_record(config) == prepared


# --- what a rollback is refused on -------------------------------------------


def test_a_repository_that_promoted_nothing_has_nothing_to_roll_back(
    config: evolution.EvolutionConfig, release: str
) -> None:
    write_manifest(config.batches_root, BATCH_ID, ["r1"])

    with pytest.raises(evolution.BatchError, match="no batch has promoted anything"):
        rollback.rollback(config, reason=WHY, now=REVERSED)


def test_a_later_attempt_built_on_the_promotion_stops_it(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """The acceptance's "no later pinned candidate lineage": that attempt was
    developed and measured against a line carrying this change, and taking it
    back out from under it leaves evidence describing a line that no longer
    exists.

    The attempt can only stand there because somebody decided it should: no base
    is frozen until the cohort's reading of the release is settled (invariant
    17), so `retain` is what put this work on the promoted line in the first
    place.
    """

    directory = write_manifest(
        config.batches_root, SECOND_BATCH, ["r3"], analysis_task_id=f"2026-08-09-{SECOND_BATCH}"
    )
    (directory / "findings.md").write_text("# Findings\n", encoding="utf-8")
    write_closure(config.batches_root, SECOND_BATCH, analysis_task_id=f"2026-08-09-{SECOND_BATCH}")
    write_draft(config.batches_root, SECOND_BATCH, "second-idea")
    assert settle_release(config, at=LATER) is True
    experiments.create(config, ["second-idea"], base=promoted.promotion_revision, now=LATER)

    with pytest.raises(evolution.BatchError, match="stands on"):
        rollback.rollback(config, reason=WHY, now=LATER)

    assert git_rev(config.repo_root, RELEASE_REF) == promoted.promotion_revision
    assert not (config.batches_root / BATCH_ID / "rollback.json").exists()


def test_a_later_attempt_this_checkout_cannot_place_stops_it(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """The one Git question here that is answered by refusing. Everywhere else an
    unanswerable one is a fact about the clone and is reported; this one stands
    between an irreversible move and somebody's work, so a checkout that cannot
    see whether the two are related does not get to decide they are not."""

    write_manifest(config.batches_root, SECOND_BATCH, ["r3"])
    write_experiment(
        config.experiments_root,
        f"{SECOND_BATCH}-exp-01",
        base_revision="9" * 40,
        rounds=[experiment_round(1)],
    )

    with pytest.raises(evolution.BatchError, match="cannot be answered in this checkout"):
        rollback.rollback(config, reason=WHY, now=LATER)

    assert git_rev(config.repo_root, RELEASE_REF) == promoted.promotion_revision


def test_a_later_batch_that_admitted_nothing_does_not_stop_it(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """A frozen cohort has pinned no candidate and holds no work, so it is not
    lineage built on the promotion — the guard is about attempts, not about
    time."""

    write_manifest(config.batches_root, SECOND_BATCH, ["r3"])

    result = rollback.rollback(config, reason=WHY, now=LATER)

    assert result.reverted is True
    assert git_rev(config.repo_root, RELEASE_REF) == result.revision


def test_only_the_latest_promotion_is_rolled_back(
    config: evolution.EvolutionConfig, release: str
) -> None:
    """Two promotions, and the operation names neither: it reverses the newest,
    which is the one derivation `status` reports. Reaching past it is not
    offered, because what this repository did afterwards stands on it."""

    first = promote_candidate(config, batch_id=BATCH_ID, at=PROMOTED)
    second = promote_candidate(
        config,
        batch_id=SECOND_BATCH,
        drafts=("second-idea",),
        # Membership of its own: one report belongs to one batch (invariant 3),
        # and the second cohort's reading of the first release is taken over both
        # manifests — two batches naming one report is a report on both sides.
        reports=[make_manifest_report(key="s1", sequence=1, task_id="2026-08-05-second-task")],
        at=LATER,
    )

    result = rollback.rollback(config, reason=WHY, now=LATER)

    assert result.batch_id == SECOND_BATCH
    assert result.promotion_revision == second.promotion_revision
    assert revisions.contains(config.repo_root, first.promotion_revision, RELEASE_REF) is True
    assert not (config.batches_root / BATCH_ID / "rollback.json").exists()


def test_a_source_line_some_worktree_is_on_is_not_rolled_back(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult, tmp_path: Path
) -> None:
    """The same repository-level guard a promotion makes, worktree-wide: moving
    the branch a work tree is on leaves that tree describing the commit before."""

    git_worktree(config.repo_root, tmp_path / "linked", RELEASE_REF)

    with pytest.raises(evolution.BatchError, match=f"{RELEASE_REF} is checked out at"):
        rollback.rollback(config, reason=WHY, now=REVERSED)

    assert git_rev(config.repo_root, RELEASE_REF) == promoted.promotion_revision
    assert not (config.batches_root / BATCH_ID / "rollback.json").exists()


def test_a_line_that_no_longer_carries_the_promotion_is_not_rolled_back(
    config: evolution.EvolutionConfig, release: str, promoted: experiments.PromotionResult
) -> None:
    """The line was reset, or is not the one this promotion went onto. Either
    way there is nothing on it to take back, and recording a rollback for it
    would claim work this operation did not do."""

    git_update_ref(config.repo_root, RELEASE_REF, release)

    with pytest.raises(evolution.BatchError, match="does not have the promotion"):
        rollback.rollback(config, reason=WHY, now=REVERSED)

    assert git_rev(config.repo_root, RELEASE_REF) == release
    assert not (config.batches_root / BATCH_ID / "rollback.json").exists()


def test_a_promotion_taken_off_the_line_by_hand_is_not_recorded_as_a_rollback(
    config: evolution.EvolutionConfig, release: str, promoted: experiments.PromotionResult
) -> None:
    """The content is already gone, so the inverse would change nothing — and a
    commit saying a rollback happened while changing nothing is this controller
    claiming somebody else's work."""

    by_hand = git_file_commit(
        config.repo_root,
        promoted.promotion_revision,
        "file.txt",
        git_file(config.repo_root, release, "file.txt"),
        "revert the promotion, by hand",
    )
    git_update_ref(config.repo_root, RELEASE_REF, by_hand)
    assert git_tree(config.repo_root, by_hand) == git_tree(config.repo_root, release)

    with pytest.raises(evolution.BatchError, match="already carries none of the promotion"):
        rollback.rollback(config, reason=WHY, now=REVERSED)

    assert git_rev(config.repo_root, RELEASE_REF) == by_hand
    assert not (config.batches_root / BATCH_ID / "rollback.json").exists()


def test_a_revert_that_does_not_apply_is_a_persons_job(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """A conflict is a decision about what the line should hold, which needs a
    working tree and somebody to make it — so it refuses rather than committing
    a tree with markers in it."""

    conflicting = git_sibling_commit(
        config.repo_root, promoted.promotion_revision, "something else entirely\n", "rewrite the file"
    )
    git_update_ref(config.repo_root, RELEASE_REF, conflicting)

    with pytest.raises(evolution.BatchError, match="produces no tree here"):
        rollback.rollback(config, reason=WHY, now=REVERSED)

    assert git_rev(config.repo_root, RELEASE_REF) == conflicting
    assert not (config.batches_root / BATCH_ID / "rollback.json").exists()


def test_a_checkout_without_the_source_line_rolls_nothing_back(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    """A clone that never fetched the line cannot say where it stands, and a
    rollback moves it."""

    git_delete_ref(config.repo_root, RELEASE_REF)

    with pytest.raises(evolution.BatchError, match="does not hold that ref"):
        rollback.rollback(config, reason=WHY, now=REVERSED)


def test_the_moment_a_rollback_records_is_unambiguous(
    config: evolution.EvolutionConfig, promoted: experiments.PromotionResult
) -> None:
    with pytest.raises(evolution.BatchError, match="rollback time must be timezone-aware"):
        rollback.rollback(config, reason=WHY, now=datetime(2026, 8, 9, 9, 0, 0))
