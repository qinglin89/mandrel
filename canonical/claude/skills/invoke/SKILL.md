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

`/invoke <role> <task-id>` where `<role>` is `dev-advancement` |
`dev-remediation` | `review` | `plan` | `dev` (auto-select the dev mode).
`$ARGUMENTS` carries both. Missing or unknown role/task id: ask, do not
guess.

## Procedure

1. Parse `<role>` and `<task-id>` from `$ARGUMENTS`. Read the task file
   `.ai-tasks/<task-id>.md`.
2. **Legality precheck** — verify the named role against the task file
   (mode predicate: `.ai-protocol/meta/taskfile.md` §3). On a mismatch,
   report the actual state and STOP — do not proceed under a wrong
   contract:
   - `dev-advancement`: latest review entry's `Verdict:` (if any) is NOT
     `changes-requested`, and frontmatter `fix-set` is not set.
   - `dev-remediation`: latest review entry's `Verdict:` IS
     `changes-requested`.
   - `review`: frontmatter `fix-set` is not set, and at least one work
     entry is not yet named by a `review of <sid>` entry (else report
     nothing to review).
   - `plan`: same preconditions as `dev-advancement` (a plan shadows
     advancement work).
   - `dev`: apply the predicate and select `dev-advancement` or
     `dev-remediation`; state the certified mode in your first reply.
3. Read the contract file for the (certified) role — its text is binding
   for this session:
   - `dev-advancement` — `.ai-protocol/protocols/dev-advancement.md`
   - `dev-remediation` — `.ai-protocol/protocols/dev-remediation.md`
   - `review` — `.ai-protocol/protocols/review.md`
   - `plan` — `.ai-protocol/protocols/plan.md`
4. Work the task under that contract, exactly as if the invocation message
   had carried the verb (`task <task-id>` / `review <task-id>` / a plan
   request) with the contract text pasted in.

The paste fallback and the human's on-return verification are documented in
the runbook (`.ai-protocol/workflow/runbook.md` §6; postcheck contract:
`.mandrel/orchestrator/prompts/postcheck-contract.md`).
