# Dev contract

Work contract for a session invoked to develop one task: implement the
required work per the task file, in the mode the task file itself selects.
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

## Mode selection (derived from the task file)

Your mode derives from the latest review entry in the session log:

- Latest review entry's `Verdict:` is `changes-requested` → **remediation
  mode**: the recorded findings of the active convergence group are your work
  order.
- Otherwise (no review entries, or latest verdict `pass`) → **advancement
  mode**: advance the task's planned scope.

Do not mix the modes in one session: while the latest verdict is
`changes-requested`, only remediate — new scope resumes when mode selection
says advancement again.

## Advancement mode

- **preReEst** — before implementation, compare the overall task
  Scope/Acceptance, the optional `## Session plan`, and the latest
  `## Session log` Next/Open to the remaining work. If the current planned
  slice is too large for one effective session, update the `session-est` total
  and split only the current and future unimplemented plan slices before
  working. Prefer adding a continuation slice (for example `session-2-cont`)
  over renumbering later slices. Do not rewrite completed/reviewed slices.
- Work one clear slice. Record a `Plan-slice:` line naming the slice worked
  when a plan slice applies.
- End as one complete, coherent, reviewable unit. A context-overage wrap-up is
  an ordinary clean handoff: a session-log entry whose Next carries the
  handoff, a re-estimated `session-est`, and — when working from a
  `## Session plan` — the remaining work of the current slice split into a
  one-session-sized continuation slice.
- An advancement session never writes the `- Handoff: continuation` marker.

## Remediation mode

- Review findings are claims to verify against actual code (conduct:
  reasoning rules) before implementing. Fix the valid findings, correctness
  first.
- A finding verified invalid is a dispute (conduct: disagreement): record it
  in your session-log entry — do not silently fix and do not silently skip it.
- A remediation session never changes `status`; its session-log entry is its
  complete output (`blocked` only for a genuine human question).
- Ending with your fix set incomplete: mark your entry with
  `- Handoff: continuation` — the marker declares an open fix set, which is
  not yet a reviewable unit. Never write it when the fix set is complete.
- Do not run preReEst and do not advance planned scope. An optional
  `Plan-slice:` line names the review group being remediated
  (`remediation for review group <sid>`).

## Work conduct (both modes)

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
  task), Open (unresolved items) — plus any dispute records and, in
  remediation mode only, the continuation marker when the fix set is open.
- A `status` declaration. Output vocabulary for this contract: `in_progress`
  (work remains), `final_review` (only when the whole task scope is
  complete), `blocked` (a genuine question for the human, with `blockers:`
  set) — never `completed`, and no change at all in remediation mode. The
  authoritative transition table is in `.ai-protocol/meta/taskfile.md`.
