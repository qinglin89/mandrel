"""The preamble every guarded evolution operation runs before it writes.

The contract's Guarded operations section says each of them settles the same
questions first — which batch is current, whether its analysis stage has ended,
whether a supersession left it owing an attempt, whether a promotion left it
owing the outcome that ends it, whether every replay record the batch holds can
still be read, which experiment is open, and whether that experiment's ref still
agrees with its record. Those questions have one set of answers and one set of
refusals, and they are here rather than in the module that happens to write
first.

Three modules write against this lineage: `experiments.py` moves an attempt
through its rounds and ends it, `replay.py` records the runs measured against a
round's pinned candidate, and `rollback.py` takes a promotion back off the
source line. Restating the preamble in any of them would give an operator
several spellings of the same refusal and let them drift; the alternative —
importing it from `experiments.py` — points the dependency the wrong way, since
a promotion has to read replay evidence before it decides.

Three things here are not readings of the lineage at all: the reason a decision
records, whether some working tree is sitting on the ref about to move, and
holding a ref where it was observed until the record saying so has landed. They
are here for the same reason as the rest — two operations ask each of them, and
an operator meets one refusal rather than one per module.

One question here is not about the lineage at all but about the *reading* an
operator acted on: `state_revision` digests the durable state, and a mutation
handed the token a reading carried refuses where the repository has moved since.
It is here for the same reason as the rest — the console publishes the token and
every guarded operation checks it, and a digest computed two ways is two tokens.

Nothing here writes. `settled` publishes this machine's analysis closures, which
is a record catching up with a task lifecycle rather than a change to the
lineage, and is exactly what the freeze does before it reads.

Each question is stated once as a *refusal*: a function returning the reason in
one line, or None where the state allows the write. The raising guards below are
wrappers over those, and the console's gate reads the same functions to derive
what may be done next (`phase.allowed_actions`). One statement rather than two is
what keeps the two answers from drifting — a gate refusing what an operation
allows withholds the verb that repairs an interruption, and one allowing what an
operation refuses offers a write that cannot happen. It is also why these texts
name no verb: they are read by every operation that runs this preamble, and a
refusal phrased for an admission is the wrong sentence over a seal.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .batches import awaiting_analysis, record_closures
from .config import EvolutionConfig
from .errors import BatchError, RefHoldError
from .lineage import BatchLineage, Experiment, Lineage
from .lineage import describe as describe_lineage
from .revisions import checked_out_refs, held_at, ref_tip


def current_cycle(
    config: EvolutionConfig,
    *,
    now: datetime,
    finishing: bool = False,
    known: Lineage | None = None,
) -> BatchLineage:
    """The batch these operations act on, settled before any of them writes.

    Five questions in one, and all of them are the derivation `status` reads
    rather than a cheaper local reading: which batch is current (invariant 14,
    from the whole lineage — an outcome record its own experiments contradict has
    concluded nothing), whether its analysis stage has ended, whether a
    supersession left the batch owing an experiment, whether a promotion left it
    owing the outcome that ends it, and what its gate and experiments currently
    are.

    `finishing` is for the one operation that may act on a batch owing a
    successor: the supersession that is being redone to create it. Every other
    operation would be building on a lineage with no attempt to build in. The
    unfinished promotion below takes no such flag, because the operation that
    finishes *it* is the one operation here that assembles this preamble itself
    (`experiments.promote`) — a batch it has concluded is one nothing reached
    through here may work in.

    `known` is for a caller that needs the whole lineage as well — the batches
    before this one, or what the release before it came to. The preamble then
    runs over that reading rather than taking a second one, so the operation's
    refusals and the work they guard describe one state of the repository rather
    than two readings a moment apart.
    """

    current = (known if known is not None else settled(config, now=now)).current
    if current is None:
        raise BatchError(no_current_batch())
    require_stage_ended(config, current)
    if not finishing:
        require_no_pending_successor(current)
    require_outcome_recorded(current)
    require_readable_evidence(config, current)
    return current


def no_current_batch() -> str:
    """Why there is nothing for any of these operations to act on."""

    return (
        "no batch is current, and every change lineage belongs to one; freeze a cohort with "
        "`aii-2 evolution start` and let its analysis produce the drafts (invariant 14)"
    )


def require_readable_evidence(config: EvolutionConfig, current: BatchLineage) -> None:
    """Every replay record this batch holds is readable, before anything writes.

    An unreadable record stops the operation — the rule this preamble applies to
    every other persisted state — and it has to be applied from here rather than
    from the module that owns the file, because the operations that would step
    around it are in the other one. Replay's own writes read that file anyway;
    the lifecycle's writes never touch it, so without this an experiment can be
    revised or ended over a record nobody can read.

    Ending it is the case that makes this more than tidiness. Evidence is
    derived for the open experiment only, so a decision recorded over an
    experiment whose `replays.json` is malformed retires the finding along with
    the attempt: the file stays on disk, saying something no reader will accept,
    and nothing reports it again. That is the same shape the ref check refuses
    for the same reason, and one persisted truth being bypassed by writing
    another is exactly what neither may allow.

    Every experiment of the batch, not only the open one — a record that has
    already been ended over is still a record this batch carries, and reading
    only the open experiment would make the state above unreachable rather than
    refused.
    """

    # Locally imported: `replay.py` runs this preamble before its own writes, so
    # the dependency between the two points that way. What is needed here is its
    # reader, and a module-level import would close the loop.
    from .replay import read_replays

    for experiment in current.experiments:
        read_replays(config, experiment)


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

    refusal = stage_refusal(current, stage_open=awaiting_analysis(config, current.batch))
    if refusal is not None:
        raise BatchError(refusal)


def stage_refusal(current: BatchLineage, *, stage_open: bool) -> str | None:
    """Why nothing acts on a batch whose analysis is still being written.

    The observation is the caller's — `batches.awaiting_analysis` on both sides,
    made once per reading, because it is answered from a machine-local task
    lifecycle and a status that asked it again could describe a stage its own
    phase label disagrees with. What is here is the rule and its words.
    """

    if not stage_open:
        return None
    return (
        f"{current.batch_id} is still in its analysis stage; drafts reach the gate when that task completes "
        f"and {current.batch.closure_path.name} records it — anything decided before then rests on "
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

    refusal = successor_refusal(current)
    if refusal is not None:
        raise BatchError(refusal)


def successor_refusal(current: BatchLineage) -> str | None:
    """Why a batch owing the attempt a supersession named has nothing to work in."""

    successor = current.pending_successor
    if successor is None:
        return None
    return (
        f"{current.experiments[-1].experiment_id} was superseded by {successor}, which does not exist; the "
        "decision landed and the attempt it creates did not, so this batch has nothing to work in — redo that "
        "supersession, for the same reason, to finish it"
    )


def require_outcome_recorded(current: BatchLineage) -> None:
    """A promotion that recorded its decision and not the outcome that ends the
    batch stops everything but its own redo.

    The supersession above, one decision along: that state is readable on purpose
    (`lineage.pending_outcome`) because the merge is already on the source line
    and refusing it in the reader would leave the promotion with no operation
    able to finish it. This is the other half of that, and it is the whole reason
    the state needs a refusal at all — with no experiment open, a batch whose
    cycle is over looks from here exactly like one between attempts, so an
    admission takes the frozen base and opens a second attempt in a batch that
    has already concluded. The redo then finds that attempt, tries to promote it
    instead, and the interrupted promotion cannot be finished until it is
    abandoned.
    """

    refusal = outcome_refusal(current)
    if refusal is not None:
        raise BatchError(refusal)


def outcome_refusal(current: BatchLineage) -> str | None:
    """Why a batch owing the outcome its promotion ends it with has nothing to
    work in."""

    promoted = current.pending_outcome
    if promoted is None:
        return None
    decision = promoted.decision
    revision = (decision.promotion_revision or "") if decision is not None else ""
    return (
        f"{promoted.experiment_id} was promoted as {revision[:12]}, so {current.batch_id} concluded by "
        "promoting it; what is outstanding is the outcome record that ends the batch, and a cycle that ended "
        "is not one to work in — redo that promotion, for the reason and the targets it was made under, to "
        "finish it"
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

    return BatchError(_no_open_experiment(current, action))


def open_experiment_refusal(current: BatchLineage, action: str) -> str | None:
    """Why the attempt an operation would act on is not there.

    Takes the verb's own words because this one is about what the operator asked
    for rather than about the lineage: "there is nothing to seal" and "there is
    nothing to promote" are different sentences, and the gate names each verb the
    way the operation it stands for does.
    """

    if current.open_experiment is not None:
        return None
    return _no_open_experiment(current, action)


def _no_open_experiment(current: BatchLineage, action: str) -> str:
    return (
        f"{current.batch_id} has no open experiment, so there is nothing to {action}; a terminal decision is "
        "never reopened, and what continues a batch is the next attempt — a grouped admission of the drafts "
        "it needs"
    )


# --- the state a reading was of ----------------------------------------------
#
# The scheme `state_revision` is computed by. A token from another scheme is a
# different thing from a lifecycle that moved, and only the version tells them
# apart — so a mutation refusing a stale expectation can say which happened.
STATE_REVISION_VERSION = 1
_REVISION_DIGITS = 16

# The commit an operation resolves when nothing on record names one. Exactly one
# does — the grouped admission that freezes a batch's first base takes `HEAD`
# where the operator names no revision (`experiments._base_revision`) — and it is
# in play whenever that admission is, which is before any record of this batch
# exists to name it. A checkout moved between the reading and the write is
# therefore a different base than the one the reading was taken against, and the
# cost of covering it is a token that stops being current when the operator
# switches branch: the safe direction, since the alternative is freezing a base
# nobody read.
_HEAD = "HEAD"

# What a durable record calls a ref. Everything else in these records is a
# revision — `reverted_from` and the pinned candidates included — and a revision
# is covered by the bytes that state it.
_REF_FIELDS = frozenset({"ref", "base_release_ref", "merge_input_ref", "source_ref"})


@dataclass(frozen=True)
class Inputs:
    """Everything `state_revision` is a digest of, read in one pass.

    A value rather than a digest so it can be compared with the same reading
    taken a moment later: the token says *what* the state is, and two of these
    say whether it was one state throughout.
    """

    # Every durable record, by repository-relative path and content hash.
    records: tuple[tuple[str, str], ...]
    # Where each ref in play stands, or None where this checkout does not hold it.
    tips: tuple[tuple[str, str | None], ...]


def state_revision(inputs: Inputs) -> str:
    """A digest of the durable state the lifecycle was derived from.

    What it is for: a mutation handed the revision an operator saw can refuse
    rather than act on a lifecycle another writer moved in between. So what it
    covers is what a writer changes — the versioned records under `evolution/`,
    the policy that reads them, the machine's pending pool, and the tips of the
    refs in play — and not what a reading merely mentions.

    Three things are deliberately outside it. The clock: `waited_days` and the
    freeze it releases move on their own, and a token that expired overnight
    would refuse operations over a repository nobody wrote to. `.ai-tasks/`: a
    task finishing is this machine's own work rather than a competing writer's,
    and every operation re-reads it under the lock. The audit ledger: it is not
    flow state, and a line appended beside a record already covered here would
    move the revision twice for one operation.

    Truncated on purpose. This detects a lifecycle that moved between a read and
    a write — the writer re-derives every refusal under the lock regardless — so
    what it needs is to be short enough to pass on a command line.
    """

    digest = hashlib.sha256()
    digest.update(f"{STATE_REVISION_VERSION}\n".encode())
    for path, content in inputs.records:
        digest.update(f"{path}={content}\n".encode())
    for ref, tip in inputs.tips:
        digest.update(f"{ref}={tip or ''}\n".encode())
    return f"{STATE_REVISION_VERSION}-{digest.hexdigest()[:_REVISION_DIGITS]}"


def read_inputs(config: EvolutionConfig) -> Inputs:
    """Read everything the token is over, independently of any reading.

    Independent on purpose, and that is the whole shape of this: the console
    takes it before and after the reading it accompanies, so a ref set derived
    from that reading could not bracket it. The refs therefore come from the
    records — every ref a durable record names is a line this lifecycle stands
    on — rather than from what a reading made of them, which also covers the ones
    no reading mentions.
    """

    records: list[tuple[str, str]] = []
    refs = {_HEAD}
    for path in _authoritative_paths(config):
        try:
            data = path.read_bytes()
        except OSError:
            # A file that went away under the walk. Left out rather than raised:
            # it reads as a difference between the two brackets, which is what
            # the console's retry is for, and a record genuinely gone is a state
            # the reading itself reports.
            continue
        records.append((path.relative_to(config.repo_root).as_posix(), hashlib.sha256(data).hexdigest()))
        refs |= _refs_named(data)
    return Inputs(
        records=tuple(records),
        tips=tuple((ref, ref_tip(config.repo_root, ref)) for ref in sorted(refs)),
    )


def require_expected(config: EvolutionConfig, expected: str | None) -> None:
    """The lifecycle still stands where the caller's reading left it.

    Asked under the single-writer lock and before anything else, because that is
    the only place the answer holds until the write: outside it the token would
    be compared against a state another writer is free to move before this one
    records anything. First, too, because the preamble below publishes this
    machine's analysis closures — a write, and one that would change the very
    digest being compared.
    """

    refusal = expectation_refusal(config, expected)
    if refusal is not None:
        raise BatchError(refusal)


def expectation_refusal(config: EvolutionConfig, expected: str | None) -> str | None:
    """Why the state a caller read is not the state this operation would act on.

    None where no expectation was given, which is the ordinary terminal path: an
    operator reading the status and typing the next verb is their own single
    writer. A surface acting on a reading somebody else may have moved — a Web
    adapter, orch-hub, a script resuming — passes the token, and this is what
    makes that safe.

    A token from another scheme is refused rather than compared, and said so:
    "this does not describe this repository" and "this repository moved" are
    different facts, and a caller that reads them as one will retry the wrong
    thing forever.
    """

    if expected is None:
        return None
    token = expected.strip()
    version, separator, digest = token.partition("-")
    if not separator or version != str(STATE_REVISION_VERSION) or not digest:
        return (
            f"'{expected}' is not a state revision this build computes; the scheme is "
            f"{STATE_REVISION_VERSION}-<digest>, and a token from another one says nothing about whether this "
            "lifecycle moved — so it is refused rather than compared. Take the current one from the `state` line "
            "of `aii-2 evolution status` in this checkout"
        )
    current = state_revision(read_inputs(config))
    if token == current:
        return None
    return (
        f"the evolution records moved since {token} was read: they now stand at {current}. This verb was asked "
        "for the state that token describes, and what it would act on is a different one — read the lifecycle "
        "again with `aii-2 evolution status` and decide over what is there now"
    )


def _authoritative_paths(config: EvolutionConfig) -> list[Path]:
    """Every file the lifecycle is derived from, in one order.

    Walked rather than listed, so a record this workspace grows later is covered
    by being written rather than by somebody remembering to add it here.
    """

    known = [config.path, config.state_path]
    for root in (config.batches_root, config.experiments_root):
        known.extend(root.rglob("*"))
    return sorted(path for path in known if path.is_file())


def _refs_named(data: bytes) -> set[str]:
    """Every ref this record names, wherever it names it.

    Walked rather than read field by field for the reason the paths above are:
    a record that grows another integration, another line, or another nesting is
    covered by stating it in one of these fields rather than by somebody
    remembering this function. A file that is not a record parses as nothing and
    names none — drafts are bytes at the admission gate, and their bytes are
    already covered.
    """

    try:
        record = json.loads(data)
    except (UnicodeDecodeError, ValueError):
        return set()
    found: set[str] = set()
    _collect_refs(record, found)
    return found


def _collect_refs(node: Any, found: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _REF_FIELDS and isinstance(value, str) and value.startswith("refs/"):
                found.add(value)
            else:
                _collect_refs(value, found)
    elif isinstance(node, list):
        for item in node:
            _collect_refs(item, found)


def reason(text: str, requirement: str) -> str:
    """The human reason a decision records, as one line.

    Collapsed because it travels in a versioned record and is compared there: a
    redo recognising its own interrupted work matches the reason it wrote, and
    two spellings differing only in how they were wrapped would read as two
    different decisions.
    """

    collapsed = " ".join((text or "").split())
    if not collapsed:
        raise BatchError(requirement)
    return collapsed


def require_line_not_checked_out(config: EvolutionConfig, ref: str, action: str) -> None:
    """No working tree of this repository is sitting on the line about to move.

    Moving a ref touches no working tree, which is what lets these operations run
    in a checkout busy with unrelated work. That is only true while the ref is
    nobody's: moving a branch some work tree is on leaves that tree and its index
    describing the commit before, so everything the move changed reads there as
    an edit nobody made — and the obvious repair takes whatever else was
    uncommitted there with it.

    Every worktree, not this process's own `HEAD`. A linked worktree
    (`git worktree add`) holds a branch this checkout never looks at, and
    `update-ref` moves it there without a word — leaving a directory the operator
    may not have thought about in exactly the state above. So the question is put
    to Git once, and a Git that cannot answer it refuses: this is the only thing
    standing between the move and somebody's working tree.

    Refused rather than repaired, because the repair is a checkout and that is a
    far larger promise than either of these operations makes. It is also the one
    refusal here an operator answers without deciding anything: run it from a
    clone that is not on the release line.
    """

    checked_out = checked_out_refs(config.repo_root)
    if checked_out is None:
        raise BatchError(
            f"whether {ref} is checked out anywhere cannot be answered in {config.repo_root}, and {action} "
            "moves it without touching a working tree; a work tree sitting on that branch would be left "
            "describing the commit before, so this refuses rather than move a ref it cannot say is free"
        )
    directory = checked_out.get(ref)
    if directory is None:
        return
    raise BatchError(
        f"{ref} is checked out at {directory}, and {action} moves it without touching a working tree; that "
        "tree and its index would go on describing the commit before, showing what the move changed as an edit "
        f"nobody made — run {action} from a checkout that is not on the source line"
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

    refusal = ref_refusal(current)
    if refusal is not None:
        raise BatchError(refusal)


def ref_refusal(current: BatchLineage) -> str | None:
    """Why a ref standing off the history its record pins takes no further work."""

    ref = current.ref
    if ref is None or ref.consistent is not False:
        return None
    if ref.chain_break is not None:
        earlier, later = ref.chain_break
        return (
            f"{ref.ref}: {later[:12]} does not descend from {earlier[:12]}, which this experiment's record pins "
            "before it; rounds only add (invariant 15), so a candidate off that history leaves the revisions the "
            "record names unreachable — resolve the ref before anything else is recorded onto it"
        )
    return (
        f"{ref.ref} stands at {(ref.tip or 'nothing')[:12]}, not on the history of the {ref.pinned[:12]} its "
        f"record pins ({ref.state}); the ref only fast-forwards, and work admitted onto it now would be measured "
        "as part of a candidate nobody can identify"
    )


@contextmanager
def held(
    config: EvolutionConfig,
    ref: str,
    revision: str | None,
    subject: str,
    requirement: str,
) -> Iterator[None]:
    """Any ref, held where it was observed, for the length of the body.

    A read of a ref answers where it stood, which stops being true the moment the
    reading is over. Where that answer is about to be *recorded*, the record has
    to be written while the answer still holds, and no lock this package takes
    can arrange that: what moves a ref is ordinary Git — a fetch, a push, an
    operator's own `update-ref` — which the single-writer lock has never covered.
    Git's own lock is the only thing that spans the gap (`revisions.held_at`),
    and it is taken from here rather than from either writer because both of them
    would otherwise spell the same refusal.

    Two refs are held this way, for the same reason and about different claims.
    An experiment's ref is held while a seal, a revision, or a terminal decision
    is recorded (`experiments._unmoved`). The source line is held while a
    rollback records that the line carries its inverse commit — the moment
    `promotion_effective` is read from afterwards, and a claim about where the
    line stands rather than about a commit that exists. A promotion's own move is
    deliberately not held this way: what it records is that a commit was made and
    put on the line, which a later advance leaves true.
    """

    holding = ExitStack()
    try:
        holding.enter_context(held_at(config.repo_root, ref, revision))
    except RefHoldError as exc:
        raise BatchError(
            f"{subject}: {exc}; {requirement}, so it is recorded while the ref is held there "
            "rather than from a reading that may already be stale — read the ref as it now stands and decide "
            "again"
        ) from exc
    with holding:
        yield
