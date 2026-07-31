---
last-updated: 2026-07-31
verified-against: c7c625c20f6d917c24bb586cc73e8c9bf2742490
---

# Architecture

## System Diagram

```text
ai-native-development/canonical (source of truth)
        │ aii-2 deploy/render/resolve/record
        ▼
target repo deployed payload + target-owned .ai/.ai-tasks
        │ Claude imports / Cursor+Codex hooks / orchestrator injection
        ▼
role-scoped coding sessions

archived completed-task L1+L2 evaluation
        │ orch-hub global report feed
        ▼
evolution runtime/import pool → frozen batch → analysis/change/canary
        │ reviewed canonical commit + human promotion
        └──────────────────────► canonical source of truth
```

## Ownership Layers

| Layer | Owner | State |
|---|---|---|
| Canonical contracts/runtime | This Git repository | `canonical/` |
| Deploy operation | `ai_native_deployment` | copy/render/resolve/record |
| Deployed payload | Deployment tool | target `.ai-protocol`, agent dirs, loader, orchestrator |
| Project memory/tasks | Target repository | target `.ai/`, `.ai-tasks/` |
| Local inventory/runtime | Machine operator | registry, manifests, venvs, secrets |
| Report scheduling/storage | orch-hub | complete L1+L2 artifacts and global feed |
| Evolution policy/tasks | This repository | `evolution/`, `.ai-tasks/` |

## Deployment Flow

1. `iter_deployment_items` maps canonical buckets to target paths and filters
   credentials/runtime files.
2. Target-specific templates and `CLAUDE.md` eager-memory entrypoints resolve.
3. Deployment overwrites deploy-owned files and writes target-local manifest,
   portable lock, managed gitignore block, and registry entry.
4. Optional bootstrap builds `.cursor/orchestrator/.venv`, installs
   requirements, and creates but never overwrites `.env`.
5. `status` compares manifest, target, current canonical source, and eager
   memory entrypoint invariants.

## Session Context Flow

- Eager: loader/conduct/meta schemas, `.ai` routing/map/core invariants, task
  index.
- Lazy: project modules/APIs/features loaded through `.ai/index.md`.
- Delivered: role contracts supplied by caller/invocation; never ambient.
- Claude uses static imports rendered at deploy; Cursor/Codex resolve eager
  memory dynamically in session-start hooks.

## Evolution Flow

The initialized workspace is policy/data scaffolding; executable import/batch
commands remain pending. Raw reports will live under ignored `.ai-evolution/`;
versioned `evolution/` keeps configuration, schemas, immutable manifests,
sanitized cases, experiment outcomes, and the audit ledger. Analysis and
canonical implementation are separate reviewed tasks, with runner revision
pinning and human canary promotion.

## External Dependencies

- Git for source revisions and target audit.
- Local filesystem and Python virtualenv tooling.
- Claude/Codex CLI logins or Cursor API key only for selected runtime backends.
- orch-hub protected report API for future evolution imports.

## Deployment

This repository can deploy/bootstrap itself for orchestration. Canonical edits
make deployed targets report `canonical changed` until explicitly redeployed.
