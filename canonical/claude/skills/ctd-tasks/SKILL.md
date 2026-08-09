---
name: ctd-tasks
description: Fast status overview of `.ai-tasks/*.md` entries grouped by lifecycle status (pending / in_progress / final_review / blocked, plus the archive). Use when the user asks "what's pending", "where are we on tasks", "show me active tasks", or similar. Manual-invoke only — never auto-trigger.
---

# ctd-tasks

List tasks in `.ai-tasks/` grouped by frontmatter `status:` (taskfile schema:
`.ai-protocol/meta/taskfile.md`). Read-only. `.ai-tasks/index.md` is the
curated active list and is already in baseline context; this skill reads the
task files themselves, so it also shows what the index omits — `session-est`
progress, `blockers`, and any file whose status the index has not caught up
with.

## How to run

Execute the bundled scan script via the Bash tool from the repo root:

```bash
bash .claude/skills/ctd-tasks/scan.sh                    # active only (default)
bash .claude/skills/ctd-tasks/scan.sh --all              # active + archived
bash .claude/skills/ctd-tasks/scan.sh --status blocked   # filter one group
```

Output is a plain text table. `active` means every in-flight status —
`pending`, `in_progress`, `final_review`, `blocked` — plus any file whose
`status:` is missing or unrecognized. Completed tasks leave `.ai-tasks/` for
`.ai-tasks/archive/` at close-out, so they appear only under `--all` or
`--status archived`.

`--status` accepts `pending`, `in_progress`, `final_review`, `blocked`,
`completed`, `other`, `archived`.

## Expected output shape

```
📋 Pending  (1)
  pending        | 2026-07-31-evolution-batch-controller.md       | 0/3   | Evolution batch controller

🟢 In progress  (1)
  in_progress    | 2026-08-08-stale-workflow-skills.md            | 1/1   | Retire or repair the pre-cut workflow skills

⛔ Blocked  (1)
  blocked        | 2026-08-02-report-feed.md                      | 1/2   | Report feed  (blockers: external:awaiting API spec)

active total: 3  (2 archived; use --all to include them)
```

Columns are `status | file | session-est | title`, with `blockers` appended
when the task has any.

## When NOT to run

- Don't run from outside a repo that has `.ai-tasks/`. The script exits
  quietly (stderr note).
- Don't use it to answer "what is task X about" — that is the task file's
  Goal/Scope and its `## Session log`, which this scan does not read.
- Don't use to modify tasks — read-only.

## After running

Report the output to the user. Two rows are drift worth calling out rather
than just listing:

- `⚠  Completed (unarchived)` — a task reached `completed` but never went
  through close-out (`.ai-protocol/workflow/skills/closeout.md`), so it is
  still in the active directory and still in the index.
- `⚠  Other / no status` — frontmatter missing or off-schema.

Report either as drift and ask whether the user wants it fixed; do not fix it
from this skill.
