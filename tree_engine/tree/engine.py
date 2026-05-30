"""Core orchestration engine: Step 0→1→2→3→4 loop."""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from tree.agents.archivist import ArchivistAgent
from tree.agents.examiner import ExaminerAgent
from tree.agents.loader import AgentLoader
from tree.agents.student import StudentAgent
from tree.agents.writer import WriterAgent, sanitize_writer_context
from tree.config import Settings
from tree.curriculum.chapter_naming import (
    build_chapter_naming_context,
    fallback_chapter_title,
    next_tree_id,
)
from tree.curriculum.inventory import (
    load_inventory,
    rebuild_source_inventory_with_ai,
)
from tree.curriculum.candidate_nodes import (
    load_candidate_nodes,
    rebuild_candidate_nodes_with_ai,
)
from tree.curriculum.branches import (
    build_branch_prior_scope,
    branch_context_for_run,
    load_knowledge_dag,
    load_knowledge_branches,
    rebuild_branch_plan,
    start_ready_branch_runs,
    validate_branch_covered_node_ids,
)
from tree.curriculum.graph import (
    build_selected_node_context,
    load_knowledge_graph,
    rebuild_knowledge_graph,
    rebuild_knowledge_graph_with_ai,
)
from tree.curriculum.ledger import (
    duplicate_brief,
    format_duplicate_brief,
    format_scoped_ledger_context,
    reconcile_finished_outputs,
    update_finished_record,
)
from tree.model.client import LLMClient
from tree.io import file_ops, git_ops, paths, source_ops
from tree.observability.limiter import IterationLimiter
from tree.observability.logger import TraceLogger
from tree.observability.progress import ProgressTracker
from tree.services import stop_requested
from tree.state.manager import StateManager
from tree.state.models import (
    AuditResult,
    ExamSections,
    IterationState,
    PipelineState,
    Route,
    WriterResult,
)

RAW_MATERIAL_EXTENSIONS = {
    ".bmp",
    ".docx",
    ".jpeg",
    ".jpg",
    ".md",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".tif",
    ".tiff",
    ".txt",
    ".webp",
}


class StopRequested(Exception):
    """Raised when a background stop request reaches a safe checkpoint."""


class TreeEngine:
    def __init__(self, settings: Settings):
        self.settings = settings
        root = settings.project_root
        self.client = LLMClient(settings)

        self.loader = AgentLoader()
        self.state_mgr = StateManager(paths.pipeline_state_path(root))
        self.examiner = ExaminerAgent(
            self.client,
            self.loader,
            max_format_retries=settings.max_format_retries,
            project_root=root,
        )
        self.student = StudentAgent(self.client, self.loader)
        self.writer = WriterAgent(self.client, self.loader)
        self.archivist = ArchivistAgent(self.client, self.loader)
        self.tracer = TraceLogger(paths.pipeline_temp_root(root) / "trace.jsonl")
        self.progress = ProgressTracker(root)
        self.limiter = IterationLimiter(settings.max_iterations)
        self.coverage_update_lock = asyncio.Lock()
        self.rag_client = None
        self.rag_indexer = None
        self._init_rag()

    async def run(self) -> None:
        """Entry point for `tree run`."""
        self.tracer.log_pipeline_start()
        self.progress.reset()
        self._raise_if_stop_requested()
        reconcile_finished_outputs(self.settings.project_root)
        await self._prepare_source_materials_for_loop()
        await self._rebuild_source_inventory_from_rag()
        while True:
            self._raise_if_stop_requested()
            state = self.state_mgr.load()
            state = self._activate_ready_branch_runs(state)
            in_progress = self.state_mgr.find_in_progress_all(state)
            if not in_progress:
                # No active BranchRun execution; refresh planner artifacts and schedule ready branches.
                self.progress.learning_stage(
                    stage="refresh_branch_plan",
                    stage_label="Refreshing branch plan",
                    stage_index=1,
                    stage_total=6,
                    message="Planner is rebuilding KnowledgeNodes, DAG, and ready branches",
                )
                await self._refresh_planner_artifacts(state)
                state = self._activate_ready_branch_runs(self.state_mgr.load())
                in_progress = self.state_mgr.find_in_progress_all(state)
                if in_progress:
                    await asyncio.gather(
                        *[
                            self.process_chapter(item.chapter_name)
                            for item in in_progress[: self.settings.max_active_branch_runs]
                        ]
                    )
                    continue
                blockage = _branch_plan_blockage(load_knowledge_branches(self.settings.project_root))
                if blockage:
                    message = f"TREE_BLOCKED — {blockage}"
                    self.progress.update({"phase": "blocked", "message": message})
                    print(message)
                    return
                state = await self._name_completed_chapters(state)
                self.state_mgr.save(state)
                self.tracer.log_pipeline_complete()
                self.progress.complete("WOODS_COMPLETE — all source nodes covered.")
                print("WOODS_COMPLETE — all source nodes covered.")
                return

            await asyncio.gather(
                *[
                    self.process_chapter(item.chapter_name)
                    for item in in_progress[: self.settings.max_active_branch_runs]
                ]
            )

    async def ingest(
        self,
        input_path: Path,
        output_dir: Path,
        use_archivist: bool = True,
        collection: str | None = None,
        indexer: object | None = None,
        track_files: bool = True,
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
            progress=self.progress,
            track_files=track_files,
        )

    async def _prepare_source_materials_for_loop(self) -> None:
        """Ingest new uploads and ensure every source material is embedded."""
        root = self.settings.project_root
        self._raise_if_stop_requested()
        manifest = _load_source_manifest(root)
        _refresh_manifest_index_status(root, manifest, getattr(self, "rag_indexer", None))
        pending = _pending_materials(root, manifest)

        if pending:
            self.progress.source_ingest_start(len(pending))
            print(f"Source ingest: {len(pending)} new or changed material(s) detected.")
        if pending:
            ingest_sem = asyncio.Semaphore(max(1, self.settings.source_ingest_concurrency))
            embedding_sem = asyncio.Semaphore(max(1, self.settings.source_embedding_concurrency))
            manifest_lock = asyncio.Lock()
            embedding_tasks: list[asyncio.Task[int]] = []
            files_done = 0
            embedding_done = 0
            embedding_total = 0

            async def ingest_one(material_path: Path, collection: str) -> tuple[Path, str, list[Path]]:
                async with ingest_sem:
                    output_dir = source_ops.source_root(root) / collection
                    outputs = await self.ingest(
                        material_path,
                        output_dir,
                        use_archivist=True,
                        collection=collection,
                        indexer=None,
                        track_files=False,
                    )
                    return material_path, collection, outputs

            async def embed_outputs(material_path: Path, collection: str, outputs: list[Path]) -> int:
                nonlocal embedding_done
                indexed = 0
                for output in outputs:
                    self._raise_if_stop_requested()
                    async with embedding_sem:
                        indexed += await asyncio.to_thread(
                            _index_source_output,
                            root,
                            collection,
                            output,
                            getattr(self, "rag_indexer", None),
                        )
                        async with manifest_lock:
                            embedding_done += 1
                            self.progress.embedding_done(
                                _relative_to_root(root, output),
                                embedding_done,
                                embedding_total,
                            )
                async with manifest_lock:
                    _mark_material_embedded(root, manifest, material_path)
                    _save_source_manifest(root, manifest)
                return indexed

            tasks = [
                asyncio.create_task(ingest_one(material_path, collection))
                for material_path, collection in pending
            ]
            for task in asyncio.as_completed(tasks):
                self._raise_if_stop_requested()
                material_path, collection, outputs = await task
                async with manifest_lock:
                    files_done += 1
                    _mark_material_ingested(root, manifest, material_path, collection, outputs)
                    _save_source_manifest(root, manifest)
                    self.progress.source_file_done(
                        _relative_to_root(root, material_path),
                        files_done,
                        len(pending),
                    )
                    embedding_total += len(outputs)
                    self.progress.embedding_start(embedding_total)
                if outputs:
                    embedding_tasks.append(asyncio.create_task(embed_outputs(material_path, collection, outputs)))

            embedded_count = sum(await asyncio.gather(*embedding_tasks)) if embedding_tasks else 0
            if embedded_count:
                print(f"Source ingest: embedded {embedded_count} source material file(s).")

        self._ensure_all_source_materials_embedded()
        _refresh_manifest_index_status(root, manifest, getattr(self, "rag_indexer", None))
        _save_source_manifest(root, manifest)

    def _ensure_all_source_materials_embedded(self) -> None:
        """Block the TREE loop until all structured source materials are indexed."""
        root = self.settings.project_root
        self._raise_if_stop_requested()
        collections = source_ops.read_all_collections(root)
        docs = [(collection, doc.path) for collection, items in collections.items() for doc in items]
        if not docs:
            return

        indexer = getattr(self, "rag_indexer", None)
        if indexer is None:
            raise RuntimeError(
                "Source materials exist but RAG indexer is unavailable. "
                "Start the embedding service and ensure RAG dependencies are installed before running TREE."
            )

        indexed = 0
        docs_done = 0
        self.progress.embedding_start(len(docs))
        for collection, path in docs:
            self._raise_if_stop_requested()
            indexed += _index_source_output(root, collection, path, indexer)
            docs_done += 1
            self.progress.embedding_done(_relative_to_root(root, path), docs_done, len(docs))
        if indexed:
            print(f"Source ingest: embedded {indexed} source material file(s).")

    async def process_chapter(self, chapter_name: str) -> None:
        """Run the BranchRun exam-writing loop for one declared branch span."""
        while True:
            self._raise_if_stop_requested()
            state = self.state_mgr.load()
            chapter = next(c for c in state.chapters if c.chapter_name == chapter_name)
            state = self._reconcile_finished_outputs(state, chapter_name)
            chapter = next(c for c in state.chapters if c.chapter_name == chapter_name)
            next_seq = str(len(chapter.files_completed) + 1).zfill(2)

            # Step 1: Examiner composes exam
            self.progress.learning_stage(
                stage="find_knowledge_point",
                stage_label="Finding knowledge point",
                stage_index=1,
                stage_total=6,
                chapter=chapter_name,
                file_seq=next_seq,
                message="Examiner is finding the next knowledge point",
            )
            t0 = time.time()
            exam_sections, is_complete = await self._step1_compose(chapter, next_seq)
            self._raise_if_stop_requested()
            self.tracer.log_step(
                "S1", chapter_name, next_seq, "examiner", "compose_exam",
                duration_ms=int((time.time() - t0) * 1000),
            )
            if is_complete:
                raise RuntimeError(
                    "Examiner attempted to complete a tree during exam assembly. "
                    "Tree completion is planner-controlled."
                )

            # Stash exam for iteration loop
            iter_state = IterationState(
                chapter=chapter_name,
                file_seq=next_seq,
                knowledge_point=exam_sections.knowledge_point,
                covered_node_ids=list(exam_sections.covered_node_ids),
                exam_sections=exam_sections,
            )
            self.progress.learning_stage(
                stage="examiner_compose_exam",
                stage_label="Examiner composed exam",
                stage_index=2,
                stage_total=6,
                chapter=chapter_name,
                file_seq=next_seq,
                knowledge_point=exam_sections.knowledge_point,
                message="Examiner has selected the knowledge point and exam",
            )
            print(f"Step 1: knowledge point = {exam_sections.knowledge_point}")

            # Step 2→3→4→2 loop
            await self._iteration_loop(iter_state, chapter_name)
            self._mark_active_node_complete(chapter_name)
            return

    async def _iteration_loop(self, iter_state: IterationState, chapter_name: str) -> None:
        """Step 2→3→4→2 loop until PASS or iteration limit."""
        while True:
            self._raise_if_stop_requested()
            iter_state.iteration += 1
            self.limiter.check(iter_state.chapter, iter_state.file_seq, iter_state.iteration)

            # Step 2: Student blind test
            self.progress.learning_stage(
                stage="student_blind_test",
                stage_label="Student blind test",
                stage_index=3,
                stage_total=6,
                chapter=chapter_name,
                file_seq=iter_state.file_seq,
                knowledge_point=iter_state.knowledge_point,
                iteration=iter_state.iteration,
                message="Student is answering the blind exam",
            )
            t0 = time.time()
            answer = await self._step2_blind_test(iter_state)
            self._raise_if_stop_requested()
            self.tracer.log_step(
                "S2", chapter_name, iter_state.file_seq, "student", "blind_test",
                duration_ms=int((time.time() - t0) * 1000),
                iteration=iter_state.iteration,
            )
            print(f"  Step 2: student answered (iteration {iter_state.iteration})")

            # Step 3: Examiner audit
            self.progress.learning_stage(
                stage="examiner_audit",
                stage_label="Examiner audit",
                stage_index=4,
                stage_total=6,
                chapter=chapter_name,
                file_seq=iter_state.file_seq,
                knowledge_point=iter_state.knowledge_point,
                iteration=iter_state.iteration,
                message="Examiner is auditing the student answer",
            )
            t0 = time.time()
            audit = await self._step3_audit(iter_state, answer)
            self._raise_if_stop_requested()
            self.tracer.log_step(
                "S3", chapter_name, iter_state.file_seq, "examiner", "audit",
                duration_ms=int((time.time() - t0) * 1000),
                route=audit.route.value,
                iteration=iter_state.iteration,
            )

            if audit.route == Route.PASS:
                self.progress.learning_stage(
                    stage="pass_save_output",
                    stage_label="PASS and save output",
                    stage_index=6,
                    stage_total=6,
                    chapter=chapter_name,
                    file_seq=iter_state.file_seq,
                    knowledge_point=iter_state.knowledge_point,
                    iteration=iter_state.iteration,
                    message="Knowledge point passed; saving output",
                )
                await self._handle_pass(iter_state, audit)
                print(f"  PASS: {iter_state.knowledge_point}")
                return

            print(f"  Step 3: FAIL_KNOWLEDGE_GAP (iteration {iter_state.iteration})")

            # Step 4: Writer creates/optimizes draft
            self.progress.learning_stage(
                stage="writer_drafting",
                stage_label="Writer drafting",
                stage_index=5,
                stage_total=6,
                chapter=chapter_name,
                file_seq=iter_state.file_seq,
                knowledge_point=iter_state.knowledge_point,
                iteration=iter_state.iteration,
                message="Writer is creating or optimizing the draft",
            )
            t0 = time.time()
            writer_result = await self._step4_writer(iter_state, audit)
            self._raise_if_stop_requested()
            self.tracer.log_step(
                "S4", chapter_name, iter_state.file_seq, "writer",
                "optimize_draft" if iter_state.draft_path else "create_draft",
                duration_ms=int((time.time() - t0) * 1000),
                iteration=iter_state.iteration,
            )

            writer_result = persist_writer_result(self.settings.project_root, iter_state, writer_result)
            # Draft written → back to Step 2 (same exam)
            iter_state.previous_bottleneck = audit.bottleneck_report
            iter_state.draft_path = writer_result.draft_path
            print("  Step 4: draft written → back to Step 2")

    # --- Step implementations ---

    async def _step1_compose(
        self,
        chapter: object,
        next_seq: str,
    ) -> tuple[ExamSections, bool]:
        from tree.state.models import ChapterRecord

        ch = chapter if isinstance(chapter, ChapterRecord) else None
        ch_name = ch.chapter_name if ch else getattr(chapter, "chapter_name", "")
        source_collections = _chapter_source_collections(ch) if ch else []
        if not source_collections:
            source_collection = getattr(chapter, "source_collection", None)
            source_collections = [source_collection] if source_collection else []
        if not source_collections and ch_name:
            source_collections = self._source_collections_for_chapter(ch_name)
        prior_paths, prior_contents, allowed_paths = self._prior_finished_context(ch_name)
        query_text = f"{ch_name}\n{next_seq}\n下一知识点命题"
        duplicate_context = (
            format_scoped_ledger_context(self.settings.project_root, allowed_paths)
            + "\n\n"
            + format_duplicate_brief(
                duplicate_brief(self.settings.project_root, query_text, allowed_paths=allowed_paths)
            )
        )
        source_filters = {"content_kind": "source"}
        if source_collections:
            source_filters["source_collection"] = source_collections
        retrieved_context = (
            self._rag_query(
                query_text,
                filters=source_filters,
                top_k=5,
                include_drafts=False,
            )
            + self._finished_rag_query(
                query_text,
                top_k=8,
                allowed_paths=allowed_paths,
            )
        )
        if duplicate_context:
            retrieved_context.append(
                {
                    "text": duplicate_context,
                    "score": 1.0,
                    "metadata": {
                        "content_kind": "ledger",
                        "path": "knowledge-ledger",
                    },
                }
            )
        exam_sections, is_complete = await self.examiner.compose_exam(
            next_seq,
            prior_contents,
            prior_paths,
            source_material_contents=[],
            source_material_paths=self._source_paths_from_rag(source_collections),
            retrieved_context=retrieved_context,
            graph_context=self._active_chapter_graph_context(ch),
        )
        if ch is not None and exam_sections is not None:
            exam_sections = self._validate_exam_covered_nodes(ch, exam_sections)
        return exam_sections, is_complete

    async def _step2_blind_test(self, iter_state: IterationState) -> str:
        prior_paths, prior_contents, allowed_paths = _prior_finished_context_for_engine(
            self,
            iter_state.chapter,
            iter_state.covered_node_ids,
        )
        draft_text = None
        if iter_state.draft_path and iter_state.draft_path.exists():
            draft_text = iter_state.draft_path.read_text(encoding="utf-8")
        assert iter_state.exam_sections is not None
        query_text = (
            f"{iter_state.knowledge_point}\n"
            f"{iter_state.exam_sections.blind_exam}"
        )
        retrieved_context = self._finished_rag_query(
            query_text,
            top_k=6,
            allowed_paths=allowed_paths,
        )
        return await self.student.blind_test(
            iter_state.exam_sections.blind_exam,
            prior_contents,
            prior_paths,
            draft_text,
            retrieved_context=retrieved_context,
        )

    async def _step3_audit(self, iter_state: IterationState, answer: str) -> AuditResult:
        prior_paths, prior_contents, allowed_paths = _prior_finished_context_for_engine(
            self,
            iter_state.chapter,
            iter_state.covered_node_ids,
        )
        draft_text = None
        if iter_state.draft_path and iter_state.draft_path.exists():
            draft_text = iter_state.draft_path.read_text(encoding="utf-8")
        assert iter_state.exam_sections is not None
        query_text = f"{iter_state.knowledge_point}\n{iter_state.exam_sections.blind_exam}\n{answer}"
        source_filters = {"content_kind": "source"}
        source_collections = self._source_collections_for_chapter(iter_state.chapter)
        if source_collections:
            source_filters["source_collection"] = source_collections
        retrieved_context = (
            self._finished_rag_query(
                query_text,
                top_k=6,
                allowed_paths=allowed_paths,
            )
            + self._rag_query(
                query_text,
                filters=source_filters,
                top_k=5,
                include_drafts=False,
            )
        )
        state_mgr = getattr(self, "state_mgr", None)
        chapter = None
        if state_mgr is not None:
            state = state_mgr.load()
            chapter = next((ch for ch in state.chapters if ch.chapter_name == iter_state.chapter), None)
        graph_context_method = getattr(self, "_active_chapter_graph_context", None)
        graph_context = graph_context_method(chapter) if graph_context_method else None
        return await self.examiner.audit(
            iter_state.exam_sections.blind_exam,
            iter_state.exam_sections.answer_key,
            answer,
            draft_text,
            prior_contents,
            prior_paths,
            iter_state.previous_bottleneck,
            retrieved_context=retrieved_context,
            graph_context=graph_context,
        )

    async def _step4_writer(self, iter_state: IterationState, audit: AuditResult) -> WriterResult:
        prior_paths, prior_contents, allowed_paths = _prior_finished_context_for_engine(
            self,
            iter_state.chapter,
            iter_state.covered_node_ids,
        )
        draft_text = None
        if iter_state.draft_path and iter_state.draft_path.exists():
            draft_text = iter_state.draft_path.read_text(encoding="utf-8")
        writer_instructions = iter_state.exam_sections.writer_instructions if iter_state.exam_sections else None
        state = self.state_mgr.load()
        chapter = next((ch for ch in state.chapters if ch.chapter_name == iter_state.chapter), None)
        source_collections = self._source_collections_for_chapter(iter_state.chapter)
        writer_bottleneck = sanitize_writer_context(audit.bottleneck_report)
        query_text = f"{iter_state.knowledge_point}\n{writer_bottleneck}"
        delta_brief = format_duplicate_brief(
            duplicate_brief(self.settings.project_root, iter_state.knowledge_point, allowed_paths=allowed_paths)
        )
        source_filters = {"content_kind": "source"}
        if source_collections:
            source_filters["source_collection"] = source_collections
        retrieved_context = (
            self._rag_query(
                query_text,
                filters=source_filters,
                top_k=5,
                include_drafts=False,
            )
            + self._finished_rag_query(
                query_text,
                top_k=8,
                allowed_paths=allowed_paths,
            )
        )
        retrieved_context.append(
            {
                "text": delta_brief,
                "score": 1.0,
                "metadata": {
                    "content_kind": "ledger",
                    "path": "delta-brief",
                },
            }
        )
        return await self.writer.create_or_optimize(
            iter_state.knowledge_point,
            iter_state.file_seq,
            writer_bottleneck,
            prior_contents,
            prior_paths,
            draft_text,
            iter_state.previous_bottleneck,
            writer_instructions,
            retrieved_context=retrieved_context,
            graph_context=self._active_chapter_graph_context(chapter),
        )

    # --- Handlers ---

    async def _handle_pass(self, iter_state: IterationState, audit: AuditResult | None) -> Path:
        """Move draft to outputs, update state."""
        lock = getattr(self, "coverage_update_lock", None)
        if lock is None:
            return TreeEngine._handle_pass_locked(self, iter_state, audit)
        async with lock:
            return TreeEngine._handle_pass_locked(self, iter_state, audit)

    def _handle_pass_locked(self, iter_state: IterationState, audit: AuditResult | None) -> Path:
        """Move draft to outputs and commit coverage state under the global lock."""
        if not iter_state.draft_path or not iter_state.draft_path.exists():
            raise RuntimeError(
                "Cannot PASS without a persisted draft. "
                "The writer must create a draft before examiner PASS can be accepted."
            )
        filename = iter_state.draft_path.name
        dst = file_ops.move_draft_to_finished(
            self.settings.project_root, iter_state.chapter, filename
        )
        self._index_finished_output_or_raise(iter_state.chapter, dst)
        git_ops.git_add_commit(
            dst,
            f"docs({filename}): PASS — {iter_state.knowledge_point}",
            cwd=self.settings.project_root,
        )
        state = self.state_mgr.load()
        chapter = next((ch for ch in state.chapters if ch.chapter_name == iter_state.chapter), None)
        update_finished_record(
            self.settings.project_root,
            iter_state.chapter,
            dst,
            graph_node_id=(iter_state.covered_node_ids or [getattr(chapter, "graph_node_id", None)])[0],
            covered_node_ids=iter_state.covered_node_ids,
            required_nodes=getattr(chapter, "required_nodes", None) or [],
            source_collections=_chapter_source_collections(chapter),
            hit_chunks=_chapter_graph_hit_chunks(
                self.settings.project_root,
                chapter,
                node_ids=iter_state.covered_node_ids,
            ),
        )
        state = self.state_mgr.add_file_completed(state, iter_state.chapter, filename)
        if chapter and chapter.branch_run_id:
            state = self.state_mgr.add_branch_run_file_completed(
                state,
                chapter.branch_run_id,
                filename,
            )
        self.state_mgr.save(state)
        self._refresh_knowledge_graph_from_ledger()
        return dst

    def _mark_active_node_complete(self, chapter_name: str) -> None:
        """Close the active branch execution so the scheduler can unlock downstream branches."""
        state = self.state_mgr.load()
        chapter = next((ch for ch in state.chapters if ch.chapter_name == chapter_name), None)
        if chapter and chapter.branch_run_id and chapter.branch_id:
            branches_doc = load_knowledge_branches(self.settings.project_root)
            branch = next(
                (
                    item
                    for item in branches_doc.get("branches", [])
                    if isinstance(item, dict) and item.get("branch_id") == chapter.branch_id
                ),
                {},
            )
            missing = _string_list((branch.get("coverage") or {}).get("missing_node_ids"))
            if missing:
                state = self.state_mgr.reopen_chapter(
                    state,
                    chapter_name,
                    source_collection=chapter.source_collection,
                    source_collections=chapter.source_collections,
                    graph_node_id=missing[0],
                    required_nodes=missing,
                    branch_id=chapter.branch_id,
                    branch_run_id=chapter.branch_run_id,
                )
                run = next(
                    (item for item in state.branch_runs if item.run_id == chapter.branch_run_id),
                    None,
                )
                if run is not None:
                    state = self.state_mgr.update_branch_run(
                        state,
                        chapter.branch_run_id,
                        current_iteration=run.current_iteration + 1,
                    )
                self.state_mgr.save(state)
                return
        state = self.state_mgr.complete_chapter(state, chapter_name)
        if chapter and chapter.branch_run_id:
            state = self.state_mgr.update_branch_run(
                state,
                chapter.branch_run_id,
                status="complete",
            )
        self.state_mgr.save(state)

    def _refresh_knowledge_graph_from_ledger(self) -> dict[str, Any]:
        """Refresh the persisted graph without re-running AI candidate extraction."""
        ledger = reconcile_finished_outputs(self.settings.project_root)
        candidate_nodes = load_candidate_nodes(self.settings.project_root)
        graph = rebuild_knowledge_graph(self.settings.project_root, candidate_nodes, ledger)
        running = {
            run.branch_id
            for run in self.state_mgr.load().branch_runs
            if run.status == "running"
        }
        rebuild_branch_plan(
            self.settings.project_root,
            graph,
            ledger,
            running_branch_ids=running,
        )
        return graph

    def _activate_ready_branch_runs(self, state: PipelineState) -> PipelineState:
        """Create BranchRun-backed chapters for every ready executable branch."""
        graph = load_knowledge_graph(self.settings.project_root)
        if not graph.get("nodes"):
            return state
        ledger = reconcile_finished_outputs(self.settings.project_root)
        running = {run.branch_id for run in state.branch_runs if run.status == "running"}
        plan = rebuild_branch_plan(
            self.settings.project_root,
            graph,
            ledger,
            running_branch_ids=running,
        )
        before = {run.run_id for run in state.branch_runs}
        updated = start_ready_branch_runs(
            state,
            plan["branches"],
            ledger,
            max_active_branch_runs=self.settings.max_active_branch_runs,
            now=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        if len(updated.branch_runs) == len(state.branch_runs):
            return updated
        branches_by_id = {
            branch.get("branch_id"): branch
            for branch in plan["branches"].get("branches", [])
            if isinstance(branch, dict)
        }
        with_chapters = updated
        for run in updated.branch_runs:
            if run.run_id in before or run.execution_path:
                continue
            branch = branches_by_id.get(run.branch_id, {})
            tree_id = _tree_id_for_branch_execution(branch, with_chapters, ledger)
            chapter_name = f"{tree_id}/{_branch_chapter_slug(run.branch_id)}"
            coverage_nodes = list(branch.get("coverage_node_ids") or [])
            source_collections = _string_list(branch.get("source_collections"))
            with_chapters = self.state_mgr.add_chapter(
                with_chapters,
                chapter_name,
                source_collection=source_collections[0] if source_collections else None,
                source_collections=source_collections,
                graph_node_id=coverage_nodes[0] if coverage_nodes else branch.get("start_node_id"),
                required_nodes=coverage_nodes,
                provisional_chapter_title=f"Branch {run.branch_id}",
                branch_id=run.branch_id,
                branch_run_id=run.run_id,
            )
            with_chapters = self.state_mgr.update_branch_run(
                with_chapters,
                run.run_id,
                execution_path=chapter_name,
                tree_id=tree_id,
            )
        self.state_mgr.save(with_chapters)
        return with_chapters

    def _reconcile_finished_outputs(self, state: PipelineState, chapter_name: str) -> PipelineState:
        """Record finished output files that were saved before a previous crash."""
        chapter = next((ch for ch in state.chapters if ch.chapter_name == chapter_name), None)
        if chapter is None:
            return state
        completed = set(chapter.files_completed)
        output_dir = paths.outputs_root(self.settings.project_root) / chapter_name
        if not output_dir.exists():
            return state

        reconciled = state
        changed = False
        for path in sorted(output_dir.glob("*.md")):
            if path.name in completed:
                continue
            self._index_finished_output_or_raise(chapter_name, path)
            reconciled = self.state_mgr.add_file_completed(reconciled, chapter_name, path.name)
            if chapter.branch_run_id:
                reconciled = self.state_mgr.add_branch_run_file_completed(
                    reconciled,
                    chapter.branch_run_id,
                    path.name,
                )
            update_finished_record(
                self.settings.project_root,
                chapter_name,
                path,
                graph_node_id=getattr(chapter, "graph_node_id", None),
                required_nodes=getattr(chapter, "required_nodes", None) or [],
                source_collections=_chapter_source_collections(chapter),
                hit_chunks=_chapter_graph_hit_chunks(self.settings.project_root, chapter),
            )
            completed.add(path.name)
            changed = True
        if changed:
            self.state_mgr.save(reconciled)
        return reconciled

    def _index_finished_output_or_raise(self, chapter: str, path: Path) -> int:
        rag_indexer = getattr(self, "rag_indexer", None)
        if rag_indexer is None:
            raise RuntimeError(
                "Finished output passed but RAG indexer is unavailable. "
                "Start the embedding service before marking the file complete."
            )
        return rag_indexer.index_finished_file(self.settings.project_root, chapter, path)

    async def _name_completed_chapters(self, state: PipelineState) -> PipelineState:
        """Name completed unnamed trees after their boundaries are known."""
        ledger = reconcile_finished_outputs(self.settings.project_root)
        updated = state
        for chapter in state.chapters:
            if chapter.status != "completed" or chapter.chapter_title:
                continue
            context = build_chapter_naming_context(ledger, chapter.chapter_name)
            if not context.get("file_count"):
                continue
            try:
                result = await self.archivist.name_chapter(context)
            except Exception:
                result = fallback_chapter_title(context)
            updated = self.state_mgr.set_chapter_title(
                updated,
                chapter.chapter_name,
                result["chapter_title"],
                result.get("reason", ""),
            )
            print(f"TREE_COMPLETE: {chapter.chapter_name} -> {result['chapter_title']}")
        return updated

    async def _refresh_planner_artifacts(self, state: object) -> None:
        """Refresh inventory, KnowledgeNodes, graph, and executable branch artifacts."""
        from tree.state.models import PipelineState

        s = state if isinstance(state, PipelineState) else None
        ledger = reconcile_finished_outputs(self.settings.project_root)
        inventory = await self._rebuild_source_inventory_from_rag()
        candidate_nodes = await rebuild_candidate_nodes_with_ai(
            self.settings.project_root,
            inventory,
            self.archivist,
            completed_collections=_completed_source_collections(s),
        )
        knowledge_graph = await rebuild_knowledge_graph_with_ai(
            self.settings.project_root,
            candidate_nodes,
            ledger,
            self.archivist,
        )
        running = {
            run.branch_id
            for run in (s.branch_runs if s is not None else [])
            if run.status == "running"
        }
        rebuild_branch_plan(
            self.settings.project_root,
            knowledge_graph,
            ledger,
            running_branch_ids=running,
        )

    async def close(self) -> None:
        rag_client = getattr(self, "rag_client", None)
        if rag_client is not None:
            try:
                rag_client.close()
            except Exception:
                pass
            self.rag_client = None
            self.rag_indexer = None
        await self.client.close()

    def _raise_if_stop_requested(self) -> None:
        if stop_requested(self.settings.project_root, "tree"):
            raise StopRequested("TREE stop requested")

    def _init_rag(self) -> None:
        """Initialize optional local RAG components when dependencies are installed."""
        try:
            from tree.rag.client import RAGClient
            from tree.rag.indexer import RAGIndexer
        except ImportError:
            return

        try:
            self.rag_client = RAGClient(store_path=paths.rag_store_path(self.settings.project_root))
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

    def _finished_rag_query(
        self,
        query_text: str,
        top_k: int = 8,
        allowed_paths: set[str] | None = None,
    ) -> list[dict]:
        hits = self._rag_query(
            query_text,
            filters={"content_kind": "finished"},
            top_k=top_k,
            include_drafts=False,
        )
        if allowed_paths is None:
            return hits
        return [
            hit
            for hit in hits
            if _hit_path(hit) in allowed_paths or f"finished:{_hit_path(hit)}" in allowed_paths
        ]

    def _allowed_finished_paths_for_chapter(self, chapter_name: str) -> set[str] | None:
        return self._allowed_finished_paths_for_chapter_span(chapter_name, None)

    def _allowed_finished_paths_for_chapter_span(
        self,
        chapter_name: str,
        covered_node_ids: list[str] | None,
    ) -> set[str] | None:
        scope = self._branch_prior_scope(chapter_name, covered_node_ids)
        if scope is None:
            return None
        return set(scope.allowed_paths)

    def _prior_finished_context(
        self,
        chapter_name: str,
        covered_node_ids: list[str] | None = None,
    ) -> tuple[list[str], list[str], set[str] | None]:
        allowed_paths = self._allowed_finished_paths_for_chapter_span(chapter_name, covered_node_ids)
        if allowed_paths is None:
            paths_list = file_ops.list_prior_paths(self.settings.project_root, chapter_name)
            return [str(path) for path in paths_list], file_ops.read_prior_files(self.settings.project_root, chapter_name), None
        path_objects = [
            self.settings.project_root / rel
            for rel in sorted(allowed_paths)
            if rel.startswith("outputs/")
        ]
        existing = [path for path in path_objects if path.exists()]
        return (
            [str(path) for path in existing],
            [path.read_text(encoding="utf-8") for path in existing],
            allowed_paths,
        )

    def _branch_prior_scope(
        self,
        chapter_name: str,
        covered_node_ids: list[str] | None = None,
    ):
        try:
            state = self.state_mgr.load()
        except Exception:
            return None
        chapter = next((item for item in state.chapters if item.chapter_name == chapter_name), None)
        if chapter is None or not chapter.branch_run_id:
            return None
        run = next((item for item in state.branch_runs if item.run_id == chapter.branch_run_id), None)
        if run is None:
            return None
        return build_branch_prior_scope(
            run,
            load_knowledge_dag(self.settings.project_root),
            load_knowledge_branches(self.settings.project_root),
            reconcile_finished_outputs(self.settings.project_root),
            covered_node_ids=covered_node_ids,
        )

    def _validate_exam_covered_nodes(
        self,
        chapter,
        exam_sections: ExamSections,
    ) -> ExamSections:
        if not chapter.branch_id:
            return exam_sections
        branches_doc = load_knowledge_branches(self.settings.project_root)
        branch = next(
            (
                item
                for item in branches_doc.get("branches", [])
                if isinstance(item, dict) and item.get("branch_id") == chapter.branch_id
            ),
            None,
        )
        if branch is None:
            return exam_sections
        covered = validate_branch_covered_node_ids(exam_sections.covered_node_ids, branch)
        return exam_sections.model_copy(update={"covered_node_ids": covered})

    def _source_collection_for_chapter(self, chapter_name: str) -> str | None:
        collections = self._source_collections_for_chapter(chapter_name)
        return collections[0] if collections else None

    def _active_chapter_graph_context(self, chapter: object | None) -> str | None:
        from tree.state.models import ChapterRecord

        if not isinstance(chapter, ChapterRecord):
            return None
        if not chapter.graph_node_id and not chapter.required_nodes and not chapter.branch_run_id:
            return None
        sections = []
        if chapter.branch_run_id:
            state = self.state_mgr.load()
            run = next(
                (item for item in state.branch_runs if item.run_id == chapter.branch_run_id),
                None,
            )
            if run is not None:
                sections.append(
                    branch_context_for_run(
                        run,
                        load_knowledge_branches(self.settings.project_root),
                        reconcile_finished_outputs(self.settings.project_root),
                    )
                )
        lines = [
            "## Active BranchRun Graph Binding",
            f"Execution path: {chapter.execution_path}",
            f"Tree id: {chapter.tree_id or 'none'}",
            f"Branch id: {chapter.branch_id or 'none'}",
            f"Current start KnowledgeNode: {chapter.graph_node_id or 'none'}",
            f"Coverage node ids: {', '.join(chapter.required_nodes) or 'none'}",
            f"Source collections: {', '.join(_chapter_source_collections(chapter)) or 'none'}",
            "",
        ]
        if chapter.graph_node_id:
            graph = self._load_knowledge_graph()
            lines.append(build_selected_node_context(graph, node_id=chapter.graph_node_id))
        sections.append("\n".join(lines).strip())
        return "\n\n".join(section for section in sections if section).strip()

    def _source_collections_for_chapter(self, chapter_name: str) -> list[str]:
        state_mgr = getattr(self, "state_mgr", None)
        if state_mgr is None:
            return [chapter_name]
        try:
            state = state_mgr.load()
        except Exception:
            return [chapter_name]
        for chapter in state.chapters:
            if chapter.chapter_name == chapter_name:
                collections = list(chapter.source_collections or [])
                if chapter.source_collection and chapter.source_collection not in collections:
                    collections.insert(0, chapter.source_collection)
                return collections
        return []

    def _source_payload_from_rag(self) -> dict[str, list[dict[str, str]]]:
        return self._payload_from_rag("source")

    def _finished_payload_from_rag(self) -> dict[str, list[dict[str, str]]]:
        return self._payload_from_rag("finished")

    async def _rebuild_source_inventory_from_rag(self) -> dict[str, Any]:
        rag_client = getattr(self, "rag_client", None)
        if rag_client is None:
            return load_inventory(self.settings.project_root)
        try:
            chunks = rag_client.scroll_chunks(
                filters={"content_kind": "source"},
                include_drafts=False,
            )
        except Exception:
            return load_inventory(self.settings.project_root)
        return await rebuild_source_inventory_with_ai(
            self.settings.project_root,
            chunks,
            self.archivist,
            concurrency=self.settings.source_archivist_concurrency,
        )

    def _load_candidate_nodes(self) -> dict[str, Any]:
        return load_candidate_nodes(self.settings.project_root)

    def _load_knowledge_graph(self) -> dict[str, Any]:
        return load_knowledge_graph(self.settings.project_root)

    def _payload_from_rag(self, content_kind: str) -> dict[str, list[dict[str, str]]]:
        rag_client = getattr(self, "rag_client", None)
        if rag_client is None:
            return {}
        try:
            chunks = rag_client.scroll_chunks(
                filters={"content_kind": content_kind},
                include_drafts=False,
            )
        except Exception:
            return {}
        grouped: dict[str, dict[str, list[str]]] = {}
        for hit in chunks:
            metadata = hit.get("metadata") or {}
            group = metadata.get("source_collection") or metadata.get("chapter") or ""
            if not group:
                continue
            path = metadata.get("path") or metadata.get("filename") or metadata.get("doc_id") or "indexed-source"
            grouped.setdefault(group, {}).setdefault(path, []).append(hit.get("text", ""))
        return {
            group: [
                {"path": path, "content": _trim_payload_text("\n\n".join(parts))}
                for path, parts in sorted(docs.items())
            ]
            for group, docs in sorted(grouped.items())
        }

    def _source_paths_from_rag(self, collections: list[str] | str | None) -> list[str]:
        payload = self._source_payload_from_rag()
        if collections is None:
            return [
                doc["path"]
                for _, docs in sorted(payload.items())
                for doc in docs
            ]
        if isinstance(collections, str):
            collections = [collections]
        return [
            doc["path"]
            for collection in collections
            for doc in payload.get(collection, [])
        ]


def _pending_materials(root: Path, manifest: dict[str, Any]) -> list[tuple[Path, str]]:
    materials_root = paths.materials_root(root)
    if not materials_root.exists():
        return []

    pending = []
    for path in sorted(materials_root.rglob("*")):
        if not _is_supported_material(path):
            continue
        collection = _collection_for_material(materials_root, path)
        output = source_ops.source_root(root) / collection / f"{path.stem}.md"
        rel = _relative_to_root(root, path)
        entry = manifest.get(rel, {})
        same_fingerprint = entry.get("fingerprint") == _file_fingerprint(path)
        if same_fingerprint and (entry.get("embedded") is True or output.exists()):
            continue
        if not same_fingerprint or not output.exists():
            pending.append((path, collection))
    return pending


def _is_supported_material(path: Path) -> bool:
    return (
        path.is_file()
        and not path.name.startswith(".")
        and path.suffix.lower() in RAW_MATERIAL_EXTENSIONS
    )


def _collection_for_material(materials_root: Path, path: Path) -> str:
    rel = path.relative_to(materials_root)
    return rel.parts[0] if len(rel.parts) > 1 else path.stem


def _file_fingerprint(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def _load_source_manifest(root: Path) -> dict[str, Any]:
    path = _source_manifest_path(root)
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _save_source_manifest(root: Path, manifest: dict[str, Any]) -> None:
    path = _source_manifest_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _source_manifest_path(root: Path) -> Path:
    return paths.pipeline_temp_root(root) / "source-ingest-manifest.json"


def _mark_material_ingested(
    root: Path,
    manifest: dict[str, Any],
    material_path: Path,
    collection: str,
    outputs: list[Path],
) -> None:
    manifest[_relative_to_root(root, material_path)] = {
        "collection": collection,
        "embedded": False,
        "fingerprint": _file_fingerprint(material_path),
        "outputs": [_relative_to_root(root, output) for output in outputs],
    }


def _refresh_manifest_index_status(
    root: Path,
    manifest: dict[str, Any],
    indexer: object | None,
) -> None:
    """Keep source-ingest manifest in sync with actual source vectors."""
    for entry in manifest.values():
        if not isinstance(entry, dict):
            continue
        outputs = entry.get("outputs") or []
        if not outputs:
            continue
        collection = entry.get("collection")
        if not collection or indexer is None:
            entry["embedded"] = False
            continue
        is_indexed = getattr(indexer, "is_source_file_indexed", None)
        if is_indexed is None:
            entry["embedded"] = False
            continue
        entry["embedded"] = all(
            bool(is_indexed(root, collection, _manifest_output_path(root, collection, output)))
            for output in outputs
        )


def _manifest_output_path(root: Path, collection: str, output: str) -> Path:
    path = Path(output)
    if path.is_absolute():
        return path
    if len(path.parts) == 1:
        return source_ops.source_root(root) / collection / path
    return root / path


def _mark_material_embedded(root: Path, manifest: dict[str, Any], material_path: Path) -> None:
    entry = manifest.get(_relative_to_root(root, material_path))
    if isinstance(entry, dict) and entry.get("outputs"):
        entry["embedded"] = True


def _index_source_output(root: Path, collection: str, path: Path, indexer: object | None) -> int:
    if indexer is None:
        raise RuntimeError(
            "Source materials exist but RAG indexer is unavailable. "
            "Start the embedding service and ensure RAG dependencies are installed before running TREE."
        )
    is_indexed = getattr(indexer, "is_source_file_indexed", None)
    if is_indexed is not None and is_indexed(root, collection, path):
        path.unlink(missing_ok=True)
        return 0
    indexer.index_source_file(root, collection, path)
    path.unlink(missing_ok=True)
    return 1


def _relative_to_root(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _trim_payload_text(text: str, limit: int = 4000) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n[TRUNCATED]"


def _hit_path(hit: dict[str, Any]) -> str:
    metadata = hit.get("metadata") or {}
    return str(metadata.get("path") or metadata.get("filename") or metadata.get("doc_id") or "")


def _allowed_finished_paths_for_engine(
    engine: object,
    chapter_name: str,
    covered_node_ids: list[str] | None = None,
) -> set[str] | None:
    method = getattr(engine, "_allowed_finished_paths_for_chapter_span", None)
    if method is not None:
        return method(chapter_name, covered_node_ids)
    method = getattr(engine, "_allowed_finished_paths_for_chapter", None)
    if method is None:
        return None
    return method(chapter_name)


def _prior_finished_context_for_engine(
    engine: object,
    chapter_name: str,
    covered_node_ids: list[str] | None = None,
) -> tuple[list[str], list[str], set[str] | None]:
    method = getattr(engine, "_prior_finished_context", None)
    if method is not None:
        return method(chapter_name, covered_node_ids)
    root = engine.settings.project_root
    paths_list = file_ops.list_prior_paths(root, chapter_name)
    return [str(path) for path in paths_list], file_ops.read_prior_files(root, chapter_name), None


def _completed_source_collections(state: PipelineState | None) -> set[str]:
    if state is None:
        return set()
    completed = set()
    for chapter in state.chapters:
        if chapter.status != "completed" or not chapter.chapter_title:
            continue
        completed.update(_chapter_source_collections(chapter))
    return completed


def _chapter_source_collections(chapter: object | None) -> list[str]:
    if chapter is None:
        return []
    collections = list(getattr(chapter, "source_collections", None) or [])
    primary = getattr(chapter, "source_collection", None)
    if primary and primary not in collections:
        collections.insert(0, primary)
    return collections


def _chapter_graph_hit_chunks(
    root: Path,
    chapter: object | None,
    node_ids: list[str] | None = None,
) -> list[str]:
    target_node_ids = _unique(
        [
            *(node_ids or []),
            *([getattr(chapter, "graph_node_id", None)] if getattr(chapter, "graph_node_id", None) else []),
        ]
    )
    if not target_node_ids:
        return []
    graph = load_knowledge_graph(root)
    chunks = []
    for node in graph.get("nodes", []):
        if isinstance(node, dict) and node.get("node_id") in target_node_ids:
            chunks.extend(_string_list(node.get("hit_chunks")))
    if chunks:
        return _unique(chunks)
    candidate_nodes = load_candidate_nodes(root)
    for candidate in candidate_nodes.get("chapter_candidates", []):
        if isinstance(candidate, dict) and candidate.get("candidate_id") in target_node_ids:
            chunks.extend(_candidate_hit_chunks(candidate))
    return _unique(chunks)


def _candidate_hit_chunks(candidate: dict[str, Any]) -> list[str]:
    chunks = []
    for item in candidate.get("representative_chunks", []) or []:
        if isinstance(item, str):
            chunks.append(item)
        elif isinstance(item, dict) and item.get("chunk_ref"):
            chunks.append(str(item["chunk_ref"]))
    return _unique(chunks)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,\n，、]+", value)
    if not isinstance(value, list):
        return []
    return _unique(str(item).strip() for item in value if str(item).strip())


def _unique(values: Any) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if not value:
            continue
        key = str(value)
        if key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def _chapter_name_from_required_nodes(required_nodes: list[str]) -> str | None:
    for node_id in required_nodes:
        if not node_id.startswith("finished:"):
            continue
        rel = node_id.removeprefix("finished:")
        parts = Path(rel).parts
        if len(parts) >= 3 and parts[0] == "outputs":
            return "/".join(parts[1:-1])
    return None


def _tree_id_for_branch_execution(
    branch: dict[str, Any],
    state: PipelineState,
    ledger: dict[str, Any],
) -> str:
    upstream_ids = set(_string_list(branch.get("upstream_branch_ids")))
    if upstream_ids:
        for execution in state.chapters:
            if execution.branch_id in upstream_ids and execution.tree_id:
                return execution.tree_id
        start_node = str(branch.get("start_node_id") or "")
        for record in ledger.get("records", []):
            if not isinstance(record, dict):
                continue
            covered = set(_string_list(record.get("covered_node_ids")))
            graph_node_id = str(record.get("graph_node_id") or "")
            if start_node and (start_node in covered or start_node == graph_node_id):
                tree_id = str(record.get("tree_id") or "").strip()
                if tree_id:
                    return tree_id
                execution_path = str(record.get("execution_path") or record.get("chapter") or "")
                parts = [part for part in execution_path.split("/") if part]
                if parts:
                    return parts[0]
    return next_tree_id(state)


def _branch_plan_blockage(branches_doc: dict[str, Any]) -> str:
    branches = [item for item in branches_doc.get("branches", []) if isinstance(item, dict)]
    if not branches:
        return ""
    if any(branch.get("status") in {"ready", "running"} for branch in branches):
        return ""
    if all(branch.get("status") == "complete" for branch in branches):
        return ""
    diagnostics = [item for item in branches_doc.get("diagnostics", []) if isinstance(item, dict)]
    if diagnostics:
        kinds = _unique(str(item.get("kind") or "diagnostic") for item in diagnostics)
        return ", ".join(kinds)
    blocked = [branch for branch in branches if branch.get("status") == "blocked"]
    if blocked:
        reasons = _unique(str(branch.get("blocked_reason") or "blocked") for branch in blocked)
        return ", ".join(reasons)
    return ""


def _branch_chapter_slug(branch_id: str) -> str:
    slug = branch_id.replace(":", "-")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", slug).strip("-")
    return slug or "branch"


def persist_writer_result(
    root: Path,
    iter_state: IterationState,
    writer_result: WriterResult,
) -> WriterResult:
    """Persist writer Markdown into drafts/{chapter}/{NN}.{title}.md."""
    if writer_result.is_exam_too_broad:
        raise ValueError("Writer returned obsolete EXAM_TOO_BROAD control signal")
    if not writer_result.draft_content.strip():
        raise ValueError("Writer returned no draft content")

    filename = _draft_filename(iter_state.file_seq, iter_state.knowledge_point)
    draft_content = _strip_markdown_front_matter(writer_result.draft_content)
    draft_path = file_ops.write_draft(
        root,
        iter_state.chapter,
        filename,
        draft_content,
    )
    return writer_result.model_copy(update={"draft_path": draft_path, "draft_content": draft_content})


def _strip_markdown_front_matter(content: str) -> str:
    """Remove accidental YAML front matter from user-facing drafts."""
    text = content.lstrip()
    if not text.startswith("---\n"):
        return content
    lines = text.splitlines()
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[index + 1:]).lstrip() + "\n"
    return content


def _draft_filename(file_seq: str, knowledge_point: str) -> str:
    title = knowledge_point.strip()
    title = re.sub(r"^\s*\d+\s*[.．、-]\s*", "", title)
    title = re.sub(r"[\\/:\*\?\"<>\|]", "-", title)
    title = re.sub(r"\s+", "", title)
    if not title:
        title = "未命名知识点"
    return f"{file_seq}.{title}.md"
