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
   memory, orchestrator, or evaluator change is justified. That conclusion ends
   the batch by itself and fabricates nothing on the way out: no candidate, no
   experiment, no promotion, no merge, no deployment, and no revision change.
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
    analysis may queue, but admitted implementations are serialized. Invariant
    14 is what that comes to once a batch has a lineage.
13. **Do not optimize one score.** Evaluate quality, convergence/remediation
    rounds, quota consumption, elapsed time, and regressions. Cross-provider raw
    token counts are not directly comparable.
14. **One current batch, one open experiment.** At most one batch is current at
    a time — from the freeze of its manifest until it records a terminal
    outcome — and within that batch at most one experiment is open. Reports keep
    accumulating in the pending pool throughout; they are a later batch's
    evidence, not a reason to cut this one short. Terminal experiments are
    history and never block anything: abandoning or superseding one frees the
    batch for another alternative, and only a promotion ends the batch.
15. **One frozen base per batch, and rounds only add.** The first experiment
    created for a batch freezes the source revision every experiment of that
    batch starts from, so the alternatives are alternatives and none is built on
    another. Within an experiment a revision round is appended, never rewritten:
    each round's candidate revision stays reachable from the next one's, which
    is what keeps prior task selections, candidate trees, and the evidence
    measured against them readable after the experiment has moved on.
16. **Pin the candidate before measuring it.** A round is measured only once it
    is **candidate-ready**: every task admitted into it durably observed
    complete, and its candidate revision pinned from the ref. Replay and every
    terminal decision name that pinned revision, never the tip as it stands —
    an open round's tip moves, so evidence taken against it describes a tree the
    record cannot afterwards identify. While the last round is candidate-ready
    the ref stays where it was pinned; opening a new round is what lets work
    resume. An experiment dropped before it ever became candidate-ready records
    no candidate at all, in the same spirit as invariant 7.

An exceptional fast path for a severe safety or correctness failure requires a
human-recorded justification. It does not silently weaken the normal batch
rule.

## Lifecycle states

Every unit here is either **non-terminal** — still able to change — or
**terminal**, which is a decision that was recorded and is never edited
afterwards. Nothing is terminal by being old, by being unreferenced, or by
having its files moved away; each state below names the artifact that ends it.

| Unit | Non-terminal while | Terminal when |
|---|---|---|
| Batch | its manifest is frozen and no outcome is recorded | `batches/<batch-id>/outcome.json` records `promoted` or `no-change` |
| Experiment | its record carries no decision | `experiment.json` records `abandoned`, `superseded`, or `promoted` |
| Round | it is the experiment's last round and carries no seal | its `seal` pins a candidate revision — the round is **candidate-ready** |

A candidate-ready round is terminal on its own account, not for the experiment
holding it: through replay the experiment has no open round at all, and it stays
non-terminal because nothing has decided it. Opening the next round resumes the
work; a decision is what ends the experiment.

Analysis completion (`analysis-complete.json`) is a stage inside a current
batch, not the end of one: it releases the batch's dispositions to the
admission gate, and the batch stays current through admission, rounds, replay,
and the decision that follows.

## Revisions in play

Five different commits, which an evidence trail must not substitute for one
another — a candidate measured against the wrong one measures nothing.

| Term | What it is | Recorded in |
|---|---|---|
| Base revision | the exact source commit every experiment of one batch starts from, frozen when that batch's first experiment is created | `experiment.json.base_revision` |
| Candidate tip | the current head of the experiment's ref — where an open round's work is accumulating, and nothing to measure while it can still move | `refs/evolution/experiments/<experiment-id>` |
| Round candidate revision | the tip pinned when a round was sealed; immutable, what replay exercises, and what that round's evidence describes | `experiment.json.rounds[].seal.candidate_revision` |
| Promotion revision | the canonical source-line commit carrying a promoted experiment's change; never the candidate tip it came from | `outcome.json.promotion_revision` |
| Deployed (effective) revision | what one target repository actually holds; per target, and it lags promotion until that target is redeployed | target `.ai-deploy-lock.json`; a report's `provenance.effective_revision` |

The last one is why promotion is not the end of the evidence chain: a promoted
revision changes nothing an evaluation can see until targets carry it, so the
cohort that measures a promotion is the one whose reports were produced at that
effective revision.

## Data layout

```text
evolution/
  README.md                         this contract
  config.toml                       versioned policy defaults
  ledger.jsonl                      append-only sanitized audit
  schemas/                          every versioned data contract below
  batches/<batch-id>/manifest.json  immutable report membership
  batches/<batch-id>/findings.md    analysis disposition record
  batches/<batch-id>/analysis-complete.json  reviewed-completion record; ends the analysis stage
  batches/<batch-id>/proposed-tasks/<draft-id>.md  change-task drafts; kept after admission
  batches/<batch-id>/rejected-drafts.json  drafts declined at the admission gate
  batches/<batch-id>/outcome.json   terminal batch outcome; ends the batch
  cases/                            curated sanitized regression cases
  experiments/<experiment-id>/experiment.json  identity, frozen base, ref, rounds, decision

refs/evolution/experiments/<experiment-id>  durable candidate ref, fast-forward only

.ai-evolution/                      ignored machine-local runtime state
  state.json                        discovery cursor and pending pool
  lock                              single-writer guard
  imported-artifacts/               raw fetched bundles
```

The ref namespace is part of the layout, not an implementation detail. A
branch is deleted when its work is over, and abandoning an experiment is
exactly when that happens; the ref is what keeps every round's candidate tree
reachable afterwards, so the revisions the record pins stay resolvable rather
than becoming names of objects nobody can produce.

Discovery, eligibility, the analysis stage, and the batch itself end at
different moments, and each has its own record:

- Advancing discovery records that a feed item was inspected. Whether that
  discovery reached the end of the feed is recorded with it: a pool left as a
  prefix by a page bound is not a denominator (invariants 1 and 2).
- A pending report remains eligible until assigned to a frozen batch.
- A batch's analysis is finished only after its analysis task completes
  successfully. `findings.md` is the disposition record and is written while
  that task is still being developed and reviewed, so it does not by itself end
  the stage. Completion is read from the analysis task's own lifecycle status,
  and the controller then records `analysis-complete.json` in the batch
  directory. That record is committed because `.ai-tasks/` is machine-local:
  without it, no other clone can tell a finished analysis from a draft.
- A batch is finished only when its outcome is recorded. Everything between the
  two — the admission gate, the experiments, their rounds — happens inside a
  batch that is still current (invariant 14).

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
  -> human admits a group of drafts -> one experiment on the batch's frozen base
  -> admitted improvement tasks -> round work on the experiment ref
  -> every admitted task observed complete -> seal: the round is candidate-ready
  -> replay/canary of that pinned candidate against the base
  -> human promote | revise (next round) | abandon | supersede (next experiment)
  -> batch outcome: promoted, or no-change
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
   schema-conforming task file at
   `batches/<batch-id>/proposed-tasks/<draft-id>.md`. The draft id is a
   kebab-case slug and is the proposal's identity. A draft is inert there:
   nothing dispatches it, and it states its own evidence and batch.
2. A human admits a group of drafts by starting an experiment from them
   (below). Each admitted draft is copied into `.ai-tasks/` with its index row;
   the experiment records the draft id, the hash of the bytes admitted, and the
   task id the copy took.

A draft is never written straight into `.ai-tasks/` as `pending`: the active
pool is what turn selection dispatches from, so a draft placed there would be
picked up as admitted work and the gate would be bypassed. This reuses the
existing task and index mechanics rather than adding a proposed-but-not-admitted
task status.

Admission copies rather than moves, and that is what makes it readable later:
`.ai-tasks/` is machine-local and close-out archives a finished task away, so a
draft that had moved out of the batch would leave nothing behind saying what was
proposed or which experiment took it.

A human may also decline a draft instead of admitting it, which is recorded in
`batches/<batch-id>/rejected-drafts.json` with the reason and the bytes
declined. That record exists because the gate's remaining work is *derived*:
a draft is still waiting when no experiment has taken it and nothing has turned
it down. Without it a declined proposal waits at the gate forever, and deleting
the file instead would leave "why is this gone" a question only `git log`
answers.

Admitting and declining are both terminal for a proposal: a spent draft id is
never reused. Proposing the same idea again means a new draft under a new id
(`<slug>-v2`), whose own bytes and hash state what the second proposal actually
was — so "we tried this twice" stays visible instead of collapsing into one
file that changed.

Automation may create the pending batch-analysis task itself — analysis
classifies evidence and is forbidden from editing `canonical/` (invariant 6), so
it decides no policy. Change tasks are the ones that need the gate.

## Change lineage

Analysis says what should change; an **experiment** is one attempt at it. A
batch usually needs more than one attempt: an approach gets abandoned, another
replaces it, a replay says the candidate did not work and it is revised. All
three have to remain readable afterwards, which is why the lineage is durable
artifacts and refs rather than the state of somebody's checkout.

### Experiments

One experiment is one attempt at the change a batch's analysis called for,
recorded at `experiments/<experiment-id>/experiment.json`
(`schemas/experiment.schema.json`). Its id is `<batch-id>-exp-<NN>`, allocated
one past the highest ordinal that batch has ever used — never reused, so a
historical experiment's name keeps naming the attempt it was. `<NN>` is a
positive ordinal zero-padded to two digits and no wider than it needs to be, so
one attempt has one spelling: `exp-00` names none, and `exp-01` and `exp-001`
would be two directories for one position that an allocation counting from the
highest hands out twice.

A batch's experiments are therefore one series, and reading it back says so:
ordinals run 1..N with none missing — a gap is an attempt whose record was
removed, taking its base, its task selections and its candidates with it — and
only the newest of them is open, because the next one is created only once the
one before it ended.

Creating one *is* the grouped admission of step 2 above. The human selects the
drafts that belong together, and one operation creates the experiment's ref at
the batch's base revision, the record, and the admitted tasks.

The **first** experiment created for a batch freezes that batch's base revision
(invariant 15) — not the batch freeze, which happens before anyone knows a
change is warranted and would pin a base to evidence rather than to work. Every
later experiment for the same batch starts from that exact commit; otherwise
the alternatives are not alternatives but attempts against different protocols.
A different base is refused rather than reconciled: the batch has one base, and
which one it is was settled by its first experiment.

Work happens on the durable ref `refs/evolution/experiments/<experiment-id>`,
checked out under whatever local branch name suits the operator. The ref only
fast-forwards. That is invariant 15's "rounds only add" stated as a Git rule: a
rewritten round leaves the revisions its own record pins unreachable, and the
replay evidence measured against them describing a tree nobody can produce.

### Rounds

A round is one revision pass within an experiment: the task set admitted into
it, and the candidate it produced. Round 1 is created with the experiment. A
round is **open** while its tasks are being worked, and **candidate-ready** once
it is sealed. Three operations move it, and all three only append:

- `add-tasks` admits further drafts into the round that is open.
- `seal-round` makes the open round candidate-ready: it records, per admitted
  task, that the task was observed at `completed`, and pins the ref tip as that
  round's candidate revision. It refuses while any admitted task is unfinished,
  because a candidate that does not contain the change it was admitted for is
  not the thing anyone means to measure.
- `revise` opens the next round, from a round that is already candidate-ready,
  with the reason for it.

Sealing before measuring is invariant 16, and it is what makes a round a thing
evidence can name. While a round is open the ref keeps fast-forwarding under
whoever is working on it, so a replay started then measures one tree while a
later pin names another — evidence about a tree the record cannot identify.
While the last round is candidate-ready the ref stays at the pinned revision;
work resumes by opening a new round, never by adding a commit under one that
has already been measured. An operation finding the ref ahead of a
candidate-ready round stops rather than guessing which of the two the evidence
meant.

Sealing ends the round, not the experiment. What ends the experiment is a
decision, and the three decisions differ in what they need a round to be.

A round is the unit replay evidence names. So a revision makes the previous
round's evidence stale by construction rather than by anyone remembering to
invalidate it: the new round has no evidence until its own is recorded, and the
old evidence goes on naming the round it actually measured — a round whose
candidate revision was pinned before that evidence existed and has not moved
since.

### Terminal decisions

An experiment ends with exactly one decision, and the decision is what turns it
into history:

| Decision | Means | The batch afterwards |
|---|---|---|
| `abandoned` | the attempt is dropped, with a reason | still current; another experiment may start |
| `superseded` | replaced by a different approach, which the decision names | still current; the named successor is that experiment |
| `promoted` | the candidate reached the canonical source line | ended by the batch outcome that records it |

`promoted` is available only from a candidate-ready round, and what it promotes
is that round's pinned candidate revision — never the tip as it stood when the
decision was made. Promoting from an open round would put the same unpinned tree
on the source line that invariant 16 refuses to measure. `abandoned` and
`superseded` are available from an open round too, and leave it unsealed: an
attempt dropped before it produced anything records no candidate, rather than
having one invented to stand for it.

Superseding is one operation, not two: it ends the experiment and creates the
successor it names, because only one experiment may be open (invariant 14) and
a decision cannot name a successor that does not exist yet. The successor is
therefore the next id in the series, allocated one past the attempt it replaces;
a decision naming anything else — an earlier attempt, its own id, an id nothing
was created under — describes a replacement no operation here could have made.
It starts from the batch's base like every other experiment — from the base,
never from the tip it replaces, or the alternative would inherit exactly what was
being replaced.

Abandoning or superseding discards nothing that was learned: the record keeps
the base, every round, every task selection, and every candidate revision, and
the ref keeps those trees reachable. A batch carrying three abandoned
experiments and one open alternative is an ordinary state, not damage.

### Batch outcome

A batch is current until `batches/<batch-id>/outcome.json` exists
(`schemas/batch-outcome.schema.json`), and that record is what releases the next
cohort. Two ways reach it:

- `promoted` — an experiment was promoted; the outcome names it and the
  promotion revision.
- `no-change` — the evidence justified no change to anything (invariant 7). The
  record carries the reason and nothing else: no experiment, no promotion
  revision, and no commit invented to represent a change that was not made.

The outcome and the experiments state one history between them, and it is the
whole set that has to agree rather than the record the outcome happens to name.
A `no-change` batch holds no experiment recording a promotion — that pair says
both that the source line moved and that it did not, and it is the contradiction
with nothing to name, so checking the named experiment alone never sees it. A
`promoted` batch holds exactly one, it is the one the outcome names, and the two
name the same promotion revision.

### What is derived

None of this stores a lifecycle phase, and none of it may be inferred from the
checkout. The current batch, the open experiment, its last round and whether
that round is candidate-ready, the candidate revision, and the drafts still
waiting at the gate are re-derived on every read from the committed manifests,
closure records, experiment records, rejection records, the experiment refs, and
Git — so any clone, on any branch, and a machine that has lost `.ai-tasks/`, all
derive the same answer.

Two readings are specifically not that answer:

- **`HEAD` measured against the release tag.** It answers "is this checkout on
  the release line", which is a different question: it names no experiment,
  changes with a `git checkout`, and reports a candidate for any unrelated
  branch.
- **Scanning task text for a batch citation.** `.ai-tasks/` is machine-local
  and close-out archives tasks away, so the scan finds nothing on a fresh clone
  and less as time passes. The experiment record names its own tasks, so the
  lineage does not depend on those files still being there.

### Guarded operations

Each of the operations above — grouped admission, draft rejection, `add-tasks`,
`seal-round`, `revise`, abandon, supersede, and `conclude-no-change` — writes
several places at once: the experiment ref, a versioned record, `.ai-tasks/` and
its index, the audit ledger. They take the same single-writer lock as import and
freeze, they write in an order where the durable record is what makes the
operation real, and every step is safe to redo, so an interrupted operation is
finished by the next run rather than repaired by hand. Any state they cannot
account for — a second current batch, a second open experiment, an experiment
left open under a later one, a gap or a second spelling in the batch's ordinals,
an experiment whose base is not the batch's, a candidate revision that does not
descend from the one pinned before it, a ref that is not where its record says or
that has moved past a candidate-ready round, a round sealed with no candidate or
carrying a candidate nobody sealed, a task admitted into a sealed round with no
completion observation, a draft already consumed, a task id admitted twice, a
`superseded` decision naming anything but the successor it created, an outcome
that disagrees with the experiments about a promotion, an unreadable record —
stops the operation with what it found, instead of picking one reading and
continuing.

## Evolution task requirements

Every evolution task must:

- Cite this contract and, after batching, one immutable batch ID.
- State its runner protocol revision; change tasks also state the batch's base
  revision, and the experiment and draft id they were admitted from.
- Use only reports named by the batch manifest for batch-level claims.
- Keep report content out of the taskfile except bounded summaries and
  references.
- End with explicit evidence disposition and unresolved-risk statements.
- Preserve a clean separation between descriptive `.ai/` snapshots, normative
  evolution policy here, and canonical payload delivered to target repos.

## Promotion evidence

A protocol-improvement task is not promotion proof. Its canary or replay must
record:

- The batch's base revision, and the experiment and round whose candidate
  revision was exercised. That revision is the one the round's seal pinned
  before the run started (invariant 16); evidence that names an experiment but
  not a round says nothing after the next `revise`.
- Eligible cohort and exclusions.
- Evaluator/rubric revision.
- Expected directional changes.
- Quality and convergence outcomes.
- Subscription/quota and elapsed-time observations when available.
- Regressions, ambiguity, and rollback decision.

Promotion updates canonical source through the ordinary reviewed workflow and
then uses `aii-2 deploy`; deployed target files are never edited directly. The
promotion revision it produces is a commit on the source line, distinct from the
candidate tip that was measured, and distinct again from the effective revision
each target reaches only when it is redeployed.
