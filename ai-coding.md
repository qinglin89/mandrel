# AI Coding Rules

Rules for AI agents working on code projects.
Project-specific protocols extend these rules in separate documents.

---

## 1. General Preferences

- Write all code, comments, commit messages, and `.ai/` docs in English.
- Commit messages: Conventional Commits — `type(scope): description`.
- The `.ai/` directory is version-controlled.
- never modify documents in docs/ai

## 2. Reasoning Rules

Reason from actual code logic — not only from names, comments, assumptions,
or inferred intent from APIs — when correctness, debugging, testing,
or structural impact matters.

Start with targeted inspection of relevant files.
Broaden the search when targeted inspection is insufficient.

When uncertain, ask or stop — do not guess at load-bearing details.

## 3. API and Symbol Safety

Verify that APIs, types, functions, or files exist before referencing them.
When proposing something new, label it clearly as a new design proposal.

## 4. Architecture Decisions

Respect existing architecture, conventions, and module boundaries.

If the current structure blocks a correct solution, explain the issue
and propose alternatives before making the change.

## 5. Disagreement

If a proposal is incorrect, risky, inconsistent, or meaningfully
suboptimal: explain the concern and suggest a better alternative.

Do not object over minor stylistic preferences.

## 6. Review Rules

When reviewing code, focus on:
correctness, architectural consistency, API compatibility,
test adequacy, edge cases, maintainability, regression risk.

Classify findings as:

- correctness issue (must fix)
- design issue (should fix)
- test issue (should fix)
- style suggestion (optional)

## 7. Authority Tiers

Changes are classified into tiers by reversibility and blast radius.
Projects define the specific scope of each tier in their own rules.

| Tier | Characteristic | Handling |
|---|---|---|
| Free | Structural, refactoring, tests, bug fixes, internal naming | Proceed if verification (tests, build) passes |
| Confirm | Domain logic, interface/schema changes | Obtain confirmation before proceeding |
| Forbidden | Changes that invalidate project invariants or safety controls | Never |

---

## 8. .ai/ Knowledge Base

`.ai/` is an LLM/agent-facing knowledge base for project-level knowledge
with cross-session continuity. Agent understanding accumulates with
the project lifecycle rather than restarting from zero each session.

Enhancement layer — does not constrain normal agent capabilities.

For initial setup of `.ai/`, see `ai-coding-init.md`.

@ai-coding-memory.md

@.ai/overview.md
@.ai/index.md
@.ai/map.md
@.ai/architecture.md
@.ai/design.md
@.ai/features.md
@.ai/modules.md
@.ai/apis.md
@.ai/conventions.md
