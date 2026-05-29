"""Chunk-level source inventory for curriculum planning.

The source inventory is a compact map from source RAG chunks to concepts,
methods, misconceptions, prerequisites, and short summaries. It lets the
examiner reason about chapter relationships without rereading every full chunk.
"""

from __future__ import annotations

import json
import re
import asyncio
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
    inventory = {
        "version": 1,
        "chunks": records,
        "collections": _collection_summaries(records),
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

    existing = load_inventory(root)
    cached = {
        (item.get("doc_id"), item.get("chunk_id"), item.get("text_hash")): item
        for item in existing.get("chunks", [])
        if isinstance(item, dict)
    }
    sem = asyncio.Semaphore(max(1, concurrency))

    async def analyze(hit: dict) -> dict[str, Any]:
        base = _chunk_record(hit)
        key = (base.get("doc_id"), base.get("chunk_id"), base.get("text_hash"))
        if key in cached:
            return cached[key]
        try:
            async with sem:
                ai = await analyzer.analyze_source_chunk(_chunk_for_ai(hit))
            return _merge_ai_analysis(base, ai)
        except Exception:
            return base

    records = await asyncio.gather(*(analyze(hit) for hit in source_chunks))
    records = sorted(
        records,
        key=lambda item: (
            item.get("source_collection", ""),
            item.get("path", ""),
            item.get("chunk_index", 0),
        ),
    )
    inventory = {
        "version": 1,
        "analysis_mode": "ai_with_rule_fallback",
        "chunks": records,
        "collections": _collection_summaries(records),
    }
    save_inventory(root, inventory)
    return inventory


def build_inventory_context(
    root: Path,
    inventory: dict[str, Any],
    completed_collections: set[str] | None = None,
    limit_collections: int = 12,
) -> str:
    """Format source inventory for examiner Phase C."""
    completed_collections = completed_collections or set()
    ledger = load_ledger(root)
    finished_terms = _finished_terms(ledger)
    lines = [
        "## Source Inventory",
        "Use this chunk-level inventory to choose chapters by knowledge clusters, not upload order.",
        "A chapter may reference multiple related collections, but Source_Collection must name the primary collection id for the first knowledge point.",
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
    metadata_concepts = [str(item) for item in metadata.get("concepts") or []]
    headings = _headings(text)
    concepts = _extract_core_concepts(text, section_id, metadata_concepts, headings)
    return {
        "chunk_id": str(hit.get("chunk_id") or metadata.get("chunk_id") or ""),
        "text_hash": _text_hash(text),
        "chunk_index": chunk_index,
        "chunk_ref": _chunk_ref_from_parts(source_collection, metadata.get("filename"), chunk_index),
        "doc_id": str(metadata.get("doc_id") or ""),
        "source_collection": source_collection,
        "path": str(metadata.get("path") or metadata.get("filename") or ""),
        "filename": str(metadata.get("filename") or ""),
        "section_id": section_id,
        "chunk_type": str(metadata.get("chunk_type") or ""),
        "core_concepts": concepts,
        "methods": _extract_tagged_sentences(text, _METHOD_HINTS)[:8],
        "misconceptions": _extract_tagged_sentences(text, _MISCONCEPTION_HINTS)[:8],
        "prerequisites": _extract_prerequisites(text)[:8],
        "formulas": [str(item) for item in metadata.get("formulas") or []][:10],
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


def _merge_ai_analysis(base: dict[str, Any], ai: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key in ("core_concepts", "methods", "misconceptions", "prerequisites"):
        values = ai.get(key)
        if isinstance(values, list):
            cleaned = _unique(_clean_term(item) for item in values if _is_good_term(item))
            if cleaned:
                merged[key] = cleaned[:18 if key == "core_concepts" else 10]
    for key in ("source_type", "teaching_role", "summary"):
        value = ai.get(key)
        if isinstance(value, str) and value.strip():
            merged[key] = _clean_sentence(value) if key == "summary" else value.strip()[:40]
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
        summaries.append(
            {
                "source_collection": collection,
                "doc_count": len({item.get("doc_id") or item.get("path") for item in items}),
                "chunk_count": len(items),
                "paths": _unique(item.get("path", "") for item in items)[:8],
                "section_ids": _unique(item.get("section_id", "") for item in items)[:16],
                "core_concepts": [concept for concept, _ in concept_counter.most_common(24)],
                "representative_chunks": [
                    {
                        "chunk_ref": _chunk_ref(item),
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
    candidates.extend(_split_section_id(section_id))
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


def _int(value: Any) -> int:
    return value if isinstance(value, int) else 0


def _natural_key(value: str) -> list[Any]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value)]
