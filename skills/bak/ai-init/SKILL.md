---
name: ai-init
description: Initialize .ai/ knowledge base for a project. Use only when .ai/ does not exist yet. One-time setup.
---

# .ai/ Initialization

Only run when `.ai/` directory does not exist or is empty.

## Step 1: Check

If `.ai/index.md` already exists, output "`.ai/` already initialized" and stop.

## Step 2: Assess repository

- Empty/near-empty repo → ask the user for an initial project description.
- Existing code → inspect structure and source files to infer project context.

## Step 3: Create `.ai/` files

Generate all files below. Fill content from Step 2.
For unknown sections, use `<!-- TODO -->` marker.
Set all `last-updated` to today's date.

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

## Templates

Every file begins with:

```markdown
---
last-updated: YYYY-MM-DD
---
```

### index.md

```markdown
# .ai/ Index

## Documents

| Document | File | Content |
|---|---|---|
| Overview | overview.md | Purpose, scope, users, non-goals, tech stack, repo layout |
| Architecture | architecture.md | System diagram, components, layers, data flow, external deps |
| Design | design.md | Principles, tradeoffs, patterns, anti-patterns |
| Modules | modules.md | Module ownership, responsibilities, dependencies, boundaries |
| APIs | apis.md | Service interfaces, messaging contracts, conventions |
| Features | features.md | Capabilities, status, cross-module behavior |
| Conventions | conventions.md | Code style, naming, error handling, testing, git workflow |
| Map | map.md | Feature↔module↔API cross-reference |

## Tasks

`tasks/` contains historical records (write-once): design decisions, pitfalls, tech debt.

**When to look**: before making design decisions, when debugging recurring issues,
when working on areas with known tradeoffs.

**How to find**: check map.md "Key Decisions" column first → scan task filenames if no match.
Do not open all task files.
```

### overview.md

```markdown
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
# Modules

## Module Index

| Module | Location | Description |
|---|---|---|

## Module Details

<!--
### module-name
Location: `src/module-name/`
Responsibility: what this module owns
Dependencies: ...
Depended on by: ...
-->

## Boundary Rules
<!-- How modules interact, what is allowed/forbidden -->
```

### apis.md

```markdown
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
# Features

## Feature Index

| Feature | Status | Key Modules | Key APIs |
|---|---|---|---|

## Feature Details

<!--
### Feature Name
Status: active | planned | deprecated
Modules involved: ...
APIs involved: ...
Key behaviors: ...
Known limitations: ...
-->
```

### conventions.md

```markdown
# Conventions

## Code Style
## Naming
## Error Handling
## Logging
## Directory Organization
## Git Workflow
## Commit Messages
## Testing Conventions
## Dependencies
```

### map.md

```markdown
# Project Map

## Feature → Modules

| Feature | Modules | Key Decisions |
|---|---|---|

## Feature → APIs

| Feature | APIs |
|---|---|

## Module → Key Responsibilities

| Module | Responsibilities |
|---|---|
```

### Task file template

Task files: `.ai/tasks/YYYY-MM-DD-brief-title.md`

```markdown
# Task: <title>

Date: YYYY-MM-DD
Type: decision | pitfall | tech-debt | ...
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

Create task files only for non-trivial design decisions, tricky bugs, or architectural tradeoffs.

## Scaling

When a document exceeds ~3000 tokens, split:

```
.ai/modules.md → .ai/modules/index.md + .ai/modules/{topic}.md
```

Update index.md and CLAUDE.md `@` references when splitting.
