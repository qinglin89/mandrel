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
about it lives in the memory of the process that started it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .config import REPLAYS_SCHEMA_FILENAME, EvolutionConfig
from .errors import BatchError
from .lineage import Experiment
from .revisions import ref_tip
from .schema import load_schema, validate_or_raise

REPLAYS_FILENAME = "replays.json"
REPLAYS_SCHEMA_VERSION = 1

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
        """What a harness was asked to run, rebuilt from the record.

        The record is the durable form of the request, so polling or repeating a
        run needs nothing that was held in the memory of the process that started
        it.
        """

        return ReplayRequest(
            experiment_id=self.experiment_id,
            round_number=self.round_number,
            attempt=self.attempt,
            integration=self.integration,
        )


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
    """What a harness is asked to exercise: one pinned integration, and which
    round of which experiment it belongs to.

    The expectation recorded beside a run is deliberately not in here. It is a
    human prediction, kept so the numbers cannot be read back onto it afterwards;
    handing it to the thing being measured would make it an instruction.
    """

    experiment_id: str
    round_number: int
    attempt: int
    integration: Integration


@dataclass(frozen=True)
class ReplayPlan:
    """A harness's answer to a request: what it will run, and its name for the run.

    The cohort and the evaluator come from the harness rather than the
    controller, which owns neither the case suite nor the rubric. What the
    controller owns is the integration it pinned, and the record that states both.
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
        """Begin exercising `request.integration`, and say what is being run."""

    def poll(self, handle: str) -> ReplayReport | None:
        """The measurements for `handle`, or None while that run is still going."""


def read_replays(config: EvolutionConfig, experiment: Experiment) -> tuple[Replay, ...]:
    """Every run recorded against one experiment, in the order they were started.

    Empty covers both "the file lists nothing" and "no file yet": an experiment
    nobody has measured is the ordinary state of every round before its first
    replay, and the two are the same fact about the evidence.
    """

    path = replays_path(experiment)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ()
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
) -> tuple[Replay, ...]:
    """One experiment's replay records, validated and checked against the rounds
    they claim to have measured.

    The implemented JSON Schema subset has no cross-field conditionals and no
    view of the experiment record at all, so the rules that make these runs one
    readable history live here: runs that only append, one unfinished run at a
    time, a result whose shape matches how it ended, and — the load-bearing one —
    a candidate revision that is the one the named round actually pinned. Each is
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
    _require_appended_runs(path, replays)
    _require_one_unfinished(path, replays)
    _require_pinned_candidates(path, experiment, replays)
    return replays


def describe_evidence(config: EvolutionConfig, experiment: Experiment) -> Evidence:
    """What the experiment's current round has been measured by. Read-only.

    Precedence, and each step is a different question a promotion would ask:

    1. a completed run for this round whose merge input has not moved — the one
       state that supports a promotion, and it is not unmade by a second run
       started beside it, because evidence that is still exact stays exact;
    2. a run still going;
    3. the newest run, when that is the one that failed;
    4. a completed run that no longer describes the tree in question — the round
       moved on, or the source line did;
    5. nothing yet.
    """

    replays = read_replays(config, experiment)
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
    if newest_complete is not None and moved is not True:
        # `None` is a check this checkout could not make, which is not the same
        # answer as agreement and is not reported as one: the state describes the
        # run, and `unverified` is what stops it short of a promotion.
        return Evidence(
            **common,
            state=EVIDENCE_COMPLETE,
            replay=newest_complete,
            drift=(),
            unverified=() if moved is False else (note,),
        )

    # Nothing below can also be carrying `unsealed`: every one of these branches
    # needs a run that names this round, and an unsealed round has none.
    if here and here[-1].running:
        return Evidence(**common, state=EVIDENCE_RUNNING, replay=here[-1], drift=(), unverified=())
    if here and here[-1].failed:
        return Evidence(
            **common,
            state=EVIDENCE_FAILED,
            replay=here[-1],
            drift=(f"the newest run of round {last.number} failed: {here[-1].result.detail}",),
            unverified=(),
        )
    if newest_complete is not None:
        return Evidence(**common, state=EVIDENCE_STALE, replay=newest_complete, drift=(note,), unverified=())
    if replays:
        stale = replays[-1]
        return Evidence(
            **common,
            state=EVIDENCE_STALE,
            replay=stale,
            drift=unsealed
            + (
                f"the newest evidence measured round {stale.round_number}, and this experiment is on round "
                f"{last.number}; a round's evidence describes the candidate that round pinned and never the "
                "next one's",
            ),
            unverified=(),
        )
    return Evidence(
        **common,
        state=EVIDENCE_INCOMPLETE,
        replay=None,
        drift=unsealed or (f"round {last.number} has not been replayed",),
        unverified=(),
    )


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
        integration=Integration(
            base_revision=integration["base_revision"],
            candidate_revision=integration["candidate_revision"],
            merge_input_revision=integration["merge_input_revision"],
            merge_input_ref=integration["merge_input_ref"],
            tree=integration["tree"],
        ),
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
    _require_pollable(path, replay)
    return replay


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
    """Runs only append: rounds in order, attempts 1..N within each.

    A gap in the attempts is a run whose record is gone, taking its integration
    and its numbers with it, and nothing distinguishes that from an attempt
    allocated wrongly. A round appearing again after a later one has been
    measured is evidence written back into a round the experiment had already
    left, which is the one ordering the seal exists to give this record
    (invariant 16).
    """

    positions = [(replay.round_number, replay.attempt) for replay in replays]
    if positions != sorted(set(positions)):
        raise BatchError(
            f"{path}: runs are recorded at {positions}; they are appended one at a time, in round order and "
            "then attempt order, and none is ever removed or rewritten"
        )
    attempts: dict[int, list[int]] = {}
    for round_number, attempt in positions:
        attempts.setdefault(round_number, []).append(attempt)
    for round_number, numbered in attempts.items():
        if numbered != list(range(1, len(numbered) + 1)):
            raise BatchError(
                f"{path}: round {round_number} has attempts {numbered}; they are allocated one at a time from "
                "1, so a gap is a run whose record is missing rather than one that never happened"
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


def _require_pinned_candidates(path: Path, experiment: Experiment, replays: Sequence[Replay]) -> None:
    """Every run names a round this experiment has, that round is candidate-ready,
    and the run exercised exactly the revision its seal pinned.

    This is invariant 16 read from the evidence side, and it is what stops a
    report from being reused. A run belongs to the round it names; a later round
    that wanted the same numbers would have to claim a candidate revision its own
    seal did not pin, which is refused here rather than discovered at a
    promotion. The base is checked with it, for the same reason the record states
    it: evidence carries its own baseline, and one naming a different base is
    measuring a different comparison.
    """

    rounds = {round_.number: round_ for round_ in experiment.rounds}
    for replay in replays:
        described = _describe_replay(replay)
        round_ = rounds.get(replay.round_number)
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
        if replay.integration.candidate_revision != pinned:
            raise BatchError(
                f"{path}: {described} exercised {replay.integration.candidate_revision[:12]}, while round "
                f"{round_.number} pinned {pinned[:12]}; a round's evidence names the revision that round's seal "
                "fixed, so evidence for one candidate is never carried over to another"
            )
        if replay.integration.base_revision != experiment.base_revision:
            raise BatchError(
                f"{path}: {described} states base {replay.integration.base_revision[:12]}, while "
                f"{experiment.experiment_id} was created on {experiment.base_revision[:12]}; a candidate is "
                "measured against the base its batch froze (invariant 15)"
            )


def _position(round_number: int, attempt: int) -> str:
    """How one run is named in an error: by the position it occupies, which is
    also its identity within the experiment."""

    return f"round {round_number} attempt {attempt}"


def _describe_replay(replay: Replay) -> str:
    return _position(replay.round_number, replay.attempt)
