"""Portable deploy lockfile creation."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .hashing import sha256_bytes
from .manifest import SCHEMA_VERSION, source_git_commit
from .paths import CANONICAL_DIRNAME, lock_path, target_path_identity

# The only tree entries whose bytes a payload can be said to have come from.
EXECUTABLE_BLOB_MODE = "100755"
REGULAR_BLOB_MODES = frozenset({"100644", EXECUTABLE_BLOB_MODE})

# The receipt shapes this build reads back. Every receipt this contract has ever
# written states its schema, and this build reads exactly the one it writes: a
# later version is refused rather than read as the current shape, since a field
# this build knows by name may mean something else in the document that wrote it.
SUPPORTED_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION})

# Git's two object-id widths, as `git rev-parse` writes them — SHA-1 and
# SHA-256, lowercase both.
_OBJECT_ID_WIDTHS = frozenset({40, 64})
_HEX_DIGITS = "0123456789abcdef"


class LockError(RuntimeError):
    """Raised when a document is not a deploy receipt this build reads."""


def is_object_id(value: object) -> bool:
    """Whether `value` names one commit exactly: a full lowercase object id, as
    `git rev-parse` writes one.

    The shape a revision has to have before any reader here may place it, and the
    reason is the resolution rather than the string. `git rev-parse` answers for
    `HEAD`, a branch, or a tag as readily as for an object id, so a symbolic name
    is placed against whatever the *reading* repository is currently sitting on
    and moves whenever that does — reporting a fact about somewhere else as a fact
    about this checkout's position. An abbreviation is refused for the other half
    of the same reason: it names one commit today and may name two tomorrow.

    Asked in one place because two readers ask it, and both of them resolve what
    they read against this repository's Git: a target's deploy receipt
    (`stated_source_commit`) and the effective revision a report's provenance
    states (`evolution.assessment`). A second spelling of the rule would let one
    of them place what the other refuses, over the same string.

    What it is not is a claim that the commit exists here. Whether this clone
    holds the object is a fact about the clone, and each reader reports that
    separately — an unresolvable revision is a question this checkout cannot
    answer, while a revision of the wrong shape is one nobody's checkout should
    answer.
    """

    return isinstance(value, str) and len(value) in _OBJECT_ID_WIDTHS and not value.strip(_HEX_DIGITS)


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
    targets = {target_path_identity(record.target_relative_path) for record in deployed}
    if len(payload) != len(deployed) or len(targets) != len(deployed):
        # Not one record per file, in either direction, and the comparison below
        # would read as a complete account of the payload either way. Two records
        # for one canonical file leave one of them unchecked; two for one target
        # file describe a payload the target cannot be holding, since only the
        # later write survives there. Target files are counted by
        # `target_path_identity` for that reason: two records the target resolves
        # to one file are the case this rejects, whether or not their paths are
        # the same string. The mapping refuses such a payload before a deploy can
        # write it (`deploy.iter_deployment_items`), which is where the manifest
        # and `status` are kept honest about it too; this is the receipt saying
        # what it needs a record set to be before it vouches for one.
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
        "source_repo": "mandrel",
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


def stated_source_commit(lock: object) -> str | None:
    """The canonical commit a receipt ties its payload to, or None where it ties
    it to none.

    `read_lock` returns whatever the file parsed to; this is what may be believed
    about it, and it is asked here rather than by each reader because a receipt
    is a document in a repository this tool does not own — anyone may have
    edited, truncated, or written it with another build, and the two things that
    make the field usable are not properties of the string alone.

    The schema is asked because a version this build does not write is a document
    whose fields it only appears to understand.

    The commit is asked for its exact shape (`is_object_id`) because every reader
    placing a target by it resolves it in a repository of its own: a receipt
    stating a symbolic name would be placed against the reading checkout's own
    current position, reporting a target as carrying a revision nobody ever
    deployed to it. What a deploy writes here is `git rev-parse HEAD`'s own answer
    (`manifest.source_git_commit`), which is a full object id and nothing else.

    None is an ordinary answer rather than a complaint: a deploy states a source
    commit only where the payload it wrote matched that commit's canonical tree
    exactly (`payload_source_commit`), and a receipt stating none is saying that
    what it holds cannot be placed by anyone.

    Which is why the field's absence is not that answer and is refused with the
    rest. Every receipt this contract writes states it — `build_lock` fills it
    from `payload_source_commit`, whose own answer for an unplaceable payload is
    the explicit null — so a document without the key never came from a deploy
    this build reads, and reading it as the null would put a truncated file's
    silence into the contract's mouth. The two are a different fact for the
    reader: null is a target whose payload nothing can place, and absence is a
    receipt that answers nothing at all.
    """

    if not isinstance(lock, dict):
        raise LockError("not a deploy receipt")
    version = lock.get("schema_version")
    # The type is asked before the value, and both halves of that matter: JSON
    # `true` decodes to a `bool`, which is an `int` equal to 1, and a JSON array
    # or object reaching a set membership test raises on being unhashable rather
    # than answering — which would leave this refusal as an exception no reader
    # is catching.
    if not isinstance(version, int) or isinstance(version, bool) or version not in SUPPORTED_SCHEMA_VERSIONS:
        raise LockError(
            f"deploy receipt schema_version {version!r}; this build reads {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
        )
    # Presence before value, and `in` rather than `get`: the two states this
    # field has are told apart by nothing else, since a `get` answers None for
    # both the receipt that states no commit and the receipt that states nothing.
    if "source_git_commit" not in lock:
        raise LockError("source_git_commit is absent; a receipt states the field even where it names no commit")
    commit = lock["source_git_commit"]
    if commit is None:
        return None
    if not is_object_id(commit):
        raise LockError(f"source_git_commit {commit!r} is not a commit id")
    return commit
