"""Committed batch content: the manifest, and the records that end its stages.

The read side of a frozen batch — membership, versioning, whether its analysis
has finished, and the record that ends the batch itself. `batches.py` owns the
writes, the way it already owns `_write_manifest` against this module's
`read_manifest`. Whether a batch is *current* is not answered here: that is a
question about its whole lineage, experiments included (`lineage.py`).

Four records sit beside the manifest and none of them substitutes for another.
`analysis-complete.json` ends the analysis stage; `outcome.json` ends the batch
(contract invariant 14), which is a later and different moment — admission, the
experiments, and their rounds all happen between the two;
`rejected-drafts.json` ends nothing but is what keeps a declined proposal from
waiting at the admission gate forever; `rollback.json` comes after the end,
saying that the promotion the outcome recorded is no longer on the source line.
All four are committed for the same reason: `.ai-tasks/` and `.ai-evolution/`
are machine-local, so a conclusion that lived only there would be unreadable on
every other clone.

Split out of `batches.py` because two layers need it and neither may import the
other: `state.py` checks a processed report against the batch that claims it,
and `batches.py` allocates ids and freezes new manifests.

**Versioning.** A frozen manifest is immutable (invariant 3), so the schema it
was written against is immutable too. Version 1 is the shape this repository
shipped before per-report cohort provenance existed; version 2 adds it. The
writer emits the current version and the reader accepts both, each against its
own frozen schema file. Tightening version 1 in place would instead have
redefined manifests already written under it — a manifest that was valid when
frozen would stop loading, with no migration possible on content that may not
be edited.

A version is bumped when the *reader* needs something old records cannot
supply. A field only newer records can carry is added to the current version as
optional instead, where absent and null are the same answer — every record
written before it stays exactly what it claimed to be. The batch outcome's
merge unit is that case: it describes a promotion, and no record written before
the promotion operation existed can be one. Requiring it would have been the
tightening above, arriving as a field rather than as a rule.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .config import (
    BATCH_SCHEMA_FILENAME,
    BATCH_SCHEMA_V1_FILENAME,
    CLOSURE_SCHEMA_FILENAME,
    OUTCOME_SCHEMA_FILENAME,
    REJECTED_DRAFTS_SCHEMA_FILENAME,
    ROLLBACK_SCHEMA_FILENAME,
    EvolutionConfig,
    batch_id_number,
    format_batch_id,
)
from .errors import BatchError
from .schema import load_schema, validate_or_raise

MANIFEST_FILENAME = "manifest.json"
FINDINGS_FILENAME = "findings.md"
CLOSURE_FILENAME = "analysis-complete.json"
OUTCOME_FILENAME = "outcome.json"
REJECTED_DRAFTS_FILENAME = "rejected-drafts.json"
ROLLBACK_FILENAME = "rollback.json"

CLOSURE_SCHEMA_VERSION = 1
OUTCOME_SCHEMA_VERSION = 1
REJECTED_DRAFTS_SCHEMA_VERSION = 1
ROLLBACK_SCHEMA_VERSION = 1

OUTCOME_PROMOTED = "promoted"
OUTCOME_NO_CHANGE = "no-change"

# What a freeze writes.
BATCH_SCHEMA_VERSION = 2
# What this build reads, and the frozen schema each version is validated
# against. Every version ever written keeps its entry here: dropping one would
# make an immutable manifest unreadable rather than merely outdated.
BATCH_SCHEMA_FILENAMES = {1: BATCH_SCHEMA_V1_FILENAME, 2: BATCH_SCHEMA_FILENAME}


@dataclass(frozen=True)
class Batch:
    """One frozen batch on disk, as its manifest describes it."""

    batch_id: str
    directory: Path
    manifest: Mapping[str, Any]

    @property
    def manifest_path(self) -> Path:
        return self.directory / MANIFEST_FILENAME

    @property
    def findings_path(self) -> Path:
        return self.directory / FINDINGS_FILENAME

    @property
    def findings_recorded(self) -> bool:
        """Whether the analysis has committed its dispositions.

        Necessary for a batch to be closed, and never sufficient on its own —
        the task writes this file while it is still being developed. What turns
        it into closure is the record beside it (`closure_path`), which the
        controller writes only from a completed analysis task.
        """

        return self.findings_path.is_file()

    @property
    def closure_path(self) -> Path:
        return self.directory / CLOSURE_FILENAME

    @property
    def outcome_path(self) -> Path:
        return self.directory / OUTCOME_FILENAME

    @property
    def rejected_drafts_path(self) -> Path:
        return self.directory / REJECTED_DRAFTS_FILENAME

    @property
    def rollback_path(self) -> Path:
        return self.directory / ROLLBACK_FILENAME

    @property
    def schema_version(self) -> int | None:
        version = self.manifest.get("schema_version")
        return version if isinstance(version, int) and not isinstance(version, bool) else None

    @property
    def analysis_task_id(self) -> str | None:
        value = self.manifest.get("analysis_task_id")
        return value if isinstance(value, str) and value else None

    @property
    def reports(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.manifest.get("reports") or ())

    @property
    def report_keys(self) -> frozenset[str]:
        return frozenset(str(report["report_key"]) for report in self.reports)

    @property
    def task_count(self) -> int:
        """Unique completed tasks — the unit invariant 1 counts in. Reruns share
        a `(repo_id, task_id)` with their primary and so do not inflate it."""

        return len({(report["repo_id"], report["task_id"]) for report in self.reports})


def load_batches(config: EvolutionConfig) -> list[Batch]:
    """Every frozen batch, validated against the schema of its own version.

    Fails closed on anything it cannot account for: an unrecognised directory
    name might be a batch this build cannot read, and skipping it would let an
    allocation reuse an id a manifest already claims. Dot-prefixed names are the
    exception — those are staging residue from an interrupted freeze, which
    belongs to no batch.
    """

    root = config.batches_root
    if not root.is_dir():
        return []

    batches: list[Batch] = []
    for entry in sorted(root.iterdir()):
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        if batch_id_number(entry.name) is None:
            raise BatchError(
                f"{entry}: not a batch identifier; only frozen batches belong under {config.storage.batches}/"
            )
        manifest = read_manifest(config, entry / MANIFEST_FILENAME)
        if manifest.get("batch_id") != entry.name:
            raise BatchError(
                f"{entry / MANIFEST_FILENAME}: manifest claims batch_id {manifest.get('batch_id')!r} "
                f"but sits in {entry.name!r}; a manifest cannot name another batch's id"
            )
        batches.append(Batch(batch_id=entry.name, directory=entry, manifest=manifest))
    return batches


def next_batch_id(batches: list[Batch]) -> str:
    """One past the highest id ever allocated.

    Counted from the highest, not from how many exist: reusing the id of a batch
    whose directory was moved away would attach new evidence to an old cohort's
    name.
    """

    highest = max((batch_id_number(batch.batch_id) or 0 for batch in batches), default=0)
    return format_batch_id(highest + 1)


def claimed_reports(config: EvolutionConfig) -> dict[str, set[str]]:
    """Every report a frozen manifest names, mapped to the batches naming it.

    The authority for whether a `processed` claim in runtime state means
    anything: the manifests are the membership record, so a claim naming a batch
    that does not name the report back is a claim with nothing behind it. A key
    maps to a set because two manifests naming one report is a repository this
    controller must be able to describe rather than crash on — the caller
    decides what to do about it.
    """

    owners: dict[str, set[str]] = {}
    for batch in load_batches(config):
        for report_key in batch.report_keys:
            owners.setdefault(report_key, set()).add(batch.batch_id)
    return owners


def read_closure(config: EvolutionConfig, batch: Batch) -> Mapping[str, Any] | None:
    """The batch's committed closure record, or None when it has none.

    This is the portable half of the closure guard. `.ai-tasks/` is
    machine-local and ignored, so the analysis task's lifecycle can be read on
    at most one machine; the record travels with the repository, which is what
    lets every other clone tell a finished analysis from a draft. Reading task
    *absence* as completion, as this controller once did, gives the opposite
    answer everywhere the task never existed.

    Validated, and cross-checked against the manifest it sits beside: a record
    naming another batch or another task — or sitting beside a manifest that
    names no task for it to attest to — is corruption, and corruption that loads
    here releases the next cohort early.
    """

    path = batch.closure_path
    record = read_batch_record(
        config,
        batch,
        path,
        schema_filename=CLOSURE_SCHEMA_FILENAME,
        description="batch closure record",
        foreign="one batch's completion cannot close another",
    )
    if record is None:
        return None
    named = batch.analysis_task_id
    if named is None:
        # Unrepresentable from this controller: it publishes a closure only from
        # the completion of the task the manifest names, so a record beside a
        # manifest naming none has no task whose lifecycle it could attest to —
        # and nothing on disk could ever be shown to have analyzed this batch.
        # Skipping the identity check here instead would let exactly that record
        # release the next cohort.
        raise BatchError(
            f"{path}: {batch.manifest_path} names no analysis task, so this record attests to a completion "
            f"nothing can be checked against; restore the manifest's analysis_task_id, or remove the record "
            "and let the analysis this batch is still waiting for publish it"
        )
    if record["analysis_task_id"] != named:
        raise BatchError(
            f"{path}: closure record attests to task {record['analysis_task_id']!r}, but "
            f"{batch.manifest_path} names {named!r} as this batch's analysis task"
        )
    return record


def read_outcome(config: EvolutionConfig, batch: Batch) -> Mapping[str, Any] | None:
    """The batch's terminal outcome record, or None when it has none.

    Half of the answer to "is this batch still current" (invariant 14) and never
    the whole of it: what a record says has to agree with the experiments it
    concludes over, and that is a question about the batch's whole lineage
    (`lineage.current_batch`). What this reader owes that one is a record it can
    read as one unambiguous conclusion, so it refuses anything else.

    The two outcomes carry different fields, and the pairing is checked here
    because the implemented JSON Schema subset cannot express it. A `no-change`
    record naming a promotion revision is the fabrication invariant 7 forbids,
    and a `promoted` record missing any of the experiment, the revision, or the
    merge unit ends the batch while saying less than it must about what reached
    the source line — either way the batch is over and the trail is wrong, which
    is exactly the state that must not load.

    The merge unit is part of that pairing rather than an ornament on it. A
    promotion revision alone says a commit exists; what makes it checkable
    against the evidence is the round, the candidate, the source line it went
    onto, and the tree — so a record carrying the revision and not those is one
    nothing can afterwards hold to the replay it was argued from.
    """

    path = batch.outcome_path
    record = read_batch_record(
        config,
        batch,
        path,
        schema_filename=OUTCOME_SCHEMA_FILENAME,
        description="batch outcome record",
        foreign="one batch's conclusion cannot end another",
    )
    if record is None:
        return None

    promoted = record["outcome"] == OUTCOME_PROMOTED
    fields = ("experiment_id", "promotion_revision", "promotion")
    # `.get`, because the merge unit arrived after this schema version shipped
    # and a record written without it is a `no-change` conclusion from before
    # any promotion could be recorded — absent and null are the same answer.
    named = {key: record.get(key) for key in fields if record.get(key) is not None}
    if promoted and len(named) != len(fields):
        raise BatchError(
            f"{path}: a {OUTCOME_PROMOTED!r} outcome states the experiment it promoted, the source-line "
            f"revision that carries it, and the merge unit it was promoted as; this one states "
            f"{sorted(named) or 'none of them'}"
        )
    if promoted and record["promotion"]["merge_input_revision"] == record["promotion_revision"]:
        raise BatchError(
            f"{path}: promotes {record['promotion_revision'][:12]} and names it as the source line this "
            "promotion was made onto; the merge unit is the line before and the commit after, and a record "
            "where they are one commit describes a promotion that carried nothing"
        )
    if not promoted and named:
        raise BatchError(
            f"{path}: a {record['outcome']!r} outcome fabricates no candidate, promotion, or revision "
            f"(invariant 7), but this one names {sorted(named)}"
        )
    return record


def read_rollback(config: EvolutionConfig, batch: Batch) -> Mapping[str, Any] | None:
    """The record taking this batch's promotion back off the source line, or None.

    Absence is the ordinary answer for every batch: most promotions stand, and a
    batch that concluded `no-change` has nothing to reverse. What this reader
    owes the lineage is a record it can read as one unambiguous rollback of one
    promotion — whether that promotion is *this* batch's is checked where both
    records are in hand (`lineage`), since the outcome is what the claim has to
    agree with.

    A null `reverted_at` is an operation in flight rather than a malformed
    record: the inverse commit is made and recorded before the source line moves,
    which is what lets an interrupted rollback be finished rather than repeated.
    """

    return read_batch_record(
        config,
        batch,
        batch.rollback_path,
        schema_filename=ROLLBACK_SCHEMA_FILENAME,
        description="batch rollback record",
        foreign="one batch's promotion cannot be reversed by another's record",
    )


def read_rejected_drafts(config: EvolutionConfig, batch: Batch) -> tuple[Mapping[str, Any], ...]:
    """Every draft a human declined at this batch's admission gate.

    Empty covers both "the record exists and lists nothing" and "no record yet":
    a batch nobody has declined anything in is the ordinary starting state, and
    the two are the same fact about the gate.

    A draft id appearing twice is refused rather than deduplicated. Declining is
    terminal for a proposal, so a second entry for one id is either two decisions
    about the same bytes or one decision recorded twice, and the record does not
    say which — while re-proposing the idea legitimately means a new id, which
    never collides.
    """

    record = read_batch_record(
        config,
        batch,
        batch.rejected_drafts_path,
        schema_filename=REJECTED_DRAFTS_SCHEMA_FILENAME,
        description="rejected-drafts record",
        foreign="one batch's admission gate cannot decline another's proposals",
    )
    if record is None:
        return ()

    rejected = tuple(record["rejected"])
    seen: set[str] = set()
    for entry in rejected:
        draft_id = entry["draft_id"]
        if draft_id in seen:
            raise BatchError(
                f"{batch.rejected_drafts_path}: draft {draft_id!r} is declined twice; declining is terminal "
                "for a proposal, so a re-proposal is a new draft id rather than a second entry"
            )
        seen.add(draft_id)
    return rejected


def read_batch_record(
    config: EvolutionConfig,
    batch: Batch,
    path: Path,
    *,
    schema_filename: str,
    description: str,
    foreign: str,
) -> Mapping[str, Any] | None:
    """One committed record from a batch directory, or None when it is absent.

    Absence is a legal answer for all three of them — a stage or a batch that
    has not ended yet — while an unreadable or foreign one is not: every caller
    here is deciding whether something has finished, and a record that cannot be
    read as this batch's finishes it just as effectively as a valid one.
    """

    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchError(f"unreadable {description} {path}: {exc}") from exc
    if not isinstance(record, dict):
        raise BatchError(f"{description} is not a JSON object: {path}")

    validate_or_raise(
        record,
        load_schema(config.schema_path(schema_filename)),
        description=f"{description} {path}",
    )
    if record["batch_id"] != batch.batch_id:
        raise BatchError(
            f"{path}: {description} names batch {record['batch_id']!r} but sits in {batch.batch_id!r}; {foreign}"
        )
    return record


def read_manifest(config: EvolutionConfig, path: Path) -> Mapping[str, Any]:
    """One manifest, validated against the schema of the version it declares."""

    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BatchError(
            f"{path.parent} has no {MANIFEST_FILENAME}; a batch directory without its membership snapshot "
            "is not a batch — remove it or restore the manifest"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchError(f"unreadable batch manifest {path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise BatchError(f"batch manifest is not a JSON object: {path}")

    version = manifest.get("schema_version")
    filename = BATCH_SCHEMA_FILENAMES.get(version) if isinstance(version, int) and not isinstance(version, bool) else None
    if filename is None:
        raise BatchError(
            f"{path}: unsupported batch manifest schema_version {version!r}; "
            f"this build reads {sorted(BATCH_SCHEMA_FILENAMES)}"
        )
    validate_or_raise(
        manifest,
        load_schema(config.schema_path(filename)),
        description=f"batch manifest {path} (schema version {version})",
    )
    return manifest
