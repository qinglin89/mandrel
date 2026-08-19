# Role mapping

The caller's dispatch table: condition → (contract, prompt composition,
session mode). Companion to the runbook (`.ai-protocol/workflow/runbook.md`);
data shapes per the taskfile schema (`.ai-protocol/meta/taskfile.md`).

## Dispatch inputs and durability

The ONLY cross-run durable dispatch input is the **task file** — this is the
stateless-attach invariant: any executor can attach to any task at any
session boundary from the file alone. Run-local inputs exist but are
**ephemeral** (lost on restart, recomputable or re-askable):

| Input | Durability |
|---|---|
| task file: `status`, latest `Verdict:`/`Group:`, `blockers`, unreviewed entries, `fix-set`, `Dispute-unresolved:`, `claimed-by` | **durable** (ground truth) |
| session-id → tool routing (`logs/sessions.json`) | ephemeral (resume convenience) |
| pending human ruling | ephemeral (re-escalates after the next failed review) |
| followup / group-budget / max-session counters | ephemeral (group counts recompute from the file) |
| control-dir signals (question/answer files, stop.flag) | ephemeral (per run) |

## Verb → contract

Interactive sessions get this mapping from the loader (target `CLAUDE.md`);
the caller applies the same mapping when composing prompts. The caller also
DELIVERS the mapped contract text at activation — orchestrated prompts
wrapper-inject it (composition below); interactive sessions use the
`/invoke` skill or a paste (runbook §6). Ambient context carries no role
contract, and "read it on demand" is not a delivery channel:

| verb | contract |
|---|---|
| `task <id>` | exactly one caller-certified mode contract: `.ai-protocol/protocols/dev-remediation.md` when the latest review verdict is `changes-requested`, else `.ai-protocol/protocols/dev-advancement.md` (predicate: taskfile schema §3) |
| `review <id>` | `.ai-protocol/protocols/review.md` (interim at `in_progress`, final gate at `final_review`) |
| plan gate (caller-initiated) | `.ai-protocol/protocols/plan.md` |
| intake (new work) | `.ai-protocol/protocols/intake.md` |

## Session modes

Four modes of running a session, by conversation lifecycle:

| mode | mechanics |
|---|---|
| new (first-entry) | attach-table dispatch, fresh conversation |
| continue-turn (iteration) | same open conversation: post-check followups, answered-continue, discussion rounds, plan feedback |
| resume-by-sid | blocked-resume: reopen the SAME persisted conversation identified by `claimed-by` |
| fresh-continuation (wrap-up) | new conversation, same contract: remediation `fix-set: open` only (advancement never continues — its landed work is reviewed first) |

## Attach table (in-flight task → first action)

Turn detection is stateless; attaching to any task state is fully supported:

| Task state at attach | First action |
|---|---|
| frontmatter `fix-set: open` (+ latest verdict changes-requested) | dev remediation turn (fix set still open; re-review deferred). The flag without an open remediation → ignored with a warning, normal dispatch |
| dev entries unreviewed (any tool made them) | review turn (reviewer needs only task file + git, no transcript) |
| all reviewed, `in_progress` (e.g. after a changes-requested review) | dev turn (remediation mode) |
| `final_review` + unreviewed dev entries | review turn (final gate) |
| `final_review`, all reviewed, latest verdict changes-requested | dev remediation turn (status stays `final_review`) |
| `final_review`, all reviewed, no verdict-driven handback | ruling prompt (runbook §4.6) |
| `blocked` | blocker prompt → resume (runbook §4.4); resumable only when the executor can reopen the `claimed-by` conversation — otherwise unblock manually first |
| `completed` | close-out |

Group budgets are continuous across attach: counting re-scans ALL review
entries in the file, including pre-orchestrator ones.

## Prompt composition (caller-assembled entry)

Templates under `.mandrel/orchestrator/prompts/` (`entry/` + `midflight/`);
composition = mode contract + injected fragments:

| kind | composition |
|---|---|
| dev advancement | preamble (protocol block or native note) + conduct annex + dev contract text (dev-advancement wrapper) + dev invocation + entry checklist (claim, est, prefetch) + preReEst block + [approved plan-report] + [human ruling] + checks preview |
| dev remediation | preamble + conduct annex + dev contract text (dev-remediation wrapper) + dev invocation + entry checklist + remediation block (group values) + [human ruling] + checks preview |
| review (interim/final gate) | preamble + conduct annex + review contract text + review invocation + independence note + entry checklist (pending set, no-est) + checks preview |
| plan gate | the dev advancement composition as its base prompt (the plan shadow therefore carries the dev-advancement contract along; remediation never gates) + plan contract text + plan-gate instruction (+ plan feedback rounds midflight) |
| close-out | closeout instruction (task id, active count, skill pointer) |

The checks preview is GENERATED from the postcheck contract at dispatch time
— never frozen prose. Fragments instantiate values; rules live in the
contracts.
