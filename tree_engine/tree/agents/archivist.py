"""ArchivistAgent: clean OCR Markdown + cut it into MTUs.

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
from tree.planner.ids import normalize_text_key
from tree.planner.models import MTU
from tree.planner.mtu import (
    MtuCoverageError,
    _normalize_unit as _normalize_mtu_unit,
    build_mtus,
    number_lines,
    validate_and_normalize,
)

logger = logging.getLogger(__name__)

_MIN_FINAL_MTU_LINES = 20


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
        prompt_body = _clean_prompt_body(raw_markdown, line_count)
        malformed_feedback = ""
        last_error: ValueError | None = None
        plan: dict[str, Any] | None = None

        for attempt in range(repair_attempts + 1):
            try:
                raw_plan = await self.complete(
                    prompt_body + malformed_feedback,
                    system_prompt=ARCHIVIST_CLEAN_PROMPT,
                    timeout_sec=timeout_sec,
                )
                plan = extract_json_object(raw_plan)
                break
            except ValueError as exc:
                last_error = exc
                logger.warning("Clean delete plan malformed (attempt %d): %s", attempt + 1, exc)
                malformed_feedback = (
                    f"\n\nPREVIOUS RESPONSE WAS NOT VALID JSON: {exc}. "
                    "Regenerate one strict JSON object only with valid delete ranges."
                )
        if plan is None:
            raise last_error or ValueError("Clean delete plan malformed.")
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
                        "Regenerate one strict JSON object only, with valid `units` "
                        "that cover every line exactly once. Do not output skipped_ranges."
                    )
                    continue
            try:
                final_plan = {"units": partition["valid_units"]}
                if (
                    partition["invalid_units"]
                    or partition["missing_ranges"]
                    or partition["overlap_ranges"]
                    or partition["semantic_unit_problems"]
                ):
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
                    try:
                        if _merge_short_units_fallback(partition, line_count=line_count):
                            final_plan = {"units": partition["valid_units"]}
                            units, _skipped = validate_and_normalize(final_plan, line_count)
                            units = sorted(units, key=lambda unit: (unit["start_line"], unit["end_line"]))
                            return build_mtus(
                                units,
                                collection=collection,
                                source_file=source_file,
                                order_offset=order_offset,
                            )
                    except MtuCoverageError as fallback_exc:
                        last_error = fallback_exc
                    break
                try:
                    while True:
                        stage = _next_mtu_repair_stage(partition)
                        if stage is None:
                            break
                        if stage == "coverage":
                            if _coverage_invalid_units(partition):
                                partition = None
                                malformed_feedback = (
                                    f"\n\nPREVIOUS ATTEMPT WAS INVALID: {exc}. "
                                    "Regenerate the full strict JSON object with `units` only. "
                                    "Do not output skipped_ranges."
                                )
                                break
                            await self._repair_mtu_assignments(
                                cleaned_markdown,
                                line_count=line_count,
                                partition=partition,
                                timeout_sec=timeout_sec,
                            )
                            continue
                        if stage == "short_unit":
                            await self._repair_mtu_units(
                                cleaned_markdown,
                                line_count=line_count,
                                partition=partition,
                                timeout_sec=timeout_sec,
                                problem_types={"short_unit"},
                            )
                            continue
                        if stage == "metadata":
                            if partition["invalid_units"]:
                                await self._repair_mtu_metadata(
                                    cleaned_markdown,
                                    line_count=line_count,
                                    partition=partition,
                                    timeout_sec=timeout_sec,
                                )
                            else:
                                await self._repair_mtu_units(
                                    cleaned_markdown,
                                    line_count=line_count,
                                    partition=partition,
                                    timeout_sec=timeout_sec,
                                    problem_types={"empty_defines", "duplicate_defines"},
                                )
                            continue
                        if stage == "regenerate":
                            partition = None
                            malformed_feedback = (
                                f"\n\nPREVIOUS ATTEMPT WAS INVALID: {exc}. "
                                "Regenerate the full strict JSON object with `units` only. "
                                "Do not output skipped_ranges."
                            )
                            break
                except (ValueError, MtuCoverageError) as repair_exc:
                    last_error = repair_exc
                    logger.warning(
                        "MTU cut repair invalid for %s/%s (attempt %d): %s",
                        collection, source_file, attempt + 1, repair_exc,
                    )

        raise last_error or MtuCoverageError(
            f"MTU cut plan invalid for {collection}/{source_file} after {repair_attempts + 1} attempts"
        )

    async def _repair_mtu_assignments(
        self,
        cleaned_markdown: str,
        *,
        line_count: int,
        partition: dict[str, Any],
        timeout_sec: float | None,
    ) -> None:
        problems = [
            *[
                {"problem_type": "overlap", "range": item["range"], "previous": item["previous"], "next": item["next"]}
                for item in partition["overlap_ranges"]
            ],
            *[
                {"problem_type": "missing_range", "range": item}
                for item in partition["missing_ranges"]
            ],
        ]
        for problem in problems:
            previous, next_unit = _assignment_neighbors(partition["valid_units"], problem)
            candidates = [unit for unit in (previous, next_unit) if unit is not None]
            if not candidates:
                raise MtuCoverageError(f"No MTU candidates for {problem['problem_type']} {problem['range']}")
            titles = [unit["title"] for unit in candidates]
            if len(set(titles)) != len(titles):
                raise MtuCoverageError(f"Ambiguous assignment candidates share title: {titles}")
            raw_repair = await self.complete(
                _mtu_assignment_prompt_body(
                    cleaned_markdown,
                    problem_type=problem["problem_type"],
                    line_range=problem["range"],
                    previous=previous,
                    next_unit=next_unit,
                ),
                system_prompt=ARCHIVIST_MTU_PROMPT,
                timeout_sec=timeout_sec,
            )
            decision = extract_json_object(raw_repair)
            _apply_assignment_decision(partition["valid_units"], problem, decision)
            _sync_invalid_unit_ranges(partition)
        _refresh_mtu_partition_problems(partition, line_count=line_count)

    async def _repair_mtu_metadata(
        self,
        cleaned_markdown: str,
        *,
        line_count: int,
        partition: dict[str, Any],
        timeout_sec: float | None,
    ) -> None:
        remaining_invalid: list[dict[str, Any]] = []
        for item in partition["invalid_units"]:
            metadata_errors = item.get("metadata_errors") or []
            if not metadata_errors:
                remaining_invalid.append(item)
                continue
            original_range = _unit_line_range(item["block"])
            if original_range is None:
                remaining_invalid.append(item)
                continue
            field_error = metadata_errors[0]
            field = field_error["field"]
            raw_repair = await self.complete(
                _mtu_metadata_repair_prompt_body(
                    cleaned_markdown,
                    invalid_unit=item,
                    line_range=original_range,
                    metadata_error=field_error,
                ),
                system_prompt=ARCHIVIST_MTU_PROMPT,
                timeout_sec=timeout_sec,
            )
            repair = extract_json_object(raw_repair)
            repaired_block = _apply_metadata_field_repair(item["block"], repair, field)
            try:
                normalized = _normalize_mtu_unit(repaired_block, int(item["index"]))
            except MtuCoverageError as exc:
                problem = str(exc)
                item["block"] = repaired_block
                item["problem"] = problem
                item["metadata_errors"] = _metadata_errors(problem)
                remaining_invalid.append(item)
                placeholder = _metadata_placeholder_unit(repaired_block, int(item["index"]))
                if placeholder is not None:
                    _replace_unit_by_original_range(partition["valid_units"], original_range, placeholder)
                continue

            if normalized["start_line"] < 1 or normalized["end_line"] > line_count:
                raise MtuCoverageError(
                    f"metadata repair range {normalized['start_line']}-{normalized['end_line']} "
                    f"is out of bounds (1..{line_count})"
                )
            if normalized["end_line"] < normalized["start_line"]:
                raise MtuCoverageError(
                    f"metadata repair range {normalized['start_line']}-{normalized['end_line']} is inverted"
                )
            _replace_unit_by_original_range(partition["valid_units"], original_range, normalized)
        partition["invalid_units"] = remaining_invalid
        _refresh_mtu_partition_problems(partition, line_count=line_count)

    async def _repair_mtu_units(
        self,
        cleaned_markdown: str,
        *,
        line_count: int,
        partition: dict[str, Any],
        timeout_sec: float | None,
        problem_types: set[str] | None = None,
    ) -> None:
        while True:
            problem = _first_semantic_problem(partition, problem_types)
            if problem is None:
                return
            if problem["problem_type"] == "duplicate_defines":
                await self._repair_mtu_duplicate_defines(
                    cleaned_markdown,
                    partition=partition,
                    problem=problem,
                    timeout_sec=timeout_sec,
                )
                continue
            window_units = _semantic_repair_window(partition["valid_units"], problem)
            if not window_units:
                raise MtuCoverageError(f"No MTU repair window for semantic problem: {problem}")
            window_start = min(int(unit["start_line"]) for unit in window_units)
            window_end = max(int(unit["end_line"]) for unit in window_units)
            raw_repair = await self.complete(
                _mtu_units_repair_prompt_body(
                    cleaned_markdown,
                    problem=problem,
                    window_units=window_units,
                    window_start=window_start,
                    window_end=window_end,
                ),
                system_prompt=ARCHIVIST_MTU_PROMPT,
                timeout_sec=timeout_sec,
            )
            repair = extract_json_object(raw_repair)
            repaired_units = _normalize_repair_units(
                repair,
                line_count=line_count,
                window_start=window_start,
                window_end=window_end,
                problem=problem,
            )
            partition["valid_units"] = [
                unit
                for unit in partition["valid_units"]
                if int(unit["end_line"]) < window_start or int(unit["start_line"]) > window_end
            ]
            partition["invalid_units"] = [
                item
                for item in partition["invalid_units"]
                if not _unit_range_within_window(item.get("block"), window_start, window_end)
            ]
            partition["valid_units"].extend(repaired_units)
            _refresh_mtu_partition_problems(partition, line_count=line_count)

    async def _repair_mtu_duplicate_defines(
        self,
        cleaned_markdown: str,
        *,
        partition: dict[str, Any],
        problem: dict[str, Any],
        timeout_sec: float | None,
    ) -> None:
        duplicate_units = _duplicate_define_repair_units(partition["valid_units"], problem)
        if not duplicate_units:
            raise MtuCoverageError(f"No MTU repair units for duplicate define problem: {problem}")
        raw_repair = await self.complete(
            _mtu_duplicate_defines_repair_prompt_body(
                cleaned_markdown,
                problem=problem,
                duplicate_units=duplicate_units,
            ),
            system_prompt=ARCHIVIST_MTU_PROMPT,
            timeout_sec=timeout_sec,
        )
        repair = extract_json_object(raw_repair)
        repaired_units = _normalize_duplicate_define_repair_units(repair, duplicate_units)
        expected_ranges = {
            (int(unit["start_line"]), int(unit["end_line"]))
            for unit in duplicate_units
        }
        partition["valid_units"] = [
            unit
            for unit in partition["valid_units"]
            if (int(unit["start_line"]), int(unit["end_line"])) not in expected_ranges
        ]
        partition["valid_units"].extend(repaired_units)
        _refresh_mtu_partition_problems(partition, line_count=len(cleaned_markdown.splitlines()))


def _mtu_prompt_body(cleaned_markdown: str, line_count: int) -> str:
    numbered = number_lines(cleaned_markdown)
    return (
        "NUMBERED_MARKDOWN_CONTRACT\n"
        f"TOTAL_LINES: {line_count}\n"
        f"LAST_VALID_LINE: {line_count}\n"
        f"Do not output start_line or end_line greater than {line_count}.\n"
        f"Every line number in units must be between 1 and {line_count}, inclusive.\n"
        "Do not output skipped_ranges. The JSON object must contain only `units`.\n"
        "END_CONTRACT\n\n"
        f"{numbered}"
    )


def _mtu_assignment_prompt_body(
    cleaned_markdown: str,
    *,
    problem_type: str,
    line_range: dict[str, int],
    previous: dict[str, Any] | None,
    next_unit: dict[str, Any] | None,
) -> str:
    lines = cleaned_markdown.splitlines()
    start = line_range["start_line"]
    end = line_range["end_line"]
    excerpt = "\n".join(
        f"{line_no}\t{lines[line_no - 1]}"
        for line_no in range(max(1, start), min(len(lines), end) + 1)
    )
    return (
        "ASSIGN_MTU_RANGE\n"
        "Decide whether the given range belongs to the previous MTU or the next MTU.\n"
        "Do not rewrite any MTU metadata. Do not create units. Do not output prose.\n"
        "Return strict JSON only in this exact shape: {\"mtu_title\": \"目标MTU标题\"}\n\n"
        f"{json.dumps({
            'problem_type': problem_type,
            'range': line_range,
            'range_excerpt': excerpt,
            'previous_mtu_metadata': _assignment_meta(previous),
            'next_mtu_metadata': _assignment_meta(next_unit),
        }, ensure_ascii=False, indent=2)}"
    )


def _mtu_metadata_repair_prompt_body(
    cleaned_markdown: str,
    *,
    invalid_unit: dict[str, Any],
    line_range: dict[str, int],
    metadata_error: dict[str, str],
) -> str:
    lines = cleaned_markdown.splitlines()
    start = line_range["start_line"]
    end = line_range["end_line"]
    excerpt = "\n".join(
        f"{line_no}\t{lines[line_no - 1]}"
        for line_no in range(max(1, start), min(len(lines), end) + 1)
    )
    field = metadata_error["field"]
    output_shapes = {
        "title": '{"title": "有效标题"}',
        "defines": '{"defines": ["新定义或公式"]}',
        "summary": '{"summary": "有效摘要"}',
    }
    return (
        "REPAIR_MTU_METADATA\n"
        f"Repair only the `{field}` field for this one MTU JSON block.\n"
        "Do not return start_line, end_line, unit_kind, or any unchanged metadata.\n"
        "Do not create, delete, split, or merge units.\n"
        "For `defines`, use `defines`, never `keywords`; it must contain 1-4 new definitions, formulas, methods, models, or laws introduced by this MTU.\n"
        f"Return strict JSON only in this exact shape: {output_shapes[field]}\n\n"
        f"{json.dumps({
            'problem_type': 'invalid_metadata',
            'field': field,
            'range': line_range,
            'range_excerpt': excerpt,
            'current_metadata': _assignment_meta(invalid_unit.get('block')),
            'metadata_errors': [metadata_error],
        }, ensure_ascii=False, indent=2)}"
    )


def _mtu_units_repair_prompt_body(
    cleaned_markdown: str,
    *,
    problem: dict[str, Any],
    window_units: list[dict[str, Any]],
    window_start: int,
    window_end: int,
) -> str:
    lines = cleaned_markdown.splitlines()
    excerpt = "\n".join(
        f"{line_no}\t{lines[line_no - 1]}"
        for line_no in range(max(1, window_start), min(len(lines), window_end) + 1)
    )
    if problem["problem_type"] == "short_unit":
        instruction = (
            "The target concept MTU is 19 lines or shorter. It is invalid as a standalone MTU. "
            "Merge its lines into the previous or next related concept. Do not keep the target as a separate unit."
        )
    else:
        instruction = (
            "The target concept MTU has empty defines. If it truly introduces a new concept, formula, method, model, or law, "
            "return it as a concept with non-empty defines. If it does not define new content, merge its lines into the "
            "previous or next related concept."
        )
    return (
        "REPAIR_MTU_UNITS\n"
        f"{instruction}\n"
        "Return a strict JSON object with `units` only, using the normal MTU schema.\n"
        f"The returned units must cover exactly lines {window_start}-{window_end}, with no gaps, no overlaps, and no out-of-window lines.\n"
        f"Any final `concept` unit must cover at least {_MIN_FINAL_MTU_LINES} lines and must have at least one define.\n"
        "If a worked example is merged into a concept, the title must name only the concept; do not mention examples, exercises, applications, or cases in the title.\n"
        "Use `defines`, never `keywords`. Do not output prose or code fences.\n\n"
        f"{json.dumps({
            'problem': problem,
            'window_range': {'start_line': window_start, 'end_line': window_end},
            'window_units_metadata': [_assignment_meta(unit) for unit in window_units],
            'window_excerpt': excerpt,
        }, ensure_ascii=False, indent=2)}"
    )


def _mtu_duplicate_defines_repair_prompt_body(
    cleaned_markdown: str,
    *,
    problem: dict[str, Any],
    duplicate_units: list[dict[str, Any]],
) -> str:
    lines = cleaned_markdown.splitlines()
    excerpts: list[dict[str, Any]] = []
    for unit in duplicate_units:
        start = int(unit["start_line"])
        end = int(unit["end_line"])
        excerpt = "\n".join(
            f"{line_no}\t{lines[line_no - 1]}"
            for line_no in range(max(1, start), min(len(lines), end) + 1)
        )
        excerpts.append({"metadata": _assignment_meta(unit), "excerpt": excerpt})
    return (
        "REPAIR_MTU_DUPLICATE_DEFINES\n"
        "The provided concept MTUs were produced in the same MTU cut response and contain the same normalized define.\n"
        "Repair only the defines for these MTU JSON blocks so that no two provided concept units use the same normalized define.\n"
        "Return one item per provided unit, matching its original start_line and end_line exactly.\n"
        "Each returned item may contain only `start_line`, `end_line`, and `defines`; do not return title, summary, unit_kind, keywords, or any extra field.\n"
        "Do not create, delete, split, merge, or reorder units.\n"
        "If the repeated define is truly introduced by only one MTU, keep it there and replace/remove it from the other MTU with a specific define that is actually introduced by that MTU.\n"
        "Use `defines`, never `keywords`. Every concept unit must keep 1-4 defines.\n"
        "Return a strict JSON object with `units` only. Each unit may contain only `start_line`, `end_line`, and `defines`. Do not output prose or code fences.\n\n"
        f"{json.dumps({
            'problem': problem,
            'duplicate_units_metadata': [_assignment_meta(unit) for unit in duplicate_units],
            'duplicate_unit_excerpts': excerpts,
        }, ensure_ascii=False, indent=2)}"
    )


def _partition_mtu_plan(
    plan: dict[str, Any],
    *,
    line_count: int,
) -> dict[str, Any]:
    if "skipped_ranges" in plan:
        raise MtuCoverageError("`skipped_ranges` is not allowed in MTU cut plans")
    raw_units = plan.get("units") or []
    if not isinstance(raw_units, list):
        raise MtuCoverageError("`units` must be a list")

    valid_units: list[dict[str, Any]] = []
    invalid_units: list[dict[str, Any]] = []

    for index, raw in enumerate(raw_units, start=1):
        try:
            unit = _normalize_mtu_unit(raw, index)
        except MtuCoverageError as exc:
            problem = str(exc)
            placeholder = _metadata_placeholder_unit(raw, index)
            if placeholder is not None and _metadata_errors(problem):
                valid_units.append(placeholder)
            invalid_units.append(
                {
                    "index": index,
                    "block": raw,
                    "problem": problem,
                    "metadata_errors": _metadata_errors(problem),
                }
            )
            continue
        if unit["start_line"] < 1 or unit["end_line"] > line_count:
            invalid_units.append(
                {
                    "index": index,
                    "block": unit,
                    "problem": f"range {unit['start_line']}-{unit['end_line']} is out of bounds (1..{line_count})",
                }
            )
            continue
        if unit["end_line"] < unit["start_line"]:
            invalid_units.append(
                {
                    "index": index,
                    "block": unit,
                    "problem": f"range {unit['start_line']}-{unit['end_line']} is inverted",
                }
            )
            continue
        valid_units.append(unit)

    valid_units.sort(key=lambda unit: (unit["start_line"], unit["end_line"]))
    _refresh_mtu_partition_problems(
        partition := {"valid_units": valid_units, "invalid_units": invalid_units},
        line_count=line_count,
    )
    return partition


def _next_mtu_repair_stage(partition: dict[str, Any]) -> str | None:
    if _coverage_invalid_units(partition):
        return "coverage"
    if partition["missing_ranges"] or partition["overlap_ranges"]:
        return "coverage"
    if _first_semantic_problem(partition, {"short_unit"}) is not None:
        return "short_unit"
    if partition["invalid_units"]:
        return "metadata"
    if _first_semantic_problem(partition, {"empty_defines", "duplicate_defines"}) is not None:
        return "metadata"
    if partition["semantic_unit_problems"]:
        return "metadata"
    return None


def _coverage_invalid_units(partition: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in partition["invalid_units"] if not item.get("metadata_errors")]


def _first_semantic_problem(
    partition: dict[str, Any], problem_types: set[str] | None = None
) -> dict[str, Any] | None:
    for problem in partition["semantic_unit_problems"]:
        if problem_types is None or problem["problem_type"] in problem_types:
            return problem
    return None


def _merge_short_units_fallback(partition: dict[str, Any], *, line_count: int) -> bool:
    if partition["invalid_units"] or partition["missing_ranges"] or partition["overlap_ranges"]:
        return False
    problems = partition["semantic_unit_problems"]
    if not problems or any(problem.get("problem_type") != "short_unit" for problem in problems):
        return False

    merged = False
    while True:
        problem = _first_semantic_problem(partition, {"short_unit"})
        if problem is None:
            return merged
        if not _merge_one_short_unit(partition["valid_units"], problem):
            raise MtuCoverageError(f"short unit fallback failed for {problem}: no adjacent concept candidate")
        merged = True
        _refresh_mtu_partition_problems(partition, line_count=line_count)
        if partition["invalid_units"] or partition["missing_ranges"] or partition["overlap_ranges"]:
            raise MtuCoverageError(
                "short unit fallback failed after merge: "
                f"invalid_units={partition['invalid_units']}; "
                f"missing_ranges={partition['missing_ranges']}; "
                f"overlap_ranges={partition['overlap_ranges']}"
            )
        if any(problem.get("problem_type") != "short_unit" for problem in partition["semantic_unit_problems"]):
            raise MtuCoverageError(
                "short unit fallback failed after merge: "
                f"semantic_unit_problems={partition['semantic_unit_problems']}"
            )


def _merge_one_short_unit(units: list[dict[str, Any]], problem: dict[str, Any]) -> bool:
    target_index = _semantic_problem_unit_index(units, problem)
    if target_index is None:
        return False
    previous = _nearest_concept_before(units, target_index)
    next_unit = _nearest_concept_after(units, target_index)
    if previous is None and next_unit is None:
        return False

    target = units[target_index]
    if previous is None:
        assert next_unit is not None
        next_unit["start_line"] = min(int(next_unit["start_line"]), int(target["start_line"]))
    else:
        previous["end_line"] = max(int(previous["end_line"]), int(target["end_line"]))
    units.pop(target_index)
    return True


def _semantic_problem_unit_index(units: list[dict[str, Any]], problem: dict[str, Any]) -> int | None:
    problem_range = problem.get("range") or {}
    for index, unit in enumerate(units):
        if (
            int(unit["start_line"]) == int(problem_range.get("start_line", -1))
            and int(unit["end_line"]) == int(problem_range.get("end_line", -1))
            and unit.get("title") == problem.get("title")
        ):
            return index
    return None


def _nearest_concept_before(units: list[dict[str, Any]], target_index: int) -> dict[str, Any] | None:
    for unit in reversed(units[:target_index]):
        if unit.get("unit_kind") == "concept":
            return unit
    return None


def _nearest_concept_after(units: list[dict[str, Any]], target_index: int) -> dict[str, Any] | None:
    for unit in units[target_index + 1:]:
        if unit.get("unit_kind") == "concept":
            return unit
    return None


def _refresh_mtu_partition_problems(partition: dict[str, Any], *, line_count: int) -> None:
    units = [
        unit for unit in partition["valid_units"]
        if unit["start_line"] <= unit["end_line"]
    ]
    units.sort(key=lambda unit: (unit["start_line"], unit["end_line"]))
    partition["valid_units"] = units
    missing: list[dict[str, int]] = []
    overlaps: list[dict[str, Any]] = []
    expected = 1
    previous: dict[str, Any] | None = None
    for unit in units:
        start = int(unit["start_line"])
        end = int(unit["end_line"])
        if start > expected:
            missing.append({"start_line": expected, "end_line": start - 1})
        if previous is not None and start <= int(previous["end_line"]):
            overlaps.append(
                {
                    "range": {
                        "start_line": start,
                        "end_line": min(end, int(previous["end_line"])),
                    },
                    "previous": previous,
                    "next": unit,
                }
            )
        expected = max(expected, end + 1)
        previous = unit
    if expected <= line_count:
        missing.append({"start_line": expected, "end_line": line_count})
    partition["missing_ranges"] = missing
    partition["overlap_ranges"] = overlaps
    partition["semantic_unit_problems"] = _semantic_unit_problems(units)


def _semantic_unit_problems(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    problems: list[dict[str, Any]] = []
    for index, unit in enumerate(units, start=1):
        if unit.get("unit_kind") != "concept":
            continue
        line_count = int(unit["end_line"]) - int(unit["start_line"]) + 1
        if line_count < _MIN_FINAL_MTU_LINES:
            problems.append(
                {
                    "problem_type": "short_unit",
                    "index": index,
                    "title": unit["title"],
                    "range": {"start_line": unit["start_line"], "end_line": unit["end_line"]},
                    "line_count": line_count,
                    "minimum_lines": _MIN_FINAL_MTU_LINES,
                }
            )
            continue
        if not unit.get("defines"):
            problems.append(
                {
                    "problem_type": "empty_defines",
                    "index": index,
                    "title": unit["title"],
                    "range": {"start_line": unit["start_line"], "end_line": unit["end_line"]},
                }
            )
    if problems:
        return problems
    problems.extend(_duplicate_define_problems(units))
    return problems


def _duplicate_define_problems(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    define_index: dict[str, list[dict[str, Any]]] = {}
    define_labels: dict[str, list[str]] = {}
    for index, unit in enumerate(units, start=1):
        if unit.get("unit_kind") != "concept":
            continue
        unit_seen: set[str] = set()
        for define in unit.get("defines") or []:
            key = normalize_text_key(str(define))
            if not key or key in unit_seen:
                continue
            unit_seen.add(key)
            define_index.setdefault(key, []).append(
                {
                    "index": index,
                    "title": unit["title"],
                    "range": {"start_line": unit["start_line"], "end_line": unit["end_line"]},
                }
            )
            labels = define_labels.setdefault(key, [])
            text = str(define).strip()
            if text and text not in labels:
                labels.append(text)

    problems: list[dict[str, Any]] = []
    for key, entries in sorted(define_index.items()):
        if len(entries) <= 1:
            continue
        problems.append(
            {
                "problem_type": "duplicate_defines",
                "define_key": key,
                "defines": define_labels.get(key, []),
                "units": entries,
            }
        )
    return problems


def _semantic_repair_window(units: list[dict[str, Any]], problem: dict[str, Any]) -> list[dict[str, Any]]:
    problem_range = problem["range"]
    target_index = next(
        (
            index
            for index, unit in enumerate(units)
            if int(unit["start_line"]) == int(problem_range["start_line"])
            and int(unit["end_line"]) == int(problem_range["end_line"])
            and unit.get("title") == problem.get("title")
        ),
        None,
    )
    if target_index is None:
        return []
    start_index = max(0, target_index - 1)
    end_index = min(len(units), target_index + 2)
    return [dict(unit) for unit in units[start_index:end_index]]


def _duplicate_define_repair_units(units: list[dict[str, Any]], problem: dict[str, Any]) -> list[dict[str, Any]]:
    ranges = {
        (int(item["range"]["start_line"]), int(item["range"]["end_line"]))
        for item in problem.get("units", [])
    }
    return [
        dict(unit)
        for unit in units
        if (int(unit["start_line"]), int(unit["end_line"])) in ranges
    ]


def _normalize_repair_units(
    plan: dict[str, Any],
    *,
    line_count: int,
    window_start: int,
    window_end: int,
    problem: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if "skipped_ranges" in plan:
        raise MtuCoverageError("`skipped_ranges` is not allowed in MTU unit repairs")
    raw_units = plan.get("units") or []
    if not isinstance(raw_units, list) or not raw_units:
        raise MtuCoverageError("unit repair must return a non-empty `units` list")
    units = [_normalize_mtu_unit(raw, index) for index, raw in enumerate(raw_units, start=1)]
    units.sort(key=lambda unit: (unit["start_line"], unit["end_line"]))
    expected = window_start
    for unit in units:
        start = int(unit["start_line"])
        end = int(unit["end_line"])
        if start < window_start or end > window_end:
            raise MtuCoverageError(
                f"unit repair range {start}-{end} is outside repair window {window_start}-{window_end}"
            )
        if end < start:
            raise MtuCoverageError(f"unit repair range {start}-{end} is inverted")
        if start != expected:
            if start < expected:
                raise MtuCoverageError(f"unit repair overlap at line {start} (expected next line {expected})")
            raise MtuCoverageError(f"unit repair gap: lines {expected}-{start - 1} uncovered")
        if unit["unit_kind"] == "concept":
            if not unit.get("defines") and _unit_still_matches_semantic_problem(unit, problem):
                raise MtuCoverageError(f"unit repair concept `{unit['title']}` must contain at least one define")
            line_span = end - start + 1
            if line_span < _MIN_FINAL_MTU_LINES and _unit_still_matches_semantic_problem(unit, problem):
                raise MtuCoverageError(
                    f"unit repair concept `{unit['title']}` must cover at least {_MIN_FINAL_MTU_LINES} lines; got {line_span}"
                )
        expected = end + 1
    if expected != window_end + 1:
        raise MtuCoverageError(f"unit repair gap: lines {expected}-{window_end} uncovered")
    if window_start < 1 or window_end > line_count:
        raise MtuCoverageError(f"unit repair window {window_start}-{window_end} is out of bounds (1..{line_count})")
    return units


def _unit_still_matches_semantic_problem(unit: dict[str, Any], problem: dict[str, Any] | None) -> bool:
    if not problem:
        return True
    problem_range = problem.get("range") or {}
    return (
        int(unit["start_line"]) == int(problem_range.get("start_line", -1))
        and int(unit["end_line"]) == int(problem_range.get("end_line", -1))
    )


def _unit_range_within_window(raw: Any, window_start: int, window_end: int) -> bool:
    line_range = _unit_line_range(raw)
    if line_range is None:
        return False
    return int(line_range["start_line"]) >= window_start and int(line_range["end_line"]) <= window_end


def _normalize_duplicate_define_repair_units(
    plan: dict[str, Any],
    expected_units: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if "skipped_ranges" in plan:
        raise MtuCoverageError("`skipped_ranges` is not allowed in duplicate define repairs")
    raw_units = plan.get("units") or []
    if not isinstance(raw_units, list) or not raw_units:
        raise MtuCoverageError("duplicate define repair must return a non-empty `units` list")
    expected_by_range = {
        (int(unit["start_line"]), int(unit["end_line"])): dict(unit)
        for unit in expected_units
    }
    units: list[dict[str, Any]] = []
    allowed_keys = {"start_line", "end_line", "defines"}
    for index, raw in enumerate(raw_units, start=1):
        if not isinstance(raw, dict):
            raise MtuCoverageError(f"duplicate define repair unit {index} must be an object")
        extra = set(raw) - allowed_keys
        if extra:
            raise MtuCoverageError(
                f"duplicate define repair unit {index} may only contain start_line, end_line, defines; got {sorted(extra)}"
            )
        start = _int_field(raw.get("start_line"), f"duplicate define repair unit {index} start_line")
        end = _int_field(raw.get("end_line"), f"duplicate define repair unit {index} end_line")
        original = expected_by_range.get((start, end))
        if original is None:
            units.append({"start_line": start, "end_line": end, "defines": raw.get("defines")})
            continue
        repaired = dict(original)
        repaired["defines"] = raw.get("defines")
        units.append(_normalize_mtu_unit(repaired, index))
    expected_ranges = {
        (int(unit["start_line"]), int(unit["end_line"]))
        for unit in expected_units
    }
    repaired_ranges = [
        (int(unit["start_line"]), int(unit["end_line"]))
        for unit in units
    ]
    if set(repaired_ranges) != expected_ranges or len(repaired_ranges) != len(expected_ranges):
        raise MtuCoverageError(
            "duplicate define repair must return exactly the original duplicate MTU ranges "
            f"{sorted(expected_ranges)}; got {sorted(repaired_ranges)}"
        )
    for unit in units:
        if unit.get("unit_kind") == "concept":
            if not unit.get("defines"):
                raise MtuCoverageError(
                    f"duplicate define repair concept `{unit['title']}` must contain at least one define"
                )
            if len(unit.get("defines") or []) > 4:
                raise MtuCoverageError(
                    f"duplicate define repair concept `{unit['title']}` has too many defines"
                )
    duplicate_problems = _duplicate_define_problems(units)
    if duplicate_problems:
        raise MtuCoverageError(f"duplicate define repair still contains duplicate defines: {duplicate_problems[0]}")
    return units


def _apply_metadata_field_repair(raw: Any, repair: dict[str, Any], field: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise MtuCoverageError("metadata repair original unit must be an object")
    if set(repair) != {field}:
        raise MtuCoverageError(f"metadata repair for {field} must return only `{field}`")
    block = dict(raw)
    if field == "defines":
        block.pop("keywords", None)
    block[field] = repair[field]
    return block


def _metadata_placeholder_unit(raw: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    try:
        start = _int_field(raw.get("start_line"), f"unit {index} start_line")
        end = _int_field(raw.get("end_line"), f"unit {index} end_line")
    except MtuCoverageError:
        return None
    if end < start:
        return None
    return {
        "start_line": start,
        "end_line": end,
        "title": str(raw.get("title") or f"unit {index}").strip(),
        "defines": list(raw.get("defines") or raw.get("keywords") or []),
        "summary": str(raw.get("summary") or "").strip(),
        "unit_kind": str(raw.get("unit_kind") or "concept").strip() or "concept",
        "__invalid_index": index,
    }


def _replace_unit_by_original_range(
    units: list[dict[str, Any]], original_range: dict[str, int], replacement: dict[str, Any]
) -> None:
    target = (int(original_range["start_line"]), int(original_range["end_line"]))
    for index, unit in enumerate(units):
        if (int(unit["start_line"]), int(unit["end_line"])) == target:
            units[index] = replacement
            return
    units.append(replacement)


def _sync_invalid_unit_ranges(partition: dict[str, Any]) -> None:
    units_by_index = {
        int(unit["__invalid_index"]): unit
        for unit in partition["valid_units"]
        if "__invalid_index" in unit
    }
    for item in partition["invalid_units"]:
        unit = units_by_index.get(int(item.get("index", -1)))
        block = item.get("block")
        if unit is None or not isinstance(block, dict):
            continue
        block["start_line"] = unit["start_line"]
        block["end_line"] = unit["end_line"]


def _int_field(value: Any, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise MtuCoverageError(f"{label} must be an integer") from exc


def _assignment_neighbors(
    units: list[dict[str, Any]], problem: dict[str, Any]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if problem["problem_type"] == "overlap":
        return problem.get("previous"), problem.get("next")
    line_range = problem["range"]
    previous = max(
        (unit for unit in units if int(unit["end_line"]) < line_range["start_line"]),
        key=lambda unit: (int(unit["end_line"]), unit["title"]),
        default=None,
    )
    next_unit = min(
        (unit for unit in units if int(unit["start_line"]) > line_range["end_line"]),
        key=lambda unit: (int(unit["start_line"]), unit["title"]),
        default=None,
    )
    return previous, next_unit


def _apply_assignment_decision(
    units: list[dict[str, Any]], problem: dict[str, Any], decision: dict[str, Any]
) -> None:
    previous, next_unit = _assignment_neighbors(units, problem)
    candidates = [unit for unit in (previous, next_unit) if unit is not None]
    title = str(decision.get("mtu_title") or "").strip()
    matches = [unit for unit in candidates if unit["title"] == title]
    if len(matches) != 1:
        raise MtuCoverageError(f"Assignment decision must select one candidate title, got: {title}")
    target = matches[0]
    line_range = problem["range"]
    if problem["problem_type"] == "missing_range":
        if target is previous:
            target["end_line"] = line_range["end_line"]
        else:
            target["start_line"] = line_range["start_line"]
        return
    if target is previous:
        if next_unit is not None:
            next_unit["start_line"] = line_range["end_line"] + 1
    else:
        if previous is not None:
            previous["end_line"] = line_range["start_line"] - 1


def _assignment_meta(unit: dict[str, Any] | None) -> dict[str, Any] | None:
    if unit is None:
        return None
    return {
        "title": unit.get("title", ""),
        "start_line": unit.get("start_line"),
        "end_line": unit.get("end_line"),
        "defines": unit.get("defines", unit.get("keywords", [])),
        "summary": unit.get("summary", ""),
        "unit_kind": unit.get("unit_kind", ""),
    }


def _invalid_units_are_metadata_only(items: list[dict[str, Any]]) -> bool:
    return bool(items) and all(item.get("metadata_errors") for item in items)


def _metadata_errors(problem: str) -> list[dict[str, str]]:
    field_map = {
        "title": "title",
        "defines": "defines",
        "keywords": "defines",
        "summary": "summary",
    }
    errors: list[dict[str, str]] = []
    for needle, field in field_map.items():
        if needle in problem and not any(item["field"] == field for item in errors):
            errors.append({"field": field, "problem": problem})
    return errors


def _unit_line_range(raw: Any) -> dict[str, int] | None:
    if not isinstance(raw, dict):
        return None
    try:
        return {"start_line": int(raw.get("start_line")), "end_line": int(raw.get("end_line"))}
    except (TypeError, ValueError):
        return None


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
    if partition["overlap_ranges"]:
        parts.append(f"overlap_ranges={partition['overlap_ranges']}")
    if partition["missing_ranges"]:
        parts.append(f"missing_ranges={partition['missing_ranges']}")
    if partition.get("semantic_unit_problems"):
        problems = [str(item) for item in partition["semantic_unit_problems"][:3]]
        parts.append(f"semantic_unit_problems={len(partition['semantic_unit_problems'])}: {'; '.join(problems)}")
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
            if locked_ranges is not None and problem.startswith("overlap:"):
                for start, end in _uncovered_segments(
                    item["start_line"],
                    item["end_line"],
                    occupied,
                    line_count,
                ):
                    segment = {**item, "start_line": start, "end_line": end}
                    occupied.append((start, end))
                    valid.append(segment)
                continue
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
