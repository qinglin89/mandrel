---
last-updated: 2026-09-11
verified-against: 1ac17fed46680731906681413c8c7499f35e81c4
---

# Project Map

## Feature → Module → Interface

| Feature | Modules | Interfaces |
|---|---|---|
| Deploy/upgrade | cli, deploy, manifest, lockfile, hashing, paths | `mandrel deploy`, manifest, lock |
| Drift/readiness | deploy, placement, manifest, lockfile, registry, evolution/deployment | `mandrel status [--all] [--json]`; target integrity plus canonical-identity placement at base/sealed candidate; versioned machine findings labelled by origin plus each target's placed line |
| Registry | registry, paths, cli | `mandrel registry ...`, local registry JSON |
| Orchestrator bootstrap | deploy, cli, canonical/orchestrator | deploy bootstrap flags, `.mandrel/orchestrator/` venv/env |
| Role launch policy | canonical orchestrator config/resolver | profile flags, `--print-config` |
| Context delivery | canonical loader, Claude/Cursor/Codex hooks | imports, sessionStart/Stop hooks |
| Workflow lifecycle | canonical protocols/workflow/meta | role contracts, runbook, task/memory schemas |
| Boundary assurance | boundary lint, deployment/hook tests | shell lint, pytest |
| Unified verification | check script, hook installer, CI workflows | `scripts/check.sh`, optional pre-push hook, GitHub Actions |
| Workflow skills | deploy, paths, canonical Claude skills | deploy manifest/lock, `status` shadow check, backend closeout pointers |
| Evolution policy | evolution contract/config/schemas/ledger | versioned policy, manifests, closures, cases, experiments, audit |
| Evolution controller | evolution package, CLI | complete standalone `mandrel evolution` verb set, ignored runtime pool/artifacts/proposal, canonical-identity selection and human membership window, schema-v11 next-action status, schema-v1 portfolio index/bounded detail with durable and observation revisions, typed recovery-specific refusal codes, schema-v1 JSON non-completion envelope |
| Evolution change lineage | lineage/experiment/guard services, evolution policy | versioned experiment/outcome/rejection records, durable refs, guarded admission/round/decision operations |
| Evolution replay/releases | replay/rollback/revision/harness services, evolution policy | operator-stated replay histories, prepared promotion, exact merge unit, source-line inverse commits, rollback interruption recovery, and base-keyed `rolled-back` batch ending; phase JSON v11 |
| Release effectiveness gate | assessment/lineage/experiment/rollback/harness services, lockfile identity, evolution policy | Canonical-identity sides/exclusions with unstated/differing facet failures, CLI-visible assessment recommendation, pinned counterfactual, human retain/rollback settlement, retained-base admission or rollback ending |
| Evolution deployment visibility | deployment/lockfile/registry/revision services | planned target names beside validated machine-local deploy-lock placement states; observation is outside lifecycle state |
| Report publication | external orch-hub, hub client | published catalog/raw-artifact feed, import-record translation, manual live probe |

## Change Impact

| Change | Check/update |
|---|---|
| Canonical file/path | payload mapping, init target-surface exclusions, boundary lint, deploy/status tests, targets |
| Deploy receipt/readiness | lock-before-manifest publication boundary, canonical identity at base/sealed candidate, status/docs tests |
| Workflow skill | manifest/lock coverage, backend pointers, personal-shadow status, operator deploy-before-cleanup order |
| Eager memory topic | loader rendering, both hooks, entrypoint drift tests |
| Persisted receipt/schema | reader compatibility, schema version, recovery tests |
| Orchestrator profile/config | README, print-config JSON, precedence tests, orch-hub contract |
| Repository check/tool | `scripts/check.sh`, `dev` extra, structural gate tests; CI/hooks call only the gate |
| Evolution admission/state | evolution contract/config/schema, ledger/batch invariants, `.ai` feature/API snapshot |
| Release assessment/settlement | assessment schema/reader, replay and rollback boundaries, experiment-base gate, ledger vocabulary, `.ai` feature/API snapshot |
| orch-hub report contract | hub client, import schema/client fixtures, manual live probe, task acceptance |
