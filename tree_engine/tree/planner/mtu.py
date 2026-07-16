"""MTU helpers: line numbering, cut-plan coverage validation, MTU construction.

Pure functions (no LLM) so they can be unit-tested directly. The Archivist agent
calls these around its LLM cut-plan call.
"""

from __future__ import annotations

import unicodedata
from typing import Any

from tree.planner.ids import normalize_concepts, prefixed_id
from tree.planner.models import MTU

_VALID_UNIT_KINDS = {
    "concept",
    "exercise",
    "excercise",
    "application",
    "review",
    "summary",
    "intro",
}
_MAX_DEFINES = 4
_TITLE_MAX_WIDTH = 40
_SUMMARY_MAX_WIDTH = 150


class MtuCoverageError(ValueError):
    """Raised when an Archivist cut plan does not tile every source line exactly once."""


def number_lines(text: str) -> str:
    """Return 1-based ``<n>\\t<line>`` numbered Markdown for the cut-plan prompt."""
    lines = text.splitlines()
    return "\n".join(f"{i}\t{line}" for i, line in enumerate(lines, start=1))


def validate_and_normalize(plan: dict[str, Any], line_count: int) -> tuple[list[dict], list[dict]]:
    """Validate that units tile lines 1..line_count exactly once.

    Returns (normalized_units, []). Raises MtuCoverageError on any
    gap, overlap, or out-of-bounds range so the agent can repair.
    """
    if line_count <= 0:
        return [], []

    if "skipped_ranges" in plan:
        raise MtuCoverageError("`skipped_ranges` is not allowed in MTU cut plans")
    raw_units = plan.get("units") or []
    if not isinstance(raw_units, list):
        raise MtuCoverageError("`units` must be a list")

    spans: list[tuple[int, int, str, dict]] = []
    units: list[dict] = []
    for index, raw in enumerate(raw_units, start=1):
        unit = _normalize_unit(raw, index)
        spans.append((unit["start_line"], unit["end_line"], "unit", unit))
        units.append(unit)

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

    return units, []


def build_mtus(
    units: list[dict],
    *,
    collection: str,
    source_file: str,
    source_id: str = "",
    source_sha256: str = "",
    order_offset: int = 0,
) -> list[MTU]:
    units = _merge_auxiliary_units(units)
    units = _merge_short_units(units)
    _validate_final_units(units)
    mtus: list[MTU] = []
    for offset, unit in enumerate(units):
        start, end = unit["start_line"], unit["end_line"]
        mtus.append(
            MTU(
                mtu_id=prefixed_id(
                    "mtu",
                    [source_id or f"{collection}/{source_file}", source_sha256, start, end],
                ),
                collection=collection,
                source_file=source_file,
                source_id=source_id,
                source_sha256=source_sha256,
                line_range=(start, end),
                title=unit["title"],
                defines=unit.get("defines", []),
                keywords=unit.get("defines", unit.get("keywords", [])),
                summary=unit["summary"],
                unit_kind=unit["unit_kind"],
                source_order_index=order_offset + offset,
            )
        )
    return mtus


def _validate_final_units(units: list[dict]) -> None:
    for index, unit in enumerate(units, start=1):
        if unit["unit_kind"] != "concept":
            raise MtuCoverageError(f"final unit {index} must be concept, got {unit['unit_kind']}")
        if not unit.get("defines"):
            raise MtuCoverageError(f"final unit {index} concept must contain at least one define")


def _merge_auxiliary_units(units: list[dict]) -> list[dict]:
    """Drop/merge non-concept MTUs that should not become planner nodes."""
    merged = [dict(unit) for unit in sorted(units, key=lambda item: (item["start_line"], item["end_line"]))]
    removed: set[int] = set()

    for index, unit in enumerate(merged):
        if unit["unit_kind"] == "application":
            target_index = _find_concept_index(merged, index, step=-1, removed=removed)
            if target_index is not None:
                _absorb_unit(merged[target_index], unit)
            removed.add(index)
        elif unit["unit_kind"] in {"intro", "summary", "exercise", "excercise", "review"}:
            removed.add(index)

    result = [unit for index, unit in enumerate(merged) if index not in removed]
    result.sort(key=lambda item: (item["start_line"], item["end_line"]))
    return result


def _merge_short_units(
    units: list[dict[str, Any]], minimum_lines: int = 20
) -> list[dict[str, Any]]:
    """Deterministically absorb short concept units when an adjacent unit exists."""
    merged = [dict(unit) for unit in units]
    while len(merged) > 1:
        short_index = next(
            (
                index
                for index, unit in enumerate(merged)
                if int(unit["end_line"]) - int(unit["start_line"]) + 1 < minimum_lines
            ),
            None,
        )
        if short_index is None:
            break
        target_index = short_index - 1 if short_index > 0 else 1
        _absorb_unit(merged[target_index], merged[short_index])
        merged.pop(short_index)
        merged.sort(key=lambda item: (item["start_line"], item["end_line"]))
    return merged


def _find_concept_index(
    units: list[dict], start_index: int, *, step: int, removed: set[int]
) -> int | None:
    index = start_index + step
    while 0 <= index < len(units):
        if index not in removed and units[index]["unit_kind"] == "concept":
            return index
        index += step
    return None


def _absorb_unit(target: dict, source: dict) -> None:
    target["start_line"] = min(target["start_line"], source["start_line"])
    target["end_line"] = max(target["end_line"], source["end_line"])
    target["defines"] = normalize_concepts(list(target.get("defines") or []) + list(source.get("defines") or []))[
        :_MAX_DEFINES
    ]


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
    title = _truncate_display_width(
        str(raw.get("title") or "").strip() or f"教学单元 {index}",
        _TITLE_MAX_WIDTH,
    )
    if "keywords" in raw:
        raise MtuCoverageError(f"unit {index} keywords is not allowed; use defines")
    defines = normalize_concepts(raw.get("defines") or [])
    if len(defines) > _MAX_DEFINES:
        raise MtuCoverageError(f"unit {index} defines must contain no more than {_MAX_DEFINES} items")
    summary = _truncate_display_width(str(raw.get("summary") or "").strip() or title, _SUMMARY_MAX_WIDTH)
    unit_kind = str(raw.get("unit_kind") or "concept").strip().lower()
    if unit_kind == "excercise":
        unit_kind = "exercise"
    if unit_kind not in _VALID_UNIT_KINDS:
        unit_kind = "concept"
    return {
        "start_line": start,
        "end_line": end,
        "title": title,
        "defines": defines,
        "summary": summary,
        "unit_kind": unit_kind,
    }


def _int(value: Any, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise MtuCoverageError(f"{label} must be an integer") from exc


def _truncate_display_width(value: str, maximum: int) -> str:
    if _display_width(value) <= maximum:
        return value
    result: list[str] = []
    width = 0
    for char in value:
        char_width = 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
        if width + char_width > maximum - 1:
            break
        result.append(char)
        width += char_width
    return "".join(result).rstrip() + "…"


def _display_width(value: str) -> int:
    """Count display characters for LLM metadata limits."""
    width = 0
    for char in value:
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width
