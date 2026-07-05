# `.ai/` Initialization

Used for first-time setup of project memory. Ordinary `/ai-init` runs only when `.ai/index.md` does not exist. A full rebuild of an existing snapshot is allowed only when the user explicitly asks for it and accepts the overwrite risk.

## Initialization State

| State | Condition | Handling |
|---|---|---|
| Uninitialized | `.ai/index.md` absent | First init allowed |
| Initialized | `.ai/index.md` present and `.ai-tasks/index.md` present | Ordinary `/ai-init` refuses; use normal workflow for updates, or explicit full rebuild if the user asks |
| Partial / broken init | `.ai/index.md` present but `.ai-tasks/index.md` absent | Stop and ask the user whether to repair the tasks layer or perform an explicit full rebuild |

`.ai-tasks/index.md` is a completeness check after `.ai/index.md` indicates memory exists.

## Target-Project Surface

Mode detection and brownfield scanning operate on the target-project surface, not the whole repository. Before judging greenfield vs brownfield, exclude deployed AI protocol/tooling paths:

- `ai-coding-*.md`
- `.claude/**`
- `.codex/**`
- `.cursor/**`
- `.ai/**`
- `.ai-tasks/**`
- `.ai-deploy-*.json`

Excluded paths do not make a repo brownfield and are not scan inputs.

## Modes

| Mode | Trigger | Approach |
|---|---|---|
| Greenfield | No substantive source, build files, or product documentation remain in the target-project surface | Ask user for project description; fill from description; mark unknowns `<!-- TODO -->` |
| Brownfield | Target-project surface contains an existing codebase | Multi-pass derivation from target-project source (see below) |

## Brownfield Multi-Pass Procedure

| Pass | Inputs | Outputs | Parallelism |
|---|---|---|---|
| 1. Inventory | target-project README, top-level dirs, build files (`go.mod` / `package.json` / `Cargo.toml` / etc.) | `overview.md`, skeleton `architecture.md` | sequential |
| 2. Module survey | target-project per-directory deep read | `modules.md`, optional `modules/<name>.md` splits | **fan-out per module** |
| 3. Cross-reference | outputs of pass 1+2 | `map.md`, `features.md` | sequential |
| 4. Conventions sniff | 5–10 representative target-project files (test, error, style) | `conventions.md` | sequential |
| 5. User review | user inspects; sign off | `last-updated` + `verified-against:` stamps on every doc | — |

All brownfield inputs come from the target-project surface. Do not scan excluded protocol/tooling paths unless the user explicitly declared them part of the product.

Pass 2 is the heavy step — consider fanning out one sub-agent per module when module count is moderate; sequential is fine for large counts to avoid context overflow.

No initial tasks are derived for brownfield — `.ai-tasks/index.md` stays `(none)`; tasks emerge via `/intake-task` as work begins.

## Greenfield Procedure

1. Ask the user for enough project context to initialize the snapshot: purpose, users, scope, non-goals, tech stack, major capabilities, external systems, deployment/runtime expectations, and known constraints. If the target-project surface is empty after exclusions, do not infer project goals from deployed AI infrastructure.
2. Generate `.ai/` skeletons from the current understanding.
3. Leave sections without user input as `<!-- TODO -->`.
4. Create a bounded set of initial pending tasks that covers the system at the feature/scope level. Target 10-25 tasks; do not exceed 30 during init.
5. Tasks are a work pool, not a timeline. Split by functional scope or architectural responsibility. Express hard dependencies only through existing `blockers` frontmatter.
6. All tasks follow the `.ai-tasks/` task definition from `ai-coding-tasks-v2.md`.
7. At least one initial task should be unblocked and specific enough for a dev session to start.
8. Initial tasks are provisional. Later sessions may update pending task scope, blockers, estimates, or split/add/remove tasks as project understanding improves, following the normal session workflow.
9. Stamp `last-updated: <today>`, `verified-against: <current HEAD SHA>`.

## Final State

```
.ai/
  index.md
  map.md
  overview.md
  architecture.md
  design.md
  modules.md
  apis.md
  features.md
  conventions.md
.ai-tasks/
  index.md
```

## Frontmatter (every `.ai/` doc)

```
---
last-updated: YYYY-MM-DD
verified-against: <git-sha>
---
```

## Document Templates

### index.md

```markdown
---
last-updated: YYYY-MM-DD
verified-against: <git-sha>
---

# AI Knowledge Router

Load only what the current task needs.

## Documents

| Document | File | Use when |
|---|---|---|
| Overview | overview.md | what the project is, users, scope, non-goals, tech stack |
| Architecture | architecture.md | layers, components, data flow, external dependencies, deployment |
| Design | design.md | principles, tradeoffs, patterns, anti-patterns |
| Modules | modules.md | module ownership, responsibilities, dependencies, hooks, boundaries |
| APIs | apis.md | public APIs, internal interfaces, API conventions |
| Features | features.md | capabilities, status, cross-module behavior chains |
| Conventions | conventions.md | code style, naming, error / event / logging, git workflow, testing, dependencies |
| Map | map.md | feature ↔ module ↔ API cross-reference; horizontal sync source for absorption |

## Routing Table

| Task Type | Load Order |
|---|---|
| New feature | map → features → modules → apis |
| API change | map → apis → modules → architecture |
| Bug / debugging | map → modules → code |
| Architecture change | architecture → design → modules |
| Code review | map → relevant docs → code/tests |
| Conventions question | conventions |
| Onboarding | overview → architecture → map |

## Domain Vocabulary

<!-- project-specific terms -->
```

### overview.md

```markdown
---
last-updated: YYYY-MM-DD
verified-against: <git-sha>
---

# Project Overview

## Purpose
## Users
## Scope
## Non-Goals
## Tech Stack
## Repository Structure
```

### architecture.md

```markdown
---
last-updated: YYYY-MM-DD
verified-against: <git-sha>
---

# Architecture

## System Diagram
## Components
## Layers
## Data Flow
## External Dependencies
## Deployment
```

### design.md

```markdown
---
last-updated: YYYY-MM-DD
verified-against: <git-sha>
---

# Design Principles and Decisions

## Core Principles

## Key Tradeoffs

<!--
### Tradeoff: X vs Y
- Chose: X
- Reason: ...
- Consequence: ...
-->

## Patterns in Use

## Anti-Patterns to Avoid
```

### modules.md

```markdown
---
last-updated: YYYY-MM-DD
verified-against: <git-sha>
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
```

### Directory form (when a doc upgrades)

Same upgrade pattern applies to any content doc (modules, apis, features, etc.) that outgrows single-file form.

#### `<topic>/index.md`

```markdown
---
last-updated: YYYY-MM-DD
verified-against: <git-sha>
---

# <Topic>

## Index

| Sub-item | Location | Description |
|---|---|---|
| itemA | inline | ... |
| itemB | `<name>.md` | ... (split out) |

## Details

### itemA
[inline content for non-split sub-items]

(itemB: see `<name>.md`)
```

#### `<topic>/<name>.md` (per split sub-item)

```markdown
---
last-updated: YYYY-MM-DD
verified-against: <git-sha>
---

# <Sub-item name>

[deep-dive content]
```

### apis.md

```markdown
---
last-updated: YYYY-MM-DD
verified-against: <git-sha>
---

# APIs and Interfaces

## Public APIs

<!--
### API Name
- Signature / endpoint
- Purpose
- Owner module
-->

## Internal Interfaces

## API Conventions
```

### features.md

```markdown
---
last-updated: YYYY-MM-DD
verified-against: <git-sha>
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
Modules: ...
APIs: ...
-->
```

### conventions.md

```markdown
---
last-updated: YYYY-MM-DD
verified-against: <git-sha>
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
verified-against: <git-sha>
---

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

## Maintenance Rules

Update when:
- a new major feature is added
- a feature depends on a new module
- a new API joins a feature workflow
- responsibilities shift between modules
```

### `.ai-tasks/index.md`

```markdown
---
last-updated: YYYY-MM-DD
---

# Active tasks

(none)
```

## Scaling

Size limits per memory protocol §4. Upgrade to directory form when any of:

- A doc exceeds the size limit.
- One sub-item dominates (~50%+ of content).
- Growth is foreseeable.

**Upgrade**:

1. Rename `x.md` → `x/index.md`.
2. Spawn `x/<topic>.md` for sub-items that warrant their own file (bloated, dominant, or deep-dive). Multiple can spawn together.
3. Keep the rest inline in `x/index.md`.
4. Update `.ai/index.md` to point at `x/index.md`.

Top-level routing never lists individual sub-files.

## After Init

Initial content is provisional. Subsequent updates flow via `/ai-sync-v2` at task completion.
