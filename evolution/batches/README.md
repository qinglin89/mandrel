# Evolution batches

Each `<batch-id>/manifest.json` is an immutable, schema-validated membership
snapshot created before analysis. `findings.md` records every cluster's
disposition and is the committed signal that the analysis concluded.
`proposed-tasks/` holds that analysis's change-task drafts until a human admits
one into `.ai-tasks/`.

A batch stays open until its analysis task **completes** and its findings are
recorded. Findings drafted while that task is still pending, in progress, or in
final review do not close it: on a machine holding the task, its lifecycle
status is read alongside `findings.md`, so an unfinished analysis cannot release
a second batch. Where the task files are absent — any fresh clone, since
`.ai-tasks/` is machine-local and ignored — `findings.md` alone is the reading,
so closure travels with the repository.

Manifests are versioned and never rewritten, so each version keeps the schema it
was written against: `batch-manifest-v1.schema.json` for version 1, and
`batch-manifest.schema.json` for the current version, which adds the per-report
evaluator and provenance blocks cohort coherence needs. New batches are written
at the current version; older ones stay readable as frozen.

Never edit a frozen manifest to add late reports; create a later batch.
