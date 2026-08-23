"""Canonical payload deployment and status checks."""

from __future__ import annotations

import os
import re
import stat
import shutil
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import hashing, lockfile, manifest, registry
from .paths import (
    CANONICAL_DIRNAME,
    GITIGNORE_BEGIN,
    GITIGNORE_END,
    MANIFEST_FILENAME,
    canonical_root,
    claude_user_skills_root,
    registry_path,
    source_root,
    target_path_identity,
)

PAYLOADS: tuple[tuple[str, str], ...] = (
    ("repo-root", ""),
    ("protocols", ".ai-protocol/protocols"),
    ("workflow", ".ai-protocol/workflow"),
    ("meta", ".ai-protocol/meta"),
    ("cursor", ".cursor"),
    ("codex", ".codex"),
    ("claude", ".claude"),
    ("orchestrator", ".mandrel/orchestrator"),
)
BUCKET_TARGET_PREFIXES: dict[str, str] = dict(PAYLOADS)

# The Codex CLI reads `.codex/config.toml`; canonical carries the template it is
# rendered from, and a `config.toml` sitting beside that template is a local
# file the deploy never carries.
CODEX_CONFIG_TEMPLATE = f"{CANONICAL_DIRNAME}/codex/config.toml.template"
CODEX_CONFIG_LOCAL = f"{CANONICAL_DIRNAME}/codex/config.toml"
CODEX_CONFIG_TARGET = ".codex/config.toml"

# /ai-coding*.md stays during the layout transition: targets deployed before
# the .ai-protocol/ cut still carry the legacy files untracked; drop the line
# once every target has had its legacy docs removed after the deploy wave.
GITIGNORE_BLOCK = f"""{GITIGNORE_BEGIN}
/.ai-deploy-manifest.json
/.ai-protocol/
/.mandrel/
/.cursor/
/.codex/
/.claude/
/CLAUDE.md
/ai-coding*.md
/.ai-tasks/
__pycache__/
*.py[cod]
!/.ai-deploy-lock.json
{GITIGNORE_END}
"""

GITIGNORE_BLOCK_PATTERN = re.compile(
    rf"{re.escape(GITIGNORE_BEGIN)}.*?{re.escape(GITIGNORE_END)}\n?",
    re.DOTALL,
)

CLAUDE_MD_TARGET = "CLAUDE.md"
NORMALIZED_SHA256_FIELD = "normalized_sha256"
# The permission bits the deploy applied to a target file, recorded per manifest
# record. A deployed file is bytes and a mode — a hook stripped of its executable
# bit is the payload's content and not the payload's behavior — so the receipt
# has to state the mode for `status` to answer for it. Absent from manifests
# written before this field existed; `_manifest_mode` reads that as a receipt
# that states no mode rather than as a mode of zero.
MODE_FIELD = "mode"
MODE_BITS = 0o7777
# The eager memory docs that the memory protocol allows to be upgraded to
# directory form. Ordered, and kept in lockstep with the `add_eager_entrypoint`
# calls in the cursor/codex session-start hooks (tests/test_hook_eager_set.py).
CLAUDE_MD_MEMORY_IMPORT_TOPICS: tuple[str, ...] = (
    "overview",
    "architecture",
    "design",
    "conventions",
)
AI_ROUTER_PATH = ".ai/index.md"
# Where the claude payload bucket puts workflow skills in a target, and the
# file an agent tool needs to find to treat a directory there as a skill.
DEPLOYED_SKILLS_ROOT = PurePosixPath(".claude/skills")
SKILL_MANIFEST_FILENAME = "SKILL.md"


class PayloadError(RuntimeError):
    """Raised when the canonical payload cannot be carried into a target as
    written, so no surface can answer for it."""


@dataclass(frozen=True)
class DeploymentItem:
    canonical_relative_path: str
    target_relative_path: str
    source_path: Path
    mode: int
    render_template: bool = False

    def render(self, source_bytes: bytes, target_root: Path) -> bytes:
        """What this target file becomes, from the canonical bytes it is made
        of. Kept separate from reading those bytes so a deploy can write the
        target and describe what it wrote from one reading of the source."""
        if self.render_template:
            data = source_bytes.decode("utf-8").replace("{{REPO_ROOT}}", str(target_root)).encode("utf-8")
        else:
            data = source_bytes
        if self.target_relative_path == CLAUDE_MD_TARGET:
            data = _resolve_claude_md_memory_imports(data, target_root)
        return data

    def bytes_for_target(self, target_root: Path) -> bytes:
        return self.render(self.source_path.read_bytes(), target_root)


@dataclass(frozen=True)
class Drift:
    kind: str
    target_relative_path: str
    detail: str = ""


@dataclass(frozen=True)
class StatusResult:
    target_root: Path
    total_files: int
    drifts: tuple[Drift, ...]

    @property
    def in_sync(self) -> bool:
        return not self.drifts


@dataclass(frozen=True)
class DeployPreviewChange:
    action: str
    target_relative_path: str
    detail: str = ""


@dataclass(frozen=True)
class DeployPreviewResult:
    target_root: Path
    total_files: int
    changes: tuple[DeployPreviewChange, ...]
    gitignore_action: str

    @property
    def changed(self) -> tuple[DeployPreviewChange, ...]:
        return tuple(change for change in self.changes if change.action != "unchanged")


@dataclass(frozen=True)
class OrchestratorBootstrapResult:
    target_root: Path
    orchestrator_root: Path
    venv_path: Path
    python_path: Path
    requirements_path: Path
    env_path: Path
    env_created: bool
    python_executable: str


def _posix(path: Path | PurePosixPath | str) -> str:
    return str(path).replace(os.sep, "/")


def _venv_python_path(venv_path: Path) -> Path:
    if os.name == "nt":
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"


def memory_entrypoint_forms(topic: str) -> tuple[str, str]:
    """The two legal entrypoint paths for an eager memory topic: the initial
    single-file form and its directory-form upgrade."""
    return f".ai/{topic}.md", f".ai/{topic}/index.md"


def _memory_import_topic(stripped: str) -> str | None:
    """Topic name when `stripped` is an eager `@.ai/<topic>` import line in
    either legal form, else None."""
    if not stripped.startswith("@.ai/"):
        return None
    path = stripped[1:]
    for topic in CLAUDE_MD_MEMORY_IMPORT_TOPICS:
        if path in memory_entrypoint_forms(topic):
            return topic
    return None


def _rewrite_claude_md_lines(data: bytes, rewrite: Callable[[str], str | None]) -> bytes:
    """Apply a line rewriter to the loader, preserving trailing whitespace and
    line endings. The rewriter returns a replacement body or None to keep."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data

    rewritten_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        newline = line[len(body) :]
        stripped = body.rstrip(" \t")
        trailing = body[len(stripped) :]

        replacement = rewrite(stripped)
        if replacement is not None:
            body = f"{replacement}{trailing}"

        rewritten_lines.append(body + newline)

    return "".join(rewritten_lines).encode("utf-8")


def _normalize_claude_md_memory_imports(data: bytes) -> bytes:
    """Treat the eager `@.ai/<topic>.md` imports in the loader (CLAUDE.md)
    and their directory-form `@.ai/<topic>/index.md` upgrades as the same
    content for status purposes. Applies to the four upgradeable memory
    topics only, anywhere in the file — the loader is fully deploy-owned.

    This deliberately makes the content hash blind to which form is deployed,
    so a loader left pointing at the pre-upgrade path cannot be caught by
    hashing; `eager_import_drifts` checks that separately."""

    def to_flat_form(stripped: str) -> str | None:
        topic = _memory_import_topic(stripped)
        if topic is None:
            return None
        flat, _dir_index = memory_entrypoint_forms(topic)
        return f"@{flat}"

    return _rewrite_claude_md_lines(data, to_flat_form)


def _ai_router_entries(target_root: Path) -> dict[str, str]:
    """Parse the `.ai/index.md` document table into {lowercased label: path},
    matching how the session-start hooks read it: second table column is the
    label, third is the path. First row wins."""
    router_path = target_root / AI_ROUTER_PATH
    if not router_path.is_file():
        return {}
    try:
        text = router_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}

    entries: dict[str, str] = {}
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = line.split("|")
        if len(cells) < 3:
            continue
        label = cells[1].strip().lower()
        routed = cells[2].strip().replace("`", "")
        if label and routed and label not in entries:
            entries[label] = routed
    return entries


def _routed_memory_path(router: dict[str, str], topic: str) -> str | None:
    """The `.ai/`-relative path the router assigns to a topic, or None when the
    topic is unrouted or the entry escapes `.ai/`."""
    routed = router.get(topic)
    if not routed:
        return None
    if routed.startswith("./"):
        routed = routed[2:]
    if routed.startswith("/") or ".." in PurePosixPath(routed).parts:
        return None
    if not routed.startswith(".ai/"):
        routed = f".ai/{routed}"
    return routed


def resolve_memory_entrypoint(target_root: Path, topic: str, router: dict[str, str] | None = None) -> str:
    """Current entrypoint for an eager memory topic in a target repo: router
    first, then file shape, then the single-file default. Mirrors
    `add_eager_entrypoint` in the cursor/codex session-start hooks so every
    surface loads the same file."""
    flat, dir_index = memory_entrypoint_forms(topic)
    if router is None:
        router = _ai_router_entries(target_root)

    routed = _routed_memory_path(router, topic)
    if routed in (flat, dir_index) and (target_root / routed).is_file():
        return routed
    if (target_root / dir_index).is_file() and not (target_root / flat).is_file():
        return dir_index
    return flat


def _resolve_claude_md_memory_imports(data: bytes, target_root: Path) -> bytes:
    """Point the loader's eager `@.ai/<topic>` imports at the entrypoint that
    actually exists in the target's `.ai/`, so a deploy re-derives a §4
    directory-form upgrade instead of silently reverting it. Targets without a
    `.ai/` snapshot keep the canonical single-file form."""
    router = _ai_router_entries(target_root)

    def to_current_entrypoint(stripped: str) -> str | None:
        topic = _memory_import_topic(stripped)
        if topic is None:
            return None
        return f"@{resolve_memory_entrypoint(target_root, topic, router=router)}"

    return _rewrite_claude_md_lines(data, to_current_entrypoint)


def _status_bytes(item: DeploymentItem, data: bytes) -> bytes:
    if item.target_relative_path == CLAUDE_MD_TARGET:
        return _normalize_claude_md_memory_imports(data)
    return data


def _status_sha256(item: DeploymentItem, data: bytes) -> str:
    return hashing.sha256_bytes(_status_bytes(item, data))


def _manifest_status_hash(record: dict[object, object], manifest_hash: str) -> str:
    normalized_hash = record.get(NORMALIZED_SHA256_FIELD)
    if isinstance(normalized_hash, str):
        return normalized_hash
    return manifest_hash


def _manifest_mode(record: dict[object, object]) -> int | None:
    """The mode this record vouches for, or None where it vouches for none.

    The expected mode comes from the receipt and never from a fresh reading of
    the canonical item, even though `check_status` has that item in hand. The
    receipt is what the deploy left behind, and the two questions `status`
    answers are both asked against it: a target that no longer carries the
    deployed mode is `target modified`, and a canonical file whose mode has
    moved since is `canonical changed`. Substituting the current source for the
    receipt collapses them into one — the target would be blamed for a mode the
    canonical tree changed under it.

    `bool` is excluded because it is an `int` in Python and a receipt reading
    `"mode": true` states nothing about permission bits."""
    mode = record.get(MODE_FIELD)
    if isinstance(mode, bool) or not isinstance(mode, int):
        return None
    if not 0 <= mode <= MODE_BITS:
        # Permission bits, which is what a deploy chmods and what a target
        # stats. A whole `st_mode` carries the file type above them and is not
        # the value either side of the comparison holds.
        return None
    return mode


def _divergence_detail(content_changed: bool, mode_changed: bool) -> str:
    """How a file diverges, in one vocabulary for `status` and for
    `preview_deploy`: the same target state has to read the same way whether it
    is described as drift from a receipt or as work a deploy would do."""
    if content_changed and mode_changed:
        return "content and mode differ"
    if mode_changed:
        return "mode differs"
    return ""


def is_forbidden_relative_path(path: Path | PurePosixPath | str) -> bool:
    parts = PurePosixPath(_posix(path)).parts
    if not parts:
        return False

    if any(part in {".git", ".venv", "__pycache__", "logs", ".logs"} for part in parts):
        return True

    filename = parts[-1]
    if filename in {"sessions.json", "settings.local.json"}:
        return True
    if filename == ".env":
        return True
    if filename.startswith(".env.") and filename != ".env.example":
        return True
    if filename.endswith((".pyc", ".pyo")):
        return True

    for index, part in enumerate(parts[:-1]):
        if part in {"claude", ".claude"} and parts[index + 1] == "projects":
            return True
    return False


def target_relative_path_for(canonical_relative_path: str) -> str | None:
    """Where the payload mapping carries one canonical file, or `None` when it
    carries it nowhere.

    Derived from the path alone, so it answers for a canonical path a commit
    holds exactly as it answers for one in the working tree. That is what lets
    the deploy receipt ask which canonical files a commit expected the payload
    to carry (`lockfile.payload_source_commit`) without walking a tree twice and
    getting two different answers."""

    parts = PurePosixPath(canonical_relative_path).parts
    if len(parts) < 3 or parts[0] != CANONICAL_DIRNAME:
        return None
    target_prefix = BUCKET_TARGET_PREFIXES.get(parts[1])
    if target_prefix is None:
        return None

    bucket_rel = PurePosixPath(*parts[2:])
    if canonical_relative_path == CODEX_CONFIG_TEMPLATE:
        target_rel = PurePosixPath(CODEX_CONFIG_TARGET)
    elif canonical_relative_path == CODEX_CONFIG_LOCAL:
        return None
    elif target_prefix:
        target_rel = PurePosixPath(target_prefix) / bucket_rel
    else:
        target_rel = bucket_rel

    if is_forbidden_relative_path(canonical_relative_path) or is_forbidden_relative_path(target_rel):
        return None
    return target_rel.as_posix()


def deploys_canonical_path(canonical_relative_path: str) -> bool:
    return target_relative_path_for(canonical_relative_path) is not None


def iter_deployment_items(root: Path | None = None) -> list[DeploymentItem]:
    """The payload this canonical tree carries into a target: one item per file
    the target receives.

    One item per *target* file, which is what makes it a payload. The buckets
    overlap by design: `repo-root` maps to the target root, so it reaches under
    every other bucket's prefix. Two canonical files can therefore name one
    target file, and a target file holds the bytes of one of them. Deployed in
    bucket order, the later write would silently replace the earlier one while
    both receipts describe the writes rather than the files left behind: the
    lock would list two records for a file the target holds one of, and state a
    canonical commit over bytes no target file carries, which is the placement a
    release assessment reads as evidence (`evolution/README.md`, Release
    assessment). Neither the manifest nor `check_status` would see it — both key
    by target path, so the collision collapses and the dropped file's absence
    reads as in sync.

    Two canonical files name one target file whenever their target paths are
    the same file, which is `target_path_identity` and not string equality: on
    the supported hosts `.CURSOR/hooks/x` and `.cursor/hooks/x` are one file,
    and a payload carrying both drops one exactly as an identical pair would,
    while every string-keyed check above still sees two.

    Refused here rather than in any of them, because this is the one mapping all
    of them read: a canonical tree that cannot say which file a target path gets
    has no payload to deploy, preview, or check.
    """

    root = (root or source_root()).resolve()
    canonical = canonical_root(root)
    items: list[DeploymentItem] = []
    claimed: dict[str, tuple[str, str]] = {}

    for bucket, _target_prefix in PAYLOADS:
        bucket_root = canonical / bucket
        if not bucket_root.exists():
            continue
        for source_path in sorted(bucket_root.rglob("*")):
            if not source_path.is_file():
                continue
            canonical_rel = source_path.relative_to(root).as_posix()
            target_rel = target_relative_path_for(canonical_rel)
            if target_rel is None:
                continue
            claimant, claimed_rel = claimed.setdefault(
                target_path_identity(target_rel), (canonical_rel, target_rel)
            )
            if claimant != canonical_rel:
                where = (
                    target_rel
                    if claimed_rel == target_rel
                    else f"{claimed_rel} and {target_rel}, one file on a case- or normalization-insensitive volume"
                )
                raise PayloadError(
                    f"two canonical files map to {where}: {claimant} and {canonical_rel}; "
                    "a target path holds one file, so the payload cannot carry both"
                )
            items.append(
                DeploymentItem(
                    canonical_relative_path=canonical_rel,
                    target_relative_path=target_rel,
                    source_path=source_path,
                    mode=stat.S_IMODE(source_path.stat().st_mode),
                    render_template=canonical_rel == CODEX_CONFIG_TEMPLATE,
                )
            )
    return items


def append_gitignore_block(target_root: Path) -> Path:
    path = target_root / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if GITIGNORE_BEGIN in existing and GITIGNORE_END in existing:
        updated = GITIGNORE_BLOCK_PATTERN.sub(GITIGNORE_BLOCK, existing)
        if updated != existing:
            path.write_text(updated, encoding="utf-8")
        return path

    separator = "" if not existing or existing.endswith("\n") else "\n"
    if existing and not existing.endswith("\n\n"):
        separator += "\n"
    path.write_text(existing + separator + GITIGNORE_BLOCK, encoding="utf-8")
    return path


def planned_gitignore_action(target_root: Path) -> str:
    path = target_root / ".gitignore"
    if not path.exists():
        return "add"
    existing = path.read_text(encoding="utf-8")
    if GITIGNORE_BEGIN in existing and GITIGNORE_END in existing:
        updated = GITIGNORE_BLOCK_PATTERN.sub(GITIGNORE_BLOCK, existing)
        return "unchanged" if updated == existing else "update"
    return "update"


def preview_deploy(target: str | Path, *, root: Path | None = None) -> DeployPreviewResult:
    root = (root or source_root()).resolve()
    target_root = Path(target).expanduser().resolve()
    if not target_root.is_dir():
        raise FileNotFoundError(f"target repo does not exist: {target_root}")

    changes: list[DeployPreviewChange] = []
    for item in iter_deployment_items(root):
        target_path = target_root / item.target_relative_path
        rendered_bytes = item.bytes_for_target(target_root)
        detail = ""

        if not target_path.exists():
            action = "add"
        elif not target_path.is_file():
            action = "blocked"
            detail = "target path exists but is not a file"
        else:
            content_changed = target_path.read_bytes() != rendered_bytes
            mode_changed = stat.S_IMODE(target_path.stat().st_mode) != item.mode
            if content_changed or mode_changed:
                action = "update"
                detail = _divergence_detail(content_changed, mode_changed)
            else:
                action = "unchanged"

        changes.append(
            DeployPreviewChange(
                action=action,
                target_relative_path=item.target_relative_path,
                detail=detail,
            )
        )

    return DeployPreviewResult(
        target_root=target_root,
        total_files=len(changes),
        changes=tuple(changes),
        gitignore_action=planned_gitignore_action(target_root),
    )


def deploy_canonical(
    target: str | Path,
    *,
    root: Path | None = None,
    registry_file: Path | None = None,
) -> dict[str, object]:
    root = (root or source_root()).resolve()
    target_root = Path(target).expanduser().resolve()
    if not target_root.is_dir():
        raise FileNotFoundError(f"target repo does not exist: {target_root}")

    manifest_records: list[dict[str, int | str]] = []
    deployed_files: list[lockfile.DeployedFile] = []
    for item in iter_deployment_items(root):
        target_path = target_root / item.target_relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        # One reading of the source, and the receipt describes it. Reading it
        # again after the target is written would describe a file that is no
        # longer the payload's, and a lock is read as an account of the payload.
        source_bytes = item.source_path.read_bytes()
        rendered_bytes = item.render(source_bytes, target_root)
        target_path.write_bytes(rendered_bytes)
        target_path.chmod(item.mode)
        file_info = hashing.file_record(target_path)
        manifest_record = {
            "canonical_relative_path": item.canonical_relative_path,
            "target_relative_path": item.target_relative_path,
            "sha256": file_info["sha256"],
            "size_bytes": file_info["size_bytes"],
            # The mode the deploy applied, like the lock's `executable` beside
            # it: both receipts describe this deployment's own act. A mode read
            # back off the target instead would record whatever the filesystem
            # made of the chmod, so a volume that drops the executable bit would
            # produce a receipt vouching for a hook that cannot run.
            MODE_FIELD: item.mode,
        }
        if item.target_relative_path == CLAUDE_MD_TARGET:
            manifest_record[NORMALIZED_SHA256_FIELD] = _status_sha256(item, rendered_bytes)
        manifest_records.append(manifest_record)
        deployed_files.append(
            lockfile.DeployedFile(
                canonical_relative_path=item.canonical_relative_path,
                target_relative_path=item.target_relative_path,
                canonical_sha256=hashing.sha256_bytes(source_bytes),
                canonical_size_bytes=len(source_bytes),
                executable=bool(item.mode & 0o111),
            )
        )

    append_gitignore_block(target_root)
    deployed_manifest = manifest.build_manifest(source_root=root, target_root=target_root, files=manifest_records)
    manifest.write_manifest(target_root, deployed_manifest)
    deploy_lock = lockfile.build_lock(
        source_root=root,
        files=deployed_files,
        deploys=deploys_canonical_path,
    )
    lockfile.write_lock(target_root, deploy_lock)
    registry.add_repo(
        target_root,
        registry_file=registry_file or registry_path(root),
        source_root=root,
        require_manifest=True,
    )
    return deployed_manifest


def bootstrap_orchestrator(
    target: str | Path,
    *,
    python_executable: str = "python3.14",
) -> OrchestratorBootstrapResult:
    target_root = Path(target).expanduser().resolve()
    if not target_root.is_dir():
        raise FileNotFoundError(f"target repo does not exist: {target_root}")

    orchestrator_root = target_root / ".mandrel" / "orchestrator"
    if not orchestrator_root.is_dir():
        raise FileNotFoundError(f"orchestrator payload is missing: {orchestrator_root}")

    requirements_path = orchestrator_root / "requirements.txt"
    if not requirements_path.is_file():
        raise FileNotFoundError(f"orchestrator requirements are missing: {requirements_path}")

    venv_path = orchestrator_root / ".venv"
    if venv_path.exists() and not venv_path.is_dir():
        raise FileExistsError(f"orchestrator venv path exists but is not a directory: {venv_path}")

    subprocess.run([python_executable, "-m", "venv", str(venv_path)], cwd=target_root, check=True)

    python_path = _venv_python_path(venv_path)
    if not python_path.is_file():
        raise FileNotFoundError(f"orchestrator venv python was not created: {python_path}")

    subprocess.run([str(python_path), "-m", "pip", "install", "-U", "pip"], cwd=target_root, check=True)
    subprocess.run(
        [str(python_path), "-m", "pip", "install", "-r", str(requirements_path)],
        cwd=target_root,
        check=True,
    )

    env_example_path = orchestrator_root / ".env.example"
    env_path = orchestrator_root / ".env"
    env_created = False
    if env_example_path.is_file() and not env_path.exists():
        shutil.copy2(env_example_path, env_path)
        env_created = True

    return OrchestratorBootstrapResult(
        target_root=target_root,
        orchestrator_root=orchestrator_root,
        venv_path=venv_path,
        python_path=python_path,
        requirements_path=requirements_path,
        env_path=env_path,
        env_created=env_created,
        python_executable=python_executable,
    )


def eager_import_drifts(target_root: Path) -> tuple[Drift, ...]:
    """Entrypoint checks the content hash cannot make.

    `_normalize_claude_md_memory_imports` deliberately treats both entrypoint
    forms as equal, so a loader left pointing at the pre-upgrade path — exactly
    what a memory §4 directory-form upgrade produces until the next deploy — is
    invisible to hash comparison. Reported explicitly instead."""
    if not (target_root / ".ai").is_dir():
        return ()

    router = _ai_router_entries(target_root)
    loader_imports: dict[str, str] = {}
    loader_path = target_root / CLAUDE_MD_TARGET
    if loader_path.is_file():
        try:
            loader_text = loader_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            loader_text = ""
        for line in loader_text.splitlines():
            stripped = line.strip()
            topic = _memory_import_topic(stripped)
            if topic is not None and topic not in loader_imports:
                loader_imports[topic] = stripped[1:]

    drifts: list[Drift] = []
    for topic in CLAUDE_MD_MEMORY_IMPORT_TOPICS:
        flat, dir_index = memory_entrypoint_forms(topic)

        if (target_root / flat).is_file() and (target_root / dir_index).is_file():
            drifts.append(
                Drift(
                    "ambiguous memory entrypoint",
                    flat,
                    f"both {flat} and {dir_index} exist; the directory-form upgrade renames, it does not duplicate",
                )
            )

        routed = _routed_memory_path(router, topic)
        raw_route = router.get(topic)
        if raw_route and routed is None:
            drifts.append(
                Drift("stale eager import", AI_ROUTER_PATH, f"{topic} routes to {raw_route}, which escapes .ai/")
            )
        elif routed is not None and routed not in (flat, dir_index):
            drifts.append(
                Drift("stale eager import", AI_ROUTER_PATH, f"{topic} routes to {routed}; expected {flat} or {dir_index}")
            )
        elif routed is not None and not (target_root / routed).is_file():
            drifts.append(
                Drift("stale eager import", AI_ROUTER_PATH, f"{topic} routes to {routed}, which does not exist")
            )

        current = resolve_memory_entrypoint(target_root, topic, router=router)
        imported = loader_imports.get(topic)
        if imported is not None and imported != current:
            drifts.append(
                Drift("stale eager import", CLAUDE_MD_TARGET, f"imports @{imported}; current entrypoint is {current}")
            )

    return tuple(drifts)


def deployed_skill_names(target_relative_paths: Iterable[str]) -> tuple[str, ...]:
    """Skill names a deployed payload carries: the directory names directly
    under the target's skills root."""
    depth = len(DEPLOYED_SKILLS_ROOT.parts)
    names = set()
    for raw in target_relative_paths:
        parts = PurePosixPath(str(raw)).parts
        if len(parts) > depth and parts[:depth] == DEPLOYED_SKILLS_ROOT.parts:
            names.add(parts[depth])
    return tuple(sorted(names))


def shadowed_skill_drifts(
    skill_names: Iterable[str],
    *,
    user_skills_root: Path | None = None,
) -> tuple[Drift, ...]:
    """Precedence checks the content hash cannot make.

    Agent tools resolve same-named skills personal-level over project-level, so
    a leftover personal copy silently wins over the deployed one: the deploy
    succeeded, the manifest and the lock record the deployed file, and every
    hash matches — while the skill that actually executes is the stale one.
    Same blind spot as `ambiguous memory entrypoint`: two legal locations
    present, the wrong one winning, and no content comparison can see it."""
    root = claude_user_skills_root() if user_skills_root is None else Path(user_skills_root)
    if not root.is_dir():
        return ()

    drifts: list[Drift] = []
    for name in skill_names:
        shadow = root / name / SKILL_MANIFEST_FILENAME
        if shadow.is_file():
            drifts.append(
                Drift(
                    "shadowed skill",
                    str(DEPLOYED_SKILLS_ROOT / name),
                    f"{shadow} takes precedence; the deployed skill does not run until it is removed",
                )
            )
    return tuple(drifts)


# The complete status vocabulary, in report order. `format_status` prints only
# the kinds listed here, so a `Drift` kind missing from this tuple would be
# counted in the header and then never shown; tests/test_docs_getting_started.py
# asserts the tuple covers every kind deploy.py constructs, and that the
# operator-facing docs list the same set.
STATUS_IN_SYNC = "in sync"
STATUS_DRIFT_KINDS: tuple[str, ...] = (
    "missing manifest",
    "target modified",
    "canonical changed",
    "stale eager import",
    "ambiguous memory entrypoint",
    "shadowed skill",
    "missing target file",
    "extra deployed file",
    "invalid manifest entry",
)


def check_status(
    target: str | Path,
    *,
    root: Path | None = None,
    user_skills_root: Path | None = None,
) -> StatusResult:
    root = (root or source_root()).resolve()
    target_root = Path(target).expanduser().resolve()
    try:
        deployed_manifest = manifest.read_manifest(target_root)
    except manifest.ManifestError as exc:
        return StatusResult(target_root=target_root, total_files=0, drifts=(Drift("missing manifest", MANIFEST_FILENAME, str(exc)),))

    manifest_files = deployed_manifest["files"]
    assert isinstance(manifest_files, dict)
    current_items = {item.target_relative_path: item for item in iter_deployment_items(root)}
    drifts: list[Drift] = []
    modeless_records = 0

    for target_rel, raw_record in sorted(manifest_files.items()):
        if not isinstance(raw_record, dict):
            drifts.append(Drift("invalid manifest entry", str(target_rel), "record is not an object"))
            continue
        target_rel = str(raw_record.get("target_relative_path") or target_rel)
        manifest_hash = raw_record.get("sha256")
        if not isinstance(manifest_hash, str):
            drifts.append(Drift("invalid manifest entry", target_rel, "missing sha256"))
            continue
        manifest_status_hash = _manifest_status_hash(raw_record, manifest_hash)
        manifest_mode = _manifest_mode(raw_record)
        if manifest_mode is None:
            modeless_records += 1

        item = current_items.get(target_rel)
        if item is None:
            canonical_rel = raw_record.get("canonical_relative_path", "unknown canonical path")
            drifts.append(Drift("extra deployed file", target_rel, f"tracked from {canonical_rel} but absent from canonical payload"))
            continue

        target_path = target_root / target_rel
        canonical_bytes = item.bytes_for_target(target_root)
        canonical_hash = hashing.sha256_bytes(canonical_bytes)
        if not target_path.is_file():
            drifts.append(Drift("missing target file", target_rel))
        else:
            target_bytes = target_path.read_bytes()
            target_hash = hashing.sha256_bytes(target_bytes)
            target_status_hash = _status_sha256(item, target_bytes)
            content_changed = target_hash != manifest_hash and target_status_hash != manifest_status_hash
            mode_changed = manifest_mode is not None and stat.S_IMODE(target_path.stat().st_mode) != manifest_mode
            if content_changed or mode_changed:
                drifts.append(Drift("target modified", target_rel, _divergence_detail(content_changed, mode_changed)))

        canonical_status_hash = _status_sha256(item, canonical_bytes)
        canonical_content_changed = canonical_hash != manifest_hash and canonical_status_hash != manifest_status_hash
        canonical_mode_changed = manifest_mode is not None and item.mode != manifest_mode
        if canonical_content_changed or canonical_mode_changed:
            drifts.append(
                Drift(
                    "canonical changed",
                    target_rel,
                    _divergence_detail(canonical_content_changed, canonical_mode_changed),
                )
            )

    for target_rel in sorted(set(current_items) - {str(key) for key in manifest_files}):
        drifts.append(Drift("canonical changed", target_rel, "new canonical file not deployed"))

    if modeless_records:
        # A receipt written before modes were recorded, or one hand-edited since.
        # Reported rather than passed over: `status` is what the release contract
        # asks whether a target still matches its receipt
        # (`evolution/README.md`, Release assessment), and a silent skip would
        # let it vouch for an executable bit nothing compared. Said once for the
        # whole receipt, because that is where the gap is — the per-file checks
        # above still answer for content, so the target keeps a usable status
        # until the next deploy writes the modes in.
        drifts.append(
            Drift(
                "invalid manifest entry",
                MANIFEST_FILENAME,
                f"{modeless_records} of {len(manifest_files)} records state no deployed mode; "
                "redeploy so status can check modes",
            )
        )

    drifts.extend(eager_import_drifts(target_root))
    drifts.extend(
        shadowed_skill_drifts(
            deployed_skill_names(str(key) for key in manifest_files),
            user_skills_root=user_skills_root,
        )
    )

    return StatusResult(target_root=target_root, total_files=len(manifest_files), drifts=tuple(drifts))


def format_status(result: StatusResult, label: str | None = None) -> str:
    prefix = f"{label}: " if label else ""
    if result.in_sync:
        return f"{prefix}{STATUS_IN_SYNC} ({result.total_files} files)"

    lines = [f"{prefix}drift detected ({len(result.drifts)} issues)"]
    for kind in STATUS_DRIFT_KINDS:
        entries = [drift for drift in result.drifts if drift.kind == kind]
        if not entries:
            continue
        lines.append(f"  {kind}:")
        for drift in entries:
            detail = f" ({drift.detail})" if drift.detail else ""
            lines.append(f"    - {drift.target_relative_path}{detail}")
    return "\n".join(lines)


def format_deploy_preview(result: DeployPreviewResult) -> str:
    counts = {
        "add": 0,
        "update": 0,
        "unchanged": 0,
        "blocked": 0,
    }
    for change in result.changes:
        counts[change.action] = counts.get(change.action, 0) + 1

    lines = [
        f"{result.target_root}: dry-run deploy preview ({result.total_files} files)",
        f"  add: {counts['add']}, update: {counts['update']}, unchanged: {counts['unchanged']}, blocked: {counts['blocked']}",
        f"  gitignore: {result.gitignore_action}",
        "  manifest: would write .ai-deploy-manifest.json",
        "  lockfile: would write .ai-deploy-lock.json",
        "  registry: would add/update local registry entry",
    ]

    for action in ("blocked", "add", "update"):
        entries = [change for change in result.changes if change.action == action]
        if not entries:
            continue
        lines.append(f"  {action}:")
        for change in entries:
            detail = f" ({change.detail})" if change.detail else ""
            lines.append(f"    - {change.target_relative_path}{detail}")
    return "\n".join(lines)
