# Task-completion closeout (boundary skill)

Fires only when a task reaches `status: completed` (with a clean tree).
Context-independent by design: it may run natively in the concluding
conversation (stop-hook chain) or in a caller-dispatched fresh/resumed
conversation. Packaged for invocation as the `/ai-sync-v2` skill; the caller
verifies its results per runbook §4.7. Closeout operations are this skill's
EXCLUSIVE domain — sessions must not pre-absorb, pre-edit `.ai/`, pre-archive
the task, or pre-remove the task's index row.

## Procedure

1. **Verify preconditions** — task `status: completed` AND working tree
   clean; otherwise report state and abort (idempotent: absent file or other
   status → no-op). For a split closure, also verify `Phase: ready`, its
   reserved Target exists as an independent pending task with an index row,
   and the source records complete scope/finding transfer and dependency
   reconciliation. A preparing or incomplete split cannot be archived.
2. **Absorption** — walk the task's full `## Session log` (all entries) plus
   the concluding session's context when available; apply the memory
   protocol's admission tests (`.ai-protocol/meta/memory.md` §3) to each
   candidate finding; absorb passing findings into `.ai/` with §4
   propagation (routing, horizontal sync, size limits — oversize splits may
   defer to `.ai/.housekeeping-pending`). Absorption may be retroactive
   across archived tasks. No admitted findings → archive only. Split closure
   is administrative: neither `completed` nor a transferred finding is
   evidence that the original acceptance criteria passed or a defect was
   fixed. Preserve this distinction when admitting facts into memory.
3. **Archive** — move the task file to `.ai-tasks/archive/` and remove its
   row from `.ai-tasks/index.md`.
4. **Remaining-task reconciliation** — repeat the session-end procedure's
   reconciliation (required even when no remaining task changes): audit every
   other active task, update blockers / scope / assumptions / acceptance
   criteria / prefetch / estimate / status where this completed task changes
   them. For a split, replace dependencies on transferred work with the
   successor id before removing the source id; only actually satisfied
   dependencies disappear. Then remove blockers naming any archived task id;
   a `blocked` task left
   without blockers restores its inferable active status, else `pending`.
5. **Commit** absorption changes so the tree ends clean.
6. **Report** — the final response includes exactly one line in the shape
   (per the taskfile schema):
   `Remaining-task audit: checked N active task(s); updated <ids|none>; unchanged <ids|none>`

## Verification (what the caller checks)

Task file gone from `.ai-tasks/`; archive copy exists; index row removed; no
active task lists an archived task id in `blockers`; no `blocked` task has
empty `blockers`; the `Remaining-task audit:` line is present; the tree is
clean.

## Interrupted split closeout

If the source is already archived with `Split` phase `ready`, `/split-task`
may resume only the remaining reconciliation and verification: reuse its
recorded Target, verify the existing successor, reconcile dependent tasks,
ensure the source index row is absent, and emit the required audit line.
Do not repeat intake or memory absorption. A missing/conflicting successor,
or a successor that has already started, needs a human ruling; never recreate
or overwrite it. Commit any reconciliation changes needed for a clean tree.
