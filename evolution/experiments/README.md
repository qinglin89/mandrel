# Evolution experiments

One directory per experiment: `<experiment-id>/experiment.json`, validated
against `../schemas/experiment.schema.json`. An experiment is one attempt at the
change a batch's analysis called for — its identity, the batch base revision it
starts from, its durable ref, its append-only rounds, and the terminal decision
that turns it into history. The normative rules are in `../README.md` (Change
lineage); this file is the layout note.

The id is `<batch-id>-exp-<NN>`, allocated one past the highest ordinal that
batch has ever used, so a name always keeps pointing at the attempt it was.
Work lives on `refs/evolution/experiments/<experiment-id>`, which only
fast-forwards: a rewritten round would leave the candidate revisions its own
record pins unreachable, and any replay evidence measured against them
describing a tree nobody can produce.

A batch may hold several experiments and usually will — abandoned, superseded,
and one open alternative is an ordinary state. All of them start from the same
base revision, frozen by the batch's first experiment, because alternatives
built on different sources are not alternatives. Only one experiment is open at
a time, and only a promotion ends the batch; the rest stay as evidence.

Rounds are the unit replay evidence names. Revising an experiment closes the
open round with its candidate revision pinned and opens the next, so evidence
gathered for an earlier round goes on naming that round rather than silently
being read as describing the current candidate.

An implementation task is not evidence of improvement; promotion depends on a
completed replay or canary record naming the base revision, the experiment, and
the round it exercised.
