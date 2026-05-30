"""Pipeline state file I/O with Pydantic validation."""

from __future__ import annotations

from pathlib import Path

from tree.state.models import BranchRunRecord, ChapterRecord, PipelineState


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

    def find_in_progress_all(self, state: PipelineState) -> list[ChapterRecord]:
        return [ch for ch in state.chapters if ch.status == "in_progress"]

    def add_chapter(
        self,
        state: PipelineState,
        name: str,
        source_collection: str | None = None,
        source_collections: list[str] | None = None,
        graph_node_id: str | None = None,
        required_nodes: list[str] | None = None,
        provisional_chapter_title: str | None = None,
        branch_id: str | None = None,
        branch_run_id: str | None = None,
    ) -> PipelineState:
        collections = list(source_collections or [])
        if source_collection and source_collection not in collections:
            collections.insert(0, source_collection)
        chapters = list(state.chapters) + [
            ChapterRecord(
                execution_path=name,
                status="in_progress",
                provisional_display_title=provisional_chapter_title,
                source_collection=source_collection,
                source_collections=collections,
                current_start_node_id=graph_node_id,
                coverage_node_ids=list(required_nodes or []),
                branch_id=branch_id,
                branch_run_id=branch_run_id,
            )
        ]
        return state.model_copy(update={"chapters": chapters})

    def reopen_chapter(
        self,
        state: PipelineState,
        name: str,
        source_collection: str | None = None,
        source_collections: list[str] | None = None,
        graph_node_id: str | None = None,
        required_nodes: list[str] | None = None,
        branch_id: str | None = None,
        branch_run_id: str | None = None,
    ) -> PipelineState:
        collections = list(source_collections or [])
        if source_collection and source_collection not in collections:
            collections.insert(0, source_collection)
        chapters = []
        for ch in state.chapters:
            if ch.chapter_name == name:
                chapters.append(
                    ch.model_copy(
                        update={
                            "status": "in_progress",
                            "source_collection": source_collection or ch.source_collection,
                            "source_collections": collections or ch.source_collections,
                            "current_start_node_id": graph_node_id or ch.graph_node_id,
                            "coverage_node_ids": list(required_nodes or ch.required_nodes),
                            "branch_id": branch_id or ch.branch_id,
                            "branch_run_id": branch_run_id or ch.branch_run_id,
                        }
                    )
                )
            else:
                chapters.append(ch)
        return state.model_copy(update={"chapters": chapters})

    def set_chapter_title(
        self,
        state: PipelineState,
        name: str,
        title: str,
        reason: str = "",
    ) -> PipelineState:
        chapters = []
        for ch in state.chapters:
            if ch.chapter_name == name:
                chapters.append(
                    ch.model_copy(
                        update={
                            "display_title": title,
                            "display_naming_reason": reason,
                        }
                    )
                )
            else:
                chapters.append(ch)
        return state.model_copy(update={"chapters": chapters})

    def complete_chapter(self, state: PipelineState, name: str) -> PipelineState:
        chapters = []
        for ch in state.chapters:
            if ch.chapter_name == name:
                chapters.append(ch.model_copy(update={"status": "completed"}))
            else:
                chapters.append(ch)
        return state.model_copy(update={"chapters": chapters})

    def add_file_completed(self, state: PipelineState, chapter: str, filename: str) -> PipelineState:
        chapters = []
        for ch in state.chapters:
            if ch.chapter_name == chapter:
                files = list(ch.outputs_completed)
                if filename not in files:
                    files.append(filename)
                chapters.append(ch.model_copy(update={"outputs_completed": files}))
            else:
                chapters.append(ch)
        return state.model_copy(update={"chapters": chapters})

    def upsert_branch_run(self, state: PipelineState, run: BranchRunRecord) -> PipelineState:
        runs = []
        replaced = False
        for item in state.branch_runs:
            if item.run_id == run.run_id:
                runs.append(run)
                replaced = True
            else:
                runs.append(item)
        if not replaced:
            runs.append(run)
        return state.model_copy(update={"branch_runs": runs})

    def update_branch_run(
        self,
        state: PipelineState,
        run_id: str,
        **updates: object,
    ) -> PipelineState:
        runs = [
            run.model_copy(update=updates) if run.run_id == run_id else run
            for run in state.branch_runs
        ]
        return state.model_copy(update={"branch_runs": runs})

    def add_branch_run_file_completed(
        self,
        state: PipelineState,
        run_id: str,
        filename: str,
    ) -> PipelineState:
        runs = []
        for run in state.branch_runs:
            if run.run_id != run_id:
                runs.append(run)
                continue
            files = list(run.outputs_completed)
            if filename not in files:
                files.append(filename)
            runs.append(run.model_copy(update={"outputs_completed": files}))
        return state.model_copy(update={"branch_runs": runs})
