"""Local protocol-evolution controller.

`evolution/README.md` is the normative contract; this package implements the
mechanical part of it — discovering already-complete orch-hub reports,
validating them, and staging unique completed tasks into a pending pool.

What it deliberately does not do: run or schedule an evaluation, and decide
anything a human is supposed to decide. Batch formation, change admission, and
promotion stay human gates (contract invariant 9). Automation prepares evidence
and pending analysis tasks; a human triggers the freeze and admits every
proposed canonical change.
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
from .importer import Candidate, ListResult, SyncResult, list_candidates, sync
from .ledger import append_records, build_record, read_records
from .reports import NormalizedReport, Rejection, normalize
from .revisions import release_line_revision
from .state import EvolutionState, PoolEntry, ReportRef, load_state, save_state, single_writer_lock

__all__ = [
    "AdmissionDecision",
    "AnalysisTaskSpec",
    "Batch",
    "BatchError",
    "Candidate",
    "ConfigError",
    "DirectoryFeed",
    "EvolutionConfig",
    "EvolutionError",
    "EvolutionState",
    "FeedError",
    "FeedPage",
    "FreezeResult",
    "LedgerError",
    "ListResult",
    "LockError",
    "NormalizedReport",
    "PoolEntry",
    "Rejection",
    "ReportFeed",
    "ReportRef",
    "SchemaError",
    "StartResult",
    "StateError",
    "SyncResult",
    "ValidationError",
    "append_records",
    "build_record",
    "evaluate_admission",
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
