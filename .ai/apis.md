---
last-updated: 2026-08-11
verified-against: 120f012b80e48cae8e529199ea88d0444a6814b6
---

# APIs and Interfaces

## `aii-2` CLI

| Command | Behavior |
|---|---|
| `deploy [--dry-run] [--bootstrap-orchestrator] <target>` | Preview or deploy canonical payload; optional venv bootstrap |
| `status <target>` | Compare current manifest entries, target, canonical source, eager entrypoints, and personal skill shadows; files dropped from canonical are not detected as target orphans yet |
| `status --all` | Check every local registry entry |
| `registry list [--json]` | Read machine-local managed repos |
| `registry add <target>` | Register a target with a readable manifest |
| `registry remove <name-or-path>` | Remove local tracking only |
| `evolution list [--feed-dir <path>]` | Inspect feed candidates without changing cursor, pool, ledger, artifacts, batches, or tasks |
| `evolution sync [--feed-dir <path>]` | Import, validate, hash, deduplicate, and audit eligible complete reports |
| `evolution status [--json]` | Derive schema-v6 phase, gate, experiment/round history, replay evidence, ref state, distinct revisions, and the last promotion/rollback |
| `evolution start [--force --justification <text>]` | Reconcile, sync, and freeze one batch when admission policy allows; force never waives the minimum |

All evolution operations are human-invoked and make no evaluation model call.
`list` and `status` are read-only; `start` returning no batch is a normal
successful outcome when policy is not met.

## Evolution Domain Operations

| Operation | Behavior |
|---|---|
| `replay.start` / `conclude` / `abandon` / `withdraw` | Persist/resume an idempotent exact-integration replay request, then record or retire its durable run state |
| `experiments.promote` | Prepare and record the exact replayed merge, compare-and-swap the source ref, and publish agreeing experiment/batch outcomes without implying deployment |
| `rollback.rollback` | Add and record a three-way inverse commit for the latest effective promotion when no later candidate lineage depends on it |

## Persisted Interfaces

| Interface | Ownership / semantics |
|---|---|
| `.ai-deploy-manifest.json` | Ignored target-local rendered receipt used by status |
| `.ai-deploy-lock.json` | Portable canonical hash/source revision receipt |
| `.registry/repos.local.json` | Ignored machine-local repo inventory |
| `orchestrator.toml` | Deployed defaults and named per-backend profiles |
| `orchestrator.py --print-config` | Machine-readable effective launch snapshot |
| `evolution/config.toml` | Versioned evolution admission/storage policy |
| `evolution/schemas/*.json` | Versioned import, batch, closure, experiment, replay, outcome, rollback, rejection, and ledger contracts |
| `.ai-evolution/state.json` | Ignored schema-v2 cursor, feed-exhaustion proof, pending/rejected/processed state |
| `.ai-evolution/imported-artifacts/` | Ignored normalized report records and raw L1/L2 artifact bodies |
| `evolution/batches/<id>/manifest.json` | Immutable committed cohort membership and evaluator/protocol provenance |
| `evolution/batches/<id>/analysis-complete.json` | Portable reviewed-analysis closure record |
| `evolution/batches/<id>/proposed-tasks/` | Inert change-task drafts retained after human admission copies them into `.ai-tasks/` |
| `evolution/batches/<id>/rejected-drafts.json` | Durable terminal decisions for declined draft identities and bytes |
| `evolution/experiments/<id>/experiment.json` | Versioned experiment identity/rounds/decision record; v2 explicitly carries nullable prepared-promotion state while frozen v1 remains readable |
| `evolution/experiments/<id>/replays.json` | Versioned per-experiment replay history: allocated withdrawals, optional pending request, and durable running/failed/completed attempts |
| `refs/evolution/experiments/<id>` | Durable fast-forward candidate ref; independent of the checked-out branch |
| `evolution/batches/<id>/outcome.json` | Terminal promoted/no-change batch outcome; a promotion carries the exact replayed merge unit and planned target names, never deployment state |
| `evolution/batches/<id>/rollback.json` | Prepared/completed inverse commit for the batch's promotion; leaves the outcome and experiment history unchanged |
| `evolution/ledger.jsonl` | Versioned sanitized append-only evolution audit |

## External Integration

The pending orch-hub global report feed is expected to supply globally ordered,
archived-task reports with durable complete L1+L2 artifacts. Eligibility does
not depend on evaluation trigger provenance. The implemented client uses an
opaque cursor, bearer token from `ORCH_HUB_TOKEN`, and base URL from
`ORCH_HUB_URL`; it follows no redirects, permits cleartext HTTP only for
loopback, and bounds every response read to 32 MiB. Until orch-hub publishes the
feed, `--feed-dir` provides deterministic offline imports against the same
`ReportFeed` boundary.

## API Conventions

- Validate path containment and schema versions before writes.
- Make dry-run/list/status read-only.
- Keep consumer cursors opaque; do not infer global order from scoped
  evaluation timestamps.
- Persist hashes with imported artifacts and frozen batch membership.
- Fail explicitly on malformed persisted state; do not silently reset cursors
  or drop pending reports.
