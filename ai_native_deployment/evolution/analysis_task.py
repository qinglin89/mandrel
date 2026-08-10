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

TASKS_DIRNAME = ".ai-tasks"
INDEX_FILENAME = "index.md"
ARCHIVE_DIRNAME = "archive"

TASK_ID_SUFFIX = "-analysis"
CONTRACT_PATH = "evolution/README.md"
PROPOSED_TASKS_DIRNAME = "proposed-tasks"

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


def task_exists(config: EvolutionConfig, task_id: str) -> bool:
    """Whether this task id is already taken, active or completed.

    The archive counts: a completed batch analysis must never be recreated as
    pending by a restarted freeze, which would reopen a closed decision.
    """

    return task_path(config, task_id).is_file() or archived_task_path(config, task_id).is_file()


def write_task(config: EvolutionConfig, spec: AnalysisTaskSpec) -> Path:
    """Create the task file. Never overwrites: an existing file may already
    carry a session log, and a freeze has no business replacing one."""

    path = task_path(config, spec.task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(render(spec))
    except FileExistsError as exc:
        raise EvolutionError(f"analysis task already exists: {path}") from exc
    return path


def append_index_row(config: EvolutionConfig, spec: AnalysisTaskSpec) -> bool:
    """Add the task's row to the active index, once.

    Returns False when a row for this id is already there, so a restarted
    freeze completing an interrupted one does not list the task twice.
    """

    path = index_path(config)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    row = f"| {spec.task_id} | pending | {spec.summary} |"
    if any(line.startswith("|") and f"| {spec.task_id} " in line for line in text.splitlines()):
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    if not text.strip():
        path.write_text("\n".join([INDEX_HEADING, "", *INDEX_TABLE_HEADER, row]) + "\n", encoding="utf-8")
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
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


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

# Batch analysis — {spec.batch_id}

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
- Write the disposition record to `{spec.findings_relative_path}`.
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
