"""Command-line entry point for ai-native-deployment."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from . import deploy, lockfile, manifest, registry
from .evolution import errors as evolution_errors


def _add_workspace_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", help="evolution workspace root (default: this checkout)")


def _add_feed_arguments(parser: argparse.ArgumentParser) -> None:
    """Where reports come from, and how far one run reads.

    `--feed-dir` is the offline path: a local report bundle, used for fixtures,
    replay, and every run made before orch-hub publishes its global feed.
    Without it the protected orch-hub client is built from the environment
    variables `evolution/config.toml` names.
    """

    from .evolution import importer

    parser.add_argument("--feed-dir", help="read reports from a local bundle directory instead of the orch-hub feed")
    parser.add_argument(
        "--page-size",
        type=int,
        default=importer.DEFAULT_PAGE_SIZE,
        help=f"records per feed page (default: {importer.DEFAULT_PAGE_SIZE})",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=importer.DEFAULT_MAX_PAGES,
        help=f"drain-safety bound on pages per run (default: {importer.DEFAULT_MAX_PAGES})",
    )


def build_parser(prog: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog or os.environ.get("AI_NATIVE_DEPLOYMENT_PROG") or "aii-2",
        description="Deploy and check AI-native protocol payloads.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    deploy_parser = subparsers.add_parser("deploy", help="deploy canonical files into a target repo")
    deploy_parser.add_argument("--dry-run", action="store_true", help="preview deploy changes without writing files")
    deploy_parser.add_argument(
        "--bootstrap-orchestrator",
        action="store_true",
        help="after deployment, create/update .cursor/orchestrator/.venv and install requirements",
    )
    deploy_parser.add_argument(
        "--orchestrator-python",
        default="python3.14",
        help="python executable used with --bootstrap-orchestrator (default: python3.14)",
    )
    deploy_parser.add_argument("target", help="target repo path")

    status_parser = subparsers.add_parser("status", help="check a target repo for drift")
    status_parser.add_argument("target", nargs="?", help="target repo path")
    status_parser.add_argument("--all", action="store_true", help="check every repo in the local registry")

    registry_parser = subparsers.add_parser("registry", help="manage local repo registry")
    registry_subparsers = registry_parser.add_subparsers(dest="registry_command", required=True)

    list_parser = registry_subparsers.add_parser("list", help="list locally registered repos")
    list_parser.add_argument("--json", action="store_true", help="print JSON")

    add_parser = registry_subparsers.add_parser("add", help="add a repo that already has a manifest")
    add_parser.add_argument("target", help="target repo path")

    remove_parser = registry_subparsers.add_parser("remove", help="remove a repo from local registry tracking")
    remove_parser.add_argument("name_or_path", help="registered name or repo path")

    evolution_parser = subparsers.add_parser(
        "evolution",
        help="protocol-evolution controller for this repository (human-triggered; never starts an evaluation)",
    )
    evolution_subparsers = evolution_parser.add_subparsers(dest="evolution_command", required=True)

    evolution_list = evolution_subparsers.add_parser("list", help="inspect feed candidates; writes nothing")
    _add_workspace_argument(evolution_list)
    _add_feed_arguments(evolution_list)

    evolution_sync = evolution_subparsers.add_parser("sync", help="import eligible reports into the pending pool")
    _add_workspace_argument(evolution_sync)
    _add_feed_arguments(evolution_sync)

    evolution_status = evolution_subparsers.add_parser("status", help="show the lifecycle phase; writes nothing")
    _add_workspace_argument(evolution_status)
    evolution_status.add_argument("--json", action="store_true", help="print the same shape as JSON")

    evolution_start = evolution_subparsers.add_parser(
        "start",
        help="close finished analyses, repair, sync, then freeze a batch when admission policy allows",
    )
    _add_workspace_argument(evolution_start)
    _add_feed_arguments(evolution_start)
    evolution_start.add_argument(
        "--force",
        action="store_true",
        help="waive the configured target (never the minimum); requires --justification",
    )
    evolution_start.add_argument(
        "--justification",
        help="written human reason recorded in the manifest when --force forms a below-target batch",
    )

    return parser


def _evolution(args: argparse.Namespace) -> int:
    """Adapter for the evolution controller: resolve the workspace and the feed,
    call one domain function, print what it returned.

    No policy lives here. Which reports are eligible, whether a batch may form,
    and what closes one are all decided in `ai_native_deployment.evolution`,
    where the contract's invariants are stated next to the code enforcing them.

    Exit status is 0 for every completed run, including a `start` that formed no
    batch: too little evidence is the contract's normal outcome, not a failure.
    A refusal or an unusable feed raises and `main` reports it as 2.
    """

    from .evolution import batches, config as evolution_config, importer, phase, render

    config = evolution_config.load_config(Path(args.repo).expanduser() if args.repo else None)

    if args.evolution_command == "status":
        status = phase.describe(config)
        print(json.dumps(status.to_json(), indent=2, sort_keys=True) if args.json else render.format_status(status))
        return 0

    feed = _evolution_feed(config, args.feed_dir)
    if args.evolution_command == "list":
        result = importer.list_candidates(config, feed, page_size=args.page_size, max_pages=args.max_pages)
        print(render.format_list(result))
        return 0

    if args.evolution_command == "sync":
        result = importer.sync(config, feed, page_size=args.page_size, max_pages=args.max_pages)
        print(render.format_sync(result))
        return 0

    started = batches.start(
        config,
        feed,
        forced=args.force,
        justification=args.justification,
        page_size=args.page_size,
        max_pages=args.max_pages,
    )
    print(render.format_start(started, config.repo_root))
    return 0


def _evolution_feed(config, feed_dir: str | None):
    from .evolution import feed as feed_module, hub

    if feed_dir:
        return feed_module.DirectoryFeed(Path(feed_dir).expanduser().resolve())
    return hub.feed_from_config(config, environ=os.environ)


def _status_all() -> int:
    entries = registry.load_registry()
    if not entries:
        print("No registered repos.")
        return 0

    has_drift = False
    for index, entry in enumerate(entries):
        result = deploy.check_status(entry["path"])
        has_drift = has_drift or not result.in_sync
        if index:
            print()
        print(deploy.format_status(result, label=f"{entry['name']} {entry['path']}"))
    return 1 if has_drift else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "deploy":
            if args.dry_run:
                preview = deploy.preview_deploy(args.target)
                print(deploy.format_deploy_preview(preview))
                if args.bootstrap_orchestrator:
                    print("  orchestrator bootstrap: would create/update .cursor/orchestrator/.venv and install requirements")
                return 1 if any(change.action == "blocked" for change in preview.changes) else 0
            deployed_manifest = deploy.deploy_canonical(args.target)
            target_root = Path(args.target).expanduser().resolve()
            files = deployed_manifest.get("files", {})
            print(f"deployed {len(files)} files to {target_root}")
            print(f"manifest: {target_root / manifest_path_name()}")
            print(_deployed_revision_line(target_root))
            if args.bootstrap_orchestrator:
                result = deploy.bootstrap_orchestrator(args.target, python_executable=args.orchestrator_python)
                print(f"orchestrator venv: {result.venv_path}")
                print(f"orchestrator python: {result.python_path}")
                print(f"orchestrator requirements: {result.requirements_path}")
                if result.env_created:
                    print(f"orchestrator env: created {result.env_path}")
                else:
                    print(f"orchestrator env: kept existing {result.env_path}")
            return 0

        if args.command == "status":
            if args.all:
                if args.target:
                    parser.error("status accepts either a target path or --all, not both")
                return _status_all()
            if not args.target:
                parser.error("status requires a target path unless --all is set")
            result = deploy.check_status(args.target)
            print(deploy.format_status(result, label=str(Path(args.target).expanduser().resolve())))
            return 0 if result.in_sync else 1

        if args.command == "registry":
            if args.registry_command == "list":
                entries = registry.load_registry()
                if args.json:
                    print(json.dumps(entries, indent=2, sort_keys=True))
                else:
                    for entry in entries:
                        print(f"{entry['name']}\t{entry['path']}")
                return 0

            if args.registry_command == "add":
                entry = registry.add_repo(args.target, require_manifest=True)
                print(f"registered {entry['name']}: {entry['path']}")
                return 0

            if args.registry_command == "remove":
                removed = registry.remove_repo(args.name_or_path)
                if not removed:
                    print(f"not registered: {args.name_or_path}", file=sys.stderr)
                    return 1
                print(f"removed from registry: {args.name_or_path}")
                return 0

        if args.command == "evolution":
            return _evolution(args)

    except (
        FileNotFoundError,
        deploy.PayloadError,
        manifest.ManifestError,
        registry.RegistryError,
        evolution_errors.EvolutionError,
        OSError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    parser.error("unknown command")
    return 2


def manifest_path_name() -> str:
    from .paths import MANIFEST_FILENAME

    return MANIFEST_FILENAME


def _deployed_revision_line(target_root: Path) -> str:
    """What the lock this deploy just wrote says the payload came from.

    Printed because the absence is a fact the operator can act on and would
    otherwise never see. A payload the deploy could not tie to a committed
    canonical revision leaves the lock stating none (`lockfile.py`), and a
    report produced by that target then carries no effective revision and is
    excluded from any release-effectiveness reading (`evolution/README.md`,
    Release assessment). Committing the canonical work and redeploying is what
    puts the target back on the record.
    """

    revision = lockfile.read_lock(target_root).get("source_git_commit")
    if isinstance(revision, str) and revision:
        return f"source revision: {revision}"
    return (
        "source revision: none — this payload is not exactly a committed canonical revision, "
        "so reports produced by this target cannot be placed in a release assessment"
    )


if __name__ == "__main__":
    raise SystemExit(main())
