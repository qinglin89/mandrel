---
name: ai-init
description: Initialize `.ai/` snapshot content for a project. Two modes — greenfield (empty repo, user describes project) and brownfield (existing codebase, multi-pass scan to derive `.ai/`). Follows `ai-coding-init-v2.md`. Assumes infrastructure (CLAUDE.md, protocol files, hooks, gitignore) is already deployed externally.
---

`ai-coding-init-v2.md` is the canonical procedure. This skill orchestrates user-facing flow.

## Precondition

Infrastructure must already be in place (deployed externally, e.g., by a setup script):

- `CLAUDE.md` loads `@ai-coding-v2.md`
- Protocol files present: `ai-coding-v2.md`, `ai-coding-memory-v2.md`, `ai-coding-tasks-v2.md`, `ai-coding-init-v2.md`
- `.claude/hooks/stop-context-check.sh` and `.claude/hooks/session-start-housekeeping-check.sh` installed and executable
- `.claude/settings.json` registers Stop + SessionStart hooks
- `.gitignore` excludes `.ai-tasks/`
- User-level skills available: `/intake-task`, `/ai-sync-v2`, `/ai-housekeeping`

If any piece is missing, abort and tell the user which — suggest re-running the infrastructure deploy script.

## Invocation

Manual. First-time setup of `.ai/` content for a project. Not for re-init (use `/ai-housekeeping` for ongoing structural maintenance).

## Procedure

1. **Verify preconditions** (see above). If any missing, abort with a clear list of what's needed.

2. **Refuse re-init**: if `.ai/index.md` already exists, abort: "Already initialized. Use `/ai-housekeeping` for structural maintenance, or normal workflow for content updates."

3. **Detect mode**:
   - Empty / near-empty repo (≤ ~20 source files, README absent or stub) → **greenfield**
   - Substantial codebase (working source, build artifacts, README with content) → **brownfield**
   - Ambiguous → ask the user

4. **Execute mode-specific procedure per `ai-coding-init-v2.md`**:

   **Greenfield**:
   - Ask user for: purpose, users, scope, non-goals, tech stack
   - Generate `.ai/` skeletons from the description
   - Mark sections lacking user input as `<!-- TODO -->`
   - **Derive initial pending tasks** from the description (scaffolding, design decisions, infrastructure setup, first features). Each becomes a `.ai-tasks/<id>.md` per `ai-coding-tasks-v2.md` §2/§3 with `status: pending`, `session-est: 0/<rough>`, prefetch where applicable

   **Brownfield** (5 passes per init-v2):
   1. **Inventory** — read README, top-level dirs, build files (`go.mod` / `package.json` / etc.) → produce `overview.md`, skeleton `architecture.md`
   2. **Module survey** — per-directory deep read; **fan out one sub-agent per top-level module** → produce `modules.md`. If any single module's content would exceed §4 size limit, initialize in directory form (`modules/<name>/index.md` + sub-files) rather than producing oversize content
   3. **Cross-reference** — outputs of pass 1+2 → produce `map.md`, `features.md`
   4. **Conventions sniff** — 5–10 representative files (test, error, style) → produce `conventions.md`
   5. **User review** — present produced docs to user for sign-off

5. **Initialize tasks layer**:
   - `mkdir -p .ai-tasks/`
   - **Greenfield**: write each derived initial task as `.ai-tasks/<id>.md` (id format `YYYY-MM-DD-<slug>`); populate `.ai-tasks/index.md` with one row per task.
   - **Brownfield**: create `.ai-tasks/index.md` with `(none)` placeholder. Tasks emerge later via `/intake-task` as work begins.

6. **Stamp frontmatter** on every `.ai/` doc:
   - `last-updated: <today>` (`YYYY-MM-DD`)
   - `verified-against: <full 40-char SHA from git rev-parse HEAD>`

7. **Verify**:
   - All `.ai/` docs have correct frontmatter
   - All snapshot docs within §4 size limits (directory form used where needed)
   - `.ai-tasks/index.md` exists with `(none)`

8. **Commit**: `chore(.ai): initial setup via /ai-init`

9. **Print summary**:
   - Mode used (greenfield / brownfield)
   - Docs created (with which used directory form)
   - Total snapshot size
   - Suggested next step: `/intake-task` to file the first task

## Edge cases

- **Infrastructure incomplete**: abort with checklist of missing pieces; suggest infrastructure deploy script
- **Already initialized** (`.ai/index.md` exists): abort; redirect to `/ai-housekeeping` or normal workflow
- **Brownfield scan finds oversize module**: initialize that module in directory form from the start, not as a single oversize file
- **User aborts mid-procedure**: leave partial state; user can resume manually or re-invoke `/ai-init` (precondition #2 will see `.ai/index.md` exists and refuse — user should clean up first)
- **Greenfield with very thin user description**: generate minimal skeletons with extensive `<!-- TODO -->` markers; let user fill in over time via normal absorption flow
