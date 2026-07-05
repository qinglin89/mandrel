---
name: ai-init
description: Initialize `.ai/` snapshot content for a project. Two modes — greenfield (no substantive target-project surface after excluding deployed AI infrastructure; user describes project) and brownfield (existing target-project codebase, multi-pass scan to derive `.ai/`). Follows `ai-coding-init-v2.md`. Assumes infrastructure (CLAUDE.md, protocol files, hooks, gitignore) is already deployed externally.
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

Manual. First-time setup of `.ai/` content for a project. Ordinary re-init is refused; full rebuild requires explicit human instruction and confirmation. Use `/ai-housekeeping` for ongoing structural maintenance.

## Procedure

1. **Verify preconditions** (see above). If any missing, abort with a clear list of what's needed.

2. **Determine initialization state**:
   - If `.ai/index.md` is absent → first init is allowed.
   - If `.ai/index.md` and `.ai-tasks/index.md` both exist → abort unless the user explicitly requested a full rebuild: "Already initialized. Use `/ai-housekeeping` for structural maintenance, normal workflow for content updates, or explicitly request a full rebuild."
   - If `.ai/index.md` exists but `.ai-tasks/index.md` is absent → treat as partial / broken init; stop and ask the user whether to repair the tasks layer or perform an explicit full rebuild.

3. **Detect mode from target-project surface**:
   - First exclude deployed AI protocol/tooling paths: `ai-coding-*.md`, `.claude/**`, `.codex/**`, `.cursor/**`, `.ai/**`, `.ai-tasks/**`, `.ai-deploy-*.json`.
   - No substantive source, build files, or product documentation remain → **greenfield**. If the repo only contains excluded infrastructure, ask the user for project goals instead of inferring them from that infrastructure.
   - Substantial target-project codebase (working source, build artifacts, product README with content) → **brownfield**.
   - Ambiguous → ask the user.
   - Include normally excluded paths only if the user explicitly says the repo's product is the AI protocol/deployment system itself.

4. **Execute mode-specific procedure per `ai-coding-init-v2.md`**:

   **Greenfield**:
   - Ask user for enough project context to initialize the snapshot: purpose, users, scope, non-goals, tech stack, major capabilities, external systems, deployment/runtime expectations, and known constraints
   - Generate `.ai/` skeletons from the current understanding
   - Mark sections lacking user input as `<!-- TODO -->`
   - Create a bounded set of initial pending tasks that covers the system at the feature/scope level. Target 10-25 tasks; do not exceed 30 during init
   - Treat tasks as a work pool, not a timeline. Split by functional scope or architectural responsibility. Express hard dependencies only through existing `blockers` frontmatter
   - Follow the `.ai-tasks/` task definition from `ai-coding-tasks-v2.md`
   - Ensure at least one initial task is unblocked and specific enough for a dev session to start

   **Brownfield** (5 passes per init-v2; target-project surface only):
   1. **Inventory** — read target-project README, top-level dirs, build files (`go.mod` / `package.json` / etc.) → produce `overview.md`, skeleton `architecture.md`
   2. **Module survey** — target-project per-directory deep read; **fan out one sub-agent per top-level module** → produce `modules.md`. If any single module's content would exceed §4 size limit, initialize in directory form (`modules/<name>/index.md` + sub-files) rather than producing oversize content
   3. **Cross-reference** — outputs of pass 1+2 → produce `map.md`, `features.md`
   4. **Conventions sniff** — 5–10 representative target-project files (test, error, style) → produce `conventions.md`
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
   - `.ai-tasks/index.md` exists; brownfield uses `(none)`, greenfield lists the generated pending tasks

8. **Commit**: `chore(.ai): initial setup via /ai-init`

9. **Print summary**:
   - Mode used (greenfield / brownfield)
   - Docs created (with which used directory form)
   - Total snapshot size
   - Suggested next step: start one unblocked pending task, or use `/intake-task` only if the initial backlog does not cover the desired work

## Edge cases

- **Infrastructure incomplete**: abort with checklist of missing pieces; suggest infrastructure deploy script
- **Already initialized** (`.ai/index.md` and `.ai-tasks/index.md` exist): abort unless the user explicitly requested a full rebuild; redirect ordinary use to `/ai-housekeeping` or normal workflow
- **Partial / broken init** (`.ai/index.md` exists but `.ai-tasks/index.md` is missing): stop and ask whether to repair the tasks layer or perform an explicit full rebuild
- **Infrastructure-only target surface**: after exclusions, treat as greenfield and ask the user for project goals; do not derive product semantics from deployed AI infrastructure
- **Brownfield scan finds oversize module**: initialize that module in directory form from the start, not as a single oversize file
- **User aborts mid-procedure**: leave partial state; user can resume manually or re-invoke `/ai-init` (precondition #2 will see `.ai/index.md` exists and refuse — user should clean up first)
- **Greenfield with very thin user description**: generate minimal skeletons with extensive `<!-- TODO -->` markers; let user fill in over time via normal absorption flow
