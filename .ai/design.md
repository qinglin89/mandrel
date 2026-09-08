---
last-updated: 2026-09-08
verified-against: 182cb36f727a1490610822b675b57512430f009f
---

# Design Principles and Decisions

## Core Principles

- `canonical/` is the only deployable source of truth; target edits are drift.
- Deploy-owned payload and target-owned `.ai/`/`.ai-tasks/` never share write
  ownership.
- Initialization mode detection/scanning excludes every top-level target entry
  written by `deploy.PAYLOADS` plus the manifest and lock receipts; payload-map
  changes must update the init contract/skill and their derivation test.
- Contracts describe role behavior; callers/workflows own dispatch and
  sequencing.
- The eager channel carries shared conduct/meta/project invariants, never a
  role contract.
- Machine-local inventory, credentials, raw evidence, and runtime state stay
  outside Git; portable hashes/receipts and sanitized decisions may enter Git.
- Deployment readiness is target integrity plus equality between the lock
  revision's deployable canonical identity and the configured base or latest
  sealed candidate; whole-commit equality, stored digests, the maintainer's
  checkout, and uncommitted canonical bytes are not readiness inputs.
- Prefer one existing semantic carrier over layered duplicate mechanisms.
- Evolution non-completions are classified by type attributes, never prose:
  `kind` names the recovery/exit family and unique `code` names the class.
  Refusal codes split where the next command or answering machine differs;
  persisted readers translate shared parser answers, while writers retain the
  parser's refusal/validation direction.
- Every evolution verb's `--json` non-completion channel is the flat schema-v1
  stderr envelope (`schema_version`, `verb`, `kind`, `code`, `message`). Exit 2
  has no family/envelope; completed mutations keep human output until the
  separate success protocol lands.
- Deterministic verification is single-sourced in `scripts/check.sh`; CI and
  optional Git hooks invoke it without restating individual checks.
- Protocol evolution uses repeated unique-task evidence, immutable batches,
  separate analysis/change tasks, human gates, and one configured source line.
  A batch proposal records that line's tip; `freeze` refuses a moved line and
  publishes the same commit as the manifest-owned base/runner. The first
  experiment consumes that base, while evidence—not configuration—supplies
  promotion and rollback lines.
- Automatic batch membership asks the one report pack held by the pending task:
  its deployable-canonical identity must equal the proposal base's, and freeze
  records that same pack. Superseded packs stay accounted for outside the pool
  but enter no manifest. Human inclusion/exclusion is the only override and
  remains attributed in the proposal, immutable manifest denominator, and
  generated analysis task. Evaluator/rubric/model facets are recorded per
  report, never membership gates; generated analysis states the required
  evaluator values, calls uniform non-statement absent rather than mixed, and
  qualifies claims that cross a stated split.
- The shipped evolution source line is the exclusive receiving branch
  `refs/heads/dev`; `refs/heads/main` is human-maintained and canonical work
  stays on feature branches. Rollback's current-tip three-way inverse depends
  on that exclusivity to restore measured release state.
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
  refuses when later candidate lineage stands on the promotion. The landed
  inverse also ends the zero-attempt current batch whose base carries that
  promotion but not its inverse; its claimed reports remain spent.
- A release assessment belongs to the first cohort after a promotion. Its
  manifest-derived denominator/exclusions stay visible; facet failures separate
  `unstated` evidence gaps from `differing` side values, and either remains
  inconclusive rather than negative evidence. Because manifests carry no
  task-shape provenance, directional claims rest on a completed pinned
  counterfactual whose goal metrics agree. The reading recommends retain or
  rollback, but the human settlement remains the distinct answer.
- Release-assessment sides use the deployment layer's canonical identity, not
  ancestry. `before` wins equal identities; an unreadable `before` fails closed
  before any `after` match, while a readable `before` remains usable when only
  `after` is unreadable.
- Frozen manifest bytes settle absent and malformed effective-revision
  exclusions identically in every clone; durable assessment validation requires
  exact agreement for those reasons. Only Git-dependent placement outcomes may
  treat an unresolvable revision from another reader as portable uncertainty.
- The assessment gate answers once with retain or rollback. Retain releases the
  frozen base for first admission; rollback ends the batch standing on the
  reversed release. Its obligation survives an owning cohort's no-change
  conclusion; identical settlement retries remain idempotent, while evidence
  mutation closes after the answer.

## Key Tradeoffs

### Static Claude imports versus dynamic memory entrypoints

- Chose target-aware deploy rendering for Claude and dynamic hooks for
  Cursor/Codex.
- Consequence: status separately detects stale/ambiguous entrypoints because
  normalized content hashes intentionally treat legal file forms as equivalent.

### Local manifest versus portable lock

- Chose two receipts: manifest contains rendered hashes, deployed modes, and
  machine paths for target-integrity status; lock contains canonical hashes and
  the source commit used to derive canonical identity for source-line placement.
- The lock's source commit is nullable and stated only when the exact canonical
  paths, bytes, and executable modes captured during deployment match that
  commit's deployable tree; readers derive canonical identity from that commit,
  never from the lock's stored payload digest. Target-file aliases are refused
  before writes.
- Publish the lock before the manifest. If receipt publication is interrupted,
  the surviving old manifest is visibly stale against the new payload; the
  reverse order can leave matching target bytes beside an old lock and silently
  place them at the wrong revision.

### Deployment status JSON versus evolution schemas

- Chose one schema-versioned envelope for single-target and registry-sweep
  status; the shared source-line reading sits above `targets`, and every drift
  carries an origin while placement drifts carry structured reasons/revisions.
  Each target also states its placed base/candidate line from that same
  comparison; null is total and accompanied by a finding, so `in_sync: true`
  implies a named line.
- The payload version is independent of evolution lifecycle schema versions:
  this is one read-only deployment observation, while evolution schemas version
  persisted lifecycle records.
- The payload version promises its exact key set; adding, removing, or
  repurposing a key bumps it, additive changes included.
- Consequence: every status kind needs a `DRIFT_ORIGINS` label; every new
  unplaceable cause needs a documented `UNPLACEABLE_REASONS` value.

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
- A persisted reader owns translation of shared parser validation/refusal
  answers to record corruption; ignored pool state and the staged bundles its
  pointers/hashes name are one runtime-state corruption carrier.
- Batch proposal decisions are held in `(repo_id, task_id)` order independent
  of report arrival, so a no-op poll preserves proposal bytes and stale-state
  tokens; human moves carry forward while their automatic answer still differs.
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
