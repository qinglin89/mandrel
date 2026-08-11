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

Six different commits, which an evidence trail must not substitute for one
another — a candidate measured against the wrong one measures nothing.

| Term | What it is | Recorded in |
|---|---|---|
| Base revision | the exact source commit every experiment of one batch starts from, frozen when that batch's first experiment is created | `experiment.json.base_revision` |
| Candidate tip | the current head of the experiment's ref — where an open round's work is accumulating, and nothing to measure while it can still move | `refs/evolution/experiments/<experiment-id>` |
| Round candidate revision | the tip pinned when a round was sealed; immutable, what replay exercises, and what that round's evidence describes | `experiment.json.rounds[].seal.candidate_revision` |
| Merge input revision | where the source line stood when a replay integrated the candidate onto it; it moves for reasons the experiment knows nothing about, and each time it does that replay stops describing what a promotion would carry | `replays.json` `integration.merge_input_revision` |
| Promotion revision | the canonical source-line commit carrying a promoted experiment's change; never the candidate tip it came from | `outcome.json.promotion_revision` |
| Deployed (effective) revision | what one target repository actually holds; per target, and it lags promotion until that target is redeployed | target `.ai-deploy-lock.json`; a report's `provenance.effective_revision` |

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
  cases/                            curated sanitized regression cases
  experiments/<experiment-id>/experiment.json  identity, frozen base, ref, rounds, decision
  experiments/<experiment-id>/replays.json     every replay run against that experiment's rounds, and the request outstanding

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
finish, and the position blocked for the same reason. It records no reason and
no run — a request that never became one measured nothing, is derived as
nothing, and leaves nothing for a reason to be attached to — so it reports the
integration it withdrew, which is what an operator needs to stop that run at the
harness. Run again it reports that nothing was outstanding.

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

### Batch outcome

A batch is current until `batches/<batch-id>/outcome.json`
(`schemas/batch-outcome.schema.json`) records a conclusion the rest of its
lineage agrees with, and that record is what releases the next cohort. Two ways
reach it:

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
are terminal for a proposal. Those four conditions are exactly the derived phase
`conclusion-pending`, which is the point: what `status` reports a batch is
waiting for is the condition of the operation that answers it.

### What is derived

None of this stores a lifecycle phase, and none of it may be inferred from the
checkout. The current batch, the open experiment, its last round and whether
that round is candidate-ready, the candidate revision, a successor a
supersession recorded and did not create, the drafts still
waiting at the gate, and what the current round has been replayed by are
re-derived on every read from the committed manifests, closure records,
experiment records, replay records, rejection records, the experiment refs, and
Git — so any clone, on any branch, and a machine that has lost `.ai-tasks/`, all
derive the same answer.

Replay evidence is the one reading with a question Git may be unable to answer:
whether the source line has moved since a run measured it needs the ref that run
integrated onto, and a clone that never fetched it holds no answer at all. That
is reported as the unanswered check it is, never as agreement — a promotion is
refused on it, because a check nobody could make is not one that passed.

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
abandon, supersede, and `conclude-no-change` — writes
several places at once: the experiment ref, a versioned record, `.ai-tasks/` and
its index, the audit ledger. Not every one of them touches all four — a seal and
a revision write a record and an audit line, because what they record is a
decision about work that has already happened, and an abandonment or a
conclusion writes one record and an audit line for the same reason. A replay
start writes only its record, and a withdrawal only takes one back: what an audit
would say about either is what the record already says, and the run's outcome is
the event there is something to audit about. They take
the same single-writer lock as import and freeze, they write in an order where
the durable record is what makes the operation real, and every step is safe to
redo, so an interrupted operation is finished by the next run rather than
repaired by hand.

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

That lock covers this package's own runs, and the two round transitions and both
terminal decisions need more than it. Each is decided from one reading of where
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
which that hold neither covers nor stands in the way of creating.

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
controller never recorded, a replay request standing anywhere but at the position
the run it becomes will take, a resumed request answered as a different one, a
conclusion or an ending taken while a request is outstanding,
a task whose completion this machine cannot observe, a second reason for a round
that is already open, a task admitted into a sealed round with
no completion observation, a draft already consumed, a draft that is not the inert
task file described above, a task id admitted twice, a file at an admitted task's
id that is not its copy, a second decision about a proposal already declined, a
second decision about an attempt that has already ended, a decision naming an
attempt the batch never had or one older than the attempt that ended, a batch
owing the successor a supersession recorded — to anything but that supersession
redone — a `no-change` conclusion over an open attempt, over one that records a
promotion, or over a gate where a proposal is still waiting, an outcome of either
kind over a batch owing a successor, a
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

Promotion updates canonical source through the ordinary reviewed workflow and
then uses `aii-2 deploy`; deployed target files are never edited directly. The
promotion revision it produces is a commit on the source line, distinct from the
candidate tip that was measured, and distinct again from the effective revision
each target reaches only when it is redeployed.
