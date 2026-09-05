"""Failures the evolution controller can raise, and what each one asks for.

One hierarchy, so a CLI adapter catches `EvolutionError` and turns any of them
into a single bounded, actionable message. Two attributes on top of that, so a
caller can tell them apart without reading the sentence:

- `kind` is the family — what an operator does next. A refusal is answered by
  changing the state, a stale reading by taking it again, a corrupt record by
  repairing this machine, a busy lock by waiting. Callers that *route* rather
  than report key on this, and it is the level the process exit codes are drawn
  at (`mandrel.cli.EVOLUTION_EXIT_CODES`).
- `code` is the class — which of the family's failures this is. Unique across
  the hierarchy and stable across rewordings, so a record or an assertion
  naming one still names it after the sentence is rewritten.

Both are class attributes rather than constructor arguments: a failure's family
is a property of what went wrong, not of the site that noticed it, and a raiser
free to pass its own would make the taxonomy a convention instead of a type.

What this exists to prevent is classification by message text. The messages here
are written for a person about to act, which means they get rewritten as the
operations they explain get sharper; anything keying on their words breaks
silently when they do, and quietly reclassifies a failure when a sentence
happens to gain a word. `kind` and `code` are the two things that do not move.
"""

from __future__ import annotations

# The families, by what the operator does about one.
#
# Drawn at the level a *response* differs rather than at the level a cause does:
# two failures with different causes and one next step are one family, and one
# cause an operator answers two ways is two. That is why the exit codes are
# taken from here — a shell branching on the number is choosing what to do.
KIND_INVALID = "invalid"
"""What the caller supplied is not usable. Fix the arguments and run it again."""

KIND_REFUSED = "refused"
"""A legal operation the current state does not permit. The message names what
stands in the way; the state changes, or the operator does something else."""

KIND_STALE = "stale"
"""What this acted on is not the reading that stands. Take the reading again
and decide over what is there now — the redo is the recovery, not a repair."""

KIND_BUSY = "busy"
"""Another writer holds the single-writer lock. Wait, or clear a lock whose
holder is gone."""

KIND_CORRUPT = "corrupt"
"""A persisted record this machine owns cannot be read, or is a version this
build has no reader for. Never repaired silently; the file is restored or
removed by a human who has looked at it."""

KIND_UNAVAILABLE = "unavailable"
"""Something outside this checkout did not answer. Nothing was established, so
the run stops rather than recording an empty reading of it."""

KIND_MISCONFIGURED = "misconfigured"
"""The versioned policy this workspace is configured by is missing, malformed,
or asks for behavior this build does not implement. Fix `evolution/`."""

KIND_TOOLCHAIN = "toolchain"
"""A tool an operation needs is absent or too old here. A fact about this
machine and about no revision, which is exactly what a refusal would hide."""

KIND_FAILED = "failed"
"""An operation's own write did not complete and nothing above says why. The
catch-all, and deliberately the family with the least to say: a failure landing
here that recurs is one of the others waiting to be named."""

KIND_UNANSWERED = "unanswered"
"""The third response family, and the one that is not a failure. A durable
write already happened — a request is on record — and what is missing is the
half only a person can state. Neither success nor failure, so it is neither
reported as one nor exited as one."""

ERROR_KINDS: tuple[str, ...] = (
    KIND_INVALID,
    KIND_REFUSED,
    KIND_STALE,
    KIND_BUSY,
    KIND_CORRUPT,
    KIND_UNAVAILABLE,
    KIND_MISCONFIGURED,
    KIND_TOOLCHAIN,
    KIND_FAILED,
    KIND_UNANSWERED,
)


class EvolutionError(RuntimeError):
    """Base class for every evolution controller failure.

    Raised directly where an operation's own filesystem work did not complete —
    a generated task that cannot be read or written — which is what `failed`
    names. Its attributes are the defaults every subclass narrows.
    """

    kind: str = KIND_FAILED
    code: str = "operation-failed"


class ConfigError(EvolutionError):
    """`evolution/config.toml` is missing, malformed, or asks for behavior this
    implementation does not support."""

    kind = KIND_MISCONFIGURED
    code = "config-unusable"


class SchemaError(EvolutionError):
    """A versioned schema under `evolution/schemas/` cannot be read, or uses a
    JSON Schema keyword or pattern construct this validator does not
    implement."""

    kind = KIND_MISCONFIGURED
    code = "schema-unusable"


class ValidationError(EvolutionError):
    """A value does not satisfy its versioned schema.

    `invalid` rather than `corrupt` because the validator is asked in both
    directions and the value is the subject either way: a record about to be
    written that the schema will not take is the caller's, and a persisted one
    that no longer validates is read through the parser that owns its file,
    which says whose record it is (`StateError`, `ProposalError`, `RecordError`).
    A reader that does not translate reports a file on this machine as something
    the operator typed, which is the one mistake this split exists to prevent.
    """

    kind = KIND_INVALID
    code = "value-invalid"


class InputError(EvolutionError):
    """What a caller stated is not something this operation can read.

    A number where the argument holds a number, a token in this build's scheme.
    Separate from `BatchError` because the two ask opposite things: a refusal
    describes a state and is answered by changing it, while this describes the
    command and is answered by retyping it — and an operator told to change the
    lifecycle over a mistyped figure changes the wrong thing.
    """

    kind = KIND_INVALID
    code = "input-invalid"


class StateError(EvolutionError):
    """`.ai-evolution/state.json` is unreadable or malformed. Never repaired
    silently: a reset cursor would re-import, a dropped pool would lose
    evidence."""

    kind = KIND_CORRUPT
    code = "runtime-state-corrupt"


class StaleStateError(EvolutionError):
    """The lifecycle moved since the caller read it.

    Its own class rather than a refusal because of what a caller does with it.
    Every other refusal is about the lifecycle and is answered by changing it;
    this one is about the *reading* — the records are fine and the state a verb
    was asked for is simply not the state it would act on — and the answer is to
    read again and decide over what is there now. A surface that retried this as
    a refusal would keep asking for a state that has already gone.
    """

    kind = KIND_STALE
    code = "reading-moved"


class ProposalError(EvolutionError):
    """`.ai-evolution/batch-proposal.json` is unreadable, malformed, or no
    longer describes the pool and the source line it was built against.

    Separate from `StateError` because the two absences mean opposite things:
    losing the pool loses evidence, while losing a proposal loses a reading
    that the next `start` rebuilds. Nothing durable stands on it, so a bad one
    is refused and rebuilt rather than repaired — which is the `stale` family's
    recovery and not the corrupt one's, whatever damaged the file.
    """

    kind = KIND_STALE
    code = "proposal-unusable"


class RecordError(EvolutionError):
    """A durable record under `evolution/` cannot be read as what it claims to be.

    The read side of the file the writers publish through. Those parsers are
    asked in both directions, so what they raise about a value is `invalid` —
    correct for a caller handing in the next state of a record, and wrong for
    the same parser reading one off disk, where nothing was typed and the value
    is a file this machine is holding. Whoever reads owns that translation, and
    this is what it translates to: the record's own family, whose recovery is a
    person looking at the file rather than an operator retyping a command.
    """

    kind = KIND_CORRUPT
    code = "record-corrupt"


class LedgerError(EvolutionError):
    """`evolution/ledger.jsonl` cannot be appended to safely."""

    kind = KIND_CORRUPT
    code = "ledger-corrupt"


class LockError(EvolutionError):
    """The single-writer lock is held by someone else."""

    kind = KIND_BUSY
    code = "writer-busy"


class FeedError(EvolutionError):
    """The report feed is unavailable or returned an unusable page."""

    kind = KIND_UNAVAILABLE
    code = "feed-unavailable"


class ToolchainError(EvolutionError):
    """A tool this operation needs is not usable here — in practice a Git older
    than the floor `revisions.py` states.

    Its own family for the reason `MergeAnswer` is three-valued: "these do not
    integrate" is a fact about the revisions, true in every checkout that holds
    them, and "this Git cannot compute it" is a fact about this machine. Read as
    a refusal, the second retires a candidate nobody measured — so it is raised
    as what it is, and the message names the version that would answer.
    """

    kind = KIND_TOOLCHAIN
    code = "git-unusable"


class RefHoldError(EvolutionError):
    """A ref cannot be held where it was observed, so an operation about to
    record something about it has nothing to record from — an experiment ref a
    transition is decided over, or the source line a rollback is recording as
    carrying its inverse.

    Raised at the Git boundary and translated by the operation, which is what
    knows why that ref had to stay still. Its own kind is therefore what a
    caller reaching past that translation sees: an operational write that could
    not proceed, rather than the refusal `guards.held` turns it into.
    """

    kind = KIND_FAILED
    code = "ref-unheld"


class BatchError(EvolutionError):
    """A batch cannot be read or frozen: a manifest under `evolution/batches/`
    contradicts itself or the pool, an id is already taken, or the requested
    freeze would break an admission invariant.

    The whole legal-refusal family under one code. What each refusal is about is
    in its sentence and not yet in an attribute, which is the next thing this
    taxonomy owes a machine caller.
    """

    kind = KIND_REFUSED
    code = "state-refused"
