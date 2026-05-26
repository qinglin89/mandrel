---
name: ai-sync-v2
description: Apply absorption for a completed task. Reviews the task's session log and the session's working context (decisions, code changes, reasoning), applies admission tests from the memory protocol; if any finding passes, absorbs into Snapshot. Always archives the task. Invoked by Stop hook when task.status reaches completed; may also be called manually.
---

`ai-coding-memory-v2.md` (admission §3, propagation §4) and
`ai-coding-tasks-v2.md` (task shapes + lifecycle close-out §5) are both in
baseline context (loaded via `CLAUDE.md` → `ai-coding-v2.md`). Refer
directly; do not re-Read.

`.ai/index.md`, `.ai/map.md`, `.ai-tasks/index.md` are also baseline-loaded.
Read other content docs on demand per memory §2 (loading contract).

## Invocation

Stop hook fires this skill when the active task transitions to
`status: completed`. May also be called manually with
`$ARGUMENTS = <task_id>`.

## Inputs

- **Task file** `.ai-tasks/<task_id>.md` (id from `$ARGUMENTS` or the
  active session's `claimed-by` field). Source of: session log, frontmatter,
  prefetch.
- **Session context**: this session's conversation — what was reasoned,
  decided (and rejected), and modified. Available because the skill runs
  in-session.
- **Code state for verification only**: `git status` / `git log` (covers
  in-session commits) / `git diff HEAD` (covers uncommitted edits). Use to
  cross-check that absorption claims match what landed.

## Procedure

1. Read the task file. Verify `status: completed` AND working tree clean
   (`git status --porcelain` empty). If either check fails, print state
   and abort.
2. Gather absorption inputs:
   - Full `## Session log` from the task (all entries — multi-session
     tasks accumulate).
   - Session conversation: what was reasoned, decided, modified, rejected.
   - Code state (verification only): `git status`, `git log` for any
     in-session commits, `git diff HEAD` for uncommitted edits.
3. List candidate findings. Walk session-log `Done` items + session
   context (decisions, rejected alternatives, design notes); identify
   discrete facts / decisions / invariants that might warrant Snapshot.
4. Apply protocol §3 admission tests to each finding (derivation cost /
   stability / leverage). Borderline → compress to one keyword-heavy line
   and keep.
5. **Plan absorption**.

   If ≥1 finding passed admission:
   a. Route each admitted finding to its target content doc via
      `.ai/index.md` (its catalog defines which doc covers each topic).
      Both invariant docs (`overview` for scope changes, `architecture`
      for boundary changes, `design` for principles / tradeoffs / patterns
      / anti-patterns) and inventory docs (`modules` / `apis` / `features`
      / `conventions` / etc.) are valid targets.
   b. Draft each proposed edit: which doc, which section, what content.
   c. Identify horizontal-sync impacts via `.ai/map.md`.
   d. Flag whether the relation graph changed (→ routing update needed in
      `.ai/index.md` / `.ai/map.md`).

   If no admitted findings: plan is empty (archive only).

6. Print the plan summary before proceeding to apply:
   - Absorbing? yes / no
   - If yes: target docs + drafted edits + horizontal-sync impacts +
     routing-update flag
   - Open items left for the next session

7. Apply:

   **If absorbing** (execute the plan from step 5):
   a. Apply each planned edit to its target doc.
   b. Apply §4 propagation: update related docs identified via map;
      update `.ai/index.md` / `.ai/map.md` if the relation graph changed.
   c. If any touched doc exceeds §4 size limit: either split inline per §4 upgrade procedure (when the restructure is small) OR defer — append the doc to `.ai/.housekeeping-pending` (format: `<path> SIZE <current>/<limit>`) and surface the deferral in the summary. SessionStart hook picks up the flag; `/ai-housekeeping` resolves it.
   d. Bump `last-updated:` (today) and `verified-against:` (current HEAD SHA) on touched docs.

   **Always**:
   a. Move task file to `.ai-tasks/archive/` (creating the directory if it doesn't exist).
   b. Remove task line from `.ai-tasks/index.md`.

8. Commit absorption changes (snapshot edits) so the working tree is clean post-absorption.

9. Verify:
   - Task absent from `.ai-tasks/index.md`.
   - Touched Snapshot docs within §4 size limits (or flagged in `.ai/.housekeeping-pending` per step 7c).
   - Routing entries consistent with edits.
10. Print summary: absorbed? / docs touched / task archived / (if any) housekeeping flagged.

## Edge cases

- **Mixed findings** (admit-worthy + chaff in one task): absorb only the
  admitted facts; the task itself still archives.
- **Idempotency**: if the task file is absent or `status ≠ completed`,
  print state and exit. Do not silently re-run.
- **No findings**: no absorption; task still archives.
- **Routing-doc size**: `.ai/index.md` ≤ 1500 tokens (§4) — apply tighter
  scrutiny when editing routing.
