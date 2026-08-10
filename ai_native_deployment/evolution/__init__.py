"""Local protocol-evolution controller.

`evolution/README.md` is the normative contract; this package implements the
mechanical part of it — discovering already-complete orch-hub reports,
validating and staging them as unique completed tasks, freezing an immutable
cohort when admission policy allows, preparing that cohort's pending analysis
task, and deriving where the lifecycle currently stands.

What it deliberately does not do: run or schedule an evaluation, and decide
anything a human is supposed to decide. Batch formation, change admission, and
promotion stay human gates (contract invariant 9). Automation prepares evidence
and pending analysis tasks; a human triggers the freeze and admits every
proposed canonical change.

The names below are the package's stable surface. Three things are reached
through their modules rather than re-exported, because the bare name says
nothing on its own: `phase.describe` (the derived lifecycle status),
`lineage.describe` (the derived batch/experiment lineage), and the
`render.format_*` functions (operator-facing text for the CLI).
"""

from __future__ import annotations

from .analysis_task import AnalysisTaskSpec
from .batches import (
    AdmissionDecision,
    Batch,
    FreezeResult,
    StartResult,
    evaluate_admission,
    freeze,
    load_batches,
    open_batch,
    start,
)
from .config import EvolutionConfig, load_config
from .errors import (
    BatchError,
    ConfigError,
    EvolutionError,
    FeedError,
    LedgerError,
    LockError,
    SchemaError,
    StateError,
    ValidationError,
)
from .feed import DirectoryFeed, FeedPage, ReportFeed
from .hub import OrchHubFeed, feed_from_config
from .importer import Candidate, ListResult, SyncResult, list_candidates, sync
from .ledger import append_records, build_record, read_records
from .lineage import BatchLineage, Experiment, Lineage, RefState
from .phase import BatchView, LifecycleStatus
from .reports import NormalizedReport, Rejection, normalize
from .revisions import Revision, Revisions, describe_revisions, release_line_revision
from .state import EvolutionState, PoolEntry, ReportRef, load_state, save_state, single_writer_lock

__all__ = [
    "AdmissionDecision",
    "AnalysisTaskSpec",
    "Batch",
    "BatchError",
    "BatchLineage",
    "BatchView",
    "Candidate",
    "ConfigError",
    "DirectoryFeed",
    "EvolutionConfig",
    "EvolutionError",
    "EvolutionState",
    "Experiment",
    "FeedError",
    "FeedPage",
    "FreezeResult",
    "LedgerError",
    "LifecycleStatus",
    "Lineage",
    "ListResult",
    "LockError",
    "NormalizedReport",
    "OrchHubFeed",
    "PoolEntry",
    "Rejection",
    "RefState",
    "ReportFeed",
    "ReportRef",
    "Revision",
    "Revisions",
    "SchemaError",
    "StartResult",
    "StateError",
    "SyncResult",
    "ValidationError",
    "append_records",
    "build_record",
    "describe_revisions",
    "evaluate_admission",
    "feed_from_config",
    "freeze",
    "list_candidates",
    "load_batches",
    "load_config",
    "load_state",
    "normalize",
    "open_batch",
    "read_records",
    "release_line_revision",
    "save_state",
    "single_writer_lock",
    "start",
    "sync",
]
