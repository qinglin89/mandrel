# Evolution experiments

One directory per experiment: `<experiment-id>/experiment.json`, validated
against `../schemas/experiment.schema.json`. An experiment is one attempt at the
change a batch's analysis called for — its identity, the batch base revision it
starts from, its durable ref, its append-only rounds, and the terminal decision
that turns it into history. The normative rules are in `../README.md` (Change
lineage); this file is the layout note.

The id is `<batch-id>-exp-<NN>`, allocated one past the highest ordinal that
batch has ever used, so a name always keeps pointing at the attempt it was. The
ordinal is positive and zero-padded to two digits — `01`, `02`, … `99`, `100` —
so one attempt has one spelling. Work lives on
`refs/evolution/experiments/<experiment-id>`, which only fast-forwards: a
rewritten round would leave the candidate revisions its own record pins
unreachable, and any replay evidence measured against them describing a tree
nobody can produce. Reading the lineage back checks that whole chain — the base
reaching the first sealed candidate, each sealed candidate the next, and the ref
tip at or ahead of the last — since the tip against the latest pin alone would
accept an attempt built on a history the batch never froze.

The record is written by the controller, not by hand: one grouped admission
creates the ref at the batch's base, this record with its first round, and one
`.ai-tasks/` copy per admitted draft, recording the draft id, the sha256 of the
bytes admitted, and the task id the copy took. Unlike a frozen batch manifest it
is rewritten as the attempt proceeds — a round's admitted tasks, its seal, the
terminal decision — and what it already says is never rewritten.

A batch may hold several experiments and usually will — abandoned, superseded,
and one open alternative is an ordinary state. All of them start from the same
base revision, frozen by the batch's first experiment, because alternatives
built on different sources are not alternatives. Only one experiment is open at
a time, and only a promotion ends the batch; the rest stay as evidence.

Rounds are the unit replay evidence names. Sealing a round records that every
task admitted into it was observed complete and pins the ref tip as that round's
candidate revision — the round is then candidate-ready, and that pinned revision
is the only thing replay or a promotion may name. Revising opens the next round
from an already-sealed one, so evidence gathered for an earlier round goes on
naming that round rather than silently being read as describing the current
candidate. A revised round opens with nothing admitted into it and is filled by
a later admission, so work resumes as soon as the revision is decided rather
than when the next proposals happen to be written. Both transitions are recorded
while the ref is held where they read it — a commit arriving in between would be
pinned unasked or counted as the new round's work, and this record is the only
thing that could have said otherwise.

An implementation task is not evidence of improvement; promotion depends on a
completed replay or canary record naming the base revision, the experiment, and
the round it exercised.
