---
last-updated: 2026-08-12
verified-against: 19a786f4595f18d5901556ed32dfea5e9da6d0ba
---

# Features

| Feature | Status | Key modules/interfaces |
|---|---|---|
| Canonical payload deployment | implemented | deploy, manifest, lock, canonical buckets |
| Dry-run and drift status | implemented; removed-file orphan detection pending | preview/status; eager-entrypoint and personal-skill-precedence checks |
| Managed repo registry | implemented | registry CLI and local JSON |
| Orchestrator bootstrap | implemented | Python 3.14 venv, requirements, non-overwriting env |
| Split role launch profiles | implemented | dev/review profiles, legacy profile compatibility, print-config |
| Cross-agent context loading | implemented | Claude imports, Cursor/Codex hooks, eager/lazy/delivered contract |
| Protocol boundary enforcement | implemented | charter layout/reference/eager/prompt lint |
| Unified regression/CI gate | implemented | `scripts/check.sh`; deterministic lint/test/build matrix; optional pre-push hook; manual live probe |
| Repository-local workflow skills | implemented | canonical Claude skill payload; manifest/lock coverage; cross-backend closeout pointers |
| Evolution normative workspace | initialized | README, config, schemas, ledger, batch/case/experiment dirs |
| Evolution report feed | pending in orch-hub | globally ordered archived-task reports with complete L1+L2 artifacts |
| Evolution import/pending pool | implemented | protected/file feeds, validated bundles, ignored state/artifacts, sanitized ledger |
| Batch freeze and analysis-task creation | implemented | admission policy, immutable manifests, closure records, generated task/index |
| Evolution lifecycle CLI | implemented | `aii-2 evolution list|sync|status|start`; human and JSON phase rendering |
| Evolution experiment lineage | implemented | grouped draft gate, durable refs/records, append-only rounds, candidate seal, terminal decisions, promotion/no-change outcomes, phase JSON v6 |
| Replay and release decisions | implemented | durable replay requests/results, exact integration-tree promotion, latest-effective inverse rollback; execution remains explicit and human-triggered |
| Cross-batch release assessment | domain implemented; CLI/status exposure pending | derived provenance cohorts/exclusions, durable assessment and counterfactual, retain/rollback settlement, next-base gate |

## Evolution Behavior Chain

```text
archived tasks acquire complete L1+L2 evaluation artifacts
  → orch-hub exposes eligible completed reports
  → human starts local evolution sync
  → validate/hash/dedupe into pending unique-task pool
  → threshold/age rule freezes immutable batch
  → pending analysis task cites evolution contract + batch and reads the prior release
  → optional pinned before/after counterfactual → human retain/rollback settlement
  → reviewed dispositions create inert change-task drafts
  → human admission creates an experiment on the settlement-selected base
  → append-only task rounds seal an exact candidate revision
  → exact integration replay → human exact-tree promotion or no-change
  → the next eligible cohort repeats the release-effectiveness gate
```

The evolution controller is independent of evaluation trigger policy and only
consumes already-complete candidate reports.
