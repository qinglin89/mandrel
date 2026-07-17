# Dev contract — remediation add

Mode add extending the dev base contract
(`.ai-protocol/protocols/dev-base.md`) for a session invoked in remediation
mode: the recorded findings of the active convergence group are your work
order.

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
