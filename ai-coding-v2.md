# AI Coding Rules

Rules for AI agents working on code projects.
Project-specific protocols extend these rules in separate documents.

---

## 1. General Preferences

- All artifacts in English.
- The `.ai/` directory is version-controlled.

## 2. Reasoning Rules

Reason from actual code logic — not only from names, comments, assumptions, or inferred intent from APIs — when correctness, debugging, testing, or structural impact matters.

Start with targeted inspection of relevant files. Broaden the search when targeted inspection is insufficient.

When uncertain, ask or stop — do not guess at load-bearing details.

## 3. API and Symbol Safety

Verify that APIs, types, functions, or files exist before referencing them. When proposing something new, label it clearly as a new design proposal.

## 4. Architecture Decisions

Respect existing architecture, conventions, and module boundaries.

If the current structure blocks a correct solution, explain the issue and propose alternatives before making the change.

## 5. Disagreement

If a proposal is incorrect, risky, inconsistent, or meaningfully suboptimal: explain the concern and suggest a better alternative.

Do not object over minor stylistic preferences.

## 6. Review Rules

When reviewing code, focus on: correctness, architectural consistency, API compatibility, test adequacy, edge cases, maintainability, regression risk.

Classify findings as:

- correctness issue (must fix)
- design issue (should fix)
- test issue (should fix)
- style suggestion (optional)

## 7. Authority Tiers

Changes are classified into tiers by reversibility and blast radius. Projects define the specific scope of each tier in their own rules.

| Tier | Characteristic | Handling |
|---|---|---|
| Free | Structural, refactoring, tests, bug fixes, internal naming | Proceed if verification (tests, build) passes |
| Confirm | Domain logic, interface/schema changes | Obtain confirmation before proceeding |
| Forbidden | Changes that invalidate project invariants or safety controls | Never |

---

## 8. Memory system

`.ai/` is the project's cross-session memory: a timeless distillation of project understanding (overview, architecture, modules, apis, features, design, conventions, map). Each session inherits prior knowledge without re-derivation from source.

Protocol: `ai-coding-memory-v2.md` (data shapes, admission, maintenance).
Bootstrap: `ai-coding-init.md` via `/ai-init`.

@ai-coding-memory-v2.md

@.ai/index.md
@.ai/map.md
@.ai/overview.md
@.ai/architecture.md
@.ai/design.md
@.ai/conventions.md

## 9. Work tracking

`.ai-tasks/` holds active work — one file per task. Each task accumulates a session log carrying handoff state across sessions. Tasks are the pipeline by which new knowledge enters memory: `/ai-sync-v2` reviews completed tasks at the Stop hook, absorbs admission-passing findings into `.ai/`, then archives the task.

Protocol: `ai-coding-tasks-v2.md` (frontmatter, session log shape, lifecycle close-out).

@ai-coding-tasks-v2.md

@.ai-tasks/index.md

## 10. Session workflow

**Entry**:

1. Consult `.ai-tasks/index.md` (already in context).
2. Pick an `in_progress` (resume) or `pending` task. If no existing task fits new work, invoke `/intake-task` to create a pending one (ad-hoc work bypassing this is not allowed).
3. Claim the task: set `claimed-by` to `$CLAUDE_CODE_SESSION_ID@<utc-iso-ts>` (UTC ISO 8601, e.g., `2026-05-26T09:30:00Z`, from `date -u +%Y-%m-%dT%H:%M:%SZ`); if status was `pending`, transition to `in_progress`.
4. Pre-load `prefetch:` content docs listed in the task.

**Work**:

- Consult `.ai/` content docs only via routing in `.ai/index.md` and `.ai/map.md` — do not grep across `.ai/`.
- Do not edit `.ai/` mid-task. Snapshot writes go only through `/ai-sync-v2` at close-out. Observed update needs → record in session-log `Open`.
- Modify code per the authority tiers (§7).
- New work discovered mid-task: if it doesn't block current scope, spawn a pending task via `/intake-task`. If it blocks current scope, adjust the current task body / plan instead — do not spawn.
- Adjust the active task's body / scope / `session-est` as understanding sharpens. Record the adjustment in the next session-log entry.
- Calibrate `session-est` to one effective context window per session (~200k tokens for Opus 4.7 1M context). Stop hook prompts mid-session handoff on context overage.

**End**:

1. Commit all uncommitted changes so the working tree is clean (`git status --porcelain` empty).
2. Append a `## Session log` entry (Done / Next / Open).
3. Update task `status` (one of `in_progress` / `blocked` / `completed`).
4. Backfill `prefetch:` with what was actually consulted.
5. Update any **other** pending tasks whose scope or blockers shifted due to this session. (Current task's row is `/ai-sync-v2`'s domain — see memory §1.)

On `task.status == completed`, the Stop hook fires `/ai-sync-v2` to apply close-out per `ai-coding-tasks-v2.md` §5 (Lifecycle close-out). Do not pre-archive the task, pre-edit `.ai/`, or pre-remove the task's row from `.ai-tasks/index.md` — `/ai-sync-v2` owns the entire close-out.
