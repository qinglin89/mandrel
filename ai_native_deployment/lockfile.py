"""Portable deploy lockfile creation."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .hashing import sha256_bytes
from .manifest import SCHEMA_VERSION, source_git_commit
from .paths import CANONICAL_DIRNAME, lock_path

# The only tree entries whose bytes a payload can be said to have come from.
EXECUTABLE_BLOB_MODE = "100755"
REGULAR_BLOB_MODES = frozenset({"100644", EXECUTABLE_BLOB_MODE})


@dataclass(frozen=True)
class DeployedFile:
    """One file as it was deployed: where its bytes came from, where they went,
    and the digest, size, and executable bit the target received.

    Built while the deploy writes the file, from the bytes it wrote and the mode
    it applied, so everything the receipt says is said about the payload and not
    about whatever the source reads as afterwards."""

    canonical_relative_path: str
    target_relative_path: str
    canonical_sha256: str
    canonical_size_bytes: int
    executable: bool


def payload_source_commit(
    source_root: Path,
    deployed: Sequence[DeployedFile],
    *,
    deploys: Callable[[str], bool],
) -> str | None:
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

    - every file the payload carried came from a canonical file the commit
      tracks, hashes to the content the commit holds for it, and reached the
      target with the executable bit the commit records — deployment copies the
      mode along with the bytes;
    - every canonical file the commit holds that the mapping deploys is in the
      payload, because the deployed set is a function of which canonical files
      existed when the deploy read the tree: a committed file missing then
      leaves the target short of a contract, where the deployed side alone sees
      nothing missing.

    Both sides of that comparison are settled before it runs. `deployed` is the
    deployment's own record — the bytes it copied and the mode it applied,
    captured as it wrote each file — and never a second reading of the source:
    an edit undone after the target was written, a mode restored, or a canonical
    file put back after the enumeration had already passed it would each make
    every later look at the working tree agree with the commit while the target
    runs something else. The commit's tree is the other side, and no index can
    rewrite it: cleanliness is not this proof either, since a tracked file
    marked `assume-unchanged` or `skip-worktree` can differ while `status` and
    `diff` stay silent, and `core.fileMode=false` hides a mode change that
    deployment still copies.

    Anything else states no revision — including a canonical entry the mapping
    deploys that the commit does not hold as a regular file, since
    `iter_deployment_items` reads bytes through a symlink and deploys whatever
    it resolved to, and including any question Git could not answer. That
    excludes such a report from a cohort, which costs a denominator, where the
    alternative manufactures a placement.

    The question is scoped to the canonical files the mapping carries into a
    target, because those are the payload's bytes; work elsewhere in the
    checkout, and canonical files no target receives, say nothing about them. It
    is scoped to content, not to rendering: the lock hashes canonical sources,
    and which version of this tool rendered them into a target is a separate
    fact no receipt states.
    """

    commit = source_git_commit(source_root)
    if commit is None:
        return None
    committed = _committed_canonical_blobs(source_root, commit, deploys)
    if committed is None:
        return None

    payload = {record.canonical_relative_path: record for record in deployed}
    targets = {record.target_relative_path for record in deployed}
    if len(payload) != len(deployed) or len(targets) != len(deployed):
        # Not one record per file, in either direction, and the comparison below
        # would read as a complete account of the payload either way. Two records
        # for one canonical file leave one of them unchecked; two for one target
        # file describe a payload the target cannot be holding, since only the
        # later write survives there. The mapping refuses the second before a
        # deploy can write it (`deploy.iter_deployment_items`), which is where
        # the manifest and `status` are kept honest about it too; this is the
        # receipt saying what it needs a record set to be before it vouches for
        # one.
        return None
    # Both directions at once: a payload file the commit does not hold, and a
    # canonical file the commit expected this payload to carry.
    if payload.keys() != committed.keys():
        return None
    contents = _committed_content_digests(source_root, {entry[0] for entry in committed.values()})
    if contents is None:
        return None

    for path, (object_id, executable) in committed.items():
        record = payload[path]
        if record.canonical_sha256 != contents.get(object_id) or record.executable != executable:
            return None
    return commit


def _committed_canonical_blobs(
    source_root: Path,
    commit: str,
    deploys: Callable[[str], bool],
) -> dict[str, tuple[str, bool]] | None:
    """Every canonical file `commit` holds that the mapping deploys, as
    `path -> (object id, executable)`.

    Read from the commit's own tree — the side of the comparison a working tree
    cannot influence and an index cannot suppress. Entries the mapping carries
    nowhere are left out rather than compared: they are not the payload's bytes,
    so nothing about them can make the payload this commit's or stop it being.
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
        if not deploys(path):
            continue
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
    files: Iterable[DeployedFile],
    deploys: Callable[[str], bool],
) -> dict[str, object]:
    deployed = sorted(files, key=lambda record: record.target_relative_path)
    deployed_files = [
        {
            "canonical_relative_path": record.canonical_relative_path,
            "target_relative_path": record.target_relative_path,
            "canonical_sha256": record.canonical_sha256,
            "canonical_size_bytes": record.canonical_size_bytes,
        }
        for record in deployed
    ]
    payload = "\n".join(
        f"{item['target_relative_path']}\0{item['canonical_sha256']}" for item in deployed_files
    ).encode("utf-8")
    return {
        "schema_version": SCHEMA_VERSION,
        "source_repo": "ai-native-deployment",
        # Derived here and nowhere else, from the deployment's own record of
        # what it copied: a caller-supplied revision would be a second way to
        # fill the field, and the one that skips the check above.
        "source_git_commit": payload_source_commit(source_root, deployed, deploys=deploys),
        "canonical_payload_sha256": sha256_bytes(payload),
        "deployed_files": deployed_files,
    }


def write_lock(target_root: Path, lock: dict[str, object]) -> Path:
    path = lock_path(target_root)
    path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_lock(target_root: Path) -> dict[str, object]:
    return json.loads(lock_path(target_root).read_text(encoding="utf-8"))
