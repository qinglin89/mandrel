# Review contract

Evaluation contract for a session invoked to review one task's landed work.
Self-contained: inputs → evaluation → declared outputs. It evaluates; it does
not develop. Do not write feature code beyond trivial fixes recorded in the
review entry. Conduct rules (`.ai-protocol/protocols/conduct.md`) apply
throughout; reason from the actual diff (conduct: reasoning rules).

## Inputs

Evidence is ONLY: the task file with its full `## Session log`, the relevant
`.ai/` docs, and the actual commits/diffs of the work under review
(`git log` / `git show`). No work-session conversation transcript exists.

## Findings taxonomy

Focus on: correctness, architectural consistency, API compatibility, test
adequacy, edge cases, maintainability, regression risk.

Classify findings as:

- correctness issue (must fix)
- design issue (should fix)
- test issue (should fix)
- style suggestion (optional)

## Procedure

1. Read the task, its full `## Session log`, and the `.ai/` docs relevant to
   the change.
2. Claim the task: update `claimed-by` to this session's id (shape per
   `.ai-protocol/meta/taskfile.md`).
3. Determine the pending set: work entries (non-review session-log entries)
   whose session-id is not yet named by any `review of <session-id>` review
   entry. An entry carrying `- Handoff: continuation` declares an open fix
   set — not yet a reviewable unit; exclude it from the pending set. If the
   pending set is empty, report that there is nothing new to review and stop.
4. For each pending work session, obtain its commits (from `git log` or the
   work entry) and review the actual `git diff`. Classify findings per the
   taxonomy above. Scope an interim review to the LANDED diff: remaining work
   the work entry's Next/Open explicitly defers is not a finding (the final
   gate still verifies dev-completeness). (Codex only: the native reviewer —
   `codex review` / `codex exec review --base <branch>` — may generate the
   diff review; still record the verdict in the task session log.)
5. Append a review entry to `## Session log` (shape per
   `.ai-protocol/meta/taskfile.md`):
   `### <date> / <session-id> / review of <work-session-id> / (<before> → <after>)`
   with:
   - `Verdict:` pass | changes-requested. `pass` declares the active
     convergence group has no unresolved findings, or all residual behavior
     was explicitly accepted by the human; `changes-requested` declares
     unresolved findings remain.
   - `Group:` `<anchor-session-id>` — the convergence group this review
     belongs to (assignment rules under Convergence).
   - `Findings:` (taxonomy-classified).
   ONE entry per reviewed work session-id: a batched review (multiple pending
   sids) appends one entry per sid — a sid named only in another entry's
   prose stays formally pending.
6. Declare `status` per the transition table
   (`.ai-protocol/meta/taskfile.md`). Review-side notes:
   - Findings never downgrade a status: a final gate that cannot pass records
     `changes-requested` and leaves `final_review` in place.
   - Before reviewing a `final_review` diff, verify the task is apparently
     dev-complete: no scope item left unexecuted, and the latest work entry's
     Next/Open defers no remaining scope. If `final_review` was set in
     error, set `in_progress` and record why in your entry — that is the
     ONLY case a reviewer sets `in_progress` from `final_review`.
   - `blocked` remains available for a genuine question for the human
     (with `blockers:` set).

## Convergence

To keep the finding-remediation loop bounded:

- **Delta-only re-review**: the first review of a task's work is comprehensive.
  Every later review covers only (a) whether the prior findings are resolved and
  (b) correctness regressions newly introduced by the fixes — it does not raise
  pre-existing design or style issues that earlier reviews left unflagged.
- **Findings ledger**: interim reviews (entry status `in_progress`) are advisory
  — they never gate. Their findings accumulate across review entries as the
  task's findings ledger. The `final_review` gate verifies the WHOLE accumulated
  ledger (every unresolved finding from any earlier review, interim or final)
  plus regressions — not just the latest work session's delta.
- **Severity gates completion**: only `correctness` findings block
  `final_review → completed`. A `design` or `test` finding is either fixed in
  place when cheap, or carried as a new pending task per the intake contract
  while the current review passes; `style` findings never block.
- **Disputed findings**: when a pending work session recorded a dispute for a
  finding, evaluate the dispute on the merits against the actual code. If you
  agree the finding was invalid, mark it withdrawn in your entry. If you
  still hold it valid, add a line `Dispute-unresolved: <finding, one line>`
  to your review entry and do NOT re-request the same change in later
  entries — record the standing disagreement once and leave the decision to
  the human.
- **Convergence groups**: every review entry carries a `Group:` field naming the
  work session that anchors the finding-chain it belongs to. A group's scope is
  FROZEN at its first review entry: the group = that finding set, plus any
  regressions introduced by the fixes for it. A work session normally either
  remediates a group or advances new scope, not both (the fallback below
  handles violations), so a review normally covers either fixes or newly
  advanced work. Assignment:
  - Review of newly ADVANCED scope work (a work session executing the task's
    plan, not remediating findings) → new group, anchored at that work session
    (`Group:` = its session id).
  - Re-review of fixes for group G checks ONLY (a) whether G's findings are
    resolved and (b) regressions introduced by those fixes. EVERYTHING it
    finds — leftover originals and fix-introduced regressions alike — belongs
    to group G. A fix session NEVER opens a new group; it must not expand the
    finding set beyond (a)+(b).
  - Fallback, for a work session that both fixed G and advanced new scope:
    G's items stay in G; findings in the newly advanced portion anchor a NEW
    group at that session. ("A fixed but B's new work has an issue" is
    progress on A's chain and a fresh chain for B — not a reset of A's.)
- **Honest verdicts over forced convergence**: record unresolved findings and
  your verdict as they stand; do not expand scope to force convergence, and do
  not re-request changes already recorded as disputed. Round budgets are
  counted outside this contract from the entries you write.
