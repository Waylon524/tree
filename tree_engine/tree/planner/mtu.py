"""MTU helpers: line numbering, cut-plan coverage validation, MTU construction.

Pure functions (no LLM) so they can be unit-tested directly. The Archivist agent
calls these around its LLM cut-plan call. See docs/REBUILD-DESIGN.md §4 ②.
"""

from __future__ import annotations

from typing import Any

from tree.planner.ids import normalize_concepts, prefixed_id
from tree.planner.models import MTU

_VALID_UNIT_KINDS = {"concept", "example", "exercise", "misconception", "procedure", "application"}


class MtuCoverageError(ValueError):
    """Raised when an Archivist cut plan does not tile every source line exactly once."""


def number_lines(text: str) -> str:
    """Return 1-based ``<n>\\t<line>`` numbered Markdown for the cut-plan prompt."""
    lines = text.splitlines()
    return "\n".join(f"{i}\t{line}" for i, line in enumerate(lines, start=1))


def validate_and_normalize(plan: dict[str, Any], line_count: int) -> tuple[list[dict], list[dict]]:
    """Validate that units + skipped_ranges tile lines 1..line_count exactly once.

    Returns (normalized_units, normalized_skipped). Raises MtuCoverageError on any
    gap, overlap, or out-of-bounds range so the agent can repair.
    """
    if line_count <= 0:
        return [], []

    raw_units = plan.get("units") or []
    raw_skipped = plan.get("skipped_ranges") or []
    if not isinstance(raw_units, list) or not isinstance(raw_skipped, list):
        raise MtuCoverageError("`units` and `skipped_ranges` must be lists")

    spans: list[tuple[int, int, str, dict]] = []
    units: list[dict] = []
    for index, raw in enumerate(raw_units, start=1):
        unit = _normalize_unit(raw, index)
        spans.append((unit["start_line"], unit["end_line"], "unit", unit))
        units.append(unit)
    skipped: list[dict] = []
    for index, raw in enumerate(raw_skipped, start=1):
        start = _int(raw.get("start_line"), f"skipped {index} start_line")
        end = _int(raw.get("end_line"), f"skipped {index} end_line")
        entry = {"start_line": start, "end_line": end, "reason": str(raw.get("reason") or "")[:300]}
        spans.append((start, end, "skip", entry))
        skipped.append(entry)

    spans.sort(key=lambda s: (s[0], s[1]))
    expected = 1
    for start, end, kind, _payload in spans:
        if start < 1 or end > line_count:
            raise MtuCoverageError(f"{kind} range {start}-{end} is out of bounds (1..{line_count})")
        if end < start:
            raise MtuCoverageError(f"{kind} range {start}-{end} is inverted")
        if start != expected:
            if start < expected:
                raise MtuCoverageError(f"overlap at line {start} (expected next line {expected})")
            raise MtuCoverageError(f"gap: lines {expected}-{start - 1} uncovered")
        expected = end + 1
    if expected != line_count + 1:
        raise MtuCoverageError(f"gap: lines {expected}-{line_count} uncovered")

    return units, skipped


def build_mtus(
    units: list[dict],
    *,
    collection: str,
    source_file: str,
    order_offset: int = 0,
) -> list[MTU]:
    mtus: list[MTU] = []
    for offset, unit in enumerate(units):
        start, end = unit["start_line"], unit["end_line"]
        mtus.append(
            MTU(
                mtu_id=prefixed_id("mtu", [collection, source_file, start, end]),
                collection=collection,
                source_file=source_file,
                line_range=(start, end),
                title=unit["title"],
                keywords=unit["keywords"],
                summary=unit["summary"],
                unit_kind=unit["unit_kind"],
                source_order_index=order_offset + offset,
            )
        )
    return mtus


def whole_document_fallback(
    line_count: int,
    *,
    collection: str,
    source_file: str,
    order_offset: int = 0,
    title: str = "",
) -> list[MTU]:
    """Deterministic single-MTU fallback when cut planning cannot be validated."""
    if line_count <= 0:
        return []
    unit = {
        "start_line": 1,
        "end_line": line_count,
        "title": title or source_file.rsplit(".", 1)[0] or "Source Unit",
        "keywords": [],
        "summary": "Imported as a single teachable unit (cut-plan fallback).",
        "unit_kind": "concept",
    }
    return build_mtus([unit], collection=collection, source_file=source_file, order_offset=order_offset)


def mtu_text(markdown: str, line_range: tuple[int, int]) -> str:
    """Slice a MTU's source text from cleaned Markdown by 1-based inclusive range."""
    lines = markdown.splitlines()
    start, end = line_range
    return "\n".join(lines[max(0, start - 1) : end]).strip()


def _normalize_unit(raw: Any, index: int) -> dict:
    if not isinstance(raw, dict):
        raise MtuCoverageError(f"unit {index} must be an object")
    start = _int(raw.get("start_line"), f"unit {index} start_line")
    end = _int(raw.get("end_line"), f"unit {index} end_line")
    title = str(raw.get("title") or "").strip() or f"Unit {index}"
    keywords = normalize_concepts(raw.get("keywords") or [])
    summary = str(raw.get("summary") or "").strip()
    unit_kind = str(raw.get("unit_kind") or "concept").strip().lower()
    if unit_kind not in _VALID_UNIT_KINDS:
        unit_kind = "concept"
    return {
        "start_line": start,
        "end_line": end,
        "title": title,
        "keywords": keywords,
        "summary": summary,
        "unit_kind": unit_kind,
    }


def _int(value: Any, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise MtuCoverageError(f"{label} must be an integer") from exc
