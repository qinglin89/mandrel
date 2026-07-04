# Cross-model review workflow (canonical)

Canonical single source for the `review <id>` role (`ai-coding-v2.md` §11).
Loaded lazily by every tool: Claude Code reads it on the `review <id>` verb
per §11; Cursor and Codex reach it through their pointer files
(`.cursor/rules/review-workflow.mdc`, `.codex/review-workflow.md`); the
orchestrator injects it into review prompts. Rides the `ai-coding-*.md`
gitignore pattern.

Apply when a session is invoked as `review <task-id>` (review role). Does not
apply to `task <id>` development.

A `review <id>` session evaluates a task's dev work; it does not develop.
Evaluate per §6 and reason from the actual diff per §2. Do not write feature
code beyond trivial fixes recorded in the review entry.

## Procedure

1. Read the task, its full `## Session log`, and the `.ai/` docs relevant to
   the change.
2. Claim the task per §10 Entry step 3: update `claimed-by` to this session's
   id. (The Stop hooks locate the active task through `claimed-by` — a review
   that sets `completed` without claiming never triggers the ai-sync
   close-out.)
3. Determine the pending set: dev session-log entries whose session-id is not
   yet named by any `review of <session-id>` review entry. If the pending set
   is empty, report that there is nothing new to review and stop. (An entry
   marked `Handoff: continuation` is a remediation session whose fix set is
   still open — re-review waits until the fix set completes; if the LATEST
   entry carries the marker, the review turn normally waits.)
4. For each pending dev session, obtain its commits (from `git log` or the dev
   entry) and review the actual `git diff`. Classify findings per §6
   (correctness / design / test / style). Scope an interim review to the
   LANDED diff: remaining work the dev entry's Next/Open explicitly defers
   is not a finding (the final gate still verifies dev-completeness).
   (Codex only: the native reviewer — `codex review` / `codex exec review
   --base <branch>` — may generate the diff review; still record the verdict
   in the task session log.)
5. Append a review entry to `## Session log`:
   `### <date> / <session-id> / review of <dev-session-id> / (<before> → <after>)`
   with:
   - `Verdict:` pass | changes-requested
   - `Group:` `<anchor-dev-session-id>` — the convergence group this review
     belongs to (assignment rules under Convergence).
   - `Findings:` (§6-classified).
   ONE entry per reviewed dev session-id: a batched review (multiple pending
   sids) appends one entry per sid — a sid named only in another entry's
   prose stays formally pending.
6. Update `status` per the status-transition table
   (`ai-coding-tasks-v2.md` §3). Review-side notes:
   - A final gate that cannot pass leaves `final_review` in place — the
     `changes-requested` entry itself sends the task back to dev
     remediation. Do not downgrade the status for findings.
   - Before reviewing a `final_review` diff, verify the task is apparently
     dev-complete: no scope item left unexecuted, and the latest dev entry's
     Next/Open defers no remaining scope. If `final_review` was set in
     error, set `in_progress` and record why in your entry — that is the
     ONLY case a reviewer sets `in_progress` from `final_review`.

## Convergence

To keep the dev↔review loop bounded:

- **Delta-only re-review**: the first review of a task's work is comprehensive.
  Every later review covers only (a) whether the prior findings are resolved and
  (b) correctness regressions newly introduced by the fixes — it does not raise
  pre-existing design or style issues that earlier reviews left unflagged.
- **Findings ledger**: interim reviews (entry status `in_progress`) are advisory
  — they never gate. Their findings accumulate across review entries as the
  task's findings ledger. The `final_review` gate verifies the WHOLE accumulated
  ledger (every unresolved finding from any earlier review, interim or final)
  plus regressions — not just the latest dev session's delta.
- **Severity gates completion**: only `correctness` findings block
  `final_review → completed`. A `design` or `test` finding is either fixed in
  place when cheap, or carried to a new task via `/intake-task` while the current
  review passes; `style` findings never block.
- **Disputed findings**: when a pending dev session recorded a dispute for a
  finding, evaluate the dispute on the merits against the actual code. If you
  agree the finding was invalid, mark it withdrawn in your entry. If you
  still hold it valid, add a line `Dispute-unresolved: <finding, one line>`
  to your review entry and do NOT spend loop rounds re-requesting the same
  change — a dispute that survives one re-review escalates to the user
  immediately (the orchestrator pauses on this marker; in a manual session
  raise it to the user directly).
- **Convergence groups**: every review entry carries a `Group:` field naming the
  dev session that anchors the finding-chain it belongs to. A group's scope is
  FROZEN at its first review entry: the group = that finding set, plus any
  regressions introduced by the fixes for it. Convergence happens inside the
  group or escalates — a group never leaks into a fresh budget. Dev sessions
  must not mix remediation with new scope (roles §11: remediation before
  advancement), so a review normally covers either fixes or newly advanced
  work, not both. Assignment:
  - Review of newly ADVANCED scope work (a dev session executing the task's
    plan, not remediating findings) → new group, anchored at that dev session
    (`Group:` = its session id).
  - Re-review of fixes for group G checks ONLY (a) whether G's findings are
    resolved and (b) regressions introduced by those fixes. EVERYTHING it
    finds — leftover originals and fix-introduced regressions alike — belongs
    to group G. A fix session NEVER opens a new group and NEVER resets the
    budget; it must not expand the finding set beyond (a)+(b).
  - Fallback, for a dev session that (against §11) both fixed G and advanced
    new scope: G's items stay in G (same budget); findings in the newly
    advanced portion anchor a NEW group at that session. ("A fixed but B's
    new work has an issue" is progress on A's chain and a fresh chain for B —
    not a reset of A's budget.)
- **Per-group round budget (uniform for interim AND final reviews)**: count the
  `changes-requested` review entries sharing the current `Group:` anchor. Once
  two such entries exist and the current review still cannot pass, do not hand
  the task back for another remediation round — record the unresolved findings
  in the entry and escalate to the user for a decision (the orchestrator
  pauses on the round count mechanically; in a manual session raise it to the
  user directly). A finding the dev session disputes (§5) is likewise
  escalated to the user rather than looped.
