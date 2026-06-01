"""Planner data models: MTU -> KnowledgeNode -> DAG -> Branch.

See docs/REBUILD-DESIGN.md §3.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class MTU(BaseModel):
    """Minimal Teachable Unit produced by the Archivist (stage ②)."""

    mtu_id: str
    collection: str
    source_file: str
    line_range: tuple[int, int]
    title: str
    keywords: list[str] = Field(default_factory=list)
    summary: str = ""
    unit_kind: str = "concept"  # concept | example | exercise | misconception | ...
    source_order_index: int = 0
    # `text` is intentionally NOT persisted in mtus.json; it is read from the
    # cleaned Markdown by line_range at embedding time, then the Markdown is deleted.


class KnowledgeNode(BaseModel):
    """Canonical node produced by Dagger (stage ③) by merging duplicate MTUs."""

    node_id: str
    title: str
    member_mtu_ids: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    summary: str = ""
    collections: list[str] = Field(default_factory=list)
    source_order_index: int = 0


class DagEdge(BaseModel):
    from_node_id: str
    to_node_id: str
    relation: str = "prerequisite"  # "prerequisite" (hard) | "order" (soft)
    confidence: float = 1.0


class KnowledgeDag(BaseModel):
    nodes: list[KnowledgeNode] = Field(default_factory=list)
    edges: list[DagEdge] = Field(default_factory=list)


class KnowledgeBranch(BaseModel):
    """Linear executable segment cut from the DAG (stage ④, deterministic)."""

    branch_id: str
    node_ids: list[str] = Field(default_factory=list)
    coverage_node_ids: list[str] = Field(default_factory=list)
    start_node_id: str = ""
    end_node_id: str = ""
    upstream_branch_ids: list[str] = Field(default_factory=list)
    downstream_branch_ids: list[str] = Field(default_factory=list)
    display_order: int = 0
