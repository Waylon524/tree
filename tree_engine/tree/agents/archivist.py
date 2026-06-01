"""ArchivistAgent: clean OCR Markdown + cut it into MTUs.

Stage ② of the pipeline. See docs/REBUILD-DESIGN.md §4.

  clean(raw_markdown) -> cleaned_markdown          (ARCHIVIST_CLEAN_PROMPT)
  cut_mtus(cleaned_markdown, collection, file) -> list[MTU]
      number lines -> ARCHIVIST_MTU_PROMPT -> strict JSON -> validate full line
      coverage -> repair on failure -> deterministic whole-doc fallback.
"""

from __future__ import annotations

import logging

from tree.agents.base import Agent
from tree.agents.parsers import extract_json_object
from tree.agents.prompts import ARCHIVIST_CLEAN_PROMPT, ARCHIVIST_MTU_PROMPT
from tree.planner.models import MTU
from tree.planner.mtu import (
    MtuCoverageError,
    build_mtus,
    number_lines,
    validate_and_normalize,
)

logger = logging.getLogger(__name__)


class ArchivistAgent(Agent):
    role = "archivist"

    async def clean(self, raw_markdown: str, *, timeout_sec: float | None = None) -> str:
        """Lossless cleanup of OCR Markdown into normalized Markdown."""
        if not raw_markdown.strip():
            return ""
        result = await self.complete(
            raw_markdown, system_prompt=ARCHIVIST_CLEAN_PROMPT, timeout_sec=timeout_sec
        )
        return result.strip()

    async def cut_mtus(
        self,
        cleaned_markdown: str,
        *,
        collection: str,
        source_file: str,
        order_offset: int = 0,
        timeout_sec: float | None = None,
        repair_attempts: int = 1,
    ) -> list[MTU]:
        """Cut cleaned Markdown into Minimal Teachable Units with metadata."""
        line_count = len(cleaned_markdown.splitlines())
        if line_count == 0:
            return []

        numbered = number_lines(cleaned_markdown)
        feedback = ""
        last_error: ValueError | None = None
        for attempt in range(repair_attempts + 1):
            try:
                raw = await self.complete(
                    numbered + feedback,
                    system_prompt=ARCHIVIST_MTU_PROMPT,
                    timeout_sec=timeout_sec,
                )
                plan = extract_json_object(raw)
                units, _skipped = validate_and_normalize(plan, line_count)
                return build_mtus(
                    units,
                    collection=collection,
                    source_file=source_file,
                    order_offset=order_offset,
                )
            except (ValueError, MtuCoverageError) as exc:
                last_error = exc
                logger.warning(
                    "MTU cut plan invalid for %s/%s (attempt %d): %s",
                    collection, source_file, attempt + 1, exc,
                )
                feedback = (
                    f"\n\nPREVIOUS ATTEMPT WAS INVALID: {exc}. "
                    f"Regenerate the strict JSON object. Fix the invalid unit metadata and "
                    f"ensure units + skipped_ranges together cover every line 1..{line_count} "
                    f"exactly once, with no gaps or overlaps."
                )

        raise last_error or MtuCoverageError(
            f"MTU cut plan invalid for {collection}/{source_file} after {repair_attempts + 1} attempts"
        )
