"""Local protocol-evolution controller.

`evolution/README.md` is the normative contract; this package implements the
mechanical part of it — discovering already-complete orch-hub reports,
validating them, and staging unique completed tasks into a pending pool.

What it deliberately does not do: run or schedule an evaluation, and decide
anything a human is supposed to decide. Batch formation, change admission, and
promotion stay human gates (contract invariant 9).
"""

from __future__ import annotations

from .config import EvolutionConfig, load_config
from .errors import (
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
from .state import EvolutionState, PoolEntry, ReportRef, load_state, save_state, single_writer_lock

__all__ = [
    "Candidate",
    "ConfigError",
    "DirectoryFeed",
    "EvolutionConfig",
    "EvolutionError",
    "EvolutionState",
    "FeedError",
    "FeedPage",
    "LedgerError",
    "ListResult",
    "LockError",
    "NormalizedReport",
    "PoolEntry",
    "Rejection",
    "ReportFeed",
    "ReportRef",
    "SchemaError",
    "StateError",
    "SyncResult",
    "ValidationError",
    "append_records",
    "build_record",
    "list_candidates",
    "load_config",
    "load_state",
    "normalize",
    "read_records",
    "save_state",
    "single_writer_lock",
    "sync",
]
