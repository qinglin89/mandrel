# Task Protocol

Tasks describe in-flight changes. They live in `.ai-tasks/` and feed into memory via `/ai-sync-v2` at completion.

## 1. Invariants

- One file per task under `.ai-tasks/`.
- One session handles one task.
- Task-id blockers must reference active task files only. No active task may
  list an archived/completed task id in `blockers`.
- A `blocked` task must have at least one real blocker. When task-id blockers
  are cleared and no blockers remain, restore an active status (`pending` or
  the pre-blocked status inferable from the latest session-log heading).

## 2. Frontmatter

```
---
id: <date-prefixed-slug>
status: pending | in_progress | final_review | completed | blocked   # final_review = dev-complete claim standing, in the final-review loop. Legal transitions: §3 Status transitions.
session-est: <current>/<total>      # progress/total; e.g., 0/3 pending, 1/3 after first session entry. `current` increments at DEV session entry (part of the claim, §10 Entry); review sessions do not consume the estimate. Raise `total` if estimate undershoots.
blockers: [<task-id> | external:<text>]   # only if status=blocked. task-id refs another active task by id (e.g., 2026-05-26-foo); external:<text> for non-task blockers (e.g., external:awaiting API spec)
prefetch: [<.ai/*.md paths>]        # optional hint; lazy docs only (eager set per memory §2 is already loaded). Pre-load at start. Mutable.
claimed-by: <session-id>@<utc-iso-ts>  # session-id = $CLAUDE_CODE_SESSION_ID; ts = UTC ISO 8601 (e.g., 2026-05-26T09:30:00Z, from `date -u +%Y-%m-%dT%H:%M:%SZ`); set/updated at each session entry
---
```

## 3. Status transitions

A session's kind is derived from the invocation verb (role — ai-coding-v2.md
§11) plus the task file: a dev session is **remediation** when the latest
review entry's verdict is `changes-requested`, otherwise **advancement**; a
review session is **interim** when it enters at `in_progress`, the **final
gate** when it enters at `final_review`.

| session kind      | enters at                    | may set status to |
|-------------------|------------------------------|-------------------|
| dev advancement   | in_progress                  | in_progress; final_review (only when the whole scope is complete) |
| dev remediation   | in_progress / final_review   | unchanged — remediation never touches status; re-review is triggered by its session-log entry |
| review interim    | in_progress                  | in_progress (findings never gate an interim review) |
| review final gate | final_review                 | completed (pass — the sole ai-sync trigger); final_review (changes required — the entry sends the task back to dev remediation); in_progress (only if final_review was set in error, i.e. the task is not dev-complete — record why) |
| any session       | any                          | blocked (a question for the human, ai-coding-v2.md §10) |

A dev session never sets `completed`. A review session never advances the
lifecycle forward.

## 4. Task body

Must include a `## Session log` section, appended one entry per session-end:

```
## Session log

### YYYY-MM-DD / $CLAUDE_CODE_SESSION_ID / (status_before → status_after)
- Done: ...
- Next: ...
- Open: ...
```

Field semantics (split by time-direction):

- **Done** — what happened this session: committed changes, decisions made
  (incl. rejected alternatives + rationale), and truths learned worth
  recording. Past/history. This is the source `/ai-sync-v2` absorbs from at
  close-out — write to enough fidelity that absorption needs no re-derivation.
- **Next** — the immediate forward work the next session resumes (handoff).
- **Open** — forward-looking unresolved items: open questions, deferred
  decisions, blockers to revisit. In the final entry of a task reaching
  `completed` this is `none`: by then every forward-looking item has been
  resolved or spawned as a pending task.

The session log is the single source for cross-session handoff and the input for close-out absorption review.

## 5. Tasks index

`.ai-tasks/index.md` lists active tasks only (`pending` / `in_progress` / `blocked`), one line each. Completed tasks move to archive (per close-out below). Must stay small enough for eager loading.

## 6. Lifecycle close-out

Close-out (admission → absorb → archive) is executed by `/ai-sync-v2` at the Stop hook trigger.

Absorption may be **retroactive**: multiple archived tasks may collectively warrant a Snapshot update that no single task did alone.

Close-out also performs remaining-task reconciliation: audit every other
active task under `.ai-tasks/` excluding `archive/`, update blockers/scope/
assumptions/acceptance criteria/prefetch/estimate/status when the completed
task changes them, and report `Remaining-task audit: checked N active task(s);
updated ...; unchanged ...` before ending. This audit is required even when no
remaining task changes.

## 7. Archive

`.ai-tasks/archive/` holds all completed tasks as files (filename: `YYYY-MM-DD-<slug>.md`). Not in the eager set. Retrieval via `ls .ai-tasks/archive/` (filename-sorted = chronological) and `grep -l <keyword> .ai-tasks/archive/*.md` for body matches.
