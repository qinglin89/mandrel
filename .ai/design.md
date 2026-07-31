---
last-updated: 2026-07-31
verified-against: 1cc444eefee5e7d41cd94f7c01b661bf94c75152
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
- Protocol evolution uses repeated unique-task evidence, immutable cohorts,
  separate analysis/change tasks, stable runner revisions, and human gates.

## Key Tradeoffs

### Static Claude imports versus dynamic memory entrypoints

- Chose target-aware deploy rendering for Claude and dynamic hooks for
  Cursor/Codex.
- Consequence: status separately detects stale/ambiguous entrypoints because
  normalized content hashes intentionally treat legal file forms as equivalent.

### Local manifest versus portable lock

- Chose two receipts: manifest contains rendered hashes/machine paths for
  status; lock contains canonical hashes/source commit for version control.

### Evolution contract versus `.ai/` snapshot

- Chose `evolution/README.md` as normative workflow and `.ai/` as concise
  current-state routing/architecture.
- Consequence: evolution task generation must explicitly load/cite the contract
  rather than relying on duplicated snapshot prose.

### Human evaluation selection versus automatic model spending

- Chose human selection. orch-hub only lists already-complete L1+L2 reports;
  evolution discovery/import never triggers evaluation.

## Patterns in Use

- Dataclass value objects at deployment/status boundaries.
- Dependency injection for filesystem/subprocess-heavy tests.
- Atomic or idempotent writes for receipts, registries, and future evolution
  state.
- Config/schema version fields on persisted machine contracts.
- Fail closed on malformed state, unsafe paths, unsupported config, or missing
  provenance required for a decision.

## Anti-Patterns to Avoid

- Editing deployed target payloads instead of canonical source.
- Committing secrets, raw evaluation bundles, local manifests, registry paths,
  or orchestrator `.env`.
- Treating one evaluation finding as global policy evidence.
- Combining heterogeneous protocol/rubric cohorts without stating it.
- Letting an analysis task edit canonical files or a candidate govern its own
  creating run.
- Comparing Claude and Codex raw token counts as equivalent cost.
