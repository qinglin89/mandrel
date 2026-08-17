"""The replay harness this console can reach: the one an operator speaks for.

`replay.ReplayHarness` is the whole of what the controller may assume about the
thing that runs a case suite — it starts a run and names it, and polling that
name eventually reports. Nothing in this repository implements it, which is why
the four verbs that cross that boundary (`replay-start`, `replay-conclude`,
`assess-measure`, `assess-conclude`) could not be reached from the command line
at all: every other verb needs nothing outside this checkout.

What is implemented here is the smallest thing that makes the console
sufficient on its own: a harness whose answers are the operator's. They run the
evaluation however they run it — a script, a worktree, a rented machine, an
afternoon of reading — and state what it is running and what it reported. No
external protocol is invented, nothing is scheduled, and this repository still
never triggers an evaluation (contract invariant 9). A harness that speaks for
itself over a wire is a second implementation of the same Protocol, and none of
the four verbs would change to gain one.

Two properties of the Protocol are the operator's to hold up here, and each is
said rather than faked:

- *One key, one run.* A conforming harness asked twice for one position answers
  twice with the run it already began. Here the operator is that harness, so on
  a resume they name the run they started for the request that stands. Nothing
  on record can check them — a request holds a position, an integration and a
  prediction, and a handle exists only once something has been started. What the
  console does instead is refuse to invent one: a start with nothing stated
  writes the request, says what to run, and stops (`Unanswered`), so the handle
  that arrives afterwards is one the operator went and got.
- *Reproduce or refuse.* A rerun of a completed attempt is asked for the earlier
  run's selections by name, and a harness that cannot exercise them raises
  rather than running the nearest thing it can. This is the harness, so the
  check is here: a second attempt replaces the first as its round's evidence
  (`replay.describe_evidence`), so a substituted cohort does not stand *beside*
  the run it was meant to reproduce — it stands where that run's numbers stood,
  and a promotion is argued from it. The controller keeping what the harness
  answered with is what makes such a substitution legible after the fact; it is
  not a licence to make one. The operator sees the request either way: the
  reproduction is stated in full when the run is asked for.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .errors import BatchError, EvolutionError
from .replay import CaseSet, Evaluator, Harness, ReplayPlan, ReplayReport, ReplayRequest


class Unanswered(EvolutionError):
    """A run was asked for and the operator has not said what is running.

    Raised from `start`, which is after the controller has made its request
    durable — so this is not a failed start but an unfinished one: the position,
    the integration and the prediction are on record, and what is missing is the
    half only the harness can state. Carrying the request is the point. It is
    what an operator has to hand the evaluation they are about to run, and it
    reaches them no other way: the integration is computed as the request is
    written, and no reading recomputes it.

    An `EvolutionError` so that a caller who does not expect it still reports it
    as the refusal it is, rather than as a traceback.
    """

    def __init__(self, request: ReplayRequest) -> None:
        super().__init__(
            f"nothing was stated for {request.experiment_id} round {request.round_number} "
            f"attempt {request.attempt}; the request is on record and no run is"
        )
        self.request = request


@dataclass(frozen=True)
class StatedHarness:
    """A harness whose answers an operator supplies.

    Both answers are optional and their absence means different things, because
    the two calls are different questions. No `plan` is "I have not started
    anything yet" — the controller's request is written and this refuses to
    describe a run, which leaves exactly the durable outstanding-request state
    the boundary is designed around. No `report` is "still going", which is what
    a real harness answers for a run it has not finished, and the controller
    writes nothing for it.

    `handle` is the operator's statement of which run they are answering for. It
    is a precondition wherever it is given and nothing where it is not — the
    shape an experiment id has on a terminal decision — and what it prevents is
    one run's numbers recorded onto another run's record.
    """

    plan: ReplayPlan | None = None
    report: ReplayReport | None = None
    handle: str | None = None

    def start(self, request: ReplayRequest) -> ReplayPlan:
        if self.plan is None:
            raise Unanswered(request)
        _require_reproduced(request, self.plan)
        return self.plan

    def poll(self, handle: str) -> ReplayReport | None:
        if self.handle is not None and self.handle != handle:
            raise BatchError(
                f"the run on record is {handle!r} and this states numbers for {self.handle!r}; a harness answers "
                "for the run it was asked about, so a report named for another one is not this run's result — "
                "name the run this record has going, or leave it unnamed to answer for whatever that is"
            )
        return self.report


def _require_reproduced(request: ReplayRequest, stated: ReplayPlan) -> None:
    """A rerun exercises the selections it was asked for, or this refuses.

    The Protocol's own rule, held up where the Protocol puts it: the cohort, the
    evaluator and the configuration of a rerun are not a choice, and a harness
    that cannot resolve them raises rather than measuring the nearest thing it
    still has (`replay.ReplayHarness.start`).

    Enforced rather than reported because of where the numbers land. A second
    attempt of a round replaces the first as that round's evidence, so a
    substituted cohort is not a second reading beside the first — it is the one a
    promotion is argued from, wearing the provenance of the attempt it displaced.
    The record does keep what was answered, which is what makes a substitution
    findable afterwards; refusing it is what keeps it from having to be found.

    Everything the request states but the run's own name (`_unnamed`).
    """

    asked = request.reproduce
    if asked is None:
        return
    compared = (
        ("cases", stated.cases, asked.cases, _cases),
        ("evaluator", stated.evaluator, asked.evaluator, _evaluator),
        ("harness", _unnamed(stated.harness), _unnamed(asked.harness), _configuration),
    )
    # The values are compared and only the differing ones described: a sentence
    # states what an operator has to read, and two selections that differ in
    # something it does not say still differ.
    differences = [
        f"{what} {describe(here)} where {describe(there)} was asked for"
        for what, here, there, describe in compared
        if here != there
    ]
    if not differences:
        return
    raise BatchError(
        f"{request.experiment_id} round {request.round_number} attempt {request.attempt} reruns an attempt that "
        f"completed, and exercises what that one did: {'; '.join(differences)}. A rerun measures the same cohort "
        "over an integration that moved, so numbers from another one would not stand beside that attempt — they "
        "would replace it as this round's evidence and be what a promotion is argued from. State what that "
        "attempt ran, or give this position up (`replay-withdraw`): a different cohort is a different "
        "measurement, and it belongs to a round of its own"
    )


def _cases(cases: CaseSet) -> str:
    """A cohort as an operator restates it — every part they type, in full.

    Hashes unshortened here although they are shortened almost everywhere else:
    this sentence is read by someone about to retype them, and the exclusions are
    listed rather than counted for the same reason. A cohort that agrees on its
    id, its hash and its size and holds a different case out of it is a different
    cohort, and counting them would leave that difference unsayable.
    """

    excluded = "; ".join(f"{item.case_id} ({item.reason})" for item in cases.excluded)
    return f"{cases.case_set_id} {cases.case_set_sha256} ({cases.count} case(s), excluding {excluded or 'nothing'})"


def _evaluator(evaluator: Evaluator) -> str:
    return f"{evaluator.backend}/{evaluator.model} rubric {evaluator.rubric_revision or '<none>'}"


def _unnamed(harness: Harness) -> Harness:
    """The harness without its name for the run.

    What a rerun reproduces is which harness, at which revision, under which
    configuration. The handle is the one thing it does not: a rerun is a new run
    and takes the name its harness issues for it — the request carries the
    reproduced run's handle to say which run is being repeated, and the record
    refuses two runs wearing one name (`replay._require_distinct_handles`).
    """

    return replace(harness, handle=None)


def _configuration(harness: Harness) -> str:
    return f"{harness.id} {harness.revision} ({harness.config_sha256})"
