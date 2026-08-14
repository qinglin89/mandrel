---
last-updated: 2026-08-14
verified-against: dcc3f73956ef11af19b40306c650ca1e041a297b
---

# Modules

## Module Index

| Module | Location | Responsibility |
|---|---|---|
| CLI | `ai_native_deployment/cli.py`, `aii-2` | Parse/dispatch deploy, status, registry, and evolution commands |
| Deployment | `ai_native_deployment/deploy.py` | Map/filter/render/copy current canonical files; bootstrap; content/mode, entrypoint, and skill-precedence drift checks; removed-file orphan detection/pruning pending |
| Manifest/lock | `manifest.py`, `lockfile.py`, `hashing.py` | Local rendered-content/mode receipt, portable canonical-revision receipt, and hashes |
| Registry | `registry.py`, `paths.py` | Machine-local managed-repo inventory and path constants |
| Workflow skills | `canonical/claude/skills/` | Versioned per-target skills; Claude native discovery and repo-local paths for other backends |
| Protocol contracts | `canonical/protocols/` | Conduct and role-specific behavioral contracts |
| Workflow/meta | `canonical/workflow/`, `canonical/meta/` | Caller runbook/rolemapping and memory/task/init schemas |
| Agent adapters | `canonical/claude/`, `cursor/`, `codex/`, `repo-root/` | Tool-specific loading/hooks/config and root loader |
| Orchestrator | `canonical/orchestrator/` | Configurable dev/review state machine and execution backends |
| Boundary lint | `scripts/boundary-lint.sh` | Mechanical charter, reference, eager-channel, prompt checks |
| Verification gate | `scripts/check.sh`, `scripts/install-git-hooks.sh`, `.github/workflows/` | Single deterministic local/CI/hook entrypoint; manual credentialed probe path |
| Evolution policy | `evolution/` | Normative workflow, config, schemas, batches/cases/experiments/ledger |
| Evolution controller | `ai_native_deployment/evolution/` | Config/schema/feed boundaries; report import, runtime state and ledger; manifest freeze, analysis tasks and closure; phase-v6 rendering |
| Evolution lineage | `ai_native_deployment/evolution/lineage.py`, `experiments.py`, `guards.py` | Whole-history derivation; shared durable-state guards; experiment refs/records; admission, round, terminal-decision, promotion, and batch-outcome operations |
| Evolution replay/releases | `ai_native_deployment/evolution/replay.py`, `rollback.py`, `revisions.py` | Durable replay request/run histories; exact integration trees; source-ref promotion helpers; latest-effective inverse rollback and Git/ref recovery |
| Evolution release assessment | `ai_native_deployment/evolution/assessment.py`, `experiments.py` | Derived cross-batch cohort frame; assessment formation/read validation; pinned counterfactual request/run recovery; retain/rollback settlement; first-base sequencing |

## Boundary Rules

- Deployment never creates or mutates target `.ai/` or `.ai-tasks/`.
- Registry discovery does not imply deployment readiness.
- Status reads the manifest and target; it does not repair drift.
- Canonical protocol contracts contain no caller/orchestrator dispatch details.
- Agent hooks adapt delivery mechanics without redefining semantic contracts.
- Orchestrator config comes from deployed TOML/env/CLI with explicit
  precedence; dashboard policy is not hardcoded here.
- Evolution raw artifacts/state stay under `.ai-evolution/`; versioned
  evolution files contain only safe normalized/audit material.
- orch-hub report publication and local protocol-evolution decisions remain
  separate modules and repositories.
