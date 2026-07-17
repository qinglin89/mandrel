# Project protocol (loader)

This project runs the ai-protocol suite deployed under `.ai-protocol/`:
role contracts in `protocols/`, data schemas in `meta/`, caller-layer docs in
`workflow/`. The documents imported below are binding for every session.

## Verb → contract mapping

The invocation verb selects the session's work contract — a session-level
distinction, independent of tool and model. The caller delivers the contract
text at invocation: orchestrated sessions receive it injected into the entry
prompt; interactive sessions receive it via the `/invoke` skill or a paste
(runbook §6, `.ai-protocol/workflow/runbook.md`):

- `task <id>` → dev contract, the mode file the caller certifies:
  `.ai-protocol/protocols/dev-advancement.md` or
  `.ai-protocol/protocols/dev-remediation.md` (mode predicate:
  `.ai-protocol/meta/taskfile.md` §3, applied per
  `.ai-protocol/workflow/rolemapping.md`).
- `review <id>` → review contract: `.ai-protocol/protocols/review.md`.

New work that no active task covers enters through the intake contract
(`.ai-protocol/protocols/intake.md`), packaged as the `/intake-task` skill.
The task-completion closeout (`.ai-protocol/workflow/skills/closeout.md`) is
packaged as `/ai-sync-v2` and fires on `status: completed`. Memory bootstrap
(`.ai-protocol/meta/init.md`) is packaged as `/ai-init`.

## Protocol imports

@.ai-protocol/protocols/conduct.md
@.ai-protocol/meta/taskfile.md
@.ai-protocol/meta/memory.md

## Memory (eager set)

`.ai/` is the project's cross-session memory: a timeless distillation of
project understanding. Each session inherits prior knowledge without
re-derivation from source. Contract: `.ai-protocol/meta/memory.md`.

@.ai/index.md
@.ai/map.md
@.ai/overview.md
@.ai/architecture.md
@.ai/design.md
@.ai/conventions.md

## Work tracking

`.ai-tasks/` holds active work — one file per task, accumulating a session
log that carries handoff state across sessions. Schema:
`.ai-protocol/meta/taskfile.md`.

@.ai-tasks/index.md
