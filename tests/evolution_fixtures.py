"""Shared scaffolding for the evolution controller suites.

One source for the repository, report, and feed builders, so the import tests
and the admission tests cannot drift into describing two different contracts.
Not a test module: pytest collects nothing here.
"""

from __future__ import annotations

import hashlib
import json
import shutil
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


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
