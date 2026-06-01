"""ArchivistAgent: clean OCR Markdown + cut it into MTUs.

Stage ② of the pipeline. See docs/REBUILD-DESIGN.md §4.

  clean(raw_markdown) -> cleaned_markdown          (LLM delete plan + local deletion)
  cut_mtus(cleaned_markdown, collection, file) -> list[MTU]
      number lines -> ARCHIVIST_MTU_PROMPT -> strict JSON -> validate full line
      coverage -> repair on failure -> raise on exhausted repairs.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from tree.agents.base import Agent
from tree.agents.parsers import extract_json_object
from tree.agents.prompts import ARCHIVIST_CLEAN_PROMPT, ARCHIVIST_MTU_PROMPT
from tree.planner.models import MTU
from tree.planner.mtu import (
    MtuCoverageError,
    _int as _strict_mtu_int,
    _normalize_unit as _normalize_mtu_unit,
    build_mtus,
    number_lines,
    validate_and_normalize,
)

logger = logging.getLogger(__name__)


class ArchivistAgent(Agent):
    role = "archivist"

    async def clean(
        self,
        raw_markdown: str,
        *,
        timeout_sec: float | None = None,
        repair_attempts: int = 1,
    ) -> str:
        """Remove non-teaching OCR lines using an LLM delete plan."""
        lines = raw_markdown.splitlines()
        line_count = len(lines)
        if line_count == 0:
            return ""
        raw_plan = await self.complete(
            _clean_prompt_body(raw_markdown, line_count),
            system_prompt=ARCHIVIST_CLEAN_PROMPT,
            timeout_sec=timeout_sec,
        )
        plan = extract_json_object(raw_plan)
        deleted_ranges, invalid_ranges = _partition_deleted_ranges(plan, line_count=line_count)

        for _attempt in range(repair_attempts):
            if not invalid_ranges:
                break
            raw_repair = await self.complete(
                _clean_repair_prompt_body(
                    raw_markdown,
                    line_count=line_count,
                    valid_ranges=deleted_ranges,
                    invalid_ranges=invalid_ranges,
                ),
                system_prompt=ARCHIVIST_CLEAN_PROMPT,
                timeout_sec=timeout_sec,
            )
            repair_plan = extract_json_object(raw_repair)
            repaired_ranges, invalid_ranges = _partition_deleted_ranges(
                repair_plan,
                line_count=line_count,
                locked_ranges=deleted_ranges,
            )
            deleted_ranges.extend(repaired_ranges)

        if invalid_ranges:
            raise ValueError(f"Archivist clean delete ranges invalid: {invalid_ranges}")
        return _apply_deleted_ranges(lines, deleted_ranges)

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

        prompt_body = _mtu_prompt_body(cleaned_markdown, line_count)
        partition: dict[str, Any] | None = None
        malformed_feedback = ""
        last_error: ValueError | None = None

        for attempt in range(repair_attempts + 1):
            if partition is None:
                try:
                    raw = await self.complete(
                        prompt_body + malformed_feedback,
                        system_prompt=ARCHIVIST_MTU_PROMPT,
                        timeout_sec=timeout_sec,
                    )
                    plan = extract_json_object(raw)
                    partition = _partition_mtu_plan(plan, line_count=line_count)
                except (ValueError, MtuCoverageError) as exc:
                    last_error = exc
                    logger.warning(
                        "MTU cut plan malformed for %s/%s (attempt %d): %s",
                        collection, source_file, attempt + 1, exc,
                    )
                    malformed_feedback = (
                        f"\n\nPREVIOUS RESPONSE WAS NOT VALID JSON: {exc}. "
                        "Regenerate one strict JSON object only, with valid `units` and "
                        "`skipped_ranges` that cover every line exactly once."
                    )
                    continue
            try:
                final_plan = {
                    "units": partition["valid_units"],
                    "skipped_ranges": partition["valid_skipped_ranges"],
                }
                if partition["invalid_units"] or partition["invalid_skipped_ranges"] or partition["missing_ranges"]:
                    raise MtuCoverageError(_mtu_partition_problem(partition))
                units, _skipped = validate_and_normalize(final_plan, line_count)
                units = sorted(units, key=lambda unit: (unit["start_line"], unit["end_line"]))
                return build_mtus(
                    units,
                    collection=collection,
                    source_file=source_file,
                    order_offset=order_offset,
                )
            except MtuCoverageError as exc:
                last_error = exc
                logger.warning(
                    "MTU cut plan invalid for %s/%s (attempt %d): %s",
                    collection, source_file, attempt + 1, exc,
                )
                if attempt >= repair_attempts:
                    break
                raw_repair = await self.complete(
                    _mtu_repair_prompt_body(cleaned_markdown, line_count=line_count, partition=partition),
                    system_prompt=ARCHIVIST_MTU_PROMPT,
                    timeout_sec=timeout_sec,
                )
                try:
                    repair_plan = extract_json_object(raw_repair)
                    partition = _partition_mtu_plan(
                        repair_plan,
                        line_count=line_count,
                        locked_units=partition["valid_units"],
                        locked_skipped_ranges=partition["valid_skipped_ranges"],
                    )
                except (ValueError, MtuCoverageError) as repair_exc:
                    last_error = repair_exc
                    logger.warning(
                        "MTU cut repair malformed for %s/%s (attempt %d): %s",
                        collection, source_file, attempt + 1, repair_exc,
                    )
                    partition = None
                    malformed_feedback = (
                        f"\n\nPREVIOUS RESPONSE WAS NOT VALID JSON: {repair_exc}. "
                        "Regenerate the full strict JSON object only, with valid `units` and "
                        "`skipped_ranges` that cover every line exactly once."
                    )

        raise last_error or MtuCoverageError(
            f"MTU cut plan invalid for {collection}/{source_file} after {repair_attempts + 1} attempts"
        )


def _mtu_prompt_body(cleaned_markdown: str, line_count: int) -> str:
    numbered = number_lines(cleaned_markdown)
    return (
        "NUMBERED_MARKDOWN_CONTRACT\n"
        f"TOTAL_LINES: {line_count}\n"
        f"LAST_VALID_LINE: {line_count}\n"
        f"Do not output start_line or end_line greater than {line_count}.\n"
        "Every line number in units and skipped_ranges must be between 1 and "
        f"{line_count}, inclusive.\n"
        "END_CONTRACT\n\n"
        f"{numbered}"
    )


def _mtu_repair_prompt_body(cleaned_markdown: str, *, line_count: int, partition: dict[str, Any]) -> str:
    numbered = number_lines(cleaned_markdown)
    return (
        "REPAIR_ONLY_INVALID_MTU_BLOCKS\n"
        f"PREVIOUS ATTEMPT WAS INVALID: {_mtu_partition_problem(partition)}\n"
        f"TOTAL_LINES: {line_count}\n"
        f"LAST_VALID_LINE: {line_count}\n"
        "The following valid blocks are locked. Do not change or repeat them.\n"
        f"VALID_UNITS_LOCKED:\n{json.dumps(partition['valid_units'], ensure_ascii=False)}\n\n"
        f"VALID_SKIPPED_RANGES_LOCKED:\n{json.dumps(partition['valid_skipped_ranges'], ensure_ascii=False)}\n\n"
        "Only regenerate replacements for the invalid blocks below and any missing ranges. "
        "Do not regenerate the full file. Replacement blocks must not overlap locked blocks.\n"
        "Repair coverage rules: every missing range must be fully covered by replacement units "
        "or skipped_ranges; replacement blocks may not overlap each other or any locked block; "
        f"all line numbers must stay within 1..{line_count}.\n"
        "Repair metadata rules for every replacement unit: title display width 6-40, "
        "keywords length 1-10 items, summary display width 20-150, and unit_kind must be one "
        "of concept/example/exercise/misconception/procedure/application.\n"
        f"INVALID_UNITS:\n{json.dumps(partition['invalid_units'], ensure_ascii=False)}\n\n"
        f"INVALID_SKIPPED_RANGES:\n{json.dumps(partition['invalid_skipped_ranges'], ensure_ascii=False)}\n\n"
        f"MISSING_RANGES:\n{json.dumps(partition['missing_ranges'], ensure_ascii=False)}\n\n"
        "Return strict JSON only in this shape, containing only replacement blocks:\n"
        '{"units": [{"start_line": 1, "end_line": 1, "title": "标题", '
        '"keywords": ["关键词"], "summary": "摘要", "unit_kind": "concept"}], '
        '"skipped_ranges": [{"start_line": 1, "end_line": 1, "reason": "reason"}]}\n\n'
        f"{numbered}"
    )


def _partition_mtu_plan(
    plan: dict[str, Any],
    *,
    line_count: int,
    locked_units: list[dict[str, Any]] | None = None,
    locked_skipped_ranges: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    raw_units = plan.get("units") or []
    raw_skipped = plan.get("skipped_ranges") or []
    if not isinstance(raw_units, list) or not isinstance(raw_skipped, list):
        raise MtuCoverageError("`units` and `skipped_ranges` must be lists")

    valid_units = list(locked_units or [])
    valid_skipped = list(locked_skipped_ranges or [])
    occupied = _mtu_spans(valid_units, valid_skipped)
    invalid_units: list[dict[str, Any]] = []
    invalid_skipped: list[dict[str, Any]] = []

    for index, raw in enumerate(raw_units, start=1):
        try:
            unit = _normalize_mtu_unit(raw, index)
            problem = _mtu_span_problem(
                unit["start_line"],
                unit["end_line"],
                line_count=line_count,
                occupied=occupied,
            )
        except MtuCoverageError as exc:
            invalid_units.append({"index": index, "block": raw, "problem": str(exc)})
            continue
        if problem:
            added = False
            for start, end in _uncovered_segments(unit["start_line"], unit["end_line"], occupied, line_count):
                segment = {**unit, "start_line": start, "end_line": end}
                occupied.append((start, end))
                valid_units.append(segment)
                added = True
            if added:
                continue
            if "overlaps locked/valid range" in problem:
                continue
            invalid_units.append({"index": index, "block": unit, "problem": problem})
            continue
        occupied.append((unit["start_line"], unit["end_line"]))
        valid_units.append(unit)

    for index, raw in enumerate(raw_skipped, start=1):
        try:
            skipped = _normalize_skipped_range(raw, index)
            problem = _mtu_span_problem(
                skipped["start_line"],
                skipped["end_line"],
                line_count=line_count,
                occupied=occupied,
            )
        except MtuCoverageError:
            continue
        if problem:
            continue
        occupied.append((skipped["start_line"], skipped["end_line"]))
        valid_skipped.append(skipped)

    return {
        "valid_units": valid_units,
        "valid_skipped_ranges": valid_skipped,
        "invalid_units": invalid_units,
        "invalid_skipped_ranges": invalid_skipped,
        "missing_ranges": _missing_ranges(occupied, line_count),
    }


def _normalize_skipped_range(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise MtuCoverageError(f"skipped {index} must be an object")
    return {
        "start_line": _strict_mtu_int(raw.get("start_line"), f"skipped {index} start_line"),
        "end_line": _strict_mtu_int(raw.get("end_line"), f"skipped {index} end_line"),
        "reason": str(raw.get("reason") or "")[:300],
    }


def _mtu_spans(
    units: list[dict[str, Any]],
    skipped_ranges: list[dict[str, Any]],
) -> list[tuple[int, int]]:
    return [
        (int(item["start_line"]), int(item["end_line"]))
        for item in [*units, *skipped_ranges]
    ]


def _mtu_span_problem(
    start: int,
    end: int,
    *,
    line_count: int,
    occupied: list[tuple[int, int]],
) -> str:
    if start < 1 or end > line_count:
        return f"range {start}-{end} is out of bounds (1..{line_count})"
    if end < start:
        return f"range {start}-{end} is inverted"
    for used_start, used_end in occupied:
        if start <= used_end and end >= used_start:
            return f"range {start}-{end} overlaps locked/valid range {used_start}-{used_end}"
    return ""


def _uncovered_segments(
    start: int,
    end: int,
    occupied: list[tuple[int, int]],
    line_count: int,
) -> list[tuple[int, int]]:
    if start < 1 or end > line_count or end < start:
        return []
    segments = [(start, end)]
    for used_start, used_end in sorted(occupied):
        next_segments: list[tuple[int, int]] = []
        for seg_start, seg_end in segments:
            if used_end < seg_start or used_start > seg_end:
                next_segments.append((seg_start, seg_end))
                continue
            if seg_start < used_start:
                next_segments.append((seg_start, used_start - 1))
            if used_end < seg_end:
                next_segments.append((used_end + 1, seg_end))
        segments = next_segments
        if not segments:
            break
    return segments


def _missing_ranges(spans: list[tuple[int, int]], line_count: int) -> list[dict[str, int]]:
    missing: list[dict[str, int]] = []
    expected = 1
    for start, end in sorted(spans):
        if start > expected:
            missing.append({"start_line": expected, "end_line": start - 1})
        expected = max(expected, end + 1)
    if expected <= line_count:
        missing.append({"start_line": expected, "end_line": line_count})
    return missing


def _mtu_partition_problem(partition: dict[str, Any]) -> str:
    parts: list[str] = []
    if partition["invalid_units"]:
        unit_problems = [str(item.get("problem") or "") for item in partition["invalid_units"][:3]]
        parts.append(f"invalid_units={len(partition['invalid_units'])}: {'; '.join(unit_problems)}")
    if partition["invalid_skipped_ranges"]:
        skipped_problems = [str(item.get("problem") or "") for item in partition["invalid_skipped_ranges"][:3]]
        parts.append(f"invalid_skipped_ranges={len(partition['invalid_skipped_ranges'])}: {'; '.join(skipped_problems)}")
    if partition["missing_ranges"]:
        parts.append(f"missing_ranges={partition['missing_ranges']}")
    return "; ".join(parts) or "unknown MTU partition error"


def _clean_prompt_body(raw_markdown: str, line_count: int) -> str:
    numbered = number_lines(raw_markdown)
    return (
        "NUMBERED_MARKDOWN_CONTRACT\n"
        f"TOTAL_LINES: {line_count}\n"
        f"LAST_VALID_LINE: {line_count}\n"
        f"Only output deleted_ranges with line numbers between 1 and {line_count}, inclusive.\n"
        "Do not output cleaned Markdown. Do not modify headings.\n"
        "END_CONTRACT\n\n"
        f"{numbered}"
    )


def _clean_repair_prompt_body(
    raw_markdown: str,
    *,
    line_count: int,
    valid_ranges: list[dict[str, Any]],
    invalid_ranges: list[dict[str, Any]],
) -> str:
    numbered = number_lines(raw_markdown)
    return (
        "REPAIR_ONLY_INVALID_DELETED_RANGES\n"
        f"TOTAL_LINES: {line_count}\n"
        "The following valid deleted_ranges are locked. Do not change or repeat them.\n"
        f"VALID_DELETED_RANGES:\n{json.dumps(valid_ranges, ensure_ascii=False)}\n\n"
        "Only regenerate replacements for the invalid deleted_ranges below. "
        "If an invalid range should not delete anything, omit it from the replacement output.\n"
        f"INVALID_DELETED_RANGES:\n{json.dumps(invalid_ranges, ensure_ascii=False)}\n\n"
        "Return strict JSON only in this shape:\n"
        '{"deleted_ranges": [{"start_line": 1, "end_line": 1, "reason": "reason"}]}\n\n'
        f"{numbered}"
    )


def _partition_deleted_ranges(
    plan: dict[str, Any],
    *,
    line_count: int,
    locked_ranges: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_ranges = plan.get("deleted_ranges") or []
    if not isinstance(raw_ranges, list):
        raise ValueError("`deleted_ranges` must be a list")

    occupied = [
        (int(item["start_line"]), int(item["end_line"]))
        for item in (locked_ranges or [])
    ]
    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []

    for raw in sorted(raw_ranges, key=lambda item: (item.get("start_line", 0), item.get("end_line", 0))):
        item = _deleted_range_payload(raw)
        problem = _deleted_range_problem(item, line_count=line_count, occupied=occupied)
        if problem:
            invalid.append({**item, "problem": problem})
            continue
        occupied.append((item["start_line"], item["end_line"]))
        valid.append(item)

    return valid, invalid


def _deleted_range_payload(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"start_line": 0, "end_line": 0, "reason": "", "raw": raw}
    return {
        "start_line": _int(raw.get("start_line")),
        "end_line": _int(raw.get("end_line")),
        "reason": str(raw.get("reason") or "")[:300],
    }


def _deleted_range_problem(
    item: dict[str, Any],
    *,
    line_count: int,
    occupied: list[tuple[int, int]],
) -> str:
    start = item["start_line"]
    end = item["end_line"]
    if start < 1 or end > line_count:
        return f"out_of_bounds: {start}-{end} not within 1..{line_count}"
    if end < start:
        return f"inverted: {start}-{end}"
    for used_start, used_end in occupied:
        if start <= used_end and end >= used_start:
            return f"overlap: {start}-{end} overlaps locked/valid range {used_start}-{used_end}"
    return ""


def _apply_deleted_ranges(lines: list[str], deleted_ranges: list[dict[str, Any]]) -> str:
    delete_lines: set[int] = set()
    for item in deleted_ranges:
        delete_lines.update(range(item["start_line"], item["end_line"] + 1))
    return "\n".join(line for index, line in enumerate(lines, start=1) if index not in delete_lines).strip()


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
