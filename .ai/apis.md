---
last-updated: 2026-08-09
verified-against: ea0d02247214a3993b637809dadd816893ffecd3
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

Evolution commands are not implemented yet. The initialized contract reserves
human-triggered `evolution list|sync|start|status` behavior for the active
controller task.

## Persisted Interfaces

| Interface | Ownership / semantics |
|---|---|
| `.ai-deploy-manifest.json` | Ignored target-local rendered receipt used by status |
| `.ai-deploy-lock.json` | Portable canonical hash/source revision receipt |
| `.registry/repos.local.json` | Ignored machine-local repo inventory |
| `orchestrator.toml` | Deployed defaults and named per-backend profiles |
| `orchestrator.py --print-config` | Machine-readable effective launch snapshot |
| `evolution/config.toml` | Versioned evolution admission/storage policy |
| `evolution/schemas/*.json` | Normalized report, batch, ledger contracts |
| `.ai-evolution/state.json` | Future ignored discovery cursor and pending pool |
| `evolution/ledger.jsonl` | Versioned sanitized append-only evolution audit |

## External Integration

The pending orch-hub global report feed supplies globally ordered,
archived-task reports with durable complete L1+L2 artifacts. Eligibility does
not depend on evaluation trigger provenance. The evolution importer will use
an opaque cursor and protected bundle fetch; list/import operations must make
no evaluation model call. URL/token come from `ORCH_HUB_URL` and
`ORCH_HUB_TOKEN`, never committed config.

## API Conventions

- Validate path containment and schema versions before writes.
- Make dry-run/list/status read-only.
- Keep consumer cursors opaque; do not infer global order from scoped
  evaluation timestamps.
- Persist hashes with imported artifacts and frozen batch membership.
- Fail explicitly on malformed persisted state; do not silently reset cursors
  or drop pending reports.
