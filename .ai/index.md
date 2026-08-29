---
last-updated: 2026-08-29
verified-against: 3384626a0062dce990a5fcadf1f0e1474d6f3862
---

# AI Knowledge Router

Load only what the current task needs.

## Documents

| Document | File | Use when |
|---|---|---|
| Overview | overview.md | purpose, users, scope, non-goals, stack, repository shape |
| Architecture | architecture.md | ownership layers, deployment/session flows, evolution boundary |
| Design | design.md | invariants, tradeoffs, patterns, anti-patterns |
| Modules | modules.md | implementation and canonical payload ownership |
| APIs | apis.md | `mandrel`, orchestrator, files, and integration contracts |
| Features | features.md | capability status and cross-module behavior |
| Conventions | conventions.md | code/docs style, tests, git and deployment workflow |
| Map | map.md | feature-to-module-to-interface cross-reference |

## Routing Table

| Task Type | Load Order |
|---|---|
| Deployment/status change | map.md → features.md → modules.md → apis.md |
| Canonical protocol change | architecture.md → design.md → features.md → `evolution/README.md` when evidence-driven |
| Orchestrator change | map.md → modules.md → apis.md → `.mandrel/orchestrator/README.md` |
| Protocol evolution | architecture.md → design.md → features.md → `evolution/README.md` |
| API/CLI change | map.md → apis.md → modules.md → architecture.md |
| Bug/debugging | map.md → modules.md → code/tests |
| Code review | map.md → relevant docs → code/tests |
| Conventions question | conventions.md |
| Onboarding | overview.md → architecture.md → map.md |

## Domain Vocabulary

| Term | Meaning |
|---|---|
| Canonical payload | Versioned files under `canonical/` copied into targets |
| Canonical identity | Commit-derived map of deployed canonical path to Git blob id plus executable bit; shared equality key for status and release-assessment placement |
| Target repo | Managed project receiving the deployed protocol/runtime payload |
| Snapshot | Target-owned, version-controlled `.ai/` project memory |
| Taskfile | Local `.ai-tasks/<id>.md` lifecycle and cross-session handoff record |
| Deploy manifest | Ignored target-local rendered-file receipt used by `status` |
| Deploy lock | Portable committable canonical payload receipt |
| Runner protocol revision | Batch manifest's frozen full commit at the configured source line; distinct from each report's run provenance |
| Evolution report | Archived completed-task evaluation with durable complete L1+L2 artifacts |
| Evolution batch | Immutable cohort of unique completed-task reports analyzed together |
| Evolution experiment | One durable alternative attempted inside a batch, with its own ref, rounds, tasks, and terminal decision |
| Experiment round | Append-only revision pass; candidate-ready only after task completion is observed and the ref tip is sealed |
| Evolution revisions | Batch runner, batch base, moving candidate tip, sealed round candidate, source-line promotion, and per-target effective revision are distinct commits |
| Release assessment | First post-promotion cohort's durable reading of the prior release: derived provenance frame, pinned counterfactual, and retain/rollback settlement |
