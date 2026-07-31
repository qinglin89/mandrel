---
last-updated: 2026-07-31
verified-against: 1cc444eefee5e7d41cd94f7c01b661bf94c75152
---

# Project Map

## Feature → Module → Interface

| Feature | Modules | Interfaces |
|---|---|---|
| Deploy/upgrade | cli, deploy, manifest, lockfile, hashing, paths | `aii-2 deploy`, manifest, lock |
| Drift/readiness | deploy, manifest, registry | `aii-2 status [--all]` |
| Registry | registry, paths, cli | `aii-2 registry ...`, local registry JSON |
| Orchestrator bootstrap | deploy, cli, canonical/orchestrator | deploy bootstrap flags, venv/env |
| Role launch policy | canonical orchestrator config/resolver | profile flags, `--print-config` |
| Context delivery | canonical loader, Claude/Cursor/Codex hooks | imports, sessionStart/Stop hooks |
| Workflow lifecycle | canonical protocols/workflow/meta | role contracts, runbook, task/memory schemas |
| Boundary assurance | boundary lint, deployment/hook tests | shell lint, pytest |
| Skill compatibility | skills module, skills-backup | `skills sync-claude-global` |
| Evolution policy | evolution contract/config/schemas/ledger | versioned files; no executable CLI yet |
| Evolution controller | pending Python module/CLI | planned `evolution list|sync|start|status` |
| Report publication | external orch-hub | pending global report feed/export |

## Change Impact

| Change | Check/update |
|---|---|
| Canonical file/path | payload mapping, boundary lint, deploy/status tests, targets |
| Eager memory topic | loader rendering, both hooks, entrypoint drift tests |
| Persisted receipt/schema | reader compatibility, schema version, recovery tests |
| Orchestrator profile/config | README, print-config JSON, precedence tests, orch-hub contract |
| Evolution admission/state | evolution contract/config/schema, ledger/batch invariants, `.ai` feature/API snapshot |
| orch-hub report contract | import schema/client fixtures and task acceptance |
