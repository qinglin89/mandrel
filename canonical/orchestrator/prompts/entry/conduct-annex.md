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

## End-of-session discipline (no Stop hook backstop here)

In interactive mode a Stop hook enforces the session-end bookkeeping
(`.ai-protocol/workflow/skills/session-end.md`); under the orchestrator YOU
must satisfy it unprompted, in this order, before your final message:

1. Working tree clean (`git status --porcelain` empty).
2. `## Session log` entry appended for THIS session's id (Done / Plan-slice
   if applicable / Next / Open).
3. `status` set per the taskfile transition table
   (`.ai-protocol/meta/taskfile.md`) for your session kind — dev sessions
   NEVER set `completed`; a remediation session never changes status at all.

The orchestrator verifies all three after the session and will send the
violation back to you to fix — but each round-trip wastes a turn; get it
right the first time.

## Scope discipline

- Do exactly the invoked work for exactly the named task. No opportunistic
  side work; new work discovered mid-task goes through the intake contract
  as a pending task (conduct: scope discipline).
- Never edit `.ai-protocol/**`, `ai-coding-*.md` (legacy), `CLAUDE.md`,
  `.claude/**`, or `~/.claude/**`.
- Do not edit `.ai/` mid-task (snapshot writes happen only at close-out).
