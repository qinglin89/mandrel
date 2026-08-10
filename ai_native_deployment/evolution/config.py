"""Loading of `evolution/config.toml`.

The file is versioned policy, not a preferences file: several of its values
encode contract invariants (`evolution/README.md`) that this implementation
enforces rather than obeys. Where a setting could only ever weaken an
invariant, loading fails closed with the invariant named, instead of running a
half-honoured policy.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from ..hashing import sha256_bytes
from ..paths import source_root
from .errors import ConfigError

SCHEMA_VERSION = 1

CONFIG_RELATIVE_PATH = Path("evolution") / "config.toml"
SCHEMAS_RELATIVE_PATH = Path("evolution") / "schemas"

IMPORT_SCHEMA_FILENAME = "evaluation-import.schema.json"
BATCH_SCHEMA_FILENAME = "batch-manifest.schema.json"
LEDGER_SCHEMA_FILENAME = "ledger-record.schema.json"

STATE_FILENAME = "state.json"
LOCK_FILENAME = "lock"

SUPPORTED_SOURCE_KIND = "orch-hub"
SUPPORTED_COUNT_UNIT = "unique-completed-task"
SUPPORTED_DEDUPLICATION = "source-repo-and-task"

# The two storage ownerships of the contract's Data layout. Everything the
# controller writes belongs to exactly one of them: ignored machine-local
# runtime state under the runtime root, sanitized versioned records under the
# evolution workspace. The runtime root is pinned because it is the path this
# repository's .gitignore covers — a config that moves it does not relocate
# runtime state, it publishes raw evidence.
VERSIONED_ROOT = CONFIG_RELATIVE_PATH.parent.as_posix()
SUPPORTED_RUNTIME_ROOT = ".ai-evolution"
VERSIONED_STORAGE_KEYS = ("ledger", "batches", "cases", "experiments")

# Paths inside those two trees that the controller addresses by fixed name.
# None of them can also be a configurable storage location, in either
# direction: a location that contains one, equals one, or sits inside one takes
# a path away from the code that writes it by name.
FIXED_PATHS = (
    (f"{SUPPORTED_RUNTIME_ROOT}/{STATE_FILENAME}", "the runtime state file"),
    (f"{SUPPORTED_RUNTIME_ROOT}/{LOCK_FILENAME}", "the single-writer lock"),
    (CONFIG_RELATIVE_PATH.as_posix(), "this policy file"),
    (SCHEMAS_RELATIVE_PATH.as_posix(), "the versioned schemas every validator loads"),
)

# Settings whose only legal value is `true`, and the reason. Turning one off
# does not configure a laxer controller — it describes one that cannot exist,
# either because the versioned import schema hard-requires the same thing or
# because an invariant in evolution/README.md forbids the alternative.
REQUIRED_TRUE = {
    ("source", "completed_l1_l2_only"): "eligibility is complete-L1+L2 only (contract: purpose and ownership)",
    ("batch", "human_trigger_only"): "invariant 9: humans trigger batch formation",
    ("eligibility", "require_archived_task"): "evaluation-import.schema.json pins source.archived to true",
    ("eligibility", "require_evidence"): "evaluation-import.schema.json requires artifacts.evidence",
    ("eligibility", "require_static_metrics"): "evaluation-import.schema.json requires artifacts.static_metrics",
    ("eligibility", "require_semantic_report"): "evaluation-import.schema.json requires artifacts.semantic_report",
    ("eligibility", "include_reports_without_findings"): "invariant 2: import the denominator",
    ("analysis", "allow_no_change"): "invariant 7: no change is a valid conclusion",
    ("analysis", "separate_analysis_and_implementation"): "invariant 6: analysis is not implementation",
}


@dataclass(frozen=True)
class SourceConfig:
    kind: str
    url_env: str
    token_env: str
    report_feed_path: str
    completed_l1_l2_only: bool


@dataclass(frozen=True)
class BatchConfig:
    target_task_count: int
    minimum_task_count: int
    max_wait_days: int
    count_unit: str
    maximum_open_analysis_batches: int
    human_trigger_only: bool


@dataclass(frozen=True)
class EligibilityConfig:
    require_archived_task: bool
    require_evidence: bool
    require_static_metrics: bool
    require_semantic_report: bool
    include_reports_without_findings: bool
    deduplicate_by: str


@dataclass(frozen=True)
class AnalysisConfig:
    minimum_cluster_task_count: int
    allow_no_change: bool
    separate_analysis_and_implementation: bool


@dataclass(frozen=True)
class StorageConfig:
    runtime_root: str
    imported_artifacts: str
    ledger: str
    batches: str
    cases: str
    experiments: str


@dataclass(frozen=True)
class EvolutionConfig:
    """Effective policy plus the repository it applies to.

    `sha256` is the hash of the config file's bytes. A frozen batch records it
    so a later reader can tell whether the admission policy that produced the
    batch is the one in the tree today.
    """

    repo_root: Path
    path: Path
    sha256: str
    source: SourceConfig
    batch: BatchConfig
    eligibility: EligibilityConfig
    analysis: AnalysisConfig
    storage: StorageConfig

    @property
    def runtime_root(self) -> Path:
        return self.repo_root / self.storage.runtime_root

    @property
    def artifacts_root(self) -> Path:
        return self.repo_root / self.storage.imported_artifacts

    @property
    def ledger_path(self) -> Path:
        return self.repo_root / self.storage.ledger

    @property
    def batches_root(self) -> Path:
        return self.repo_root / self.storage.batches

    @property
    def state_path(self) -> Path:
        return self.runtime_root / STATE_FILENAME

    @property
    def lock_path(self) -> Path:
        return self.runtime_root / LOCK_FILENAME

    @property
    def schemas_root(self) -> Path:
        return self.repo_root / SCHEMAS_RELATIVE_PATH

    def schema_path(self, filename: str) -> Path:
        return self.schemas_root / filename

    def artifacts_relative(self, directory_name: str) -> str:
        """Repo-relative POSIX path recorded in state for one report's bundle."""

        return f"{Path(self.storage.imported_artifacts).as_posix()}/{directory_name}"


def load_config(repo_root: Path | None = None, *, config_path: Path | None = None) -> EvolutionConfig:
    root = (repo_root or source_root()).expanduser().resolve()
    path = config_path or (root / CONFIG_RELATIVE_PATH)
    if not path.is_file():
        raise ConfigError(f"missing evolution config: {path}")

    raw_bytes = path.read_bytes()
    try:
        data = tomllib.loads(raw_bytes.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"invalid evolution config: {path}: {exc}") from exc

    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ConfigError(
            f"unsupported evolution config schema_version {version!r} in {path}; this build supports {SCHEMA_VERSION}"
        )

    source = SourceConfig(
        kind=_string(data, "source", "kind", path),
        url_env=_string(data, "source", "url_env", path),
        token_env=_string(data, "source", "token_env", path),
        report_feed_path=_string(data, "source", "report_feed_path", path),
        completed_l1_l2_only=_bool(data, "source", "completed_l1_l2_only", path),
    )
    batch = BatchConfig(
        target_task_count=_int(data, "batch", "target_task_count", path),
        minimum_task_count=_int(data, "batch", "minimum_task_count", path),
        max_wait_days=_int(data, "batch", "max_wait_days", path),
        count_unit=_string(data, "batch", "count_unit", path),
        maximum_open_analysis_batches=_int(data, "batch", "maximum_open_analysis_batches", path),
        human_trigger_only=_bool(data, "batch", "human_trigger_only", path),
    )
    eligibility = EligibilityConfig(
        require_archived_task=_bool(data, "eligibility", "require_archived_task", path),
        require_evidence=_bool(data, "eligibility", "require_evidence", path),
        require_static_metrics=_bool(data, "eligibility", "require_static_metrics", path),
        require_semantic_report=_bool(data, "eligibility", "require_semantic_report", path),
        include_reports_without_findings=_bool(data, "eligibility", "include_reports_without_findings", path),
        deduplicate_by=_string(data, "eligibility", "deduplicate_by", path),
    )
    analysis = AnalysisConfig(
        minimum_cluster_task_count=_int(data, "analysis", "minimum_cluster_task_count", path),
        allow_no_change=_bool(data, "analysis", "allow_no_change", path),
        separate_analysis_and_implementation=_bool(data, "analysis", "separate_analysis_and_implementation", path),
    )
    storage = StorageConfig(
        runtime_root=_contained(data, "storage", "runtime_root", path, root),
        imported_artifacts=_contained(data, "storage", "imported_artifacts", path, root),
        ledger=_contained(data, "storage", "ledger", path, root),
        batches=_contained(data, "storage", "batches", path, root),
        cases=_contained(data, "storage", "cases", path, root),
        experiments=_contained(data, "storage", "experiments", path, root),
    )

    _check_supported(path, source, batch, eligibility, analysis, storage)

    return EvolutionConfig(
        repo_root=root,
        path=path,
        sha256=sha256_bytes(raw_bytes),
        source=source,
        batch=batch,
        eligibility=eligibility,
        analysis=analysis,
        storage=storage,
    )


def _check_supported(
    path: Path,
    source: SourceConfig,
    batch: BatchConfig,
    eligibility: EligibilityConfig,
    analysis: AnalysisConfig,
    storage: StorageConfig,
) -> None:
    sections = {"source": source, "batch": batch, "eligibility": eligibility, "analysis": analysis}
    for (section, key), reason in REQUIRED_TRUE.items():
        if getattr(sections[section], key) is not True:
            raise ConfigError(f"{path}: [{section}].{key} must be true — {reason}")

    if source.kind != SUPPORTED_SOURCE_KIND:
        raise ConfigError(f"{path}: unsupported [source].kind {source.kind!r}; this build implements {SUPPORTED_SOURCE_KIND!r}")
    if batch.count_unit != SUPPORTED_COUNT_UNIT:
        raise ConfigError(f"{path}: unsupported [batch].count_unit {batch.count_unit!r}; invariant 1 counts {SUPPORTED_COUNT_UNIT!r}")
    if eligibility.deduplicate_by != SUPPORTED_DEDUPLICATION:
        raise ConfigError(
            f"{path}: unsupported [eligibility].deduplicate_by {eligibility.deduplicate_by!r}; "
            f"this build implements {SUPPORTED_DEDUPLICATION!r}"
        )

    if batch.minimum_task_count < 1:
        raise ConfigError(f"{path}: [batch].minimum_task_count must be at least 1")
    if batch.target_task_count < batch.minimum_task_count:
        raise ConfigError(
            f"{path}: [batch].target_task_count ({batch.target_task_count}) is below "
            f"minimum_task_count ({batch.minimum_task_count})"
        )
    if batch.max_wait_days < 0:
        raise ConfigError(f"{path}: [batch].max_wait_days must not be negative")
    if batch.maximum_open_analysis_batches != 1:
        raise ConfigError(
            f"{path}: [batch].maximum_open_analysis_batches must be 1 — invariant 12 serializes admitted work"
        )
    if analysis.minimum_cluster_task_count < 1:
        raise ConfigError(f"{path}: [analysis].minimum_cluster_task_count must be at least 1")

    _check_storage(path, storage)


def _check_storage(path: Path, storage: StorageConfig) -> None:
    """Hold the ownership boundary the contract's Data layout draws.

    Repository containment is not enough: `evolution/cases` is inside the
    repository and is also tracked, so a runtime root pointed there would write
    raw fetched bundles straight into Git (invariant 11). Each location must
    therefore sit on the correct side of the boundary, the two sides must not
    overlap, and no configurable location may claim a path the controller
    already addresses by fixed name.
    """

    if storage.runtime_root != SUPPORTED_RUNTIME_ROOT:
        raise ConfigError(
            f"{path}: [storage].runtime_root must be {SUPPORTED_RUNTIME_ROOT!r}, got {storage.runtime_root!r} — "
            "invariant 11 keeps raw imported artifacts out of Git, and that is the path this repository ignores"
        )
    if not _inside(storage.imported_artifacts, storage.runtime_root):
        raise ConfigError(
            f"{path}: [storage].imported_artifacts must live under [storage].runtime_root "
            f"({storage.runtime_root}/), got {storage.imported_artifacts!r} — raw fetched bundles are runtime data"
        )

    for key in VERSIONED_STORAGE_KEYS:
        value = getattr(storage, key)
        if not _inside(value, VERSIONED_ROOT):
            raise ConfigError(
                f"{path}: [storage].{key} must live under {VERSIONED_ROOT}/, got {value!r} — "
                "the versioned workspace is where sanitized, committable records belong"
            )

    if _overlaps(storage.runtime_root, VERSIONED_ROOT):
        raise ConfigError(
            f"{path}: [storage].runtime_root {storage.runtime_root!r} overlaps the versioned workspace "
            f"{VERSIONED_ROOT}/ — ignored runtime state and committed records never share a tree"
        )

    for index, key in enumerate(VERSIONED_STORAGE_KEYS):
        for other in VERSIONED_STORAGE_KEYS[index + 1 :]:
            if _overlaps(getattr(storage, key), getattr(storage, other)):
                raise ConfigError(
                    f"{path}: [storage].{key} and [storage].{other} overlap; each versioned location "
                    "owns its own path"
                )

    # Being on the correct side of the boundary is still not enough: each tree
    # holds paths this controller writes by fixed name, and a configurable
    # location that lands on one of them is a config that loads and then cannot
    # run. Staging under `.ai-evolution/state.json/` creates the state file as a
    # directory, so the atomic commit at the end of the page fails; a ledger
    # pointed at `evolution/config.toml` appends audit lines over the policy
    # this loader just read.
    for key in ("imported_artifacts",) + VERSIONED_STORAGE_KEYS:
        value = getattr(storage, key)
        for fixed, owner in FIXED_PATHS:
            if _overlaps(value, fixed):
                raise ConfigError(
                    f"{path}: [storage].{key} {value!r} collides with {fixed} — {owner}, "
                    "and the fixed paths of each tree are not configurable"
                )


def _inside(child: str, parent: str) -> bool:
    """True when `child` lies strictly inside `parent`. Both are normalized
    repository-relative POSIX paths, so a prefix test is exact."""

    return child.startswith(parent + "/")


def _overlaps(first: str, second: str) -> bool:
    return first == second or _inside(first, second) or _inside(second, first)


def _section(data: dict[str, object], section: str, path: Path) -> dict[str, object]:
    value = data.get(section)
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: missing [{section}] section")
    return value


def _value(data: dict[str, object], section: str, key: str, path: Path) -> object:
    table = _section(data, section, path)
    if key not in table:
        raise ConfigError(f"{path}: missing [{section}].{key}")
    return table[key]


def _string(data: dict[str, object], section: str, key: str, path: Path) -> str:
    value = _value(data, section, key, path)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{path}: [{section}].{key} must be a non-empty string")
    return value


def _bool(data: dict[str, object], section: str, key: str, path: Path) -> bool:
    value = _value(data, section, key, path)
    if not isinstance(value, bool):
        raise ConfigError(f"{path}: [{section}].{key} must be a boolean")
    return value


def _int(data: dict[str, object], section: str, key: str, path: Path) -> int:
    value = _value(data, section, key, path)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"{path}: [{section}].{key} must be an integer")
    return value


def _contained(data: dict[str, object], section: str, key: str, path: Path, root: Path) -> str:
    """Storage locations are repository-relative by contract. An absolute or
    escaping path would put runtime state, raw bundles, or the audit ledger
    somewhere this tool has no business writing."""

    raw = _string(data, section, key, path)
    candidate = Path(raw)
    if candidate.is_absolute():
        raise ConfigError(f"{path}: [{section}].{key} must be repository-relative, got {raw!r}")
    # Rejected rather than resolved away: `evolution/../elsewhere` stays inside
    # the repository but lands outside the tree its name claims, and every
    # ownership comparison below is a comparison of these strings.
    if ".." in candidate.parts:
        raise ConfigError(f"{path}: [{section}].{key} must not contain '..', got {raw!r}")
    resolved = (root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise ConfigError(f"{path}: [{section}].{key} escapes the repository: {raw!r}")
    return candidate.as_posix()
