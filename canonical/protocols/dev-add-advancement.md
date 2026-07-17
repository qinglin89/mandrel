# Dev contract — advancement add

Mode add extending the dev base contract
(`.ai-protocol/protocols/dev-base.md`) for a session invoked in advancement
mode: advance the task's planned scope.

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
- Status output vocabulary: `in_progress` (work remains), `final_review`
  (only when the whole task scope is complete), `blocked` (a genuine question
  for the human, with `blockers:` set) — never `completed`.
