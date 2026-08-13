"""Portable deploy lockfile creation."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Iterable, Mapping

from .hashing import sha256_bytes, sha256_file
from .manifest import SCHEMA_VERSION, source_git_commit
from .paths import CANONICAL_DIRNAME, lock_path

# The only tree entries whose bytes a payload can be said to have come from.
EXECUTABLE_BLOB_MODE = "100755"
REGULAR_BLOB_MODES = frozenset({"100644", EXECUTABLE_BLOB_MODE})


def payload_source_commit(source_root: Path, deployed_digests: Mapping[str, str]) -> str | None:
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
    commit's payload with the mapping code that commit carried.

    So the receipt states a revision only where the payload and that commit's
    canonical tree are compared directly and correspond exactly:

    - every file the payload carried is one the commit tracks, and the bytes
      that were deployed hash to the content the commit holds for it;
    - every canonical file the commit tracks is in the working tree with that
      same content and the same executable bit — deployment copies the mode, and
      the deployed set is a function of which canonical files exist, so a
      committed file missing or altered before the deploy read it shrinks or
      changes the payload where the deployed side alone cannot see it.

    Nothing here asks the index or `status` whether the tree is clean, because
    cleanliness is not that proof: a tracked file marked `assume-unchanged` or
    `skip-worktree` can differ in the working tree while both stay silent, and
    `core.fileMode=false` hides a mode change that deployment still copies. Both
    states would have passed a clean-tree check and put a commit beside bytes it
    does not hold. Content and mode are therefore read from the commit's own
    tree and from the payload, neither of which the index can rewrite.

    Anything else states no revision — including a canonical tree entry that is
    not a regular file, since `iter_deployment_items` reads bytes through a
    symlink that the commit does not hold, and including any question Git could
    not answer. That excludes such a report from a cohort, which costs a
    denominator, where the alternative manufactures a placement.

    The question is scoped to the canonical tree because that is where the
    payload's bytes come from; unrelated work elsewhere in the checkout says
    nothing about them. It is scoped to content, not to rendering: the lock
    hashes canonical sources, and which version of this tool rendered them into
    a target is a separate fact no receipt states.
    """

    commit = source_git_commit(source_root)
    if commit is None:
        return None
    committed = _committed_canonical_blobs(source_root, commit)
    if committed is None:
        return None
    if not deployed_digests.keys() <= committed.keys():
        return None
    contents = _committed_content_digests(source_root, {entry[0] for entry in committed.values()})
    if contents is None:
        return None

    for path, (object_id, executable) in committed.items():
        source_path = source_root / path
        try:
            if source_path.is_symlink() or not source_path.is_file():
                return None
            if bool(source_path.stat().st_mode & 0o111) != executable:
                return None
            deployed = deployed_digests.get(path)
            # What the payload carried where the payload carried it; the file as
            # it stands otherwise, since that is what the next deploy would read.
            digest = deployed if deployed is not None else sha256_file(source_path)
        except OSError:
            return None
        if digest != contents[object_id]:
            return None
    return commit


def _committed_canonical_blobs(source_root: Path, commit: str) -> dict[str, tuple[str, bool]] | None:
    """Every canonical file `commit` holds, as `path -> (object id, executable)`.

    Read from the commit's own tree — the side of the comparison a working tree
    cannot influence and an index cannot suppress.
    """

    output = _git_output(source_root, ["ls-tree", "-r", "-z", commit, "--", CANONICAL_DIRNAME])
    if output is None:
        return None
    try:
        entries = output.decode("utf-8")
    except UnicodeDecodeError:
        return None

    blobs: dict[str, tuple[str, bool]] = {}
    for entry in entries.split("\0"):
        if not entry:
            continue
        head, tab, path = entry.partition("\t")
        fields = head.split(" ")
        if not tab or len(fields) != 3:
            return None
        mode, kind, object_id = fields
        if kind != "blob" or mode not in REGULAR_BLOB_MODES:
            return None
        blobs[path] = (object_id, mode == EXECUTABLE_BLOB_MODE)
    return blobs


def _committed_content_digests(source_root: Path, object_ids: set[str]) -> dict[str, str] | None:
    """sha256 of each committed object's content, by object id.

    The comparison runs over content and not over Git's own object ids because
    the payload's digests are sha256 of the bytes that were copied. Asking Git
    to hash the working tree instead would answer through its filters, so a
    checkout that rewrites content on the way in (`core.autocrlf`, a clean
    filter) would report ids that agree while the deployed bytes differ from
    what the commit holds.
    """

    if not object_ids:
        return {}
    request = "".join(f"{object_id}\n" for object_id in sorted(object_ids)).encode("ascii")
    stream = _git_output(source_root, ["cat-file", "--batch"], stdin=request)
    if stream is None:
        return None

    digests: dict[str, str] = {}
    position = 0
    while position < len(stream):
        end = stream.find(b"\n", position)
        if end < 0:
            return None
        # `<object id> <type> <size>`, or `<object id> missing` for an object
        # this repository cannot read — which is not an answer either.
        header = stream[position:end].split(b" ")
        if len(header) != 3:
            return None
        try:
            size = int(header[2])
        except ValueError:
            return None
        content_start = end + 1
        position = content_start + size + 1
        if position > len(stream):
            return None
        digests[header[0].decode("ascii")] = sha256_bytes(stream[content_start : content_start + size])
    return digests


def _git_output(source_root: Path, arguments: list[str], *, stdin: bytes | None = None) -> bytes | None:
    """Git's answer, or `None` when it could not answer.

    A question Git refused is not a matching tree: it is the same "nothing can
    be said" a checkout without Git gives, and both leave the revision unstated.
    """

    try:
        result = subprocess.run(
            ["git", "-C", str(source_root), *arguments],
            check=True,
            capture_output=True,
            input=stdin,
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
        # The digests are the payload's own — what these bytes were, not what
        # the file says when the check gets around to reading it.
        "source_git_commit": payload_source_commit(
            source_root,
            {
                str(item["canonical_relative_path"]): str(item["canonical_sha256"])
                for item in deployed_files
            },
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
