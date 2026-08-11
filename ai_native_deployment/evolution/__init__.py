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

The names below are the package's stable surface. Five things are reached
through their modules rather than re-exported, because the bare name says
nothing on its own: `phase.describe` (the derived lifecycle status),
`lineage.describe` (the derived batch/experiment lineage), the
`experiments.create` / `add_tasks` / `reject` / `seal_round` / `revise` /
`abandon` / `supersede` / `promote` / `conclude_no_change` operations on a
batch's change lineage, the `replay.start` / `replay.conclude` / `replay.abandon` /
`replay.withdraw` runs against a round's pinned candidate together with
`replay.read_replays` / `replay.describe_evidence` and the rest of that module's
vocabulary (`replay.Integration`, `replay.CaseSet`, `replay.Measurement`, …),
`rollback.rollback` (the latest promotion taken back off the source line), and
the `render.format_*` functions (operator-facing text for the CLI).
"""

from __future__ import annotations

from .analysis_task import AnalysisTaskSpec
from .batches import (
    AdmissionDecision,
    Batch,
    FreezeResult,
    StartResult,
    batch_awaiting_analysis,
    evaluate_admission,
    freeze,
    load_batches,
    start,
)
from .config import EvolutionConfig, load_config
from .experiments import (
    AdmissionResult,
    Admitted,
    ConclusionResult,
    DecisionResult,
    PromotionResult,
    RejectionResult,
    ReviseResult,
    SealResult,
)
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
from .lineage import BatchLineage, Experiment, Gate, Lineage, RefState, current_batch
from .phase import BatchView, LifecycleStatus
from .replay import Evidence, Replay, ReplayHarness, ReplayPlan, ReplayReport, ReplayRequest
from .reports import NormalizedReport, Rejection, normalize
from .revisions import Revision, release_line_revision
from .rollback import RollbackResult
from .state import EvolutionState, PoolEntry, ReportRef, load_state, save_state, single_writer_lock

__all__ = [
    "AdmissionDecision",
    "AdmissionResult",
    "Admitted",
    "AnalysisTaskSpec",
    "Batch",
    "BatchError",
    "BatchLineage",
    "BatchView",
    "Candidate",
    "ConclusionResult",
    "ConfigError",
    "DecisionResult",
    "DirectoryFeed",
    "EvolutionConfig",
    "EvolutionError",
    "EvolutionState",
    "Evidence",
    "Experiment",
    "FeedError",
    "FeedPage",
    "FreezeResult",
    "Gate",
    "LedgerError",
    "LifecycleStatus",
    "Lineage",
    "ListResult",
    "LockError",
    "NormalizedReport",
    "OrchHubFeed",
    "PromotionResult",
    "PoolEntry",
    "RefState",
    "Rejection",
    "RejectionResult",
    "Replay",
    "ReplayHarness",
    "ReplayPlan",
    "ReplayReport",
    "ReplayRequest",
    "ReportFeed",
    "ReportRef",
    "Revision",
    "ReviseResult",
    "RollbackResult",
    "SchemaError",
    "SealResult",
    "StartResult",
    "StateError",
    "SyncResult",
    "ValidationError",
    "append_records",
    "batch_awaiting_analysis",
    "build_record",
    "current_batch",
    "evaluate_admission",
    "feed_from_config",
    "freeze",
    "list_candidates",
    "load_batches",
    "load_config",
    "load_state",
    "normalize",
    "read_records",
    "release_line_revision",
    "save_state",
    "single_writer_lock",
    "start",
    "sync",
]
