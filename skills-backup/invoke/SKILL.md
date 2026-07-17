---
name: invoke
description: Deliver a role contract at session invocation (ai-protocol). /invoke <role> <task-id> reads the deployed contract file under .ai-protocol/protocols/ into context and starts the work — dev (applies the taskfile mode predicate to select the mode contract), review, or plan. Interactive equivalent of the orchestrator's contract wrapper injection.
---

Caller-side contract delivery for interactive sessions (manual workflow
execution: `.ai-protocol/workflow/runbook.md` §6). The loader's verb→contract
mapping names the contract; this skill delivers its text on the activation
channel — byte-equivalent to the orchestrator's wrapper injection. Requires a
deployed `.ai-protocol/` tree; in any other repo, say so and stop.

## Invocation

`/invoke <role> <task-id>` where `<role>` is `dev` | `review` | `plan`.
`$ARGUMENTS` carries both. Missing or unknown role/task id: ask, do not guess.

## Procedure

1. Parse `<role>` and `<task-id>` from `$ARGUMENTS`. The task file is
   `.ai-tasks/<task-id>.md`.
2. Read the contract file for the role — its text is binding for this
   session:
   - `dev` — apply the mode predicate (`.ai-protocol/meta/taskfile.md` §3)
     to the task file: latest review entry's `Verdict:` is
     `changes-requested` → read `.ai-protocol/protocols/dev-remediation.md`;
     otherwise → read `.ai-protocol/protocols/dev-advancement.md`. Read
     exactly one mode contract; state the certified mode in your first
     reply.
   - `review` — read `.ai-protocol/protocols/review.md`.
   - `plan` — read `.ai-protocol/protocols/plan.md`.
3. Work the task under that contract, exactly as if the invocation message
   had carried the verb (`task <task-id>` / `review <task-id>` / a plan
   request) with the contract text pasted in.

The paste fallback and the human's on-return verification are documented in
the runbook (`.ai-protocol/workflow/runbook.md` §6; postcheck contract:
`.cursor/orchestrator/prompts/postcheck-contract.md`).
