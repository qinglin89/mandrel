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
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import EvolutionConfig
from .errors import EvolutionError
from .schema import format_rfc3339
from .state import atomic_write_text

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

    @property
    def summary(self) -> str:
        return (
            f"Classify and dispose the evidence in {self.batch_id} "
            f"({self.task_count} unique completed tasks); dispositions only, no canonical edits."
        )

    @property
    def session_est_total(self) -> int:
        needed = -(-max(self.task_count, 1) // TASKS_PER_SESSION)
        return min(needed, MAX_SESSION_EST)


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
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, separator, value = line.partition(":")
        if separator and key.strip() == "status":
            return value.strip() or None
    return None


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
    active = task_path(config, spec.task_id)
    if active.is_file():
        return task_status(active) == STATUS_COMPLETED
    return True


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
    """Create the task file, published whole.

    Never overwrites: an existing file may already carry a session log, and a
    freeze has no business replacing one. Written through a temporary file and
    renamed into place, so an interruption leaves no partial task behind for a
    later run to accept as complete.
    """

    path = task_path(config, spec.task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise EvolutionError(f"analysis task already exists: {path}")
    atomic_write_text(path, render(spec))
    return path


def append_index_row(config: EvolutionConfig, spec: AnalysisTaskSpec) -> bool:
    """Add the task's row to the active index, once.

    Returns False when a row for this id is already there, so a restarted
    freeze completing an interrupted one does not list the task twice. The
    rewrite is atomic: the index is the operator's list of active work, and a
    truncated one loses rows belonging to other tasks entirely.
    """

    path = index_path(config)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    row = f"| {spec.task_id} | pending | {spec.summary} |"
    if any(line.startswith("|") and f"| {spec.task_id} " in line for line in text.splitlines()):
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
  close the batch by existing: this batch stays open — and no later batch can
  form (invariant 12) — until this task reaches `completed`, at which point the
  next `aii-2 evolution` run publishes `{spec.closure_relative_path}` from that
  status. Never write that record by hand; it attests to a lifecycle, not to a
  file.
- Draft each accepted `protocol-candidate` (and any other change task this
  analysis concludes is warranted) as a schema-conforming task file under
  `{spec.proposed_tasks_relative_path}/`. Drafts are inert until a human moves
  one into `.ai-tasks/` and adds its index row; writing one straight into
  `.ai-tasks/` as `pending` would put it in the dispatch pool and bypass the
  human admission gate (invariant 9).
- Analysis is not implementation: this task must not edit `canonical/`
  (invariant 6). Accepted recommendations become separate admitted tasks.

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

## Session log
"""
