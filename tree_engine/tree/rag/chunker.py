"""Markdown chunker — simplified for the new architecture.

MTU boundaries are authoritative: one MTU normally maps to one RAG chunk. Only
sub-chunk when a single MTU exceeds the token budget. The heavy cut-plan logic
of the old engine is gone. See docs/REBUILD-DESIGN.md §4 ⑤ / §0.

TODO (step 3):
  - chunk_mtu(mtu, text) -> list[chunk dict]   (payload: mtu_id, node_id, title,
        keywords, collection, line_range, chunk_index, token_estimate)
  - _split_oversized(text, max_tokens) for the rare large MTU
"""

from __future__ import annotations

MAX_TOKENS_PER_CHUNK = 3000


def chunk_mtu(mtu: object, text: str) -> list[dict]:
    raise NotImplementedError("chunker.chunk_mtu — implement in step 3")
