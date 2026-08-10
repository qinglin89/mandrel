---
last-updated: 2026-08-11
verified-against: 6af4fd1d487bb0ad1873c6825df5fe5d31d13139
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
- A record naming a Git-ref transition is written under Git's own lock on the
  revision it read; an atomic write of a stale ref observation is still stale.
- Evolution writers publish through the reader's parser so persisted
  cross-field rules are enforced in both directions.
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
