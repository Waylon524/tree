"""`tre rag ...` artifact inspection commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tree.io import paths
from tree.planner.pipeline import load_dag, load_nodes
from tree.planner.store import read_envelope_data


def status_text(root: Path) -> str:
    rag_store = paths.rag_store_path(root)
    return "\n".join(
        [
            f"rag-store: {'ok' if rag_store.exists() else 'missing'}",
            f"nodes: {len(load_nodes(root))}",
            f"edges: {len(load_dag(root).get('edges', []))}",
        ]
    )


def inventory_text(root: Path) -> str:
    data = read_envelope_data(paths.mtus_path(root))
    mtus = data.get("mtus", [])
    return json.dumps({"mtu_count": len(mtus), "mtus": mtus}, ensure_ascii=False, indent=2)


def nodes_text(root: Path) -> str:
    nodes = load_nodes(root)
    if not nodes:
        return "No knowledge nodes."
    return "\n".join(_node_line(node) for node in nodes)


def graph_text(root: Path) -> str:
    dag = load_dag(root)
    lines = ["Knowledge DAG"]
    for edge in dag.get("edges", []):
        lines.append(f"{edge.get('from_node_id')} -> {edge.get('to_node_id')} [{edge.get('relation')}]")
    return "\n".join(lines)


def search_text(root: Path, query: str, *, top_k: int = 5) -> str:
    from tree.rag.client import RAGClient

    hits = RAGClient(store_path=paths.rag_store_path(root)).query(query, top_k=top_k)
    if not hits:
        return "No hits."
    return "\n\n".join(_hit_text(hit, index) for index, hit in enumerate(hits, start=1))


def _node_line(node: dict[str, Any]) -> str:
    collections = ",".join(node.get("collections", []) or [])
    return f"{node.get('node_id')} | {node.get('title')} | {collections}"


def _hit_text(hit: dict[str, Any], index: int) -> str:
    metadata = hit.get("metadata") or {}
    source = metadata.get("path") or metadata.get("doc_id") or metadata.get("filename") or "unknown"
    return f"{index}. {source}\n{hit.get('text', '')}"
