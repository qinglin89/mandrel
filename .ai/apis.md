---
last-updated: 2026-08-15
verified-against: 712b34f89c6436a001dfff9b73d801715c4e51b0
---

# APIs and Interfaces

## `aii-2` CLI

| Command | Behavior |
|---|---|
| `deploy [--dry-run] [--bootstrap-orchestrator] <target>` | Preview or deploy canonical payload; optional venv bootstrap |
| `status <target>` | Compare manifest-recorded content/modes with the target and current canonical source, plus eager entrypoints and personal skill shadows; files dropped from canonical are not detected as target orphans yet |
| `status --all` | Check every local registry entry |
| `registry list [--json]` | Read machine-local managed repos |
| `registry add <target>` | Register a target with a readable manifest |
| `registry remove <name-or-path>` | Remove local tracking only |
| `evolution list [--feed-dir <path>]` | Inspect feed candidates without changing cursor, pool, ledger, artifacts, batches, or tasks |
| `evolution sync [--feed-dir <path>]` | Import, validate, hash, deduplicate, and audit eligible complete reports |
| `evolution status [--json]` | Derive schema-v7 pool/batch/experiment/replay/release/deployment state, stable object IDs, `state_revision`, and every verb's allowed/refused/recovery result |
| `evolution start [--force --justification <text>]` | Reconcile, sync, and freeze one batch when admission policy allows; force never waives the minimum |
| `evolution create|add-tasks|reject|seal-round|revise|abandon|supersede|conclude-no-change` | Drive the guarded, recoverable experiment and batch lineage |
| `evolution replay-start|replay-conclude|replay-abandon|replay-withdraw|promote|rollback` | Record/resume exact-integration evidence, promote its exact tree, or reverse the latest promotion |
| `evolution assess|assess-measure|assess-conclude|assess-abandon|assess-withdraw|assess-resolve|settle` | Record the next cohort's release reading/counterfactual and settle retain or composed rollback |

All evolution operations are human-invoked and make no evaluation model call.
`list` and `status` are read-only; `start` returning no batch is a normal
successful outcome when policy is not met. Lifecycle mutations accept optional
`--expect <state_revision>` and check it first under their writer lock.

## Evolution Domain Operations

| Operation | Behavior |
|---|---|
| `experiments.create` / `add_tasks` / `reject` / `seal_round` / `revise` / `abandon` / `supersede` / `conclude_no_change` | Recoverable grouped admission, rounds, terminal experiment decisions, and batch no-change conclusion |
| `assessment.describe` / `obligation` / `read` / `form` | Derive the first post-promotion cohort's provenance frame and owner, validate its durable reading, and record caller-owned measurements/judgement through the reader |
| `assessment.measure` / `conclude` / `abandon` / `withdraw` / `resolve` | Allocate an experiment-disjoint replay position, persist/recover the pinned before/after counterfactual, and hold a directional verdict to its goal metrics |
| `assessment.settle` | Idempotently retain the release or compose its audited rollback under one writer lock; the decision gates and constrains the next first experiment base |
| `replay.start` / `conclude` / `abandon` / `withdraw` | Persist/resume an idempotent exact-integration replay request, then record or retire its durable run state |
| `experiments.promote` | Prepare and record the exact replayed merge, compare-and-swap the source ref, and publish agreeing experiment/batch outcomes without implying deployment |
| `rollback.rollback` | Add and record a three-way inverse commit for the latest effective promotion when no later candidate lineage depends on it |
| `harness.StatedHarness` | Operator-stated implementation of replay/counterfactual start and poll; enforces completed-attempt reproduction while permitting a new handle |
| `deployment.describe` | Read each planned target through the machine-local registry and its validated deploy lock; return placement/absence/error states without gating lifecycle status |
| `lockfile.stated_source_commit` | Accept only supported deploy-lock schemas with a present, null-or-full-object-id `source_git_commit`; distinguish explicit null from a malformed absent field |

## Persisted Interfaces

| Interface | Ownership / semantics |
|---|---|
| `.ai-deploy-manifest.json` | Ignored target-local receipt of rendered hashes and deployed modes used by status |
| `.ai-deploy-lock.json` | Portable canonical hash/source revision receipt; readers validate schema and require `source_git_commit` to be present as null or a full object id |
| `.registry/repos.local.json` | Ignored machine-local repo inventory |
| `orchestrator.toml` | Deployed defaults and named per-backend profiles |
| `orchestrator.py --print-config` | Machine-readable effective launch snapshot |
| `evolution/config.toml` | Versioned evolution admission/storage policy |
| `evolution/schemas/*.json` | Versioned import, batch, closure, experiment, replay, release-assessment, outcome, rollback, rejection, and ledger contracts |
| `.ai-evolution/state.json` | Ignored schema-v2 cursor, feed-exhaustion proof, pending/rejected/processed state |
| `.ai-evolution/imported-artifacts/` | Ignored normalized report records and raw L1/L2 artifact bodies |
| `evolution/batches/<id>/manifest.json` | Immutable committed cohort membership and evaluator/protocol provenance |
| `evolution/batches/<id>/analysis-complete.json` | Portable reviewed-analysis closure record |
| `evolution/batches/<id>/proposed-tasks/` | Inert change-task drafts retained after human admission copies them into `.ai-tasks/` |
| `evolution/batches/<id>/rejected-drafts.json` | Durable terminal decisions for declined draft identities and bytes |
| `evolution/batches/<id>/release-assessment.json` | First post-promotion cohort's derived frame, visible exclusions/denominators, counterfactual request/run/withdrawals, verdict, and retain/rollback decision |
| `evolution/experiments/<id>/experiment.json` | Versioned experiment identity/rounds/decision record; v2 explicitly carries nullable prepared-promotion state while frozen v1 remains readable |
| `evolution/experiments/<id>/replays.json` | Versioned per-experiment replay history: allocated withdrawals, optional pending request, and durable running/failed/completed attempts |
| `refs/evolution/experiments/<id>` | Durable fast-forward candidate ref; independent of the checked-out branch |
| `evolution/batches/<id>/outcome.json` | Terminal promoted/no-change batch outcome; a promotion carries the exact replayed merge unit and planned target names, never deployment state |
| `evolution/batches/<id>/rollback.json` | Prepared/completed inverse commit for the batch's promotion; leaves the outcome and experiment history unchanged |
| `evolution/ledger.jsonl` | Versioned sanitized append-only evolution audit |

## External Integration

The published orch-hub feed supplies globally ordered archived-task reports.
Its integer `after` watermark is stringified at the `ReportFeed` boundary;
`has_more` is the feed-owned exhaustion signal. The client translates catalog
entries into import-schema-v1 records and fetches byte-exact artifacts by fixed
wire filename; 410 means published bytes were pruned, while 404/409/500 remain
errors. It uses `ORCH_HUB_URL` plus bearer `ORCH_HUB_TOKEN`, follows no
redirects, permits cleartext HTTP only for loopback, and bounds each response at
32 MiB. `scripts/probe-orch-hub.py` is the manual credentialed contract check;
`--feed-dir` remains the deterministic offline implementation of the same
boundary. The client copies orch-hub's nested `provenance.protocol`
`effective_revision` + `deploy_lock_hash` pair verbatim only when `available` is
true and both halves are stated; absent, unavailable, legacy, malformed, or
half-identity sections become two nulls. Imported and frozen records retain that
pair, while release assessment uses only its revision half for Git ancestry
placement. The complete-pair path is live-proven; reports imported before the
publisher began stating it remain unplaceable and are never retrofitted.

## API Conventions

- Validate path containment and schema versions before writes.
- Make dry-run/list/status read-only.
- Keep consumer cursors opaque; do not infer global order from scoped
  evaluation timestamps.
- Persist hashes with imported artifacts and frozen batch membership.
- Fail explicitly on malformed persisted state; do not silently reset cursors
  or drop pending reports.
