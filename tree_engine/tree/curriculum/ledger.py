"""Knowledge ledger for finished TREE outputs.

The ledger is a compact, structured curriculum memory. RAG chunks are good for
finding passages; the ledger is better for deciding whether a concept has
already been taught and what the next section should add.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from tree.io import paths

_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_CJK_OR_WORD_RE = re.compile(r"[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_`()+\-]*")
_COMMON_TERM_SPLIT_RE = re.compile(r"[，,、/；;：:（）()\[\]【】\s的与和及或]+")
_STOPWORDS = {
    "学习目标与先修前置",
    "核心内容",
    "例题",
    "常见误区",
    "自测题",
    "自测题参考答案",
    "小结",
    "解答",
    "输出",
    "代码",
    "示例",
}
_METHOD_HINTS = ("方法", "步骤", "使用", "分析", "转换", "运算", "赋值", "输入", "输出", "调试", "判断")
_MISCONCEPTION_HINTS = ("误区", "错误", "混淆", "不是", "不能", "不要")
_PREREQUISITE_RE = re.compile(r"(?:先修前置|先修|前置)[：:】]?\s*(.+)")


def load_ledger(root: Path) -> dict[str, Any]:
    path = paths.knowledge_ledger_path(root)
    if not path.exists():
        return {"version": 1, "records": []}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": 1, "records": []}
    if not isinstance(loaded, dict):
        return {"version": 1, "records": []}
    records = loaded.get("records")
    if not isinstance(records, list):
        loaded["records"] = []
    loaded.setdefault("version", 1)
    return loaded


def save_ledger(root: Path, ledger: dict[str, Any]) -> None:
    path = paths.knowledge_ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(path)


def update_finished_record(
    root: Path,
    chapter: str,
    path: Path,
    graph_node_id: str | None = None,
    covered_node_ids: list[str] | None = None,
    required_nodes: list[str] | None = None,
    source_collections: list[str] | None = None,
    hit_chunks: list[str] | None = None,
) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    record = build_finished_record(
        root,
        chapter,
        path,
        text,
        graph_node_id=graph_node_id,
        covered_node_ids=covered_node_ids,
        required_nodes=required_nodes,
        source_collections=source_collections,
        hit_chunks=hit_chunks,
    )
    ledger = load_ledger(root)
    records = [
        item
        for item in ledger.get("records", [])
        if isinstance(item, dict) and item.get("path") != record["path"]
    ]
    records.append(record)
    ledger["records"] = sorted(records, key=lambda item: (item.get("chapter", ""), item.get("file_seq", "")))
    save_ledger(root, ledger)
    return record


def reconcile_finished_outputs(root: Path) -> dict[str, Any]:
    ledger = load_ledger(root)
    finished_paths = {
        _relative_path(root, path)
        for path in sorted(paths.outputs_root(root).glob("**/*.md"))
    }
    known_paths = {
        item.get("path")
        for item in ledger.get("records", [])
        if isinstance(item, dict)
    }
    records = [
        item
        for item in ledger.get("records", [])
        if isinstance(item, dict) and item.get("path") in finished_paths
    ]
    changed = len(records) != len([item for item in ledger.get("records", []) if isinstance(item, dict)])
    for path in sorted(paths.outputs_root(root).glob("**/*.md")):
        rel = _relative_path(root, path)
        if rel in known_paths:
            continue
        execution_path = _execution_path_from_output_path(root, path)
        records.append(build_finished_record(root, execution_path, path, path.read_text(encoding="utf-8")))
        known_paths.add(rel)
        changed = True
    if changed:
        ledger["records"] = sorted(records, key=lambda item: (item.get("chapter", ""), item.get("file_seq", "")))
        save_ledger(root, ledger)
    return ledger


def build_finished_record(
    root: Path,
    chapter: str,
    path: Path,
    text: str,
    graph_node_id: str | None = None,
    covered_node_ids: list[str] | None = None,
    required_nodes: list[str] | None = None,
    source_collections: list[str] | None = None,
    hit_chunks: list[str] | None = None,
) -> dict[str, Any]:
    metadata = _front_matter(text)
    headings = _headings(text)
    title = _title_from_text(path, headings)
    execution_path = str(chapter or _execution_path_from_output_path(root, path))
    tree_id, branch_id = _split_execution_path(execution_path)
    terms = _unique(
        _clean_term(term)
        for term in (
            [title, metadata.get("chapter", ""), *headings, *_metadata_list(metadata.get("confusion_points"))]
        )
    )
    concepts = [term for term in terms if term and term not in _STOPWORDS][:24]
    methods = [term for term in concepts if any(hint in term for hint in _METHOD_HINTS)][:12]
    misconceptions = _extract_misconceptions(text, metadata)[:12]
    prerequisites = _extract_prerequisites(text)[:8]
    file_seq = path.stem.split(".", 1)[0]
    covered_nodes = _unique(
        str(item)
        for item in [
            *(covered_node_ids or []),
            *([graph_node_id] if graph_node_id else []),
        ]
        if str(item).strip()
    )
    return {
        "chapter": execution_path,
        "execution_path": execution_path,
        "tree_id": tree_id,
        "branch_id": branch_id,
        "file_seq": file_seq,
        "filename": path.name,
        "path": _relative_path(root, path),
        "knowledge_point": _clean_term(title),
        "covered_concepts": concepts,
        "covered_methods": methods,
        "covered_misconceptions": misconceptions,
        "prerequisites": prerequisites,
        "graph_node_id": graph_node_id,
        "covered_node_ids": covered_nodes,
        "required_nodes": list(required_nodes or []),
        "source_collections": list(source_collections or []),
        "hit_chunks": _unique(str(item) for item in hit_chunks or [] if str(item).strip()),
        "summary": _summary(text),
    }


def duplicate_brief(
    root: Path,
    query: str,
    *,
    top_n: int = 5,
    threshold: float = 0.34,
    allowed_paths: set[str] | None = None,
) -> dict[str, Any]:
    ledger = reconcile_finished_outputs(root)
    records = [item for item in ledger.get("records", []) if isinstance(item, dict)]
    if allowed_paths is not None:
        records = [record for record in records if _record_path_allowed(record, allowed_paths)]
    scored = []
    query_terms = _term_set(query)
    for record in records:
        record_terms = _record_terms(record)
        overlap = query_terms & record_terms
        score = _overlap_score(query_terms, record_terms, overlap)
        if score <= 0:
            continue
        scored.append((score, overlap, record))
    scored.sort(key=lambda item: item[0], reverse=True)
    matches = [_match_summary(score, overlap, record) for score, overlap, record in scored[:top_n]]
    likely_duplicate = bool(matches and matches[0]["score"] >= threshold)
    return {
        "query": query,
        "likely_duplicate": likely_duplicate,
        "threshold": threshold,
        "matches": matches,
    }


def format_duplicate_brief(brief: dict[str, Any]) -> str:
    lines = [
        "## Duplicate / Delta Brief",
        f"Candidate or retrieval query: {brief.get('query', '')}",
        f"Likely duplicate: {'YES' if brief.get('likely_duplicate') else 'NO'}",
        "",
        "Most similar finished outputs:",
    ]
    matches = brief.get("matches") or []
    if not matches:
        lines.append("- (none)")
    for match in matches:
        concepts = ", ".join(match.get("overlap_concepts", [])[:8]) or "n/a"
        lines.append(
            f"- score={match.get('score', 0):.2f} | {match.get('path')} | "
            f"{match.get('knowledge_point')} | overlap: {concepts}"
        )
    lines.extend(
        [
            "",
            "Rules:",
            "- Treat the matched finished outputs as already taught.",
            "- New work must state the incremental delta beyond these matches.",
            "- If there is no clear delta, keep duplicate material brief and focus the draft on the remaining declared branch-span delta.",
            "- Cite matched concepts briefly as prerequisites; do not reteach them.",
        ]
    )
    return "\n".join(lines)


def format_ledger_context(root: Path, limit: int = 30) -> str:
    ledger = reconcile_finished_outputs(root)
    records = [item for item in ledger.get("records", []) if isinstance(item, dict)]
    if not records:
        return "Knowledge Ledger: no finished outputs recorded yet."
    lines = ["Knowledge Ledger: finished outputs already taught:"]
    for record in records[:limit]:
        concepts = ", ".join(record.get("covered_concepts", [])[:8])
        lines.append(f"- {record.get('path')}: {record.get('knowledge_point')} | concepts: {concepts}")
    if len(records) > limit:
        lines.append(f"- ... {len(records) - limit} more records omitted")
    return "\n".join(lines)


def format_scoped_ledger_context(root: Path, allowed_paths: set[str] | None, limit: int = 30) -> str:
    """Format finished ledger records visible to one BranchRun prior scope."""
    if allowed_paths is None:
        return format_ledger_context(root, limit=limit)
    ledger = reconcile_finished_outputs(root)
    records = [
        item
        for item in ledger.get("records", [])
        if isinstance(item, dict) and _record_path_allowed(item, allowed_paths)
    ]
    if not records:
        return "Knowledge Ledger: no prior finished outputs are visible in this BranchRun scope."
    lines = ["Knowledge Ledger: prior finished outputs visible in this BranchRun scope:"]
    for record in records[:limit]:
        concepts = ", ".join(record.get("covered_concepts", [])[:8])
        lines.append(f"- {record.get('path')}: {record.get('knowledge_point')} | concepts: {concepts}")
    if len(records) > limit:
        lines.append(f"- ... {len(records) - limit} more records omitted")
    return "\n".join(lines)


def _front_matter(text: str) -> dict[str, Any]:
    match = _FRONT_MATTER_RE.search(text)
    if not match:
        return {}
    result: dict[str, Any] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = _parse_metadata_value(value.strip())
    return result


def _record_path_allowed(record: dict[str, Any], allowed_paths: set[str]) -> bool:
    path = str(record.get("path") or "")
    return path in allowed_paths or f"finished:{path}" in allowed_paths


def _execution_path_from_output_path(root: Path, path: Path) -> str:
    try:
        rel = path.relative_to(paths.outputs_root(root))
    except ValueError:
        return path.parent.name
    parts = rel.parts
    if len(parts) >= 3:
        return "/".join(parts[:2])
    if len(parts) >= 2:
        return parts[0]
    return path.parent.name


def _split_execution_path(execution_path: str) -> tuple[str, str]:
    parts = [part for part in str(execution_path).split("/") if part]
    tree_id = parts[0] if parts else ""
    branch_id = parts[1] if len(parts) > 1 else ""
    return tree_id, branch_id


def _parse_metadata_value(value: str) -> Any:
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip("\"'") for item in inner.split(",") if item.strip()]
    return value.strip("\"'")


def _metadata_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value:
        return [value]
    return []


def _headings(text: str) -> list[str]:
    return [_clean_term(match.group(2)) for match in _HEADING_RE.finditer(text)]


def _title_from_text(path: Path, headings: list[str]) -> str:
    if headings:
        return headings[0]
    return path.stem


def _extract_misconceptions(text: str, metadata: dict[str, Any]) -> list[str]:
    candidates = _metadata_list(metadata.get("confusion_points"))
    for line in text.splitlines():
        stripped = line.strip(" -*\t")
        if not stripped:
            continue
        if any(hint in stripped for hint in _MISCONCEPTION_HINTS):
            candidates.append(stripped)
    return _unique(_clean_term(item) for item in candidates)


def _extract_prerequisites(text: str) -> list[str]:
    items = []
    for line in text.splitlines()[:80]:
        match = _PREREQUISITE_RE.search(line)
        if match:
            items.extend(_split_terms(match.group(1)))
    return _unique(_clean_term(item) for item in items if item)


def _summary(text: str, limit: int = 500) -> str:
    body = _FRONT_MATTER_RE.sub("", text).strip()
    body = re.sub(r"```.*?```", "", body, flags=re.DOTALL)
    body = re.sub(r"\s+", " ", body)
    return body[:limit].strip()


def _record_terms(record: dict[str, Any]) -> set[str]:
    values = [
        record.get("knowledge_point", ""),
        record.get("chapter", ""),
        record.get("filename", ""),
        *record.get("covered_concepts", []),
        *record.get("covered_methods", []),
        *record.get("covered_misconceptions", []),
    ]
    terms: set[str] = set()
    for value in values:
        terms.update(_term_set(str(value)))
    return terms


def _term_set(text: str) -> set[str]:
    terms = set()
    for raw in _CJK_OR_WORD_RE.findall(text):
        term = _clean_term(raw)
        if not term or term in _STOPWORDS:
            continue
        terms.add(term.lower())
        for piece in _split_terms(term):
            if piece and piece not in _STOPWORDS:
                terms.add(piece.lower())
    return terms


def _split_terms(text: str) -> list[str]:
    return [
        _clean_term(part)
        for part in _COMMON_TERM_SPLIT_RE.split(text)
        if _clean_term(part)
    ]


def _overlap_score(query_terms: set[str], record_terms: set[str], overlap: set[str]) -> float:
    if not query_terms or not record_terms:
        return 0.0
    if not overlap:
        return 0.0
    return (len(overlap) / len(query_terms)) * 0.7 + (len(overlap) / len(record_terms)) * 0.3


def _match_summary(score: float, overlap: set[str], record: dict[str, Any]) -> dict[str, Any]:
    concepts = record.get("covered_concepts", [])
    concept_terms = {
        concept: _term_set(str(concept))
        for concept in concepts
    }
    overlapped_concepts = [
        concept
        for concept, terms in concept_terms.items()
        if terms & overlap
    ]
    return {
        "score": round(score, 4),
        "path": record.get("path", ""),
        "chapter": record.get("chapter", ""),
        "knowledge_point": record.get("knowledge_point", ""),
        "overlap_concepts": _unique([*overlapped_concepts, *sorted(overlap)])[:12],
    }


def _clean_term(value: str) -> str:
    value = re.sub(r"`([^`]+)`", r"\1", str(value))
    value = re.sub(r"^\s*\d+\s*[.．、-]\s*", "", value)
    value = re.sub(r"#+", "", value)
    value = re.sub(r"\s+", "", value)
    return value.strip(" -—:：。；;，,")


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


def _relative_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
