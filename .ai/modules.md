---
last-updated: 2026-08-09
verified-against: ea0d02247214a3993b637809dadd816893ffecd3
---

# Modules

## Module Index

| Module | Location | Responsibility |
|---|---|---|
| CLI | `ai_native_deployment/cli.py`, `aii-2` | Parse/dispatch deploy, status, and registry commands |
| Deployment | `ai_native_deployment/deploy.py` | Map/filter/render/copy current canonical files; bootstrap; content, entrypoint, and skill-precedence drift checks; removed-file orphan detection/pruning pending |
| Manifest/lock | `manifest.py`, `lockfile.py`, `hashing.py` | Local and portable receipts plus hashes |
| Registry | `registry.py`, `paths.py` | Machine-local managed-repo inventory and path constants |
| Workflow skills | `canonical/claude/skills/` | Versioned per-target skills; Claude native discovery and repo-local paths for other backends |
| Protocol contracts | `canonical/protocols/` | Conduct and role-specific behavioral contracts |
| Workflow/meta | `canonical/workflow/`, `canonical/meta/` | Caller runbook/rolemapping and memory/task/init schemas |
| Agent adapters | `canonical/claude/`, `cursor/`, `codex/`, `repo-root/` | Tool-specific loading/hooks/config and root loader |
| Orchestrator | `canonical/orchestrator/` | Configurable dev/review state machine and execution backends |
| Boundary lint | `scripts/boundary-lint.sh` | Mechanical charter, reference, eager-channel, prompt checks |
| Verification gate | `scripts/check.sh`, `scripts/install-git-hooks.sh`, `.github/workflows/` | Single deterministic local/CI/hook entrypoint; manual credentialed probe path |
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
