# Taskfile schema

Tasks describe in-flight changes. They live in `.ai-tasks/` (not
version-controlled), one file per task; their session logs are the input for
memory absorption at completion. This document is the single source for every
task-file data shape: frontmatter, status transitions, body, session-log
entries, markers, index, and archive semantics.

## 1. Invariants

- One file per task under `.ai-tasks/`.
- One session handles one task.
- The task file is ground truth: every cross-session decision derives from
  re-reading it, never from conversation memory.
- Task-id blockers must reference active task files only. No active task may
  list an archived/completed task id in `blockers`.
- A `blocked` task must have at least one real blocker. When task-id blockers
  are cleared and no blockers remain, restore an active status (`pending` or
  the pre-blocked status inferable from the latest session-log heading).

## 2. Frontmatter

```
---
id: <date-prefixed-slug>
status: pending | in_progress | final_review | completed | blocked   # final_review = dev-complete claim standing, in the final-review loop. Legal transitions: §3.
session-est: <current>/<total>      # progress/total in dev sessions; one estimated session ≈ one effective context window (~200k tokens). e.g., 0/3 pending, 1/3 after first session entry. `current` increments at DEV session entry (part of the claim, §4); review sessions do not consume the estimate. Raise `total` if estimate undershoots.
session-limit: <positive integer>  # immutable dev-session budget: initial session-est total + 3, set at intake. Includes advancement and remediation. Legacy tasks missing it freeze the then-current total + 3 at first orchestrator attach. A review-boundary split threshold, not a limit on sessions in an open fix set.
blockers: [<task-id> | external:<text>]   # only if status=blocked. task-id refs another active task by id (e.g., 2026-05-26-foo); external:<text> for non-task blockers (e.g., external:awaiting API spec)
prefetch: [<.ai/*.md paths>]        # optional hint; lazy docs only (eager set per the memory loading contract is already loaded). Pre-load at start. Mutable — backfill with what was actually consulted.
fix-set: open                       # optional; declared only by a remediation-mode dev session whose fix set is incomplete (not yet a re-reviewable unit). Remove the line when the fix set completes — absent = no open fix set. (Local meaning in the dev contract; scheduling consequences in the workflow runbook.)
claimed-by: <session-id>@<utc-iso-ts>  # session-id = the current agent session/conversation id supplied by the caller; ts = UTC ISO 8601 (e.g., 2026-05-26T09:30:00Z, from `date -u +%Y-%m-%dT%H:%M:%SZ`); set/updated at each session entry
---
```

## 3. Status transitions

Session kinds are data predicates over this file: a **dev** session is
**remediation** when the latest review entry's verdict is `changes-requested`,
otherwise **advancement**; a **review** session is **interim** when it enters
at `in_progress`, the **final gate** when it enters at `final_review`. (How a
runner maps kinds to contracts and prompts is the workflow layer's concern —
`.ai-protocol/workflow/rolemapping.md`.)

| session kind      | enters at                    | may set status to |
|-------------------|------------------------------|-------------------|
| dev advancement   | in_progress                  | in_progress; final_review (only when the whole scope is complete) |
| dev remediation   | in_progress / final_review   | unchanged — remediation never touches status; its session-log entry is its complete output |
| review interim    | in_progress                  | in_progress (findings never gate an interim review) |
| review final gate | final_review                 | completed (pass); final_review (changes required); in_progress (only if final_review was set in error, i.e. the task is not dev-complete — record why) |
| split skill       | in_progress / final_review   | completed (administrative split closure, only after §5 Split transfer is ready); unchanged while preparing |
| any session       | any                          | blocked (a question for the human; `blockers:` set) |

A dev session never sets `completed`. A review session never advances the
lifecycle forward. Every non-`completed` status is in-flight: the session-end
bookkeeping (clean tree, session-log entry) applies to review sessions
unchanged.

A split session uses the dev agent configuration but has no dev/review role
contract. Its `completed` declaration closes the source task administratively;
it is not a review pass or a claim that transferred findings are fixed. It
claims the source task without consuming `session-est` and invokes the normal
closeout only after the transfer is ready. `session-limit` never increases
with re-estimation, retries, or remediation.

Verdict values (review entries): `pass` | `changes-requested` — semantics in
the review contract (`.ai-protocol/protocols/review.md`); routing consequences
in the workflow runbook (`.ai-protocol/workflow/runbook.md`).

## 4. Claim

At session entry, the session claims the task in the frontmatter:

- `claimed-by: <session-id>@<utc-iso-ts>` (refresh the timestamp at claim
  time).
- If `status` was `pending`, transition to `in_progress` as part of the claim.
- A dev session increments `session-est` `<current>` by 1 as part of the
  claim; raise `<total>` too if the estimate undershoots. Review sessions do
  not consume the estimate.

## 5. Task body

The body should include:

- `## Goal`
- `## Scope`
- `## Acceptance`
- optional `## Session plan`
- optional `## Split` (before `## Session log`; format below)
- required `## Session log`

### Session-log entries

`## Session log` is appended one entry per session-end. Work entry:

```
### YYYY-MM-DD / <session-id> / (status_before → status_after)
- Done: ...
- Plan-slice: session-2   # optional
- Next: ...
- Open: ...
```

Review entry (one per reviewed work session):

```
### YYYY-MM-DD / <session-id> / review of <work-session-id> / (status_before → status_after)
- Verdict: pass | changes-requested
- Group: <anchor-session-id>
- Findings: ...
```

Field semantics:

- **Done** — what happened this session: committed changes, decisions made
  (incl. rejected alternatives + rationale), and truths learned worth
  recording. Past/history. This is the source absorption reads at close-out —
  write to enough fidelity that absorption needs no re-derivation.
- **Plan-slice** — optional soft link from a dev session to the task's
  `## Session plan` slice (for example `session-2`, or `remediation for review
  group <sid>`). A human-readable handoff aid, not a lifecycle state.
- **Next** — remaining work / required changes on this task; never name
  sessions or roles.
- **Open** — forward-looking unresolved items: open questions, deferred
  decisions, blockers to revisit. In the final entry of a task reaching
  `completed` this is `none`: by then every forward-looking item has been
  resolved or spawned as a pending task.
- **Verdict / Group / Findings** — review-entry fields; semantics in the
  review contract.
- **Kind** — a split bookkeeping entry declares `- Kind: split`. It is not a
  dev entry, consumes no estimate, and never enters the pending review set.
  Other work entries retain the default dev interpretation.

The session log is the single source for cross-session handoff and the input
for close-out absorption review.

### Markers and report lines

Entry fields and markers are machine-parsed as exact `- X: ...` list lines;
a prose mention of a field name elsewhere in an entry is not a declaration.

- `- Dispute-unresolved: <finding, one line>` — declared in a review entry
  when a disputed finding is still held valid (review contract).
- `- Kind: split` — declares a split bookkeeping entry, as above.

### Split (optional administrative closure)

The `/split-task` skill transfers all unfinished scope and unresolved
findings into one self-contained pending task. A source task records:

```
## Split

- Phase: preparing
- Target: <task-id>
- Reason: review boundary reached session-limit <limit> at <current> dev sessions
- Retained: <delivered scope and its actual acceptance evidence>
- Transferred: <each remaining scope/acceptance item and unresolved finding>
- Dependencies: <other active tasks whose remaining dependency moves to Target>
```

`Phase` and `Target` are exact machine-readable list lines in this section.
The caller first reserves `Phase: preparing`; Target is initially absent.
An optional `- Ruling: <human instruction>` records a binding human ruling
obtained before dispatch; continuation lines are indented. The caller may
persist this alongside the phase reservation so a restart preserves the
decision. Do not treat the transfer itself as a ruling on a disputed finding.
The skill persists a collision-free Target and the transfer plan before
creating that task. Target is immutable once selected. A retry resumes this
transfer and reuses its target; it never generates a second successor.
`Phase: ready` means the target exists with its index row, the entire
remainder and finding evidence have been transferred, the source's current
Scope/Acceptance reflect what it retains, and dependent tasks have been
reconciled. Only then may the source become `completed` and enter closeout.

The Split section preserves the original requirements' disposition; it does
not replace updating current Scope/Acceptance. Preserve reviewed plan slices,
session history, real verdicts, and finding severity. Transferring a finding
is not fixing, withdrawing, or accepting it. A target needs no source-task
reference: its goal, scope, acceptance, dependencies, evidence, and execution
constraints must be usable without the source file. Preserve provenance and
placement requirements such as an experiment's required branch. The target
starts pending with its own new
estimate and limit. A real blocker or unresolved human dispute prevents
automatic split closure; do not clear it merely to meet the budget.

An unfinished `preparing` or `ready` transfer is resumed before normal
dev/review dispatch (after handling genuine blockers). Split bookkeeping
entries record the transfer with `Kind: split`; final Next/Open are `none`
because all remaining work has an owner. Completion via split is an
administrative disposition, not a fabricated final-gate pass.

### Session plan (optional work slicing)

A task whose `session-est` total is greater than 1 may include a
`## Session plan` section before `## Session log`. Keep it simple: one heading
per planned dev advancement slice, with short Scope and Acceptance bullets.
Each slice should fit one effective session, using the same ~200k-token
context budget as `session-est`. The plan is a soft planning aid, not a strict
state counter. Example:

```
## Session plan

### session-1
Scope:
- ...
Acceptance:
- ...

### session-2
Scope:
- ...
Acceptance:
- ...
```

The plan is mutable for unimplemented slices only. A dev advancement session
may split the current or later slices when the work proves too large; prefer
adding a continuation slice such as `session-2-cont` over renumbering later
slices. Do not rewrite completed/reviewed slices. Remediation sessions do not
advance planned slices; their optional `Plan-slice` line names the review
group they remediate.

## 6. Tasks index

`.ai-tasks/index.md` lists active tasks only (`pending` / `in_progress` /
`blocked`), one line each. Completed tasks move to archive
(close-out). Must stay small enough for eager loading.

## 7. Lifecycle

On `completed`, the task leaves the active set through the task-completion
closeout (`.ai-protocol/workflow/skills/closeout.md`): admission-passing
findings are absorbed into `.ai/`, the file moves to archive, and its index
row is removed. Absorption may be **retroactive**: multiple archived tasks may
collectively warrant a Snapshot update that no single task did alone.

## 8. Archive

`.ai-tasks/archive/` holds all completed tasks as files (filename:
`YYYY-MM-DD-<slug>.md`). Not in the eager set. Retrieval via
`ls .ai-tasks/archive/` (filename-sorted = chronological) and
`grep -l <keyword> .ai-tasks/archive/*.md` for body matches.
