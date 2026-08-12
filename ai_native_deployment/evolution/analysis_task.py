"""The pending batch-analysis task a freeze creates.

Contract invariant 9 draws the line this module sits on: automation may prepare
evidence and pending tasks, but not make policy decisions. A batch-analysis
task is preparation — it classifies and disposes evidence and is forbidden from
editing `canonical/` (invariant 6) — so it may be written straight into
`.ai-tasks/` as `pending`. A *change* task is a policy decision, so an analysis
session drafts it under `batches/<batch-id>/proposed-tasks/` and a human admits
it by moving it. The generated task says so, because the session that reads it
is cold and the drafts directory is the only place a proposal can legally wait.

Shapes come from `.ai-protocol/meta/taskfile.md` (frontmatter, body sections,
index row) and the intake contract's field table. Nothing here is deploy-owned:
`.ai-tasks/` is target-local, ignored, operational state.

The `.ai-tasks/` mechanics themselves — where a task lives, whether the id is
taken, publishing a file without overwriting one, and adding a row to the active
index exactly once — are shared with the change tasks `experiments.py` admits
from a batch's drafts. They live here rather than being written twice: the index
rewrite is atomic because a truncated one loses rows belonging to unrelated
tasks, and that is not a rule worth restating in a second module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import EvolutionConfig
from .errors import EvolutionError
from .schema import format_rfc3339
from .state import atomic_create_text, atomic_write_text

TASKS_DIRNAME = ".ai-tasks"
INDEX_FILENAME = "index.md"
ARCHIVE_DIRNAME = "archive"

TASK_ID_SUFFIX = "-analysis"
CONTRACT_PATH = "evolution/README.md"
PROPOSED_TASKS_DIRNAME = "proposed-tasks"

# The taskfile lifecycle's terminal status. A task carrying it (or archived,
# which is where close-out moves it) has finished; anything else is in flight.
STATUS_COMPLETED = "completed"

# One estimated session is one effective context window (taskfile schema). Ten
# reports of bounded evaluation artifacts plus their clustering is about that;
# the cap is the intake contract's "flag scope above 5" turned into a ceiling,
# since a generated estimate has nobody to flag it to.
TASKS_PER_SESSION = 10
MAX_SESSION_EST = 5

PREFETCH = (".ai/features.md", ".ai/modules.md")

INDEX_HEADING = "# Active tasks"
INDEX_TABLE_HEADER = ("| Task | Status | Summary |", "|---|---|---|")
INDEX_PLACEHOLDER = "(none)"


@dataclass(frozen=True)
class ReleaseAssessment:
    """The release a generated task also owes a reading of, when there is one.

    Identity only, and deliberately: the two cohorts, their exclusions and their
    comparability are derived from frozen manifests and Git on every read
    (`assessment.py`), and a clone that cannot resolve what some target held
    derives different exclusions from the machine that froze the batch. Rendering
    them into the task text would make the file machine-dependent — a repair on
    another machine would write a different task — and would freeze a reading the
    session is supposed to take for itself.

    `standing` is left out for the same reason twice over: a rollback landing
    between the freeze and the reading changes it, and the answer is the
    lineage's to give (`aii-2 evolution status`).
    """

    batch_id: str
    experiment_id: str
    revision: str
    round_number: int
    candidate_revision: str
    merge_input_revision: str
    merge_input_ref: str
    tree: str
    planned_targets: tuple[str, ...]
    assessment_relative_path: str


@dataclass(frozen=True)
class AnalysisTaskSpec:
    """Everything the generated task states about its batch.

    All of it is identity, counts, and paths — never report content, which the
    contract's task requirements keep out of taskfiles.
    """

    task_id: str
    batch_id: str
    manifest_relative_path: str
    proposed_tasks_relative_path: str
    findings_relative_path: str
    closure_relative_path: str
    artifacts_root: str
    task_count: int
    report_count: int
    runner_protocol_revision: str | None
    config_sha256: str
    forced: bool
    force_justification: str | None
    # The release this batch is the first cohort after, when it follows one. None
    # for the first batch a repository ever freezes and for every batch whose
    # predecessor changed nothing — a `no-change` conclusion fabricates no
    # revision (invariant 7), so there is no upgrade effect to look for.
    release: ReleaseAssessment | None = None

    @property
    def summary(self) -> str:
        tail = (
            f" Assess the {self.release.batch_id} release from this cohort."
            if self.release is not None
            else ""
        )
        return (
            f"Classify and dispose the evidence in {self.batch_id} "
            f"({self.task_count} unique completed tasks); dispositions only, no canonical edits.{tail}"
        )

    @property
    def session_est_total(self) -> int:
        """One session per bounded group of reports, plus one for a release
        assessment when the batch owes one: it is a second reading of the same
        cohort against a different question, not a paragraph in the first."""

        needed = -(-max(self.task_count, 1) // TASKS_PER_SESSION)
        return min(needed + (1 if self.release is not None else 0), MAX_SESSION_EST)


def analysis_task_id(batch_id: str, created_at: datetime) -> str:
    """`<YYYY-MM-DD>-<batch-id>-analysis`, the date-prefixed slug the taskfile
    schema requires. The batch id makes it unique without a collision suffix."""

    return f"{format_rfc3339(created_at)[:10]}-{batch_id}{TASK_ID_SUFFIX}"


def tasks_root(config: EvolutionConfig) -> Path:
    return config.repo_root / TASKS_DIRNAME


def task_path(config: EvolutionConfig, task_id: str) -> Path:
    return tasks_root(config) / f"{task_id}.md"


def archived_task_path(config: EvolutionConfig, task_id: str) -> Path:
    return tasks_root(config) / ARCHIVE_DIRNAME / f"{task_id}.md"


def index_path(config: EvolutionConfig) -> Path:
    return tasks_root(config) / INDEX_FILENAME


def existing_task_path(config: EvolutionConfig, task_id: str) -> Path | None:
    """Where this task currently lives, active or archived, if anywhere."""

    for path in (task_path(config, task_id), archived_task_path(config, task_id)):
        if path.is_file():
            return path
    return None


def task_exists(config: EvolutionConfig, task_id: str) -> bool:
    """Whether this task id is already taken, active or completed.

    The archive counts: a completed batch analysis must never be recreated as
    pending by a restarted freeze, which would reopen a closed decision.
    """

    return existing_task_path(config, task_id) is not None


def task_status(path: Path) -> str | None:
    """The `status:` value in a task file's frontmatter, or None when the file
    does not carry one.

    None is the answer for an unreadable or shapeless file too, and callers are
    expected to treat it as "not completed": a task whose lifecycle cannot be
    read has not been shown to have finished.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return frontmatter(text).get("status") or None


def task_finished(config: EvolutionConfig, task_id: str) -> bool:
    """Whether this machine's copy of the task has finished.

    An archived task counts as finished by location: close-out moves a task to
    `archive/` only after it reaches `completed` (taskfile schema §7-8), and the
    active file is gone by then. False for a task that is not here at all —
    `.ai-tasks/` is machine-local, so absence is not evidence of anything, and
    every caller has its own reading of what a missing task means.
    """

    if existing_task_path(config, task_id) is None:
        return False
    active = task_path(config, task_id)
    if active.is_file():
        return task_status(active) == STATUS_COMPLETED
    return True


@dataclass(frozen=True)
class Frontmatter:
    """A task file's frontmatter block, read strictly enough to validate one.

    `fields` alone answers "what does this file say its status is", which is all
    a lifecycle reading needs. Admitting a draft is the other kind of question —
    is this a task file at all — and the two facts that answer it are exactly the
    ones a field mapping cannot carry: whether the block was ever closed, and
    where the body starts.
    """

    fields: dict[str, str]
    # Keys the block declares more than once. First occurrence wins in `fields`,
    # which is what a reader scanning down sees; a validator has to know the file
    # said two things.
    duplicated: tuple[str, ...]
    # Whether the file opens with a `---` line at all. Separate from `closed`
    # because the two are different damage: no block is a file that is not a task
    # file, an unclosed one is a task file someone truncated.
    present: bool
    # Whether the opening `---` has a closing one. An unterminated block has no
    # body at all: everything below it is still frontmatter as far as the shape
    # goes, however much it looks like sections.
    closed: bool
    # Line index the body starts at — 0 when the file carries no block, so a
    # caller that inserts or scans "in the body" of a shapeless file works on the
    # whole of it rather than on nothing.
    body_start: int


def frontmatter(text: str) -> dict[str, str]:
    """The task file's frontmatter fields, or empty when it carries none."""

    return parse_frontmatter(text).fields


def parse_frontmatter(text: str) -> Frontmatter:
    """Read the frontmatter block, and how well-formed it is.

    A flat `key: value` reading, which is all the taskfile schema's frontmatter
    is and all any caller here needs — the lifecycle status, and the id an
    admitted draft declares. First occurrence wins: a file with the field twice
    says two things, and the earlier one is what a reader scanning down sees.
    """

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return Frontmatter(fields={}, duplicated=(), present=False, closed=False, body_start=0)
    fields: dict[str, str] = {}
    duplicated: list[str] = []
    for offset, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return Frontmatter(
                fields=fields,
                duplicated=tuple(duplicated),
                present=True,
                closed=True,
                body_start=offset + 1,
            )
        key, separator, value = line.partition(":")
        if not separator:
            continue
        name = key.strip()
        if name in fields:
            if name not in duplicated:
                duplicated.append(name)
            continue
        fields[name] = value.strip()
    return Frontmatter(
        fields=fields,
        duplicated=tuple(duplicated),
        present=True,
        closed=False,
        body_start=len(lines),
    )


def completion(config: EvolutionConfig, spec: AnalysisTaskSpec) -> bool | None:
    """Whether this machine's copy of the task says the work finished.

    Three answers, and the third is the one that matters: None means this
    machine does not have the task at all. `.ai-tasks/` is machine-local and
    ignored, so that is the ordinary state of every other clone — it is not
    evidence either way, and a caller that reads it as "finished" concludes
    from a missing file that work it never saw is done.

    An archived task counts as finished by location: close-out moves a task to
    `archive/` only after it reaches `completed` (taskfile schema §7-8), and the
    active file is gone by then.

    The file is identified as this batch's generated task before any of that is
    read, because a status line is only a lifecycle when it belongs to the task
    the manifest names. Everything downstream rests on this one reading: it
    releases the open-batch guard, and it is the sole thing the committed
    closure record attests to — so an unrelated, truncated, or replaced file
    carrying `status: completed` at this path would close a batch whose analysis
    never happened. A mismatch raises rather than reading as unfinished: it is
    damage a human has to resolve, and a silent "still in flight" would hold the
    batch open forever without ever saying why. The spec is taken whole for the
    same reason `assert_generated` needs it — the markers are the task id, the
    batch heading, and the manifest path together.
    """

    if existing_task_path(config, spec.task_id) is None:
        return None
    assert_generated(config, spec)
    return task_finished(config, spec.task_id)


def assert_generated(config: EvolutionConfig, spec: AnalysisTaskSpec) -> None:
    """Refuse to treat an unrelated file at this task id as the generated task.

    A freeze that finds the file present declares the step complete, so what is
    at that path decides whether the batch has an analysis task at all. The
    markers checked here are the ones no working session removes — the task's
    own id, its heading, and the manifest it must analyze — so an ordinary
    claimed and logged task passes while a truncated, replaced, or unrelated
    file is reported instead of silently accepted.

    Nothing is repaired: a file at this path may already carry a session log,
    and overwriting one to satisfy a freeze would destroy the record it exists
    to keep.
    """

    path = existing_task_path(config, spec.task_id)
    if path is None:
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvolutionError(f"cannot read the analysis task {path}: {exc}") from exc
    missing = [marker for marker in _markers(spec) if marker not in text]
    if not text.startswith("---") or missing:
        raise EvolutionError(
            f"{path} is not the analysis task generated for {spec.batch_id} (missing {missing or ['frontmatter']}); "
            "restore it from the batch manifest, or remove the file so the next run can write it again"
        )


def write_task(config: EvolutionConfig, spec: AnalysisTaskSpec) -> Path:
    """Create the analysis task file, published whole."""

    return publish_task(config, spec.task_id, render(spec), description="analysis task")


def publish_task(config: EvolutionConfig, task_id: str, text: str, *, description: str) -> Path:
    """Create one task file under `.ai-tasks/`, published whole.

    Never overwrites: an existing file may already carry a session log, and
    neither a freeze nor an admission has any business replacing one. The
    creation is what checks — looking first and writing second leaves a window,
    and a task file created in it is precisely the one this refuses to destroy.
    The content lands through a temporary file, so an interruption leaves no
    partial task behind for a later run to accept as complete.
    """

    path = task_path(config, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not atomic_create_text(path, text):
        raise EvolutionError(f"{description} already exists: {path}")
    return path


def append_index_row(config: EvolutionConfig, spec: AnalysisTaskSpec) -> bool:
    """Add the analysis task's row to the active index, once."""

    return append_row(config, spec.task_id, spec.summary)


def append_row(config: EvolutionConfig, task_id: str, summary: str) -> bool:
    """Add one `pending` row to the active index, once.

    Returns False when a row for this id is already there, so a restarted freeze
    or a redone admission completing an interrupted one does not list the task
    twice. The rewrite is atomic: the index is the operator's list of active
    work, and a truncated one loses rows belonging to other tasks entirely.
    """

    path = index_path(config)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    row = f"| {task_id} | pending | {summary} |"
    if any(line.startswith("|") and f"| {task_id} " in line for line in text.splitlines()):
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    if not text.strip():
        atomic_write_text(path, "\n".join([INDEX_HEADING, "", *INDEX_TABLE_HEADER, row]) + "\n")
        return True

    # A `(none)` placeholder means the table is absent, not that a row exists.
    lines = [line for line in text.splitlines() if line.strip() != INDEX_PLACEHOLDER]
    last_row = max((index for index, line in enumerate(lines) if line.startswith("|")), default=None)
    if last_row is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend([*INDEX_TABLE_HEADER, row])
    else:
        lines.insert(last_row + 1, row)
    atomic_write_text(path, "\n".join(lines) + "\n")
    return True


def task_heading(batch_id: str) -> str:
    return f"# Batch analysis — {batch_id}"


def _markers(spec: AnalysisTaskSpec) -> tuple[str, ...]:
    """What `render` puts in every generated task and a session keeps."""

    return (f"id: {spec.task_id}", task_heading(spec.batch_id), spec.manifest_relative_path)


def render(spec: AnalysisTaskSpec) -> str:
    """The task file, conforming to the taskfile schema.

    `status: pending` and an empty `claimed-by` are the intake contract's
    values: the lifecycle starts when a session claims the task, not when
    automation writes it.
    """

    revision = spec.runner_protocol_revision or "unknown — no release tag reachable from HEAD at freeze time"
    # Collapsed to one line so a multi-line justification stays one bullet. The
    # manifest keeps the operator's text verbatim.
    justification = " ".join((spec.force_justification or "").split())
    force_line = f"- Below-target batch, frozen on human justification: {justification}\n" if spec.forced else ""
    release_scope = _release_scope(spec.release)
    release_acceptance = _release_acceptance(spec.release)
    return f"""---
id: {spec.task_id}
status: pending
session-est: 0/{spec.session_est_total}
blockers: []
prefetch: [{", ".join(PREFETCH)}]
claimed-by:
---

{task_heading(spec.batch_id)}

## Goal

Classify and dispose the evidence frozen in evolution batch `{spec.batch_id}` —
{spec.task_count} unique completed task(s) across {spec.report_count} report(s) —
under the normative contract `{CONTRACT_PATH}`. Produce dispositions and, where
warranted, change-task drafts; nothing else. Concluding that no change is
justified is a valid outcome (invariant 7).

## Scope

- Normative contract: `{CONTRACT_PATH}`. Cite it; do not work from the `.ai/`
  snapshot's summary of it.
- Batch: `{spec.batch_id}`. Immutable membership: `{spec.manifest_relative_path}`.
  Use only the reports it names for batch-level claims, and treat it as
  read-only — a late report belongs to a later batch (invariant 3).
- Runner protocol revision: {revision}. It stays fixed for this task, and a
  candidate revision never governs the run that creates it (invariant 8).
- Admission policy that formed this batch: `evolution/config.toml` at sha256
  `{spec.config_sha256}`.
{force_line}- Raw evaluation bundles are machine-local runtime data under
  `{spec.artifacts_root}/`; they are ignored by Git and may be absent on
  another machine. Keep report content out of this task file beyond bounded
  summaries and references (invariant 11).
- Check cohort coherence before comparing anything: the manifest carries each
  report's evaluator/rubric revision, runner protocol revision, and role
  models. Separate materially different revisions or account for them
  explicitly (invariant 5).
- Give every finding cluster exactly one primary disposition from the
  contract's triage table, and record task count, repository/task-type
  coverage, recurrence, counterexamples, confidence, affected revisions,
  expected benefit, and regression risk.
- Write the disposition record to `{spec.findings_relative_path}`. It does not
  end the analysis by existing: this stage stays open until this task reaches
  `completed`, at which point the next `aii-2 evolution` run publishes
  `{spec.closure_relative_path}` from that status. Never write that record by
  hand; it attests to a lifecycle, not to a file. Completing this task releases
  the dispositions to the admission gate; the batch itself stays current — and
  no later cohort forms (invariant 14) — until its outcome is recorded, which is
  a decision later than and separate from this task.
- Draft each accepted `protocol-candidate` (and any other change task this
  analysis concludes is warranted) as a schema-conforming task file at
  `{spec.proposed_tasks_relative_path}/<draft-id>.md`, where `<draft-id>` is a
  kebab-case slug and is that proposal's identity. Admission copies the draft
  into the active pool as it stands, so it is a whole task file and the gate
  checks it as one: frontmatter opened and closed, declaring its own
  date-prefixed `id`, `status: pending`, `session-est: 0/<total>`,
  `blockers: []` and an empty `claimed-by`; then `## Goal`, `## Scope`,
  `## Acceptance`, and an empty `## Session log`, each of those stated exactly
  once and standing at column 0, and no admission section of its own — admission
  adds that one, naming the experiment and the ref to work on. An indented line
  that looks like a heading is refused: Markdown reads it as code and the session
  working the copy reads it as a section. Drafts are inert until a human admits
  a group of them into an experiment, which copies each into
  `.ai-tasks/` and adds its index row; writing one straight into `.ai-tasks/`
  as `pending` would put it in the dispatch pool and bypass the human admission
  gate (invariant 9).
- Analysis is not implementation: this task must not edit `canonical/`
  (invariant 6). Accepted recommendations become separate admitted tasks.
{release_scope}
## Acceptance

- `{spec.findings_relative_path}` records every finding cluster with exactly one
  primary disposition, its evidence, and its confidence.
- Cohort coherence is stated: which revisions are represented, and how mixed
  cohorts were separated or accounted for.
- Recurrence claims name the unique completed tasks they rest on, counted from
  the manifest — never a single report (invariant 1).
- Zero changes under `canonical/`; every proposed change exists only as a draft
  under `{spec.proposed_tasks_relative_path}/`, and `.ai-tasks/` gains no file
  from this session.
- Unresolved risks and open evidence questions are stated explicitly.
{release_acceptance}
## Session log
"""


def _release_scope(release: ReleaseAssessment | None) -> str:
    """What a post-release batch owes beyond its dispositions.

    Empty for a batch that follows no promotion, so the ordinary generated task
    is exactly what it was. The identity here is the promoted batch's outcome
    restated; everything derived — the cohorts, the exclusions, the comparability
    — is left to be read at the time, because it depends on Git and on the
    machine reading it.
    """

    if release is None:
        return ""
    targets = ", ".join(release.planned_targets) or "none recorded"
    return f"""
### Release assessment — {release.batch_id}

This is the first frozen cohort after a promotion, so it also owes one reading of
whether that release improved the work (contract: Release assessment). It is a
separate question from the dispositions above and neither answer decides the
other.

- The release: batch `{release.batch_id}`, experiment `{release.experiment_id}`,
  round {release.round_number}, on source line `{release.merge_input_ref}`.
  Promotion revision:
  `{release.revision}`. Candidate measured:
  `{release.candidate_revision}`. Tree carried:
  `{release.tree}`.
- The pair to compare is stated, not reconstructed. The pre-promotion revision is
  the promotion's merge input:
  `{release.merge_input_revision}`; the promoted one is the promotion revision
  above. Read both from that batch's `outcome.json`, never from the experiment's
  own promotion record — a promotion made at experiment schema version 1 states
  its revision alone.
- Ask `aii-2 evolution status` whether the source line still carries it. A
  promotion a rollback reversed is a different question from a standing one — the
  cohort after the reversal was produced at a revision the change is not in — and
  the assessment states which of the two it answered.
- Cohorts come from frozen manifests only: this batch's and
  `{release.batch_id}`'s. Which side a report is on is a per-report reading of
  `provenance.effective_revision` — what that target actually held — and never
  the targets the promotion was *planned* for ({targets}), which is a plan and
  not a deployment.
- Verdicts are `improved`, `neutral`, `regressed`, or `inconclusive`. The first
  three are directional and rest on one of two kinds of evidence. The cohorts
  carry a direction only when nothing else explains it: one evaluator, rubric,
  protocol revision and role configuration across both sides, the same shape of
  work, both sides at or above the configured minimum unique-task count, and a
  goal quantity measured on both. Mixed provenance and too small a sample are
  reasons to know less, never evidence against the release, so they produce
  `inconclusive` (invariants 1, 4, 5).
- Expect the shape of the work to be the facet that stops a cohort-only claim. No
  manifest states what kind of task a report judged, and the two cohorts are two
  different task sets, so the numbers they came to are explained by the work at
  least as well as by the release. A direction — in either sign — is settled by
  the pinned two-revision counterfactual: the pre-promotion and promoted
  revisions over one case set with one evaluator, which is the only comparison in
  which the release is the only difference. `regressed` rests on it in every
  case. Without one, record what the cohorts show and read it `inconclusive`.
- Record the reading at
  `{release.assessment_relative_path}`, with the denominators and every exclusion
  visible. Retaining or rolling back the release is a human decision recorded on
  that artifact afterwards; this task produces the evidence and the verdict, not
  the settlement.
"""


def _release_acceptance(release: ReleaseAssessment | None) -> str:
    """The acceptance lines that go with `_release_scope`, or none at all.

    Ends with a newline so the section that follows keeps its blank line: the
    empty case supplies it from the template, and a non-empty one has to bring
    its own.
    """

    if release is None:
        return ""
    return f"""- `{release.assessment_relative_path}`
  records one reading of the `{release.batch_id}` release: the cohorts and their
  denominators, every exclusion with its reason, the comparability facets, the
  verdict, and the rationale — or an explicit `inconclusive` with what was
  missing.
- No directional verdict rests on mixed provenance, work whose shape nothing
  states, an under-sized cohort, or an unmeasured quantity — a direction the
  cohorts cannot carry is either settled by a completed counterfactual or read
  `inconclusive`.
"""
