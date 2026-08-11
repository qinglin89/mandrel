"""The preamble every guarded evolution operation runs before it writes.

The contract's Guarded operations section says each of them settles the same
questions first — which batch is current, whether its analysis stage has ended,
whether a supersession left it owing an attempt, which experiment is open, and
whether that experiment's ref still agrees with its record. Those questions have
one set of answers and one set of refusals, and they are here rather than in the
module that happens to write first.

Two modules write against this lineage: `experiments.py` moves an attempt
through its rounds and ends it, and `replay.py` records the runs measured
against a round's pinned candidate. Restating the preamble in the second one
would give an operator two spellings of the same refusal and let them drift; the
alternative — importing it from `experiments.py` — points the dependency the
wrong way, since a promotion has to read replay evidence before it decides.

Nothing here writes. `settled` publishes this machine's analysis closures, which
is a record catching up with a task lifecycle rather than a change to the
lineage, and is exactly what the freeze does before it reads.
"""

from __future__ import annotations

from datetime import datetime

from .batches import awaiting_analysis, record_closures
from .config import EvolutionConfig
from .errors import BatchError
from .lineage import BatchLineage, Experiment, Lineage
from .lineage import describe as describe_lineage


def current_cycle(config: EvolutionConfig, *, now: datetime, finishing: bool = False) -> BatchLineage:
    """The batch these operations act on, settled before any of them writes.

    Four questions in one, and all of them are the derivation `status` reads
    rather than a cheaper local reading: which batch is current (invariant 14,
    from the whole lineage — an outcome record its own experiments contradict has
    concluded nothing), whether its analysis stage has ended, whether a
    supersession left the batch owing an experiment, and what its gate and
    experiments currently are.

    `finishing` is for the one operation that may act on a batch owing a
    successor: the supersession that is being redone to create it. Every other
    operation would be building on a lineage with no attempt to build in.
    """

    current = settled(config, now=now).current
    if current is None:
        raise BatchError(
            "no batch is current, so there is no admission gate to act on; freeze a cohort with "
            "`aii-2 evolution start` and let its analysis produce the drafts (invariant 14)"
        )
    require_stage_ended(config, current)
    if not finishing:
        require_no_pending_successor(current)
    return current


def settled(config: EvolutionConfig, *, now: datetime) -> Lineage:
    """The whole lineage, with this machine's analysis closures published first.

    The closure records are published first for the same reason the freeze
    publishes them first: the stage's end is read from the analysis task's own
    lifecycle on the machine that has it, and from the committed record
    everywhere else. Admitting a draft before that stage ends would implement
    dispositions that are still being written — and concluding a batch before it
    would end a cohort whose analysis is still being written.
    """

    record_closures(config, now=now)
    return describe_lineage(config)


def require_stage_ended(config: EvolutionConfig, current: BatchLineage) -> None:
    """The batch's analysis stage is over before anything acts on its lineage."""

    if awaiting_analysis(config, current.batch):
        raise BatchError(
            f"{current.batch_id} is still in its analysis stage; drafts reach the gate when that task completes "
            f"and {current.batch.closure_path.name} records it — a proposal admitted before then implements "
            "dispositions nobody has reviewed (invariant 6)"
        )


def require_no_pending_successor(current: BatchLineage) -> None:
    """A supersession that recorded its decision and not the successor it names
    stops everything but its own redo.

    That state is readable on purpose (`lineage.pending_successor`): refusing it
    in the reader would leave the interruption unrecoverable, since the operation
    that finishes it could not run either. So the refusal is here, where an
    operation is about to build on a batch whose only attempt is one that does
    not exist yet.
    """

    successor = current.pending_successor
    if successor is None:
        return
    raise BatchError(
        f"{current.experiments[-1].experiment_id} was superseded by {successor}, which does not exist; the "
        "decision landed and the attempt it creates did not, so this batch has nothing to work in — redo that "
        "supersession, for the same reason, to finish it"
    )


def require_open_experiment(current: BatchLineage, action: str) -> Experiment:
    """The attempt these operations act on: the one experiment still open."""

    experiment = current.open_experiment
    if experiment is None:
        raise no_open_experiment(current, action)
    return experiment


def no_open_experiment(current: BatchLineage, action: str) -> BatchError:
    """Why there is nothing to act on, in one place.

    Every operation on an experiment reaches this, and each of them reaches it
    from two directions: nothing has been admitted into this batch yet, or the
    last attempt ended. The answer is the same either way, and it is not "try
    again" — a terminal decision is never reopened.
    """

    return BatchError(
        f"{current.batch_id} has no open experiment, so there is nothing to {action}; a terminal decision is "
        "never reopened, and what continues a batch is the next attempt — a grouped admission of the drafts "
        "it needs"
    )


def require_consistent_ref(current: BatchLineage) -> None:
    """The open experiment's ref agrees with what its record pins, or nothing
    else is admitted into it.

    The reader deliberately reports a ref disagreement as data rather than
    raising — a status that refused to describe the lifecycle is not how an
    operator learns which ref moved. This is the other half of that decision: an
    operation that writes stops here. Admitting work onto a ref standing off the
    history the record pins would put that work on a tree the record cannot
    identify, and the round's later seal would pin it.

    "Cannot tell" is not a refusal. `refs/evolution/experiments/*` is outside the
    default fetch refspec, so a clone that never fetched the namespace has no
    ref and no answer — the ordinary state everywhere but the machine doing the
    work, and one that says nothing about the lineage.
    """

    ref = current.ref
    if ref is None or ref.consistent is not False:
        return
    if ref.chain_break is not None:
        earlier, later = ref.chain_break
        raise BatchError(
            f"{ref.ref}: {later[:12]} does not descend from {earlier[:12]}, which this experiment's record pins "
            "before it; rounds only add (invariant 15), so a candidate off that history leaves the revisions the "
            "record names unreachable — resolve the ref before admitting anything else into it"
        )
    raise BatchError(
        f"{ref.ref} stands at {(ref.tip or 'nothing')[:12]}, not on the history of the {ref.pinned[:12]} its "
        f"record pins ({ref.state}); the ref only fast-forwards, and work admitted onto it now would be measured "
        "as part of a candidate nobody can identify"
    )
