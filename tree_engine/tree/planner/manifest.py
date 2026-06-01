"""Material scan: incremental fingerprint manifest over materials/.

Decides which materials are new / changed / unchanged so the planner can reuse
cached MTUs for untouched files. See docs/REBUILD-DESIGN.md §5.
"""

from __future__ import annotations

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
            rel = str(path.relative_to(materials_root))
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
                    "collection": _collection_for(materials_root, path),
                    "source_file": path.name,
                    "fingerprint": fingerprint,
                    "status": status,
                }
            )

    inactive = sorted(set(prior) - seen_paths)
    return {
        "materials": materials,
        "active_materials": [m["path"] for m in materials],
        "inactive_materials": inactive,
    }


def _is_supported(path: Path) -> bool:
    return path.suffix.lower() in MATERIAL_EXTENSIONS and not path.name.startswith(".")


def _collection_for(materials_root: Path, path: Path) -> str:
    rel = path.relative_to(materials_root)
    return rel.parts[0] if len(rel.parts) > 1 else "default"


def _fingerprint(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_size}-{int(stat.st_mtime)}"
