---
last-updated: 2026-08-11
verified-against: 6af4fd1d487bb0ad1873c6825df5fe5d31d13139
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
| Evolution experiment lineage | implemented | grouped draft gate, durable refs/records, append-only rounds, candidate seal, terminal decisions, batch no-change outcome, phase JSON v3 |
| Canary/replay automation | future | candidate-ready rounds exist; replay, promotion, and rollback execution are not implemented |

## Evolution Behavior Chain

```text
archived tasks acquire complete L1+L2 evaluation artifacts
  → orch-hub exposes eligible completed reports
  → human starts local evolution sync
  → validate/hash/dedupe into pending unique-task pool
  → threshold/age rule freezes immutable batch
  → pending analysis task cites evolution contract + batch
  → reviewed dispositions create inert change-task drafts
  → human admission creates an experiment on the batch's frozen base
  → append-only task rounds seal an exact candidate revision
  → candidate canary/replay → human promotion or terminal no-change decision
```

The evolution controller is independent of evaluation trigger policy and only
consumes already-complete candidate reports.
