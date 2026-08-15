"""Command-line entry point for ai-native-deployment."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import deploy, lockfile, manifest, registry
from .evolution import errors as evolution_errors


def _add_workspace_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", help="evolution workspace root (default: this checkout)")


def _add_expectation_argument(parser: argparse.ArgumentParser) -> None:
    """The state the caller decided this verb from.

    Optional, because an operator reading `status` and typing the next verb is
    their own single writer. A surface that is not — a Web adapter, orch-hub, a
    script resuming — passes the `state_revision` its reading carried, and the
    operation refuses under the lock where the lifecycle has moved since. That
    check belongs to the operation and not here: outside its lock the answer
    stops being true before the write.
    """

    parser.add_argument(
        "--expect",
        metavar="STATE_REVISION",
        help="the state_revision this decision was made against; refuse if the lifecycle has moved since",
    )


def _add_reason_argument(parser: argparse.ArgumentParser, what: str) -> None:
    parser.add_argument("--reason", required=True, help=f"the human reason {what} (recorded, and required)")


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
    _add_expectation_argument(evolution_start)

    _add_lineage_commands(evolution_subparsers)
    _add_release_commands(evolution_subparsers)
    return parser


def _add_lineage_commands(subparsers: argparse._SubParsersAction) -> None:
    """The verbs that move a batch's change lineage.

    One command per domain operation, named exactly as `status` names it in
    `allowed_actions` — a surface reading that gate has the verb and the object
    id it needs, with nothing to translate. None of them decides anything: which
    drafts belong together, whether an attempt is worth continuing, whether a
    batch changed nothing, and whether the evidence justifies the source line are
    human judgements the operator states here and the operation records
    (invariant 9).
    """

    from .evolution import phase

    create = subparsers.add_parser(
        phase.ACTION_CREATE, help="admit a group of drafts as a new experiment on the current batch"
    )
    create.add_argument("draft", nargs="+", help="draft id, as `status` lists it at the admission gate")
    create.add_argument(
        "--base",
        help="source revision the first experiment of a batch freezes (default: HEAD); later ones take that commit",
    )
    create.add_argument("--reason", help="optional note recorded with the admission")
    _add_workspace_argument(create)
    _add_expectation_argument(create)

    add_tasks = subparsers.add_parser(
        phase.ACTION_ADD_TASKS, help="admit further drafts into the open experiment's open round"
    )
    add_tasks.add_argument("draft", nargs="+", help="draft id waiting at the admission gate")
    _add_workspace_argument(add_tasks)
    _add_expectation_argument(add_tasks)

    reject = subparsers.add_parser(phase.ACTION_REJECT, help="decline drafts at the admission gate, with the reason")
    reject.add_argument("draft", nargs="+", help="draft id to decline; declining is terminal for that proposal")
    _add_reason_argument(reject, "these drafts were declined")
    _add_workspace_argument(reject)
    _add_expectation_argument(reject)

    seal = subparsers.add_parser(
        phase.ACTION_SEAL_ROUND, help="observe the open round's tasks complete and pin its candidate revision"
    )
    _add_workspace_argument(seal)
    _add_expectation_argument(seal)

    revise = subparsers.add_parser(
        phase.ACTION_REVISE, help="open the next round of the open experiment, from the candidate already pinned"
    )
    _add_reason_argument(revise, "the sealed round is being revised")
    _add_workspace_argument(revise)
    _add_expectation_argument(revise)

    abandon = subparsers.add_parser(phase.ACTION_ABANDON, help="end the open experiment without replacing it")
    _add_reason_argument(abandon, "this attempt was dropped")
    _add_experiment_argument(abandon)
    _add_workspace_argument(abandon)
    _add_expectation_argument(abandon)

    supersede = subparsers.add_parser(
        phase.ACTION_SUPERSEDE, help="replace the open experiment with a fresh attempt at the same change"
    )
    _add_reason_argument(supersede, "this attempt is being replaced")
    _add_experiment_argument(supersede)
    _add_workspace_argument(supersede)
    _add_expectation_argument(supersede)

    conclude = subparsers.add_parser(
        phase.ACTION_CONCLUDE_NO_CHANGE, help="end the current batch having changed nothing (invariant 7)"
    )
    _add_reason_argument(conclude, "this batch's evidence justified no change")
    _add_workspace_argument(conclude)
    _add_expectation_argument(conclude)

    promote = subparsers.add_parser(
        phase.ACTION_PROMOTE, help="carry the replayed candidate onto the source line and end the batch with it"
    )
    _add_reason_argument(promote, "this evidence justified putting the candidate on the source line")
    promote.add_argument(
        "--target",
        action="append",
        default=[],
        metavar="NAME",
        help="a target this promotion is intended for, repeatable; a plan, never a deployment",
    )
    _add_workspace_argument(promote)
    _add_expectation_argument(promote)


def _add_release_commands(subparsers: argparse._SubParsersAction) -> None:
    """The verbs about a release: the runs measured against a candidate, the
    reversal of a promotion, and the cohort's reading of the one before it.

    They act on things the change lineage has already produced — a round's run, a
    promotion on the line, a frozen cohort's reading — which is why they are
    grouped apart from the verbs that move that lineage. Judging any of it stays
    the operator's: these record a verdict, a settlement or a reversal, and none
    of them derives one.
    """

    from .evolution import assessment, phase

    replay_abandon = subparsers.add_parser(
        phase.ACTION_REPLAY_ABANDON, help="record why a replay run ended when its harness cannot say"
    )
    _add_reason_argument(replay_abandon, "this run stopped without its harness reporting")
    _add_workspace_argument(replay_abandon)
    _add_expectation_argument(replay_abandon)

    replay_withdraw = subparsers.add_parser(
        phase.ACTION_REPLAY_WITHDRAW, help="give up a replay request the harness never answered for"
    )
    _add_workspace_argument(replay_withdraw)
    _add_expectation_argument(replay_withdraw)

    rollback = subparsers.add_parser(
        phase.ACTION_ROLLBACK, help="take the latest promotion back off the line it was put on"
    )
    _add_reason_argument(rollback, "this promotion is being taken back off the source line")
    _add_workspace_argument(rollback)
    _add_expectation_argument(rollback)

    assess = subparsers.add_parser(
        phase.ACTION_ASSESS, help="record this cohort's reading of the release before it"
    )
    _add_reading_arguments(assess, assessment)
    assess.add_argument(
        "--metric",
        action="append",
        default=[],
        metavar="NAME:UNIT:BEFORE:AFTER:BETTER",
        help=(
            "a quantity both cohorts came to, repeatable "
            f"(e.g. review-rounds:rounds:1.8:1.2:{assessment.BETTER_LOWER}); an empty side is one that cohort does "
            f"not state, which a quantity called better in some direction may not have — record it "
            f"{assessment.BETTER_NEITHER!r} instead"
        ),
    )
    _add_workspace_argument(assess)
    _add_expectation_argument(assess)

    assess_abandon = subparsers.add_parser(
        phase.ACTION_ASSESS_ABANDON, help="record why the counterfactual run ended when its harness cannot say"
    )
    _add_reason_argument(assess_abandon, "this run stopped without its harness reporting")
    _add_workspace_argument(assess_abandon)
    _add_expectation_argument(assess_abandon)

    assess_withdraw = subparsers.add_parser(
        phase.ACTION_ASSESS_WITHDRAW, help="give up a counterfactual request the harness never answered for"
    )
    _add_workspace_argument(assess_withdraw)
    _add_expectation_argument(assess_withdraw)

    assess_resolve = subparsers.add_parser(
        phase.ACTION_ASSESS_RESOLVE, help="revise the reading on the strength of the completed counterfactual"
    )
    _add_reading_arguments(assess_resolve, assessment)
    _add_workspace_argument(assess_resolve)
    _add_expectation_argument(assess_resolve)

    settle = subparsers.add_parser(
        phase.ACTION_SETTLE, help="answer the gate between this release and the next base freeze (invariant 17)"
    )
    settle.add_argument(
        "--settlement",
        required=True,
        choices=list(assessment.SETTLEMENTS),
        help=(
            f"{assessment.SETTLEMENT_RETAIN}: the release stays the line the next base is frozen on; "
            f"{assessment.SETTLEMENT_ROLLED_BACK}: it comes back off first, which this verb does itself"
        ),
    )
    _add_reason_argument(settle, "the release was kept or taken back")
    _add_workspace_argument(settle)
    _add_expectation_argument(settle)


def _add_reading_arguments(parser: argparse.ArgumentParser, assessment) -> None:
    """What a human states about a release: the verdict, how sure of it, and why.

    The rationale is required for the reason every recorded reason here is: the
    evidence the judging session had in front of it is machine-local, and this
    sentence is what a later reader has instead of it. The two vocabularies are
    the record's own, so a value this build does not know is refused at the
    command line rather than at the write.
    """

    parser.add_argument(
        "--verdict",
        required=True,
        choices=list(assessment.VERDICTS),
        help="what this cohort reads of the release; a directional one has to be supported by the evidence recorded",
    )
    parser.add_argument(
        "--confidence",
        required=True,
        choices=list(assessment.CONFIDENCES),
        help="how strongly the evidence carries that verdict",
    )
    parser.add_argument(
        "--rationale",
        required=True,
        help="why the verdict is that verdict (recorded, and required)",
    )


def _add_experiment_argument(parser: argparse.ArgumentParser) -> None:
    """Which attempt the decision is about.

    Optional in the operation and passed whenever the caller has it, because it
    is the only thing that tells an interrupted supersession redone from an
    untouched successor superseded in its turn. `status` names the experiment
    this verb would act on, so a surface reading the gate always has it.
    """

    parser.add_argument("--experiment", help="experiment id this decision is about, as `status` names it")


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

    if args.evolution_command in _lineage_verbs(phase):
        print(_lineage(config, args))
        return 0

    if args.evolution_command in _release_verbs(phase):
        print(_release(config, args))
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
        expect=args.expect,
        page_size=args.page_size,
        max_pages=args.max_pages,
    )
    print(render.format_start(started, config.repo_root))
    return 0


def _lineage_verbs(phase) -> frozenset[str]:
    """The verbs that move a batch's change lineage, by the name
    `allowed_actions` gives them.

    Derived from those constants rather than spelled again, here and in the
    parser, so a verb cannot be offered under one name and dispatched under
    another — and so a surface acting on the gate's JSON can pass the action it
    read straight to the command line.
    """

    return frozenset(
        {
            phase.ACTION_CREATE,
            phase.ACTION_ADD_TASKS,
            phase.ACTION_REJECT,
            phase.ACTION_SEAL_ROUND,
            phase.ACTION_REVISE,
            phase.ACTION_ABANDON,
            phase.ACTION_SUPERSEDE,
            phase.ACTION_CONCLUDE_NO_CHANGE,
            phase.ACTION_PROMOTE,
        }
    )


def _release_verbs(phase) -> frozenset[str]:
    """The verbs about a release: a run measured against a candidate, a
    promotion taken back off the line, and a cohort's reading of the release
    before it.

    Named from the same constants for the same reason. Two of the replay verbs
    are here and two are not — `replay-start` and `replay-conclude` ask a harness,
    which this console has no way to reach yet.
    """

    return frozenset(
        {
            phase.ACTION_REPLAY_ABANDON,
            phase.ACTION_REPLAY_WITHDRAW,
            phase.ACTION_ROLLBACK,
            phase.ACTION_ASSESS,
            phase.ACTION_ASSESS_ABANDON,
            phase.ACTION_ASSESS_WITHDRAW,
            phase.ACTION_ASSESS_RESOLVE,
            phase.ACTION_SETTLE,
        }
    )


def _wired_verbs(phase) -> frozenset[str]:
    """Every lifecycle verb this CLI dispatches.

    What holds the console together: a command the gate has no verb for is a
    lifecycle only this surface believes in, so both directions are derived from
    `phase.ACTION_*` and a surface can pass the action it read from
    `allowed_actions` straight to the command line.
    """

    return _lineage_verbs(phase) | _release_verbs(phase)


def _lineage(config, args: argparse.Namespace) -> str:
    """Run one lineage verb and describe what it did.

    Nothing is decided here. Each branch passes the operator's selection, reason
    and expected state to the operation that owns the guards, the lock, and the
    recoverable write order, and formats what came back — including whether this
    run wrote anything at all, which a redo is entitled to report and an adapter
    is not entitled to guess.
    """

    from .evolution import experiments, phase, render

    command = args.evolution_command
    if command == phase.ACTION_CREATE:
        return render.format_admission(
            experiments.create(config, args.draft, base=args.base, reason=args.reason, expect=args.expect),
            config.repo_root,
        )
    if command == phase.ACTION_ADD_TASKS:
        return render.format_admission(
            experiments.add_tasks(config, args.draft, expect=args.expect), config.repo_root
        )
    if command == phase.ACTION_REJECT:
        return render.format_rejection(
            experiments.reject(config, args.draft, reason=args.reason, expect=args.expect), config.repo_root
        )
    if command == phase.ACTION_SEAL_ROUND:
        return render.format_seal(experiments.seal_round(config, expect=args.expect))
    if command == phase.ACTION_REVISE:
        return render.format_revision(experiments.revise(config, reason=args.reason, expect=args.expect))
    if command in (phase.ACTION_ABANDON, phase.ACTION_SUPERSEDE):
        end = experiments.abandon if command == phase.ACTION_ABANDON else experiments.supersede
        return render.format_decision(
            end(config, reason=args.reason, experiment_id=args.experiment, expect=args.expect)
        )
    if command == phase.ACTION_CONCLUDE_NO_CHANGE:
        return render.format_conclusion(
            experiments.conclude_no_change(config, reason=args.reason, expect=args.expect), config.repo_root
        )
    if command == phase.ACTION_PROMOTE:
        return render.format_promotion(
            experiments.promote(config, reason=args.reason, targets=args.target, expect=args.expect),
            config.repo_root,
        )
    # Named above as a lineage verb and not dispatched here, which is a verb
    # added to one list and not the other. Raised rather than fallen through to
    # the last branch: the last branch puts a candidate on the source line.
    raise AssertionError(f"{command} is a lineage verb with no dispatch")


def _release(config, args: argparse.Namespace) -> str:
    """Run one release verb and describe what it did.

    Same shape as the lineage half and for the same reasons: the operator's
    judgement goes to the operation that owns the guards, the lock and the
    recoverable write order, and what comes back is formatted — including
    whether this run wrote anything. The settlement is the one that composes
    another operation, and it does that itself: a `rolled-back` answer runs the
    reversal under its own lock, so this offers one verb rather than a
    rollback-then-settle sequence of its own.
    """

    from .evolution import assessment, phase, render, replay, rollback

    command = args.evolution_command
    if command == phase.ACTION_REPLAY_ABANDON:
        return render.format_run_ended(replay.abandon(config, reason=args.reason, expect=args.expect))
    if command == phase.ACTION_REPLAY_WITHDRAW:
        return render.format_request_withdrawn(replay.withdraw(config, expect=args.expect))
    if command == phase.ACTION_ROLLBACK:
        return render.format_rollback(
            rollback.rollback(config, reason=args.reason, expect=args.expect), config.repo_root
        )
    if command in (phase.ACTION_ASSESS, phase.ACTION_ASSESS_RESOLVE):
        reading = (
            assessment.form(
                config,
                verdict=args.verdict,
                confidence=args.confidence,
                rationale=args.rationale,
                metrics=_measurements(assessment, args.metric),
                expect=args.expect,
            )
            if command == phase.ACTION_ASSESS
            else assessment.resolve(
                config,
                verdict=args.verdict,
                confidence=args.confidence,
                rationale=args.rationale,
                expect=args.expect,
            )
        )
        return render.format_reading(reading, config.repo_root, resolved=command == phase.ACTION_ASSESS_RESOLVE)
    if command == phase.ACTION_ASSESS_ABANDON:
        return render.format_counterfactual_ended(assessment.abandon(config, reason=args.reason, expect=args.expect))
    if command == phase.ACTION_ASSESS_WITHDRAW:
        return render.format_counterfactual_withdrawn(assessment.withdraw(config, expect=args.expect))
    if command == phase.ACTION_SETTLE:
        return render.format_settlement(
            assessment.settle(config, settlement=args.settlement, reason=args.reason, expect=args.expect),
            config.repo_root,
        )
    # A release verb named above and not dispatched here, for `_lineage`'s
    # reason: falling through would answer a release gate that was never asked.
    raise AssertionError(f"{command} is a release verb with no dispatch")


def _measurements(assessment, stated: list[str]) -> list[Any]:
    """The quantities an operator read off this machine's evaluation artifacts.

    Five fields because that is what the record holds: the metric, its unit, what
    each cohort came to, and which direction is better. A side left empty is one
    that cohort does not state — a quantity nothing measured before the release is
    an ordinary reading, and it is a different fact from zero.

    Only the shape is checked here. Whether `better` is a direction this build
    knows, and whether the numbers support the verdict argued from them, are the
    record's own rules and are answered where every other reader of them is
    (`assessment.parse`).
    """

    parsed = []
    for item in stated:
        fields = item.split(":")
        if len(fields) != 5:
            raise evolution_errors.BatchError(
                f"{item!r} is not a measurement: the shape is NAME:UNIT:BEFORE:AFTER:BETTER, five fields, with an "
                "empty BEFORE or AFTER where that cohort states none"
            )
        metric, unit, before, after, better = (field.strip() for field in fields)
        parsed.append(
            assessment.Measurement(
                metric=metric,
                unit=unit,
                before=_quantity(before, item),
                after=_quantity(after, item),
                better=better,
            )
        )
    return parsed


def _quantity(stated: str, item: str) -> float | None:
    """One side of a measurement: a number, or nothing at all."""

    if not stated:
        return None
    try:
        return float(stated)
    except ValueError:
        raise evolution_errors.BatchError(
            f"{item!r} states {stated!r} where a measurement holds a number; an empty field is how a cohort that "
            "measured nothing here is stated"
        ) from None


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
