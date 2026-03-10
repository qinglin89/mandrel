# AI Development Global Policy

This repository follows an AI-native development workflow.

AI assistants must follow these rules.

---

# Repository Initialization

When entering a repository:

1. Check whether `.ai/` directory exists.

2. If `.ai/` does not exist:

   Generate the following structure:

   .ai/
     index.md
     architecture.md
     design.md
     modules.md
     apis.md
     features.md
     conventions.md
     tasks/

3. Initialize documentation based on repository structure.

4. Summarize detected modules and architecture.

---

# Documentation Responsibilities

## architecture.md

Describes the high-level structure of the system.

Includes:

- main components
- data flow
- system layers

---

## design.md

Explains design philosophy and major technical decisions.

Includes:

- design principles
- tradeoffs
- important patterns

---

## modules.md

Lists major code modules and their responsibilities.

---

## apis.md

Documents key system interfaces.

Includes:

- exported APIs
- important service boundaries

---

## features.md

Lists system capabilities and user-visible features.

---

## conventions.md

Defines project coding conventions.

Includes:

- error handling
- logging
- package structure
- naming conventions

---

## tasks/

Contains historical development decisions.

Each file should describe:

- problem
- decision
- implementation

---

# Editing Rules

Do not modify code unless explicitly requested.

Discussion or explanation should not change code.

Prefer minimal localized edits.

Avoid large refactors unless required.

---

# Testing Rules

Tests define expected behavior.

If a test fails:

1. Determine whether the test is correct.
2. If correct, fix implementation.
3. Do not weaken tests to make them pass.

---

# Reasoning Rules

Always analyze real code logic.

Do not infer behavior from:

- names
- comments
- assumptions

Inspect implementation.

---

# API Safety

Do not invent APIs.

Use only:

- existing symbols
- explicitly defined interfaces

---

# Architecture Respect

Respect existing architecture.

Do not introduce new abstractions without justification.

---

# Disagreement

If the user's proposal seems incorrect or risky:

Explain concerns and propose alternatives.

---

# Uncertainty

If uncertain:

state assumptions clearly.

Never fabricate implementation details.

---

# Documentation Maintenance

After completing tasks:

AI should check whether changes affect:

- architecture
- APIs
- modules
- features

If so:

Propose updates to `.ai/` documentation.

User confirmation is required before applying updates.

---

# Context Loading Strategy

AI should not load all documentation at once.

Instead:

1. Read `.ai/index.md`
2. Identify relevant documentation
3. Load only necessary sections
