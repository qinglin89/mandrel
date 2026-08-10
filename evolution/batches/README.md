# Evolution batches

Each `<batch-id>/manifest.json` is an immutable, schema-validated membership
snapshot created before analysis. `findings.md` records every cluster's
disposition. `analysis-complete.json` records that the analysis task finished,
and is what closes the batch. `proposed-tasks/` holds that analysis's
change-task drafts until a human admits one into `.ai-tasks/`.

A batch stays open until its analysis task **completes** and its findings are
recorded. Findings drafted while that task is still pending, in progress, or in
final review do not close it — the task writes them long before its review gate
ends. Completion is read from the task's own lifecycle status, on the one
machine that has the task; the controller then publishes
`analysis-complete.json`, which is committed and so answers for every other
clone. Task *absence* is never read as completion: `.ai-tasks/` is machine-local
and ignored, so absence is the ordinary state everywhere the analysis did not
run, and reading it as "finished" let draft findings release the next cohort
from any fresh checkout. A machine that holds an unfinished task keeps the batch
open even against the record — local lifecycle may only be more conservative
than the committed answer, never less.

Manifests are versioned and never rewritten, so each version keeps the schema it
was written against: `batch-manifest-v1.schema.json` for version 1, and
`batch-manifest.schema.json` for the current version, which adds the per-report
evaluator and provenance blocks cohort coherence needs. New batches are written
at the current version; older ones stay readable as frozen.

Never edit a frozen manifest to add late reports; create a later batch.
