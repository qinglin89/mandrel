---
last-updated: 2026-07-31
verified-against: 1cc444eefee5e7d41cd94f7c01b661bf94c75152
---

# Modules

## Module Index

| Module | Location | Responsibility |
|---|---|---|
| CLI | `ai_native_deployment/cli.py`, `aii-2` | Parse/dispatch deploy, status, registry, skills commands |
| Deployment | `ai_native_deployment/deploy.py` | Map/filter/render/copy canonical files; bootstrap; drift checks |
| Manifest/lock | `manifest.py`, `lockfile.py`, `hashing.py` | Local and portable receipts plus hashes |
| Registry | `registry.py`, `paths.py` | Machine-local managed-repo inventory and path constants |
| Skills compatibility | `skills.py`, `skills-backup/` | Temporary guarded global Claude-skill sync |
| Protocol contracts | `canonical/protocols/` | Conduct and role-specific behavioral contracts |
| Workflow/meta | `canonical/workflow/`, `canonical/meta/` | Caller runbook/rolemapping and memory/task/init schemas |
| Agent adapters | `canonical/claude/`, `cursor/`, `codex/`, `repo-root/` | Tool-specific loading/hooks/config and root loader |
| Orchestrator | `canonical/orchestrator/` | Configurable dev/review state machine and execution backends |
| Boundary lint | `scripts/boundary-lint.sh` | Mechanical charter, reference, eager-channel, prompt checks |
| Evolution policy | `evolution/` | Normative workflow, config, schemas, batches/cases/experiments/ledger |
| Evolution controller | `ai_native_deployment/evolution.py` | Not implemented; pending import/pool/batch/task CLI |

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
