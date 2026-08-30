---
last-updated: 2026-08-31
verified-against: dceee9345be5e9201fc7a8a71cc2ef7692b42a3b
---

# Modules

## Module Index

| Module | Location | Responsibility |
|---|---|---|
| CLI | `mandrel/cli.py`, `mandrel` | Parse/dispatch deploy, status, registry, and evolution commands |
| Deployment | `mandrel/deploy.py` | Map/filter/render/copy current canonical files; preview/bootstrap; target-receipt content/mode, entrypoint, skill-precedence, and source-line drift checks; schema-v1 status JSON projection with drift origins and placement reasons; removed-file orphan detection/pruning pending |
| Manifest/lock | `manifest.py`, `lockfile.py`, `hashing.py` | Local rendered-content/mode receipt, portable canonical-revision receipt, hashes, and validated supported-schema/full-object-id reading |
| Deployment placement | `mandrel/lockfile.py`, `mandrel/placement.py`, `mandrel/evolution/deployment.py` | One mode-aware canonical-tree identity and cached resolver; status compares lock/base/candidate identities with fail-closed degradation, while evolution's machine-local per-target observation keeps promotion/rollback ancestry |
| Registry | `registry.py`, `paths.py` | Machine-local managed-repo inventory and path constants |
| Workflow skills | `canonical/claude/skills/` | Versioned per-target skills; Claude native discovery and repo-local paths for other backends |
| Protocol contracts | `canonical/protocols/` | Conduct and role-specific behavioral contracts |
| Workflow/meta | `canonical/workflow/`, `canonical/meta/` | Caller runbook/rolemapping and memory/task/init schemas |
| Agent adapters | `canonical/claude/`, `cursor/`, `codex/`, `repo-root/` | Tool-specific loading/hooks/config and root loader |
| Orchestrator | `canonical/orchestrator/` | Configurable dev/review state machine and execution backends |
| Boundary lint | `scripts/boundary-lint.sh` | Mechanical charter, reference, eager-channel, prompt checks |
| Verification gate | `scripts/check.sh`, `scripts/install-git-hooks.sh`, `.github/workflows/` | Single deterministic local/CI/hook entrypoint; manual credentialed probe path |
| Evolution policy | `evolution/` | Normative workflow, config, schemas, batches/cases/experiments/ledger |
| Evolution controller | `mandrel/evolution/batches.py`, `proposals.py`, `manifests.py`, `state.py`, `phase.py`, `render.py`; `mandrel/cli.py` | Canonical-identity proposal selection and human window, immutable semantic manifest publication, schema-v10 whole-lifecycle JSON/human status, state revision, per-verb allowed/refused/recovery projection, and standalone CLI routing |
| Evolution lineage | `mandrel/evolution/lineage.py`, `experiments.py`, `guards.py` | Whole-history derivation; shared durable-state guards; experiment refs/records; admission, round, terminal-decision, promotion, and batch-outcome operations |
| Evolution replay/releases | `mandrel/evolution/replay.py`, `rollback.py`, `revisions.py` | Durable replay request/run histories; exact integration trees; source-ref promotion helpers; latest-effective inverse rollback and Git/ref recovery |
| Evolution release assessment | `mandrel/evolution/assessment.py`, `lockfile.py`, `experiments.py` | Canonical-identity before/after cohorts and visible exclusions; assessment formation/read validation; pinned counterfactual recovery; retain/rollback settlement; first-base sequencing |
| Evolution stated harness | `mandrel/evolution/harness.py` | Operator-stated replay/counterfactual starts and polls; completed-attempt reproduction guard |
| Evolution deployment reading | `mandrel/evolution/deployment.py`, `placement.py` | Add promotion/rollback ancestry to deploy-layer target revisions; preserve unregistered/unreadable/unplaceable states without gating the lifecycle |

## Boundary Rules

- Deployment never creates or mutates target `.ai/` or `.ai-tasks/`.
- Registry discovery does not imply deployment readiness.
- Status reads target integrity from the manifest and release placement from the lock; it does not repair drift or compare the working canonical tree.
- Canonical protocol contracts contain no caller/orchestrator dispatch details.
- Agent hooks adapt delivery mechanics without redefining semantic contracts.
- Orchestrator config comes from deployed TOML/env/CLI with explicit
  precedence; dashboard policy is not hardcoded here.
- Evolution raw artifacts/state stay under `.ai-evolution/`; versioned
  evolution files contain only safe normalized/audit material.
- orch-hub report publication and local protocol-evolution decisions remain
  separate modules and repositories.
