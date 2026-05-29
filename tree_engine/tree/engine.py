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
    ExamTooBroadContext,
    IterationState,
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
        self.rag_client = None
        self.rag_indexer = None
        self._init_rag()

    async def run(self) -> None:
        """Entry point for `tree run`."""
        self.tracer.log_pipeline_start()
        self.progress.reset()
        self._raise_if_stop_requested()
        await self._prepare_source_materials_for_loop()
        while True:
            self._raise_if_stop_requested()
            state = self.state_mgr.load()
            chapter = self.state_mgr.find_in_progress(state)
            if chapter is None:
                # No in_progress chapter — scan for next
                self.progress.learning_stage(
                    stage="scan_next_chapter",
                    stage_label="Scanning next chapter",
                    stage_index=1,
                    stage_total=6,
                    message="Examiner is scanning source materials for the next chapter",
                )
                scan_result = await self._scan_next_chapter(state)
                if scan_result is None:
                    self.tracer.log_pipeline_complete()
                    self.progress.complete("PIPELINE_COMPLETE — all source materials covered.")
                    print("PIPELINE_COMPLETE — all source materials covered.")
                    return
                new_name = scan_result
                state = self.state_mgr.add_chapter(state, new_name)
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
        _mark_manifest_outputs_embedded(manifest)
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
        """Run Step 0→1→2→3→4 loop for one chapter until CHAPTER_COMPLETE."""
        while True:
            self._raise_if_stop_requested()
            state = self.state_mgr.load()
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
                state = self.state_mgr.complete_chapter(state, chapter_name)
                self.state_mgr.save(state)
                self.progress.learning_stage(
                    stage="chapter_complete",
                    stage_label="Chapter complete",
                    stage_index=6,
                    stage_total=6,
                    chapter=chapter_name,
                    file_seq=next_seq,
                    message=f"CHAPTER_COMPLETE: {chapter_name}",
                )
                print(f"CHAPTER_COMPLETE: {chapter_name}")
                return

            # Stash exam for iteration loop
            iter_state = IterationState(
                chapter=chapter_name,
                file_seq=next_seq,
                knowledge_point=exam_sections.knowledge_point,
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

            if writer_result.is_exam_too_broad:
                print("  Step 4: EXAM_TOO_BROAD — returning to Step 1")
                iter_state = await self._handle_exam_too_broad(iter_state, writer_result)
                continue

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
        exam_too_broad_ctx: ExamTooBroadContext | None = None,
    ) -> tuple[ExamSections, bool]:
        from tree.state.models import ChapterRecord

        ch = chapter if isinstance(chapter, ChapterRecord) else None
        ch_name = ch.chapter_name if ch else getattr(chapter, "chapter_name", "")
        source_collection = ch.source_collection if ch else getattr(chapter, "source_collection", None)
        prior_paths = [str(p) for p in file_ops.list_prior_paths(self.settings.project_root, ch_name)]
        query_text = f"{ch_name}\n{next_seq}\n下一知识点命题"
        source_filters = {"content_kind": "source"}
        if source_collection:
            source_filters["source_collection"] = source_collection
        retrieved_context = (
            self._rag_query(
                query_text,
                filters=source_filters,
                top_k=5,
                include_drafts=False,
            )
            + self._rag_query(
                query_text,
                filters={
                    "content_kind": "finished",
                    "chapter": ch_name,
                },
                top_k=5,
                include_drafts=False,
            )
        )
        return await self.examiner.compose_exam(
            next_seq,
            [],
            prior_paths,
            source_material_contents=[],
            source_material_paths=self._source_paths_from_rag(source_collection),
            retrieved_context=retrieved_context,
            exam_too_broad_ctx=exam_too_broad_ctx,
        )

    async def _step2_blind_test(self, iter_state: IterationState) -> str:
        prior_paths = [str(p) for p in file_ops.list_prior_paths(self.settings.project_root, iter_state.chapter)]
        draft_text = None
        if iter_state.draft_path and iter_state.draft_path.exists():
            draft_text = iter_state.draft_path.read_text(encoding="utf-8")
        assert iter_state.exam_sections is not None
        query_text = (
            f"{iter_state.knowledge_point}\n"
            f"{iter_state.exam_sections.blind_exam}"
        )
        retrieved_context = self._rag_query(
            query_text,
            filters={
                "content_kind": "finished",
                "chapter": iter_state.chapter,
            },
            top_k=5,
            include_drafts=False,
        )
        return await self.student.blind_test(
            iter_state.exam_sections.blind_exam,
            [],
            prior_paths,
            draft_text,
            retrieved_context=retrieved_context,
        )

    async def _step3_audit(self, iter_state: IterationState, answer: str) -> AuditResult:
        prior_paths = [str(p) for p in file_ops.list_prior_paths(self.settings.project_root, iter_state.chapter)]
        draft_text = None
        if iter_state.draft_path and iter_state.draft_path.exists():
            draft_text = iter_state.draft_path.read_text(encoding="utf-8")
        assert iter_state.exam_sections is not None
        return await self.examiner.audit(
            iter_state.exam_sections.blind_exam,
            iter_state.exam_sections.answer_key,
            answer,
            draft_text,
            [],
            prior_paths,
            iter_state.previous_bottleneck,
            retrieved_context=self._rag_query(
                f"{iter_state.knowledge_point}\n{iter_state.exam_sections.blind_exam}\n{answer}",
                filters={
                    "content_kind": "finished",
                    "chapter": iter_state.chapter,
                },
                top_k=5,
                include_drafts=False,
            ),
        )

    async def _step4_writer(self, iter_state: IterationState, audit: AuditResult) -> WriterResult:
        prior_paths = [str(p) for p in file_ops.list_prior_paths(self.settings.project_root, iter_state.chapter)]
        draft_text = None
        if iter_state.draft_path and iter_state.draft_path.exists():
            draft_text = iter_state.draft_path.read_text(encoding="utf-8")
        writer_instructions = iter_state.exam_sections.writer_instructions if iter_state.exam_sections else None
        source_collection = self._source_collection_for_chapter(iter_state.chapter)
        writer_bottleneck = sanitize_writer_context(audit.bottleneck_report)
        query_text = f"{iter_state.knowledge_point}\n{writer_bottleneck}"
        source_filters = {"content_kind": "source"}
        if source_collection:
            source_filters["source_collection"] = source_collection
        retrieved_context = (
            self._rag_query(
                query_text,
                filters=source_filters,
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
            writer_bottleneck,
            [],
            prior_paths,
            draft_text,
            iter_state.previous_bottleneck,
            writer_instructions,
            retrieved_context=retrieved_context,
        )

    # --- Handlers ---

    async def _handle_pass(self, iter_state: IterationState, audit: AuditResult) -> None:
        """Move draft to outputs, update state."""
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
        self, iter_state: IterationState, writer_result: WriterResult
    ) -> IterationState:
        """Return to Step 1 with narrowed scope."""
        exam_sections, _ = await self._step1_compose(
            type("Ch", (), {"chapter_name": iter_state.chapter})(),
            iter_state.file_seq,
            ExamTooBroadContext(
                bloat_description=writer_result.bloat_description,
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
        source_payload = self._source_payload_from_rag()
        name, is_complete = await self.examiner.scan_next_chapter(
            state_text, source_payload
        )
        if is_complete:
            return None
        return name

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

    def _source_collection_for_chapter(self, chapter_name: str) -> str | None:
        state_mgr = getattr(self, "state_mgr", None)
        if state_mgr is None:
            return chapter_name
        try:
            state = state_mgr.load()
        except Exception:
            return chapter_name
        for chapter in state.chapters:
            if chapter.chapter_name == chapter_name:
                return chapter.source_collection
        return None

    def _source_payload_from_rag(self) -> dict[str, list[dict[str, str]]]:
        rag_client = getattr(self, "rag_client", None)
        if rag_client is None:
            return {}
        try:
            chunks = rag_client.scroll_chunks(
                filters={"content_kind": "source"},
                include_drafts=False,
            )
        except Exception:
            return {}
        grouped: dict[str, dict[str, list[str]]] = {}
        for hit in chunks:
            metadata = hit.get("metadata") or {}
            collection = metadata.get("source_collection") or metadata.get("chapter") or ""
            if not collection:
                continue
            path = metadata.get("path") or metadata.get("filename") or metadata.get("doc_id") or "indexed-source"
            grouped.setdefault(collection, {}).setdefault(path, []).append(hit.get("text", ""))
        return {
            collection: [
                {"path": path, "content": "\n\n".join(parts)}
                for path, parts in sorted(docs.items())
            ]
            for collection, docs in sorted(grouped.items())
        }

    def _source_paths_from_rag(self, collection: str | None) -> list[str]:
        payload = self._source_payload_from_rag()
        if collection is None:
            return [
                doc["path"]
                for _, docs in sorted(payload.items())
                for doc in docs
            ]
        return [doc["path"] for doc in payload.get(collection, [])]


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


def _mark_manifest_outputs_embedded(manifest: dict[str, Any]) -> None:
    for entry in manifest.values():
        if not isinstance(entry, dict):
            continue
        outputs = entry.get("outputs") or []
        if outputs:
            entry["embedded"] = True


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


def persist_writer_result(
    root: Path,
    iter_state: IterationState,
    writer_result: WriterResult,
) -> WriterResult:
    """Persist writer Markdown into drafts/{chapter}/{NN}.{title}.md."""
    if writer_result.is_exam_too_broad:
        return writer_result
    if not writer_result.draft_content.strip():
        raise ValueError("Writer returned no draft content")

    filename = _draft_filename(iter_state.file_seq, iter_state.knowledge_point)
    draft_path = file_ops.write_draft(
        root,
        iter_state.chapter,
        filename,
        writer_result.draft_content,
    )
    return writer_result.model_copy(update={"draft_path": draft_path})


def _draft_filename(file_seq: str, knowledge_point: str) -> str:
    title = knowledge_point.strip()
    title = re.sub(r"^\s*\d+\s*[.．、-]\s*", "", title)
    title = re.sub(r"[\\/:\*\?\"<>\|]", "-", title)
    title = re.sub(r"\s+", "", title)
    if not title:
        title = "未命名知识点"
    return f"{file_seq}.{title}.md"
