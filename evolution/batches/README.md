# Evolution batches

Each `<batch-id>/manifest.json` is an immutable, schema-validated membership
snapshot created before analysis. `findings.md` records every cluster's
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
run, and reading it as "finished" let draft findings release the next cohort
from any fresh checkout. A machine that holds an unfinished task keeps the
analysis stage open even against the record — local lifecycle may only be more
conservative than the committed answer, never less.

Manifests are versioned and never rewritten, so each version keeps the schema it
was written against: `batch-manifest-v1.schema.json` for version 1, and
`batch-manifest.schema.json` for the current version, which adds the per-report
evaluator and provenance blocks cohort coherence needs. New batches are written
at the current version; older ones stay readable as frozen.

Never edit a frozen manifest to add late reports; create a later batch.
