#!/usr/bin/env python3
"""Fail fast when a TREE release is internally inconsistent."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _requirement_name(requirement: str) -> str:
    """Return a normalized distribution name, ignoring extras and versions."""
    return re.split(r"[<>=!~\[]", requirement, maxsplit=1)[0].strip().lower().replace("_", "-")


def _version_map() -> dict[str, str]:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    cargo = (ROOT / "desktop/src-tauri/Cargo.toml").read_text(encoding="utf-8")
    cargo_match = re.search(r'^version\s*=\s*"([^"]+)"', cargo, flags=re.MULTILINE)
    if cargo_match is None:
        raise RuntimeError("Cargo.toml package version is missing")
    package_lock = json.loads((ROOT / "desktop/package-lock.json").read_text(encoding="utf-8"))
    cargo_lock = (ROOT / "desktop/src-tauri/Cargo.lock").read_text(encoding="utf-8")
    app_lock_match = re.search(
        r'\[\[package\]\]\s*name = "app"\s*version = "([^"]+)"',
        cargo_lock,
        flags=re.MULTILINE,
    )
    if app_lock_match is None:
        raise RuntimeError("Cargo.lock app version is missing")
    return {
        "pyproject.toml": str(pyproject["project"]["version"]),
        "desktop/package.json": str(
            json.loads((ROOT / "desktop/package.json").read_text(encoding="utf-8"))["version"]
        ),
        "desktop/src-tauri/Cargo.toml": cargo_match.group(1),
        "desktop/package-lock.json": str(package_lock["version"]),
        "desktop/package-lock.json packages root": str(package_lock["packages"][""]["version"]),
        "desktop/src-tauri/Cargo.lock": app_lock_match.group(1),
        "desktop/src-tauri/tauri.conf.json": str(
            json.loads(
                (ROOT / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8")
            )["version"]
        ),
    }


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="", help="Expected release tag, for example v0.3.7")
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()

    versions = _version_map()
    unique = set(versions.values())
    if len(unique) != 1:
        print(f"ERROR: version mismatch: {versions}", file=sys.stderr)
        return 1
    version = next(iter(unique))
    if args.tag and args.tag != f"v{version}":
        print(f"ERROR: tag {args.tag!r} does not match version v{version}", file=sys.stderr)
        return 1
    if not args.allow_dirty and _git("status", "--porcelain"):
        print("ERROR: release worktree is dirty", file=sys.stderr)
        return 1
    required = [
        ROOT / "desktop/package-lock.json",
        ROOT / "desktop/src-tauri/Cargo.lock",
        ROOT / "packaging/release-constraints.txt",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    if missing:
        print(f"ERROR: missing release locks: {missing}", file=sys.stderr)
        return 1
    constraints = {
        _requirement_name(line)
        for line in (ROOT / "packaging/release-constraints.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    release_requirements = [
        *pyproject["project"]["dependencies"],
        *pyproject["project"]["optional-dependencies"]["rag"],
        *pyproject["project"]["optional-dependencies"]["gui"],
        "pyinstaller",
    ]
    unconstrained = sorted(
        {
            _requirement_name(requirement)
            for requirement in release_requirements
        }
        - constraints
    )
    if unconstrained:
        print(f"ERROR: unconstrained release dependencies: {unconstrained}", file=sys.stderr)
        return 1
    print(f"Release doctor OK: TREE v{version}; versions and lock inputs agree.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
