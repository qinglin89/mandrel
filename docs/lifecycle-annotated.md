# The lifecycle, annotated

The reference layer under [getting-started.md](getting-started.md) and the two
walkthroughs. Same loop, same commands — this document simply refuses to skip
anything: every rule turn selection runs on, every case where a fresh
conversation is mandatory, and the exclusion list that decides how a repository
is classified.

Read it when the short path stops answering your question — a review came back
`changes-requested`, a task needs more than one session, a closeout absorbed
nothing, or you want to know why the loop chose the turn it chose. Nothing here
is required for a first task.

For a complete worked example instead of the rules behind one, the two
walkthroughs carry one concrete task each from an empty directory to the
archive:

- [Walkthrough: greenfield](walkthroughs/greenfield.md) — a new project, an
  interview-driven initialization, a generated task pool, and the full
  convergence loop: advancement, `changes-requested`, remediation, re-review,
  a second advancement, final gate, closeout with absorption.
- [Walkthrough: brownfield](walkthroughs/brownfield.md) — adopting mandrel in a
  repository that already has code: derivation-driven initialization, intake
  from an English request, the two-session floor, a non-blocking finding carried
  out as its own task, and admission tests that reject as much as they accept.

Both walkthroughs run the **manual loop**: you are the scheduler and your agent
does the work. The unattended scheduler runs the identical loop
([getting-started.md](getting-started.md#running-the-loop-unattended)).

This guide links the normative documents rather than restating them. When the
guide and a contract disagree, the contract wins.

## Every rule the loop runs on

After the deploy, everything is one loop over one task file. Nine facts carry
almost all of it:

1. **The task file is ground truth.** `.ai-tasks/<id>.md` holds the goal, the
   scope, the acceptance criteria, and an append-only `## Session log`. Nothing
   is remembered between sessions except what is written there.
2. **One session = one conversation.** A session claims the task, does one
   coherent slice of work, ends with a clean tree and a session-log entry, and
   stops. The next turn is a new conversation.
3. **Roles are delivered, not ambient.** `/invoke dev <id>` and
   `/invoke review <id>` read the matching contract out of `.ai-protocol/` and
   bind the session to it. Without an invocation, a session has no role.
4. **Review is a separate fresh conversation, always.** Its only evidence is the
   task file, the `.ai/` memory, and the actual `git diff`. There is no
   transcript to inherit, which is exactly the point — do not paste the dev
   conversation into it.
5. **Dev never writes `status: completed`.** A dev session may set
   `in_progress`, or `final_review` when the *whole* task scope is done, or
   `blocked`. That is the entire set.
6. **Only a final-gate review session may set `completed`.** A review at
   `final_review` that cannot pass records `changes-requested` and leaves the
   status where it is.
7. **A `changes-requested` verdict routes the next dev turn to remediation.**
   Remediation fixes the recorded findings and changes no status at all. The
   re-review then checks only those findings and any regressions the fixes
   introduced.
8. **Severity decides what blocks.** Only `correctness` findings can hold up
   `final_review → completed`. A `design` or `test` finding is either fixed
   cheaply in place or carried out as a new pending task while the review
   passes. `style` findings never block.
9. **Closeout is automatic, and it always archives.** When a task reaches
   `completed` with a clean tree, the session-end hook blocks the stop and
   directs the same conversation into `/ai-sync-v2`: absorb what qualifies into
   `.ai/`, move the task file to `.ai-tasks/archive/`, drop its index row,
   re-check the other active tasks, commit. **Archiving always happens.
   Absorption is conditional** — findings must pass three admission tests
   (derivation cost, stability, leverage), and a task that teaches nothing
   durable is archived with `.ai/` untouched.

The turn you should run next is always derivable from the file alone. Read top
to bottom; the first row that matches is the turn:

| Task file says | Next turn |
|---|---|
| `status: completed` | **closeout** — `/ai-sync-v2`. Usually the stop hook already ran it in the final-review conversation and the file is in `.ai-tasks/archive/` by now. A task still sitting in `.ai-tasks/` at `completed` means the hook was interrupted, disabled, or unavailable, so closeout has *not* run: it is the turn, in a fresh conversation. `/ctd-tasks` flags this state as `⚠ Completed (unarchived)`. |
| `status: blocked` | answer the question in `blockers:`, then resume that conversation |
| frontmatter `fix-set: open` | `/invoke dev <id>` — the fix set is still open, so the re-review waits |
| a dev entry that no `review of <sid>` entry names | `/invoke review <id>` (the final gate, if the status is `final_review`) |
| `status: pending` | `/invoke dev <id>` |
| all reviewed, latest verdict `changes-requested` | `/invoke dev <id>` — auto-selects remediation |
| all reviewed, latest verdict `pass`, scope remains | `/invoke dev <id>` — advancement |
| `status: final_review`, all reviewed, no verdict-driven handback | the last review didn't conclude: decide it yourself and dispatch a fresh review with your ruling |

### When you need a fresh conversation

| Situation | Conversation |
|---|---|
| Every dev turn, every review turn | **new** |
| Review immediately after dev | **new** — a shared conversation destroys review independence |
| Remediation after a `changes-requested` review | **new** |
| Continuing a remediation that ran out of context (`fix-set: open`) | **new** |
| Answering a `blocked` task's question | **the same** conversation that blocked, so it keeps its role and its claim |
| Closeout after a task completes | **the same** conversation as the final review — the hook drives it |
| Closeout for a task left at `completed` in `.ai-tasks/` | **new** — the hook did not run; invoke `/ai-sync-v2` yourself |

The full scheduling spec is `.ai-protocol/workflow/runbook.md` in your target
(source: [`canonical/workflow/runbook.md`](../canonical/workflow/runbook.md)).
The lifecycle data shapes are `.ai-protocol/meta/taskfile.md`.

## The target-project surface

`/ai-init` classifies a repository as greenfield or brownfield, and the two
initializations behave completely differently — one interviews you and generates
a task pool, the other derives memory from your code and stops for your
sign-off. One list decides which you get.

Classification runs over the **target-project surface**, which is your
repository *minus* everything the deploy owns: `.ai-protocol/`, `CLAUDE.md`,
`ai-coding-*.md` (the legacy loader), `.claude/`, `.codex/`, `.cursor/`,
`.mandrel/`, `.ai/`, `.ai-tasks/`, and the deploy receipts `.ai-deploy-*.json`.
That list covers the whole payload — the orchestrator's Python source and
requirements under `.mandrel/orchestrator/` included — so a repository holding
nothing but a mandrel deployment has an empty surface: greenfield, not
brownfield.

The same exclusions are what stop a brownfield scan from reading the protocol's
own code as if it were yours. Without them, a five-pass derivation would happily
describe `.ai-protocol/` and `.mandrel/orchestrator/` in your `.ai/` as though
they were your service.

The binding version of this list is `.ai-protocol/meta/init.md` in your target
(source: [`canonical/meta/init.md`](../canonical/meta/init.md)). It moves when
the payload moves; this restatement is checked against it.

## Where to go next

| | |
|---|---|
| [walkthroughs/greenfield.md](walkthroughs/greenfield.md) | one task from an empty directory to the archive, including remediation and re-review |
| [walkthroughs/brownfield.md](walkthroughs/brownfield.md) | one task in a repository that already has code, including a carried finding |
| [getting-started.md](getting-started.md) | the short path, if you arrived here first |
| [operations.md](operations.md) | every command, flag, drift state, receipt, and lifecycle verb; deploy collisions and hook troubleshooting |
| [ARCHITECTURE.md](../ARCHITECTURE.md) | how context reaches a session across the three agent tools |
| `.ai-protocol/workflow/runbook.md` (in your target) | the scheduling spec both executors read |
| `.ai-protocol/meta/taskfile.md` (in your target) | task frontmatter, status transitions, session-log shapes |
| `.ai-protocol/meta/memory.md` (in your target) | what earns a place in `.ai/`, and how it is maintained |
| `.ai-protocol/protocols/` (in your target) | the contracts themselves: conduct, dev, review, plan, intake |
