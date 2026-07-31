---
last-updated: 2026-07-31
verified-against: 1cc444eefee5e7d41cd94f7c01b661bf94c75152
---

# Features

| Feature | Status | Key modules/interfaces |
|---|---|---|
| Canonical payload deployment | implemented | deploy, manifest, lock, canonical buckets |
| Dry-run and drift status | implemented | preview/status; eager entrypoint checks |
| Managed repo registry | implemented | registry CLI and local JSON |
| Orchestrator bootstrap | implemented | Python 3.14 venv, requirements, non-overwriting env |
| Split role launch profiles | implemented | dev/review profiles, legacy profile compatibility, print-config |
| Cross-agent context loading | implemented | Claude imports, Cursor/Codex hooks, eager/lazy/delivered contract |
| Protocol boundary enforcement | implemented | charter layout/reference/eager/prompt lint |
| Temporary global Claude skills | implemented compatibility | parked backup + guarded sync |
| Evolution normative workspace | initialized | README, config, schemas, ledger, batch/case/experiment dirs |
| Evolution report feed | pending in orch-hub | global ordered, human-selected L1+L2 reports |
| Evolution import/pending pool | pending | active evolution controller task |
| Batch freeze and analysis-task creation | pending | active evolution controller task |
| Canary/replay automation | future | experiment records exist; executable support not implemented |

## Evolution Behavior Chain

```text
human chooses valuable tasks for L1+L2 evaluation
  → orch-hub exposes completed archived-task reports
  → human starts local evolution sync
  → validate/hash/dedupe into pending unique-task pool
  → threshold/age rule freezes immutable batch
  → pending analysis task cites evolution contract + batch
  → reviewed dispositions create zero or more admitted change tasks
  → candidate canary/replay → human promotion decision
```

No current component automatically selects or evaluates target tasks.
