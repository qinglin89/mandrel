"""Replay evidence: the runs measured against an experiment's pinned candidates.

Invariant 10 puts a canary or replay between a candidate and the source line, and
invariant 16 says what it may be run against: the revision a round's seal pinned,
never the tip as it stands. So a replay is always *about* one round, and this
module reads the runs recorded for an experiment and derives whether any of them
still describes what a promotion would carry.

**Two pins, not one.** A round's candidate revision is immutable, which is what
lets evidence keep naming one tree after the experiment moves on. It is not
enough on its own: what a promotion puts on the source line is that candidate
integrated onto that line, and the line moves for reasons the experiment knows
nothing about — another promotion, ordinary release work. A result therefore
binds to the merge input as well, and records the tree the two produced. The
candidate going stale is a fact about the record (a later round was sealed); the
merge input going stale is a fact about the repository right now, so it is asked
of Git and answered three-valued, the way everything Git-shaped is answered here.

**Five states, three of them written down.** A record says `running`,
`completed`, or `failed`. `incomplete` and `stale` are derived — the first
because no run names the round that needs one, the second because the run that
does no longer describes the tree in question. None of the five is stored:
`Evidence` is re-derived on every read from the experiment record, the replay
records, and Git, so a clone that never ran the harness reads the same answer as
the machine that did (contract: What is derived).

**The harness is a dependency, not a component.** `ReplayHarness` is the whole
of what this controller may assume about whatever runs the cases: it is handed
one pinned integration and answers with what it will run and an opaque handle,
and the handle is stored and given back unread exactly as a feed cursor is. The
run's durable state is the record, not a process — a replay started here is
visible to a `status` run tomorrow, on this machine or another, because nothing
about it lives in the memory of the process that started it. The record is also
the whole of the request: the selections the harness made travel back to it as
`ReplayRequest.reproduce`, so running the same thing again is asking for it by
name rather than hoping the same choice is made twice.

**Four operations, and the order inside them.** `start` pins the integration and
asks the harness for a run; `conclude` asks that run for its numbers and records
them; `abandon` records why a run ended when its harness cannot say, which is
what keeps a harness that died from leaving the round unmeasurable behind a run
nothing will ever conclude; `withdraw` takes back a request the harness never
answered for. All four take the same single-writer lock as every other evolution
write, run the same guarded preamble, and publish through this module's own
parser, so a write cannot produce evidence its own reader refuses.

**The request is durable before the harness is asked.** A record cannot state a
run until the harness answers, because the cohort, the evaluator, the
configuration and the handle are all its to choose. So what is written first is
not a run but the request for one — the round, the attempt it will occupy, the
integration pinned for it, and the expectation — which is the whole of what the
controller owns, and is what names that run across the window where the harness
has begun something this file does not yet describe. `start` run again resumes
the request instead of making a new one: the same experiment, round, and attempt
reach the harness a second time, and a harness that already began that run
answers with it rather than starting another (`ReplayHarness.start`). Attempts
are allocated once and never reused, so no second request legitimately wears one
position, and the retry cannot re-pin the integration underneath a run that is
already measuring the old one.

A pending request is not evidence and never becomes any: nothing has measured
the round while one stands, and the write that records the run it became clears
it in the same file. Every check a start makes is still made before the harness
is asked, so a refusal costs nothing that was already running. What that
order can still leave is a harness answering with something this record cannot
hold: `abandon` is the way out of it once a run is recorded and `withdraw` is
before one is, since a round is measured against one integration at a time and
either would otherwise block every later run of the experiment.

**A position is allocated once, whatever becomes of it.** A withdrawal gives up
a request, not the attempt it held: the harness may have begun a run under that
position, and reissuing it would key a second request to the run already going —
the old integration's numbers arriving under a record stating the new one, which
is the whole of what the request was written down to prevent. So a withdrawn
position is kept, the next request takes the one after it, and what a round's
attempts are allocated from is the runs and the withdrawals together.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from . import guards
from .config import REPLAYS_SCHEMA_FILENAME, EvolutionConfig
from .errors import BatchError, ValidationError
from .ledger import append_records, build_record
from .lineage import BatchLineage, Experiment
from .revisions import merge_tree, ref_tip
from .schema import definition, format_rfc3339, load_schema, parse_json, validate_or_raise
from .state import atomic_write_text, single_writer_lock

REPLAYS_FILENAME = "replays.json"
REPLAYS_SCHEMA_VERSION = 1

RECORD_REPLAY_COMPLETED = "replay-completed"

# How a run ended, as its own record states it.
RESULT_COMPLETED = "completed"
RESULT_FAILED = "failed"

# Which direction of a measurement counts as improvement. `neither` marks an
# observation invariant 13 wants recorded without making it the score.
BETTER_LOWER = "lower"
BETTER_HIGHER = "higher"
BETTER_NEITHER = "neither"

# What the experiment's current round has for evidence. The first three are read
# off a record; the last two are derived, and are the two a promotion gate cares
# about most — nothing has measured this round, or what measured it has since
# stopped describing it.
EVIDENCE_COMPLETE = "completed"
EVIDENCE_RUNNING = "running"
EVIDENCE_FAILED = "failed"
EVIDENCE_STALE = "stale"
EVIDENCE_INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class Integration:
    """The exact tree a run exercised, and the commits that identify it."""

    base_revision: str
    candidate_revision: str
    merge_input_revision: str
    merge_input_ref: str | None
    tree: str


@dataclass(frozen=True)
class Exclusion:
    case_id: str
    reason: str


@dataclass(frozen=True)
class CaseSet:
    """The eligible cohort a run exercised, and what was held out of it."""

    case_set_id: str
    case_set_sha256: str
    count: int
    excluded: tuple[Exclusion, ...]


@dataclass(frozen=True)
class Evaluator:
    """Who judged the run, in an imported report's vocabulary (invariant 5)."""

    backend: str
    model: str
    rubric_revision: str | None


@dataclass(frozen=True)
class Harness:
    """Which harness produced this evidence, how it was configured, and its own
    name for the run.

    `handle` is opaque above this boundary: only the harness knows what it means,
    and this package stores it and hands it back unread. Inventing a meaning for
    it here would tie the controller to one implementation of a dependency the
    contract keeps replaceable.
    """

    id: str
    revision: str
    config_sha256: str
    handle: str | None


@dataclass(frozen=True)
class Measurement:
    """One quantity on the base and on the candidate."""

    metric: str
    unit: str
    baseline: float | None
    candidate: float
    better: str

    @property
    def goal(self) -> bool:
        """Whether this measurement is something the change was meant to move,
        as opposed to an observation recorded beside it (invariant 13)."""

        return self.better != BETTER_NEITHER


@dataclass(frozen=True)
class Regression:
    case_id: str
    summary: str


@dataclass(frozen=True)
class Result:
    """How a run ended."""

    outcome: str
    concluded_at: str
    detail: str
    elapsed_seconds: float | None
    metrics: tuple[Measurement, ...]
    regressions: tuple[Regression, ...]
    ambiguity: str | None


@dataclass(frozen=True)
class Replay:
    """One run of the case suite against one round's pinned candidate."""

    experiment_id: str
    round_number: int
    attempt: int
    started_at: str
    integration: Integration
    cases: CaseSet
    evaluator: Evaluator
    harness: Harness
    expectation: str
    result: Result | None

    @property
    def running(self) -> bool:
        """Still going: nothing has concluded it. Age is not a conclusion — a
        harness that died leaves this true until something records why."""

        return self.result is None

    @property
    def completed(self) -> bool:
        return self.result is not None and self.result.outcome == RESULT_COMPLETED

    @property
    def failed(self) -> bool:
        return self.result is not None and self.result.outcome == RESULT_FAILED

    @property
    def request(self) -> ReplayRequest:
        """This run as a request that would run it again.

        The record is the durable form of the request, so repeating a run needs
        nothing that was held in the memory of the process that started it — and
        that has to include the selections the harness made, not only the
        integration the controller pinned. A request naming the integration
        alone would be answered by whatever cohort, evaluator, and configuration
        the harness chose this time, which is a different measurement wearing
        this one's provenance.

        The attempt is the one thing a rerun replaces: a repeat is a new run at
        the next attempt, never an edit of this record.
        """

        return ReplayRequest(
            experiment_id=self.experiment_id,
            round_number=self.round_number,
            attempt=self.attempt,
            integration=self.integration,
            reproduce=ReplayPlan(cases=self.cases, evaluator=self.evaluator, harness=self.harness),
        )


@dataclass(frozen=True)
class PendingRun:
    """A run this controller committed to before it asked the harness for one.

    Everything here is the controller's own: which round is being measured, the
    position the run will occupy, the integration pinned for it, and what it was
    expected to show. Nothing the harness chooses is in it, which is exactly why
    it can be written before the harness is asked anything — and why it is not a
    run. Nothing derives evidence from it: while one stands, the round it names
    has been measured by nothing.

    What it is for is the window in between. The harness is an external thing
    being started, and an answer that never arrives — a lost process, a write
    that failed — would otherwise leave a run going that no record names. This
    names it: `start` run again re-submits this request unchanged, and the
    harness answers with the run it already began.
    """

    round_number: int
    attempt: int
    requested_at: str
    integration: Integration
    expectation: str


@dataclass(frozen=True)
class WithdrawnRequest:
    """A position a request held and gave up without ever becoming a run.

    Kept rather than erased, because the position is half of what the harness is
    keyed on. A withdrawal is what happens when the harness cannot describe what
    it is running, which means it may well be running something: a later request
    reissued at that position would be answered with *that* run, and the numbers
    of the integration this record no longer names would arrive under the one it
    does. So the position stays allocated forever and the next request takes the
    one after it.

    What it carries is what a withdrawal is about afterwards: the window the
    stray run would have started in, and the integration pinned for it — which
    is what an operator has to go on to find that run at the harness and stop
    it. Deliberately not the expectation: that is a prediction kept to be read
    back against numbers, and this request produced none.
    """

    round_number: int
    attempt: int
    requested_at: str
    withdrawn_at: str
    integration: Integration


@dataclass(frozen=True)
class History:
    """One experiment's replay file: the runs, the request that is not one yet,
    and the positions given up without becoming either.

    All three are read together because they are one file and one sequence — a
    request occupies the position the run it becomes will take, and a withdrawal
    holds one nothing will ever take — so which attempt is next cannot be
    answered from any of them alone.
    """

    replays: tuple[Replay, ...]
    pending: PendingRun | None
    withdrawn: tuple[WithdrawnRequest, ...]


@dataclass(frozen=True)
class Evidence:
    """What the experiment's current round has been measured by, if anything.

    Derived on every read. `state` is the answer a promotion gate asks for, and
    the two tuples are why it is not `completed`: `drift` is what this checkout
    established, `unverified` is what it could not answer at all. A repository
    that does not hold the source-line ref reports the second rather than
    guessing at the first — but it is still not a promotion this evidence
    supports, which is why `promotable` requires both to be empty.
    """

    experiment_id: str
    # The round evidence is needed for: the experiment's last one, whether or not
    # it is candidate-ready.
    round_number: int
    state: str
    # The run being reported, which for a stale reading may name an earlier
    # round. None when nothing has ever been run for this experiment.
    replay: Replay | None
    drift: tuple[str, ...]
    unverified: tuple[str, ...]

    @property
    def promotable(self) -> bool:
        """Whether this evidence describes the tree a promotion would carry.

        Everything else — an unsealed round, a superseded candidate, a merge
        input that moved, a run still going, a run that failed, a check this
        checkout could not make — is the same answer to a promotion: not from
        here, not yet.
        """

        return self.state == EVIDENCE_COMPLETE and not self.drift and not self.unverified


@dataclass(frozen=True)
class ReplayRequest:
    """What a harness is asked to exercise: one pinned integration, which round
    of which experiment it belongs to, and — for a rerun — the selections it must
    reproduce.

    `reproduce` is what makes a record enough to run again. None is a first run:
    the controller pins the integration, and the harness answers with the cohort,
    the evaluator, and the configuration it selected, because it owns all three.
    Set, it carries what an earlier run resolved, and the harness exercises
    exactly that — resolving the configuration hash back to the configuration it
    issued for it — or refuses. Selecting the nearest thing it still has would
    produce a second set of numbers standing under the first one's provenance,
    which is the one failure a reproduction cannot report.

    The handle inside it names the run being reproduced, not the run being
    started: a rerun is a new attempt and takes the new handle its harness issues.

    The expectation recorded beside a run is deliberately not in here. It is a
    human prediction, kept so the numbers cannot be read back onto it afterwards;
    handing it to the thing being measured would make it an instruction.
    """

    experiment_id: str
    round_number: int
    attempt: int
    integration: Integration
    reproduce: ReplayPlan | None = None


@dataclass(frozen=True)
class ReplayPlan:
    """A harness's answer to a request: what it will run, and its name for the run.

    The cohort and the evaluator come from the harness rather than the
    controller, which owns neither the case suite nor the rubric. What the
    controller owns is the integration it pinned, and the record that states
    both — which is also what a rerun hands back as `ReplayRequest.reproduce`,
    so a selection made once can be asked for again by name.
    """

    cases: CaseSet
    evaluator: Evaluator
    harness: Harness


@dataclass(frozen=True)
class ReplayReport:
    """A harness's measurements for a finished run.

    Carries no timestamp: when a run concluded is recorded by the controller that
    observed it, the way a round's completion observation is, so the record has
    one clock behind it.
    """

    outcome: str
    detail: str
    elapsed_seconds: float | None
    metrics: tuple[Measurement, ...]
    regressions: tuple[Regression, ...]
    ambiguity: str | None


class ReplayHarness(Protocol):
    """The whole of what this controller may assume about a replay harness."""

    def start(self, request: ReplayRequest) -> ReplayPlan:
        """Begin exercising `request.integration`, and say what is being run.

        Asked twice for one run, it answers twice with that run. The experiment,
        the round and the attempt name it: attempts are allocated once and never
        reused, so a second request wearing one position is the first request
        arriving again — a controller that pinned the integration, wrote the
        request down, and did not hear back. Starting a second run for it would
        leave the first going with nothing naming it, which is the one failure
        the request exists to prevent, so the plan already issued is returned
        instead — the same handle included.

        The controller holds up its half of that key: a position a request gives
        up is recorded as withdrawn rather than freed, so this side never has to
        distinguish a repeated request from a new one wearing an old position.

        With `request.reproduce` set, what is being run is not a choice: the
        cohort, the evaluator, and the configuration are the ones an earlier run
        resolved, and a harness that cannot reproduce them — a case set it no
        longer holds, a configuration it cannot resolve from the hash it issued —
        raises rather than running the nearest thing it can.
        """

    def poll(self, handle: str) -> ReplayReport | None:
        """The measurements for `handle`, or None while that run is still going."""


def read_replays(config: EvolutionConfig, experiment: Experiment) -> History:
    """Every run recorded against one experiment, in the order they were started,
    and the request that has not become one yet.

    Empty covers both "the file lists nothing" and "no file yet": an experiment
    nobody has measured is the ordinary state of every round before its first
    replay, and the two are the same fact about the evidence.
    """

    path = replays_path(experiment)
    try:
        record = parse_json(path.read_text(encoding="utf-8"), description=f"replay record {path}")
    except FileNotFoundError:
        return History(replays=(), pending=None, withdrawn=())
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchError(f"unreadable replay record {path}: {exc}") from exc
    if not isinstance(record, dict):
        raise BatchError(f"replay record is not a JSON object: {path}")
    return parse_replays(config, record, experiment)


def replays_path(experiment: Experiment) -> Path:
    return experiment.directory / REPLAYS_FILENAME


def parse_replays(
    config: EvolutionConfig,
    record: Mapping[str, Any],
    experiment: Experiment,
) -> History:
    """One experiment's replay records, validated and checked against the rounds
    they claim to have measured.

    The implemented JSON Schema subset has no cross-field conditionals and no
    view of the experiment record at all, so the rules that make these runs one
    readable history live here: runs that only append, every attempt of a round
    allocated exactly once, one unfinished run at a time, one handle naming one
    run, a result whose shape matches how it ended, an outstanding request at
    the position the run it becomes will take, and — the load-bearing one — a
    candidate revision that is the one the named round actually pinned. Each is
    a way for a record to be well-formed on its own and still be evidence about
    a tree nobody can identify.

    Public because the writers publish through it: an operation builds the next
    state of this file and validates it here before it lands, so a write cannot
    produce evidence its own reader refuses.
    """

    path = replays_path(experiment)
    validate_or_raise(
        record,
        load_schema(config.schema_path(REPLAYS_SCHEMA_FILENAME)),
        description=f"replay record {path}",
    )

    if record["experiment_id"] != experiment.experiment_id:
        raise BatchError(
            f"{path}: names experiment {record['experiment_id']!r} but sits in "
            f"{experiment.experiment_id!r}; one experiment's evidence cannot measure another's candidate"
        )

    replays = tuple(_read_replay(path, experiment.experiment_id, item) for item in record["replays"])
    pending = _read_pending(path, record["pending"])
    withdrawn = tuple(_read_withdrawal(path, item) for item in record["withdrawn"])
    _require_appended_runs(path, replays)
    # Before anything reads a next attempt off this file: the two rules below and
    # the writers all count positions, and counting is only the same as reading
    # the highest one once the allocations are known to be contiguous.
    _require_allocated_once(path, replays, withdrawn)
    _require_one_unfinished(path, replays)
    _require_distinct_handles(path, replays)
    _require_requestable(path, replays, withdrawn, pending)
    _require_pinned_candidates(path, experiment, replays, withdrawn, pending)
    return History(replays=replays, pending=pending, withdrawn=withdrawn)


def describe_evidence(config: EvolutionConfig, experiment: Experiment) -> Evidence:
    """What the experiment's current round has been measured by. Read-only.

    Precedence, and each step is a different question a promotion would ask:

    1. a completed run for this round whose merge input this checkout confirms
       has not moved — the one state that supports a promotion, and it is not
       unmade by a second run started beside it, because evidence that is still
       exact stays exact;
    2. a completed run for this round whose merge input this checkout cannot
       check at all — the same run, reported as the unanswered check it is;
    3. a run still going;
    4. the newest run, when that is the one that failed;
    5. a completed run that no longer describes the tree in question — the round
       moved on, or the source line did;
    6. nothing yet.

    An outstanding request is none of the six. It measured nothing, so it does
    not change the state; what it changes is what every reading short of a
    promotion has to say, since "this round's run failed" and "its run failed
    and something may be running against it right now that this record does not
    name yet" send an operator to different places. So it is added once, at the
    single return below, to whichever reading was reached — rather than at each
    `return`, where the next one to be written would be the next to forget it.

    Step 1 is the only reading it is left out of, and that is the whole rule:
    the promotable reading is the one where reporting it would decide something
    else — whether work in flight holds a promotion back, which is the promotion
    gate's question. Step 2 is not that reading, however much it resembles it:
    `unverified` has already shut the gate, so the note costs nothing there and
    an operator reading "completed, and the source line cannot be checked here"
    still needs to know a run may be going.

    A withdrawn position is not reported at all: it measured nothing, it is over
    as far as this controller is concerned, and nothing here can ever learn
    whether the harness ran it — the record keeps it for whoever goes looking,
    and a note nothing could ever clear is not evidence about this round.
    """

    history = read_replays(config, experiment)
    replays = history.replays
    awaiting = () if history.pending is None else (_describe_outstanding(history.pending),)
    last = experiment.last_round
    common = {"experiment_id": experiment.experiment_id, "round_number": last.number}
    # No replay can name an unsealed round (`_require_pinned_candidates`), so the
    # open-round case needs no test of its own — it arrives here with nothing for
    # this round and is explained rather than special-cased.
    unsealed = (
        ()
        if last.candidate_ready
        else (
            f"round {last.number} is open; nothing measures a round before its seal pins the candidate "
            "(invariant 16)",
        )
    )
    here = tuple(replay for replay in replays if replay.round_number == last.number)

    newest_complete = next((replay for replay in reversed(here) if replay.completed), None)
    moved, note = (None, "") if newest_complete is None else _merge_input_moved(config, newest_complete)
    if newest_complete is not None and moved is False:
        # The one reading a promotion is built on, and the one an outstanding
        # request is deliberately left out of: reporting it in either tuple would
        # make evidence that is still exact unpromotable, which is the
        # promotion-side question of whether a run in flight holds a promotion
        # back — an operation that does not exist yet, and whose gate is where
        # that would be decided.
        return Evidence(**common, state=EVIDENCE_COMPLETE, replay=newest_complete, drift=(), unverified=())

    # Every reading below is already short of a promotion, which is what lets the
    # single return underneath report the outstanding request for all of them.
    #
    # Nothing below can be carrying `unsealed` either: every one of these
    # branches needs a run that names this round, and an unsealed round has none.
    unverified: tuple[str, ...] = ()
    if newest_complete is not None and moved is None:
        # A check this checkout could not make, which is not the same answer as
        # agreement and is not reported as one. The state still describes the
        # run; `unverified` is what stops it short of a promotion, and it keeps
        # this reading ahead of a later run for the same reason step 1 is ahead
        # of one: what a promotion asks about is the newest complete result.
        measured, state, drift = newest_complete, EVIDENCE_COMPLETE, ()
        unverified = (note,)
    elif here and here[-1].running:
        # The one branch a request cannot coexist with — `_require_requestable`
        # refuses one made under an unfinished run — so `awaiting` is empty here
        # by that rule rather than by this branch's choosing.
        measured, state, drift = here[-1], EVIDENCE_RUNNING, ()
    elif here and here[-1].failed:
        measured, state, drift = (
            here[-1],
            EVIDENCE_FAILED,
            (f"the newest run of round {last.number} failed: {here[-1].result.detail}",),
        )
    elif newest_complete is not None:
        measured, state, drift = newest_complete, EVIDENCE_STALE, (note,)
    elif replays:
        measured, state, drift = (
            replays[-1],
            EVIDENCE_STALE,
            unsealed
            + (
                f"the newest evidence measured round {replays[-1].round_number}, and this experiment is on "
                f"round {last.number}; a round's evidence describes the candidate that round pinned and never "
                "the next one's",
            ),
        )
    else:
        measured, state, drift = None, EVIDENCE_INCOMPLETE, (
            unsealed or (f"round {last.number} has not been replayed",)
        )
    # Every branch above is already short of a promotion, so a request that may
    # be running against this round is reported wherever it stands.
    return Evidence(**common, state=state, replay=measured, drift=drift + awaiting, unverified=unverified)


# --- the runs ----------------------------------------------------------------


@dataclass(frozen=True)
class RunStarted:
    """A run this operation began, and the record that now names it."""

    experiment_id: str
    round_number: int
    attempt: int
    started_at: str
    integration: Integration
    plan: ReplayPlan
    record_path: Path
    # True when this run answers a request an earlier run of this operation left
    # outstanding: the integration and the position come from that request, and
    # the harness was asked for the run it may already have been running.
    resumed: bool = False


@dataclass(frozen=True)
class RequestWithdrawn:
    """A request taken back before it ever became a run.

    `withdrawn` is False when there was nothing outstanding — this operation run
    a second time, or run at all where no request stands. The redo is answered
    from the request's absence rather than from a reason matched against what
    was written: one write either landed, clearing the request and recording the
    position it gave up, or it did not.

    `pending` is what was withdrawn, reported back because it names the run that
    may still be going at a harness this controller will never hear from again.
    The same fact is on record as a withdrawal, so an operator who did not keep
    this can still find it.
    """

    experiment_id: str
    pending: PendingRun | None
    withdrawn: bool


@dataclass(frozen=True)
class RunConcluded:
    """What the newest run of the open experiment is, after asking about it.

    Three states reach here and `recorded` is what tells them apart: the run
    concluded now (True), a run still going (False, `running`), and a run whose
    result was already on record (False) — which is an interrupted conclusion
    reporting what it wrote rather than writing a second one.
    """

    experiment_id: str
    replay: Replay
    recorded: bool

    @property
    def round_number(self) -> int:
        return self.replay.round_number

    @property
    def attempt(self) -> int:
        return self.replay.attempt

    @property
    def running(self) -> bool:
        return self.replay.running

    @property
    def outcome(self) -> str | None:
        return self.replay.result.outcome if self.replay.result is not None else None


def start(
    config: EvolutionConfig,
    harness: ReplayHarness,
    *,
    source_ref: str,
    expectation: str,
    now: datetime | None = None,
) -> RunStarted:
    """Measure the open experiment's pinned candidate, integrated onto `source_ref`.

    What is pinned here is the whole of what the controller owns about a run: the
    round's already-sealed candidate revision (invariant 16 — this operation
    never seals one, and refuses an open round rather than measuring a tip that
    is still free to move), the source-line commit read from `source_ref`, and
    the tree the two produce. The harness owns the rest and answers with it.

    The source line is named rather than assumed. The commit it stands at is what
    a promotion would merge onto, it moves for reasons this experiment knows
    nothing about, and a checkout has no way to tell which of its refs is the one
    a promotion will land on — so it is given, recorded, and afterwards asked
    about by name.

    `expectation` is recorded before any numbers exist, which is the only time it
    can be: an expectation written beside the result is a reading of it. It is
    deliberately not handed to the harness, which would make a prediction an
    instruction.

    Every refusal is made before the harness is asked for anything. A second run
    started while one is going is one of them, so a retry costs nothing: the
    answer is to conclude the run that is going.

    Run again with a request outstanding, this is that request resumed rather
    than a second one: the integration, the position and the expectation come
    from the record, the harness is asked for the same run, and the answer lands
    where the request stood. What it is not free to do is pin the integration
    again — the source line may have moved since, and a retry that measured the
    new one would be recording a tree while the harness runs the old. So the
    arguments are checked against the request rather than acted on, and a
    different source line or expectation is refused with what is outstanding.

    The ref check belongs to making a request, not to answering one. A resume
    records a run that already exists, and a ref that moved since is exactly
    what a second attempt is for; refusing it would leave the run nothing names
    — the reason `conclude` does not make that check either.
    """

    moment = _moment(now)
    predicted = _expectation(expectation)

    with single_writer_lock(config):
        current = guards.current_cycle(config, now=moment)
        experiment = guards.require_open_experiment(current, "replay")

        history = read_replays(config, experiment)
        replays = history.replays
        requested = history.pending
        resumed = requested is not None
        if requested is None:
            requested = _request(
                config,
                current,
                experiment,
                history,
                source_ref=source_ref,
                expectation=predicted,
                at=format_rfc3339(moment),
            )
            _write_replays(config, experiment, replace(history, pending=requested))
        else:
            _require_same_request(experiment, requested, source_ref, predicted)

        described = _describe_pending(requested)
        here = [replay for replay in replays if replay.round_number == requested.round_number]
        plan = harness.start(
            ReplayRequest(
                experiment_id=experiment.experiment_id,
                round_number=requested.round_number,
                attempt=requested.attempt,
                integration=requested.integration,
                reproduce=_reproduce(here),
            )
        )
        started = Replay(
            experiment_id=experiment.experiment_id,
            round_number=requested.round_number,
            attempt=requested.attempt,
            # When the run began, which is when its integration was pinned — not
            # the moment a resume finally heard back about it.
            started_at=requested.requested_at,
            integration=requested.integration,
            cases=plan.cases,
            evaluator=plan.evaluator,
            harness=plan.harness,
            expectation=requested.expectation,
            result=None,
        )
        try:
            path = _write_replays(config, experiment, replace(history, replays=replays + (started,), pending=None))
        except (BatchError, ValidationError) as exc:
            raise BatchError(
                f"{experiment.experiment_id}: {plan.harness.id} began {described} as handle "
                f"{plan.harness.handle!r} and described it in a way this record cannot hold ({exc}); the request "
                "for that run is still on record, so run this again once the harness can describe what it holds "
                "— or withdraw the request and stop the run at the harness"
            ) from exc

    return RunStarted(
        experiment_id=experiment.experiment_id,
        round_number=requested.round_number,
        attempt=requested.attempt,
        started_at=requested.requested_at,
        integration=requested.integration,
        plan=plan,
        record_path=path,
        resumed=resumed,
    )


def _request(
    config: EvolutionConfig,
    current: BatchLineage,
    experiment: Experiment,
    history: History,
    *,
    source_ref: str,
    expectation: str,
    at: str,
) -> PendingRun:
    """The request a fresh start makes durable before the harness is asked.

    Every refusal a start has is here, which is what the order is for: the
    request is written down, and from then on the harness may be running
    something — so nothing that could refuse may still be waiting to be checked
    at that point.

    The position it takes comes from everything this round has already
    allocated, runs and withdrawals alike: a position handed out twice is a
    request the harness would answer with the run it began under the first one.
    """

    guards.require_consistent_ref(current)

    replays = history.replays
    round_ = experiment.last_round
    if round_.seal is None:
        raise BatchError(
            f"round {round_.number} of {experiment.experiment_id} is still open; a round is measured only "
            "once its candidate is pinned, because an open round's tip moves and evidence taken against it "
            "describes a tree the record cannot afterwards identify (invariant 16) — seal the round, and "
            "this run names what the seal pinned"
        )
    if replays and replays[-1].running:
        going = replays[-1]
        raise BatchError(
            f"{_describe_replay(going)} of {experiment.experiment_id} is still running under "
            f"{going.harness.id} handle {going.harness.handle!r}; a round is measured against one "
            "integration at a time, so a second run started under it would leave two answers about one tree "
            "with nothing to choose between them — conclude that run first"
        )

    attempt = _allocated(replays, history.withdrawn, round_.number) + 1
    return PendingRun(
        round_number=round_.number,
        attempt=attempt,
        requested_at=at,
        integration=_pin(
            config,
            experiment,
            round_.seal.candidate_revision,
            source_ref,
            _position(round_.number, attempt),
        ),
        expectation=expectation,
    )


def _require_same_request(
    experiment: Experiment,
    requested: PendingRun,
    source_ref: str,
    expectation: str,
) -> None:
    """A resume asks for the run that is outstanding, not for a different one.

    Both arguments are the operator restating what they asked for, and a
    mismatch is a request they have not made yet: the harness may already be
    running this one, so answering the new one would start a second run of the
    same attempt while the first goes on unnamed. The way to ask for something
    else is to withdraw this request first — which is a decision about a run
    that may exist, and therefore an operator's to make rather than one taken
    for them by an argument that happened to differ.
    """

    described = _describe_pending(requested)
    integration = requested.integration
    if integration.merge_input_ref != source_ref:
        raise BatchError(
            f"{experiment.experiment_id} has {described} outstanding, integrating onto "
            f"{integration.merge_input_ref} at {integration.merge_input_revision[:12]}; a request is answered as "
            f"it was made, because the harness may already be running it — name {integration.merge_input_ref} to "
            f"resume it, or withdraw it to ask for {source_ref} instead"
        )
    if requested.expectation != expectation:
        raise BatchError(
            f"{experiment.experiment_id} has {described} outstanding, expected to show "
            f"{requested.expectation!r}; an expectation is recorded before the numbers exist and this run may "
            "already be producing them, so the one on record stands — a second prediction over a run in flight "
            "is the reading it exists to prevent"
        )


def conclude(
    config: EvolutionConfig,
    harness: ReplayHarness,
    *,
    now: datetime | None = None,
) -> RunConcluded:
    """Ask the run that is going for its numbers, and record them if it has any.

    Polling a run that is still going is the ordinary case and not an error: this
    reports the run unchanged and writes nothing, so it can be called as often as
    an operator likes. The clock is the controller's — a harness reports what it
    measured, and when that was observed is recorded by whoever observed it, the
    way a round's completion observation is.

    Run again after an interrupted conclusion, it reports the result already on
    record rather than polling for a second one; the audit line that interruption
    may have cost is not re-appended, which is the rule a redone seal follows.

    Unlike the round transitions, this does not refuse on a ref standing off its
    record's history. The result is a fact about a run that already happened, and
    a ref that has moved since makes that evidence stale rather than wrong —
    which `describe_evidence` reports from the record. Refusing to write it would
    discard the only durable form of the measurement and leave the run recorded
    as going forever.
    """

    moment = _moment(now)

    with single_writer_lock(config):
        current = guards.current_cycle(config, now=moment)
        experiment = guards.require_open_experiment(current, "conclude a replay of")

        history = read_replays(config, experiment)
        _require_no_request_outstanding(experiment, history, "concluded")
        replays = history.replays
        if not replays:
            raise BatchError(
                f"{experiment.experiment_id} has no recorded run to conclude; a conclusion writes the result of "
                "a run this controller started, and a harness invocation nothing here recorded is not one it can "
                "speak for"
            )

        going = replays[-1]
        if not going.running:
            return RunConcluded(experiment_id=experiment.experiment_id, replay=going, recorded=False)

        report = harness.poll(going.harness.handle)
        if report is None:
            return RunConcluded(experiment_id=experiment.experiment_id, replay=going, recorded=False)

        stamp = format_rfc3339(moment)
        concluded = replace(
            going,
            result=Result(
                outcome=report.outcome,
                concluded_at=stamp,
                detail=report.detail,
                elapsed_seconds=report.elapsed_seconds,
                metrics=report.metrics,
                regressions=report.regressions,
                ambiguity=report.ambiguity,
            ),
        )
        _write_replays(config, experiment, replace(history, replays=replays[:-1] + (concluded,)))
        append_records(
            config,
            [
                build_record(
                    RECORD_REPLAY_COMPLETED,
                    recorded_at=stamp,
                    batch_id=current.batch_id,
                    experiment_id=experiment.experiment_id,
                    round=going.round_number,
                    revision=going.integration.candidate_revision,
                    detail=report.outcome,
                )
            ],
        )

    return RunConcluded(experiment_id=experiment.experiment_id, replay=concluded, recorded=True)


def abandon(
    config: EvolutionConfig,
    *,
    reason: str,
    now: datetime | None = None,
) -> RunConcluded:
    """Record why a run ended when its harness cannot say.

    A run is going until something records that it stopped: age concludes
    nothing, and a harness that died, lost its handle, or answers with a report
    this record cannot hold would otherwise leave the run going forever — and
    with it the whole experiment, since a round is measured against one
    integration at a time. This is what records that it stopped, and the reason
    is the operator's because the harness is the thing that could not give one.

    It takes no harness for the same reason: this is the path for when asking is
    not the problem. What it writes is a `failed` result, which is what the run
    was — no numbers, and the story in `detail`, exactly as a harness-reported
    failure carries it. A run this ends is answered by another attempt, which is
    what a `failed` result means everywhere else.

    Run again after an interrupted abandonment, it reports the failure already on
    record. A run that ended some other way is not this operation's redo, and is
    reported back rather than overwritten.

    It ends a *run*, so a request that never became one is not its business:
    there is no record to write a failure onto, and inventing one would state a
    cohort, an evaluator and a harness configuration nobody selected. `withdraw`
    takes that back instead.
    """

    moment = _moment(now)
    text = _line(
        reason,
        "ending a run records why; the record is all a later reader has of a run whose harness never reported, "
        "and a failure with no reason is indistinguishable from one nobody looked into",
    )

    with single_writer_lock(config):
        current = guards.current_cycle(config, now=moment)
        experiment = guards.require_open_experiment(current, "end a replay of")

        history = read_replays(config, experiment)
        _require_no_request_outstanding(experiment, history, "ended")
        replays = history.replays
        if not replays:
            raise BatchError(
                f"{experiment.experiment_id} has no recorded run to end; what this records is why a run this "
                "controller started stopped, and there is none"
            )

        going = replays[-1]
        if not going.running:
            result = going.result
            if result is not None and result.outcome == RESULT_FAILED and result.detail == text:
                return RunConcluded(experiment_id=experiment.experiment_id, replay=going, recorded=False)
            raise BatchError(
                f"{_describe_replay(going)} of {experiment.experiment_id} already ended "
                f"{result.outcome!r}: {result.detail!r}; a run ends once, and what is on record is what it "
                "measured — start another attempt rather than restating how this one finished"
            )

        stamp = format_rfc3339(moment)
        concluded = replace(
            going,
            result=Result(
                outcome=RESULT_FAILED,
                concluded_at=stamp,
                detail=text,
                elapsed_seconds=None,
                metrics=(),
                regressions=(),
                ambiguity=None,
            ),
        )
        _write_replays(config, experiment, replace(history, replays=replays[:-1] + (concluded,)))
        append_records(
            config,
            [
                build_record(
                    RECORD_REPLAY_COMPLETED,
                    recorded_at=stamp,
                    batch_id=current.batch_id,
                    experiment_id=experiment.experiment_id,
                    round=going.round_number,
                    revision=going.integration.candidate_revision,
                    detail=RESULT_FAILED,
                )
            ],
        )

    return RunConcluded(experiment_id=experiment.experiment_id, replay=concluded, recorded=True)


def withdraw(config: EvolutionConfig, *, now: datetime | None = None) -> RequestWithdrawn:
    """Take back a request the harness never answered for.

    The way out of the window the request exists to cover. A harness that cannot
    describe what it is running — a plan this record refuses to hold, a run it no
    longer knows about — leaves `start` unable to finish and every later run of
    the experiment blocked behind it, because a round is measured against one
    integration at a time. This gives up that request so the round can be
    measured again.

    What it does not give up is the position. The harness is keyed on the round
    and the attempt, and the reason there is a withdrawal at all is that the
    harness may be running something nobody here can describe — so reissuing
    that position would key the next request to the run this one abandoned, and
    the first integration's numbers would arrive under a record naming the
    second. The position is recorded as withdrawn instead, and the next request
    takes the one after it. A round's attempts therefore count the withdrawals
    as well as the runs, and a gap in them stays what it always was: an
    allocation whose record is missing.

    It takes no reason, and that is the difference from ending a run. A run that
    happened is history and carries why it stopped; a request that never became
    one measured nothing, is derived as nothing, and has no result to attach a
    reason to. What the withdrawal does record is what an operator needs to find
    the run that may be going at a harness this controller will never hear from
    again: the window it was started in, and the integration pinned for it.

    Run again, it reports that nothing was outstanding. That is the whole of its
    redo: a single write with nothing after it either has landed or has not, and
    the request's absence is the answer in both directions.
    """

    moment = _moment(now)

    with single_writer_lock(config):
        current = guards.current_cycle(config, now=moment)
        experiment = guards.require_open_experiment(current, "withdraw a replay request of")

        history = read_replays(config, experiment)
        pending = history.pending
        if pending is None:
            return RequestWithdrawn(
                experiment_id=experiment.experiment_id,
                pending=None,
                withdrawn=False,
            )
        taken = WithdrawnRequest(
            round_number=pending.round_number,
            attempt=pending.attempt,
            requested_at=pending.requested_at,
            withdrawn_at=format_rfc3339(moment),
            integration=pending.integration,
        )
        _write_replays(
            config,
            experiment,
            replace(history, pending=None, withdrawn=history.withdrawn + (taken,)),
        )

    return RequestWithdrawn(
        experiment_id=experiment.experiment_id,
        pending=history.pending,
        withdrawn=True,
    )


def _require_no_request_outstanding(experiment: Experiment, history: History, action: str) -> None:
    """A run is concluded or ended, never a request for one.

    Both operations act on the newest recorded run, and a request standing over
    it is the state where that is the wrong run to act on: the harness was asked
    for a further one and its answer never reached this record. Reporting the
    previous run as the thing to conclude would hide that; so would refusing
    with "no run to conclude" while one may be going. The way forward is the
    same either way — resume the start, which records the run the harness began,
    or withdraw the request.
    """

    pending = history.pending
    if pending is None:
        return
    raise BatchError(
        f"{experiment.experiment_id} has {_describe_pending(pending)} outstanding, and no run recorded for it; "
        f"the harness was asked for that run and its answer never reached this record, so there is nothing here "
        f"to be {action} — start the replay again to record the run it began, or withdraw the request"
    )


def _pin(
    config: EvolutionConfig,
    experiment: Experiment,
    candidate: str,
    source_ref: str,
    described: str,
) -> Integration:
    """The exact tree a run will exercise, or the reason there is none to pin.

    Every one of these refusals happens before a harness is asked for anything,
    so what an operator gets back is a run that never started rather than one
    whose record could not be written.

    The name is checked before Git is asked anything, so a name no ref can have
    is refused as that rather than as a ref this checkout happens not to hold —
    the two suggest opposite things to whoever reads the refusal, and only one of
    them is fixed by fetching. The integration is then checked as a whole against
    the contract's own definition of one, which is also what catches an object id
    this record cannot hold.
    """

    _require_source_line_name(config, replays_path(experiment), described, source_ref)

    merge_input = ref_tip(config.repo_root, source_ref)
    if merge_input is None:
        raise BatchError(
            f"{source_ref} is not in this checkout, so there is no source line to integrate {candidate[:12]} "
            "onto; a replay measures the candidate as a promotion would carry it, which is that candidate "
            "merged onto the release line rather than the candidate on its own — fetch the ref, or run the "
            "replay where the source line is"
        )

    tree, complaint = merge_tree(config.repo_root, merge_input, candidate)
    if tree is None:
        raise BatchError(
            f"{described} of {experiment.experiment_id} has no integration to measure: {complaint}; a candidate "
            f"that does not merge cleanly onto {source_ref} at {merge_input[:12]} is not one a promotion could "
            "carry either, so the conflict is resolved in a further round rather than measured around"
        )

    integration = Integration(
        base_revision=experiment.base_revision,
        candidate_revision=candidate,
        merge_input_revision=merge_input,
        merge_input_ref=source_ref,
        tree=tree,
    )
    validate_or_raise(
        _integration_json(integration),
        definition(load_schema(config.schema_path(REPLAYS_SCHEMA_FILENAME)), "integration"),
        description=f"the integration pinned for {described} of {experiment.experiment_id}",
    )
    return integration


def _reproduce(here: Sequence[Replay]) -> ReplayPlan | None:
    """What a new attempt of this round must exercise, if anything.

    A second attempt of one round replaces the first as that round's evidence,
    and the reason for it is drift in the integration rather than in the cohort:
    the source line moved, so the same measurement is taken again over the tree
    that moved. Letting the harness select afresh would answer a question nobody
    asked — a different cohort, a different rubric revision, standing where the
    first one's numbers stood.

    Not after a failure, which is the one case where selecting again is the point
    — a case set the harness could not hold is exactly what may have failed, and
    reproducing it would refuse every attempt after the first. So a completed
    attempt is reproduced and a failed one is not, and a round's first run always
    selects fresh.

    What the record then states is what the harness answered with, not what it
    was asked for: a harness that substituted something is visible as a different
    case-set hash beside the attempt it was meant to reproduce, rather than
    hidden behind the request.
    """

    previous = here[-1] if here else None
    return previous.request.reproduce if previous is not None and previous.completed else None


def _require_source_line_name(config: EvolutionConfig, path: Path, described: str, ref: str) -> None:
    """The name a run will record for the source line, checked before anything
    resolves it.

    Both halves of the rule the reader applies, in the order that gives the
    useful refusal. The shape comes from the contract's own statement about that
    field rather than from a pattern restated here: a record naming `HEAD`, a
    bare branch, or a revision expression answers "where does the source line
    stand now" from whichever working copy asks it later, and the schema is where
    that requirement is written down. The rest is what a pattern cannot say, and
    is asked the same way the reader asks it.
    """

    integration = definition(load_schema(config.schema_path(REPLAYS_SCHEMA_FILENAME)), "integration")
    validate_or_raise(
        ref,
        integration["properties"]["merge_input_ref"],
        description=f"the source line named for {described}",
    )
    _require_source_line_ref(path, described, ref)


def _write_replays(config: EvolutionConfig, experiment: Experiment, history: History) -> Path:
    """Publish this experiment's runs, its outstanding request, and the positions
    it has given up — through the parser that reads them back.

    The whole file is rewritten because a run's result lands after its pinned
    inputs did, and one file per experiment is what allocates the attempt. What
    is never rewritten is what it already says: the parser refuses a list that
    does not append, so a caller handing back an edited earlier run is refused
    here rather than discovered as evidence about a tree nobody exercised.

    It takes the whole file's state rather than its parts, so an operation says
    what it changed and carries the rest forward untouched — a withdrawal that
    was dropped by a caller listing arguments would hand its position back out.

    The request is part of that one write rather than a file beside it, so the
    transition every operation here makes — a request becoming the run it was
    for, or being given up — is one atomic replacement. Two files could be seen
    holding both at once, and a reader finding a request beside the run it
    became would have to guess which of them the harness is running.
    """

    record = _serialize(experiment.experiment_id, history)
    parse_replays(config, record, experiment)
    path = replays_path(experiment)
    atomic_write_text(path, _json(record))
    return path


def _serialize(experiment_id: str, history: History) -> dict[str, Any]:
    return {
        "schema_version": REPLAYS_SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "pending": None if history.pending is None else _pending_json(history.pending),
        "withdrawn": [_withdrawal_json(taken) for taken in history.withdrawn],
        "replays": [_replay_json(replay) for replay in history.replays],
    }


def _pending_json(pending: PendingRun) -> dict[str, Any]:
    return {
        "round": pending.round_number,
        "attempt": pending.attempt,
        "requested_at": pending.requested_at,
        "integration": _integration_json(pending.integration),
        "expectation": pending.expectation,
    }


def _withdrawal_json(taken: WithdrawnRequest) -> dict[str, Any]:
    return {
        "round": taken.round_number,
        "attempt": taken.attempt,
        "requested_at": taken.requested_at,
        "withdrawn_at": taken.withdrawn_at,
        "integration": _integration_json(taken.integration),
    }


def _replay_json(replay: Replay) -> dict[str, Any]:
    return {
        "round": replay.round_number,
        "attempt": replay.attempt,
        "started_at": replay.started_at,
        "integration": _integration_json(replay.integration),
        "cases": {
            "case_set_id": replay.cases.case_set_id,
            "case_set_sha256": replay.cases.case_set_sha256,
            "count": replay.cases.count,
            "excluded": [
                {"case_id": exclusion.case_id, "reason": exclusion.reason}
                for exclusion in replay.cases.excluded
            ],
        },
        "evaluator": {
            "backend": replay.evaluator.backend,
            "model": replay.evaluator.model,
            "rubric_revision": replay.evaluator.rubric_revision,
        },
        "harness": {
            "id": replay.harness.id,
            "revision": replay.harness.revision,
            "config_sha256": replay.harness.config_sha256,
            "handle": replay.harness.handle,
        },
        "expectation": replay.expectation,
        "result": None if replay.result is None else _result_json(replay.result),
    }


def _integration_json(integration: Integration) -> dict[str, Any]:
    return {
        "base_revision": integration.base_revision,
        "candidate_revision": integration.candidate_revision,
        "merge_input_revision": integration.merge_input_revision,
        "merge_input_ref": integration.merge_input_ref,
        "tree": integration.tree,
    }


def _result_json(result: Result) -> dict[str, Any]:
    return {
        "outcome": result.outcome,
        "concluded_at": result.concluded_at,
        "detail": result.detail,
        "elapsed_seconds": result.elapsed_seconds,
        "metrics": [
            {
                "metric": measurement.metric,
                "unit": measurement.unit,
                "baseline": measurement.baseline,
                "candidate": measurement.candidate,
                "better": measurement.better,
            }
            for measurement in result.metrics
        ],
        "regressions": [
            {"case_id": regression.case_id, "summary": regression.summary}
            for regression in result.regressions
        ],
        "ambiguity": result.ambiguity,
    }


def _expectation(text: str) -> str:
    """What this run was expected to show, before it shows anything."""

    return _line(
        text,
        "a replay records what it was expected to show, before it shows anything; a run started without one is "
        "read afterwards against whatever its numbers turn out to be, which is the reading the expectation "
        "exists to prevent",
    )


def _line(text: str, requirement: str) -> str:
    """One human sentence a record carries, as one line.

    Collapsed for the reason a decision's reason is collapsed: it travels in a
    versioned record and is compared there — a redo recognises its own
    interrupted work by matching what it wrote — and two spellings differing only
    in how they were wrapped would read as two different statements.
    """

    collapsed = " ".join(text.split())
    if not collapsed:
        raise BatchError(requirement)
    return collapsed


def _moment(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise BatchError("replay time must be timezone-aware; a naive datetime records an ambiguous moment")
    return now


def _json(record: Mapping[str, Any]) -> str:
    return json.dumps(record, indent=2, sort_keys=True) + "\n"


def _merge_input_moved(config: EvolutionConfig, replay: Replay) -> tuple[bool | None, str]:
    """Whether the source line has moved since this run measured it.

    Three-valued, like every other question this package puts to Git: a ref this
    checkout does not hold, or a run driven from a detached revision, says
    nothing about the source line and must not be reported as either drift or
    agreement. The refusal that matters is at the promotion, which holds the ref
    it is about to move and therefore always has an answer; a reader says what it
    knows.
    """

    integration = replay.integration
    pinned = integration.merge_input_revision
    ref = integration.merge_input_ref
    if ref is None:
        return None, (
            f"this run integrated onto {pinned[:12]} from a detached revision, so no ref here says where the "
            "source line stands now"
        )
    tip = ref_tip(config.repo_root, ref)
    if tip is None:
        return None, (
            f"{ref} is not in this checkout, so whether the merge input has moved since {pinned[:12]} cannot "
            "be answered here"
        )
    if tip == pinned:
        return False, ""
    return True, (
        f"{ref} has moved from {pinned[:12]} to {tip[:12]} since this run measured the integration; what a "
        "promotion would carry is no longer the tree that was exercised"
    )


def _read_replay(path: Path, experiment_id: str, item: Mapping[str, Any]) -> Replay:
    integration = item["integration"]
    cases = item["cases"]
    evaluator = item["evaluator"]
    harness = item["harness"]
    replay = Replay(
        experiment_id=experiment_id,
        round_number=item["round"],
        attempt=item["attempt"],
        started_at=item["started_at"],
        integration=_read_integration(integration),
        cases=CaseSet(
            case_set_id=cases["case_set_id"],
            case_set_sha256=cases["case_set_sha256"],
            count=cases["count"],
            excluded=tuple(
                Exclusion(case_id=entry["case_id"], reason=entry["reason"]) for entry in cases["excluded"]
            ),
        ),
        evaluator=Evaluator(
            backend=evaluator["backend"],
            model=evaluator["model"],
            rubric_revision=evaluator["rubric_revision"],
        ),
        harness=Harness(
            id=harness["id"],
            revision=harness["revision"],
            config_sha256=harness["config_sha256"],
            handle=harness["handle"],
        ),
        expectation=item["expectation"],
        result=_read_result(path, item["result"], _position(item["round"], item["attempt"])),
    )
    _require_distinct_exclusions(path, replay)
    _require_source_line_ref(path, _describe_replay(replay), replay.integration.merge_input_ref)
    _require_pollable(path, replay)
    return replay


def _read_pending(path: Path, item: Mapping[str, Any] | None) -> PendingRun | None:
    """The request outstanding on this file, if one is.

    The source line is checked exactly as a recorded run's is: a request naming
    something no ref can hold is one no resume could ever answer, and the run it
    would become would carry that name into the evidence.
    """

    if item is None:
        return None
    pending = PendingRun(
        round_number=item["round"],
        attempt=item["attempt"],
        requested_at=item["requested_at"],
        integration=_read_integration(item["integration"]),
        expectation=item["expectation"],
    )
    _require_source_line_ref(path, _describe_pending(pending), pending.integration.merge_input_ref)
    return pending


def _read_withdrawal(path: Path, item: Mapping[str, Any]) -> WithdrawnRequest:
    """One position given up, as the record kept it.

    Read with the same source-line rule as a run and a request, for the reason
    it is kept at all: what it is for is an operator going to the harness with
    the integration that was pinned, and a name no ref can hold would send them
    looking for a source line that never existed.
    """

    taken = WithdrawnRequest(
        round_number=item["round"],
        attempt=item["attempt"],
        requested_at=item["requested_at"],
        withdrawn_at=item["withdrawn_at"],
        integration=_read_integration(item["integration"]),
    )
    _require_source_line_ref(path, _describe_withdrawal(taken), taken.integration.merge_input_ref)
    return taken


def _read_integration(item: Mapping[str, Any]) -> Integration:
    return Integration(
        base_revision=item["base_revision"],
        candidate_revision=item["candidate_revision"],
        merge_input_revision=item["merge_input_revision"],
        merge_input_ref=item["merge_input_ref"],
        tree=item["tree"],
    )


def _read_result(path: Path, result: Mapping[str, Any] | None, described: str) -> Result | None:
    """How a run ended, with the pairings the schema subset cannot state.

    A `failed` run measured nothing readable as a cohort result: a partial sweep
    is a number for some cases and silence for the rest, which is not what the
    metric it reports would be read as. So the failure carries its reason and
    nothing else, and the run is repeated rather than half-believed.
    """

    if result is None:
        return None

    metrics = tuple(
        Measurement(
            metric=entry["metric"],
            unit=entry["unit"],
            baseline=entry["baseline"],
            candidate=entry["candidate"],
            better=entry["better"],
        )
        for entry in result["metrics"]
    )
    regressions = tuple(
        Regression(case_id=entry["case_id"], summary=entry["summary"]) for entry in result["regressions"]
    )
    outcome = result["outcome"]
    if outcome == RESULT_COMPLETED and not metrics:
        raise BatchError(
            f"{path}: {described} is recorded {RESULT_COMPLETED!r} and measured nothing; a run that reached the "
            "end of the cohort has numbers to state, and one that did not is a failure with a reason"
        )
    if outcome == RESULT_FAILED:
        stated = [
            name
            for name, present in (("metrics", metrics), ("regressions", regressions), ("ambiguity", result["ambiguity"]))
            if present
        ]
        if stated:
            raise BatchError(
                f"{path}: {described} is recorded {RESULT_FAILED!r} and still states {stated}; a run that did not "
                "measure the cohort reports why it stopped, and partial numbers read as a result nobody produced"
            )
    _require_comparable(path, described, metrics)
    return Result(
        outcome=outcome,
        concluded_at=result["concluded_at"],
        detail=result["detail"],
        elapsed_seconds=result["elapsed_seconds"],
        metrics=metrics,
        regressions=regressions,
        ambiguity=result["ambiguity"],
    )


def _require_comparable(path: Path, described: str, metrics: Sequence[Measurement]) -> None:
    """Every measurement is one quantity, and a direction has something to point
    away from.

    Two ways a metric list stops being readable. A name appearing twice leaves
    two values for one quantity with nothing to choose between them — and a
    promotion argued from the wrong one is argued from a number that is in the
    record. A direction with no baseline claims an improvement over nothing:
    invariant 10 measures a candidate *against* a baseline, and a quantity with
    only the candidate's side is an observation, which `neither` is how this
    record says.
    """

    seen: set[str] = set()
    for measurement in metrics:
        if measurement.metric in seen:
            raise BatchError(
                f"{path}: {described} measures {measurement.metric!r} twice; one run reports one value per "
                "quantity, and two of them leave a reader to pick which the change is judged on"
            )
        seen.add(measurement.metric)
        if measurement.goal and measurement.baseline is None:
            raise BatchError(
                f"{path}: {described} calls {measurement.better!r} better for {measurement.metric!r} with no "
                "baseline to compare against; a candidate is measured against the base it changed (invariant "
                f"10), and a quantity measured only on the candidate is recorded as {BETTER_NEITHER!r}"
            )


def _require_distinct_exclusions(path: Path, replay: Replay) -> None:
    """A case is held out once, with one reason.

    Two entries for one case are two reasons for one exclusion, and the record
    does not say which of them the cohort was actually narrowed by.
    """

    seen: set[str] = set()
    for exclusion in replay.cases.excluded:
        if exclusion.case_id in seen:
            raise BatchError(
                f"{path}: {_describe_replay(replay)} excludes case {exclusion.case_id!r} twice; a case is held "
                "out once, and a second entry is a second reason for one exclusion"
            )
        seen.add(exclusion.case_id)


def _require_source_line_ref(path: Path, described: str, ref: str | None) -> None:
    """The merge input is named by something that means one thing everywhere.

    The schema refuses every name that is not a fully-qualified `refs/...` one:
    `HEAD` and the rest of the pseudo-refs, a bare branch name, a revision
    expression. Each of those answers "where does the source line stand now"
    from whatever this working copy happens to be sitting on, so the same record
    would read promotable on the machine that ran it and stale in the checkout
    next door — and it is the first of those two that would be acted on.

    What a pattern cannot say is the rest of what `git check-ref-format`
    refuses. A name holding `..`, one ending in `.`, or one with a component
    beginning with `.` or ending in `.lock`, is one no ref can ever have: it
    resolves nowhere, in every checkout, forever. That reads as the one answer
    this package is careful to keep separate — a check nobody could make — when
    it is really a record that can never be checked at all, so it is refused
    here with the reason.

    The trailing `.` is a rule about the whole name and not about each of its
    parts, which is why it is asked separately: Git holds `refs/heads/re./lease`
    and refuses `refs/heads/release.`.

    Asked of a run being read and of one about to be started, from the same rule:
    the writer refuses the name before a harness is asked to measure anything,
    and the reader refuses the record wherever it is read afterwards.
    """

    if ref is None:
        return
    if (
        ".." in ref
        or ref.endswith(".")
        or any(part.startswith(".") or part.endswith(".lock") for part in ref.split("/"))
    ):
        raise BatchError(
            f"{path}: {described} names {ref!r} as the source line, which is not a name Git can hold "
            "(`git check-ref-format`); no ref will ever resolve to it, so whether the source line has moved "
            "since this run could not be answered in any checkout"
        )


def _require_distinct_handles(path: Path, replays: Sequence[Replay]) -> None:
    """One handle names one run.

    The handle is the whole of the durable link between a record and the work:
    `poll` takes it and nothing else. Two records under one handle are two runs
    a single report answers — the retry concludes with the numbers of the run it
    was retrying, and that run acquires a second record stating a second
    integration it never measured. Neither is detectable afterwards, because the
    reports agree.

    Per harness id, because a handle is only ever meaningful to the harness that
    issued it, and two of them numbering their runs from 1 have not collided.
    Null is absence rather than identity and repeats freely: it records a
    harness that issued no name, which only a concluded run may do
    (`_require_pollable`).
    """

    seen: dict[tuple[str, str], str] = {}
    for replay in replays:
        handle = replay.harness.handle
        if handle is None:
            continue
        key = (replay.harness.id, handle)
        earlier = seen.get(key)
        if earlier is not None:
            raise BatchError(
                f"{path}: {_describe_replay(replay)} and {earlier} are both recorded under {replay.harness.id} "
                f"handle {handle!r}; the handle is what a later process polls, so one naming two runs concludes "
                "the second from the first one's report — a retry is a new run, with the new handle its harness "
                "issued for it"
            )
        seen[key] = _describe_replay(replay)


def _require_pollable(path: Path, replay: Replay) -> None:
    """A run still going is a run something can still ask about.

    The handle is the only thing that connects this record to the work: a
    process that started a harness and exited is the ordinary case, so a running
    record with no handle names a run nobody can poll, conclude, or even find —
    which leaves the round permanently unmeasurable behind evidence that never
    arrives.
    """

    if replay.running and replay.harness.handle is None:
        raise BatchError(
            f"{path}: {_describe_replay(replay)} is still running and carries no harness handle; the handle is "
            "what a later process polls, so a run recorded without one can never be concluded — record the "
            "handle the harness issued, or record why the run ended"
        )


def _require_appended_runs(path: Path, replays: Sequence[Replay]) -> None:
    """Runs only append: rounds in order, attempts increasing within each.

    A round appearing again after a later one has been measured is evidence
    written back into a round the experiment had already left, which is the one
    ordering the seal exists to give this record (invariant 16). That the
    attempts are also the ones this file allocated is the next rule.
    """

    positions = [(replay.round_number, replay.attempt) for replay in replays]
    if positions != sorted(set(positions)):
        raise BatchError(
            f"{path}: runs are recorded at {positions}; they are appended one at a time, in round order and "
            "then attempt order, and none is ever removed or rewritten"
        )


def _require_allocated_once(
    path: Path,
    replays: Sequence[Replay],
    withdrawn: Sequence[WithdrawnRequest],
) -> None:
    """Every attempt of a round is allocated 1..N, to a run or to a withdrawal.

    Positions are what the harness is keyed on, so what this rule protects is
    that one of them names one request forever. Two holders are two requests a
    harness cannot tell apart — it answers the second with the run it began for
    the first, and numbers taken against one integration land under a record
    stating another. A gap is the same failure with the evidence missing: an
    allocation whose record is gone, taking its integration and its numbers with
    it, and indistinguishable from an attempt numbered wrongly.

    Runs and withdrawals are counted together because that is what a withdrawal
    is: a position that was allocated and will never carry a run.
    """

    holders: dict[int, dict[int, str]] = {}
    for described, round_number, attempt in [
        *((_describe_replay(replay), replay.round_number, replay.attempt) for replay in replays),
        *((_describe_withdrawal(taken), taken.round_number, taken.attempt) for taken in withdrawn),
    ]:
        held = holders.setdefault(round_number, {})
        earlier = held.get(attempt)
        if earlier is not None:
            raise BatchError(
                f"{path}: {described} and {earlier} both hold round {round_number} attempt {attempt}; a "
                "position is allocated once and names one request for good, because that is what the harness "
                "is keyed on — a second request wearing it would be answered with the first one's run"
            )
        held[attempt] = described
    for round_number, held in holders.items():
        numbered = sorted(held)
        if numbered != list(range(1, len(numbered) + 1)):
            raise BatchError(
                f"{path}: round {round_number} holds attempts {numbered}; they are allocated one at a time from "
                "1 — to a run, or to a request withdrawn before it became one — so a gap is an allocation whose "
                "record is missing rather than one that never happened"
            )


def _require_one_unfinished(path: Path, replays: Sequence[Replay]) -> None:
    """At most one run is unfinished, and it is the last one recorded.

    A round is measured against one integration at a time. Two runs going at
    once leave two answers about one round with nothing to choose between them,
    and an unfinished run with later ones after it is a result that was never
    going to be written — the record has moved on and nothing will conclude it.
    """

    running = [
        f"round {replay.round_number} attempt {replay.attempt}" for replay in replays if replay.running
    ]
    if not running:
        return
    if len(running) > 1:
        raise BatchError(
            f"{path}: {running} are all recorded as still running; one round is measured against one "
            "integration at a time, so a second run started under an unfinished one leaves two answers about "
            "one tree — conclude the run that is going before starting another"
        )
    if not replays[-1].running:
        raise BatchError(
            f"{path}: {running[0]} is still running while later runs are recorded after it; a run that was "
            "overtaken is one nothing will ever conclude — record how it ended"
        )


def _require_requestable(
    path: Path,
    replays: Sequence[Replay],
    withdrawn: Sequence[WithdrawnRequest],
    pending: PendingRun | None,
) -> None:
    """An outstanding request stands at the position the run it becomes will take.

    Which is the next attempt of its round that nothing else holds, with no run
    unfinished under it. Both halves are the same rule the runs follow, applied
    one step earlier — a round is measured against one integration at a time,
    and positions are allocated once — and they are asked here because a request
    that could not be answered is worse than one that was never made: `start`
    would resume it, the parser would then refuse the run it wrote, and the
    position would be stuck with no operation able to clear it but the
    withdrawal.
    """

    if pending is None:
        return
    described = _describe_pending(pending)
    if replays and replays[-1].running:
        raise BatchError(
            f"{path}: {described} is outstanding while {_describe_replay(replays[-1])} is still running; a round "
            "is measured against one integration at a time, so a request made under an unfinished run is a "
            "second answer about one tree waiting to be recorded"
        )
    # The newest round anything has been allocated in — a withdrawal counts, or a
    # request made after one in a later round would be a run written back into a
    # round this file has already moved past.
    behind = max(
        [replay.round_number for replay in replays] + [taken.round_number for taken in withdrawn],
        default=0,
    )
    if pending.round_number < behind:
        raise BatchError(
            f"{path}: {described} is outstanding while round {behind} has already been measured or requested; "
            "runs are appended in round order, so the run this request becomes could never be written after the "
            "ones already recorded"
        )
    attempt = _allocated(replays, withdrawn, pending.round_number) + 1
    if pending.attempt != attempt:
        raise BatchError(
            f"{path}: {described} is outstanding, and round {pending.round_number}'s next attempt is {attempt}; "
            "a request holds the position its run will take, and one holding any other position either skips an "
            "allocation or claims one a recorded run or withdrawal already has"
        )


def _require_pinned_candidates(
    path: Path,
    experiment: Experiment,
    replays: Sequence[Replay],
    withdrawn: Sequence[WithdrawnRequest],
    pending: PendingRun | None,
) -> None:
    """Every run names a round this experiment has, that round is candidate-ready,
    and the run exercised exactly the revision its seal pinned.

    This is invariant 16 read from the evidence side, and it is what stops a
    report from being reused. A run belongs to the round it names; a later round
    that wanted the same numbers would have to claim a candidate revision its own
    seal did not pin, which is refused here rather than discovered at a
    promotion. The base is checked with it, for the same reason the record states
    it: evidence carries its own baseline, and one naming a different base is
    measuring a different comparison.

    An outstanding request is held to the same rule, one step earlier: it is
    answered by a run, and a request naming a round with nothing pinned would be
    a run that could not be recorded once the harness answered. A withdrawn one
    is held to it a step later, for what it is kept for: an operator reading it
    is looking for a run that may have been started against exactly that
    candidate.
    """

    rounds = {round_.number: round_ for round_ in experiment.rounds}
    for described, round_number, integration in [
        *((_describe_replay(replay), replay.round_number, replay.integration) for replay in replays),
        *((_describe_withdrawal(taken), taken.round_number, taken.integration) for taken in withdrawn),
        *(
            ()
            if pending is None
            else ((_describe_pending(pending), pending.round_number, pending.integration),)
        ),
    ]:
        round_ = rounds.get(round_number)
        if round_ is None:
            raise BatchError(
                f"{path}: {described} measures a round {experiment.experiment_id} does not have (it has "
                f"{sorted(rounds)}); a run belongs to the round whose candidate it exercised"
            )
        if round_.seal is None:
            raise BatchError(
                f"{path}: {described} measures a round that carries no seal; a round is measured only once its "
                "candidate is pinned, because an open round's tip moves and evidence taken against it describes "
                "a tree the record cannot afterwards identify (invariant 16)"
            )
        pinned = round_.seal.candidate_revision
        if integration.candidate_revision != pinned:
            raise BatchError(
                f"{path}: {described} exercised {integration.candidate_revision[:12]}, while round "
                f"{round_.number} pinned {pinned[:12]}; a round's evidence names the revision that round's seal "
                "fixed, so evidence for one candidate is never carried over to another"
            )
        if integration.base_revision != experiment.base_revision:
            raise BatchError(
                f"{path}: {described} states base {integration.base_revision[:12]}, while "
                f"{experiment.experiment_id} was created on {experiment.base_revision[:12]}; a candidate is "
                "measured against the base its batch froze (invariant 15)"
            )


def _allocated(
    replays: Sequence[Replay],
    withdrawn: Sequence[WithdrawnRequest],
    round_number: int,
) -> int:
    """How many attempts of one round have been handed out, ever.

    Counted rather than read off the highest one, which is the same number: the
    parser refuses a round whose allocations are not 1..N before anything asks
    this (`_require_allocated_once`), so the count is the last position given
    out and the next is one above it. Withdrawals are in it because a position
    given up is still given out — that is the whole of what a withdrawal keeps.
    """

    return len([replay for replay in replays if replay.round_number == round_number]) + len(
        [taken for taken in withdrawn if taken.round_number == round_number]
    )


def _position(round_number: int, attempt: int) -> str:
    """How one run is named in an error: by the position it occupies, which is
    also its identity within the experiment."""

    return f"round {round_number} attempt {attempt}"


def _describe_replay(replay: Replay) -> str:
    return _position(replay.round_number, replay.attempt)


def _describe_pending(pending: PendingRun) -> str:
    """A request named by the position it holds, and marked as not yet a run."""

    return f"the request for {_position(pending.round_number, pending.attempt)}"


def _describe_withdrawal(taken: WithdrawnRequest) -> str:
    """A position named by the request that gave it up, which is what still holds
    it: no run of that round will ever be recorded there."""

    return f"the withdrawn request for {_position(taken.round_number, taken.attempt)}"


def _describe_outstanding(pending: PendingRun) -> str:
    """What an outstanding request means for a round nothing has measured.

    Not "nothing has run": the harness was asked, and something may be running
    against that integration right now. The difference matters to whoever reads
    it, because the two have different next steps — and only one of them is
    waiting on a machine somewhere.
    """

    return (
        f"{_describe_pending(pending)} is outstanding: its integration was pinned at "
        f"{pending.requested_at} and the harness's answer never reached this record, so a run may be going "
        "under it — start the replay again to record what the harness holds, or withdraw the request"
    )
