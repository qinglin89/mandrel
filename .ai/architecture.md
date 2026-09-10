---
last-updated: 2026-09-10
verified-against: 20775f411888a13c341c41c23d1150ffa456cb1f
---

# Architecture

## System Diagram

```text
mandrel/canonical (source of truth)
        │ mandrel deploy/render/resolve/record
        ▼
target repo deployed payload + target-owned .ai/.ai-tasks
        │ Claude imports / Cursor+Codex hooks / orchestrator injection
        ▼
role-scoped coding sessions

archived completed-task L1+L2 evaluation
        │ orch-hub global report feed
        ▼
evolution runtime/import pool → frozen batch → analysis/change/replay
        │ exact-tree promotion → next-batch assessment/settlement
        │ retain or audited inverse rollback → next experiment base
        └──────────────────────► canonical source line
```

## Ownership Layers

| Layer | Owner | State |
|---|---|---|
| Canonical contracts/runtime | This Git repository | `canonical/` |
| Deploy operation | `mandrel` | copy/render/resolve/record |
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
   mapping and writes a managed gitignore block, portable lock, then target-local
   manifest of rendered hashes and applied modes, followed by the registry entry.
   Lock-before-manifest publication makes an interrupted pair leave a stale
   manifest that reports target drift rather than a stale lock that silently
   misplaces the new payload. The lock
   states a source revision only when the deployment's own captured input
   paths, bytes, and executable modes exactly match that commit's deployable
   canonical tree. It does not prune files
   removed from the mapping; after the new manifest drops them, `status` cannot
   see the target orphans.
4. Optional bootstrap builds `.mandrel/orchestrator/.venv`, installs
   requirements, and creates but never overwrites `.env`.
5. `status` compares manifest-recorded content/modes with the target, then places
   the lock's source commit where its deployable canonical identity equals the
   configured base or latest sealed candidate. Identity is derived from each
   commit's deployed canonical paths, blob ids, and executable bits; it never
   reads the working tree or a stored digest. Missing, null, or unreadable locks
   are unplaceable; eager-memory entrypoints and
   personal-over-project skill precedence remain separate checks. A legacy
   manifest with no modes cannot report in sync. A sweep resolves the lines
   once: no evolution workspace means default `refs/heads/main` base only; an
   unreadable present policy names no line; unreadable lineage keeps base and
   drops candidate.

## Session Context Flow

- Eager: loader/conduct/meta schemas, `.ai` routing/map/core invariants, task
  index.
- Lazy: project modules/APIs/features loaded through `.ai/index.md`.
- Delivered: role contracts supplied by caller/invocation; never ambient.
- Claude uses static imports rendered at deploy; Cursor/Codex resolve eager
  memory dynamically in session-start hooks. Workflow skills deploy under
  target `.claude/skills/`: Claude uses project-native discovery, while
  Cursor, Codex, and the orchestrator use repository-local paths.
- `jq` failure differs by adapter: Claude/Cursor Stop hooks error and Cursor
  session-start emits no context; Codex session-start emits no context and Stop
  fails open silently. Claude's static eager imports remain available.

## Evolution Flow

`mandrel evolution` is the authoritative standalone human-triggered controller;
an optional Web surface consumes its JSON/actions rather than implementing a
second lifecycle.
One fully-qualified configured source line supplies the manifest-owned batch
base/runner commit and replay input. The first experiment reads that frozen
manifest base; release settlement gates its admission and checks the base
against the selected line rather than resolving a second commit. Promotion and
rollback instead use their persisted evidence, so a policy edit can neither
redirect a measured merge nor its inverse.
It consumes a protected orch-hub feed or deterministic local bundles, validates
and hashes complete L1+L2 reports, deduplicates them by completed source task,
and persists the cursor, drain proof, pending pool, rejections, processed claims,
and machine-local batch proposal under ignored `.ai-evolution/`. `start` selects
the proposal by equality between each pooled task's report-set protocol identity
and the source-line base; a human may record an include/exclude judgement before
`freeze` publishes the immutable manifest, claims reports, and generates one
analysis-only task. Admission thresholds and age measure the proposal, while the
manifest preserves its selected/excluded denominator and attribution. Closure
and proposed change-task drafts remain separate artifacts with a human admission
gate before implementation. Guarded domain operations then create one durable
experiment ref and record, admit task copies, append and seal candidate rounds,
run durable replays of the exact candidate/source integration tree, record
terminal experiment decisions, promote only that measured tree, and conclude a
batch with an outcome. A rollback adds an inverse commit to the latest effective
promotion without editing that promotion's terminal experiment or batch outcome;
it also writes a `rolled-back` outcome for the zero-attempt current batch whose
base carries the reversed release, spending that batch's claimed reports. The
first batch after a promotion owns a release-assessment record derived from
frozen manifests, per-target effective revisions, and lineage; a pinned
counterfactual can settle its direction, and the reading records a retain/rollback
recommendation before a human decision either releases admission or rolls back
and ends the standing batch. The obligation remains with that batch
if it concludes before answering and stays repeatable for recovery. Replay and
counterfactual harness crossings are operator-stated, durable two-step
request/run exchanges; promotion remains separate from deployment.

One whole-lineage derivation governs both status and mutations: manifests,
closure, experiment/outcome/rejection records, durable refs, and Git determine
the current batch, open attempt, gate, round, and revisions from any checkout.
Release-assessment sides use the same canonical identity as deploy status:
`before` is checked first, an unreadable `before` prevents an `after` placement,
and a revision matching neither side is excluded rather than ancestry-sorted.
`.ai-tasks/` supplies local completion observations, never historical identity;
the audit ledger is not flow state. Per-experiment replay histories and prepared
promotions, batch outcomes/rollbacks, release assessments, durable refs, and Git
commits are the release-decision state. Schema-v11 status adds a digest of those
authoritative inputs and their named ref tips, including the configured source
line and batch proposal but excluding the operator's `HEAD`, plus per-verb
allowed/refused/recovery actions and a machine-local reading of planned targets'
validated deploy locks; the deployment reading neither enters the digest nor
gates lifecycle actions. Its release block distinguishes missing facet evidence
from genuine side differences and exposes the reading's recommendation.
Versioned `evolution/` holds safe policy, schemas,
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
alone do not change readiness; `deploy --dry-run` previews their target effect.
When the configured base advances to a different deployable canonical identity,
a target on the prior protocol is off the source line until explicitly
redeployed; an intervening commit that changes no deployed canonical content
does not move it off-line.
Current orchestrator runtime lives under `.mandrel/orchestrator/`. A target
migrated from the former layout intentionally retains only
`.cursor/orchestrator/logs/`: historical orch-hub run records keep their
persisted `log_dir` and are not rewritten to the current deployment path.
