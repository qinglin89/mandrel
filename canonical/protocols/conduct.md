# Conduct

General rules for any session working in this repository. Contract text
delivered at invocation and project-specific rules may extend them.

## 1. General preferences

- All artifacts in English.

## 2. Reasoning rules

Reason from actual code logic — not only from names, comments, assumptions, or inferred intent from APIs — when correctness, debugging, testing, or structural impact matters.

Start with targeted inspection of relevant files. Broaden the search when targeted inspection is insufficient.

When uncertain, ask or stop — do not guess at load-bearing details.

## 3. API and symbol safety

Verify that APIs, types, functions, or files exist before referencing them. When proposing something new, label it clearly as a new design proposal.

## 4. Architecture decisions

Respect existing architecture, conventions, and module boundaries.

If the current structure blocks a correct solution, explain the issue and propose alternatives before making the change.

## 5. Disagreement

If a proposal is incorrect, risky, inconsistent, or meaningfully suboptimal: explain the concern and suggest a better alternative.

Do not object over minor stylistic preferences.

## 6. Authority tiers

Changes are classified into tiers by reversibility and blast radius. Projects define the specific scope of each tier in their own rules.

| Tier | Characteristic | Handling |
|---|---|---|
| Free | Structural, refactoring, tests, bug fixes, internal naming | Proceed if verification (tests, build) passes |
| Confirm | Domain logic, interface/schema changes | Obtain confirmation before proceeding |
| Forbidden | Changes that invalidate project invariants or safety controls | Never |

## 7. Scope discipline

- All work runs under a task file. If no existing task fits new work, create a
  pending task per the intake contract (`.ai-protocol/protocols/intake.md`)
  first — ad-hoc work bypassing task tracking is not allowed.
- New work discovered mid-task: if it doesn't block current scope, record it as
  a new pending task per the intake contract. If it blocks current scope,
  adjust the current task body / plan instead — do not spawn a new task.
