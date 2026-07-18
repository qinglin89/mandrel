# Project protocol (loader)

The documents imported below are binding for every session. Work runs under
role contracts delivered at invocation: `/invoke <role> <task-id>`, or a
paste (runbook §6, `.ai-protocol/workflow/runbook.md`); the role→contract
mapping lives in `.ai-protocol/workflow/rolemapping.md`. New work enters
via `/intake-task`; task-completion closeout is `/ai-sync-v2`; memory
bootstrap is `/ai-init`.

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
