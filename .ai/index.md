---
last-updated: 2026-08-11
verified-against: 6af4fd1d487bb0ad1873c6825df5fe5d31d13139
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
| APIs | apis.md | `aii-2`, orchestrator, files, and integration contracts |
| Features | features.md | capability status and cross-module behavior |
| Conventions | conventions.md | code/docs style, tests, git and deployment workflow |
| Map | map.md | feature-to-module-to-interface cross-reference |

## Routing Table

| Task Type | Load Order |
|---|---|
| Deployment/status change | map.md → features.md → modules.md → apis.md |
| Canonical protocol change | architecture.md → design.md → features.md → `evolution/README.md` when evidence-driven |
| Orchestrator change | map.md → modules.md → apis.md → `.cursor/orchestrator/README.md` |
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
| Target repo | Managed project receiving the deployed protocol/runtime payload |
| Snapshot | Target-owned, version-controlled `.ai/` project memory |
| Taskfile | Local `.ai-tasks/<id>.md` lifecycle and cross-session handoff record |
| Deploy manifest | Ignored target-local rendered-file receipt used by `status` |
| Deploy lock | Portable committable canonical payload receipt |
| Runner protocol revision | Stable protocol version governing one run |
| Evolution report | Archived completed-task evaluation with durable complete L1+L2 artifacts |
| Evolution batch | Immutable cohort of unique completed-task reports analyzed together |
| Evolution experiment | One durable alternative attempted inside a batch, with its own ref, rounds, tasks, and terminal decision |
| Experiment round | Append-only revision pass; candidate-ready only after task completion is observed and the ref tip is sealed |
| Evolution revisions | Batch base, moving candidate tip, sealed round candidate, source-line promotion, and per-target effective revision are distinct commits |
