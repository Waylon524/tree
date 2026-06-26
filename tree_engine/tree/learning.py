"""Learning-workbench state and feedback revision helpers."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from tree.agents.writer import WriterAgent
from tree.config import DEFAULT_SOURCE_MTU_CHUNK_TOKENS, Settings
from tree.io import paths
from tree.model.client import LLMClient
from tree.planner.pipeline import load_dag
from tree.planner.store import read_json, write_json_atomic
from tree.rag.client import RAGClient
from tree.rag.indexer import RAGIndexer

READING_STATUSES = {"unread", "recommended", "reading", "read"}


class FeedbackRecord(BaseModel):
    feedback: str
    submitted_at: str
    status: str = "submitted"
    revised_at: str | None = None
    backup_path: str | None = None
    error: str | None = None


class LearningNodeState(BaseModel):
    reading_status: str = "unread"
    last_opened_at: str | None = None
    read_at: str | None = None
    affected_by_feedback: bool = False
    last_revised_at: str | None = None
    last_feedback_error: str | None = None
    feedback_history: list[FeedbackRecord] = Field(default_factory=list)


class LearningState(BaseModel):
    nodes: dict[str, LearningNodeState] = Field(default_factory=dict)


def load_learning_state(root: Path) -> LearningState:
    path = paths.learning_state_path(root)
    if not path.exists():
        return LearningState()
    raw = read_json(path)
    return LearningState.model_validate(raw if isinstance(raw, dict) else {})


def save_learning_state(root: Path, state: LearningState) -> None:
    write_json_atomic(paths.learning_state_path(root), state.model_dump(mode="json"))


def mark_node_opened(root: Path, node_id: str) -> LearningNodeState:
    state = load_learning_state(root)
    node = state.nodes.setdefault(node_id, LearningNodeState())
    node.last_opened_at = _utc_now()
    node.last_feedback_error = None
    if node.reading_status != "read":
        node.reading_status = "reading"
    save_learning_state(root, state)
    return node


def mark_node_read(root: Path, node_id: str, *, read: bool = True) -> LearningNodeState:
    state = load_learning_state(root)
    node = state.nodes.setdefault(node_id, LearningNodeState())
    now = _utc_now()
    if read:
        node.reading_status = "read"
        node.read_at = now
        node.last_opened_at = node.last_opened_at or now
        node.affected_by_feedback = False
    else:
        node.reading_status = "unread"
        node.read_at = None
    node.last_feedback_error = None
    save_learning_state(root, state)
    return node


def reading_view_for_dag(
    root: Path,
    *,
    node_ids: list[str],
    parents: dict[str, set[str]],
    children: dict[str, set[str]],
    generation_statuses: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """Return per-node learning fields for the DAG API."""
    learning_ready = bool(node_ids) and all(
        generation_statuses.get(node_id) == "complete" for node_id in node_ids
    )
    state = load_learning_state(root)
    read_ids = {
        node_id
        for node_id, record in state.nodes.items()
        if record.reading_status == "read" and node_id in node_ids
    }
    recommended_ids = _recommended_node_ids(node_ids, parents, read_ids, generation_statuses)

    view: dict[str, dict[str, Any]] = {}
    for node_id in node_ids:
        record = state.nodes.get(node_id, LearningNodeState())
        status = record.reading_status if record.reading_status in READING_STATUSES else "unread"
        recommended = node_id in recommended_ids
        if recommended and status == "unread":
            status = "recommended"
        view[node_id] = {
            "reading_status": status,
            "recommended": recommended,
            "affected_by_feedback": bool(record.affected_by_feedback),
            "learning_ready": learning_ready,
            "recommendation_reason": _recommendation_reason(node_id, parents, read_ids, recommended),
            "last_opened_at": record.last_opened_at,
            "read_at": record.read_at,
            "last_revised_at": record.last_revised_at,
            "last_feedback_error": record.last_feedback_error,
            "feedback_count": len(record.feedback_history),
        }
    return view


async def revise_node_from_feedback(root: Path, node_id: str, feedback: str) -> dict[str, Any]:
    feedback = feedback.strip()
    if not feedback:
        raise ValueError("Feedback is required.")

    dag = load_dag(root)
    nodes = list(dag.get("nodes") or [])
    nodes_by_id = {str(node.get("node_id") or ""): node for node in nodes}
    node = nodes_by_id.get(node_id)
    if not node:
        raise ValueError(f"Node not found: {node_id}")

    output_path = _node_output_path(root, node_id)
    if output_path is None:
        raise ValueError(f"No generated output found for node: {node_id}")

    current_text = output_path.read_text(encoding="utf-8")
    state = load_learning_state(root)
    node_state = state.nodes.setdefault(node_id, LearningNodeState())
    record = FeedbackRecord(feedback=feedback, submitted_at=_utc_now(), status="running")
    node_state.feedback_history.append(record)
    node_state.last_feedback_error = None
    save_learning_state(root, state)

    settings: Settings | None = None
    client: LLMClient | None = None
    rag: RAGClient | None = None
    try:
        settings = Settings.from_env(root)
        client = LLMClient(settings)
        writer = WriterAgent(client, project_root=root)
        rag = RAGClient(store_path=paths.rag_store_path(root))
        indexer = RAGIndexer(
            rag,
            source_mtu_chunk_tokens=getattr(
                settings, "source_mtu_chunk_tokens", DEFAULT_SOURCE_MTU_CHUNK_TOKENS
            ),
        )
        parents, children = _dag_adjacency(dag.get("edges") or [])
        prior_paths = _prior_output_paths(root, parents, node_id)
        query = f"{node.get('title', node_id)}\n{feedback}"
        # RAG context is best-effort: if the embedding service is down the revision
        # still proceeds from the current text + feedback instead of failing.
        retrieved = _safe_rag_query(
            rag, query, top_k=6, filters={"content_kind": "source", "node_id": [node_id]}
        )
        finished = (
            _safe_rag_query(
                rag, query, top_k=6, filters={"content_kind": "finished", "path": prior_paths}
            )
            if prior_paths
            else []
        )
        result = await writer.revise_from_feedback(
            span_title=str(node.get("title") or node_id),
            file_seq=_node_file_seq(nodes, node_id),
            current_text=current_text,
            user_feedback=feedback,
            prior_paths=prior_paths,
            prior_contents=[],
            retrieved=retrieved + finished,
            node_context=_learning_node_context(node_id, dag, nodes_by_id, parents, children),
        )
        revised = result.draft_content.strip()
        if not revised:
            raise RuntimeError("Writer returned an empty revision.")

        backup = _backup_output(root, output_path)
        output_path.write_text(revised + "\n", encoding="utf-8")
        try:
            indexer.index_finished_file(root, node_id, output_path)
        except Exception:
            pass  # embedding may be down; the revised output is already saved

        state = load_learning_state(root)
        node_state = state.nodes.setdefault(node_id, LearningNodeState())
        node_state.reading_status = "unread"
        node_state.read_at = None
        node_state.affected_by_feedback = False
        node_state.last_revised_at = _utc_now()
        node_state.last_feedback_error = None
        _mark_descendants_affected(state, children, node_id)
        _finish_feedback_record(node_state, record.submitted_at, status="complete", backup_path=backup)
        save_learning_state(root, state)
        return {
            "node_id": node_id,
            "output": output_path.name,
            "status": "complete",
            "backup_path": backup,
            "revised_at": node_state.last_revised_at,
        }
    except Exception as exc:
        state = load_learning_state(root)
        node_state = state.nodes.setdefault(node_id, LearningNodeState())
        message = f"{type(exc).__name__}: {exc}"
        node_state.last_feedback_error = message
        _finish_feedback_record(node_state, record.submitted_at, status="failed", error=message)
        save_learning_state(root, state)
        raise
    finally:
        if client is not None:
            await client.close()
        if rag is not None:
            rag.close()


def _recommended_node_ids(
    node_ids: list[str],
    parents: dict[str, set[str]],
    read_ids: set[str],
    generation_statuses: dict[str, str],
) -> set[str]:
    """Per-node recommendation: a generated node whose prerequisites are all read.

    Decoupled from whole-tree completion, so base fruit (no prerequisites, or all
    prerequisites already read) ripens as soon as it is generated — even while the
    upper canopy is still growing.
    """
    return {
        node_id
        for node_id in node_ids
        if generation_statuses.get(node_id) == "complete"
        and node_id not in read_ids
        and parents.get(node_id, set()) <= read_ids
    }


def _safe_rag_query(rag: RAGClient, query: str, **kwargs: Any) -> list[dict[str, Any]]:
    """Query RAG, returning [] if the embedding service is unavailable."""
    try:
        return rag.query(query, include_drafts=False, **kwargs)
    except Exception:
        return []


def _recommendation_reason(
    node_id: str,
    parents: dict[str, set[str]],
    read_ids: set[str],
    recommended: bool,
) -> str:
    if not recommended:
        return ""
    direct = parents.get(node_id, set())
    if not direct:
        return "Root node; ready to start."
    if direct <= read_ids:
        return "All prerequisite nodes have been read."
    return "Suggested starting point."


def _node_output_path(root: Path, node_id: str) -> Path | None:
    ledger = _load_ledger(root)
    for record in ledger.get("records", []):
        node_ids = set(record.get("node_ids") or ([record.get("node_id")] if record.get("node_id") else []))
        if node_id not in node_ids:
            continue
        output_path = str(record.get("output_path") or "")
        if not output_path:
            continue
        candidate = (root / output_path).resolve()
        try:
            candidate.relative_to(paths.outputs_root(root).resolve())
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None


def _prior_output_paths(root: Path, parents: dict[str, set[str]], node_id: str) -> list[str]:
    ancestors = _ancestors(parents, node_id)
    ledger = _load_ledger(root)
    paths_list: list[str] = []
    for record in ledger.get("records", []):
        node_ids = set(record.get("node_ids") or ([record.get("node_id")] if record.get("node_id") else []))
        if not node_ids or not node_ids <= ancestors:
            continue
        rel = str(record.get("output_path") or "")
        if rel and (root / rel).exists():
            paths_list.append(rel)
    return paths_list


def _backup_output(root: Path, output_path: Path) -> str:
    stamp = _utc_now().replace(":", "").replace("-", "").replace(".", "")
    target_dir = paths.learning_revisions_root(root) / output_path.stem
    target_dir.mkdir(parents=True, exist_ok=True)
    backup = target_dir / f"{stamp}.{output_path.name}"
    shutil.copy2(output_path, backup)
    return str(backup.relative_to(root))


def _finish_feedback_record(
    node_state: LearningNodeState,
    submitted_at: str,
    *,
    status: str,
    backup_path: str | None = None,
    error: str | None = None,
) -> None:
    for item in reversed(node_state.feedback_history):
        if item.submitted_at == submitted_at:
            item.status = status
            item.revised_at = _utc_now() if status == "complete" else None
            item.backup_path = backup_path
            item.error = error
            return


def _mark_descendants_affected(
    state: LearningState,
    children: dict[str, set[str]],
    node_id: str,
) -> None:
    for descendant in _descendants(children, node_id):
        record = state.nodes.setdefault(descendant, LearningNodeState())
        if record.reading_status == "read":
            record.affected_by_feedback = True


def _load_ledger(root: Path) -> dict[str, Any]:
    path = paths.knowledge_ledger_path(root)
    if not path.exists():
        return {"records": []}
    loaded = read_json(path)
    return loaded if isinstance(loaded, dict) else {"records": []}


def _dag_adjacency(
    edges: list[dict[str, Any]],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    parents: dict[str, set[str]] = {}
    children: dict[str, set[str]] = {}
    for edge in edges:
        if edge.get("relation") != "prerequisite":
            continue
        parent = str(edge.get("from_node_id") or "")
        child = str(edge.get("to_node_id") or "")
        if not parent or not child:
            continue
        parents.setdefault(child, set()).add(parent)
        children.setdefault(parent, set()).add(child)
    return parents, children


def _ancestors(parents: dict[str, set[str]], node_id: str) -> set[str]:
    found: set[str] = set()
    stack = list(parents.get(node_id, set()))
    while stack:
        current = stack.pop()
        if current in found:
            continue
        found.add(current)
        stack.extend(parents.get(current, set()))
    return found


def _descendants(children: dict[str, set[str]], node_id: str) -> set[str]:
    found: set[str] = set()
    stack = list(children.get(node_id, set()))
    while stack:
        current = stack.pop()
        if current in found:
            continue
        found.add(current)
        stack.extend(children.get(current, set()))
    return found


def _learning_node_context(
    node_id: str,
    dag: dict[str, Any],
    nodes_by_id: dict[str, dict[str, Any]],
    parents: dict[str, set[str]],
    children: dict[str, set[str]],
) -> str:
    node = nodes_by_id.get(node_id, {})
    future = sorted(_descendants(children, node_id))
    direct = sorted(parents.get(node_id, set()))
    lines = [
        "Learning feedback revision context.",
        f"TARGET {node_id} | {node.get('title', node_id)} | {_defines_text(node)}",
        "Revise only this generated KnowledgeNode output according to user feedback.",
        "Preserve the current H1 and deterministic prerequisite section if present.",
        "Do not expand into future or sibling KnowledgeNodes.",
    ]
    if direct:
        lines.append("Direct prerequisite nodes: " + ", ".join(_node_label(item, nodes_by_id) for item in direct))
    if future:
        lines.append("Forbidden future descendant nodes: " + ", ".join(future))
    roots = dag.get("roots") or []
    if roots:
        lines.append("DAG roots: " + ", ".join(str(item) for item in roots))
    return "\n".join(lines)


def _defines_text(node: dict[str, Any]) -> str:
    defines = node.get("defines") or node.get("keywords") or []
    return ", ".join(str(item) for item in defines[:8])


def _node_label(node_id: str, nodes_by_id: dict[str, dict[str, Any]]) -> str:
    node = nodes_by_id.get(node_id, {})
    return f"{node_id} ({node.get('title', node_id)})"


def _node_file_seq(nodes: list[dict[str, Any]], node_id: str) -> str:
    ordered = sorted(nodes, key=lambda n: (n.get("source_order_index", 0), n.get("node_id", "")))
    for index, node in enumerate(ordered, start=1):
        if node.get("node_id") == node_id:
            return str(index).zfill(3)
    return "001"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
