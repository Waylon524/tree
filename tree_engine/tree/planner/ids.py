"""Stable content-derived IDs and text normalization for the planner.

IDs are deterministic so re-running the planner on unchanged inputs yields
identical artifacts (enables hash-based incremental rebuilds).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any


def prefixed_id(prefix: str, parts: Any) -> str:
    """Deterministic ``<prefix>:<hash>`` id from arbitrary parts.

    ``prefixed_id("mtu", [collection, file, start, end])``
    """
    if not isinstance(parts, (list, tuple)):
        parts = [parts]
    payload = "|".join(str(part) for part in parts)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def normalize_text_key(value: str) -> str:
    """Normalize a concept/title to a comparison key.

    Lowercase, NFKC, strip whitespace and most punctuation. Keeps CJK as-is.
    """
    text = unicodedata.normalize("NFKC", str(value)).strip().lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[　 ]", "", text)
    text = re.sub(r"[.,;:!?_\-—–/\\()\[\]{}\"'`、，。；：！？（）【】《》]", "", text)
    return text


def normalize_concepts(values: Any) -> list[str]:
    """De-duplicate a concept list while preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values or []:
        text = str(value).strip()
        if not text:
            continue
        key = normalize_text_key(text)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out
