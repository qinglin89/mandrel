"""Failures the evolution controller can raise.

One hierarchy so a CLI adapter catches `EvolutionError` and turns any of them
into a single bounded, actionable message.
"""

from __future__ import annotations


class EvolutionError(RuntimeError):
    """Base class for every evolution controller failure."""


class ConfigError(EvolutionError):
    """`evolution/config.toml` is missing, malformed, or asks for behavior this
    implementation does not support."""


class SchemaError(EvolutionError):
    """A versioned schema under `evolution/schemas/` cannot be read, or uses a
    JSON Schema keyword or pattern construct this validator does not
    implement."""


class ValidationError(EvolutionError):
    """A value does not satisfy its versioned schema."""


class StateError(EvolutionError):
    """`.ai-evolution/state.json` is unreadable or malformed. Never repaired
    silently: a reset cursor would re-import, a dropped pool would lose
    evidence."""


class ProposalError(EvolutionError):
    """`.ai-evolution/batch-proposal.json` is unreadable, malformed, or no
    longer describes the pool and the source line it was built against.

    Separate from `StateError` because the two absences mean opposite things:
    losing the pool loses evidence, while losing a proposal loses a reading
    that the next `start` rebuilds. Nothing durable stands on it, so a bad one
    is refused and rebuilt rather than repaired."""


class LedgerError(EvolutionError):
    """`evolution/ledger.jsonl` cannot be appended to safely."""


class LockError(EvolutionError):
    """The single-writer lock is held by someone else."""


class FeedError(EvolutionError):
    """The report feed is unavailable or returned an unusable page."""


class RefHoldError(EvolutionError):
    """A ref cannot be held where it was observed, so an operation about to
    record something about it has nothing to record from — an experiment ref a
    transition is decided over, or the source line a rollback is recording as
    carrying its inverse.

    Raised at the Git boundary and translated by the operation, which is what
    knows why that ref had to stay still."""


class BatchError(EvolutionError):
    """A batch cannot be read or frozen: a manifest under `evolution/batches/`
    contradicts itself or the pool, an id is already taken, or the requested
    freeze would break an admission invariant."""
