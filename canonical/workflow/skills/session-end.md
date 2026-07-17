# Session-end bookkeeping (boundary skill)

Runs at EVERY session end, in the working conversation — only the working
session can author this content. Triggered by the stop-hook chain
(interactive) or self-applied unprompted (headless, per the conduct annex);
the caller's post-checks / the human's on-return checklist verify it
(runbook §5). Entry shapes and status semantics come from the taskfile schema
(`.ai-protocol/meta/taskfile.md`) — this skill owns the procedure, not the
formats.

## Procedure (in this order)

1. **Clean tree** — make the working tree clean (`git status --porcelain`
   empty): each modified file committed, and each untracked file handled by
   its nature — real work committed; an unwanted scratch file removed; a
   run-time artifact covered by a gitignore rule for its category (not
   ignored file-by-file). A task's advancement signals (session-log entry,
   status) must never run ahead of a clean tree.
2. **Session-log entry** — append this session's `## Session log` entry
   (Done / Plan-slice if applicable / Next / Open), shapes per the taskfile
   schema; review entries also carry Verdict / Group / Findings.
3. **Status** — declare `status` per the taskfile transition table for this
   session's kind.
4. **Prefetch backfill** — update the task's `prefetch:` with what was
   actually consulted.
5. **Remaining-task reconciliation (work sessions only)** — a session that
   changed facts (code, task scope/est, blockers) inspects every other
   active task under `.ai-tasks/` (all `pending`, `in_progress`,
   `final_review`, and `blocked` files; exclude `archive/`). For each
   remaining task, decide whether this session changed its blockers, scope,
   assumptions, acceptance criteria, prefetch, estimate, or status. Apply
   required updates, including removing task-id blockers that this session
   resolved. If a blocked task has no blockers left, restore the active
   status that is inferable from its latest session-log heading; otherwise
   set it to `pending`. A review session skips this step — its declarations
   (verdict, findings, status) change only the current task. The
   completion-triggered reconciliation and the current task's own row are
   the closeout skill's domain.

## Wrap-up variant (context overage)

When the conversation outgrows the context budget (~200k tokens), the same
procedure applies with these specifics — a wrap-up is an ordinary clean
handoff, not an emergency:

- The session-log entry's Next carries the handoff; re-estimate the
  `session-est` total (wrapping early means the estimate undershot).
- Dev advancement working from a `## Session plan`: update only the current
  and future unimplemented slices so Next points to one-session-sized work;
  prefer adding a continuation slice like `session-2-cont` over renumbering
  later slices.
- Dev remediation: do not run preReEst or advance planned scope. ONLY if the
  remediation fix set is not yet complete, include the line
  `- Handoff: continuation`; an advancement session never writes that
  marker — its landed work is a complete reviewable unit.
- Do not advance lifecycle status just because of the wrap-up; keep status
  unchanged unless restoring protocol legality requires otherwise. Start no
  new work.

## Completion trigger

When `status` reaches `completed` (final-gate pass), the stop-hook chain
additionally fires the task-completion closeout
(`.ai-protocol/workflow/skills/closeout.md`, packaged as `/ai-sync-v2`)
before the session ends.
