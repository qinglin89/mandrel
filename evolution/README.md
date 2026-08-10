# Protocol evolution contract

This directory owns the evidence-driven process for changing the canonical
AI-native protocol suite. It is normative for every evolution batch-analysis,
protocol-improvement, canary, and promotion task in this repository.

## Purpose and ownership

The evolution system improves `canonical/` from repeated evidence without
letting an evaluator, scheduler, or one unusual task rewrite policy directly.

| Surface | Owns | Must not own |
|---|---|---|
| Target repositories | Work, task history, project memory | Global protocol policy |
| orch-hub | Completed evaluation artifacts, global report feed, scheduling | Evolution admission, protocol changes |
| `ai-native-development` | Import state, batches, analysis/change tasks, canonical protocol, releases | Target-project implementation decisions |
| Human operator | Evolution start; change and rollout admission | Routine evidence normalization |

Candidate eligibility is artifact-based and independent of how evaluation was
triggered: the source task is archived/completed and the report has a durable,
complete L1+L2 artifact set. Listing or importing reports must never start or
complete an evaluation.

## Core invariants

1. **Batch evidence, not anecdotes.** An ordinary single report cannot justify
   protocol evolution. Batches count unique completed tasks, not evaluator
   reruns or artifact files.
2. **Import the denominator.** Include successful/clean reports as well as
   reports with findings; otherwise recurrence and base rates are unknowable.
3. **Freeze before analysis.** Every analysis uses an immutable manifest of
   report identities and content hashes. Later reports belong to a later batch.
4. **Preserve provenance.** Keep source repo/task/evaluation identity,
   protocol/deployment revision, role models/profiles, evaluator/rubric
   revision, timestamps, artifact hashes, and explicit missing fields.
5. **Compare coherent cohorts.** Protocol, evaluator/rubric, and materially
   different model revisions must be separated or explicitly accounted for.
6. **Analysis is not implementation.** A batch-analysis task classifies and
   disposes findings. It does not edit `canonical/`. Accepted recommendations
   become separate protocol-improvement tasks.
7. **No change is valid.** A completed analysis may conclude that no protocol,
   memory, orchestrator, or evaluator change is justified.
8. **Pin the runner.** The stable protocol revision governing an evolution
   task remains fixed for that task. A candidate revision never governs the
   run that creates it.
9. **Human gates remain.** Humans trigger batch formation, admit proposed
   canonical changes, and approve canary promotion. Automation may prepare
   evidence and pending tasks, not make these policy decisions.
10. **Canary before promotion.** Candidate changes are versioned, exercised on
    a bounded cohort or replay, measured against a baseline, then promoted,
    revised, or reverted.
11. **Protect raw evidence.** Raw imported artifacts are runtime data under
    `.ai-evolution/`. Commit only sanitized reusable cases, immutable manifests,
    hashes, and decision records that are safe for this repository.
12. **One baseline at a time.** Do not run concurrent canonical
    protocol-improvement tasks against conflicting baselines. Read-only batch
    analysis may queue, but admitted implementations are serialized.
13. **Do not optimize one score.** Evaluate quality, convergence/remediation
    rounds, quota consumption, elapsed time, and regressions. Cross-provider raw
    token counts are not directly comparable.

An exceptional fast path for a severe safety or correctness failure requires a
human-recorded justification. It does not silently weaken the normal batch
rule.

## Data layout

```text
evolution/
  README.md                         this contract
  config.toml                       versioned policy defaults
  ledger.jsonl                      append-only sanitized audit
  schemas/                          import, batch, and ledger contracts
  batches/<batch-id>/manifest.json  immutable report membership
  batches/<batch-id>/findings.md    completed analysis disposition
  batches/<batch-id>/proposed-tasks/ change-task drafts awaiting human admission
  cases/                            curated sanitized regression cases
  experiments/                      canary/replay definitions and outcomes

.ai-evolution/                      ignored machine-local runtime state
  state.json                        discovery cursor and pending pool
  lock                              single-writer guard
  imported-artifacts/               raw fetched bundles
```

The discovery cursor, pending pool, and processed-batch ledger are distinct:

- Advancing discovery records that a feed item was inspected.
- A pending report remains eligible until assigned to a frozen batch.
- A batch is processed only after its analysis task completes successfully.

## Normal workflow

```text
completed archived-task L1+L2 evaluations
  -> orch-hub global completed-report feed
  -> human `aii-2 evolution start`
  -> discover, validate, hash, deduplicate, stage
  -> threshold/age admission check
  -> immutable batch manifest
  -> pending batch-analysis task
  -> reviewed dispositions
  -> change-task drafts in batches/<batch-id>/proposed-tasks/
  -> zero or more human-admitted improvement tasks
  -> candidate canonical revision
  -> replay/canary
  -> human promote | revise | revert
  -> later report cohort measures the result
```

`start` may discover too few eligible reports and exit without creating a
batch. The default threshold is in `config.toml`; forced sub-threshold batches
require a human justification and must still meet the configured minimum.

## Triage and disposition

Every finding cluster receives exactly one primary disposition:

| Disposition | Destination |
|---|---|
| `task-local` | Source-project follow-up; no global change |
| `project-memory` | Source project's `.ai/`, not this canonical suite |
| `protocol-candidate` | Separate canonical protocol/workflow/meta task |
| `orchestrator-config` | Orchestrator implementation or launch-policy task |
| `evaluator-candidate` | Evaluation rubric/evidence-system task |
| `no-action` | Evidence retained with rationale |

Analysis records task count, repository/task-type coverage, recurrence,
counterexamples, confidence, affected revisions, expected benefit, regression
risk, and the chosen disposition. Multiple unrelated candidates become
separate tasks.

## Change admission

A disposition that calls for work does not create that work. Between analysis
and implementation sits one human gate (invariant 9), and drafts wait in the
batch that produced them:

1. The analysis session writes each proposed change task as a
   schema-conforming task file under `batches/<batch-id>/proposed-tasks/`. A
   draft is inert there: nothing dispatches it, and it states its own evidence
   and batch.
2. A human admits one by moving it into `.ai-tasks/` and adding its index row.
   That move is the admission decision.

A draft is never written straight into `.ai-tasks/` as `pending`: the active
pool is what turn selection dispatches from, so a draft placed there would be
picked up as admitted work and the gate would be bypassed. This reuses the
existing task and index mechanics rather than adding a proposed-but-not-admitted
task status.

Automation may create the pending batch-analysis task itself — analysis
classifies evidence and is forbidden from editing `canonical/` (invariant 6), so
it decides no policy. Change tasks are the ones that need the gate.

## Evolution task requirements

Every evolution task must:

- Cite this contract and, after batching, one immutable batch ID.
- State its runner protocol revision; change tasks also state the candidate
  baseline.
- Use only reports named by the batch manifest for batch-level claims.
- Keep report content out of the taskfile except bounded summaries and
  references.
- End with explicit evidence disposition and unresolved-risk statements.
- Preserve a clean separation between descriptive `.ai/` snapshots, normative
  evolution policy here, and canonical payload delivered to target repos.

## Promotion evidence

A protocol-improvement task is not promotion proof. Its canary or replay must
record:

- Baseline and candidate revisions.
- Eligible cohort and exclusions.
- Evaluator/rubric revision.
- Expected directional changes.
- Quality and convergence outcomes.
- Subscription/quota and elapsed-time observations when available.
- Regressions, ambiguity, and rollback decision.

Promotion updates canonical source through the ordinary reviewed workflow and
then uses `aii-2 deploy`; deployed target files are never edited directly.
