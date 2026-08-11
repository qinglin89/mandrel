---
last-updated: 2026-08-11
verified-against: 120f012b80e48cae8e529199ea88d0444a6814b6
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
evolution runtime/import pool → frozen batch → analysis/change/replay
        │ reviewed exact-tree promotion; optional inverse rollback
        └──────────────────────► canonical source line
```

## Ownership Layers

| Layer | Owner | State |
|---|---|---|
| Canonical contracts/runtime | This Git repository | `canonical/` |
| Deploy operation | `ai_native_deployment` | copy/render/resolve/record |
| Deployed payload | Deployment tool | target `.ai-protocol`, agent dirs, loader, orchestrator |
| Project memory/tasks | Target repository | target `.ai/`, `.ai-tasks/` |
| Local inventory/runtime | Machine operator | registry, manifests, venvs, secrets, `.ai-evolution/` |
| Report scheduling/storage | orch-hub | complete L1+L2 artifacts and global feed |
| Evolution policy/tasks | This repository | `evolution/`, `.ai-tasks/` |

## Deployment Flow

1. `iter_deployment_items` maps canonical buckets to target paths and filters
   credentials/runtime files.
2. Target-specific templates and `CLAUDE.md` eager-memory entrypoints resolve.
3. Deployment overwrites the deploy-owned files in the current canonical
   mapping and writes target-local manifest, portable lock, managed gitignore
   block, and registry entry. It does not prune files removed from that
   mapping; after the new manifest drops them, `status` cannot see the target
   orphans.
4. Optional bootstrap builds `.cursor/orchestrator/.venv`, installs
   requirements, and creates but never overwrites `.env`.
5. `status` compares manifest, target, current canonical source, eager-memory
   entrypoints, and personal-over-project skill precedence.

## Session Context Flow

- Eager: loader/conduct/meta schemas, `.ai` routing/map/core invariants, task
  index.
- Lazy: project modules/APIs/features loaded through `.ai/index.md`.
- Delivered: role contracts supplied by caller/invocation; never ambient.
- Claude uses static imports rendered at deploy; Cursor/Codex resolve eager
  memory dynamically in session-start hooks. Workflow skills deploy under
  target `.claude/skills/`: Claude uses project-native discovery, while
  Cursor, Codex, and the orchestrator use repository-local paths.

## Evolution Flow

`aii-2 evolution list|sync|status|start` is the human-triggered controller.
It consumes a protected orch-hub feed or deterministic local bundles, validates
and hashes complete L1+L2 reports, deduplicates them by completed source task,
and persists the cursor, drain proof, pending pool, rejections, and processed
claims under ignored `.ai-evolution/`. Admission freezes the eligible pool into
an immutable versioned manifest and generates one analysis-only task; closure
and proposed change-task drafts remain separate artifacts with a human admission
gate before implementation. Guarded domain operations then create one durable
experiment ref and record, admit task copies, append and seal candidate rounds,
run durable replays of the exact candidate/source integration tree, record
terminal experiment decisions, promote only that measured tree, and conclude a
batch with an outcome. A rollback adds an inverse commit to the latest effective
promotion without editing the terminal experiment or batch outcome.

One whole-lineage derivation governs both status and mutations: manifests,
closure, experiment/outcome/rejection records, durable refs, and Git determine
the current batch, open attempt, gate, round, and revisions from any checkout.
`.ai-tasks/` supplies local completion observations, never historical identity;
the audit ledger is not flow state. Per-experiment replay histories and prepared
promotions, batch outcomes/rollbacks, durable refs, and Git commits are the
release-decision state. Versioned `evolution/` holds safe policy, schemas,
manifests, lineage, sanitized cases, and audit while raw report content and
credentials stay ignored. Analysis, canonical implementation, replay,
promotion, deployment, and rollback remain separate reviewed/human-gated steps.

## External Dependencies

- Git for source revisions and target audit.
- Local filesystem and Python virtualenv tooling.
- Claude/Codex CLI logins or Cursor API key only for selected runtime backends.
- orch-hub protected report API for live evolution imports; the global feed is
  still pending, so deterministic local bundle import is the available path.

## Deployment

This repository can deploy/bootstrap itself for orchestration. Canonical edits
make deployed targets report `canonical changed` until explicitly redeployed.
