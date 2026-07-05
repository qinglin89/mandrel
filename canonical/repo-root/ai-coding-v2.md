# AI Coding Rules

Rules for AI agents working on code projects.
Project-specific protocols extend these rules in separate documents.

---

## 1. General Preferences

- All artifacts in English.
- The `.ai/` directory is version-controlled, and `.ai-tasks/` is not version-controlled(gitignored).

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
Bootstrap: `ai-coding-init-v2.md` via `/ai-init`.

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
3. Claim the task: set `claimed-by` to `$CLAUDE_CODE_SESSION_ID@<utc-iso-ts>` (UTC ISO 8601, e.g., `2026-05-26T09:30:00Z`, from `date -u +%Y-%m-%dT%H:%M:%SZ`); if status was `pending`, transition to `in_progress`. A dev session also increments `session-est` `<current>` by 1 as part of the claim (review sessions do not consume the estimate — tasks protocol §2).
4. Pre-load `prefetch:` content docs listed in the task.

**Work**:

- Consult `.ai/` content docs only via routing in `.ai/index.md` and `.ai/map.md` — do not grep across `.ai/`.
- Do not edit `.ai/` mid-task. Snapshot writes go only through `/ai-sync-v2` at close-out. A `.ai/` gap or discrepancy noticed while working is a truth learned — it goes in the session-log entry's Done like any other fact.
- Modify code per the authority tiers (§7).
- New work discovered mid-task: if it doesn't block current scope, spawn a pending task via `/intake-task`. If it blocks current scope, adjust the current task body / plan instead — do not spawn.
- Adjust the active task's body / scope / `session-est` as understanding sharpens. Record the adjustment in the next session-log entry.
- Calibrate `session-est` to one effective context window per session (~200k tokens for Opus 4.7 1M context). Stop hook prompts mid-session handoff on context overage. A context-overage wrap-up is an ordinary clean handoff: clean tree, session-log entry whose Next carries the handoff, re-estimated `session-est`. One dev session is one reviewable unit — an advancement session's landed work is reviewed before the next dev session advances, regardless of why the session ended (planned convergence or context overage). Sole exception: a remediation session (§11) that must wrap before its fix set is complete marks the entry with `- Handoff: continuation` — remediation resumes in a fresh session and re-review waits until the fix set completes. An advancement session never writes the marker.

**End**:

1. Commit all uncommitted changes so the working tree is clean (`git status --porcelain` empty).
2. Append `## Session log` entries (Done / Next / Open).
3. Update task `status` per the status-transition table
   (`ai-coding-tasks-v2.md` §3).
4. Backfill `prefetch:` with what was actually consulted.
5. Update any **other** pending tasks whose scope or blockers shifted due to this session. (Current task's row is `/ai-sync-v2`'s domain — see memory §1.)

On `task.status == completed`, the Stop hook fires `/ai-sync-v2` to apply close-out per `ai-coding-tasks-v2.md` §6 (Lifecycle close-out). Do not pre-archive the task, pre-edit `.ai/`, or pre-remove the task's row from `.ai-tasks/index.md` — `/ai-sync-v2` owns the entire close-out.

## 11. Session roles (dev / review)

The invocation verb selects the session role — a session-level distinction,
independent of tool and model:

- `task <id>` → dev role: develop or continue the task per §10.
- `review <id>` → review role: evaluate per §6; read and follow
  `ai-coding-review-v2.md` (procedure, review-entry shape, convergence rules).

Status transitions for both roles are defined in the
status-transition table (`ai-coding-tasks-v2.md` §3). Role conduct:

- A dev session treats review findings as claims to verify against actual code
  (§2) before implementing. A finding verified invalid is a §5 dispute: record
  it in the session-log entry (do not silently fix or silently skip) —
  disputed findings escalate to the user per the convergence rules in
  `ai-coding-review-v2.md`; they do not loop.
- Remediation before advancement: while the latest review entry's verdict is
  `changes-requested`, a dev session only remediates that review — fix the
  valid findings, record disputes for invalid ones, hand back to review. It
  does not advance new scope; new scope resumes after a `pass` verdict.
  (Keeps re-reviews delta-only and each convergence group single-chained.)
- `completed` is the sole trigger for the `/ai-sync-v2` close-out; per the
  table only a final-gate review session sets it.
- Every non-`completed` status is in-flight: the §10 End discipline (clean
  tree, session-log entry) applies to review sessions unchanged.
