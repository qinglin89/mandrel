# Agent Initialization

This file is used only when `.ai/index.md` does not exist.
After bootstrapping, this file is not loaded again during normal work.

---

## Bootstrap Process

### Step 1: Assess the repository

- If the repository is empty or nearly empty: ask the user for an initial project description.
- If the repository already contains code: inspect the structure and source files to infer project context.

### Step 2: Create `.ai/` structure

Generate the following files using the templates below:

```
.ai/
  index.md
  overview.md
  architecture.md
  design.md
  modules.md
  apis.md
  features.md
  conventions.md
  map.md
  tasks/
```

### Step 3: Fill content

- For empty repos: fill based on the user's description. Leave unknown sections with a `<!-- TODO -->` marker.
- For existing repos: fill based on inferred project context. Mark all `last-updated` as today's date.

All generated content is a starting point. The user can refine it later.

---

## Document Templates

Every `.ai/` document begins with a metadata header:

```markdown
---
last-updated: YYYY-MM-DD
---
```

### index.md

```markdown
---
last-updated: YYYY-MM-DD
---

# AI Knowledge Router

Do not read the entire `.ai/` directory.
Load only what the current task needs.

## Documents

| Document | File | Use when |
|---|---|---|
| Overview | overview.md | purpose, scope, users, non-goals |
| Architecture | architecture.md | system structure, components, data flow |
| Design | design.md | design principles, tradeoffs |
| Modules | modules.md | module ownership, responsibilities |
| APIs | apis.md | interfaces, service boundaries |
| Features | features.md | capabilities, user-visible behavior |
| Conventions | conventions.md | coding standards, naming, error handling |
| Map | map.md | feature → module → API routing |
| Tasks | tasks/ | historical decisions, past implementation context |

## Routing Table

Load order is a suggestion, not a checklist. Start with the first document.
Stop loading when you have enough context to proceed. Prefer map.md as the
first step — it often tells you exactly which modules/APIs are involved,
making further document loads unnecessary.

| Task Type | Load Order |
|---|---|
| New feature | map → (features → overview → modules if needed) |
| API change | map → (apis → modules → architecture if needed) |
| Bug / debugging | map → tasks(targeted) → modules → code |
| Architecture change | overview → architecture → design → modules |
| Code review | map → relevant docs → code/tests |
| "Why does X exist?" | map → tasks(targeted) → design |
| Conventions question | conventions |
| Onboarding / context | overview → architecture → map |

For tasks/ lookup: check map.md Key Decisions column first to locate the specific task file.
If no match, scan task filenames (not contents) for relevance. Do not open all task files.

## Staleness

When `last-updated` is significantly older than recent code changes:
treat the document as reference only, use code as source of truth.
Notify the user only if an actual inconsistency is found.
```

### overview.md

```markdown
---
last-updated: YYYY-MM-DD
---

# Project Overview

## Purpose
<!-- What does this project do? What problem does it solve? -->

## Users
<!-- Who uses this system? -->

## Scope
<!-- What is in scope? -->

## Non-Goals
<!-- What is explicitly out of scope? -->

## Tech Stack
<!-- Languages, frameworks, key dependencies -->

## Repository Structure
<!-- High-level directory layout -->
```

### architecture.md

```markdown
---
last-updated: YYYY-MM-DD
---

# Architecture

## System Diagram
<!-- High-level component diagram (text or mermaid) -->

## Components
<!-- Major components and their roles -->

## Layers
<!-- If layered: describe the layers -->

## Data Flow
<!-- How data moves through key operations -->

## External Dependencies
<!-- External services, databases, queues -->

## Deployment
<!-- How and where deployed -->
```

### design.md

```markdown
---
last-updated: YYYY-MM-DD
---

# Design Principles and Decisions

## Core Principles
<!-- Guiding design principles -->

## Key Tradeoffs
<!--
### Tradeoff: X vs Y
- Chose: X
- Reason: ...
- Consequence: ...
-->

## Patterns in Use
<!-- Design and architectural patterns adopted -->

## Anti-Patterns to Avoid
<!-- Things explicitly avoided and why -->
```

### modules.md

```markdown
---
last-updated: YYYY-MM-DD
---

# Modules

## Module Index

| Module | Location | Description |
|---|---|---|

## Module Details

<!--
### module-name
Location: `src/module-name/`
Responsibility: what this module owns
Key files: ...
Dependencies: ...
Depended on by: ...
-->

## Boundary Rules
<!-- How modules interact, what is allowed/forbidden -->
```

### apis.md

```markdown
---
last-updated: YYYY-MM-DD
---

# APIs and Interfaces

## Public APIs
<!--
### API Name
- Endpoint / signature
- Purpose
- Key parameters
- Owner module
-->

## Internal Interfaces
<!-- Cross-module interfaces -->

## API Conventions
<!-- Auth, error format, versioning -->
```

### features.md

```markdown
---
last-updated: YYYY-MM-DD
---

# Features

## Feature Index

| Feature | Status | Key Modules | Key APIs |
|---|---|---|---|

## Feature Details

<!--
### Feature Name
Status: active | planned | deprecated
Description: ...
Modules involved: ...
APIs involved: ...
Key behaviors: ...
Known limitations: ...
-->
```

### conventions.md

```markdown
---
last-updated: YYYY-MM-DD
---

# Conventions

## Code Style
## Naming
## Error Handling
## Logging
## Directory Organization
## Git Workflow
## Commit Messages
<!-- Conventional Commits: type(scope): description -->
## Testing Conventions
## Dependencies
```

### map.md

```markdown
---
last-updated: YYYY-MM-DD
---

# Project Map

Quick routing for implementation, review, and debugging.

## Feature → Modules

| Feature | Modules | Key Decisions |
|---|---|---|
<!-- Key Decisions: link to .ai/tasks/ files for traceability -->

## Feature → APIs

| Feature | APIs |
|---|---|

## Module → Key Responsibilities

| Module | Responsibilities |
|---|---|

## Maintenance Rules

Update when:
- a new major feature is added
- a feature depends on a new module
- a new API joins a feature workflow
- responsibilities shift between modules
- a significant decision is recorded in tasks/
```

### Task file template

Task files live in `.ai/tasks/` with naming: `YYYY-MM-DD-brief-title.md`

```markdown
# Task: <title>

Date: YYYY-MM-DD
Related files: <!-- key files affected -->

## Context
<!-- Why was this needed? -->

## Decision
<!-- What was decided and why? -->

## Alternatives Considered
<!-- What else was evaluated? Why rejected? -->

## Impact
<!-- What was affected? Follow-up needed? -->
```

Create a task file when:
- A non-trivial design decision was made
- A tricky bug was diagnosed
- An architectural tradeoff was evaluated

Do not create task files for routine changes.

---

## Scaling Rules

If any `.ai/` document exceeds ~300 lines, split into topic-specific files:

```
.ai/modules/execution.md
.ai/features/backtesting.md
```

When adding files, update `index.md` and `map.md`.
