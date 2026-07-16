# Intake contract

Contract for turning a work request into one new pending task conforming to
the taskfile schema (`.ai-protocol/meta/taskfile.md`). Its outputs are a task
file under `.ai-tasks/` and a row in `.ai-tasks/index.md`. Conduct rules
(`.ai-protocol/protocols/conduct.md`) apply.

## Inputs

- The requester's natural-language description of the new work.
- `.ai-tasks/index.md` (to check for duplicates / overlap).
- `.ai/index.md` routing (to suggest prefetch candidates from the catalog).

## Procedure

1. **Capture intent.** If unclear, ask the requester: what should this task
   accomplish? How is "done" recognized (acceptance criteria)? Any external
   blockers?
2. **Check duplicates.** Scan the `.ai-tasks/index.md` active list. If a
   similar task exists, propose joining it (extend that task's scope / log)
   instead of creating new. Confirm with the requester before proceeding.
3. **Generate the task spec**:

   | Field | Value |
   |---|---|
   | `id` | `<YYYY-MM-DD>-<short-kebab-slug>` (slug from purpose) |
   | `status` | `pending` — always; transitions happen later, at session entry |
   | `session-est` | `0/<total>` — estimate `<total>` by dividing expected work by one effective context window per session (est semantics per the taskfile schema) |
   | `blockers` | from the request plus analysis of dependencies on existing active tasks; empty `[]` if none |
   | `prefetch` | 2–5 **lazy** content docs from `.ai/index.md` routing relevant to the task. Exclude eager docs (overview / architecture / design / conventions) — already in baseline context per the memory loading contract |
   | `claimed-by` | empty at intake; set at each session entry that picks up the task |

   If `<total>` > 5, flag scope; suggest splitting into smaller independent
   tasks before proceeding.
4. **Draft the task body**: `## Goal` (one paragraph), `## Scope` (bullets),
   `## Acceptance` (bullets), `## Session plan` only when the `session-est`
   total is greater than 1 (one simple slice per estimated advancement
   session, short Scope and Acceptance bullets each — the plan is
   intentionally soft), and an empty `## Session log`. Keep the body short —
   task files are operational, not documentation; detail accumulates in the
   session log as work progresses.
5. **Show the draft to the requester**: full task file plus the index row to
   be appended. Wait for confirmation or refinement.
6. **On confirm, write**: `.ai-tasks/<id>.md`, and append a row to the
   `.ai-tasks/index.md` active table (create the table header if the file
   currently has only the `(none)` placeholder).
7. **Report**: `<id>` / title / file path.

## Edge cases

- **Overlap with an existing active task**: propose join, not new. A new task
  only when scope is genuinely independent.
- **Scope too large** (`session-est` total > 5): suggest decomposition into
  smaller tasks before creating.
- **Missing index.md**: create `.ai-tasks/index.md` with frontmatter +
  heading + `(none)` placeholder, then add the new entry.
- **Slug collision** (same day, same slug): append `-2`, `-3`, etc.
- **Status other than pending**: not allowed at intake.
