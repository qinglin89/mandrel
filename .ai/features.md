---
last-updated: 2026-08-10
verified-against: 6f16f6e9c63ae0eb4bb1ad37bb54a914818a3f63
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
| Canary/replay automation | future | experiment records exist; executable support not implemented |

## Evolution Behavior Chain

```text
archived tasks acquire complete L1+L2 evaluation artifacts
  → orch-hub exposes eligible completed reports
  → human starts local evolution sync
  → validate/hash/dedupe into pending unique-task pool
  → threshold/age rule freezes immutable batch
  → pending analysis task cites evolution contract + batch
  → reviewed dispositions create zero or more admitted change tasks
  → candidate canary/replay → human promotion decision
```

The evolution controller is independent of evaluation trigger policy and only
consumes already-complete candidate reports.
