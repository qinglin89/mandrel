# Dev contract — remediation

Work contract for you to develop one task in remediation mode: the recorded
findings of the active convergence group are your work order. Conduct rules
(`.ai-protocol/protocols/conduct.md`) apply throughout.

## Inputs

- The full task file (`.ai-tasks/<id>.md`) including:
  `## Session log` — the single source of cross-session handoff state.
  Shapes: `.ai-protocol/meta/taskfile.md`.
- Assembled context: the eager set of the memory (`.ai/`) plus the task's
  `prefetch:` docs.

## Work conduct

- Review findings are claims to verify against actual code (conduct:
  reasoning rules) before implementing. Fix the valid findings, correctness
  first.
- A finding verified invalid is a dispute (conduct: disagreement): record it
  in your session-log entry — do not silently fix and do not silently skip it.
- Ending with your fix set incomplete: set frontmatter `fix-set: open` — it
  declares an open fix set, which is not yet a reviewable unit. When your
  fix set is complete, remove the `fix-set` line.
- Do not run preReEst and do not advance planned scope. An optional
  `Plan-slice:` line names the review group being remediated
  (`remediation for review group <sid>`).
- Modify code per the authority tiers (conduct).
- Do not edit `.ai/` mid-task; a `.ai/` gap or discrepancy
  noticed while working is a truth learned — it goes in the session-log
  entry's Done like any other fact.
- Mid-session memory (`.ai/`) retrieval goes through the memory read contract
  (`.ai-protocol/meta/memory.md`): route via `.ai/index.md` and `.ai/map.md` —
  do not grep across `.ai/`.

## Declared outputs

All outputs are declarations in the task file (shapes:
`.ai-protocol/meta/taskfile.md`):

- A `## Session log` entry — Done (facts, decisions, rejected alternatives,
  truths learned based on the whole session work), Plan-slice when
  applicable, Next (remaining work on this task), Open (unresolved items).
- A remediation session never changes `status`; its session-log entry is its
  complete output (`blocked` only for a genuine question for the human, with
  `blockers:` set). The authoritative transition table is in
  `.ai-protocol/meta/taskfile.md`.
- A `fix-set` declaration per Work conduct when your fix set is incomplete.
