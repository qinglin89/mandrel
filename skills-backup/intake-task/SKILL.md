---
name: intake-task
description: Create a new pending task from a user request, conforming to the frontmatter shape in ai-coding-tasks-v2.md (id, status, session-est, prefetch, etc.). Generates the task file under .ai-tasks/ and registers it in .ai-tasks/index.md. Invoke when the user wants to start new work that no existing active task covers.
---

`ai-coding-tasks-v2.md` is the contract (frontmatter §2, task body §4,
tasks index §5). This skill produces task files that conform.

## Invocation

When the user requests new work and no active task in `.ai-tasks/index.md` covers it. May also be called manually with the user's request as `$ARGUMENTS`.

## Inputs

- User's natural-language request (the new work).
- Existing `.ai-tasks/index.md` (to check for duplicates / overlap).
- `.ai/index.md` routing (to suggest prefetch candidates from the catalog).

## Procedure

1. **Capture intent.** Read the user request. If unclear, ask follow-up questions:
   - What should this task accomplish?
   - How does the user know it's done? (acceptance criteria)
   - Any external blockers?

2. **Check duplicates.** Scan `.ai-tasks/index.md` active list. If a similar task exists, propose joining it (extend that task's scope / log) instead of creating new. Ask user before proceeding.

3. **Generate task spec**:

   | Field | Value |
   |---|---|
   | `id` | `<YYYY-MM-DD>-<short-kebab-slug>` (slug from purpose) |
   | `status` | `pending` |
   | `session-est` | `0/<total>` — estimate `<total>` by dividing expected work by ~200k tokens (one effective context window per session for Opus 4.7 1M context) |
   | `blockers` | from user statement + LLM analysis of dependencies on existing active tasks; empty `[]` if none |
   | `prefetch` | suggest 2–5 **lazy** content docs (modules / apis / features / sub-indexes / etc.) from `.ai/index.md` routing relevant to the task. **Exclude eager docs** (overview / architecture / design / conventions) — already in baseline context per memory §2. |
   | `claimed-by` | empty at intake; set/updated at each session entry that picks up the task (see ai-coding-v2.md §10 Entry) |

   If `<total>` > 5, flag scope; suggest splitting into smaller independent tasks before proceeding.

4. **Draft task body**:

   ```
   # <Title>

   ## Goal
   <one paragraph: the why and the what>

   ## Scope
   <bullet list: the overall task scope>

   ## Acceptance
   <bullet list: how we know the overall task is done>

   ## Session plan
   ### session-1
   Scope:
   - <one-session-sized slice>
   Acceptance:
   - <slice-specific checks>

   ## Session log
   ```

   Include `## Session plan` only when `session-est` total is greater than 1.
   Create one simple slice per estimated dev advancement session
   (`session-1`, `session-2`, ...). Each slice has short Scope and Acceptance
   bullets. The plan is intentionally soft: later dev advancement sessions may
   split current/future unimplemented slices if preReEst finds a slice too large.

   Keep body short — task files are operational, not documentation. Detail accumulates in session log as work progresses.

5. **Show the draft to the user**: full task file + the index entry to be appended. Wait for confirmation or refinement.

6. **On confirm**, write:
   - `.ai-tasks/<id>.md` — full frontmatter + body
   - `.ai-tasks/index.md` — append a row to the active table (create table header if file currently has only `(none)`)

7. **Print summary**: `<id>` / title / file path. User can now invoke the task at the next session entry.

## Tasks index format

When the first task is added, replace the `(none)` placeholder with a table:

```
# Active tasks

| id | title | status | session-est | blockers |
|---|---|---|---|---|
| <id> | <title> | pending | 0/<total> | [] |
```

Subsequent tasks append rows. When a task transitions away (completed → archived by `/ai-sync-v2`), its row is removed.

## Edge cases

- **Overlap with existing active task**: propose join, not new. New task only when scope is genuinely independent.
- **Scope too large** (`session-est` > 5): suggest decomposition into smaller tasks before creating.
- **Missing index.md**: create `.ai-tasks/index.md` with frontmatter + heading + `(none)` placeholder, then add the new entry.
- **Slug collision** (rare same-day same-slug): append `-2`, `-3`, etc.
- **Status other than pending**: not allowed at intake. New task always starts pending; transitions happen at session entry.
