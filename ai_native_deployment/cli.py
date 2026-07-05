"""Command-line entry point for ai-native-deployment."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import deploy, manifest, registry, skills


def build_parser(prog: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog or os.environ.get("AI_NATIVE_DEPLOYMENT_PROG") or "aii-2",
        description="Deploy and check AI-native protocol payloads.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    deploy_parser = subparsers.add_parser("deploy", help="deploy canonical files into a target repo")
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

    skills_parser = subparsers.add_parser("skills", help="temporary global skill compatibility commands")
    skills_subparsers = skills_parser.add_subparsers(dest="skills_command", required=True)

    sync_parser = skills_subparsers.add_parser(
        "sync-claude-global",
        help="temporarily sync skills-backup into ~/.claude/skills",
        description=(
            "Temporary compatibility command. Sync parked skills-backup entries into "
            "~/.claude/skills for the current tested Claude/Cursor/Codex hook model. "
            "This is not target repo deployment and does not update target manifests."
        ),
    )
    sync_parser.add_argument("--dry-run", action="store_true", help="report changes without writing ~/.claude/skills")

    return parser


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
            deployed_manifest = deploy.deploy_canonical(args.target)
            files = deployed_manifest.get("files", {})
            print(f"deployed {len(files)} files to {Path(args.target).expanduser().resolve()}")
            print(f"manifest: {Path(args.target).expanduser().resolve() / manifest_path_name()}")
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

        if args.command == "skills":
            if args.skills_command == "sync-claude-global":
                result = skills.sync_claude_global(dry_run=args.dry_run)
                print(skills.format_sync_result(result))
                return 0

    except (FileNotFoundError, manifest.ManifestError, registry.RegistryError, skills.SkillsSyncError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    parser.error("unknown command")
    return 2


def manifest_path_name() -> str:
    from .paths import MANIFEST_FILENAME

    return MANIFEST_FILENAME


if __name__ == "__main__":
    raise SystemExit(main())
