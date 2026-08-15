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
  run's selections by name. That request is not checked against what is stated,
  deliberately: the controller records what a harness answered with rather than
  what it was asked for, so a substitution stands visible beside the attempt it
  was meant to reproduce instead of being hidden behind the request — or barred,
  which would leave a case set the harness no longer holds unmeasurable forever.
  What the console owes is that the operator sees the request: the reproduction
  is stated in full when the run is asked for.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import BatchError, EvolutionError
from .replay import ReplayPlan, ReplayReport, ReplayRequest


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
        return self.plan

    def poll(self, handle: str) -> ReplayReport | None:
        if self.handle is not None and self.handle != handle:
            raise BatchError(
                f"the run on record is {handle!r} and this states numbers for {self.handle!r}; a harness answers "
                "for the run it was asked about, so a report named for another one is not this run's result — "
                "name the run this record has going, or leave it unnamed to answer for whatever that is"
            )
        return self.report
