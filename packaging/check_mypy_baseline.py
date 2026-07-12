#!/usr/bin/env python3
"""Prevent the existing mypy debt from growing while it is paid down."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = 132


def main() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "tree_engine"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    count = len(re.findall(r": error:", result.stdout))
    if count > BASELINE:
        print(result.stdout)
        print(f"mypy regression: {count} errors exceeds baseline {BASELINE}", file=sys.stderr)
        return 1
    print(f"mypy ratchet OK: {count} errors (baseline {BASELINE})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
