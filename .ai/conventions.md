---
last-updated: 2026-08-09
verified-against: 623a1fd7e00197658ab81b5e6628131d4fc05faf
---

# Conventions

## Code

- Python 3.11+ with type hints, dataclasses for boundary values, `pathlib`, and
  standard-library-first dependencies.
- Keep CLI adapters thin; domain/file/subprocess behavior lives in modules.
- Inject clocks, runners, roots, and external clients for deterministic tests.
- Reject unsafe/escaping paths and malformed persisted state explicitly.
- Preserve target/user-owned data and unrelated dirty worktrees.

## Canonical Content

- Author deployable protocol/runtime changes only under `canonical/`.
- Protocol files remain role-local and caller-agnostic; caller sequencing lives
  in workflow/orchestrator layers.
- Never place credentials, `.env`, logs, sessions, caches, or local paths in
  canonical payload.
- Run `scripts/boundary-lint.sh` after canonical contract/loading changes.

## Evolution

- Every evolution task cites `evolution/README.md`; batch tasks cite a frozen
  batch ID.
- Raw imported bundles stay in `.ai-evolution/`; committed cases are sanitized.
- JSON artifacts are schema-versioned, UTF-8, deterministic, and hashed.
- Ledger records append; immutable batch manifests are never edited.
- Analysis tasks produce dispositions only; canonical edits require separate
  admitted tasks.

## Testing

- Primary suite: `scripts/check.sh` after `pip install -e '.[dev]'`; it is
  working-directory-independent and includes pytest, mock-loop scenarios,
  boundary/whitespace/static lint, package build/install, and non-mutation.
- Add deterministic checks only through `scripts/check.sh`; CI and optional
  Git hooks invoke that entrypoint rather than duplicate its commands.
- Keep credentialed/network-dependent probes outside the required gate and on
  explicit manual workflows.
- Deployment tests use temporary source/target roots.
- Cover dry-run/non-mutation, idempotency, failure boundaries, and recovery for
  filesystem workflows.
- Orchestrator config changes require CLI-level resolution/precedence tests.
- Run `scripts/check.sh` before commit.

## Git and Deployment

- Keep commits scoped and describe the affected ownership layer.
- Commit portable `.ai-deploy-lock.json` when the target wants protocol-version
  audit; never commit `.ai-deploy-manifest.json`.
- After canonical changes, redeploy explicitly; `canonical changed` is the
  intended drift signal.
- `.ai/` changes occur at initialization, task close-out absorption, or
  housekeeping—not during ordinary implementation sessions.
