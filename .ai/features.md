---
last-updated: 2026-08-15
verified-against: 712b34f89c6436a001dfff9b73d801715c4e51b0
---

# Features

| Feature | Status | Key modules/interfaces |
|---|---|---|
| Canonical payload deployment | implemented | deploy, manifest, lock, canonical buckets |
| Dry-run and drift status | implemented; removed-file orphan detection pending | preview/status content+mode checks; eager-entrypoint and personal-skill-precedence checks |
| Managed repo registry | implemented | registry CLI and local JSON |
| Orchestrator bootstrap | implemented | Python 3.14 venv, requirements, non-overwriting env |
| Split role launch profiles | implemented | dev/review profiles, legacy profile compatibility, print-config |
| Cross-agent context loading | implemented | Claude imports, Cursor/Codex hooks, eager/lazy/delivered contract |
| Protocol boundary enforcement | implemented | charter layout/reference/eager/prompt lint |
| Unified regression/CI gate | implemented | `scripts/check.sh`; deterministic lint/test/build matrix; optional pre-push hook; manual live probe |
| Repository-local workflow skills | implemented | canonical Claude skill payload; manifest/lock coverage; cross-backend closeout pointers |
| Evolution normative workspace | initialized | README, config, schemas, ledger, batch/case/experiment dirs |
| Evolution report feed | published; client reconciled; complete protocol identity live-proven | orch-hub catalog/raw-artifact routes, `ReportFeed`, atomic pair translation in `hub.py`, manual probe |
| Evolution import/pending pool | implemented | protected/file feeds, validated bundles, ignored state/artifacts, sanitized ledger |
| Batch freeze and analysis-task creation | implemented | admission policy, immutable manifests, closure records, generated task/index |
| Evolution lifecycle CLI | implemented | complete standalone `aii-2 evolution` verb set; schema-v7 human/JSON status, stale-state token, allowed/refused/recovery actions; README operator contract |
| Evolution experiment lineage | implemented | grouped draft gate, durable refs/records, append-only rounds, candidate seal, terminal decisions, promotion/no-change outcomes, phase JSON v7 |
| Replay and release decisions | implemented | operator-stated durable replay requests/results, exact integration-tree promotion, latest-effective inverse rollback; execution remains explicit and human-triggered |
| Cross-batch release assessment | implemented | derived provenance cohorts/exclusions, CLI-visible durable assessment/counterfactual, retain/rollback settlement, next-base gate |
| Evolution deployment visibility | implemented | planned target names kept separate from validated machine-local deploy-lock placement states; observation gates no lifecycle verb |

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
