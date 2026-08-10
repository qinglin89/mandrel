---
last-updated: 2026-08-10
verified-against: 6f16f6e9c63ae0eb4bb1ad37bb54a914818a3f63
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
| `evolution status [--json]` | Derive lifecycle phase plus baseline/candidate revisions from on-disk state and Git |
| `evolution start [--force --justification <text>]` | Reconcile, sync, and freeze one batch when admission policy allows; force never waives the minimum |

All evolution operations are human-invoked and make no evaluation model call.
`list` and `status` are read-only; `start` returning no batch is a normal
successful outcome when policy is not met.

## Persisted Interfaces

| Interface | Ownership / semantics |
|---|---|
| `.ai-deploy-manifest.json` | Ignored target-local rendered receipt used by status |
| `.ai-deploy-lock.json` | Portable canonical hash/source revision receipt |
| `.registry/repos.local.json` | Ignored machine-local repo inventory |
| `orchestrator.toml` | Deployed defaults and named per-backend profiles |
| `orchestrator.py --print-config` | Machine-readable effective launch snapshot |
| `evolution/config.toml` | Versioned evolution admission/storage policy |
| `evolution/schemas/*.json` | Versioned import, batch v1/v2, closure, and ledger contracts |
| `.ai-evolution/state.json` | Ignored schema-v2 cursor, feed-exhaustion proof, pending/rejected/processed state |
| `.ai-evolution/imported-artifacts/` | Ignored normalized report records and raw L1/L2 artifact bodies |
| `evolution/batches/<id>/manifest.json` | Immutable committed cohort membership and evaluator/protocol provenance |
| `evolution/batches/<id>/analysis-complete.json` | Portable reviewed-analysis closure record |
| `evolution/batches/<id>/proposed-tasks/` | Inert change-task drafts awaiting human move/admission into `.ai-tasks/` |
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
