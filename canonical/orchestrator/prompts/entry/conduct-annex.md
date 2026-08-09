# AUTOMATION MODE (orchestrator-driven session)

This session is running headless under the dev↔review orchestrator. There is
NO human watching the conversation and NO interactive prompt. These rules
override the interactive-mode habit of asking inline; everything else in the
protocol context applies unchanged.

## Never ask inline

A question typed into the conversation reaches nobody and does not pause the
run. Whenever the protocol would have you ask the user — a Confirm-tier
change, load-bearing uncertainty ("ask or stop"), or a reviewer finding you
dispute — do NOT ask and do NOT guess. Instead, block and end:

1. Stop the work at a coherent point. Do not start the change that needs the
   answer; do not leave half-applied edits.
2. Make the working tree clean (`git status --porcelain` empty): commit
   completed work, remove scratch files.
3. In the task file, set `status: blocked` and
   `blockers: [external:<the question, one line, self-contained>]`.
   The question text must be answerable by a human who has NOT read this
   conversation: name the file/decision, the options you see, and your
   recommendation if you have one.
4. Append the `## Session log` entry (Done / Plan-slice if applicable /
   Next / Open). Put the full context of the blocker — what you were doing,
   why the decision is load-bearing, the options and their consequences —
   under `Open`.
5. End the session immediately. Output nothing further.

## End-of-session discipline

No Stop-hook backstop here: before your final message, read
`.ai-protocol/workflow/skills/session-end.md` and execute the applicable
procedure in full, unprompted. Your entry prompt's POST-SESSION CHECKS preview
(when present) is exactly what gets verified after you end; it is a backstop,
not a replacement for the procedure.

## Scope discipline

- Do exactly the invoked work for exactly the named task. No opportunistic
  side work (conduct: scope discipline).
- Never edit `.ai-protocol/**`, `ai-coding-*.md` (legacy), `CLAUDE.md`, or
  `.claude/**` — the deployed skills under `.claude/skills/**` included. All of
  it is deploy-owned payload: a change belongs in the protocol repo's
  `canonical/`, never in the deployed copy.
- Never write outside the repository, `~/.claude/**` and the other agent home
  directories included. They hold machine-local config and hook logs, not
  protocol payload.
- Do not edit `.ai/` mid-task (snapshot writes happen only at close-out).
