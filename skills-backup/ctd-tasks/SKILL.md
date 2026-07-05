---
name: ctd-tasks
description: Fast status overview of .ai/tasks/*.md entries grouped by state (active / handoff / proposal / deferred / done / superseded). Use when the user asks "what's pending", "where are we on tasks", "show me active tasks", or similar. Manual-invoke only — never auto-trigger.
---

# ctd-tasks

List tasks in `.ai/tasks/` grouped by frontmatter `status:`. Read-only. The
project's `.ai/tasks/INDEX.md` is the curated narrative; this skill is for
quick mechanical scans that don't rely on INDEX being up to date.

## How to run

Execute the bundled scan script via the Bash tool from the repo root:

```bash
bash ~/.claude/skills/ctd-tasks/scan.sh           # pending only (default)
bash ~/.claude/skills/ctd-tasks/scan.sh --all     # every entry
bash ~/.claude/skills/ctd-tasks/scan.sh --status done     # filter one group
```

Output is a plain text table. `pending` means: not done, not obsolete, not
superseded — i.e. active / handoff / proposal / deferred / (any with no
status).

## Expected output shape

```
🟢 Active  (2)
  active         | 2026-04-14-golden-harness-handoff.md     | A-family end-to-end...
  active         | 2026-04-13-v1-golden-test-harness.md     | four-family test design...

🟠 Deferred  (3)
  deferred       | 2026-04-12-performance-report-three-line-design.md | DESIGN ONLY (v1.1)...  (revisit: 2026-05-12)
  ...

pending total: 5  (use --all to include done/superseded)
```

## When NOT to run

- Don't run from outside a repo that has `.ai/tasks/`. The script exits
  quietly (stderr note).
- Don't use this as a substitute for reading `INDEX.md` when you need the
  curated "why it matters" context for a task.
- Don't use to modify tasks — read-only.

## After running

Report the output to the user. If `(missing)` status rows appear, note them
as drift (frontmatter not following convention) and ask if the user wants to
fix them.
