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

After the deploy, everything is one loop over one task file. Ten facts carry
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
6. **A final-gate review passes the task into `completed`.** A review at
   `final_review` that cannot pass records `changes-requested` and leaves the
   status where it is. The split skill has a separate administrative completion
   path described below; it never fabricates a review pass.
7. **A `changes-requested` verdict routes the next dev turn to remediation.**
   Remediation fixes the recorded findings and changes no status at all. The
   re-review then checks only those findings and any regressions the fixes
   introduced.
8. **Severity decides what blocks a review pass.** Only `correctness` findings
   can hold up the final gate. A `design` or `test` finding is either fixed
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
10. **The initial estimate has a fixed buffer.** Intake sets `session-limit`
    to the initial `session-est` total + 3; later re-estimation does not move
    it. At the limit, a completed review must pass with the task still
    `in_progress` to dispatch `/split-task`. It inventories remaining scope and findings
    into one independent pending task, updates the source's Scope/Acceptance
    and Split audit, and closes the source administratively through the normal
    closeout. Outstanding correctness findings remain outstanding in the
    successor. Remediation may exceed the limit until re-review passes. A genuine
    blocker or disputed finding still needs a human ruling.

The turn you should run next is always derivable from the file alone. Read top
to bottom; the first row that matches is the turn:

| Task file says | Next turn |
|---|---|
| `status: completed` without a Split transfer | **closeout** — `/ai-sync-v2`. Usually the stop hook already ran it in the final-review conversation and the file is in `.ai-tasks/archive/` by now. A task still sitting in `.ai-tasks/` at `completed` means closeout was interrupted, disabled, or unavailable: finish it in a fresh conversation. `/ctd-tasks` flags this state as `⚠ Completed (unarchived)`. |
| `status: blocked` | answer the question in `blockers:`, then resume that conversation; a blocked split resumes its original split conversation |
| recorded `## Split` with `Phase: preparing` or `ready` | `/split-task <id>` — resume or verify the reserved transfer, including a completed or already archived source; reuse its Target |
| finished review boundary, `in_progress`, latest `pass`, current at least `session-limit` | `/split-task <id>` — reserve `Phase: preparing`, verify and transfer the remainder, close out the source, and leave the successor pending |
| a dev entry that no `review of <sid>` entry names, or an open remediation fix set | `/invoke review <id>` (final gate at `final_review`), unless `fix-set: open` with a latest `changes-requested` verdict: then `/invoke dev <id>` continues remediation until the set is reviewable |
| `status: final_review`, all reviewed, no verdict-driven handback | the last review didn't conclude: decide it yourself and dispatch a fresh review with your ruling |
| otherwise: `pending` / `in_progress`, or all reviewed with latest verdict `changes-requested` | `/invoke dev <id>` — remediation after `changes-requested`, otherwise advancement |

Splitting requires no pending review work or open fix set. The skill checks
actual remaining scope, acceptance, and unresolved findings; final review or
closeout alone do not count. If no work remains despite `in_progress`, it
reports the lifecycle inconsistency through the existing blocked mechanism,
without creating an empty task or claiming completion. No Plan-slice boundary
is required. Recorded transfers retain recovery priority, including legacy
`final_review`/failed-review reservations. A missing legacy limit freezes at
current total + 3 before blocked work resumes. Split sessions use dev agent
configuration without a role or estimate increment. Failed reviews retain
normal remediation and group-budget escalation.

### When you need a fresh conversation

| Situation | Conversation |
|---|---|
| Every dev turn, every review turn | **new** |
| Review immediately after dev | **new** — a shared conversation destroys review independence |
| Remediation after a `changes-requested` review | **new** |
| Continuing a remediation that ran out of context (`fix-set: open`) | **new** |
| Answering a `blocked` task's question | **the same** conversation that blocked, so it keeps its role and its claim |
| Starting or recovering a split transfer | **new**, except a blocked split resumes its original conversation |
| Closeout after a task completes | **the same** conversation as the final review or split — the hook or split skill drives it |
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
