"""Source-markdown helpers: list cleaned Markdown collections, read by line_range.
TODO (step 2/3).
"""

from __future__ import annotations

from pathlib import Path

from tree.io import paths


def source_markdown_root(root: Path) -> Path:
    return paths.source_markdown_root(root)
