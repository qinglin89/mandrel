# Dev contract — advancement

Work contract for you to develop one task:
advance the task's planned scope, implementing the required work per the
task file. Conduct rules
(`.ai-protocol/protocols/conduct.md`) apply throughout.

## Inputs

- The full task file (`.ai-tasks/<id>.md`) including:
  `## Session log` — the single source of cross-session handoff state.
  Shapes: `.ai-protocol/meta/taskfile.md`.
- Assembled context: the eager set of the memory(`.ai/`) plus the task's `prefetch:` docs.

## Work conduct

- **preReEst** — before implementation, compare the overall task
  Scope/Acceptance, the optional `## Session plan`, and the latest
  `## Session log` Next/Open to the remaining work. If the current
  planned slice is too large for one effective session,
  update the `session-est` total and split only the current and future
  unimplemented plan slices before working.
  Prefer adding a continuation slice (for example `session-2-cont`)
  over renumbering later slices. Do not rewrite completed/reviewed slices.
- Work one clear slice. Under `## Session log` record a `Plan-slice:` line
  naming the slice worked when a plan slice applies.
- End as one complete, coherent, reviewable unit. A context-overage wrap-up is
  an ordinary clean handoff: a session-log entry whose Next carries the
  handoff, a re-estimated `session-est`, and — when working from a
  `## Session plan` — the remaining work of the current slice split into a
  one-session-sized continuation slice.
- Never set frontmatter `fix-set`.
- Modify code per the authority tiers (conduct).
- Do not edit `.ai/` mid-task; a `.ai/` gap or discrepancy
  noticed while working is a truth learned — it goes in the session-log
  entry's Done like any other fact.
- Mid-session memory(`.ai/`) retrieval goes through the memory read contract
  (`.ai-protocol/meta/memory.md`): route via `.ai/index.md` and `.ai/map.md` —
  do not grep across `.ai/`.
- Adjust the active task's body / scope / `session-est` as understanding
  sharpens. Record the adjustment in the next session-log entry.
- Calibrate `session-est` to one effective context window per session
  (est semantics: `.ai-protocol/meta/taskfile.md`).

## Declared outputs

All outputs are declarations in the task file (shapes:
`.ai-protocol/meta/taskfile.md`):

- A `## Session log` entry — Done (facts, decisions, rejected alternatives, truths learned
  based on the whole session work), Plan-slice when applicable,
  Next (remaining work on this task), Open (unresolved items).
- A `status` declaration: `in_progress` (work remains), `final_review`
  (only when the whole task scope is complete), `blocked` (a genuine question
  for the human, with `blockers:` set) — never `completed`. The authoritative
  transition table is in `.ai-protocol/meta/taskfile.md`.
- Since `final_review` stands for a task that 'the whole task scope is complete',
  there should be no any Open items under that status. As for a complete task, any Open
  items should already either be resolved or spawned as new tasks.
