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
blockers: [<task-id> | external:<text>]   # only if status=blocked. task-id refs another active task by id (e.g., 2026-05-26-foo); external:<text> for non-task blockers (e.g., external:awaiting API spec)
prefetch: [<.ai/*.md paths>]        # optional hint; lazy docs only (eager set per the memory loading contract is already loaded). Pre-load at start. Mutable — backfill with what was actually consulted.
fix-set: open | complete            # optional; declared only by a remediation-mode dev session. open = the active fix set is incomplete (not yet a reviewable unit); complete/absent = no open fix set. (Local meaning in the dev contract; scheduling consequences in the workflow runbook.)
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
| any session       | any                          | blocked (a question for the human; `blockers:` set) |

A dev session never sets `completed`. A review session never advances the
lifecycle forward. Every non-`completed` status is in-flight: the session-end
bookkeeping (clean tree, session-log entry) applies to review sessions
unchanged.

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

The session log is the single source for cross-session handoff and the input
for close-out absorption review.

### Markers and report lines

Entry fields and markers are machine-parsed as exact `- X: ...` list lines;
a prose mention of a field name elsewhere in an entry is not a declaration.

- `- Dispute-unresolved: <finding, one line>` — declared in a review entry
  when a disputed finding is still held valid (review contract).

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
