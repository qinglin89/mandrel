"""Administrative split closure must not release unfinished evolution work."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from evolution_fixtures import (
    complete_task,
    git_repo,
    make_repo,
    write_closure,
    write_draft,
    write_manifest,
)

from mandrel import evolution
from mandrel.evolution import analysis_task, experiments


SOURCE = "2026-10-10-source"
TARGET = "2026-10-10-remainder"
BATCH = "evolution-batch-0001"
ANALYSIS = "2026-07-31-evolution-batch-0001-analysis"
NOW = datetime(2026, 10, 10, 9, tzinfo=timezone.utc)


@pytest.fixture
def config(tmp_path: Path) -> evolution.EvolutionConfig:
    return evolution.load_config(make_repo(tmp_path))


def write_task(config, task_id, *, status="pending", split="", archived=False):
    path = (analysis_task.archived_task_path if archived else analysis_task.task_path)(config, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: {task_id}\nstatus: {status}\n---\n\n## Goal\n\nFinish the work.\n"
        f"{split}\n## Session log\n",
        encoding="utf-8",
    )
    return path


def transfer(target=TARGET, phase="ready"):
    return f"\n## Split\n\n- Phase: {phase}\n- Target: {target}\n"


@pytest.mark.parametrize("archived", [False, True])
def test_split_completion_waits_for_all_successors(config, archived):
    write_task(config, SOURCE, status="completed", split=transfer(), archived=archived)
    assert not analysis_task.task_finished(config, SOURCE)
    write_task(config, TARGET)
    assert not analysis_task.task_finished(config, SOURCE)
    write_task(config, TARGET, status="completed", split=transfer("2026-10-10-final"))
    assert not analysis_task.task_finished(config, SOURCE)
    write_task(config, "2026-10-10-final", status="completed")
    assert analysis_task.task_finished(config, SOURCE)
    complete_task(config, "2026-10-10-final")
    assert analysis_task.task_finished(config, SOURCE)


@pytest.mark.parametrize(
    "split",
    [
        transfer(phase="preparing"),
        "\n## Split\n\n- Phase: ready\n",
        "\n## Split\n\n- Target: " + TARGET + "\n",
        transfer() + "- Phase: ready\n",
        transfer() + "- Target: " + TARGET + "\n",
        transfer() + transfer(),
        transfer("../outside"),
        transfer("archive/" + TARGET),
        transfer("index"),
        transfer("") + "\n## Notes\n- Target: " + TARGET + "\n",
    ],
)
def test_malformed_or_unready_transfer_cannot_close_work(config, split):
    write_task(config, SOURCE, status="completed", split=split, archived=True)
    write_task(config, TARGET, status="completed")
    assert not analysis_task.task_finished(config, SOURCE)


@pytest.mark.parametrize("status", ["pending", "in_progress", "final_review", "blocked"])
def test_archival_does_not_complete_an_unfinished_split_source(config, status):
    write_task(config, SOURCE, status=status, split=transfer(), archived=True)
    write_task(config, TARGET, status="completed")
    assert not analysis_task.task_finished(config, SOURCE)


def test_split_cycles_do_not_complete_or_recurse_forever(config):
    write_task(config, SOURCE, status="completed", split=transfer(SOURCE), archived=True)
    assert not analysis_task.task_finished(config, SOURCE)
    write_task(config, SOURCE, status="completed", split=transfer(), archived=True)
    write_task(config, TARGET, status="completed", split=transfer(SOURCE), archived=True)
    assert not analysis_task.task_finished(config, SOURCE)


@pytest.mark.parametrize("body", ["# Truncated", "---\nid: another-task\nstatus: completed\n---\n"])
def test_unidentifiable_successor_is_not_completion_evidence(config, body):
    write_task(config, SOURCE, status="completed", split=transfer(), archived=True)
    successor = write_task(config, TARGET, archived=True)
    successor.write_text(body, encoding="utf-8")
    assert not analysis_task.task_finished(config, SOURCE)


def test_ordinary_task_completion_keeps_legacy_archive_semantics(config):
    assert not analysis_task.task_finished(config, SOURCE)
    write_task(config, SOURCE)
    assert not analysis_task.task_finished(config, SOURCE)
    write_task(config, SOURCE, status="completed")
    assert analysis_task.task_finished(config, SOURCE)
    analysis_task.task_path(config, SOURCE).unlink()
    write_task(config, SOURCE, archived=True)
    assert analysis_task.task_finished(config, SOURCE)


def prepare_batch(config):
    git_repo(config.repo_root, tag="v2.2.0")
    directory = write_manifest(
        config.batches_root,
        BATCH,
        ["r1", "r2"],
        analysis_task_id=ANALYSIS,
        runner_protocol_revision="v2.2.0",
    )
    directory.joinpath("findings.md").write_text("# Findings\n", encoding="utf-8")
    write_draft(config.batches_root, BATCH, "loader-fallback")
    return directory


def test_split_analysis_waits_before_admitting_its_drafts(config):
    directory = prepare_batch(config)
    path = write_task(config, ANALYSIS, status="completed", split=transfer(), archived=True)
    path.write_text(
        path.read_text(encoding="utf-8")
        + f"\n{analysis_task.task_heading(BATCH)}\n\nevolution/batches/{BATCH}/manifest.json\n",
        encoding="utf-8",
    )
    write_task(config, TARGET)

    with pytest.raises(evolution.BatchError, match="still in its analysis stage"):
        experiments.create(config, ["loader-fallback"], now=NOW)
    assert not directory.joinpath("analysis-complete.json").exists()

    complete_task(config, TARGET)
    assert experiments.create(config, ["loader-fallback"], now=NOW).created
    assert directory.joinpath("analysis-complete.json").exists()


def test_split_change_blocks_seal_until_successor_finishes(config):
    prepare_batch(config)
    write_closure(config.batches_root, BATCH, analysis_task_id=ANALYSIS)
    admission = experiments.create(config, ["loader-fallback"], now=NOW)
    source = admission.admitted[0]
    path = source.task_path
    path.write_text(
        path.read_text(encoding="utf-8")
        .replace("status: pending", "status: completed", 1)
        .replace("## Session log", transfer() + "\n## Session log", 1),
        encoding="utf-8",
    )
    complete_task(config, source.task_id)
    write_task(config, TARGET)

    with pytest.raises(evolution.BatchError, match="still in flight"):
        experiments.seal_round(config, now=NOW)

    # Re-running the original admission may repair its index, but must not
    # resurrect the archived source while the remainder is being worked.
    experiments.create(config, ["loader-fallback"], now=NOW)
    assert source.task_id not in analysis_task.index_path(config).read_text(encoding="utf-8")

    complete_task(config, TARGET)
    assert experiments.seal_round(config, now=NOW).observed == (source.task_id,)
