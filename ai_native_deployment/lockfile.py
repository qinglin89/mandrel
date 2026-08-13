"""Portable deploy lockfile creation."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Iterable

from .hashing import sha256_bytes
from .manifest import SCHEMA_VERSION, source_git_commit
from .paths import CANONICAL_DIRNAME, lock_path


def payload_source_commit(source_root: Path, canonical_relative_paths: Iterable[str]) -> str | None:
    """The canonical commit this payload can be said to have come from, if any.

    `HEAD` answers when a deploy ran, not what it deployed: the payload is read
    out of the working tree, so a canonical tree carrying uncommitted work
    produces bytes no commit holds. Downstream the field is read as a fact about
    those bytes rather than about the moment — a release assessment places every
    report a target produced by the revision that target held, as an ancestry
    test (`evolution/README.md`, Release assessment) — and a commit naming
    content the target never ran places reports on a side they did not belong
    to. The comparison is made here because this is where both sides are in
    hand: afterwards the receipt holds a digest of what was deployed and a
    commit written beside it, and closing that gap would mean re-deriving the
    commit's payload with the mapping code that commit carried. So the receipt
    states a revision only when Git says the payload is exactly that commit's:

    - nothing modified, staged, deleted or newly added under the canonical tree,
      and
    - no deployed file Git does not track — an ignored one is invisible to
      `status` and would otherwise pass as committed content.

    Anything else states no revision. That excludes such a report from a cohort,
    which costs a denominator, where the alternative manufactures a placement.

    The question is scoped to the canonical tree because that is where the
    payload's bytes come from; unrelated work elsewhere in the checkout says
    nothing about them. It is scoped to content, not to rendering: the lock
    hashes canonical sources, and which version of this tool rendered them into
    a target is a separate fact no receipt states.
    """

    commit = source_git_commit(source_root)
    if commit is None:
        return None
    changed = _git_stdout(source_root, ["status", "--porcelain", "--", CANONICAL_DIRNAME])
    if changed is None or changed.strip():
        return None
    untracked = _git_stdout(source_root, ["ls-files", "--others", "-z", "--", CANONICAL_DIRNAME])
    if untracked is None:
        return None
    unversioned = {path for path in untracked.split("\0") if path}
    if unversioned & set(canonical_relative_paths):
        return None
    return commit


def _git_stdout(source_root: Path, arguments: list[str]) -> str | None:
    """Git's answer, or `None` when it could not answer.

    A question Git refused is not a clean tree: it is the same "nothing can be
    said" a checkout without Git gives, and both leave the revision unstated.
    """

    try:
        result = subprocess.run(
            ["git", "-C", str(source_root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return result.stdout


def build_lock(
    *,
    source_root: Path,
    files: Iterable[dict[str, int | str]],
) -> dict[str, object]:
    deployed_files = [
        {
            "canonical_relative_path": str(record["canonical_relative_path"]),
            "target_relative_path": str(record["target_relative_path"]),
            "canonical_sha256": str(record["canonical_sha256"]),
            "canonical_size_bytes": int(record["canonical_size_bytes"]),
        }
        for record in sorted(files, key=lambda item: str(item["target_relative_path"]))
    ]
    payload = "\n".join(
        f"{item['target_relative_path']}\0{item['canonical_sha256']}" for item in deployed_files
    ).encode("utf-8")
    return {
        "schema_version": SCHEMA_VERSION,
        "source_repo": "ai-native-deployment",
        # Derived here and nowhere else: a caller-supplied revision would be a
        # second way to fill the field, and the one that skips the check above.
        "source_git_commit": payload_source_commit(
            source_root,
            (str(item["canonical_relative_path"]) for item in deployed_files),
        ),
        "canonical_payload_sha256": sha256_bytes(payload),
        "deployed_files": deployed_files,
    }


def write_lock(target_root: Path, lock: dict[str, object]) -> Path:
    path = lock_path(target_root)
    path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_lock(target_root: Path) -> dict[str, object]:
    return json.loads(lock_path(target_root).read_text(encoding="utf-8"))
