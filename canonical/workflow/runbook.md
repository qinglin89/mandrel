# Workflow runbook

How sessions chain over one `.ai-tasks/` task. This is the caller-layer
spec, single-sourced for its two executors: the **orchestrator**
(machine executor — implementation notes in the orchestrator README,
`.mandrel/orchestrator/README.md`) and a **human running the same loop
manually**. Role contracts (`.ai-protocol/protocols/`) never restate what is
written here; every scheduling rule in this file consumes only declared task-
file data (schema: `.ai-protocol/meta/taskfile.md`).

The caller is a dumb scheduler: it re-parses the task file before every turn
(no in-memory flow state), dispatches the next session, verifies declared
outputs afterwards, and pauses for a human on every decision it may not make
itself. The task file is ground truth; the caller NEVER writes it. Execution
can stop at any session boundary and restart later — the turn re-derives from
the file — and manual sessions can interleave freely with orchestrated ones.

## 1. Session kinds and verdict routing

A session's kind is derived from the invocation verb plus the task file
(data predicates in the taskfile schema §3): dev advancement / dev
remediation / review interim / review final gate.

`Verdict` is the routing decision for the next dev turn:

- `changes-requested`: the next dev turn MUST be remediation. It must address
  unresolved findings in the active convergence group.
- `pass`: the next dev turn may advance to the next planned scope.

## 2. Turn selection

Decision order per iteration (re-parse the task file first):

1. `status: completed` → **close-out** (the closeout skill,
   `.ai-protocol/workflow/skills/closeout.md`), then stop.
2. `status: blocked` → **surface the blocker to the human**, then resume the
   blocked conversation with the answer (§4.3).
3. Any dev session-log entry not yet named by a `review of <sid>` entry →
   **review turn** (one review session covers the whole pending set) —
   UNLESS the frontmatter declares `fix-set: open` (see §3).
4. `status: final_review` with nothing pending → the last review didn't
   conclude; **ask the human for a ruling**, then dispatch a fresh review
   with it (§4.6).
5. Otherwise (`in_progress`/`pending`, nothing awaiting review) → **dev
   turn**.

Typical full cycle: dev advances scope (`in_progress`) → review of that
session (interim) → dev remediation of the findings → re-review of the group
→ pass → dev advances the next scope chunk → … → dev sets `final_review` →
final gate reviews the WHOLE findings ledger → pass → `completed` →
close-out. A final gate that cannot pass keeps `final_review` and the loop
dispatches dev remediation; it reverts to `in_progress` only if
`final_review` was set in error.

Attach conditions for an in-flight task (any state → first action) are
enumerated in `.ai-protocol/workflow/rolemapping.md`.

## 3. Scheduling rules (consequences of declared data)

These are the workflow-side interpretations of markers/fields whose
role-local meanings live in the role contracts:

- **One dev session = one reviewable unit**: an ADVANCEMENT session's landed
  work is reviewed before the next dev session advances, regardless of why
  the session ended (planned convergence or context overage).
- **`fix-set: open`** (remediation-only frontmatter flag): the fix set is
  still open — dispatch a fresh DEV (remediation) session instead of a
  re-review; re-review waits until the fix set completes (the `fix-set`
  line removed). The flag without a changes-requested latest verdict is
  ignored with a warning. Review-side continuation needs no flag: a review
  that wraps mid-set leaves sids pending, so the next turn is a review
  anyway.
- **Remediation before advancement**: while the latest review verdict is
  `changes-requested`, dev turns dispatch in remediation mode; advancement
  resumes after a `pass` verdict. (Keeps re-reviews delta-only and each
  convergence group single-chained.)
- **`completed` is the sole close-out trigger**; per the transition table
  only a final-gate review session sets it.
- **Context budget**: a session whose conversation outgrows the per-session
  context budget (~200k tokens) is told to wrap up (clean handoff, no new
  work); remaining work lands in a fresh session — the loop re-derives the
  turn.

## 4. Budgets, escalations, and rulings

The caller counts; sessions never self-report scheduling state. Every
escalation pauses the loop for a binding human answer.

1. **Interactive ask** (a session tried to ask inline despite the conduct
   annex): surface the question, send the answer back into the same
   conversation, continue headless. Backstop only.
2. **Run error mid-flight**: ask for an instruction; retry in the same
   conversation.
3. **Post-check violations**: declared outputs are verified after every
   session against the postcheck contract
   (`.mandrel/orchestrator/prompts/postcheck-contract.md` — also the human
   executor's on-return checklist). Violations are sent back into the same
   conversation to fix, up to 3 followups, then escalate to the human.
4. **Blocked task** (`status: blocked`): show `blockers:` plus the latest
   entry's Open context; the answer resumes the ORIGINAL conversation with
   instructions to restore the pre-blocked status (the left side of
   `→ blocked` in its entry heading), clear `blockers`, and continue. Any
   session kind may block; the resumed session keeps its own role.
5. **Convergence budget / disputes**: after a `changes-requested` review,
   count the `changes-requested` review entries sharing the same `Group:`
   anchor — counting re-scans ALL review entries in the file, including
   pre-orchestrator ones. Over budget (2 re-reviews per group) → ask the
   human for a **binding ruling**, injected into the next dev prompt. A
   `Dispute-unresolved:` line skips the budget and escalates immediately — a
   two-sided disagreement is decided by the human on round 1, not looped.
6. **final_review stall**: everything reviewed, status sits at
   `final_review`, and the latest review did not conclude with a
   verdict-driven handback → ask for a ruling, appended to a fresh review
   session's prompt.
7. **Close-out verification**: after close-out, verify — task file gone from
   `.ai-tasks/`, archive copy exists, index row removed, no active task
   still lists an archived task id in `blockers`, no `blocked` task has
   empty blockers, the close-out response includes the
   `Remaining-task audit:` line, and the tree is clean. Up to 3 followups,
   then ask the human to finish manually.
8. **Plan gate** (optional, per-run): every dev ADVANCEMENT session is
   preceded by a separate read-only planning session under the plan contract
   (`.ai-protocol/protocols/plan.md`); remediation sessions skip the gate
   (the review findings already define the repair plan). The loop revolves
   around the plan-report artifact: each round is Revised (complete
   restatement adopted wholesale as the next rev) / Unchanged (sentinel
   line) / neither (warn and keep the current rev). Only a standalone
   approval word confirms; any other answer is feedback sent back into the
   SAME planning session. On confirm, the planning session closes and a
   fresh formal dev session receives ONLY the approved plan-report and the
   human ruling — never conversation history. If the run dies between plan
   and confirmation, nothing was persisted; a restart proposes a fresh plan.

## 5. Session boundaries

- **Entry (caller-assembled)**: the entry prompt carries the role contract
  text (wrapper-injected: review / plan / the caller-certified dev mode
  contract — rolemapping's composition table) and instantiates the session's
  concrete values — claim line (sid@timestamp), est increment, pending
  review set, mode block (remediation group / preReEst), injected fragments
  (approved plan-report, human ruling), and a POST-SESSION CHECKS preview
  rendered from the postcheck contract. The eager substrate (loader,
  conduct, schemas, memory set + frontmatter `prefetch:` docs) is assembled
  by the caller's backend: orchestrator injection (cursor) or the tools'
  native hooks/import chain (claude/codex). One assembly spec, two backends.
  Headless runs additionally inject the conduct annex
  (`.mandrel/orchestrator/prompts/entry/conduct-annex.md`).
- **End (session-side, hook-triggered)**: the session-end procedure
  (`.ai-protocol/workflow/skills/session-end.md`) is carried by the stop-hook
  chain in the same conversation. Orchestrated mode: the post-checks (§4.3)
  are the backstop. Manual mode: the human executing this runbook IS the
  post-check — verify declared outputs on return per the postcheck contract.
  Role contracts carry no end-procedure pointers.
- **Close-out (caller-dispatched)**: on `completed`, run the closeout skill —
  natively in-session (stop-hook chain forces it) or in a resumed/fresh
  conversation; verify per §4.7.

## 6. Manual execution (human as orchestrator)

A human runs the same loop with no orchestrator: pick the turn per §2, invoke
the verb (`task <id>` / `review <id>` — the loader carries the verb→contract
mapping), deliver the role contract at invocation, verify on return per the
postcheck contract, count budgets per §4.5, and make the escalation decisions
inline. The eager substrate (loader, conduct, schemas, memory set)
self-assembles via hooks/imports; the role contract does NOT ride ambient
context — the human caller delivers it on the activation channel,
byte-equivalent to the orchestrator's wrapper injection, either way:

- `/invoke <role> <task-id>` — skill; reads the deployed contract file (for
  dev it applies the taskfile §3 mode predicate and reads exactly one
  certified mode contract), or
- paste the contract file text(s) into the invocation message alongside the
  verb line.

Everything a session declares is in the task file, so the two executors are
interchangeable at every session boundary.
