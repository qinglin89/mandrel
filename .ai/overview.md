---
last-updated: 2026-07-31
verified-against: c7c625c20f6d917c24bb586cc73e8c9bf2742490
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
- `aii-2` deploy, dry-run, drift status, local registry, orchestrator bootstrap,
  portable lock receipts, and temporary global Claude-skill synchronization.
- Project-specific evolution workspace for evidence-batched changes to this
  canonical suite.

## Non-Goals

- Owning target-project `.ai/`, `.ai-tasks/`, product code, or secrets.
- Implementing orch-hub scheduling/dashboard services.
- Owning evaluation production or its trigger policy; evolution consumes
  already-complete reports.
- Letting evaluation reports directly mutate canonical policy.

## Tech Stack

- Python 3.11+ package and standard library; pytest test dependency.
- Python 3.14 orchestrator virtualenv with optional `cursor-sdk`.
- Markdown/TOML/JSON contracts; Bash hooks/linting.
- Git-backed canonical and memory history; machine-local manifests/registry.

## Repository Structure

| Path | Purpose |
|---|---|
| `canonical/` | Deploy-owned source payload |
| `ai_native_deployment/` | Deployment/status/registry/skills Python package |
| `tests/` | Deployment, hook, orchestrator-config, and skills tests |
| `scripts/` | Boundary/invariant checks |
| `skills-backup/` | Parked global Claude workflow-skill source |
| `evolution/` | Normative protocol-evolution contract and sanitized audit |
| `.ai/` | This repository's descriptive agent snapshot |
| `.ai-tasks/` | Local active evolution/maintenance work pool |
