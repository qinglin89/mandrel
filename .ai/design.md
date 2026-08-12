---
last-updated: 2026-08-12
verified-against: 19a786f4595f18d5901556ed32dfea5e9da6d0ba
---

# Design Principles and Decisions

## Core Principles

- `canonical/` is the only deployable source of truth; target edits are drift.
- Deploy-owned payload and target-owned `.ai/`/`.ai-tasks/` never share write
  ownership.
- Contracts describe role behavior; callers/workflows own dispatch and
  sequencing.
- The eager channel carries shared conduct/meta/project invariants, never a
  role contract.
- Machine-local inventory, credentials, raw evidence, and runtime state stay
  outside Git; portable hashes/receipts and sanitized decisions may enter Git.
- Prefer one existing semantic carrier over layered duplicate mechanisms.
- Deterministic verification is single-sourced in `scripts/check.sh`; CI and
  optional Git hooks invoke it without restating individual checks.
- Protocol evolution uses repeated unique-task evidence, immutable cohorts,
  separate analysis/change tasks, stable runner revisions, and human gates.
- Evolution lifecycle status is derived from durable artifacts and Git; no
  mutable flow-state field governs the phase.
- Evolution reads and writes share one whole-lineage derivation; batch currency
  is never inferred from one artifact's presence in isolation.
- Replay evidence binds one experiment round to its sealed candidate, exact
  source-ref revision, and computed integration tree; only completed, current,
  fully verified evidence is promotable.
- Promotion records its exact prepared merge and intent before moving the source
  ref; promotion and deployment remain distinct facts.
- Rollback preserves the promoted outcome and Git ancestry: it records and lands
  a three-way inverse commit against the current line, never resets history, and
  refuses when later candidate lineage stands on the promotion.
- A release assessment belongs to the first cohort after a promotion. Its
  manifest-derived denominator/exclusions stay visible; mixed or missing
  provenance is inconclusive, never negative evidence. Because manifests carry
  no task-shape provenance, directional claims rest on a completed pinned
  counterfactual whose goal metrics agree.
- The assessment gate answers once with retain or rollback and chooses the line
  every first experiment base must contain. Its obligation survives an owning
  cohort's no-change conclusion; identical settlement retries remain idempotent,
  while evidence mutation closes after the answer.

## Key Tradeoffs

### Static Claude imports versus dynamic memory entrypoints

- Chose target-aware deploy rendering for Claude and dynamic hooks for
  Cursor/Codex.
- Consequence: status separately detects stale/ambiguous entrypoints because
  normalized content hashes intentionally treat legal file forms as equivalent.

### Local manifest versus portable lock

- Chose two receipts: manifest contains rendered hashes/machine paths for
  status; lock contains canonical hashes/source commit for version control.

### Repository-local skills versus personal-level compatibility

- Chose canonical per-target deployment under `.claude/skills/`; the manifest
  and lock version skills with the rest of each target's protocol revision.
- Consequence: `status` separately detects same-named personal skills because
  native precedence can shadow a hash-correct project copy; operators redeploy
  targets before removing legacy personal copies.

### Evolution contract versus `.ai/` snapshot

- Chose `evolution/README.md` as normative workflow and `.ai/` as concise
  current-state routing/architecture.
- Consequence: evolution task generation must explicitly load/cite the contract
  rather than relying on duplicated snapshot prose.

### Artifact eligibility versus evaluation trigger provenance

- Chose artifact eligibility. Evolution accepts an archived completed task only
  when its L1+L2 artifact set is durable; how that evaluation was triggered is
  outside the evolution contract. Discovery/import itself remains read-only.

## Patterns in Use

- Dataclass value objects at deployment/status boundaries.
- Dependency injection for filesystem/subprocess-heavy tests.
- Atomic or idempotent writes for receipts, registries, evolution state,
  immutable batch publication, and generated task/index recovery.
- Multi-record/ref mutations are ordered so interruption leaves a named,
  resumable state; the durable domain record, not the audit line, makes the
  operation real.
- A record claiming a ref still carries an observed revision is written under
  Git's lock on that revision; operation-owned commits are identified exactly
  and recovered by ancestry when later compatible ref advances preserve the
  fact being recorded.
- Evolution writers publish through the reader's parser so persisted
  cross-field rules are enforced in both directions.
- Cross-operation release settlement holds one single-writer lock across
  preflight, optional inverse rollback, lineage re-derivation, and decision
  publication; an answered earlier-cohort obligation is followed only for the
  settlement redo path.
- Config/schema version fields on persisted machine contracts.
- Fail closed on malformed state, unsafe paths, unsupported config, or missing
  provenance required for a decision.
- Content equality over the owning structure, not marker/prose/prefix matching,
  proves ownership before existing files are adopted or repaired.

## Anti-Patterns to Avoid

- Editing deployed target payloads instead of canonical source.
- Committing secrets, raw evaluation bundles, local manifests, registry paths,
  or orchestrator `.env`.
- Treating one evaluation finding as global policy evidence.
- Combining heterogeneous protocol/rubric cohorts without stating it.
- Letting an analysis task edit canonical files or a candidate govern its own
  creating run.
- Comparing Claude and Codex raw token counts as equivalent cost.
