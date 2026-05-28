"""Core orchestration engine: Step 0→1→2→3→4 loop."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from tree.agents.archivist import ArchivistAgent
from tree.agents.examiner import ExaminerAgent
from tree.agents.loader import AgentLoader
from tree.agents.student import StudentAgent
from tree.agents.writer import WriterAgent
from tree.config import Settings
from tree.deepseek.client import LLMClient
from tree.io import file_ops, git_ops, source_ops
from tree.observability.limiter import IterationLimiter
from tree.observability.logger import TraceLogger
from tree.state.manager import StateManager
from tree.state.models import (
    ArchitectResult,
    AuditResult,
    ExamSections,
    ExamTooBroadContext,
    IterationState,
    Route,
)


class TreeEngine:
    def __init__(self, settings: Settings):
        self.settings = settings
        root = settings.project_root
        self.client = LLMClient(settings)

        self.loader = AgentLoader()
        self.state_mgr = StateManager(root / "pipeline-state.json")
        self.examiner = ExaminerAgent(self.client, self.loader)
        self.student = StudentAgent(self.client, self.loader)
        self.writer = WriterAgent(self.client, self.loader)
        self.archivist = ArchivistAgent(self.client, self.loader)
        self.tracer = TraceLogger(root / "pipeline-temp" / "trace.jsonl")
        self.limiter = IterationLimiter(settings.max_iterations)
        self.rag_client = None
        self.rag_indexer = None
        self._init_rag()

    async def run(self) -> None:
        """Entry point for `tree run`."""
        self.tracer.log_pipeline_start()
        while True:
            state = self.state_mgr.load()
            chapter = self.state_mgr.find_in_progress(state)
            if chapter is None:
                # No in_progress chapter — scan for next
                scan_result = await self._scan_next_chapter(state)
                if scan_result is None:
                    self.tracer.log_pipeline_complete()
                    print("PIPELINE_COMPLETE — all source materials covered.")
                    return
                new_name = scan_result
                state = self.state_mgr.add_chapter(state, new_name, source_collection=new_name)
                self.state_mgr.save(state)
                chapter = self.state_mgr.find_in_progress(state)
                print(f"New chapter discovered: {new_name}")

            await self.process_chapter(chapter.chapter_name)

    async def ingest(
        self,
        input_path: Path,
        output_dir: Path,
        use_archivist: bool = True,
        collection: str | None = None,
        indexer: object | None = None,
    ) -> list[Path]:
        """Run the integrated PaddleOCR → Archivist ingest pipeline."""
        from tree.ingest import ingest_path

        return await ingest_path(
            input_path,
            output_dir,
            self.settings,
            archivist=self.archivist if use_archivist else None,
            collection=collection,
            indexer=indexer,
        )

    async def process_chapter(self, chapter_name: str) -> None:
        """Run Step 0→1→2→3→4 loop for one chapter until CHAPTER_COMPLETE."""
        while True:
            state = self.state_mgr.load()
            chapter = next(c for c in state.chapters if c.chapter_name == chapter_name)
            next_seq = str(len(chapter.files_completed) + 1).zfill(2)

            # Step 1: Examiner composes exam
            t0 = time.time()
            exam_sections, is_complete = await self._step1_compose(chapter, next_seq)
            self.tracer.log_step(
                "S1", chapter_name, next_seq, "examiner", "compose_exam",
                duration_ms=int((time.time() - t0) * 1000),
            )
            if is_complete:
                state = self.state_mgr.complete_chapter(state, chapter_name)
                self.state_mgr.save(state)
                print(f"CHAPTER_COMPLETE: {chapter_name}")
                return

            # Stash exam for iteration loop
            iter_state = IterationState(
                chapter=chapter_name,
                file_seq=next_seq,
                knowledge_point=exam_sections.knowledge_point,
                exam_sections=exam_sections,
            )
            print(f"Step 1: knowledge point = {exam_sections.knowledge_point}")

            # Step 2→3→4→2 loop
            await self._iteration_loop(iter_state, chapter_name)

    async def _iteration_loop(self, iter_state: IterationState, chapter_name: str) -> None:
        """Step 2→3→4→2 loop until PASS or iteration limit."""
        while True:
            iter_state.iteration += 1
            self.limiter.check(iter_state.chapter, iter_state.file_seq, iter_state.iteration)

            # Step 2: Student blind test
            t0 = time.time()
            answer = await self._step2_blind_test(iter_state)
            self.tracer.log_step(
                "S2", chapter_name, iter_state.file_seq, "student", "blind_test",
                duration_ms=int((time.time() - t0) * 1000),
                iteration=iter_state.iteration,
            )
            print(f"  Step 2: student answered (iteration {iter_state.iteration})")

            # Step 3: Examiner audit
            t0 = time.time()
            audit = await self._step3_audit(iter_state, answer)
            self.tracer.log_step(
                "S3", chapter_name, iter_state.file_seq, "examiner", "audit",
                duration_ms=int((time.time() - t0) * 1000),
                route=audit.route.value,
                iteration=iter_state.iteration,
            )

            if audit.route == Route.PASS:
                await self._handle_pass(iter_state, audit)
                print(f"  PASS: {iter_state.knowledge_point}")
                return

            print(f"  Step 3: FAIL_KNOWLEDGE_GAP (iteration {iter_state.iteration})")

            # Step 4: Writer creates/optimizes draft
            t0 = time.time()
            arch_result = await self._step4_writer(iter_state, audit)
            self.tracer.log_step(
                "S4", chapter_name, iter_state.file_seq, "writer",
                "optimize_draft" if iter_state.draft_path else "create_draft",
                duration_ms=int((time.time() - t0) * 1000),
                iteration=iter_state.iteration,
            )

            if arch_result.is_exam_too_broad:
                print("  Step 4: EXAM_TOO_BROAD — returning to Step 1")
                iter_state = await self._handle_exam_too_broad(iter_state, arch_result)
                continue

            arch_result = persist_writer_result(self.settings.project_root, iter_state, arch_result)
            # Draft written → back to Step 2 (same exam)
            iter_state.previous_bottleneck = audit.bottleneck_report
            iter_state.draft_path = arch_result.draft_path
            print("  Step 4: draft written → back to Step 2")

    # --- Step implementations ---

    async def _step1_compose(
        self,
        chapter: object,
        next_seq: str,
        exam_too_broad_ctx: ExamTooBroadContext | None = None,
    ) -> tuple[ExamSections, bool]:
        from tree.state.models import ChapterRecord

        ch = chapter if isinstance(chapter, ChapterRecord) else None
        ch_name = ch.chapter_name if ch else getattr(chapter, "chapter_name", "")
        source_collection = (
            ch.source_collection if ch and ch.source_collection else ch_name
        )
        prior_paths = [str(p) for p in file_ops.list_prior_paths(self.settings.project_root, ch_name)]
        prior_contents = file_ops.read_prior_files(self.settings.project_root, ch_name)
        source_docs = source_ops.read_collection(self.settings.project_root, source_collection)
        retrieved_context = self._rag_query(
            f"{ch_name}\n{next_seq}\n下一知识点命题",
            filters={
                "content_kind": "source",
                "source_collection": source_collection,
            },
            top_k=8,
            include_drafts=False,
        )
        return await self.examiner.compose_exam(
            next_seq,
            prior_contents,
            prior_paths,
            source_material_contents=[doc.content for doc in source_docs],
            source_material_paths=[str(doc.path) for doc in source_docs],
            retrieved_context=retrieved_context,
            exam_too_broad_ctx=exam_too_broad_ctx,
        )

    async def _step2_blind_test(self, iter_state: IterationState) -> str:
        prior_paths = [str(p) for p in file_ops.list_prior_paths(self.settings.project_root, iter_state.chapter)]
        prior_contents = file_ops.read_prior_files(self.settings.project_root, iter_state.chapter)
        draft_text = None
        if iter_state.draft_path and iter_state.draft_path.exists():
            draft_text = iter_state.draft_path.read_text(encoding="utf-8")
        assert iter_state.exam_sections is not None
        return await self.student.blind_test(
            iter_state.exam_sections.blind_exam,
            iter_state.exam_sections.student_instructions,
            prior_contents,
            prior_paths,
            draft_text,
        )

    async def _step3_audit(self, iter_state: IterationState, answer: str) -> AuditResult:
        prior_paths = [str(p) for p in file_ops.list_prior_paths(self.settings.project_root, iter_state.chapter)]
        prior_contents = file_ops.read_prior_files(self.settings.project_root, iter_state.chapter)
        draft_text = None
        if iter_state.draft_path and iter_state.draft_path.exists():
            draft_text = iter_state.draft_path.read_text(encoding="utf-8")
        assert iter_state.exam_sections is not None
        return await self.examiner.audit(
            iter_state.exam_sections.blind_exam,
            iter_state.exam_sections.answer_key,
            answer,
            draft_text,
            prior_contents,
            prior_paths,
            iter_state.previous_bottleneck,
        )

    async def _step4_writer(self, iter_state: IterationState, audit: AuditResult) -> ArchitectResult:
        prior_paths = [str(p) for p in file_ops.list_prior_paths(self.settings.project_root, iter_state.chapter)]
        prior_contents = file_ops.read_prior_files(self.settings.project_root, iter_state.chapter)
        draft_text = None
        if iter_state.draft_path and iter_state.draft_path.exists():
            draft_text = iter_state.draft_path.read_text(encoding="utf-8")
        arch_instructions = iter_state.exam_sections.architect_instructions if iter_state.exam_sections else None
        source_collection = self._source_collection_for_chapter(iter_state.chapter)
        query_text = f"{iter_state.knowledge_point}\n{audit.bottleneck_report}"
        retrieved_context = (
            self._rag_query(
                query_text,
                filters={
                    "content_kind": "source",
                    "source_collection": source_collection,
                },
                top_k=5,
                include_drafts=False,
            )
            + self._rag_query(
                query_text,
                filters={
                    "content_kind": "finished",
                    "chapter": iter_state.chapter,
                },
                top_k=5,
                include_drafts=False,
            )
        )
        return await self.writer.create_or_optimize(
            iter_state.knowledge_point,
            iter_state.file_seq,
            audit.bottleneck_report,
            prior_contents,
            prior_paths,
            draft_text,
            iter_state.previous_bottleneck,
            arch_instructions,
            retrieved_context=retrieved_context,
        )

    # --- Handlers ---

    async def _handle_pass(self, iter_state: IterationState, audit: AuditResult) -> None:
        """Move draft to finished_outputs, update state."""
        if iter_state.draft_path and iter_state.draft_path.exists():
            filename = iter_state.draft_path.name
            dst = file_ops.move_draft_to_finished(
                self.settings.project_root, iter_state.chapter, filename
            )
            git_ops.git_add_commit(
                dst,
                f"docs({filename}): PASS — {iter_state.knowledge_point}",
                cwd=self.settings.project_root,
            )
            if rag_indexer := getattr(self, "rag_indexer", None):
                try:
                    rag_indexer.index_finished_file(
                        self.settings.project_root,
                        iter_state.chapter,
                        dst,
                    )
                except Exception:
                    pass
            state = self.state_mgr.load()
            state = self.state_mgr.add_file_completed(state, iter_state.chapter, filename)
            self.state_mgr.save(state)

    async def _handle_exam_too_broad(
        self, iter_state: IterationState, arch_result: ArchitectResult
    ) -> IterationState:
        """Return to Step 1 with narrowed scope."""
        exam_sections, _ = await self._step1_compose(
            type("Ch", (), {"chapter_name": iter_state.chapter})(),
            iter_state.file_seq,
            ExamTooBroadContext(
                bloat_description=arch_result.bloat_description,
                knowledge_point_name=iter_state.knowledge_point,
            ),
        )
        iter_state.exam_sections = exam_sections
        iter_state.knowledge_point = exam_sections.knowledge_point
        iter_state.previous_bottleneck = None
        return iter_state

    async def _scan_next_chapter(self, state: object) -> str | None:
        """Scan source materials for next chapter. Returns chapter name or None."""
        from tree.state.models import PipelineState

        s = state if isinstance(state, PipelineState) else None
        state_text = s.model_dump_json(indent=2) if s else "{}"
        collections = source_ops.read_all_collections(self.settings.project_root)
        source_payload = {
            name: [{"path": str(doc.path), "content": doc.content} for doc in docs]
            for name, docs in collections.items()
        }
        name, is_complete = await self.examiner.scan_next_chapter(
            state_text, source_payload
        )
        if is_complete:
            return None
        return name

    async def close(self) -> None:
        await self.client.close()

    def _init_rag(self) -> None:
        """Initialize optional local RAG components when dependencies are installed."""
        try:
            from tree.rag.client import RAGClient
            from tree.rag.indexer import RAGIndexer
        except ImportError:
            return

        try:
            self.rag_client = RAGClient(store_path=self.settings.project_root / "rag-store")
            self.rag_indexer = RAGIndexer(self.rag_client)
        except Exception:
            self.rag_client = None
            self.rag_indexer = None

    def _rag_query(
        self,
        query_text: str,
        filters: dict[str, Any],
        top_k: int = 5,
        include_drafts: bool = True,
    ) -> list[dict]:
        rag_client = getattr(self, "rag_client", None)
        if rag_client is None:
            return []
        try:
            return rag_client.query(
                query_text,
                top_k=top_k,
                filters=filters,
                include_drafts=include_drafts,
            )
        except Exception:
            return []

    def _source_collection_for_chapter(self, chapter_name: str) -> str:
        state_mgr = getattr(self, "state_mgr", None)
        if state_mgr is None:
            return chapter_name
        try:
            state = state_mgr.load()
        except Exception:
            return chapter_name
        for chapter in state.chapters:
            if chapter.chapter_name == chapter_name:
                return chapter.source_collection or chapter.chapter_name
        return chapter_name


def persist_writer_result(
    root: Path,
    iter_state: IterationState,
    arch_result: ArchitectResult,
) -> ArchitectResult:
    """Persist writer Markdown into drafts/{chapter}/{NN}.{title}.md."""
    if arch_result.is_exam_too_broad:
        return arch_result
    if not arch_result.draft_content.strip():
        raise ValueError("Writer returned no draft content")

    filename = _draft_filename(iter_state.file_seq, iter_state.knowledge_point)
    draft_path = file_ops.write_draft(
        root,
        iter_state.chapter,
        filename,
        arch_result.draft_content,
    )
    return arch_result.model_copy(update={"draft_path": draft_path})


def _draft_filename(file_seq: str, knowledge_point: str) -> str:
    title = knowledge_point.strip()
    title = re.sub(r"^\s*\d+\s*[.．、-]\s*", "", title)
    title = re.sub(r"[\\/:\*\?\"<>\|]", "-", title)
    title = re.sub(r"\s+", "", title)
    if not title:
        title = "未命名知识点"
    return f"{file_seq}.{title}.md"
