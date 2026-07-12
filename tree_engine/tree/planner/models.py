"""Planner data models: MTU -> KnowledgeNode -> DAG."""

from __future__ import annotations

from pydantic import BaseModel, Field


class MTU(BaseModel):
    """Minimal Teachable Unit produced by the Archivist (stage ②)."""

    mtu_id: str
    collection: str
    source_file: str
    # Workspace-relative material identity and content lineage. Defaults keep
    # older planner artifacts readable; current ingest always populates both.
    source_id: str = ""
    source_sha256: str = ""
    line_range: tuple[int, int]
    title: str
    defines: list[str] = Field(default_factory=list)
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
    defines: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    summary: str = ""
    collections: list[str] = Field(default_factory=list)
    source_order_index: int = 0
    external_prerequisites: list[str] = Field(default_factory=list)


class DagEdge(BaseModel):
    from_node_id: str
    to_node_id: str
    relation: str = "prerequisite"  # "prerequisite" (hard) | "order" (soft)
    confidence: float = 1.0
    required_defines: list[str] = Field(default_factory=list)


class KnowledgeDag(BaseModel):
    nodes: list[KnowledgeNode] = Field(default_factory=list)
    edges: list[DagEdge] = Field(default_factory=list)
