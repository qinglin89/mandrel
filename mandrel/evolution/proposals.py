"""The batch a human has been offered and has not yet fixed.

A batch is a base plus the reports produced under it, and both halves are
decided before anything durable exists. This module holds the record of that
decision while it is still open: `.ai-evolution/batch-proposal.json`, written
by the ordinary poll and consumed by the freeze.

**Why the formation is two operations.** The freeze writes the manifest, claims
the reports and generates the analysis task together, and the manifest is
immutable from that moment (invariant 3). A human who is to move a report in or
out has to do it before that write, so the selection needs somewhere to stand
in the meantime — and it cannot be the manifest, because a manifest that can be
adjusted is not the immutable membership record the contract rests on. The
proposal is that somewhere. It makes the whole human window fall *before* the
first durable write rather than beside it.

**Why it is machine-local.** A proposal is a reading of this checkout's pending
pool against this checkout's source line, and the pool is ignored runtime state
(invariant 11). Nothing derives from a proposal that outlives it: the freeze
re-reads the line, re-validates every member against the live pool and
re-verifies every staged bundle before it writes, so a proposal is a
convenience for the human window and never evidence. Losing one costs a
`start`.

**Why it is refused rather than repaired.** `state.py` never repairs, because a
reset cursor re-imports and a dropped pool loses evidence the feed may no
longer serve. Neither cost exists here: a malformed proposal is thrown away and
rebuilt from state that is still whole. So this parser is exactly as strict —
every field of the declared version required, absent included — and its refusal
names the rebuild rather than a repair.

**Why the vocabulary is not here.** The selection modes, the non-selection
reasons and the rule that a human move carries its name live in `manifests.py`:
they become permanent when a batch is frozen, and every argument for keeping
them apart is about what a reader of that immutable record can tell. This
module speaks them so that the offer and the manifest cannot mean different
things by the same word.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .config import EvolutionConfig
from .errors import ProposalError
from .manifests import (
    EXCLUSION_REASONS,
    HUMAN_MOVE_FIELDS,
    HUMAN_REASONS,
    MODE_AUTO,
    MODE_HUMAN_INCLUDED,
    SELECTION_MODES,
    attribution_problem,
)
from .schema import is_rfc3339_date_time
from .state import atomic_write_text

PROPOSAL_SCHEMA_VERSION = 1

# The complete shape of every version this build reads, in the same form and
# for the same reason as `state._STATE_FIELDS`: a missing field is damage, and
# reading a gap as a default is how a selection quietly becomes a different
# one.
_PROPOSAL_FIELDS = {
    1: ("built_at", "source_line_ref", "base_revision", "selected", "not_selected"),
}
_PROPOSAL_KEYS = {version: frozenset(fields) | {"schema_version"} for version, fields in _PROPOSAL_FIELDS.items()}
_MEMBER_FIELDS = frozenset({"repo_id", "task_id", "report_keys", "mode", *HUMAN_MOVE_FIELDS})
_EXCLUSION_FIELDS = frozenset({"repo_id", "task_id", "report_keys", "reason", *HUMAN_MOVE_FIELDS})

_REVISION = re.compile(r"\A[0-9a-f]{40}\Z", re.ASCII)
_REF = re.compile(r"\Arefs/[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)+\Z", re.ASCII)


@dataclass(frozen=True)
class Member:
    """One pooled task the proposal would put in the batch."""

    repo_id: str
    task_id: str
    report_keys: tuple[str, ...]
    mode: str = MODE_AUTO
    # Set only for `human-included`: why this task was argued in, by whom, and
    # when. The same three fields name a human exclusion (`HUMAN_MOVE_FIELDS`).
    justification: str | None = None
    actor: str | None = None
    moved_at: str | None = None

    @property
    def dedup_key(self) -> tuple[str, str]:
        return (self.repo_id, self.task_id)

    def to_json(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "task_id": self.task_id,
            "report_keys": list(self.report_keys),
            "mode": self.mode,
            "justification": self.justification,
            "actor": self.actor,
            "moved_at": self.moved_at,
        }

    @classmethod
    def from_json(cls, data: Any, where: str) -> "Member":
        fields = _entry_fields(data, where, allowed=_MEMBER_FIELDS)
        mode = _require_str(data, "mode", where)
        if mode not in SELECTION_MODES:
            raise ProposalError(f"{where}: unknown selection mode {mode!r}; this build knows {sorted(SELECTION_MODES)}")
        return cls(
            mode=mode,
            **fields,
            **_require_attribution(
                data,
                where,
                stated=mode == MODE_HUMAN_INCLUDED,
                what="a human-included member",
            ),
        )


@dataclass(frozen=True)
class Exclusion:
    """One considered task the proposal would leave out, and why.

    Kept beside the members rather than derived from the difference against the
    pool, because the pool moves and this reading did not: what the batch was
    selected out of is a fact about the moment it was formed.
    """

    repo_id: str
    task_id: str
    report_keys: tuple[str, ...]
    reason: str
    # Set only for the reasons a human owns. A machine reason explains itself;
    # a person taking out evidence the identity test selected does not.
    justification: str | None = None
    actor: str | None = None
    moved_at: str | None = None

    @property
    def dedup_key(self) -> tuple[str, str]:
        return (self.repo_id, self.task_id)

    def to_json(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "task_id": self.task_id,
            "report_keys": list(self.report_keys),
            "reason": self.reason,
            "justification": self.justification,
            "actor": self.actor,
            "moved_at": self.moved_at,
        }

    @classmethod
    def from_json(cls, data: Any, where: str) -> "Exclusion":
        fields = _entry_fields(data, where, allowed=_EXCLUSION_FIELDS)
        reason = _require_str(data, "reason", where)
        if reason not in EXCLUSION_REASONS:
            raise ProposalError(
                f"{where}: unknown non-selection reason {reason!r}; this build knows {sorted(EXCLUSION_REASONS)}"
            )
        return cls(
            reason=reason,
            **fields,
            **_require_attribution(
                data,
                where,
                stated=reason in HUMAN_REASONS,
                what=f"a {reason} exclusion",
            ),
        )


@dataclass(frozen=True)
class BatchProposal:
    """A base, and the pooled reports selected against it.

    `base_revision` is the source line's tip as this build read it, and it is
    the commit the freeze records as the batch's own (invariants 8 and 15). It
    is carried here rather than resolved again at freeze time so that a line
    that moved during the human window is visible as a mismatch instead of
    silently reselecting the evidence.
    """

    built_at: str
    source_line_ref: str
    base_revision: str
    selected: tuple[Member, ...] = ()
    not_selected: tuple[Exclusion, ...] = ()

    @property
    def task_count(self) -> int:
        return len(self.selected)

    @property
    def considered(self) -> int:
        return len(self.selected) + len(self.not_selected)

    @property
    def human_included(self) -> tuple[Member, ...]:
        return tuple(member for member in self.selected if member.mode == MODE_HUMAN_INCLUDED)

    def report_keys(self) -> set[str]:
        return {key for member in self.selected for key in member.report_keys}

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": PROPOSAL_SCHEMA_VERSION,
            "built_at": self.built_at,
            "source_line_ref": self.source_line_ref,
            "base_revision": self.base_revision,
            "selected": [member.to_json() for member in _ordered(self.selected)],
            "not_selected": [item.to_json() for item in _ordered(self.not_selected)],
        }

    @classmethod
    def from_json(cls, data: Any, path: Path) -> "BatchProposal":
        if not isinstance(data, dict):
            raise ProposalError(f"{path}: a proposal must be a JSON object")
        version = data.get("schema_version")
        # Type-checked before it is looked up, and both halves earn their place:
        # an unhashable value raises rather than compares, and `True` equals 1
        # in Python — so a version field of `true` would otherwise read as
        # version 1 and parse.
        if not isinstance(version, int) or isinstance(version, bool) or version not in _PROPOSAL_FIELDS:
            raise ProposalError(
                f"{path}: unsupported proposal schema_version {version!r}; this build reads "
                f"{sorted(_PROPOSAL_FIELDS)} — rebuild it with `mandrel evolution start`"
            )
        unexpected = set(data) - _PROPOSAL_KEYS[version]
        if unexpected:
            raise ProposalError(f"{path}: unexpected proposal fields {sorted(unexpected)}")
        for name in _PROPOSAL_FIELDS[version]:
            if name not in data:
                raise ProposalError(f"{path}: incomplete proposal, {name!r} is missing; version {version} requires it")

        ref = _require_str(data, "source_line_ref", str(path))
        if not _REF.match(ref):
            raise ProposalError(f"{path}: source_line_ref {ref!r} is not a fully-qualified ref")
        revision = _require_str(data, "base_revision", str(path))
        if not _REVISION.match(revision):
            raise ProposalError(f"{path}: base_revision {revision!r} is not a full 40-character commit id")

        selected = tuple(
            Member.from_json(item, f"{path}: selected[{i}]") for i, item in enumerate(_require_list(data, "selected", path))
        )
        not_selected = tuple(
            Exclusion.from_json(item, f"{path}: not_selected[{i}]")
            for i, item in enumerate(_require_list(data, "not_selected", path))
        )
        _require_one_decision(selected, not_selected, path=path)
        return cls(
            built_at=_require_timestamp(data, "built_at", str(path)),
            source_line_ref=ref,
            base_revision=revision,
            selected=selected,
            not_selected=not_selected,
        )


def load_proposal(config: EvolutionConfig) -> BatchProposal | None:
    """The proposal that stands, or None when none does.

    A missing file is the ordinary state of a workspace between freezes, not an
    error: the freeze removes the proposal it consumed, and `start` writes the
    next one.
    """

    path = config.proposal_path
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise ProposalError(
            f"{path} cannot be read as JSON ({error}); it is a rebuildable reading of the pool, so delete it and "
            "run `mandrel evolution start` again rather than editing it"
        ) from error
    return BatchProposal.from_json(data, path)


def save_proposal(config: EvolutionConfig, proposal: BatchProposal) -> Path:
    """Publish a proposal through the reader, so both directions agree.

    The cross-field rules — a human move carrying its actor and moment, one
    decision per task — are enforced here by parsing what is about to be
    written. A writer that skipped them could persist a proposal its own reader
    refuses, and the operator would meet that as a freeze that cannot start.
    """

    path = config.proposal_path
    path.parent.mkdir(parents=True, exist_ok=True)
    document = proposal.to_json()
    BatchProposal.from_json(document, path)
    atomic_write_text(path, json.dumps(document, indent=2, sort_keys=True) + "\n")
    return path


def clear_proposal(config: EvolutionConfig) -> bool:
    """Drop the proposal, if one is there. True when this call removed it.

    Idempotent because it is the last step of a freeze and the repair of an
    interrupted one: both have to be safe to run against a workspace where it
    already happened.
    """

    path = config.proposal_path
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def _ordered(items: Iterable[Member] | Iterable[Exclusion]) -> list[Any]:
    """One deterministic order for both lists, so the file's bytes are a
    function of the selection rather than of dictionary iteration."""

    return sorted(items, key=lambda item: (item.repo_id, item.task_id))


def _entry_fields(data: Any, where: str, *, allowed: frozenset[str]) -> dict[str, Any]:
    """The identity every selected and non-selected entry states the same way."""

    if not isinstance(data, dict):
        raise ProposalError(f"{where}: entry must be an object")
    unexpected = set(data) - allowed
    if unexpected:
        raise ProposalError(f"{where}: unexpected fields {sorted(unexpected)}")
    missing = sorted(allowed - set(data))
    if missing:
        raise ProposalError(f"{where}: incomplete entry, {missing} missing")
    keys = data.get("report_keys")
    if not isinstance(keys, list) or not keys or not all(isinstance(key, str) and key for key in keys):
        raise ProposalError(f"{where}: report_keys must be a non-empty list of report keys")
    if len(set(keys)) != len(keys):
        raise ProposalError(f"{where}: the same report appears twice on this entry")
    return {
        "repo_id": _require_str(data, "repo_id", where),
        "task_id": _require_str(data, "task_id", where),
        "report_keys": tuple(keys),
    }


def _require_attribution(
    data: Mapping[str, Any],
    where: str,
    *,
    stated: bool,
    what: str,
) -> dict[str, Any]:
    """The fields that name a human move — all of them or none of them.

    The rule itself is `manifests.attribution_problem`, because the same move
    has to survive into the immutable record with the same attribution; only the
    refusal differs, and it differs for a reason (`ProposalError`: a bad offer is
    rebuilt, a bad manifest is a batch nobody can read).
    """

    problem = attribution_problem(data, stated=stated, what=what)
    if problem is not None:
        raise ProposalError(f"{where}: {problem}")
    return {name: data.get(name) for name in HUMAN_MOVE_FIELDS}


def _require_one_decision(
    selected: tuple[Member, ...],
    not_selected: tuple[Exclusion, ...],
    *,
    path: Path,
) -> None:
    """One task, one answer.

    The two lists together are the batch's denominator, so a task named in both
    would make the considered count disagree with itself, and a task named
    twice in one would count a single judgement as two.
    """

    seen: dict[tuple[str, str], str] = {}
    for where, items in (("selected", selected), ("not_selected", not_selected)):
        for item in items:
            previous = seen.get(item.dedup_key)
            if previous is not None:
                raise ProposalError(
                    f"{path}: {item.repo_id}/{item.task_id} is named in both {previous} and {where}"
                    if previous != where
                    else f"{path}: {where} names {item.repo_id}/{item.task_id} twice"
                )
            seen[item.dedup_key] = where


def _require_list(data: Mapping[str, Any], key: str, path: Path) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list):
        raise ProposalError(f"{path}: {key} must be a list")
    return value


def _require_str(data: Mapping[str, Any], key: str, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProposalError(f"{where}: {key} must be a non-empty string")
    return value


def _require_timestamp(data: Mapping[str, Any], key: str, where: str) -> str:
    value = _require_str(data, key, where)
    if not is_rfc3339_date_time(value):
        raise ProposalError(f"{where}: {key} {value!r} is not an RFC 3339 timestamp")
    return value
