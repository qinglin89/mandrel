# Evolution batches

Each `<batch-id>/manifest.json` is an immutable, schema-validated membership
snapshot created before analysis, and it is the *first* durable write of the
batch's formation: `start` offers a batch in ignored runtime state
(`.ai-evolution/batch-proposal.json`) and `freeze` fixes that offer here, so
every human adjustment to membership happens before this file exists. `findings.md` records every cluster's
disposition. `analysis-complete.json` records that the analysis task finished,
and is what ends the analysis stage. `proposed-tasks/<draft-id>.md` holds that
analysis's change-task drafts; admission copies a draft into `.ai-tasks/` and
leaves it here, so what was proposed stays readable after the admitted task is
archived away. `rejected-drafts.json` records the ones a human declined instead,
which is what keeps a declined proposal from waiting at the gate forever.
`outcome.json` records the batch's terminal outcome — `promoted` or `no-change`
— and is what ends the batch.

The two records answer different questions and are not interchangeable. The
analysis stage ends when dispositions exist; the batch ends when its whole
change cycle does. Admission, experiments, and their rounds all happen inside a
batch that is still current, and no later batch may freeze until the outcome is
recorded (contract invariant 14).

The analysis stage ends only when its task **completes** and its findings are
recorded. Findings drafted while that task is still pending, in progress, or in
final review do not end it — the task writes them long before its review gate
ends. Completion is read from the task's own lifecycle status, on the one
machine that has the task; the controller then publishes
`analysis-complete.json`, which is committed and so answers for every other
clone. Task *absence* is never read as completion: `.ai-tasks/` is machine-local
and ignored, so absence is the ordinary state everywhere the analysis did not
run, and reading it as "finished" let draft findings release the next batch
from any fresh checkout. A machine that holds an unfinished task keeps the
analysis stage open even against the record — local lifecycle may only be more
conservative than the committed answer, never less.

A manifest states its own denominator as well as its membership. `selection`
records how many unique completed tasks the formation answered for, how the
members split between the identity test and human judgement, and why each
considered task that is not a member was left out; each report restates its own
task's answer. A member's evidence is one pack, so a batch frozen here has one
report per member — and where a manifest holds several for a task anyway, the
reader requires the same answer on all of them. That is what makes a batch's
recurrence claim checkable from committed content: the pool it was selected out
of is ignored machine-local state and has moved by the time anyone reads the
batch.

Those are cross-field facts, and JSON Schema can only constrain one field at a
time — so the reader checks the agreement itself and refuses a manifest whose
counts no set of reports could produce, which answers one task two ways, which
both selects and excludes a task, or which records a human move nobody is named
for. The freeze publishes through that same reader, because a manifest is
immutable from the moment it is written: a contradiction caught only on the next
read is one that can no longer be repaired.

Manifests are versioned and never rewritten, so once a batch exists its version
keeps the schema it was written against. Only `batch-manifest.schema.json`
(version 2) is read today: it carries the per-report evaluator and provenance
blocks the analysis reads invariant 5's evaluator limbs off, the base the batch
was formed at, and the selection above. Version 1 was dropped with its schema
rather than kept as a reader arm, because no batch has ever been frozen here —
that window closes at the first freeze.

Never edit a frozen manifest to add late reports; create a later batch.
