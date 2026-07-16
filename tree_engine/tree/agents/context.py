"""Bound optional agent context without truncating planner coverage payloads."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def bounded_text(text: str, *, max_chars: int, label: str) -> str:
    """Keep the instruction-bearing head and latest tail of optional context."""
    if len(text) <= max_chars:
        return text
    marker = f"\n\n[... {label} trimmed by TREE token budget ...]\n\n"
    if len(marker) >= max_chars:
        return text[:max_chars]
    content_chars = max_chars - len(marker)
    head_chars = content_chars * 3 // 4
    tail_chars = content_chars - head_chars
    logger.info(
        "Agent context trimmed label=%s original_chars=%d retained_chars=%d",
        label,
        len(text),
        max_chars,
    )
    return (
        text[:head_chars] + marker + text[-tail_chars:]
    )


def bounded_rag_hits(
    hits: list[dict[str, Any]],
    *,
    max_total_chars: int = 96_000,
    max_hit_chars: int = 24_000,
) -> list[dict[str, Any]]:
    """Retain higher-ranked hits first and bound optional RAG text only."""
    retained: list[dict[str, Any]] = []
    remaining = max_total_chars
    for index, hit in enumerate(hits, start=1):
        if remaining <= 0:
            logger.info("Agent RAG context dropped remaining_hits=%d", len(hits) - index + 1)
            break
        text = str(hit.get("text") or "")
        allowance = min(max_hit_chars, remaining)
        bounded = bounded_text(text, max_chars=allowance, label=f"RAG hit {index}")
        clone = dict(hit)
        clone["text"] = bounded
        retained.append(clone)
        remaining -= len(bounded)
    return retained
