"""Source ingest + embedding orchestration.

Incremental via a source manifest (file fingerprints): only new/changed
materials are re-OCR'd and re-embedded. Serial embedding; cleaned Markdown is
deleted after embedding (RAG holds the only copy of MTU text).

See docs/REBUILD-DESIGN.md §4 ⑤, docs/LEGACY-DESIGN.md §4.5.

TODO (step 8):
  - prepare_sources(): ingest pending -> MTUs -> embed -> delete markdown
  - ensure_all_embedded(): block until every MTU is indexed
"""

from __future__ import annotations


async def prepare_sources(engine: object) -> None:
    raise NotImplementedError("ingest_driver.prepare_sources — implement in step 8")
