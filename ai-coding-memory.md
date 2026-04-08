# .ai/ Knowledge Base

.ai/ gives 10 dimensions of the project for helping you/LLM to understand the project.

## 1. Scope

| Document | Role | Scope |
|---|---|---|
| overview | content | Purpose, scope, tech stack, repo layout |
| architecture | content | System diagram, components, data flows, external deps |
| design | content | Principles, tradeoffs, patterns, anti-patterns |
| modules | content | Responsibilities, locations, dependencies, boundaries |
| apis | content | Service interfaces, messaging contracts, API conventions |
| features | content | Capabilities, status, cross-module behavior chains |
| conventions | content | Code style, naming, patterns, implementation templates |
| tasks/ | content | Task-specific historical notes (write-once) |
| index | routing | Doc catalog + task-type → doc load order |
| map | routing | Feature ↔ module ↔ API cross-reference; relation table for maintenance propagation |

**Two tiers**:

- **content** docs hold the knowledge.
- **routing** docs (index, map) are relation tables used to navigate content
  and to propagate maintenance updates. They are derived from content, never
  the source of new facts.

## 2. Constraints

| Constraint | What to do |
|---|---|
| Storage efficiency | Store compressed knowledge, not raw exploration notes. One doc should answer one category of questions without requiring a second doc. |
| Context pressure | Every loaded doc reduces working context. Before loading, ask: do I need this doc, or can I proceed with data/info in current conversions only? |
| Size limits | index.md ≤ 1500 tokens, single doc ≤ 3000 tokens, When file exceeded: compress (tabulate, de-dup) → split within same scope (e.g. modules.md → modules/index.md + modules/trade.md) + update index.md → evict stale entries. |

### Content admission

`.ai/` exists for two reasons:

1. **Efficiency** — avoid re-deriving conclusions that are expensive to reach.
2. **Scope** — enable reasoning over projects whose full raw material does
   not fit in any single context. This is a cumulative effect across
   sessions, not a one-shot compression: each session contributes small
   digests, and over time the accumulated distillation allows later sessions
   to reason across scopes that no single session's raw-source exploration
   could reach. The value compounds — `.ai/` is a persistent knowledge
   ratchet, not a one-time summary.

Every line in a content doc must pass 3 tests before being admitted.
These tests are heuristics for author judgment, not strict gates — use
the tie-breaker when borderline, and apply reasonable discretion.

**Test 1 — Derivation cost.**
Would re-deriving this from source cost substantially more than storing +
reading it? Count: (a) volume of source/tool output needed, (b) number of
inference steps from evidence to conclusion, (c) risk of getting it wrong
or inconsistent across re-derivations, (d) whether derivation is feasible
at all within a single context.
Store when any dimension is high. Storage wins strongest on short
conclusions backed by long reasoning over scattered source, or facts about
absence/gaps that grep cannot find (misleading naming, intentional
omissions, cross-module invariants, dynamic dispatch paths).

**Test 2 — Stability.**
Is the content stable enough that an agent will use it without
re-verifying against source? If the agent must re-verify, storage saved
nothing. Prefer stable topology and invariants; avoid specific file lists,
function signatures, and field names — those rot quickly.

**Test 3 — Leverage.**
Does knowing this change the agent's next action (which file to open,
what to avoid, what invariant to respect)? Lines that only restate names
or give generic definitions are filler.

**Lines failing any test should be compressed or removed.**

**Tie-breaker.** If a line is borderline on any test, default to
"compress to one keyword-heavy line and keep". Do not spend more than
~10 seconds per line debating.

**Prefer to include**: topology, service boundaries, invariants, rejected
designs, non-obvious couplings, historical reasons, domain vocabulary,
misleading naming, intentional omissions, cross-module constraints,
runtime dispatch paths.

## 3. Maintenance

At conversation end, check: did this session's changes make any
.ai/ document inaccurate or incomplete within its defined scope?
If yes, run /ai-sync

Record at module/feature level, not implementation detail.
If a change only makes an existing description "work more correctly"
(bug fix, iteration on internals), do not update — only update when
the description itself needs to change.

Before writing, verify the target document can absorb the new content
within its size limit. If not, compress or split first, then write.

### Consistency

Code is truth. When `.ai/` conflicts with code, update `.ai/`.

**Maintenance propagates from content docs upward, using routing docs
(index, map) as the relation table.**

**Step 1 — Identify affected content docs.**
List the modules/features/APIs the session's code changes touched.
For each, consult map/index to find which content docs describe it.

**Step 2 — Horizontal sync across content docs.**
For each affected content doc, consult map/index for horizontally
related docs (same module, same feature chain, same API). Check each
for inconsistency with the change. Update inline.

If a related doc cannot be verified this session, add a `sync-todo`
entry to its frontmatter (date + note on what changed elsewhere). The
next session loading this doc resolves the TODO if touching the
affected area, otherwise leaves it alone.

**Cap**: max 3 active `sync-todo` per content doc. Exceeding means the
doc is stale overall — flag the user for full audit before further
edits. Routing docs (index, map) do not carry `sync-todo`.

**Step 3 — Upward update to routing docs.**
Update map/index whenever the relation graph itself changes — entries
added, removed, renamed, merged, split, or retargeted. Examples:
a feature drops a module dependency; a module is deleted; a tasks/
entry is archived; an API moves between services; a module's
responsibility shifts in a way that changes which feature it serves.

Do **not** update routing docs for content-only refinement: a
description got sharper, an invariant got clarified, a finding was
added inside an existing module's entry. Those stay in content docs.

**What does NOT require any doc update.**
Bug fixes that make existing descriptions "work more correctly",
internal refactors that don't cross module boundaries, and
implementation-detail changes (individual functions, fields, line-level
details) belong in source, not `.ai/`.

### Staleness

`last-updated` marks the last time the document was known accurate.

When a loaded document's `last-updated` is significantly older than
recent code changes: treat it as reference only and rely on code as
the source of truth during the task.

If the task naturally reveals inconsistency between a loaded document
and current code → update the document.
If no inconsistency is encountered → bump last-updated only (prevents
the doc from becoming a zombie that is perpetually distrusted).
Do not proactively verify documents not touched/affected by the current task.

### Format

`.ai/` is for agents, not humans. Optimize for machine parsing.
`docs/` is for human readers. Do not mix the styles.

- Tables over paragraphs. Keywords over full sentences.
- One line per fact. No redundant narrative.
- If one line suffices, do not write three.
- Fewer tokens per unit of knowledge is better.

Each `.ai/` document begins with a metadata header:

```
---
last-updated: YYYY-MM-DD
---
```

Update `last-updated` on modification or after task-verified accuracy.
Update `index.md` and `map.md` when routing changes.
Use `tasks/` for task-specific historical notes (write-once).
