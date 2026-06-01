"""pdf extractor — migrate from previous engine (step 2).

★ Behaviour to preserve: see docs/LEGACY-DESIGN.md §4.1.
"""

from __future__ import annotations

from pathlib import Path


def extract(path: Path) -> str:
    raise NotImplementedError("pdf_extractor.extract — migrate in step 2")
