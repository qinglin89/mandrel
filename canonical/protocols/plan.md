# Plan contract

Contract for a read-only planning session: understand one task and return a
**plan-report** to the caller. The report is this contract's only output — a
return value consumed immediately by the caller, deliberately not persisted
in the task file. Conduct rules (`.ai-protocol/protocols/conduct.md`) apply.

## Read-only bounds

A planning session is a read-only shadow of upcoming implementation work. It
must NOT: claim the task, change `session-est` or `status`, append a
session-log entry, edit files, run tests/builds, start services, install
dependencies, generate artifacts, or run long diagnostics.

It MAY do bounded read-only discovery: read the task file, its session log,
the frontmatter `prefetch:` docs, and a small number of directly relevant
source/test files; run short read-only inspection commands such as `rg`,
`sed`, `ls`, `git show`, and `git diff --name-only`.

## Report shape

Reply with exactly these headings, using `None identified` for any empty
section:

- `## Goal / Acceptance`
- `## Confirmed Facts`
- `## Assumptions / Unknowns`
- `## Work Approach`
- `## Verification Strategy`
- `## Risks / Likely Failure Points`

`## Work Approach` gives a concise implementation approach and main work
areas; name key files/modules only when they materially clarify the plan —
not a complete file-by-file checklist.

Everything from the `## Goal / Acceptance` line to the end of the reply IS
the plan-report. It is the only planning output ever delivered onward, so it
must stand alone.

## Revision protocol

Each round of feedback has exactly two conforming reply shapes:

- **Revise** — restate the COMPLETE report (optionally a short change summary
  first, then `## Goal / Acceptance` onward, unchanged sections kept
  verbatim). Fold every new fact, constraint, or decision the discussion
  produced into the report — text outside the report is never delivered.
- **Unchanged** — a purely clarifying answer that changes nothing ends with
  the exact line `PLAN-REPORT: unchanged`.

Two caveats keep replies unambiguous: do not start a line with
`## Goal / Acceptance` except to deliver the full report, and never include
the `PLAN-REPORT: unchanged` line in a revision reply.
