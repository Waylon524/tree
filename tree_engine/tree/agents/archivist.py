"""ArchivistAgent: clean OCR Markdown + cut into MTUs.

Stage ② of the pipeline. See docs/REBUILD-DESIGN.md §4.

TODO (step 4):
  - clean(raw_markdown) -> cleaned_markdown   (ARCHIVIST_CLEAN_PROMPT)
  - cut_mtus(cleaned_markdown, collection, source_file) -> list[MTU]
      * number the lines, call ARCHIVIST_MTU_PROMPT, parse strict JSON
      * validate full line coverage (units + skipped_ranges), repair on failure
      * assign mtu_id via prefixed_id("mtu", [collection, file, start, end])
"""

from __future__ import annotations

from tree.agents.base import Agent


class ArchivistAgent(Agent):
    role = "archivist"

    async def clean(self, raw_markdown: str) -> str:
        raise NotImplementedError("Archivist.clean — implement in step 4")

    async def cut_mtus(self, cleaned_markdown: str, *, collection: str, source_file: str) -> list:
        raise NotImplementedError("Archivist.cut_mtus — implement in step 4")
