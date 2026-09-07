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
"""A persisted record cannot be read as what it claims to be, or is a version
this build has no reader for — this machine's runtime state, or a record the
repository carries. Never repaired silently; the file is restored or removed by
a human who has looked at it."""

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
    a generated task this machine cannot read back — which is what `failed`
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

    A number where the argument holds a number, a token in this build's scheme,
    a selection no state could make admissible — a draft id that is not a
    kebab-case slug, a name given twice where the record holds it once, nothing
    named at all.
    Separate from `BatchError` because the two ask opposite things: a refusal
    describes a state and is answered by changing it, while this describes the
    command and is answered by retyping it — and an operator told to change the
    lifecycle over a mistyped figure changes the wrong thing.

    The test is whether any state would make the value legal, not whether the
    operation reached one: a shape check that runs before anything is read is
    still about the command, and `UnknownSubject` is where a well-formed value
    the state simply does not hold belongs.
    """

    kind = KIND_INVALID
    code = "input-invalid"


class StateError(EvolutionError):
    """This machine's runtime pool is damaged — `.ai-evolution/state.json`, or a
    staged bundle it names as the evidence for a pooled report.

    One code for both because they are one record: the state file is a table of
    pointers and hashes, and the bundles are what it points at, so a bundle that
    is gone, changed, or disagrees with the entry naming it is the same loss as a
    hole in the file itself. Never repaired silently — a reset cursor would
    re-import, a dropped pool would lose evidence — and never restored from
    version control either, because `.ai-evolution/` is ignored: what answers it
    is importing the reports again.
    """

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
    """A record this controller wrote and reads back as fact cannot be read as
    what it claims to be — the committed batch, experiment, replay, and
    release-assessment records under `evolution/`, and the generated analysis
    task a freeze reads a lifecycle from.

    The read side of the files the writers publish through. Those parsers are
    asked in both directions, so what they raise is written for the writer: the
    shared validator says `invalid` about a value, and a parser's own
    cross-field rules refuse the document. Both are right for a caller handing
    in the next state of a record and wrong for the same parser reading one off
    disk, where nothing was typed and no state changes to make the file load.
    Whoever reads owns that translation, and this is what it translates to: the
    record's own family, whose recovery is a person looking at the file rather
    than an operator retyping a command or running another verb.

    The translation is the reader's and not the parser's because only the reader
    knows which direction it is: at a rule inside a shared parser, a record
    handed in and a record read off disk are the same mapping.
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

    Also the base of the refusals below, which say what stands in the way. A
    caller that needs only to know the state said no catches this; one deciding
    what to run next reads `code`. Still raised bare where no narrower answer has
    been drawn yet, which is what the subclasses are steadily taking away.
    """

    kind = KIND_REFUSED
    code = "state-refused"


# --- what stands in the way ---------------------------------------------------
#
# The refusal family, split at the level the operator's *next command* differs.
# Every one of these is answered by changing the state, which is what keeps them
# one `kind`; the split is between the states, because "the lifecycle says no" is
# not something a surface can act on and "nothing has been admitted yet" is.
#
# Drawn from the answers rather than from the raise sites. A condition checked in
# four operations is one class, and two conditions one operation checks that send
# an operator two different places are two. Where a refusal is stated once and
# read by both an operation and the console's gate, the class belongs to the
# operation: the gate composes sentences and routes on nothing.


class NothingToActOn(BatchError):
    """What this verb acts on does not exist yet.

    No batch is current, no experiment is open, no promotion was ever made, no
    run is on record, no cohort owes a reading. One class rather than one per
    absence, because the shape of the answer is the same every time and the
    sentence already names the missing thing: what comes next is the verb that
    creates it, or nothing.

    Distinct from `StandingWork` in the direction it points, and that is the
    whole of it — an operator sent the wrong way waits for something nobody
    started, or starts a second one of something already going.
    """

    code = "nothing-to-act-on"


class AlreadyAnswered(BatchError):
    """A durable record already answers this, and what is being asked differs
    from it.

    Terminal decisions, consumed drafts, settled gates, allocated positions, an
    operation prepared under one set of arguments and asked for again under
    another — everything this controller writes once and never edits. A prepared
    operation standing in the way of a *different* verb is `StandingWork`; this
    is the same verb, asked twice, differently.

    The redo path is what makes the class worth having. An identical request
    finishes an interrupted write and comes back with what is on record, so a
    caller reaching this knows it asked for something else and that repeating the
    recorded request is the way through. Nothing here is answered by waiting.
    """

    code = "already-answered"


class StandingWork(BatchError):
    """Something unfinished is in the way, or is standing on what this would take
    away.

    An analysis stage still being written, an open round, a run the harness has
    not answered for, a request no run was recorded against, a promotion prepared
    and not landed, work admitted on the release a rollback would remove. The
    next command is about that other thing rather than about this one — finish
    it, end it, withdraw it — and the sentence names which.

    The one refusal here that can become allowed with nobody typing anything, a
    run still going being the case, which is why it is not folded into
    `NothingToActOn`: a surface that polls at all polls this.
    """

    code = "standing-work"


class UnknownSubject(BatchError):
    """What the caller named is not what this state holds.

    An experiment id this batch never allocated, an attempt that is not the one a
    decision is being redone for, a draft that is not waiting at this gate, a
    `--source-ref` other than the line the evidence or the configuration names.

    Not `InputError`, though both end in retyping. What is wrong with these is
    not their shape but that the state holds something else, so the sentence
    names what it holds and the answer is read off a status rather than off the
    argument — an operator told the value was malformed goes looking at their own
    typing for a name that is spelled exactly right.

    Where the line is drawn, now that the exit-code rules are written down: a
    selection whose *shape* no state could make admissible is `InputError` and
    exits 3 — a draft id that is not a kebab-case slug, a draft named twice in
    one selection, a selection naming nothing at all, a planned target listed
    twice. Nothing this lifecycle could ever hold makes those legal, so telling
    an operator to change the state sends them to read a status that will never
    name what they typed. What stays here is the mirror of that: a value spelled
    exactly right that this state does not hold, and a `--source-ref` naming a
    line the configuration or the evidence does not — configuration is state,
    and the sentence names the line it holds.
    """

    code = "subject-unknown"


class CheckoutUnusable(BatchError):
    """This checkout cannot be the one the operation is run from.

    A commit or ref the answer depends on is not here — an experiment ref outside
    the default refspec, a base nobody fetched, one side of a pair the question is
    about — or Git will not make the commit or the ref this operation has to
    create in it, or cannot say whether a work tree is sitting on the line about
    to move. Not only Git: `.ai-tasks/` is machine-local too, so an admitted task
    no copy here holds is a completion this machine cannot observe either.

    A fact about this machine and about no revision, which is what separates it
    from every other refusal here: the same command run where the missing thing
    is answers, whether that is a complete clone or the machine the work was done
    on. Not `toolchain`, because the tool is fine — it is this copy that cannot
    answer, and fetching is a different next command from installing a newer Git.
    """

    code = "checkout-unusable"


class LineMoved(BatchError):
    """A ref is not where the record this is checked against says it stands.

    A source line that did not take the commit an operation made for it, a line
    that no longer carries what a rollback was to remove, an experiment ref
    standing off the history its record pins, a ref that moved out from under the
    reading a write was about to be recorded from.

    Its own class because the answer is neither "change the lifecycle" nor
    "wait": run it again, and if it refuses the same way, find out what is moving
    the ref. A compare-and-swap that lost is ordinary and finishable, a line
    somebody reset is not, and only the second half of each sentence tells them
    apart — which is exactly what a caller routing on `code` should not have to
    read.
    """

    code = "line-moved"


class IntegrationUnusable(BatchError):
    """The tree Git computes here is not one this operation may carry.

    A candidate that does not merge cleanly onto the line it would be promoted
    with, an inverse that does not apply to what the line now holds. Refused
    rather than resolved: resolving it decides what the release contains, and
    this controller records decisions instead of making them, so the next step is
    a person with a working tree in front of them.
    """

    code = "integration-unusable"


class EvidenceUnfit(BatchError):
    """The evidence a decision would rest on does not say what the decision needs.

    A round nothing has replayed, a run whose reading no longer describes the
    tree a promotion would carry, an integration this checkout recomputes
    differently from the one that was measured, a counterfactual nobody started
    or one that ended without measuring anything. What answers it is producing
    evidence — replay this round as the line now stands, measure the pair again —
    and nothing about the lifecycle.

    Missing evidence and stale evidence are one class deliberately. They differ
    in the sentence and not in the answer: both are settled by measuring what is
    there now, and a caller told "there is none" does exactly what a caller told
    "it no longer fits" does. Distinct from `IntegrationUnusable`, which is Git
    saying these trees do not combine at all — that one is resolved by a person
    with a working tree, this one by a run. Distinct from `StandingWork` in the
    same way at the other end: a measurement still going is not evidence that
    fell short, and the run answering it is the one already in flight.
    """

    code = "evidence-unfit"


class RecordRefused(BatchError):
    """The document a writer handed to a record's own rules is not one that
    record may hold.

    The cross-field rules of the files this controller publishes through their
    parsers — runs appended in order, a position allocated once, a completed run
    that measured something, a verdict its counterfactual supports. Asked in the
    writing direction they answer the caller stating the record's next state,
    which is a refusal, and is answered by stating one the rules take.

    The same rules read the other way are not this. A file already on disk that
    breaks one was written by nobody present, so its reader translates the whole
    answer to `RecordError` before a caller sees it (`damaged_record`) — and the
    translation is the reader's, because at the rule itself the two directions
    are the same mapping.
    """

    code = "record-refused"


class DraftInadmissible(BatchError):
    """A change-task draft, or the copy an admission made of one, is not the
    inert task file this gate admits.

    Frontmatter that never closes, a status other than the draft's, a session log
    already carrying an entry, a task id another draft or another task holds, a
    copy whose bytes are no longer the ones the record says were admitted. What
    answers it is editing that file — in `proposed-tasks/` for a draft, at its
    task id for a copy — and running the same selection again.

    Its own class rather than `RecordRefused` because of who wrote the file. A
    draft is authored by a person and read here before anything durable happens,
    so what is refused is a document still being written; a record refused is
    this controller's own next state, handed to the rules of a file it publishes.
    An operator sent to the wrong one of those goes looking for a write to redo
    instead of for the paragraph they have to fix.
    """

    code = "draft-inadmissible"


class HarnessAnswerUnusable(BatchError):
    """What the harness was stated to have said cannot be held by the record that
    would carry it.

    The durable half is why this is not `record-refused`, though a record's rules
    are what refused: the request is already written, so nothing was lost and
    nothing is repeated — the operator states the answer again once it is one the
    record holds, or withdraws the request and stops the run at the harness. A
    caller told its document was rejected would go looking for a write to redo.
    """

    code = "harness-answer-unusable"


def damaged_record(complaint: object, *, what: str) -> str:
    """A `RecordError`'s sentence: what is wrong with the file, and what the
    family asks for.

    Here rather than in each reader because the second half is the family's and
    not any one reader's, and a copy of it in every reader would drift into that
    many recoveries for one exit code.

    `complaint` is kept verbatim. Whatever refused — the shared validator, or a
    parser's own cross-field rule — already names the file and the disagreement,
    and that is the only part saying where to look. What is added is the half
    `kind` carries: this is a record, so nothing writes over it or reads around
    it, and no command retyped anywhere fixes it.

    `what` names the record, because one word of it is load-bearing: a record
    the repository carries is restored from version control, and one only this
    machine ever held is not restored at all.
    """

    return (
        f"{complaint}; that file is {what}, so nothing here writes over it or reads around it — restore "
        "it, or have someone who has looked at it decide what it should say"
    )
