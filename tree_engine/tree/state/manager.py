"""Pipeline state file I/O with Pydantic validation."""

from __future__ import annotations

from pathlib import Path

from tree.state.models import ChapterRecord, PipelineState


class StateManager:
    def __init__(self, state_path: Path):
        self._path = state_path

    def load(self) -> PipelineState:
        if not self._path.exists():
            return PipelineState()
        text = self._path.read_text(encoding="utf-8")
        return PipelineState.model_validate_json(text)

    def save(self, state: PipelineState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def find_in_progress(self, state: PipelineState) -> ChapterRecord | None:
        for ch in state.chapters:
            if ch.status == "in_progress":
                return ch
        return None

    def add_chapter(
        self,
        state: PipelineState,
        name: str,
        source_collection: str | None = None,
    ) -> PipelineState:
        chapters = list(state.chapters) + [
            ChapterRecord(
                chapter_name=name,
                status="in_progress",
                source_collection=source_collection,
            )
        ]
        return PipelineState(chapters=chapters)

    def complete_chapter(self, state: PipelineState, name: str) -> PipelineState:
        chapters = []
        for ch in state.chapters:
            if ch.chapter_name == name:
                chapters.append(ch.model_copy(update={"status": "completed"}))
            else:
                chapters.append(ch)
        return PipelineState(chapters=chapters)

    def add_file_completed(self, state: PipelineState, chapter: str, filename: str) -> PipelineState:
        chapters = []
        for ch in state.chapters:
            if ch.chapter_name == chapter:
                files = list(ch.files_completed)
                if filename not in files:
                    files.append(filename)
                chapters.append(ch.model_copy(update={"files_completed": files}))
            else:
                chapters.append(ch)
        return PipelineState(chapters=chapters)
