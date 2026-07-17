# Dev contract — base

Work contract for a session invoked to develop one task: implement the
required work per the task file. The invocation delivers this base together
with exactly one mode add — `.ai-protocol/protocols/dev-add-advancement.md`
or `.ai-protocol/protocols/dev-add-remediation.md` — which carries the
session's work order, scope rules, and status output vocabulary.
Self-contained: inputs → work → declared outputs. Conduct rules
(`.ai-protocol/protocols/conduct.md`) apply throughout.

## Inputs

- The task file (`.ai-tasks/<id>.md`): frontmatter, body
  (Goal / Scope / Acceptance, optional `## Session plan`), and the full
  `## Session log` — the single source of cross-session handoff state.
  Shapes: `.ai-protocol/meta/taskfile.md`.
- Assembled context: the eager memory set plus the task's `prefetch:` docs.
  Mid-session retrieval goes through the memory read contract
  (`.ai-protocol/meta/memory.md`): route via `.ai/index.md` and `.ai/map.md` —
  do not grep across `.ai/`.
- The codebase itself (code is truth).

## Work conduct

- Modify code per the authority tiers (conduct).
- Do not edit `.ai/` mid-task (write access per the memory protocol's
  invariants, `.ai-protocol/meta/memory.md`). A `.ai/` gap or discrepancy
  noticed while working is a truth learned — it goes in the session-log
  entry's Done like any other fact.
- Adjust the active task's body / scope / `session-est` as understanding
  sharpens. Record the adjustment in the next session-log entry.
- Calibrate `session-est` to one effective context window per session
  (est semantics: `.ai-protocol/meta/taskfile.md`).

## Declared outputs

All outputs are declarations in the task file (shapes:
`.ai-protocol/meta/taskfile.md`):

- A `## Session log` entry — Done (facts, decisions, rejected alternatives,
  truths learned), Plan-slice when applicable, Next (remaining work on this
  task), Open (unresolved items).
- A `status` declaration. The output vocabulary is the mode add's; the
  authoritative transition table is in `.ai-protocol/meta/taskfile.md`.
