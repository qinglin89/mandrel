---
last-updated: 2026-08-11
verified-against: 120f012b80e48cae8e529199ea88d0444a6814b6
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
| Unified verification | check script, hook installer, CI workflows | `scripts/check.sh`, optional pre-push hook, GitHub Actions |
| Workflow skills | deploy, paths, canonical Claude skills | deploy manifest/lock, `status` shadow check, backend closeout pointers |
| Evolution policy | evolution contract/config/schemas/ledger | versioned policy, manifests, closures, cases, experiments, audit |
| Evolution controller | evolution package, CLI | `aii-2 evolution list|sync|status|start`, ignored runtime pool/artifacts, derived phase JSON v6 |
| Evolution change lineage | lineage/experiment/guard services, evolution policy | versioned experiment/outcome/rejection records, durable refs, guarded admission/round/decision operations |
| Evolution replay/releases | replay/rollback/revision services, evolution policy | replay histories, prepared promotion, exact merge unit, batch rollback, source-line commits, phase JSON v6 |
| Report publication | external orch-hub | pending global report feed/export |

## Change Impact

| Change | Check/update |
|---|---|
| Canonical file/path | payload mapping, boundary lint, deploy/status tests, targets |
| Workflow skill | manifest/lock coverage, backend pointers, personal-shadow status, operator deploy-before-cleanup order |
| Eager memory topic | loader rendering, both hooks, entrypoint drift tests |
| Persisted receipt/schema | reader compatibility, schema version, recovery tests |
| Orchestrator profile/config | README, print-config JSON, precedence tests, orch-hub contract |
| Repository check/tool | `scripts/check.sh`, `dev` extra, structural gate tests; CI/hooks call only the gate |
| Evolution admission/state | evolution contract/config/schema, ledger/batch invariants, `.ai` feature/API snapshot |
| orch-hub report contract | import schema/client fixtures and task acceptance |
