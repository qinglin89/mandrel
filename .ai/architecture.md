---
last-updated: 2026-08-15
verified-against: 712b34f89c6436a001dfff9b73d801715c4e51b0
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
        │ exact-tree promotion → next-cohort assessment/settlement
        │ retain or audited inverse rollback → next experiment base
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

1. `iter_deployment_items` maps canonical buckets to target paths, filters
   credentials/runtime files, and refuses two canonical inputs whose target
   paths are the same file under a host-independent Unicode canonical caseless
   identity; payload validity is portable rather than host-dependent.
2. Target-specific templates and `CLAUDE.md` eager-memory entrypoints resolve.
3. Deployment overwrites the deploy-owned files in the current canonical
   mapping and writes a target-local manifest of rendered hashes and applied
   modes, portable lock, managed gitignore block, and registry entry. The lock
   states a source revision only when the deployment's own captured input
   paths, bytes, and executable modes exactly match that commit's deployable
   canonical tree. It does not prune files
   removed from the mapping; after the new manifest drops them, `status` cannot
   see the target orphans.
4. Optional bootstrap builds `.cursor/orchestrator/.venv`, installs
   requirements, and creates but never overwrites `.env`.
5. `status` compares manifest-recorded content/modes with the target and current
   canonical source, plus eager-memory entrypoints and personal-over-project
   skill precedence. A legacy receipt with no modes cannot report in sync.

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

`aii-2 evolution` is the authoritative standalone human-triggered controller;
an optional Web surface consumes its JSON/actions rather than implementing a
second lifecycle.
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
promotion without editing the terminal experiment or batch outcome. The first
cohort after a promotion owns a release-assessment record derived from frozen
manifests, per-target effective revisions, and lineage; a pinned counterfactual
can settle its direction, then a human retain/rollback decision selects the line
the next experiment base must contain. The obligation remains with that cohort
if it concludes before answering and stays repeatable for recovery. Replay and
counterfactual harness crossings are operator-stated, durable two-step
request/run exchanges; promotion remains separate from deployment.

One whole-lineage derivation governs both status and mutations: manifests,
closure, experiment/outcome/rejection records, durable refs, and Git determine
the current batch, open attempt, gate, round, and revisions from any checkout.
`.ai-tasks/` supplies local completion observations, never historical identity;
the audit ledger is not flow state. Per-experiment replay histories and prepared
promotions, batch outcomes/rollbacks, release assessments, durable refs, and Git
commits are the release-decision state. Schema-v7 status adds a digest of those
authoritative inputs, per-verb allowed/refused/recovery actions, and a
machine-local reading of planned targets' validated deploy locks; the deployment
reading neither enters the digest nor gates lifecycle actions. Versioned `evolution/` holds safe policy, schemas,
manifests, lineage, sanitized cases, and audit while raw report content and
credentials stay ignored. Analysis, canonical implementation, replay,
promotion, deployment, and rollback remain separate reviewed/human-gated steps.

## External Dependencies

- Git for source revisions and target audit.
- Local filesystem and Python virtualenv tooling.
- Claude/Codex CLI logins or Cursor API key only for selected runtime backends.
- orch-hub protected catalog and byte-exact artifact API for live evolution
  imports; deterministic local bundles remain the offline/replay path.

## Deployment

This repository can deploy/bootstrap itself for orchestration. Canonical edits
make deployed targets report `canonical changed` until explicitly redeployed.
