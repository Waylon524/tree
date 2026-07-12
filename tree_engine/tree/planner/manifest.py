"""Material scan: incremental fingerprint manifest over materials/.

Decides which materials are new / changed / unchanged so the planner can reuse
cached MTUs for untouched files.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from tree.ingest.pipeline import MATERIAL_EXTENSIONS
from tree.io import paths


def scan_materials(root: Path, *, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Walk materials/ and classify every supported file against the prior manifest."""
    materials_root = paths.materials_root(root)
    prior = {m["path"]: m.get("fingerprint", "") for m in (previous or {}).get("materials", [])}

    materials: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    if materials_root.exists():
        for path in sorted(materials_root.rglob("*")):
            if not path.is_file() or not _is_supported(path):
                continue
            rel = path.relative_to(materials_root).as_posix()
            seen_paths.add(rel)
            fingerprint = _fingerprint(path)
            if rel not in prior:
                status = "new"
            elif prior[rel] != fingerprint:
                status = "changed"
            else:
                status = "unchanged"
            materials.append(
                {
                    "path": rel,
                    "source_id": rel,
                    "collection": _collection_for(materials_root, path),
                    "source_file": path.name,
                    "fingerprint": fingerprint,
                    "status": status,
                }
            )

    inactive = sorted(set(prior) - seen_paths)
    manifest = {
        "schema_version": 2,
        "materials": materials,
        "active_materials": [m["path"] for m in materials],
        "inactive_materials": inactive,
    }
    manifest["generation_id"] = manifest_generation_id(manifest)
    return manifest


def _is_supported(path: Path) -> bool:
    return path.suffix.lower() in MATERIAL_EXTENSIONS and not path.name.startswith(".")


def _collection_for(materials_root: Path, path: Path) -> str:
    rel = path.relative_to(materials_root)
    return rel.parts[0] if len(rel.parts) > 1 else "default"


def _fingerprint(path: Path) -> str:
    """Return a content fingerprint, not a timestamp-based change hint.

    Material correctness is more important than avoiding a local sequential
    read: size + second-resolution mtime misses same-size edits and can make the
    planner reuse knowledge generated from stale source text.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def manifest_generation_id(manifest: dict[str, Any]) -> str:
    """Stable generation for the active material contents.

    Per-run classification (new/changed/unchanged) is intentionally excluded so
    a successfully committed generation remains stable on the next scan.
    """
    digest = hashlib.sha256()
    for material in sorted(manifest.get("materials", []), key=lambda item: str(item.get("path", ""))):
        digest.update(str(material.get("path", "")).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(material.get("fingerprint", "")).encode("utf-8"))
        digest.update(b"\0")
    return f"gen:{digest.hexdigest()[:24]}"
