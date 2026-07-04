"""Local managed-repository registry."""

from __future__ import annotations

import json
from pathlib import Path

from .paths import manifest_path, registry_path


class RegistryError(RuntimeError):
    """Raised when a registry operation cannot be completed."""


def _registry_file(registry_file: Path | None = None, source_root: Path | None = None) -> Path:
    return registry_file or registry_path(source_root)


def load_registry(registry_file: Path | None = None, source_root: Path | None = None) -> list[dict[str, str]]:
    path = _registry_file(registry_file, source_root)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RegistryError(f"invalid registry JSON: {path}") from exc
    if not isinstance(data, list):
        raise RegistryError(f"registry must be a JSON list: {path}")
    entries: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        repo_path = item.get("path")
        manifest = item.get("manifest")
        if isinstance(name, str) and isinstance(repo_path, str) and isinstance(manifest, str):
            entries.append({"name": name, "path": repo_path, "manifest": manifest})
    return entries


def save_registry(
    entries: list[dict[str, str]],
    registry_file: Path | None = None,
    source_root: Path | None = None,
) -> Path:
    path = _registry_file(registry_file, source_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = sorted(entries, key=lambda item: (item["name"], item["path"]))
    path.write_text(json.dumps(entries, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def make_entry(target_root: Path) -> dict[str, str]:
    target_root = target_root.expanduser().resolve()
    return {
        "name": target_root.name,
        "path": str(target_root),
        "manifest": str(manifest_path(target_root)),
    }


def add_repo(
    target: str | Path,
    *,
    registry_file: Path | None = None,
    source_root: Path | None = None,
    require_manifest: bool = True,
) -> dict[str, str]:
    target_root = Path(target).expanduser().resolve()
    if not target_root.is_dir():
        raise RegistryError(f"target repo does not exist: {target_root}")
    if require_manifest and not manifest_path(target_root).is_file():
        raise RegistryError(f"no readable manifest at {manifest_path(target_root)}; run deploy first")

    entry = make_entry(target_root)
    entries = [item for item in load_registry(registry_file, source_root) if item["path"] != entry["path"]]
    entries.append(entry)
    save_registry(entries, registry_file, source_root)
    return entry


def remove_repo(
    name_or_path: str,
    *,
    registry_file: Path | None = None,
    source_root: Path | None = None,
) -> bool:
    entries = load_registry(registry_file, source_root)
    candidate_path: str | None = None
    if "/" in name_or_path or name_or_path.startswith(".") or name_or_path.startswith("~"):
        candidate_path = str(Path(name_or_path).expanduser().resolve())

    kept = [
        item
        for item in entries
        if item["name"] != name_or_path and item["path"] != name_or_path and item["path"] != candidate_path
    ]
    if len(kept) == len(entries):
        return False
    save_registry(kept, registry_file, source_root)
    return True
