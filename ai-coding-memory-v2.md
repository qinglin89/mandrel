# `.ai/` Memory Protocol

Agent-facing memory for `.ai/` snapshot: pay re-derivation cost once at admission time.

## 1. Invariants

- `.ai/` is for agents (compressed, machine-parsable). `docs/` is for humans. Never mix styles.
- Code is truth. When `.ai/` conflicts with code, update `.ai/`.
- Snapshot (`.ai/`) describes current state — timeless.
- Close-out operations (snapshot writes via §3/§4, task archive, removing the task's row from `.ai-tasks/index.md`) are `/ai-sync-v2`'s exclusive domain. LLM session work stops at the End procedure (`ai-coding-v2.md` §10 End steps 1-5); do not pre-absorb, pre-edit `.ai/`, pre-archive the task, or pre-remove the task's row from the active index.
- All `.ai/` content in English.
- Prefer fewer mechanisms over more contracts. When adding a step or data structure, first check whether an existing one already carries the same semantics. Layered mechanisms degrade execution fidelity multiplicatively.

## 2. Loading contract

| Tier | Mode | Files |
|---|---|---|
| Eager | Always in system prompt | `.ai/index.md`, `.ai/map.md`, `.ai-tasks/index.md`, `.ai/overview.md`, `.ai/architecture.md`, `.ai/design.md`, `.ai/conventions.md` |
| Lazy | `Read` on demand, via routing | all other files in `.ai/` |

Eager set carries routing (`index`, `map`, `tasks/index`), project invariants (`overview`, `architecture`, `design`), and writing rules (`conventions`) — every decision and every code write needs them. Lazy set is inventory (`modules`, `apis`, `features`, etc.) — loaded per task via routing.

Sub-indexes from splits (e.g., `.ai/modules/index.md`) are lazy: referenced from the top-level routing.

## 3. Admission (Write)

A fact enters Snapshot when **all three** tests pass:

| Test | Pass when |
|---|---|
| Derivation cost | Re-deriving needs multi-file traversal, cross-module reasoning, or git archeology |
| Stability | Stays true across iterations without re-verification |
| Leverage | Knowing it changes the agent's next action |

Borderline on any → compress to one keyword-heavy line and keep. Don't deliberate — default to keep.

**Prefer**: invariants, topology, anti-patterns, vocabulary, non-obvious couplings, intentional omissions, runtime dispatch paths.
**Avoid**: function signatures, file lists, easily-greppable specifics.

## 4. Maintenance (Write)

**Trigger** an update when description and reality diverge — wrong, incomplete, or misleading. Bug fixes and internal refactors that don't change what the description says are not triggers.

**Propagation** (in order):

1. Route to the target content doc via `.ai/index.md` (its catalog lists which doc covers each topic — same mapping for reads and writes). Edit inline.
2. Check related docs via `.ai/map.md` for horizontal inconsistency. Update inline.
3. Check eager-loaded invariants (`overview`, `architecture`, `design`, `conventions`) for ripple effects. Update inline.
4. If the relation graph changed (entries added / removed / renamed / merged / split / retargeted), update `.ai/index.md` and `.ai/map.md`.

**Size**: single doc ≤ 3000 tokens; `index.md` ≤ 1500 tokens.

**Upgrade to directory form** when any of:

- Doc exceeds the size limit.
- A single sub-item dominates the doc (typically > 50% of content).
- Anticipated growth makes hitting the limit foreseeable.

**Upgrade procedure**:

- Rename `x.md` → `x/index.md`.
- Spawn `x/<topic>.md` for each sub-item that warrants its own file (bloated, dominant, or deep-dive nature). Multiple can spawn at once.
- Keep the rest inline in `x/index.md`.
- Update top-level routing to point at `x/index.md`.

**Layered routing invariant**: top `.ai/index.md` lists only entry points (single file or sub-index), never individual sub-files. Sub-files are referenced from within the sub-index.

## 5. Format

- Tables over prose. Keywords over full sentences. One line per fact.
- Every `.ai/` document begins with:

```
---
last-updated: YYYY-MM-DD
verified-against: <git-sha>
---
```

  `<git-sha>` = full 40-char SHA from `git rev-parse HEAD`. `YYYY-MM-DD` = date the doc was last verified accurate.

**Staleness**: `last-updated` marks last verified accuracy. `verified-against: <sha>` records the HEAD commit when the doc was last touched. Bump both only when the doc is touched OR confirmed-correct during a task. Do not proactively verify untouched docs.
