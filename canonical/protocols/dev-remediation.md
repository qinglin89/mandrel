# Dev contract — remediation

Work contract for a session invoked to develop one task in remediation mode:
the recorded findings of the active convergence group are your work order.
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

- Review findings are claims to verify against actual code (conduct:
  reasoning rules) before implementing. Fix the valid findings, correctness
  first.
- A finding verified invalid is a dispute (conduct: disagreement): record it
  in your session-log entry — do not silently fix and do not silently skip it.
- Ending with your fix set incomplete: mark your entry with
  `- Handoff: continuation` — the marker declares an open fix set, which is
  not yet a reviewable unit. Never write it when the fix set is complete.
- Do not run preReEst and do not advance planned scope. An optional
  `Plan-slice:` line names the review group being remediated
  (`remediation for review group <sid>`).
- Modify code per the authority tiers (conduct).
- Do not edit `.ai/` mid-task (write access per the memory protocol's
  invariants, `.ai-protocol/meta/memory.md`). A `.ai/` gap or discrepancy
  noticed while working is a truth learned — it goes in the session-log
  entry's Done like any other fact.

## Declared outputs

All outputs are declarations in the task file (shapes:
`.ai-protocol/meta/taskfile.md`):

- A `## Session log` entry — Done (facts, decisions, rejected alternatives,
  truths learned), Plan-slice when applicable, Next (remaining work on this
  task), Open (unresolved items).
- A remediation session never changes `status`; its session-log entry is its
  complete output (`blocked` only for a genuine question for the human, with
  `blockers:` set). The authoritative transition table is in
  `.ai-protocol/meta/taskfile.md`.
