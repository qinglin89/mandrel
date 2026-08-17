---
last-updated: 2026-08-15
verified-against: 712b34f89c6436a001dfff9b73d801715c4e51b0
---

# Project Overview

## Purpose

Canonical source and deployment system for an AI-native coding protocol shared
by Claude Code, Cursor, Codex CLI, and the multi-session orchestrator.

## Users

- Protocol maintainers authoring role/workflow/meta contracts.
- Operators deploying and checking managed target repositories.
- AI coding agents consuming deployed contracts and target-owned memory/tasks.
- orch-hub as an external scheduler and evaluation-artifact publisher.

## Scope

- Versioned protocol, workflow, loader/hook, and orchestrator payloads.
- `mandrel` deploy, dry-run, drift status, local registry, orchestrator bootstrap,
  portable lock receipts, and repository-local workflow skills with
  personal-skill precedence detection.
- Human-triggered `mandrel evolution` control of the full lifecycle: report pool,
  immutable batches, guarded draft/round/experiment decisions, operator-stated
  exact-integration replay, exact-tree promotion and inverse rollback,
  cross-batch assessment/settlement, and schema-v7 status with stale-state
  tokens, allowed actions, recovery forms, and per-target deployment readings.
- One deterministic regression entrypoint shared by local runs, CI, and the
  optional pre-push hook; credentialed live probes remain manual.
- Project-specific evolution workspace for evidence-batched changes to this
  canonical suite.

## Non-Goals

- Owning target-project `.ai/`, `.ai-tasks/`, product code, or secrets.
- Implementing orch-hub scheduling/dashboard services.
- Owning evaluation production or its trigger policy; evolution consumes
  already-complete reports.
- Letting evaluation reports directly mutate canonical policy.

## Tech Stack

- Python 3.11+ package and standard library; pytest, ruff, shellcheck, and
  package-build verification toolchain.
- Python 3.14 orchestrator virtualenv with optional `cursor-sdk`.
- Markdown/TOML/JSON contracts; Bash hooks/linting.
- Git-backed canonical and memory history; machine-local manifests/registry.

## Repository Structure

| Path | Purpose |
|---|---|
| `canonical/` | Deploy-owned source payload |
| `mandrel/` | Deployment/status/registry Python package |
| `tests/` | Deployment, hook, closeout-path, and orchestrator-config tests |
| `scripts/` | Unified regression gate, optional hook installer, boundary checks |
| `evolution/` | Normative protocol-evolution contract and sanitized audit |
| `.ai/` | This repository's descriptive agent snapshot |
| `.ai-tasks/` | Local active evolution/maintenance work pool |
