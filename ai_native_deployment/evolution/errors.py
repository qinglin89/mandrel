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


class LedgerError(EvolutionError):
    """`evolution/ledger.jsonl` cannot be appended to safely."""


class LockError(EvolutionError):
    """The single-writer lock is held by someone else."""


class FeedError(EvolutionError):
    """The report feed is unavailable or returned an unusable page."""


class BatchError(EvolutionError):
    """A batch cannot be read or frozen: a manifest under `evolution/batches/`
    contradicts itself or the pool, an id is already taken, or the requested
    freeze would break an admission invariant."""
