# Evolution batches

Each `<batch-id>/manifest.json` is an immutable, schema-validated membership
snapshot created before analysis. `findings.md` is added only by the completed
batch-analysis task and records every cluster's disposition; its presence is
what marks the batch closed. `proposed-tasks/` holds that analysis's change-task
drafts until a human admits one into `.ai-tasks/`.

Never edit a frozen manifest to add late reports; create a later batch.
