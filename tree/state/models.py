"""Pydantic models for pipeline state and agent outputs."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel


class ChapterRecord(BaseModel):
    chapter_name: str
    status: str  # "in_progress" | "completed"
    files_completed: list[str] = []
    source_collection: str | None = None


class PipelineState(BaseModel):
    chapters: list[ChapterRecord] = []


class Route(str, Enum):
    PASS = "PASS"
    FAIL_KNOWLEDGE_GAP = "FAIL_KNOWLEDGE_GAP"


class ExamSections(BaseModel):
    knowledge_point: str
    blind_exam: str
    answer_key: str
    writer_instructions: str


class AuditResult(BaseModel):
    route: Route
    exam_id: str
    bottleneck_report: str


class WriterResult(BaseModel):
    is_exam_too_broad: bool = False
    bloat_description: str = ""
    draft_content: str = ""
    draft_path: Path | None = None


ArchitectResult = WriterResult


class ExamTooBroadContext(BaseModel):
    bloat_description: str
    knowledge_point_name: str


class IterationState(BaseModel):
    chapter: str
    file_seq: str
    knowledge_point: str = ""
    exam_sections: ExamSections | None = None
    iteration: int = 0
    previous_bottleneck: str | None = None
    draft_path: Path | None = None
