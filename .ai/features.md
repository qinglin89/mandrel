---
last-updated: 2026-08-31
verified-against: ed5c3aeb9a077845ee1a7d13c002da3818377d0c
---

# Features

| Feature | Status | Key modules/interfaces |
|---|---|---|
| Canonical payload deployment | implemented | deploy, manifest, lock, canonical buckets |
| Dry-run and drift status | implemented; removed-file orphan detection pending | preview compares current canonical payload; status checks target receipt integrity plus canonical-identity base/latest-sealed-candidate placement, eager entrypoints, and personal-skill precedence; single/sweep schema-v2 JSON labels each finding by origin, structures negative placement facts, and states each target's placed line |
| Managed repo registry | implemented | registry CLI and local JSON |
| Orchestrator bootstrap | implemented | `.mandrel/orchestrator/`; Python 3.14 venv, requirements, non-overwriting env |
| Split role launch profiles | implemented | dev/review profiles, legacy profile compatibility, print-config |
| Cross-agent context loading | implemented | Claude imports, Cursor/Codex hooks, eager/lazy/delivered contract |
| Protocol boundary enforcement | implemented | charter layout/reference/eager/prompt lint |
| Unified regression/CI gate | implemented | `scripts/check.sh`; deterministic lint/test/build matrix; optional pre-push hook; manual live probe |
| Repository-local workflow skills | implemented | canonical Claude skill payload; manifest/lock coverage; cross-backend closeout pointers |
| Evolution normative workspace | initialized | README, config, schemas, ledger, batch/case/experiment dirs |
| Evolution report feed | published; client reconciled; complete protocol identity live-proven | orch-hub catalog/raw-artifact routes, `ReportFeed`, atomic pair translation in `hub.py`, manual probe |
| Evolution import/pending pool | implemented | protected/file feeds, validated bundles, ignored state/artifacts, sanitized ledger |
| Batch proposal, human membership window, freeze and analysis-task creation | implemented | canonical-identity selection, proposal-based admission, attributed include/exclude decisions, immutable manifest denominator, closure records, generated task/index |
| Evolution lifecycle CLI | implemented | complete standalone `mandrel evolution` verb set; schema-v11 human/JSON status, stale-state token, allowed/refused/recovery actions; README operator contract |
| Evolution experiment lineage | implemented | grouped draft gate, durable refs/records, append-only rounds, candidate seal, terminal decisions, promoted/no-change/rolled-back outcomes; a landed rollback ends the zero-attempt batch whose base carries the reversed release, with public shared refusals aligning operations and console recovery |
| Replay and release decisions | implemented | operator-stated durable replay requests/results, exact integration-tree promotion, latest-effective inverse rollback; execution remains explicit and human-triggered |
| Cross-batch release assessment | implemented | canonical-identity before/after cohorts and clone-dependent exclusions; persisted/JSON `unstated` versus `differing` facet failures; CLI-visible durable assessment/counterfactual with retain/rollback recommendation; human settlement and next-base gate |
| Evolution deployment visibility | implemented | planned target names kept separate from validated machine-local deploy-lock placement states; observation gates no lifecycle verb |

## Evolution Behavior Chain

```text
archived tasks acquire complete L1+L2 evaluation artifacts
  → orch-hub exposes eligible completed reports
  → human starts the local evolution poll
  → validate/hash/dedupe into pending unique-task pool
  → canonical-identity selection → base-keyed proposal
  → optional attributed include/exclude window
  → threshold/age rule over the proposal → human freezes immutable batch
  → pending analysis task cites evolution contract + batch and reads the prior release
  → optional pinned before/after counterfactual → human retain releases admission | rollback ends the standing batch and spends its evidence
  → retained batch's reviewed dispositions create inert change-task drafts
  → human admission creates an experiment on the settlement-selected base
  → append-only task rounds seal an exact candidate revision
  → exact integration replay → human exact-tree promotion or no-change
  → the next eligible cohort repeats the release-effectiveness gate
```

The evolution controller is independent of evaluation trigger policy and only
consumes already-complete candidate reports.
