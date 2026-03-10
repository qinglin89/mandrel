# Agent Instructions

This is the canonical instruction file for coding agents in this repository.

---

## 0. Prime Directive

This file supplements the agent's built-in capabilities. It does not override them.

If any instruction here conflicts with the agent's native reasoning, tool use,
or session management, the agent's own judgment takes precedence.

The `.ai/` directory provides long-term project memory across sessions.
It is an additive layer — not a constraint framework.

---

## 1. General Preferences

- Write all code, comments, commit messages, and `.ai/` docs in English.
- Commit messages: Conventional Commits — `type(scope): description`.
- The `.ai/` directory is version-controlled.

---

## 2. Project Memory

On session start, check for `.ai/index.md`.

- If it exists: read it, load only documents relevant to the current task.
- If it does not exist: follow `AGENTS-INIT.md` to bootstrap project memory.

Do not load the entire `.ai/` directory. Use `index.md` as the routing entry.

---

## 3. Editing Rules

Do not modify code unless explicitly requested.

Prefer minimal, localized edits.
If a structural change is needed, explain why before making it.

---

## 4. Reasoning Rules

Reason from actual code logic — not only from names, comments, assumptions,
or inferred intent from APIs — when correctness, debugging, testing,
or structural impact matters.

Prefer targeted inspection of relevant files.
Avoid broad repository scans for simple tasks.

When uncertain, ask rather than guess.

---

## 5. API and Symbol Safety

Do not assume non-existent APIs, types, functions, or files exist.
When proposing something new, label it clearly as a new design proposal.

---

## 6. Architecture Respect

Respect existing architecture, conventions, and module boundaries.

If the current structure blocks a correct solution:
explain the issue → propose alternatives → wait for confirmation.

---

## 7. Disagreement

If the user's proposal is incorrect, risky, inconsistent, or meaningfully suboptimal:
explain the concern and suggest a better alternative.

Do not object over minor stylistic preferences.

---

## 8. Review Rules

When reviewing code, focus on:
correctness, architectural consistency, API compatibility,
test adequacy, edge cases, maintainability, regression risk.

Classify findings as:

- correctness issue (must fix)
- design issue (should fix)
- test issue (should fix)
- style suggestion (optional)

---

## 9. Testing Rules

If a test fails:

1. determine what kind of test it is
2. evaluate whether its expectation is correct
3. if correct, fix the implementation — do not weaken the test

Do not casually convert test types or remove coverage without justification.

---

## 10. Documentation Update Policy

Default mode: manual-batch.

Update `.ai/` only when the user explicitly asks, typically after a completed block of work.

When updating:

1. update only affected files
2. update `last-updated` in each modified document
3. update `index.md` and `map.md` if routing changed

Use `.ai/tasks/` for task-specific historical notes.

### .ai/ Writing Style

`.ai/` docs are read by agents, not humans. Optimize for machine parsing:

- Tables over paragraphs. Keywords over full sentences.
- One line per fact. No redundant narrative.
- If one line suffices, do not write three.

`docs/` is for human readers. `.ai/` is for agent readers. Do not mix the styles.
