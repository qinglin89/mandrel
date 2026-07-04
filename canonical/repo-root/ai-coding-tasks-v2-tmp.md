# Task Protocol

Tasks describe in-flight changes. They live in `.ai-tasks/` and feed into memory via `/ai-sync-v2` at completion.

## 1. Invariants

- One file per task under `.ai-tasks/`.
- One session handles one task.

## 2. Frontmatter

```
---
id: <date-prefixed-slug>
status: pending | in_progress | completed | blocked
session-est: <current>/<total>      # progress/total; e.g., 0/3 pending, 1/3 after first session entry. `current` increments at session entry; raise `total` if estimate undershoots.
blockers: [<task-id> | external:<text>]   # only if status=blocked. task-id refs another active task by id (e.g., 2026-05-26-foo); external:<text> for non-task blockers (e.g., external:awaiting API spec)
prefetch: [<.ai/*.md paths>]        # optional hint; lazy docs only (eager set per memory §2 is already loaded). Pre-load at start. Mutable.
claimed-by: <session-id>@<utc-iso-ts>  # session-id = $CLAUDE_CODE_SESSION_ID; ts = UTC ISO 8601 (e.g., 2026-05-26T09:30:00Z, from `date -u +%Y-%m-%dT%H:%M:%SZ`); set/updated at each session entry
---
```

## 3. Task body

Must include a `## Session log` section, appended one entry per session-end:

```
## Session log

### YYYY-MM-DD / $CLAUDE_CODE_SESSION_ID / (status_before → status_after)
- Done: ...
- Next: ...
- Open: ...
```

Field semantics (split by time-direction):

- **Done** — what LANDED this session: committed changes + decisions made (incl.
  rejected alternatives + rationale). Past/history; record *fact, not
  instruction* — what is true now, not what to do about it. Write to full
  fidelity; when a change supersedes prior behavior, note what it replaced
  ("previously X"), not only the new state.
- **Next** — the immediate forward work the next session resumes (handoff);
  `none` when `status_after == completed` (no next session reads it).
- **Open** — unresolved items carried forward *within this task*: open questions,
  deferred decisions, blockers to revisit. What outlives the task becomes its own
  task (spawned or cross-referenced).

The session log records what happened — the cross-session handoff, and part of
what `/ai-sync-v2` reads at close-out (§5), where what to absorb (and where) is
decided.

## 4. Tasks index

`.ai-tasks/index.md` lists active tasks only (`pending` / `in_progress` / `blocked`), one line each. Completed tasks move to archive (per close-out below). Must stay small enough for eager loading.

## 5. Lifecycle close-out

Close-out (admission → absorb → archive) is executed by `/ai-sync-v2` at the Stop
hook trigger. Its basis is the whole task — definition, background, and session
log — plus the closing session's full context, cross-checked against the commits
that landed; it then applies the memory §3 admission tests and routes each
admitted finding into `.ai/` (memory §4 propagation).

Absorption may be **retroactive**: multiple archived tasks may collectively warrant a Snapshot update that no single task did alone.

## 6. Archive

`.ai-tasks/archive/` holds all completed tasks as files (filename: `YYYY-MM-DD-<slug>.md`). Not in the eager set. Retrieval via `ls .ai-tasks/archive/` (filename-sorted = chronological) and `grep -l <keyword> .ai-tasks/archive/*.md` for body matches.
