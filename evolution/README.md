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
17. **Judge the release before freezing the next base.** A batch frozen after a
    promotion reads what that release did (Release assessment), and a human
    settles that reading — `retain` or `rolled-back` — before any base is frozen
    on the line that release is on. The two answers give two different commits
    to start from: `retain` leaves the release on the source line,
    `rolled-back` puts the inverse commit there first. So the settlement decides
    twice over: freezing before the answer takes whichever of the two the line
    happened to be holding, and a base that carries neither the release nor its
    reversal is the same accident one step later. The obligation stays with the
    first cohort frozen after the promotion and does not lapse when that cohort
    ends: a batch that concluded `no-change` promoted nothing, so the batch after
    it still follows the same release and still waits on that cohort's answer.
    Nothing else in the batch waits on it — the reading is taken while the
    analysis is still being written, and drafts, rejections and the closure are
    unaffected. A batch with no promotion anywhere before it owes nothing and
    waits for nothing.

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
| Replay run | its record carries no result | its `result` records `completed` or `failed` |

A candidate-ready round is terminal on its own account, not for the experiment
holding it: through replay the experiment has no open round at all, and it stays
non-terminal because nothing has decided it. Opening the next round resumes the
work; a decision is what ends the experiment.

Analysis completion (`analysis-complete.json`) is a stage inside a current
batch, not the end of one: it releases the batch's dispositions to the
admission gate, and the batch stays current through admission, rounds, replay,
and the decision that follows.

## Revisions in play

Seven different commits, which an evidence trail must not substitute for one
another — a candidate measured against the wrong one measures nothing.

| Term | What it is | Recorded in |
|---|---|---|
| Base revision | the exact source commit every experiment of one batch starts from, frozen when that batch's first experiment is created | `experiment.json.base_revision` |
| Candidate tip | the current head of the experiment's ref — where an open round's work is accumulating, and nothing to measure while it can still move | `refs/evolution/experiments/<experiment-id>` |
| Round candidate revision | the tip pinned when a round was sealed; immutable, what replay exercises, and what that round's evidence describes | `experiment.json.rounds[].seal.candidate_revision` |
| Merge input revision | where the source line stood when a replay integrated the candidate onto it; it moves for reasons the experiment knows nothing about, and each time it does that replay stops describing what a promotion would carry | `replays.json` `integration.merge_input_revision` |
| Promotion revision | the canonical source-line commit carrying a promoted experiment's change; never the candidate tip it came from, and recorded with the merge unit that identifies it — the round, the candidate, the merge input, and the tree. Written on the experiment before the line moves, which is what makes an interrupted promotion finishable, and stated again by the outcome that ends the batch. A promotion recorded before the experiment carried a merge unit (`experiment.json` version 1) states the revision alone, and the outcome is the only record of what it went as | `experiment.json.promotion`, `outcome.json.promotion_revision`, `outcome.json.promotion` |
| Rollback revision | the source-line commit that takes a promoted change back out; a commit of its own with the line's tip as its parent, never the promotion it reverses and never a rewrite of it | `outcome.json`'s batch: `rollback.json` `revision` |
| Deployed (effective) revision | what one target repository actually holds; per target, and it lags promotion until that target is redeployed. Stated only where the deploy could tie the payload to that commit and the target still matches its receipt; otherwise the target has none to report | target `.ai-deploy-lock.json` `source_git_commit`, as read from that target at evaluation time; restated on a report as `provenance.effective_revision` |

The middle two are the pair a promotion is decided from, and neither stands in
for the other. The candidate is pinned and cannot move; the merge input is not
this experiment's to pin at all. What identifies the thing actually measured is
therefore neither commit but the tree the two produce, which is why a replay
records that as well (`integration.tree`).

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
  batches/<batch-id>/rollback.json  the inverse commit that took a promoted change back off the source line
  batches/<batch-id>/release-assessment.json  this cohort's reading of the release before it, the pinned run that settles it, and its settlement
  cases/                            curated sanitized regression cases
  experiments/<experiment-id>/experiment.json  identity, frozen base, ref, rounds, prepared promotion, decision
  experiments/<experiment-id>/replays.json     every replay run against that experiment's rounds, the request outstanding, and the positions given up

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
- A rollback comes after the end and does not reopen it. `rollback.json` says
  that the promotion the outcome recorded is no longer what the source line
  carries; the batch stays concluded, the experiment stays promoted, and the
  outcome record is not edited.
- A release assessment is about a *different* batch's promotion, which is why it
  sits in the directory of the cohort that produced the reading rather than the
  one being read (Release assessment). It ends nothing of its own batch's
  lifecycle; what it settles is whether the release before it stands, and the one
  thing that waits on the answer is the next base freeze on that line — this
  cohort's own, or a later cohort's where this one ended without answering
  (invariant 17).

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
  -> next frozen cohort assesses the release: improved | neutral | regressed | inconclusive
  -> human settlement: retain, or roll back -> the next base freeze, on the line it chose
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

A draft is a task file, so the copy takes the task id the draft itself declares,
and the gate checks the whole shape rather than that id alone: a frontmatter
block that is opened, closed, and says each thing once, declaring a date-prefixed
slug — which is also the file name the copy takes — the inert `pending` status,
an estimate no session has consumed, nothing blocking it and nobody claiming it;
and the body a session works from, which is its goal, its scope, its acceptance,
and the session log it will record itself in. The body is read as sections, not
as text: each of those is a section the file declares once, the log is still
empty — an entry under it is some session's record of work on a task nothing has
dispatched, which is the same state the `pending` status and the unconsumed
estimate refuse from the frontmatter side — and there is no `## Admission`
section, because that one is what admission itself adds. Those sections stand at
column 0. A heading-looking line that is indented is refused rather than copied
into the pool: Markdown reads it as code and the session working the copy reads
it as a section, so it would carry a scope, or a second admission block, that
only some of its readers see. The check belongs here because admission is a copy
into the pool turn selection dispatches from: nothing downstream ever reads that
file as a proposal again, so a proposal that is a task file in name only is
refused at the gate or nowhere. An id already in
flight, already archived, or already admitted by this batch stops the admission
instead of overwriting a task file or leaving two admissions answering for one
task.

What the copy adds is what the draft could not know: an `## Admission` section
naming the batch, the experiment and round, the base revision and the release it
builds on, the runner protocol revision, and the ref to work on. Those are the
facts the task requirements below demand of a change task, and without the last
one the session implementing it has nowhere to commit. The hash in the record is
of the draft — what was proposed — and the copy is derived from it.

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
the batch's base revision, the record, and the admitted tasks. For the first
experiment of a batch that base is the commit the work starts from — the
checkout's `HEAD` unless a revision is named explicitly — recorded as its sha,
because a branch or a tag means a different commit tomorrow.

The **first** experiment created for a batch freezes that batch's base revision
(invariant 15) — not the batch freeze, which happens before anyone knows a
change is warranted and would pin a base to evidence rather than to work. Every
later experiment for the same batch starts from that exact commit; otherwise
the alternatives are not alternatives but attempts against different protocols.
A different base is refused rather than reconciled: the batch has one base, and
which one it is was settled by its first experiment.

That freeze is also where the previous release stops being an open question. A
batch frozen after a promotion owes a reading of it, and until a human settles
that reading the first experiment refuses to freeze anything (invariant 17,
Release assessment): `retain` and `rolled-back` leave two different commits on
the source line, so a base taken before the answer is the answer made by
accident. The reading it waits on is the owning cohort's — the first batch
frozen after that promotion — which is not always the batch doing the freezing:
a cohort that ended `no-change` promoted nothing, so the next one follows the
same release while owing no reading of its own.

The base is then held to what that settlement chose. `retain` selects the line
carrying the release and `rolled-back` the line carrying the inverse commit, so
a base that does not have the chosen commit in its history is refused however it
was reached — `HEAD` on a checkout that never followed the source line, an
explicit revision naming the commit the batch was frozen beside, or a line a
standalone rollback moved after the gate said the release stays. Later
experiments of the batch take the frozen commit and ask nothing.

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
  with the reason for it. The round opens with nothing admitted into it, and
  `add-tasks` is what fills it: a revision is decided the moment replay reports,
  the proposals answering it may not be written yet, and while the last round is
  candidate-ready the ref stays where it was pinned — so waiting for those drafts
  before opening the round is waiting with the work blocked.

Both halves of a seal are read where the fact actually is. The completion of an
admitted task is read from the copy that admission published — identified as
that copy the same way an interrupted admission identifies its own work, since a
file merely standing at the task's id says nothing about whether the change was
made — and `.ai-tasks/` is machine-local, so a task this machine does not hold is
observed where it was worked rather than assumed finished. The candidate is read
from the ref, which must be here and must be shown to descend from the revision
pinned before it: the pin is immutable and every later piece of evidence names
it, so a tree Git cannot answer for is not one to write down. A round whose ref
never moved seals at the revision already pinned — a candidate nobody changed,
which is an honest record and not a rewritten history. A round that admitted
nothing is not sealed at all: it would pin a revision pass no proposal accounts
for.

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

### Replay evidence

Invariant 10 puts a canary or replay between a candidate and the source line,
and every run of one is recorded in `experiments/<experiment-id>/replays.json`
(`schemas/experiment-replays.schema.json`), appended in round and then attempt
order. A run states what it exercised — the round it belongs to, the integration
it measured, the case set and the exclusions applied to it, the evaluator that
judged it, the harness and configuration that drove it, and the directional
change it was expected to produce — and then, when it ends, what it measured.

**A run is about one round.** It names the round and exercises exactly the
revision that round's seal pinned, which is invariant 16 read from the evidence
side. That binding is also what stops a report being carried forward: a later
round wanting the same numbers would have to claim a candidate revision its own
seal did not pin. The base is stated with it, so evidence carries its own
baseline rather than being read against whichever one a later reader assumes.

**A candidate is measured as it would be integrated.** What a promotion puts on
the source line is the candidate merged onto that line, so a run records the
merge input it integrated onto and the tree the two produced, and a promotion
reproduces that tree rather than trusting the pair of commits to imply it. The
pinned candidate cannot show the source line moving; the merge input is what
does, which is why a run that was exact yesterday can describe nothing today
without anything about the experiment having changed. The merge input is named
by a fully-qualified ref that Git can hold: `HEAD`, a bare branch name, or a
revision expression answers from whichever working copy is asking, and a run is
not stale in one checkout and promotable in another; a name `git
check-ref-format` refuses resolves nowhere at all, which is a record that can
never be checked rather than one this clone could not check.

**A record is enough to run again.** The integration is the controller's to pin,
but the cohort, the evaluator, and the configuration are the harness's own
selections — so the record states them, and a rerun hands them back as the
selections to reproduce rather than asking for whatever would be chosen today. A
harness that no longer holds that case set, or cannot resolve the configuration
from the hash it issued, refuses; running the nearest thing it has would put a
second measurement under the first one's provenance. Each run is identified by
the handle its harness issued, and one handle names one run: two records sharing
one would be concluded from a single report.

A rerun is a further attempt at one round, and it is what a completed attempt is
handed back for: it replaces that attempt as the round's evidence, and the reason
for it is drift in the integration rather than in the cohort. A *failed* attempt
is not reproduced — a case set the harness could not hold is exactly what may
have failed, and reproducing it would refuse every attempt after the first — and
a round's first run has nothing to reproduce. What the record then states is what
the harness answered with rather than what it was asked for, so a substitution is
visible as a different case-set hash beside the attempt it was meant to
reproduce.

**Five states, three of them written down.** A record says `running`,
`completed`, or `failed`. `incomplete` — no run names the round that needs one —
and `stale` — the run that does no longer describes the tree in question — are
derived, and neither is stored. A run still going is a record rather than a
process: it carries the harness's own opaque handle, so a later run of `status`
on this machine or another sees it, and a run nobody can poll is refused rather
than left to never conclude. A `failed` run records why it stopped and no
numbers at all; a partial sweep reads as a cohort result nobody produced.

Measurements are a set, never a score. Invariant 13 judges a candidate on
quality, convergence, quota, and elapsed time together, so a run records each
quantity on both the base and the candidate and marks which of them were goals —
an observation with no baseline is recorded as one rather than as an improvement
over nothing.

**Starting one, and ending it.** Four operations, guarded like the round
transitions and taking the same lock. Starting pins the integration — the
round's already-sealed candidate, the commit the named source-line ref stands
at, and the tree merging them produces — and then asks the harness to run it; a
candidate that does not merge cleanly onto that ref is refused rather than
measured around, because it is not one a promotion could carry either. The
source line is named by the operator rather than guessed from the checkout: a
repository cannot tell which of its refs a promotion will land on, and the ref
is what a later reader asks about the drift with. Concluding polls the run and
records what it reports; polling a run still going writes nothing and is the
ordinary case.

The order inside them is fixed by what an interruption may leave. A record
cannot state a run until the harness answers — the cohort, the evaluator, the
configuration and the handle are all its to choose, and a run nobody can poll is
refused — so what is written before the harness is asked is not a run but the
request for one: the round, the attempt it will occupy, the integration pinned
for it, and the expectation, which is the whole of what the controller owns. An
answer that never arrives, or a write of it that fails, therefore leaves that run
named rather than loose. Starting again resumes the request instead of making a
second: the same experiment, round and attempt reach the harness again, and a
harness asked twice for one run answers with that run rather than beginning
another — attempts are allocated once and never reused, so no second request
legitimately wears one position. The resume may not re-pin the integration, since
the harness is already measuring the one that was pinned; a source line that has
moved since is what a further attempt is for, and a resume naming a different one
is refused with what is outstanding. A pending request is not evidence and never
becomes any — while one stands, the round it names has been measured by nothing,
and the write that records the run it became clears it in the same file. Every
refusal a start makes is still made before the harness is asked, so a retry — a
second run started while one is going, most of all — costs nothing that was
already running. A concluded run is reported, not concluded twice; the audit line
an interrupted conclusion may have cost is not re-appended.

A run whose harness cannot report is ended by the third operation, which
records why: age concludes nothing, and a harness that died, lost the handle, or
answers with something the record cannot hold would otherwise leave the run
going forever — and with it the round, which is measured against one integration
at a time. What it writes is the `failed` the run was, with the reason in place
of numbers, and the answer to a failed run is another attempt.

The fourth takes a request back, which is the same door one step earlier: a
harness that cannot describe what it is running leaves the start unable to
finish, and the round blocked for the same reason. It records no reason and no
run — a request that never became one measured nothing, is derived as nothing,
and leaves nothing for a reason to be attached to. What it does record is the
position, which it keeps: the harness is keyed on the round and the attempt, and
a withdrawal happens precisely when that harness may be running something nobody
can describe, so reissuing the position would ask it for a run it already
answers for and the first integration's numbers would arrive under a record
naming the second. So the position stays allocated with the integration that was
pinned for it — which is what an operator needs to stop that run at the harness —
and the next request takes the one after it. A round's attempts are therefore
allocated across its runs and its withdrawals together, and a gap in them is
still an allocation whose record is missing. Run again it reports that nothing
was outstanding.

None of the four ends anything above the run. A candidate the numbers argue
against is answered by a further round or by a terminal decision, both of which
are operations of their own.

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
a decision naming anything else — an earlier attempt, its own id — describes a
replacement no operation here could have made. It starts from the batch's base
like every other experiment — from the base, never from the tip it replaces, or
the alternative would inherit exactly what was being replaced. Its round 1 opens
with nothing admitted into it and `add-tasks` fills it, for the reason a revised
round opens empty: which proposals answer the new approach is the next question,
and an attempt that could not exist until they are written could not be started
when it was decided.

Two records, one operation, and the order between them is decided by what an
interruption leaves. The decision is written first, so a run that stops in
between leaves a supersession owing its successor — one state, readable, and
finished by the same supersession redone for the same reason. Writing the
successor first would leave two open experiments instead, which no reading can
arbitrate and invariant 14 refuses outright. So a `superseded` decision whose
successor is not there yet is not a broken record: it is that interruption, it
is derived and reported as such, and every operation but its own redo refuses
while the batch is in it. That reading is for a batch whose cycle is still
running, and it ends with the batch: an outcome recorded over one is a
contradiction the records refuse (Batch outcome).

A decision may name the attempt it is about, and one shape needs it to. An
untouched successor standing open under a supersession recorded for the very same
reason answers two requests at once — that supersession redone, and this
successor superseded in its turn — because the human reason is the only thing
between them and both spell it identically. Unnamed, it is read as the redo,
which writes nothing; named, each is exactly what it says. A reason is evidence
for a later reader, not the identity of an operation, so where the two readings
diverge the request states which attempt it ends. Given, the name is a
precondition rather than a lookup: an attempt this batch never had, or one older
than the attempt that ended, refuses instead of acting on whatever happens to be
open, and naming an attempt that already ended asks to finish that decision — so
it holds to that decision's own outcome and reason.

Abandoning or superseding discards nothing that was learned: the record keeps
the base, every round, every task selection, and every candidate revision, and
the ref keeps those trees reachable. A batch carrying three abandoned
experiments and one open alternative is an ordinary state, not damage.

### Promotion

Promoting is what puts a candidate on the canonical source line, and it is the
only operation here that writes outside this repository's own records. What it
carries there is a **tree**: the merge commit it writes has the source line as it
stood and the round's pinned candidate as its parents, and the content is the
tree the replay measured. Two commits only imply a merge result — another merge
base, another strategy, a conflict resolved by hand all land somewhere else — so
the tree is what the evidence is about and the tree is what is promoted. The
merge is recomputed at promotion time and a different result refuses: a checkout
that merges those two commits into something else would put a tree nobody
exercised on the line with every recorded revision agreeing.

Three things are asked before anything moves, and each of them is a different
way for a promotion to be unjustified:

- **The round's evidence is promotable.** One completed run of the round being
  promoted, whose merge input this checkout confirms has not moved (Replay
  evidence). An unsealed round, a superseded candidate, a source line that moved,
  a run still going, a failed run, and a check this clone could not make are all
  the same answer to a promotion — and it is the reader's answer, so an operator
  meets one explanation of stale evidence whether they asked `status` or asked to
  promote.
- **Nothing is in flight.** A promotion ends the experiment, and after it nothing
  can conclude a replay run, record why one stopped, or withdraw a request
  against that experiment ever again. Every terminal decision has that boundary;
  the other two are reached when something has gone wrong, and this one is
  reached when everything went right, so the run that is going or the request
  that is outstanding is settled first. Promotable evidence says nothing about
  either — a second run started beside a result that is still exact leaves it
  promotable by design — so this is asked of the replay record rather than read
  off the evidence.
- **The gate is settled.** No proposal is still waiting. The gate belongs to the
  batch and a promotion ends the batch, so a draft left waiting is this batch's
  own analysis with nobody left to answer it. The same condition holds for a
  `no-change` conclusion, for the same reason.

A further check belongs to the repository rather than to the evidence: no
working tree may be sitting on the source line. A promotion moves a ref and
touches nothing else, which is what lets it run in a checkout busy with
unrelated work — and that is only true while the branch is nobody's. Every
worktree is asked, not the promoting process's own `HEAD`: a branch handed to a
linked worktree is one nothing here would otherwise look at, and the low-level
ref update moves it there without a word, leaving that tree and its index
describing the commit before while everything the promotion carried reads as a
pending deletion.

Then three writes make it real, in one order. The merge commit is written first
and named by nothing, so a run that stops there leaves an unreferenced object Git
collects. Then the **prepared promotion** is recorded on the experiment — the
merge unit, the exact commit, the reason, and the plan — because everything
after this point is recoverable only by a run that can say which commit was this
operation's. Then the source-line ref moves, compare-and-swap from the revision
the replay integrated onto: what advances a release line is ordinary Git, which
no lock here covers, so a line that took a commit between the evidence being read
and the merge being put on it refuses instead of carrying something nobody
measured.

After that move the promotion exists in the world whatever happens to the
process, and this experiment's evidence is stale from then on — including for
the run that comes back to finish it. So the second run does not ask the
evidence: it asks the prepared record which commit was this operation's, and Git
whether that commit is on the line. Neither weaker question works. A commit's
*shape* — those two parents, that tree — is shared by a merge somebody made by
hand, and reading shapes records a promotion this controller never performed. The
ref's *tip* is not it either: a line that took further commits after the
promotion still carries it, and a run that could not see that would leave the
canonical line holding a merge with no record of it anywhere. A prepared
promotion the line demonstrably never took — the merge is not on it, and it no
longer stands where the merge was to be made from — is discarded, because the
evidence behind it describes a line that has moved on and nothing can finish it;
one still waiting is moved by the next run rather than made again.

While a promotion is prepared, the experiment is not ended by anything else, and
no round opens under it. Abandoning or superseding it would retire the only
record saying the line may already be carrying its merge, which is the one way
this controller could produce the split it exists to prevent; revising past it
would leave that merge naming a round that is no longer the last. Because
nothing may open a round while one stands, a prepared promotion names the round
it was prepared from, which is the experiment's last — an earlier sealed round
names a candidate this experiment has already revised past, and a completed run
of that round is exactly what would make promoting it look justified.

Promotions recorded before the experiment carried a merge unit are read as what
they are rather than refused. That shape is an `experiment.json` at version 1: it
states the promotion revision and nothing about the merge, because the operation
that wrote it kept nothing else, and its schema is frozen so those records keep a
read path. Refusing them would not make one record stricter — it would stop every
reading of the batch that record ended. What such a promotion is still held to is
below; what it cannot be finished from is its own interrupted window, since the
targets it was planned for were never written down and no later run may state
them as the original plan.

Every version after that one states the field on every record, null included, and
the absence of it is read from the version rather than from the record. A current
record that does not state it is damaged and refuses, because the alternative is
to read it as an experiment that never prepared a promotion — which for the
interval this field exists to cover is the one wrong answer available: a merge
this controller made would be standing on the source line while the experiment
went back to being one that anything may end, revise, or promote again.

The records that follow are written while the experiment's ref is held where it
was read, for the reason every other transition holds one: that reading does not
survive the write it justifies. The source line is deliberately *not* held. A
promotion is a fact about a commit rather than about where a branch stands
afterwards, and holding the line would turn an ordinary advance arriving a moment
later into a promotion nobody can finish.

A promotion promises nothing about deployment. The outcome records the targets it
was **planned** for, as names rather than machine-local paths, and that is a plan
and never an observation: a promoted revision changes nothing a target holds
until that target is redeployed, and what any of them actually carries is read
from its own `.ai-deploy-lock.json`.

### Batch outcome

A batch is current until `batches/<batch-id>/outcome.json`
(`schemas/batch-outcome.schema.json`) records a conclusion the rest of its
lineage agrees with, and that record is what releases the next cohort. Two ways
reach it:

- `promoted` — an experiment was promoted; the outcome names it, the promotion
  revision, and the merge unit it went as (the round, the candidate, the source
  line and ref it was integrated onto, the tree, and the targets it was planned
  for). The revision alone would be a commit nothing could afterwards be held to;
  with the merge unit it is checkable against the run that justified it — and it
  is checked, on every reading, against the completed run that measured that
  exact integration, against the commit itself wherever the checkout holds it,
  and against the promoted experiment's own prepared promotion. A claim nothing
  checks is one a schema-valid record makes freely. The prepared record is the
  one of the three a promotion may not have: a version-1 promotion never wrote
  one, which is why the other two are put to the values the outcome states rather
  than to the prepared ones. What is lost with it is one record agreeing with
  another; what is kept is the run and the commit. A promotion later taken back
  off the line keeps this record exactly as it is — what says the line no longer
  carries it is the rollback record beside it (Rollback).
- `no-change` — the evidence justified no change to anything (invariant 7). The
  record carries the reason and nothing else: no experiment, no promotion
  revision, and no commit invented to represent a change that was not made.

The outcome and the experiments state one history between them, and it is the
whole set that has to agree rather than the record the outcome happens to name.
A `no-change` batch holds no experiment recording a promotion — that pair says
both that the source line moved and that it did not, and it is the contradiction
with nothing to name, so checking the named experiment alone never sees it. A
`promoted` batch holds exactly one, it is the one the outcome names, and the two
name the same promotion revision. Nor does either kind conclude over a
supersession still owing its successor: an attempt nobody created has not ended,
and that state is looked for in the batch that is current — so an outcome over it
ends the cycle on an attempt that does not exist and simultaneously puts it
beyond the redo that would finish it, with `status` no longer reporting it and
the next cohort released over it.

`no-change` is therefore written over a batch with nothing left open: its
analysis stage has ended, no experiment is open, no attempt records a promotion,
and no proposal is still waiting at the admission gate. The last one is the same
agreement read from the gate's side — a draft nobody decided is this batch's own
analysis saying a change was warranted, so admit it or decline it, both of which
are terminal for a proposal. It is the one condition the two outcomes share:
either of them ends the gate along with the batch. The four together are exactly
the derived phase `conclusion-pending`, which is the point: what `status` reports
a batch is waiting for is the condition of the operation that answers it.

### Rollback

A promotion that turns out to have been wrong is taken back off the source line
by a **rollback**, and everything it does is additive. The promotion commit stays
where it is, the outcome that recorded it is not edited, the experiment stays
promoted, and the batch stays concluded — what a rollback writes is a new commit
reversing the change, and one record beside that outcome saying the line no
longer carries it (`batches/<batch-id>/rollback.json`,
`schemas/batch-rollback.schema.json`). Nothing here resets, rewrites, or deletes
history: a line rewritten to drop a promotion would leave every other clone
holding the commit and this one denying it, and the record of what was promoted,
when, and on what evidence is the trail this whole contract exists to keep.

**Which promotion.** The newest one this repository recorded, and no other —
the same derivation `status` reports as the last promotion, so a rollback names
nothing and picks nothing. Reaching further back is not offered: everything this
repository did afterwards was decided on the line as that promotion left it, and
an operator who means to reverse something older is reversing a base rather than
a release. That is ordinary Git and a record of its own, not this operation.

**Who runs it.** An operator, or the release-assessment gate: a settlement of
`rolled-back` is this operation followed by the decision that names its commit
(Release assessment). Composed rather than reproduced, so everything below is
asked once and answered in one place — and a rollback answering no gate is still
an operation of its own.

Four things are asked before anything is written:

- **There is a promotion, and it is still effective.** A promotion a completed
  rollback already reversed is not reversed twice; the same request run again
  reports the rollback on record rather than making a second inverse.
- **Nothing here was built on it.** No experiment of a later batch stands on the
  promoted commit — neither its frozen base nor the candidate its last round
  pinned. Such an attempt was developed, reviewed and measured against a line
  carrying this change, and taking the change back out from under it leaves
  evidence describing a line that no longer exists. A checkout that cannot
  answer whether the two are related refuses: everywhere else here an
  unanswerable Git question is a fact about the clone and is reported, and this
  is the one place where the wrong answer costs somebody else's work.
- **The line still carries the promotion.** A line that was reset, or is not the
  one this promotion went onto, has nothing on it to take back — and a line that
  already carries none of the promotion's content is a state something outside
  this controller produced, which a rollback may not record as its own work.
- **No working tree is on the line.** The same repository-level guard a promotion
  makes, worktree-wide and for the same reason.

The content is computed, not taken from a record. Reverting is applying the
promotion's change backwards *to the line as it now stands*, so every commit that
landed after the promotion is carried through — that is the difference between a
revert and a rewind, and pinning the tree the line had before the promotion would
silently discard them. A change that cannot be taken back out without deciding
something conflicts, and a conflict refuses: what the line should hold instead is
a person's judgement with a working tree in front of them.

Then the writes, in the order a promotion uses and for the same reason — the Git
half cannot be undone by this controller, so it has to be recognisable
afterwards. The inverse commit is made first and named by nothing, so a run
stopping there leaves an object Git collects. Then the record, with its reversal
moment still null, which is what makes that commit *this operation's*. Then the
source-line ref moves, compare-and-swap from the tip the inverse was made on top
of. Then the moment is written under Git's own lock on that line, held where the
run last saw it, and the audit line after it.

A second run therefore asks the record which commit was this operation's, and
Git whether the line carries it — ancestry, never a shape a hand-made revert
shares and never the tip, since a line that took further commits still carries
the rollback. What that record states is recomputed rather than believed, in the
reader and again before the run acts on it, and in the same three terms the
writer computed it in: the line it was made from carries the promotion, reverting
the promotion out of that revision produces the tree the record names, and that
tree is not the one the line already had. An in-flight record is what moves the
canonical source line, so without that a one-parent commit written into a file
would be a revision nobody checked reaching it — and a rule only the writer keeps
is one any file written beside it escapes, which is why the last of the three is
the reader's too rather than the refusal of a hand-made reversal alone. A checkout
that cannot recompute the revert reads on and says what it could not check; the
run about to move the line refuses instead, for the reason the built-on question
refuses. A revert that *conflicts* is not that case: Git answered, and the answer
is that no commit is this revert — so the record is refused wherever it is read,
in the checkout that asked and in the reading that would otherwise retire the
promotion. Where the line has left the prepared inverse behind, the inverse is
made **again** from where the line now stands rather than discarded and refused
as a prepared promotion is: a promotion's merge carries a tree that evidence
exists about, and a moved line makes that evidence describe something else, while
a rollback's content is computed from the line and nothing measured is
invalidated by the line moving.

A rollback is not a judgement the promotion's own evidence contains. A replay
measures a tree; whether the promotion argued from it should stand is a later
judgement against a later cohort, and what this records of it is the operator's
reason (Promotion evidence). Nor does it say anything about deployment: targets
carry what they were last deployed with, and a reversed promotion reaches them
the same way the promotion did — through `aii-2 deploy`, read from each target's
own receipt.

### Release assessment

A promotion is not the end of the evidence chain. The last of the revisions in
play is why: a promoted commit changes nothing an evaluation can see until
targets carry it, so what a release actually did is a question for the cohort
whose reports were produced at that effective revision. That cohort is the next
frozen batch, and its reading is recorded in
`batches/<assessing-batch-id>/release-assessment.json`
(`schemas/release-assessment.schema.json`) — in the *assessing* batch's
directory, because the promoted batch's records are terminal and a later
cohort's judgement is not theirs to carry.

The obligation belongs to the first batch frozen after a promotion, and it stays
there. A later cohort ordinarily has nothing to assess: the batches form a series
(invariant 14), so by the time it can freeze, the reading has been taken and
settled. A batch whose predecessor concluded `no-change` owes nothing of its own
either — that conclusion fabricates no revision (invariant 7), so there is no
upgrade for an effect to be measured against and none is invented.

What it does not do is lift the older obligation. A cohort can end its own batch
without answering the gate — concluding `no-change` asks only that nothing about
*that* batch is still open — and the release before it is then still unjudged
while the next cohort follows it and owes no reading. So the obligation is
resolved by asking which cohort owes the reading of the release before this
batch, rather than by asking whether this batch owes one: the next base freeze
waits on that cohort's settlement, and an obligation it left outstanding is
recorded and settled where it sits, in that cohort's own directory. A gate that
could no longer be answered would stop the lineage for good, since the freeze
waits on the settlement and the settlement is what would release it.

**Derived, then recorded.** The release under assessment, the two cohorts, their
denominators, the exclusions and the comparability facets all follow from two
immutable manifests, the promoted batch's outcome, its rollback record if it has
one, and Git — so they are derived on every read like the rest of the lifecycle,
and the record is checked against that derivation. What is committed is what
cannot be re-derived: measurements taken from machine-local evaluation
artifacts, the counterfactual a harness ran, and the verdict, confidence and
rationale of the session that judged them. The cohorts are restated all the same,
because evidence that names no denominator is a directional claim whose sample
nobody can see.

Recording one runs that derivation as a write. The cohorts, denominators,
exclusions and facets come from the manifests rather than from whoever is
recording; the verdict is held to the evidence the record carries before anything
lands; and the audit line that follows names the cohort, the release and the
verdict, which is a bounded code this controller authored. So a reading that
could not be read back is never written, and a record written beside it by hand
is held to the same rules the moment it is read. Running the same formation again
reports the reading already on record — an interrupted one is finished by
repeating it — and a request that says something else is refused, because a
cohort reads a release once and that record is what the counterfactual and the
settlement are added to.

The release is read from the promoted batch's `outcome.json`. That record carries
the whole merge unit, which is what makes the pair to compare a reading rather
than a reconstruction: the pre-promotion revision is the merge input — the
promotion's first parent — and the promoted one is the revision itself. The
experiment's own prepared promotion is not the place to read it, since a
promotion made at experiment schema version 1 states its revision alone.

**Which question is being answered.** A promotion a rollback reversed is a
different question from a standing one, not the same question with a caveat: the
cohort produced after the reversal was produced at a revision the change is not
in. So the record states which of the two it assessed, and it is not re-derived
afterwards — the ordinary consequence of a regression finding is the rollback
that follows it, and re-reading that field would make every such rollback
contradict the assessment that justified it. What the line carries *now* is the
lineage's own reading. The other direction is checked: a record asserting the
release was already off the line is held to a reversal this repository recorded
and landed, and that exception costs nothing, because a promotion a completed
rollback reversed is never effective again. A reversal still in flight is not one
of those, and a reading formed beside one records the release as standing — which
is what it was. The inverse commit exists and the line has not taken it, so it
places the reports produced on a line that did take it and states no reversal at
all.

**Which reports contribute.** Membership comes from the two frozen manifests and
nothing else (invariant 3). Each report is placed by its own
`provenance.effective_revision` — the revision that target actually held — asked
of Git as an ancestry question, so a target redeployed at later work that
includes the promotion is post-release evidence too. The targets a promotion was
*planned* for are not that reading and never stand in for it. A report that
cannot be placed is excluded with a reason and stays in view: no effective
revision recorded, a revision this checkout cannot resolve, or a line that had
already taken the change back out. Exclusions are for what provenance cannot say
and never for what the numbers came to — a cohort narrowed to the reports that
agreed with the change is the base rate invariants 1 and 2 exist to keep
knowable.

**Where that revision comes from.** It is the evaluated target's
`.ai-deploy-lock.json` `source_git_commit` — the canonical commit its deployed
payload was rendered from — read from that target's worktree at evaluation time
and carried on the report from then on. Only the side holding the target at that
moment can state it as a fact, so the feed that publishes the report is the only
truthful source of it, and this repository derives it from nothing. A target's
lock read *now* answers what that target holds today, which is a different
question: placement is an ancestry test, so a target redeployed after the
promotion would put its pre-release reports on the `after` side and manufacture
exactly the directional claim this reading exists to refuse. Recovering the lock
from the target's own history is closer, and still an inference on a repository
this system does not own, over a path a feed supplied, for the one reading that
costs somebody a promoted change. A report that arrived without the field stays
without it (invariant 4 keeps a missing field missing).

**When a lock may be published as one.** A commit written beside a payload is not
by itself an account of that payload — and placement reads it as one. Two things
have to hold before a lock states a report's effective revision, and neither is
checkable from here:

1. *The receipt describes the payload.* A deploy copies the working tree, so
   `HEAD` alone says when it ran, not what it carried: canonical files edited
   and not committed produce bytes no commit holds, and the revision beside them
   would name content the target never ran. This repository's deploy therefore
   states `source_git_commit` only when Git says the canonical tree it read was
   exactly that commit's — nothing modified, staged, deleted or added under it,
   and no deployed file Git does not track — and states nothing otherwise
   (`ai_native_deployment/lockfile.py`; `aii-2 deploy` prints which of the two
   happened). A lock stating no revision is not a lock to publish a revision
   from, and a lock written before this rule states a commit nothing checked.
2. *The target still matches the receipt.* A deployed payload edited in place is
   bytes the receipt no longer describes, and the revision would then name a
   protocol the evaluated work did not run under. `aii-2 status <target>`
   answers that, on the machine holding the target, and the answer is only good
   as of when it was asked — at evaluation time, not later.

When either fails, publish the field absent. An excluded report costs a
denominator; one placed by an unverified revision manufactures the direction
this whole reading exists to refuse.

**What the published feed states today.** orch-hub publishes none of these
provenance fields — its per-report provenance describes runs and git history, and
it states that evidence packs carry no `.ai-protocol` version for the evaluated
task — so every report imported from it carries `effective_revision: null`, is
excluded as `effective-revision-absent`, and contributes to neither denominator.
A cohort drawn from that feed alone is therefore empty on both sides: it supports
no direction in either sign, and the pinned counterfactual below is the only
directional instrument there is. That is absent
evidence, not a negative reading and not a defect to be worked around by
inferring the field locally. For the cohorts to become readable, the publisher
has to carry the lock it already had in hand, under the two conditions above:
`source_git_commit` verbatim as `effective_revision`, and the lock's
`canonical_payload_sha256` as `deploy_lock_hash` — the second because a source
commit this repository's history no longer reaches is unresolvable here while the
payload hash still identifies what was deployed. Both are stated as absent for a
target that carried no lock, for a lock stating no revision, and for a target
that no longer matches its receipt: an unstated field is honest, and a guessed
one is not.

**Verdicts.** `improved`, `neutral`, `regressed`, or `inconclusive`. The first
three are directional, and a directional claim rests on one of two kinds of
evidence.

The **cohorts** carry one only when nothing else explains their difference: every
comparability facet coherent — one evaluator, rubric, protocol revision and role
configuration across both sides, at least one repository present on both, and the
same shape of work — both sides at or above the configured minimum unique-task
count, and at least one goal quantity measured on both sides (invariants 1, 4, 5,
13). The shape of the work is the facet nothing frozen states: a manifest entry
carries identity, hashes, evaluator and deployment provenance and nothing about
the kind of task it judged, and the two cohorts are two different task sets by
construction (one report per completed task). So it is recorded as the unknown it
is — invariant 4 — rather than approximated by repository coverage, which is
coverage and not a match. The facet becomes answerable if a later manifest
version carries durable task-shape provenance.

The **counterfactual** carries one on its own: a completed run measuring a goal
quantity on both the pre-promotion and the promoted revision is a comparison in
which the release *is* the only difference, so what the cohorts came to is
context rather than the claim's support. `regressed` rests on it in every case.
In practice, then, the cohorts show the base rate and raise the suspicion, and
the pinned run settles the direction — in either sign.

A verdict resting on that run is also the direction the run measured. It supports
the way every goal that moved points; goals it left unmoved neither add to that
nor stand against it, and all of them unmoved is `neutral` — the release measured
changing nothing, which is a finding rather than the absence of one. Goals
pointing both ways support no direction: which quantity a release is judged on is
chosen when the run is configured, and the rest are recorded as observations
(invariant 13), so weighing an improvement against a regression afterwards would
make that choice on the operator's behalf. What the cohorts came to is read by
the judging session — that is what `confidence` and `rationale` are for — but a
claim the pinned run contradicts is a claim about something else.

`inconclusive` is a real result. Mixed provenance, too small a sample, work whose
shape nothing states, and a harness that could not run are reasons to know less,
never evidence against a release — and the reading that costs somebody a promoted
change is the one that has to be measured rather than inferred.

**The counterfactual.** A cohort difference can suspect a regression; what
settles it is the exact pre-promotion and promoted revisions exercised with one
case set, one evaluator and one configuration, so that the release is the only
difference between the two halves. It is the replay boundary, driven in temporary
worktrees, and it moves no release ref.

The pair is stated rather than computed. The promotion *is* the integration — a
commit made from the merge input and the candidate, carrying the measured tree —
so what a run is handed is that merge unit exactly as the outcome records it,
with the pre-promotion revision as the base it is measured against. A checkout
that does not hold both revisions measures nothing and says so; whether the
commit it does hold carries the tree and parents the outcome states is the
lineage's own reading, taken before this operation writes anything.

One run measures both revisions, which is the harness boundary's own shape: a
report states each quantity on the base and on the candidate. That is what makes
the two halves of this comparison incapable of drifting apart — the cohort, the
evaluator and the configuration are one selection governing both sides, rather
than two that would have to be shown to have matched afterwards.

It is recorded in the assessment and nowhere near an experiment's `replays.json`.
That record binds every run to a round of that experiment and to the candidate
its seal pinned, which a pre-promotion/promoted pair is neither of; the lineage
also reads it on every derivation, so an entry it could not account for would
stop `status` for the whole history rather than for this comparison. The harness
key the run occupied is recorded, because a conforming harness answers one key
with one run: it has to be a position no experiment holds — neither a recorded
run nor a request it withdrew, since both stay allocated and neither is ever
reissued — and a promoted experiment can never open another round, which is what
makes a round beyond its last unreachable forever.

The request for a run is durable before the harness is asked, exactly as a
replay's is: from the moment it is written something may be running that this
record does not describe yet, so what names it is written first and every
refusal is made before the asking. Asking again re-submits that request
unchanged. A request is not evidence and no verdict rests on one.

A run that measured nothing is answered by another attempt at the next key, and
a run that measured the release is not: the release is measured once, and a
second completed answer would leave a reader choosing which of them the reading
rests on. A harness that died, lost its handle, or answers with something this
record cannot hold is ended by recording why — the reason is the operator's,
because the harness is the thing that could not give one — and that failure is
what another attempt answers. A comparison that never gets made leaves the
reading `inconclusive`, which is the same answer this contract already gives for
a harness that could not run.

A request the harness never answered for is given up rather than ended: there is
no run to write a failure onto, and inventing one would state a case set, an
evaluator and a configuration nobody selected. It is also the state re-asking
cannot repair — one key is answered with the run it already began, so a harness
that cannot describe what it is running says the same unrecordable thing every
time, and neither ending operation reaches a request that never became a run.
What a withdrawal does not give up is the position: the harness may be running
something under it, so it stays allocated forever, the record keeps the window
and the pair for whoever goes looking, and a further attempt is numbered past
the run and the withdrawals together.

**Reading the run.** The reading a cohort forms is written while the cohorts are
all there is, and is then settled by the numbers the completed run reports: the
verdict, the confidence and the sentence saying why, none of which existed when
the reading was formed. That revises the one record rather than adding a second,
which is the difference from forming it — a formation happens once because two
records of one release would leave a reader choosing between them, and this is
the evidence that record was written to be added to. It stops when the gate
settles: a decision stands on the reading it was made from, so a run started,
concluded, ended or given up afterwards, or a reading revised, would rewrite the
thing that was decided from.

That rule also says what a settlement may stand over: a completed run, a failed
one, or none at all. Not a run still going and not a request outstanding —
nothing is added once the gate answers, so numbers arriving after it could never
be recorded, and the decision would rest on a measurement that is lost. Conclude
the run, end it with a reason, or withdraw the request, and settle over what came
back.

**The settlement** is a human decision recorded on the assessment: `retain`
leaves the release as the line later work builds on, and `rolled-back` names the
inverse commit the rollback operation made *and landed*. A rollback records that
commit before the line takes it, so between the two writes the record already
names a revision while the promotion is still effective — a durable state, since
an interrupted rollback stays there until the operation runs again. Naming the
commit is therefore only half of what a settlement claims; the other half is the
lineage's reading that the promotion came off the line. The evidence and the
decision live in one record because a reader finding only one of them would have
to guess at the other. The reason is the operator's and not the assessment's — an
`inconclusive` reading is an ordinary ground for retaining a release, and a
rollback is a judgement the promotion's own evidence never contained (Rollback).

What waits on it is the next base freeze and nothing else (invariant 17). The
first experiment of the batch being frozen takes the commit every alternative in
it starts from, and the two settlements give two different commits: the line
carrying the release, or the line with the inverse commit on it. So the gate sits
between the reading and that freeze, and nowhere else — the analysis, its drafts,
its rejections and its closure are all unaffected, and a batch with no release
before it waits for nothing. The freeze is held to the commit as well as to the
answer: a base that carries neither the release nor the reversal would settle by
accident what the human was asked, so it is refused whether it came from `HEAD`,
from an explicit revision, or from a line that moved after the decision.

Recording the settlement composes the reversal rather than repeating it.
`rolled-back` runs the rollback operation, which takes the reason and nothing
else: which promotion, which line, whether any later attempt stands on it and
whether a working tree is sitting on that branch are its own readings, and a
second spelling of them here would be a second answer to give an operator. A
reversal already on the line is adopted instead of made again — the operator may
have run the rollback themselves, and a run interrupted between the commit and
the record is finished by repeating it. Every refusal is made before the commit:
a cohort that recorded no reading, a gate that already answered, evidence still
in flight, a `retain` of a release that is already off the line, and a
`rolled-back` of anything but the promotion a rollback would reverse. The whole
settlement runs under one hold of the single-writer lock, the reversal included,
so the state those refusals cleared is the state the commit lands on; a
composition that let go of the lock to reach the rollback would leave a window in
which another writer settles, measures or revises the reading, after which the
release is off the line for a decision nobody can record. The audit line names
the cohort, the release and which way the gate went; running the same settlement
again reports what is on record and appends no second line.

The rollback's own refusal that later work stands on the promotion is not
weakened by any of this, and the way out of it is recorded rather than worked
around. Retaining is always available, and its reason is where a release kept
because a later attempt was already built on it gets explained; the alternative
is to end that lineage and reverse the change outside this controller, which is
not an operation here and so is not one this gate can record as its own. In the
ordinary sequence that state does not arise: the gate answers before the batch's
first experiment freezes a base, so at the moment a rollback would run there is
nothing built on the release to take it out from under.

### What is derived

None of this stores a lifecycle phase, and none of it may be inferred from the
checkout. The current batch, the open experiment, its last round and whether
that round is candidate-ready, the candidate revision, a successor a
supersession recorded and did not create, the drafts still
waiting at the gate, what the current round has been replayed by, the last
promotion this repository recorded and whether the source line still carries it,
which release the current cohort follows, which cohort owes the reading of it,
which of that cohort's reports can contribute to one, and whether that reading
has been settled
are
re-derived on every read from the committed manifests, closure records,
experiment records, replay records, rejection records, rollback records,
release-assessment records, the
experiment refs, and
Git — so any clone, on any branch, and a machine that has lost `.ai-tasks/`, all
derive the same answer.

Deriving is also where the records are held to each other. A concluded batch and
its experiments state one history, and the merge unit a promoted outcome carries
is checked against the promoted experiment's own prepared promotion, the
completed run that measured that exact integration, and the commit itself
wherever the checkout holds it. A rollback is checked the same way, against the
outcome it reverses, against its own commit's shape, and against the revert
itself — that commit's tree is what taking the promotion back out of the line it
was made from produces. A record whose claim nothing checks is a claim any
schema-valid file can make, and these name what reached the canonical source line
and what came back off it.

A release assessment is held to the same treatment, against the frame its own
batch's provenance supports: the release it names, the frozen membership it
places, the side each report's effective revision puts it on wherever Git can
answer, and — as rules of the record rather than of whoever wrote it — the
denominators and the comparability facets its two frozen manifests give, a
directional verdict resting on evidence that can carry one and pointing the way
that evidence came to, a counterfactual pinned to the release's own pair and
keyed clear of every position its experiment could hold, and a settlement held to
a rollback the source line took.
Those denominators and facets are committed content, so they are checked on every
clone, including one that can resolve no effective revision and place no report
at all: a reader that skipped them there would accept a cohort size and a
coherence claim nobody could have derived. A reading that could not have been
formed is not one that may be read back.

Replay evidence is the one reading with a question Git may be unable to answer:
whether the source line has moved since a run measured it needs the ref that run
integrated onto, and a clone that never fetched it holds no answer at all. That
is reported as the unanswered check it is, never as agreement — a promotion is
refused on it, because a check nobody could make is not one that passed.

An outstanding request is reported beside what the round has already been
measured by rather than instead of it. It measured nothing, so it changes no
state; what it changes is the next step, and a round whose newest run failed
while a further one is unaccounted for is not the same situation as one simply
waiting to be run. The single reading it is deliberately left out of is a result
this checkout confirms is still exact: reporting it there would make evidence
that describes the tree in question unpromotable on account of work that has
measured nothing yet, which is a question for the promotion gate rather than for
the reader. A completed result whose source line cannot be checked here is not
that reading — the unanswered check has already shut the gate, so the request is
reported beside it like every other reading short of a promotion. A withdrawn
position is not reported at all — nothing here can learn whether the harness ever
ran it, and the record keeps it for whoever goes looking.

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
`seal-round`, `revise`, starting, concluding, ending and withdrawing a replay,
abandon, supersede, promote, `conclude-no-change`, rollback, recording a release
assessment, starting, concluding, ending, withdrawing and resolving its
counterfactual, and settling it —
writes
several places at once: the experiment ref, a versioned record, `.ai-tasks/` and
its index, the audit ledger. Not every one of them touches all four — a seal and
a revision write a record and an audit line, because what they record is a
decision about work that has already happened, and an abandonment or a
conclusion writes one record and an audit line for the same reason. A replay
start writes only its record, and a withdrawal only takes one back: what an audit
would say about either is what the record already says, and the run's outcome is
the event there is something to audit about. A promotion writes further than any
of them — a commit, the record naming it, a ref on the source line, then two
more records and two audit lines — and it is the one whose middle cannot be
undone by this controller. A rollback is the same shape with one record: a
commit, the record naming it, the ref, then the moment written into that same
record and one audit line. A release assessment writes one record and one audit
line, for the reason a seal does: what it records is a judgement about work that
has already happened. Its counterfactual writes into that same record and
nowhere else: the request for a run, then the run — neither audited, for the
reason a replay start is not, and a withdrawal that takes a request back is not
either — then the run's end with one audit line, and the reading its numbers
settle with another. Settling that reading writes into it once more, with an
audit line saying which way the gate went; a `rolled-back` settlement is that
write with a whole rollback in front of it, run as its own operation rather than
as steps repeated here, so the widest thing this gate does is what a rollback
already does and the decision lands after it — all of it inside the settlement's
own hold of the lock, which is what keeps the reading the decision was checked
against from moving between the refusals and the commit.
They take
the same single-writer lock as import and freeze, they write in an order where
the durable record is what makes the operation real, and every step is safe to
redo, so an interrupted operation is finished by the next run rather than
repaired by hand.

Recording a release assessment, every operation on its counterfactual, and
settling it are the ones whose preamble is not the whole of the shared one, and
the difference is which stage they belong to. Every other operation here writes
into a batch's change lineage, which is why each refuses while the analysis stage
is still running; this reading *is* part of that stage — the generated analysis
task's second question — so what guards it is which batch is current (invariant
14, from the whole history) and which cohort owes the reading of the release
before it, and nothing about the stage's end. Ordinarily those are the same
batch; where the owing cohort ended without answering, they are not, and the
record is written where the obligation is rather than where the current batch is.
Once that obligation is answered it is closed to everything that would add to the
reading, which is then told whose record it is — but not to the settlement's own
redo, which reaches the answer in order to report it back; a settlement a caller
could not repeat is one it could not recover, and a carried-forward settlement is
no more repeatable from the cohort that took it than any other. None of them
moves a ref or touches
an experiment; a `rolled-back` settlement moves the source line, and it does that
by running the rollback, under the guards a rollback has. What they share is the
rest: the lock, the record before the audit line, and a redo that reports what is
already on record.

They are ordered among themselves by what each one is about. The cohorts are read
first, because a pinned run answers a suspicion they raised and the reading is
where that run is recorded; the run's numbers settle a verdict only once it has
completed; the gate answers over evidence that is over, never over a run still
going or a request outstanding; and nothing is added to a reading its gate has
already answered. What the answer releases is the next base freeze, which is
guarded from the other side: the first experiment of a batch following that
release refuses until the owing cohort's settlement is on record, and then
refuses a base that is not on the line the settlement chose (invariant 17).

An ending is also guarded by the state of the ref it ends over. An experiment's
ref is described only while that experiment is open, so a decision recorded over
one standing off its own pinned history retires the finding along with the
attempt — and the revisions that record names quietly stop being reachable with
nobody left to say so. A ref this checkout simply does not hold is not that: it
is the ordinary state of every clone that never fetched the namespace, and it
stops nothing.

Starting a replay is guarded by that ref state too, and concluding one is
deliberately not. A run is started against a lineage whose ref still agrees with
its record; a result is a fact about a run that already happened, and a ref that
moved since makes that evidence stale rather than wrong — which is derived and
reported. Refusing to record it would discard the only durable form of the
measurement and leave the run recorded as going forever. Resuming a request is
answering one, not making one, so it is guarded the way a conclusion is: the run
it records already exists.

Every one of them also reads the replay evidence of every experiment the batch
holds, and stops on a record no reader accepts. An unreadable record stops the
operation wherever this contract finds one, and that rule needs stating here
because the lifecycle operations never touch that file otherwise: an attempt
could be added to, revised, or ended over evidence nobody can read. Ending it is
the case that makes this more than tidiness, and it is the shape the ref check
refuses for the same reason — evidence is derived for the open experiment only,
so a decision recorded over an attempt whose replay record is malformed retires
the finding along with the attempt, leaving the file on disk with nothing left
to report it. Every experiment of the batch rather than the open one, so ending
an attempt does not end the refusal.

That lock covers this package's own runs, and the two round transitions and every
terminal decision need more than it. Each is decided from one reading of where
the experiment ref stands — the tip a seal pins, the pinned revision a revision
opens the next round from, the ref state an ending is recorded over — while what
ordinarily advances that ref is a fetch, a push, or an operator's own
`update-ref`. A commit arriving between that reading and the record would be
pinned as a candidate whose ancestry nothing asked about, or taken up as the new
round's work, which leaves a commit made under a round that had already been
measured indistinguishable from one made after it — the very ordering that lets
replay evidence name one pinned tree. An ending loses something else again, and
loses it for good: the ref is described only while its experiment is open, so the
decision is the last reading anyone takes of it, and a ref leaving its pinned
history in that gap has its disagreement retired by the very write that follows
the check. Reading again afterwards recovers none of them, since the records that
would disagree are the ones being written. So a seal, a revision, and a terminal
decision are recorded under Git's own lock on that ref, held at the revision they
read; one that cannot be held there records nothing and is decided again from
where the ref now stands. A supersession's successor works on a ref of its own,
which that hold neither covers nor stands in the way of creating. The source
line a promotion moves is deliberately not held that way: what makes that
operation finishable is the commit it recorded before moving anything, so a line
advancing afterwards is ordinary rather than fatal, and holding it would make
every such advance a promotion nobody could complete.

A rollback's completion is held that way, and the difference is what the two
records claim. A promotion records that a commit was made and put on the line,
which stays true whatever the line does next. A rollback's reversal moment
records that the line *carries* the inverse — the reading every later one takes
of whether that promotion still stands — so a reset back to the promotion between
the move and the write would leave a finished rollback beside a line still
carrying the change. The line is therefore held where the run last saw it, at the
tip it just made or the one it found the inverse already on, until the record
catches up; a line that moved in that window writes nothing and leaves the
rollback in flight, which the next run finishes or re-prepares from where the
line then stands.

An ending is guarded once more, by whether the attempt has a promotion prepared
on it. That record may name a merge already on the canonical line with only its
own records missing, so abandoning or superseding retires the one statement that
this is so — the single way an operation here could leave another repository's
release line describing something no record explains.

That order is the same throughout. The experiment ref goes first, because it is
the one thing that must never be created twice or restored afterwards: a clone
without `refs/evolution/*` is the ordinary state, and a repair that recreated the
ref at the base would leave it behind the real work with the next commit forking
the history. Then the durable record, which is what makes the operation real.
Then `.ai-tasks/` and its index, which are derivable from the record and the
drafts, and therefore restorable. Then the audit line, which nothing derives
state from. So an interruption can leave a ref nothing yet records, or tasks a
record already names — never a task in the active pool that no experiment
accounts for, which is work a turn selection would dispatch with nothing behind
it. Redoing the same operation with the same selection is how it finishes: it
recognises its own recorded work and writes what is missing, rather than
admitting anything a second time — a rejection whose record landed and whose
audit line did not is finished by declining the same drafts for the same reason,
and a seal, a revision, or a replay conclusion whose record landed reports the
pin, the round, or the result that is already there rather than writing a second
one. A redo is as guarded as the
operation it completes, so the ref check that stops a fresh admission stops the
resumed one too — and a ref that moved while a round was candidate-ready stops
the redo before it can report that round as though nothing had happened.

Recognition is identity, not position — and identity is read from the structures
that own the values, never from text containing them. A file standing at an
admitted task's id is that admission's copy when its own frontmatter declares
that id once, and the whole of its one `## Admission` section is the section that
admission wrote: the batch, the experiment and round, the ref, the base revision,
and the digest of the proposal it implements, through to the next heading and
with nothing else under it. Both halves are structural. A block declaring a
second id says the file is two tasks, and every reader takes whichever it reaches
first; a line added under the provenance is the admission naming something no
admission recorded — another base to work from, another ref to commit on — which
reading only as far as the recorded lines reach would never see, and which
stopping at the first line the file merely made look like a heading would not see
either. The next heading stands at column 0; an indented one is content, and
content under that provenance is compared like the rest of it. That section is
the immutable part of a copy, and everything a session changes lies outside it —
the lifecycle above, the log below — so an ordinary claimed and logged task is
still recognised, while a file that merely mentions the same values in prose, or
a real copy of a different proposal renamed into this one's place, is not. An
unrelated file there stops the redo instead of being adopted, listed as this
experiment's work, and dispatched. A task the record already shows completed is
not rewritten by that repair; close-out archived it, and recreating it would
reopen work that finished. So is one that has finished before the record observed
it: the completion observation is a later operation, and between the two, a task
archived or `completed` in place belongs to close-out rather than to the active
pool a restored row would put it back in. Any state they cannot account for —
a second current batch, a second open experiment, an experiment
left open under a later one, a gap or a second spelling in the batch's ordinals,
an experiment whose base is not the batch's, a candidate revision that does not
descend from the one pinned before it, a ref that is not where its record says or
that has moved past a candidate-ready round, a tip this checkout cannot show to
descend from the revision pinned before it, a ref that moved out from under a
transition since the reading that transition was decided from, a round sealed
with no candidate or
carrying a candidate nobody sealed, a round sealed with nothing admitted into it,
a run started against a round whose candidate is not pinned, a second run started
while one is still going, a candidate that does not merge onto the named source
line, a source line named by anything but a fully-qualified ref Git can hold, a
run recorded under a handle another run already has, a conclusion for a run this
controller never recorded, one attempt of a round held by two of its runs and
withdrawals or missing from them, a replay request standing anywhere but at the
position the run it becomes will take, a resumed request answered as a different
one, a conclusion or an ending taken while a request is outstanding,
a promotion of a round whose evidence is not promotable, a promotion taken while
a run is going or a request is outstanding, a candidate whose merge no longer
produces the tree that was measured, a source line that moved between the
evidence being read and the merge being put on it, a promoted outcome stating the
revision without the merge unit it went as, a prepared promotion naming anything
but the round it was prepared from or a reason other than the one the decision
records, a rollback of a promotion its batch did not record or of a batch that
promoted nothing, a rollback naming the promotion it reverses as its own inverse
commit or naming a commit Git describes differently, a rollback whose revert Git
says conflicts or whose commit takes nothing back out of the line it was made
from, a rollback of a promotion a later attempt stands on or of a line that no
longer carries it, a second
reversal of a promotion already rolled back, a record of a version this build has
no reader for,
a task whose completion this machine cannot observe, a second reason for a round
that is already open, a task admitted into a sealed round with
no completion observation, a draft already consumed, a draft that is not the inert
task file described above, a task id admitted twice, a file at an admitted task's
id that is not its copy, a second decision about a proposal already declined, a
second decision about an attempt that has already ended, a decision naming an
attempt the batch never had or one older than the attempt that ended, a batch
owing the successor a supersession recorded — to anything but that supersession
redone — a `no-change` conclusion over an open attempt or over one that records a
promotion, an outcome of either kind over a gate where a proposal is still
waiting or over a batch owing a successor, a
`superseded` decision naming anything but the successor it created, an outcome
that disagrees with the experiments about a promotion, an unreadable record —
stops the operation with what it found, instead of picking one reading and
continuing.

The freeze, and the completion of one that was interrupted, are guarded by the
same reading. Which batch is current decides both whether a new cohort may form
and which unfinished freeze may be completed, so it is derived from the whole
lineage before either writes: a conclusion its own experiments contradict leaves
its batch current rather than releasing the next cohort, and two current batches
stop the run instead of one of them being picked to continue.

## Evolution task requirements

Every evolution task must:

- Cite this contract and, after batching, one immutable batch ID.
- State its runner protocol revision; change tasks also state the batch's base
  revision, and the experiment and draft id they were admitted from.
- Use only reports named by the batch manifest for batch-level claims.
- Record, when it analyzes the first batch frozen after a promotion, one reading
  of that release from its own frozen provenance and the promoted batch's
  (Release assessment).
- Keep report content out of the taskfile except bounded summaries and
  references.
- End with explicit evidence disposition and unresolved-risk statements.
- Preserve a clean separation between descriptive `.ai/` snapshots, normative
  evolution policy here, and canonical payload delivered to target repos.

## Promotion evidence

A protocol-improvement task is not promotion proof. Its canary or replay must
record, in the run's own record (Replay evidence):

- The batch's base revision, and the experiment and round whose candidate
  revision was exercised. That revision is the one the round's seal pinned
  before the run started (invariant 16); evidence that names an experiment but
  not a round says nothing after the next `revise`.
- The merge input the candidate was integrated onto, and the tree that produced.
- Eligible cohort and exclusions.
- Evaluator/rubric revision.
- Expected directional changes, recorded before the run produced any.
- Quality and convergence outcomes.
- Subscription/quota and elapsed-time observations when available.
- Regressions and ambiguity.

The rollback decision is not among them. A run measures a tree; whether the
promotion argued from it would be reversed, and how, is a property of that
promotion and is recorded with it. Keeping it in the run's record would put a
decision nobody had yet made into the evidence it was going to be made from.

What reaches canonical source is work that went through the ordinary reviewed
workflow before it was ever measured: each admitted change task is developed and
reviewed like any other, and a round is sealed only once every one of them has
been observed complete (invariant 16). The promotion is the merge of that
reviewed tree onto the source line and adds nothing to it — the commit it writes
carries the tree the replay exercised, which is why a merge producing anything
else refuses (Promotion).

Deployment stays a separate, explicit step: targets receive it through
`aii-2 deploy`, and deployed target files are never edited directly. The
promotion revision is a commit on the source line, distinct from the candidate
tip that was measured, and distinct again from the effective revision each target
reaches only when it is redeployed — which is why the outcome records the targets
a promotion was *planned* for and never what any of them holds.
