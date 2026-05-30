"""Chunk-level source inventory for KnowledgeGroup planning.

The source inventory is a compact map from source RAG chunks to concepts,
methods, misconceptions, prerequisites, and short summaries. It lets the
planner and AI reason about KnowledgeGroup relationships without rereading every full chunk.
"""

from __future__ import annotations

import json
import re
import hashlib
from collections import Counter
from pathlib import Path
from typing import Any, Protocol

from tree.curriculum.ledger import load_ledger
from tree.io import paths

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*([^*\n]{1,40})\*\*")
_CODE_RE = re.compile(r"`([^`\n]{1,40})`")
_LATIN_TOKEN_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\(\))?\b")
_CJK_TERM_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9_`()+\-]{2,24}")
_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;]\s*|\n+")
_TERM_SPLIT_RE = re.compile(r"[，,、/：:（）()\[\]【】\s]+")
_DEFINITION_HINTS = ("定义", "概念", "是指", "称为", "叫做", "表示", "用于")
_METHOD_HINTS = ("方法", "步骤", "使用", "语法", "操作", "输入", "输出", "转换", "判断", "调试")
_MISCONCEPTION_HINTS = ("误区", "错误", "混淆", "不是", "不能", "不要", "注意")
_PREREQUISITE_HINTS = ("先修", "前置", "基础", "依赖", "需要掌握")
_CONCEPT_HINTS = (
    "模型",
    "模式",
    "变量",
    "对象",
    "赋值",
    "类型",
    "字符串",
    "函数",
    "表达式",
    "运算",
    "输入",
    "输出",
    "程序",
    "算法",
    "环境",
    "语法",
    "控制",
    "循环",
    "分支",
    "列表",
    "字典",
    "文件",
    "异常",
    "调试",
)
_LEADING_VERB_RE = re.compile(r"^(?:理解|掌握|了解|熟悉|认识|体验|使用|学会|能够|进行|配置|安装)")
_STOPWORDS = {
    "教学目标",
    "教学内容",
    "核心内容",
    "学习目标",
    "学习目标与先修前置",
    "例题",
    "常见误区",
    "自测题",
    "参考答案",
    "答案",
    "解析",
    "代码",
    "输出",
    "输入",
    "Input",
    "Processing",
    "Output",
    "方法",
    "步骤",
    "注意",
    "练习",
    "跟着练",
    "思考",
    "进一步思考",
    "例如",
    "如果",
    "因为",
    "所以",
    "可以",
    "需要",
    "应该",
    "一个",
    "一种",
    "以及",
    "Code",
    "VS",
    "View",
    "Terminal",
    "Shift",
    "Ctrl",
    "Add",
    "PATH",
    "Image",
    "List",
    "Hello",
}
_LATIN_CONCEPT_ALLOWLIST = {"AI", "IPO", "Python"}


class SourceChunkAnalyzer(Protocol):
    async def analyze_source_chunk(self, chunk: dict[str, Any]) -> dict[str, Any]:
        """Return curriculum inventory fields for one source chunk."""

    async def analyze_inventory_chunk(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return sequential inventory grouping fields for one source chunk."""


def load_inventory(root: Path) -> dict[str, Any]:
    path = paths.source_inventory_path(root)
    if not path.exists():
        return {"version": 1, "chunks": [], "collections": []}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": 1, "chunks": [], "collections": []}
    if not isinstance(loaded, dict):
        return {"version": 1, "chunks": [], "collections": []}
    loaded.setdefault("version", 1)
    loaded.setdefault("chunks", [])
    loaded.setdefault("collections", [])
    return loaded


def save_inventory(root: Path, inventory: dict[str, Any]) -> None:
    path = paths.source_inventory_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(path)


def rebuild_source_inventory(root: Path, source_chunks: list[dict]) -> dict[str, Any]:
    """Build and persist source inventory from source RAG chunk hits."""
    records = [_chunk_record(hit) for hit in source_chunks]
    records = sorted(
        records,
        key=lambda item: (
            item.get("source_collection", ""),
            item.get("path", ""),
            item.get("chunk_index", 0),
        ),
    )
    groups = [_group_from_records([record]) for record in records]
    inventory = {
        "version": 2,
        "analysis_mode": "rules_fallback",
        "chunks": records,
        "knowledge_groups": groups,
        "collections": _collection_summaries(groups),
    }
    save_inventory(root, inventory)
    return inventory


async def rebuild_source_inventory_with_ai(
    root: Path,
    source_chunks: list[dict],
    analyzer: SourceChunkAnalyzer | None,
    concurrency: int = 4,
) -> dict[str, Any]:
    """Build source inventory, using AI semantic analysis when available.

    Existing chunk analyses are reused by text_hash so normal engine startup does
    not repeatedly call the model.
    """
    if analyzer is None:
        return rebuild_source_inventory(root, source_chunks)

    _ = concurrency  # Sequential grouping intentionally preserves file order.
    sorted_hits = sorted(source_chunks, key=_hit_sort_key)
    cached_records = _cached_ai_records_by_hash(load_inventory(root))
    records = []
    groups = []
    active_group: dict[str, Any] | None = None
    active_file_key: tuple[str, str, str] | None = None

    for hit in sorted_hits:
        base = _chunk_record(hit)
        current_file_key = (
            str(base.get("source_collection") or ""),
            str(base.get("doc_id") or ""),
            str(base.get("path") or base.get("filename") or ""),
        )
        if active_file_key is not None and current_file_key != active_file_key:
            if active_group is not None:
                groups.append(active_group)
            active_group = None
        active_file_key = current_file_key

        cached = cached_records.get(str(base.get("text_hash") or ""))
        if cached is not None:
            record = _merge_cached_ai_analysis(base, cached)
            ai = record
        else:
            try:
                ai = await _analyze_inventory_chunk(analyzer, hit, base, active_group)
                record = _merge_ai_analysis(base, ai)
            except Exception:
                ai = {}
                record = base

        records.append(record)
        should_merge = bool(ai.get("merge_with_previous")) and active_group is not None
        if should_merge:
            active_group = _merge_group_record(active_group, record)
        else:
            if active_group is not None:
                groups.append(active_group)
            active_group = _group_from_records([record])

    if active_group is not None:
        groups.append(active_group)
    records = sorted(
        records,
        key=lambda item: (
            item.get("source_collection", ""),
            item.get("path", ""),
            item.get("chunk_index", 0),
        ),
    )
    inventory = {
        "version": 2,
        "analysis_mode": "ai_sequential_groups",
        "chunks": records,
        "knowledge_groups": groups,
        "collections": _collection_summaries(groups),
    }
    save_inventory(root, inventory)
    return inventory


def _cached_ai_records_by_hash(inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for item in inventory.get("chunks", []) or []:
        if not isinstance(item, dict) or item.get("analysis_mode") != "ai":
            continue
        text_hash = str(item.get("text_hash") or "")
        if text_hash and text_hash not in records:
            records[text_hash] = item
    return records


def build_inventory_context(
    root: Path,
    inventory: dict[str, Any],
    completed_collections: set[str] | None = None,
    limit_collections: int = 12,
) -> str:
    """Format source inventory as a legacy/debug KnowledgeGroup context."""
    completed_collections = completed_collections or set()
    ledger = load_ledger(root)
    finished_terms = _finished_terms(ledger)
    lines = [
        "## Source Inventory",
        "Use this chunk-level inventory to inspect KnowledgeGroups by semantic clusters, not upload order.",
        "A KnowledgeGroup may reference multiple related collections; source_collection remains only a provenance field.",
        "",
    ]

    collections = [
        item
        for item in inventory.get("collections", [])
        if isinstance(item, dict)
    ]
    if not collections:
        lines.append("(no source inventory available)")
        return "\n".join(lines)

    for collection in collections[:limit_collections]:
        cid = str(collection.get("source_collection", "unknown"))
        concepts = [str(item) for item in collection.get("core_concepts", [])]
        overlap = _term_overlap(concepts, finished_terms)
        relation = "completed" if cid in completed_collections else _coverage_label(overlap, concepts)
        related_chunks = _chunk_refs(collection.get("representative_chunks", []), limit=6)
        related_collections = _related_collection_text(collection.get("related_collections", []))
        sections = ", ".join(collection.get("section_ids", [])[:8])
        paths_text = ", ".join(collection.get("paths", [])[:4])
        lines.extend(
            [
                f"### Collection: {cid}",
                f"- status_hint: {relation}",
                f"- docs: {collection.get('doc_count', 0)}; chunks: {collection.get('chunk_count', 0)}",
                f"- paths: {paths_text or 'n/a'}",
                f"- sections: {sections or 'n/a'}",
                f"- core_concepts: {', '.join(concepts[:18]) or 'n/a'}",
                f"- related_collections: {related_collections or 'n/a'}",
                f"- representative_chunks: {related_chunks or 'n/a'}",
                "",
            ]
        )
    if len(collections) > limit_collections:
        lines.append(f"... {len(collections) - limit_collections} more collections omitted")
    return "\n".join(lines).strip()


def chapter_candidate_context(
    root: Path,
    inventory: dict[str, Any],
    query_concepts: list[str],
    top_n: int = 10,
) -> str:
    """Find source chunks hit by core concepts and format relationship evidence."""
    hits = hit_chunks_by_concepts(inventory, query_concepts, top_n=top_n)
    lines = [
        "## Chunk Hits From Candidate Core Concepts",
        f"query_concepts: {', '.join(query_concepts) or 'n/a'}",
    ]
    if not hits:
        lines.append("- (none)")
        return "\n".join(lines)
    for hit in hits:
        lines.append(
            f"- score={hit['score']:.2f} | {hit['chunk_ref']} | collection={hit['source_collection']} | "
            f"concepts={', '.join(hit['matched_concepts'])} | {hit['summary']}"
        )
    ledger = load_ledger(root)
    finished_terms = _finished_terms(ledger)
    overlap = _term_overlap(query_concepts, finished_terms)
    if overlap:
        lines.append(f"finished_overlap_terms: {', '.join(overlap[:16])}")
    return "\n".join(lines)


def hit_chunks_by_concepts(
    inventory: dict[str, Any],
    concepts: list[str],
    top_n: int = 10,
) -> list[dict[str, Any]]:
    query_terms = _term_set(" ".join(concepts))
    if not query_terms:
        return []
    scored = []
    for record in inventory.get("chunks", []):
        if not isinstance(record, dict):
            continue
        terms = _record_terms(record)
        overlap = query_terms & terms
        if not overlap:
            continue
        score = (len(overlap) / len(query_terms)) * 0.7 + (len(overlap) / len(terms)) * 0.3
        scored.append((score, sorted(overlap), record))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "score": round(score, 4),
            "chunk_ref": _chunk_ref(record),
            "source_collection": record.get("source_collection", ""),
            "path": record.get("path", ""),
            "matched_concepts": overlap[:12],
            "summary": record.get("summary", ""),
        }
        for score, overlap, record in scored[:top_n]
    ]


def _chunk_record(hit: dict) -> dict[str, Any]:
    metadata = hit.get("metadata") or {}
    text = str(hit.get("text") or "")
    source_collection = str(metadata.get("source_collection") or metadata.get("chapter") or "")
    section_id = str(metadata.get("section_id") or "")
    chunk_index = _int(metadata.get("chunk_index"))
    metadata_concepts = [
        str(item)
        for item in metadata.get("weak_concepts") or metadata.get("concepts") or []
    ]
    raw_formulas = [
        str(item)
        for item in metadata.get("raw_formulas") or metadata.get("formulas") or []
    ][:10]
    headings = _headings(text)
    section_terms = _split_section_id(section_id)
    supported_section_terms = _supported_section_terms(section_terms, text, metadata_concepts, headings)
    concepts = _extract_core_concepts(text, section_id, metadata_concepts, headings)
    return {
        "chunk_id": str(hit.get("chunk_id") or metadata.get("chunk_id") or ""),
        "text_hash": _text_hash(text),
        "chunk_index": chunk_index,
        "chunk_ref": _chunk_ref_from_parts(source_collection, metadata.get("filename"), chunk_index),
        "doc_id": str(metadata.get("doc_id") or ""),
        "source_collection": source_collection,
        "path": str(metadata.get("source_path") or metadata.get("path") or metadata.get("filename") or ""),
        "filename": str(metadata.get("filename") or ""),
        "section_id": section_id,
        "heading_path": _string_list(metadata.get("heading_path")) or headings,
        "token_estimate": _int(metadata.get("token_estimate")) or _estimate_tokens(text),
        "weak_concepts": metadata_concepts,
        "low_confidence_section_terms": [
            term for term in section_terms if term not in set(supported_section_terms)
        ],
        "chunk_type": str(metadata.get("chunk_type") or ""),
        "core_concepts": concepts,
        "methods": _extract_tagged_sentences(text, _METHOD_HINTS)[:8],
        "misconceptions": _extract_tagged_sentences(text, _MISCONCEPTION_HINTS)[:8],
        "prerequisites": _extract_prerequisites(text)[:8],
        "raw_formulas": raw_formulas,
        "formula_signatures": _string_list(metadata.get("formula_signatures")),
        "formulas": raw_formulas,
        "summary": _summary(text),
        "analysis_mode": "rules",
    }


def _chunk_for_ai(hit: dict) -> dict[str, Any]:
    metadata = hit.get("metadata") or {}
    return {
        "chunk_id": str(hit.get("chunk_id") or metadata.get("chunk_id") or ""),
        "text": str(hit.get("text") or ""),
        "metadata": metadata,
    }


async def _analyze_inventory_chunk(
    analyzer: SourceChunkAnalyzer,
    hit: dict[str, Any],
    base: dict[str, Any],
    active_group: dict[str, Any] | None,
) -> dict[str, Any]:
    method = getattr(analyzer, "analyze_inventory_chunk", None)
    if callable(method):
        payload = {
            "chunk": _chunk_for_ai(hit),
            "active_group": _active_group_for_ai(active_group),
            "pair_metrics": _group_chunk_metrics(active_group, base),
            "size_guidance": {
                "preferred_output_lines": "300-1000",
                "hard_cap": None,
            },
        }
        return await method(payload)
    ai = await analyzer.analyze_source_chunk(_chunk_for_ai(hit))
    if isinstance(ai, dict):
        ai = dict(ai)
        ai.setdefault("merge_with_previous", False)
    return ai


def _active_group_for_ai(group: dict[str, Any] | None) -> dict[str, Any] | None:
    if not group:
        return None
    return {
        "group_id": group.get("group_id"),
        "title_hint": group.get("title_hint"),
        "source_chunks": group.get("source_chunks", [])[:8],
        "source_paths": group.get("source_paths", [])[:4],
        "chunk_range": group.get("chunk_range", {}),
        "core_concepts": group.get("core_concepts", [])[:12],
        "methods": group.get("methods", [])[:8],
        "prerequisites": group.get("prerequisites", [])[:8],
        "formula_roles": group.get("formula_roles", [])[:6],
        "teaching_role": group.get("teaching_role", ""),
        "completeness": group.get("completeness", ""),
        "summary": group.get("summary", ""),
        "length_stats": group.get("length_stats", {}),
    }


def _group_chunk_metrics(group: dict[str, Any] | None, chunk: dict[str, Any]) -> dict[str, Any]:
    if not group:
        return {
            "heading_section_continuity": 0.0,
            "embedding_similarity": None,
            "concept_overlap": 0.0,
            "formula_overlap": 0.0,
            "source_path_continuity": 0.0,
            "token_length_ratio": 1.0,
            "chunk_index_distance": None,
            "overall_similarity": 0.0,
        }
    group_terms = _term_set(" ".join([
        *group.get("core_concepts", []),
        *group.get("weak_concepts", []),
    ]))
    chunk_terms = _term_set(" ".join([
        *chunk.get("core_concepts", []),
        *chunk.get("weak_concepts", []),
    ]))
    group_formulas = set(group.get("formula_signatures", []))
    chunk_formulas = set(chunk.get("formula_signatures", []))
    same_source = str(chunk.get("path") or "") in set(group.get("source_paths", []))
    group_range = group.get("chunk_range", {}) if isinstance(group.get("chunk_range"), dict) else {}
    distance = abs(_int(chunk.get("chunk_index")) - _int(group_range.get("end")))
    section_continuity = 1.0 if str(chunk.get("section_id") or "") in set(group.get("section_ids", [])) else 0.0
    concept_overlap = _overlap_score(group_terms, chunk_terms)
    formula_overlap = _overlap_score(group_formulas, chunk_formulas)
    token_total = max(1, int(group.get("length_stats", {}).get("token_estimate") or 0))
    token_ratio = min(token_total, max(1, int(chunk.get("token_estimate") or 0))) / max(
        token_total,
        max(1, int(chunk.get("token_estimate") or 0)),
    )
    overall = (
        section_continuity * 0.22
        + concept_overlap * 0.34
        + formula_overlap * 0.24
        + (1.0 if same_source else 0.0) * 0.12
        + (1.0 if distance <= 1 else 0.0) * 0.08
    )
    return {
        "heading_section_continuity": round(section_continuity, 4),
        "embedding_similarity": None,
        "concept_overlap": round(concept_overlap, 4),
        "formula_overlap": round(formula_overlap, 4),
        "source_path_continuity": 1.0 if same_source else 0.0,
        "token_length_ratio": round(token_ratio, 4),
        "chunk_index_distance": distance,
        "overall_similarity": round(overall, 4),
    }


def _group_from_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    first = records[0] if records else {}
    group = {
        "group_id": _stable_group_id(records),
        "source_collection": str(first.get("source_collection") or ""),
        "source_chunks": [_chunk_ref(record) for record in records],
        "source_paths": _unique(record.get("path", "") for record in records),
        "section_ids": _unique(record.get("section_id", "") for record in records),
        "heading_path": _ranked_terms((record.get("heading_path", []) for record in records), 18),
        "chunk_range": {
            "start": min((_int(record.get("chunk_index")) for record in records), default=0),
            "end": max((_int(record.get("chunk_index")) for record in records), default=0),
        },
        "title_hint": str(first.get("title_hint") or ""),
        "core_concepts": _ranked_terms((record.get("core_concepts", []) for record in records), 18),
        "weak_concepts": _ranked_terms((record.get("weak_concepts", []) for record in records), 18),
        "methods": _ranked_terms((record.get("methods", []) for record in records), 12),
        "misconceptions": _ranked_terms((record.get("misconceptions", []) for record in records), 12),
        "prerequisites": _ranked_terms((record.get("prerequisites", []) for record in records), 12),
        "low_confidence_section_terms": _ranked_terms(
            (record.get("low_confidence_section_terms", []) for record in records),
            12,
        ),
        "raw_formulas": _ranked_terms((record.get("raw_formulas", []) for record in records), 12),
        "formula_signatures": _ranked_terms((record.get("formula_signatures", []) for record in records), 12),
        "formula_roles": _merge_formula_roles(records),
        "source_type": str(first.get("source_type") or ""),
        "teaching_role": str(first.get("teaching_role") or ""),
        "completeness": str(first.get("completeness") or ""),
        "evidence_spans": _ranked_terms((record.get("evidence_spans", []) for record in records), 12),
        "summary": str(first.get("summary") or ""),
        "representative_chunks": [_representative_chunk(record) for record in records[:8]],
        "length_stats": _length_stats(records),
        "analysis_mode": "ai_sequential_group" if any(record.get("analysis_mode") == "ai" for record in records) else "rules_fallback",
    }
    if not group["title_hint"]:
        group["title_hint"] = (group["core_concepts"] or group["weak_concepts"] or [""])[0]
    return group


def _merge_group_record(group: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    records = [
        *[
            {
                "chunk_ref": chunk.get("chunk_ref"),
                "source_collection": group.get("source_collection"),
                "path": chunk.get("path") or (group.get("source_paths") or [""])[0],
                "section_id": chunk.get("section_id"),
                "chunk_index": chunk.get("chunk_index", 0),
                "core_concepts": chunk.get("core_concepts", []),
                "weak_concepts": chunk.get("weak_concepts", []),
                "prerequisites": chunk.get("prerequisites", []),
                "methods": chunk.get("methods", []),
                "raw_formulas": chunk.get("raw_formulas", []),
                "formula_signatures": chunk.get("formula_signatures", []),
                "low_confidence_section_terms": chunk.get("low_confidence_section_terms", []),
                "heading_path": chunk.get("heading_path", []),
                "summary": chunk.get("summary", ""),
                "analysis_mode": group.get("analysis_mode"),
            }
            for chunk in group.get("representative_chunks", [])
            if isinstance(chunk, dict)
        ],
        record,
    ]
    merged = _group_from_records(records)
    merged["group_id"] = group.get("group_id") or merged["group_id"]
    for key in ("title_hint", "source_type", "teaching_role", "completeness", "summary"):
        if record.get(key):
            merged[key] = record[key]
    merged["source_chunks"] = _unique([*group.get("source_chunks", []), _chunk_ref(record)])
    merged["source_paths"] = _unique([*group.get("source_paths", []), record.get("path", "")])
    merged["section_ids"] = _unique([*group.get("section_ids", []), record.get("section_id", "")])
    merged["heading_path"] = _unique([*group.get("heading_path", []), *record.get("heading_path", [])])
    merged["low_confidence_section_terms"] = _unique(
        [
            *group.get("low_confidence_section_terms", []),
            *record.get("low_confidence_section_terms", []),
        ]
    )
    merged["representative_chunks"] = [
        *group.get("representative_chunks", [])[:7],
        _representative_chunk(record),
    ][:8]
    merged["chunk_range"] = {
        "start": min(_int(group.get("chunk_range", {}).get("start")), _int(record.get("chunk_index"))),
        "end": max(_int(group.get("chunk_range", {}).get("end")), _int(record.get("chunk_index"))),
    }
    merged["length_stats"] = {
        "token_estimate": int(group.get("length_stats", {}).get("token_estimate") or 0)
        + int(record.get("token_estimate") or 0),
        "chunk_count": len(merged["source_chunks"]),
        "estimated_output_lines": int(group.get("length_stats", {}).get("estimated_output_lines") or 0)
        + _estimated_output_lines_for_records([record]),
    }
    return merged


def _representative_chunk(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_ref": record.get("chunk_ref") or _chunk_ref(record),
        "path": record.get("path", ""),
        "section_id": record.get("section_id", ""),
        "chunk_index": _int(record.get("chunk_index")),
        "heading_path": record.get("heading_path", [])[:8],
        "core_concepts": record.get("core_concepts", [])[:8],
        "weak_concepts": record.get("weak_concepts", [])[:8],
        "prerequisites": record.get("prerequisites", [])[:8],
        "raw_formulas": record.get("raw_formulas", [])[:8],
        "formula_signatures": record.get("formula_signatures", [])[:8],
        "low_confidence_section_terms": record.get("low_confidence_section_terms", [])[:8],
        "summary": record.get("summary", ""),
    }


def _length_stats(records: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "token_estimate": sum(_int(record.get("token_estimate")) for record in records),
        "chunk_count": len(records),
        "estimated_output_lines": _estimated_output_lines_for_records(records),
    }


def _estimated_output_lines_for_records(records: list[dict[str, Any]]) -> int:
    concepts = _ranked_terms((record.get("core_concepts", []) for record in records), 18)
    methods = _ranked_terms((record.get("methods", []) for record in records), 12)
    formulas = _ranked_terms((record.get("raw_formulas", []) for record in records), 12)
    return int(
        130
        + len(records) * 45
        + min(len(concepts), 12) * 20
        + min(len(methods), 8) * 15
        + min(len(formulas), 8) * 10
    )


def _merge_formula_roles(records: list[dict[str, Any]]) -> list[Any]:
    roles = []
    for record in records:
        for role in record.get("formula_roles", []) or []:
            if role not in roles:
                roles.append(role)
    return roles[:12]


def _stable_group_id(records: list[dict[str, Any]]) -> str:
    basis = "\n".join(_chunk_ref(record) for record in records) or "empty"
    return f"kg:{hashlib.sha1(basis.encode('utf-8')).hexdigest()[:12]}"


def _hit_sort_key(hit: dict[str, Any]) -> tuple[Any, ...]:
    metadata = hit.get("metadata") or {}
    return (
        _natural_key(str(metadata.get("source_collection") or metadata.get("chapter") or "")),
        str(metadata.get("source_path") or metadata.get("path") or metadata.get("filename") or ""),
        _int(metadata.get("chunk_index")),
        str(hit.get("chunk_id") or metadata.get("chunk_id") or ""),
    )


def _merge_ai_analysis(base: dict[str, Any], ai: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key in ("core_concepts", "methods", "misconceptions", "prerequisites"):
        values = ai.get(key)
        if isinstance(values, list):
            cleaned = _unique(_clean_term(item) for item in values if _is_good_term(item))
            if key == "core_concepts":
                cleaned = _filter_low_confidence_section_terms(
                    cleaned,
                    base.get("low_confidence_section_terms", []),
                    str(ai.get("summary") or ""),
                )
            merged[key] = cleaned[:18 if key == "core_concepts" else 10]
    for key in ("source_type", "teaching_role", "summary", "title_hint", "completeness"):
        value = ai.get(key)
        if isinstance(value, str) and value.strip():
            merged[key] = _clean_sentence(value) if key == "summary" else value.strip()[:80]
    for key in ("formula_roles", "evidence_spans"):
        value = ai.get(key)
        if isinstance(value, list):
            merged[key] = value[:12]
    for key in ("merge_with_previous", "is_complete_knowledge_point"):
        if key in ai:
            merged[key] = bool(ai.get(key))
    merged["analysis_mode"] = "ai"
    return merged


def _merge_cached_ai_analysis(base: dict[str, Any], cached: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key in (
        "core_concepts",
        "methods",
        "misconceptions",
        "prerequisites",
        "formula_roles",
        "evidence_spans",
    ):
        value = cached.get(key)
        if isinstance(value, list):
            merged[key] = list(value)
    for key in ("source_type", "teaching_role", "summary", "title_hint", "completeness"):
        value = cached.get(key)
        if isinstance(value, str) and value.strip():
            merged[key] = value
    for key in ("merge_with_previous", "is_complete_knowledge_point"):
        if key in cached:
            merged[key] = bool(cached.get(key))
    merged["analysis_mode"] = "ai"
    return merged


def _collection_summaries(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(record.get("source_collection", "unknown"), []).append(record)

    summaries = []
    for collection, items in grouped.items():
        concept_counter: Counter[str] = Counter()
        for item in items:
            for concept in item.get("core_concepts", []):
                concept_counter[concept] += 1
        representative = sorted(
            items,
            key=lambda item: len(item.get("core_concepts", [])) + len(item.get("methods", [])),
            reverse=True,
        )[:8]
        paths = []
        section_ids = []
        chunk_count = 0
        doc_ids = set()
        for item in items:
            paths.extend(item.get("source_paths", []) or [item.get("path", "")])
            section_ids.extend(item.get("section_ids", []) or [item.get("section_id", "")])
            chunk_count += len(item.get("source_chunks", []) or [item.get("chunk_ref", "")])
            doc_ids.add(item.get("doc_id") or tuple(item.get("source_paths", []) or [item.get("path", "")]))
        summaries.append(
            {
                "source_collection": collection,
                "doc_count": len(doc_ids),
                "chunk_count": chunk_count,
                "paths": _unique(paths)[:8],
                "section_ids": _unique(section_ids)[:16],
                "core_concepts": [concept for concept, _ in concept_counter.most_common(24)],
                "representative_chunks": [
                    {
                        "chunk_ref": (item.get("source_chunks") or [_chunk_ref(item)])[0],
                        "core_concepts": item.get("core_concepts", [])[:8],
                        "summary": item.get("summary", ""),
                    }
                    for item in representative
                ],
            }
        )
    summaries = sorted(summaries, key=lambda item: _natural_key(str(item.get("source_collection", ""))))
    _attach_collection_relationships(summaries)
    return summaries


def _attach_collection_relationships(summaries: list[dict[str, Any]]) -> None:
    term_sets = {
        str(summary.get("source_collection", "")): _term_set(
            " ".join(summary.get("core_concepts", []))
        )
        for summary in summaries
    }
    concept_lists = {
        str(summary.get("source_collection", "")): summary.get("core_concepts", [])
        for summary in summaries
    }
    for summary in summaries:
        cid = str(summary.get("source_collection", ""))
        current_terms = term_sets.get(cid, set())
        related = []
        for other in summaries:
            other_id = str(other.get("source_collection", ""))
            if other_id == cid:
                continue
            other_terms = term_sets.get(other_id, set())
            overlap = current_terms & other_terms
            if not overlap:
                continue
            score = len(overlap) / max(1, min(len(current_terms), len(other_terms)))
            shared_concepts = [
                concept
                for concept in concept_lists.get(cid, [])
                if _term_set(str(concept)) & overlap
            ][:8]
            related.append(
                {
                    "source_collection": other_id,
                    "score": round(score, 4),
                    "shared_concepts": _unique([*shared_concepts, *sorted(overlap)])[:8],
                }
            )
        related.sort(key=lambda item: item["score"], reverse=True)
        summary["related_collections"] = related[:5]


def _extract_core_concepts(
    text: str,
    section_id: str,
    metadata_concepts: list[str],
    headings: list[str],
) -> list[str]:
    candidates: list[str] = []
    candidates.extend(_supported_section_terms(_split_section_id(section_id), text, metadata_concepts, headings))
    candidates.extend(headings)
    candidates.extend(metadata_concepts)
    candidates.extend(match.group(1).strip() for match in _BOLD_RE.finditer(text))
    candidates.extend(_code_terms(text))
    candidates.extend(_definition_terms(text))
    candidates.extend(_concept_phrase_terms(text))
    candidates.extend(_latin_tokens(text))
    return _unique(_clean_term(item) for item in candidates if _is_good_term(item))[:18]


def _definition_terms(text: str) -> list[str]:
    terms = []
    for sentence in _sentences(text):
        if not any(hint in sentence for hint in _DEFINITION_HINTS):
            continue
        for term in _TERM_SPLIT_RE.split(sentence[:120]):
            term = _clean_term(term)
            if _is_good_term(term):
                terms.append(term)
    return terms


def _concept_phrase_terms(text: str) -> list[str]:
    terms = []
    for raw in _CJK_TERM_RE.findall(text[:3000]):
        term = _clean_term(_LEADING_VERB_RE.sub("", raw))
        if not any(hint in term for hint in _CONCEPT_HINTS):
            continue
        terms.append(term)
    return terms


def _latin_tokens(text: str) -> list[str]:
    result = []
    for token in _LATIN_TOKEN_RE.findall(text):
        if len(token) <= 1:
            continue
        if token.lower() in {"true", "false", "none", "and", "or", "not", "for", "in"}:
            continue
        if token.endswith("()") or token in _LATIN_CONCEPT_ALLOWLIST:
            result.append(token)
    return result[:20]


def _code_terms(text: str) -> list[str]:
    terms = []
    for match in _CODE_RE.finditer(text):
        token = match.group(1).strip()
        if token.endswith("()") or token in _LATIN_CONCEPT_ALLOWLIST:
            terms.append(token)
    return terms


def _headings(text: str) -> list[str]:
    return [_clean_term(match.group(1)) for match in _HEADING_RE.finditer(text)]


def _split_section_id(section_id: str) -> list[str]:
    normalized = re.sub(r"[-_]+", " ", section_id)
    return [_clean_term(item) for item in _TERM_SPLIT_RE.split(normalized) if _is_good_term(item)]


def _supported_section_terms(
    section_terms: list[str],
    text: str,
    metadata_concepts: list[str],
    headings: list[str],
) -> list[str]:
    definition_terms = set(_definition_terms(text))
    concept_phrases = set(_concept_phrase_terms(text))
    metadata_terms = {_clean_term(item) for item in metadata_concepts if _is_good_term(item)}
    heading_terms = {_clean_term(item) for item in headings if _is_good_term(item)}
    supported = []
    for term in section_terms:
        if (
            term in metadata_terms
            or term in heading_terms
            or term in definition_terms
            or term in concept_phrases
        ):
            supported.append(term)
    return supported


def _filter_low_confidence_section_terms(
    terms: list[str],
    low_confidence_terms: Any,
    summary: str,
) -> list[str]:
    low_confidence = {str(item) for item in low_confidence_terms or []}
    if not low_confidence:
        return terms
    return [term for term in terms if term not in low_confidence or term in summary]


def _extract_tagged_sentences(text: str, hints: tuple[str, ...]) -> list[str]:
    return _unique(
        _clean_sentence(sentence)
        for sentence in _sentences(text)
        if any(hint in sentence for hint in hints)
    )


def _extract_prerequisites(text: str) -> list[str]:
    prerequisites = []
    for sentence in _sentences(text[:2000]):
        if any(hint in sentence for hint in _PREREQUISITE_HINTS):
            prerequisites.extend(_split_sentence_terms(sentence))
    return _unique(_clean_term(item) for item in prerequisites if _is_good_term(item))


def _split_sentence_terms(sentence: str) -> list[str]:
    return [_clean_term(item) for item in _TERM_SPLIT_RE.split(sentence) if _is_good_term(item)]


def _sentences(text: str) -> list[str]:
    return [sentence.strip() for sentence in _SENTENCE_SPLIT_RE.split(text) if sentence.strip()]


def _summary(text: str, limit: int = 260) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit].strip()


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _finished_terms(ledger: dict[str, Any]) -> set[str]:
    terms: set[str] = set()
    for record in ledger.get("records", []):
        if not isinstance(record, dict):
            continue
        values = [
            record.get("chapter", ""),
            record.get("knowledge_point", ""),
            *record.get("covered_concepts", []),
            *record.get("covered_methods", []),
            *record.get("covered_misconceptions", []),
        ]
        for value in values:
            terms.update(_term_set(str(value)))
    return terms


def _record_terms(record: dict[str, Any]) -> set[str]:
    values = [
        record.get("section_id", ""),
        *record.get("core_concepts", []),
        *record.get("methods", []),
        *record.get("misconceptions", []),
        *record.get("prerequisites", []),
    ]
    terms: set[str] = set()
    for value in values:
        terms.update(_term_set(str(value)))
    return terms


def _term_overlap(concepts: list[str], terms: set[str]) -> list[str]:
    overlap = []
    for concept in concepts:
        if _term_set(concept) & terms:
            overlap.append(concept)
    return _unique(overlap)


def _coverage_label(overlap: list[str], concepts: list[str]) -> str:
    if not concepts:
        return "unknown"
    ratio = len(overlap) / max(1, len(concepts))
    if ratio >= 0.65:
        return "likely_covered"
    if ratio >= 0.25:
        return "partially_covered"
    return "uncovered"


def _term_set(text: str) -> set[str]:
    terms = set()
    for raw in _CJK_TERM_RE.findall(text):
        term = _clean_term(raw)
        if not _is_good_term(term):
            continue
        terms.add(term.lower())
        for piece in _TERM_SPLIT_RE.split(term):
            piece = _clean_term(piece)
            if _is_good_term(piece):
                terms.add(piece.lower())
    return terms


def _chunk_refs(chunks: Any, limit: int = 6) -> str:
    refs = []
    if not isinstance(chunks, list):
        return ""
    for item in chunks[:limit]:
        if not isinstance(item, dict):
            continue
        concepts = ", ".join(item.get("core_concepts", [])[:4])
        refs.append(f"{item.get('chunk_ref', '')} [{concepts}]")
    return "; ".join(ref for ref in refs if ref.strip())


def _related_collection_text(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    parts = []
    for item in items[:5]:
        if not isinstance(item, dict):
            continue
        shared = ", ".join(item.get("shared_concepts", [])[:5])
        parts.append(
            f"{item.get('source_collection', '')}(score={item.get('score', 0):.2f}; shared={shared})"
        )
    return "; ".join(parts)


def _chunk_ref(record: dict[str, Any]) -> str:
    return _chunk_ref_from_parts(
        str(record.get("source_collection") or ""),
        record.get("filename") or record.get("path") or "",
        _int(record.get("chunk_index")),
    )


def _chunk_ref_from_parts(collection: str, filename: Any, chunk_index: int) -> str:
    name = Path(str(filename)).stem if filename else "chunk"
    return f"{collection}/{name}#{chunk_index:03d}"


def _clean_sentence(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" -—:：。；;，,")


def _clean_term(value: Any) -> str:
    text = str(value)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^\s*\d+\s*[.．、-]\s*", "", text)
    text = re.sub(r"#+", "", text)
    text = re.sub(r"\s+", "", text)
    text = text.strip(" -—:：。；;，,")
    return re.sub(r"[的与和及或]+$", "", text)


def _is_good_term(value: Any) -> bool:
    term = _clean_term(value)
    if len(term) < 2 or len(term) > 28:
        return False
    if term in _STOPWORDS:
        return False
    if term.isascii() and term not in _LATIN_CONCEPT_ALLOWLIST and not term.endswith("()"):
        return False
    if term.isdigit():
        return False
    return True


def _unique(values: Any) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if not value:
            continue
        key = str(value).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(str(value))
    return result


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,\n，、]+", value)
    if not isinstance(value, list):
        return []
    return _unique(str(item).strip() for item in value if str(item).strip())


def _ranked_terms(groups: Any, limit: int) -> list[str]:
    counter: Counter[str] = Counter()
    first_seen: dict[str, int] = {}
    position = 0
    for group in groups:
        for raw in group or []:
            value = str(raw).strip()
            if not value:
                continue
            if value not in first_seen:
                first_seen[value] = position
                position += 1
            counter[value] += 1
    return sorted(counter, key=lambda value: (-counter[value], first_seen[value], value))[:limit]


def _overlap_score(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, min(len(left), len(right)))


def _estimate_tokens(text: str) -> int:
    cn = sum(1 for c in text if "一" <= c <= "鿿")
    en = len(text) - cn
    return int(cn / 1.5 + en / 4)


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _natural_key(value: str) -> list[Any]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value)]
