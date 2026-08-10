"""Shared scaffolding for the evolution controller suites.

One source for the repository, report, and feed builders, so the import tests
and the admission tests cannot drift into describing two different contracts.
Not a test module: pytest collects nothing here.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from ai_native_deployment.evolution import feed as feed_module

REPO_ROOT = Path(__file__).resolve().parents[1]

ARTIFACT_BODIES = {
    "evidence": b'{"layer": "L1", "events": []}',
    "static_metrics": b'{"layer": "L1", "rounds": 2}',
    "semantic_report": b'{"layer": "L2", "findings": []}',
    "report_markdown": b"# Report\n\nNo findings.\n",
}


def make_repo(tmp_path: Path) -> Path:
    """A repository carrying the real versioned evolution contract files.

    The schemas and config are copied rather than re-invented: a test that
    validated against a hand-written schema would prove nothing about the
    contract this repository actually ships.
    """

    root = tmp_path / "repo"
    (root / "evolution").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "evolution" / "config.toml", root / "evolution" / "config.toml")
    shutil.copytree(REPO_ROOT / "evolution" / "schemas", root / "evolution" / "schemas")
    (root / "evolution" / "ledger.jsonl").write_text("", encoding="utf-8")
    return root


def make_record(
    *,
    key: str,
    sequence: int,
    repo_id: str = "repo-alpha",
    task_id: str = "2026-07-01-task",
    evaluation_id: str | None = None,
    bodies: dict[str, bytes] | None = None,
    generated_at: str = "2026-07-30T10:00:00Z",
    runner_protocol_revision: str = "v2.2.0",
    rubric_revision: str = "r7",
    dev_model: str = "claude-opus-5",
) -> dict:
    bodies = bodies or ARTIFACT_BODIES
    return {
        "schema_version": 1,
        "report_key": key,
        "sequence": sequence,
        "generated_at": generated_at,
        "source": {
            "repo_id": repo_id,
            "repo_name": repo_id.replace("-", " "),
            "task_id": task_id,
            "evaluation_id": evaluation_id or f"eval-{key}",
            "archived": True,
            "completed": True,
        },
        "evaluator": {
            "backend": "claude",
            "model": "claude-opus-5",
            "schema_version": 1,
            "rubric_revision": rubric_revision,
        },
        "artifacts": {
            name: {
                "sha256": hashlib.sha256(body).hexdigest(),
                "size_bytes": len(body),
                "media_type": "application/json" if name != "report_markdown" else "text/markdown",
            }
            for name, body in bodies.items()
        },
        "provenance": {
            "runner_protocol_revision": runner_protocol_revision,
            "deploy_lock_hash": "a" * 64,
            "config_revision": "c1",
            "effective_revision": "e1",
            "dev": {"agent": "claude", "model": dev_model, "effort": "high", "profile": "dev"},
            "review": {"agent": "codex", "model": "gpt-x", "effort": "high", "profile": "review"},
        },
    }


def make_manifest_report(*, key: str, sequence: int = 1, task_id: str = "2026-07-01-task", version: int = 2) -> dict:
    """One membership entry, in the shape the given manifest version requires."""

    item = {
        "report_key": key,
        "sequence": sequence,
        "repo_id": "repo-alpha",
        "task_id": task_id,
        "evaluation_id": f"eval-{key}",
        "bundle_sha256": "b" * 64,
    }
    if version == 1:
        return item
    record = make_record(key=key, sequence=sequence, task_id=task_id)
    return {
        **item,
        "generated_at": record["generated_at"],
        "evaluator": record["evaluator"],
        "provenance": record["provenance"],
    }


def write_manifest(
    batches_root: Path,
    batch_id: str,
    report_keys: list[str],
    *,
    version: int = 2,
    **overrides,
) -> Path:
    """A schema-valid manifest on disk, for tests that need a frozen batch
    without freezing one — an older manifest version, or a batch whose reports
    were never imported here."""

    manifest = {
        "schema_version": version,
        "batch_id": batch_id,
        "created_at": "2026-07-31T00:00:00Z",
        "config_sha256": "c" * 64,
        "forced": False,
        "reports": [
            make_manifest_report(key=key, sequence=index + 1, task_id=f"2026-07-{index + 1:02d}-task", version=version)
            for index, key in enumerate(report_keys)
        ],
        **overrides,
    }
    directory = batches_root / batch_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return directory


def write_closure(
    batches_root: Path,
    batch_id: str,
    *,
    analysis_task_id: str,
    closed_at: str = "2026-07-31T12:00:00Z",
) -> Path:
    """The closure record the controller publishes from a completed analysis
    task, for tests that need a batch already analyzed elsewhere — the ordinary
    case on any machine that did not run the analysis."""

    path = batches_root / batch_id / "analysis-complete.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "batch_id": batch_id,
                "analysis_task_id": analysis_task_id,
                "closed_at": closed_at,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def write_outcome(
    batches_root: Path,
    batch_id: str,
    *,
    outcome: str = "no-change",
    reason: str = "no cluster reached the minimum unique-task count",
    experiment_id: str | None = None,
    promotion_revision: str | None = None,
    decided_at: str = "2026-08-09T09:00:00Z",
) -> Path:
    """The terminal record that ends a batch, for tests that need a batch whose
    change cycle is over."""

    return _write_json(
        batches_root / batch_id / "outcome.json",
        {
            "schema_version": 1,
            "batch_id": batch_id,
            "outcome": outcome,
            "decided_at": decided_at,
            "reason": reason,
            "experiment_id": experiment_id,
            "promotion_revision": promotion_revision,
        },
    )


def write_rejected_drafts(batches_root: Path, batch_id: str, rejected: list[dict]) -> Path:
    """The drafts a human declined at this batch's admission gate."""

    return _write_json(
        batches_root / batch_id / "rejected-drafts.json",
        {"schema_version": 1, "batch_id": batch_id, "rejected": rejected},
    )


def rejection(draft_id: str, *, reason: str = "one report is not recurrence", sha: str | None = None) -> dict:
    return {
        "draft_id": draft_id,
        "draft_sha256": sha or draft_sha256(draft_id),
        "rejected_at": "2026-08-02T09:00:00Z",
        "reason": reason,
    }


def write_draft(batches_root: Path, batch_id: str, draft_id: str, body: str | None = None) -> Path:
    """One change-task draft waiting in a batch's `proposed-tasks/` directory."""

    path = batches_root / batch_id / "proposed-tasks" / f"{draft_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if body is not None else _draft_body(draft_id), encoding="utf-8")
    return path


def draft_sha256(draft_id: str) -> str:
    return hashlib.sha256(_draft_body(draft_id).encode("utf-8")).hexdigest()


def admitted_task(draft_id: str, *, task_id: str | None = None, complete: bool = True) -> dict:
    return {
        "task_id": task_id or f"2026-08-01-{draft_id}",
        "draft_id": draft_id,
        "draft_sha256": draft_sha256(draft_id),
        "admitted_at": "2026-08-01T09:00:00Z",
        "completion_observed_at": "2026-08-03T08:00:00Z" if complete else None,
    }


def experiment_round(
    number: int,
    *,
    tasks: list[dict] | None = None,
    candidate_revision: str | None = None,
    reason: str = "grouped admission of the loader dispositions",
    opened_at: str = "2026-08-01T09:00:00Z",
    sealed_at: str = "2026-08-03T09:00:00Z",
) -> dict:
    """One round. `candidate_revision` seals it — which is what makes it
    candidate-ready, and the only state anything may measure."""

    return {
        "round": number,
        "opened_at": opened_at,
        "reason": reason,
        "tasks": tasks if tasks is not None else [admitted_task("loader-fallback")],
        "seal": None
        if candidate_revision is None
        else {"sealed_at": sealed_at, "candidate_revision": candidate_revision},
    }


def experiment_decision(
    outcome: str,
    *,
    reason: str = "the approach needs a loader change this batch cannot justify",
    superseded_by: str | None = None,
    promotion_revision: str | None = None,
    decided_at: str = "2026-08-05T09:00:00Z",
) -> dict:
    return {
        "outcome": outcome,
        "decided_at": decided_at,
        "reason": reason,
        "superseded_by": superseded_by,
        "promotion_revision": promotion_revision,
    }


def write_experiment(
    experiments_root: Path,
    experiment_id: str,
    *,
    batch_id: str | None = None,
    base_revision: str = "a" * 40,
    base_release_ref: str | None = "v2.2.0",
    rounds: list[dict] | None = None,
    decision: dict | None = None,
    created_at: str = "2026-08-01T09:00:00Z",
    ref: str | None = None,
    **overrides,
) -> Path:
    """One experiment record on disk, in its own directory.

    `batch_id` defaults to the batch half of the id, so the ordinary case states
    the identity once and the mismatch cases state it twice on purpose.
    """

    record = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "batch_id": batch_id if batch_id is not None else experiment_id.split("-exp-")[0],
        "created_at": created_at,
        "base_revision": base_revision,
        "base_release_ref": base_release_ref,
        "ref": ref if ref is not None else f"refs/evolution/experiments/{experiment_id}",
        "rounds": rounds if rounds is not None else [experiment_round(1)],
        "decision": decision,
        **overrides,
    }
    _write_json(experiments_root / experiment_id / "experiment.json", record)
    return experiments_root / experiment_id


def _draft_body(draft_id: str) -> str:
    return f"---\nid: 2026-08-01-{draft_id}\nstatus: pending\n---\n\n# {draft_id}\n"


def _write_json(path: Path, record: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_feed(root: Path, records: list[dict], *, bodies: dict[str, bytes] | None = None) -> feed_module.DirectoryFeed:
    (root / feed_module.REPORTS_DIRNAME).mkdir(parents=True, exist_ok=True)
    for record in records:
        key = record.get("report_key") or f"unkeyed-{record.get('sequence')}"
        (root / feed_module.REPORTS_DIRNAME / f"{key}.json").write_text(json.dumps(record), encoding="utf-8")
        artifacts = record.get("artifacts")
        if not isinstance(artifacts, dict):
            continue
        directory = root / feed_module.ARTIFACTS_DIRNAME / key
        directory.mkdir(parents=True, exist_ok=True)
        for name, body in (bodies or ARTIFACT_BODIES).items():
            if name in artifacts:
                (directory / name).write_bytes(body)
    return feed_module.DirectoryFeed(root)


GIT_IDENTITY = ["-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false"]


def git_repo(root: Path, *, tag: str | None) -> Path:
    """A work tree with one commit, and — when `tag` is given — a release tag
    plus one commit on top of it.

    That extra commit is the point of the tag case: it is the candidate
    revision, and it must never be mistaken for the release line (invariant 8).
    """

    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    (root / "file.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), *GIT_IDENTITY, "commit", "-q", "-m", "first"], check=True)
    if tag is not None:
        subprocess.run(["git", "-C", str(root), "tag", tag], check=True)
        (root / "file.txt").write_text("two\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), *GIT_IDENTITY, "commit", "-q", "-m", "candidate work"], check=True)
    return root


def git_commit(root: Path, message: str) -> str:
    """One more commit on the current branch, and its sha.

    Stages only `file.txt`, never the whole tree: the evolution records a test
    writes are the untracked state under examination, and committing them would
    make a later `git checkout` delete them.
    """

    path = root / "file.txt"
    path.write_text(f"{path.read_text(encoding='utf-8')}{message}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "--", "file.txt"], check=True)
    subprocess.run(["git", "-C", str(root), *GIT_IDENTITY, "commit", "-q", "-m", message], check=True)
    return git_rev(root, "HEAD")


def git_rev(root: Path, revision: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", f"{revision}^{{commit}}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def git_update_ref(root: Path, ref: str, revision: str) -> None:
    subprocess.run(["git", "-C", str(root), "update-ref", ref, revision], check=True)


def git_checkout(root: Path, revision: str) -> None:
    subprocess.run(["git", "-C", str(root), "checkout", "-q", revision], check=True)


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
