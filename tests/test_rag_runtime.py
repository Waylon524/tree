from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tree.config import Settings
from tree.engine import TreeEngine
from tree.ingest import ingest_path
from tree.rag.indexer import RAGIndexer
from tree.rag.client import RAGClient
from tree.state.manager import StateManager
from tree.state.models import (
    ArchitectResult,
    AuditResult,
    ExamSections,
    IterationState,
    PipelineState,
    Route,
)


class FakeEmbedder:
    def embed(self, texts):
        if isinstance(texts, str):
            texts = [texts]
        vectors = []
        for index, _ in enumerate(texts):
            vectors.append([1.0, float(index), 0.0])
        return vectors


class RAGRuntimeTests(unittest.TestCase):
    def test_rag_indexes_source_and_finished_with_distinct_doc_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rag = RAGClient(
                store_path=Path(tmp) / "rag-store",
                dimensions=3,
                embedder=FakeEmbedder(),
            )

            rag.index_file(
                file_seq="01",
                filename="01.same.md",
                text="## 定义\n**源资料概念** 来自结构化资料。",
                chapter="化学平衡",
                content_kind="source",
                source_collection="化学平衡",
                path="source_materials/化学平衡/01.same.md",
            )
            rag.index_file(
                file_seq="01",
                filename="01.same.md",
                text="## 定义\n**成品概念** 来自最终教材。",
                chapter="化学平衡",
                content_kind="finished",
                source_collection="化学平衡",
                path="finished_outputs/化学平衡/01.same.md",
            )

            source_hits = rag.query("概念", filters={"content_kind": "source"})
            finished_hits = rag.query("概念", filters={"content_kind": "finished"})

        self.assertEqual(len(source_hits), 1)
        self.assertEqual(len(finished_hits), 1)
        self.assertEqual(source_hits[0]["metadata"]["content_kind"], "source")
        self.assertEqual(finished_hits[0]["metadata"]["content_kind"], "finished")
        self.assertNotEqual(source_hits[0]["metadata"]["doc_id"], finished_hits[0]["metadata"]["doc_id"])

    def test_indexer_indexes_source_collection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = root / "source_materials" / "化学平衡"
            source_dir.mkdir(parents=True)
            (source_dir / "lesson.md").write_text("## 平衡\n**平衡状态** 的定义。", encoding="utf-8")

            rag = RAGClient(
                store_path=root / "rag-store",
                dimensions=3,
                embedder=FakeEmbedder(),
            )
            indexer = RAGIndexer(rag)

            count = indexer.index_source_collection(root, "化学平衡")
            hits = rag.query("平衡状态", filters={"source_collection": "化学平衡"})

        self.assertEqual(count, 1)
        self.assertEqual(hits[0]["metadata"]["content_kind"], "source")
        self.assertEqual(hits[0]["metadata"]["source_collection"], "化学平衡")

    def test_ingest_path_indexes_outputs_when_indexer_is_supplied(self) -> None:
        class FakeArchivist:
            async def structure(self, raw_text: str) -> str:
                return "# Lesson\n\n## 平衡\n结构化内容"

        class FakeIndexer:
            def __init__(self) -> None:
                self.calls: list[tuple[Path, str, Path]] = []

            def index_source_file(self, root: Path, collection: str, path: Path) -> int:
                self.calls.append((root, collection, path))
                return 1

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "lesson.pdf"
            input_path.write_bytes(b"%PDF")
            settings = Settings.from_env(root, require_llm=False)
            indexer = FakeIndexer()

            with (
                patch("tree.ingest.get_engine"),
                patch("tree.ingest.extract_text", return_value="raw OCR"),
            ):
                outputs = asyncio.run(
                    ingest_path(
                        input_path,
                        root / "source_materials" / "化学平衡",
                        settings,
                        archivist=FakeArchivist(),
                        collection="化学平衡",
                        indexer=indexer,
                    )
                )

        self.assertEqual(len(outputs), 1)
        self.assertEqual(indexer.calls, [(root, "化学平衡", outputs[0])])

    def test_handle_pass_indexes_finished_output_when_indexer_exists(self) -> None:
        class FakeIndexer:
            def __init__(self) -> None:
                self.calls: list[tuple[Path, str, Path]] = []

            def index_finished_file(self, root: Path, chapter: str, path: Path) -> int:
                self.calls.append((root, chapter, path))
                return 1

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft_dir = root / "drafts" / "化学平衡"
            draft_dir.mkdir(parents=True)
            draft = draft_dir / "01.平衡状态.md"
            draft.write_text("# 平衡状态\n", encoding="utf-8")

            state_mgr = StateManager(root / "pipeline-state.json")
            state_mgr.save(PipelineState())
            state_mgr.save(state_mgr.add_chapter(PipelineState(), "化学平衡", "化学平衡"))

            engine = object.__new__(TreeEngine)
            engine.settings = Settings.from_env(root, require_llm=False)
            engine.state_mgr = state_mgr
            engine.rag_indexer = FakeIndexer()

            iter_state = IterationState(
                chapter="化学平衡",
                file_seq="01",
                knowledge_point="01. 平衡状态",
                draft_path=draft,
            )
            audit = AuditResult(route=Route.PASS, exam_id="01. 平衡状态", bottleneck_report="")

            with patch("tree.io.git_ops.git_add_commit"):
                asyncio.run(TreeEngine._handle_pass(engine, iter_state, audit))

            finished = root / "finished_outputs" / "化学平衡" / "01.平衡状态.md"

        self.assertEqual(engine.rag_indexer.calls, [(root, "化学平衡", finished)])

    def test_handle_pass_updates_state_when_finished_indexing_fails(self) -> None:
        class FailingIndexer:
            def index_finished_file(self, root: Path, chapter: str, path: Path) -> int:
                raise RuntimeError("embedding server down")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft_dir = root / "drafts" / "化学平衡"
            draft_dir.mkdir(parents=True)
            draft = draft_dir / "01.平衡状态.md"
            draft.write_text("# 平衡状态\n", encoding="utf-8")

            state_mgr = StateManager(root / "pipeline-state.json")
            state_mgr.save(PipelineState())
            state_mgr.save(state_mgr.add_chapter(PipelineState(), "化学平衡", "化学平衡"))

            engine = object.__new__(TreeEngine)
            engine.settings = Settings.from_env(root, require_llm=False)
            engine.state_mgr = state_mgr
            engine.rag_indexer = FailingIndexer()

            iter_state = IterationState(
                chapter="化学平衡",
                file_seq="01",
                knowledge_point="01. 平衡状态",
                draft_path=draft,
            )
            audit = AuditResult(route=Route.PASS, exam_id="01. 平衡状态", bottleneck_report="")

            with patch("tree.io.git_ops.git_add_commit"):
                asyncio.run(TreeEngine._handle_pass(engine, iter_state, audit))

            state = state_mgr.load()

        self.assertEqual(state.chapters[0].files_completed, ["01.平衡状态.md"])

    def test_step1_passes_rag_context_to_examiner_when_available(self) -> None:
        class FakeRag:
            def __init__(self) -> None:
                self.calls = []

            def query(self, query_text: str, top_k: int, filters: dict, include_drafts: bool = True):
                self.calls.append((query_text, top_k, filters, include_drafts))
                return [{"text": "RAG source chunk", "metadata": {"content_kind": "source"}}]

        class FakeExaminer:
            def __init__(self) -> None:
                self.retrieved_context = []

            async def compose_exam(self, *args, **kwargs):
                self.retrieved_context = kwargs["retrieved_context"]
                return ExamSections(
                    knowledge_point="01. 平衡状态",
                    blind_exam="Q",
                    student_instructions="S",
                    answer_key="A",
                    architect_instructions="W",
                ), False

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = root / "source_materials" / "化学平衡"
            source_dir.mkdir(parents=True)
            (source_dir / "5.md").write_text("# 化学平衡\n源内容", encoding="utf-8")
            engine = object.__new__(TreeEngine)
            engine.settings = Settings.from_env(root, require_llm=False)
            engine.examiner = FakeExaminer()
            engine.rag_client = FakeRag()
            chapter = type(
                "Chapter",
                (),
                {"chapter_name": "化学平衡", "source_collection": "化学平衡"},
            )()

            asyncio.run(TreeEngine._step1_compose(engine, chapter, "01"))

        self.assertEqual(engine.examiner.retrieved_context[0]["text"], "RAG source chunk")
        self.assertEqual(engine.rag_client.calls[0][2]["content_kind"], "source")

    def test_step4_passes_rag_context_to_writer_when_available(self) -> None:
        class FakeRag:
            def query(self, query_text: str, top_k: int, filters: dict, include_drafts: bool = True):
                return [{"text": f"{filters['content_kind']} chunk", "metadata": filters}]

        class FakeWriter:
            def __init__(self) -> None:
                self.retrieved_context = []

            async def create_or_optimize(self, *args, **kwargs):
                self.retrieved_context = kwargs["retrieved_context"]
                return ArchitectResult(draft_content="# draft")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = object.__new__(TreeEngine)
            engine.settings = Settings.from_env(root, require_llm=False)
            engine.writer = FakeWriter()
            engine.rag_client = FakeRag()
            iter_state = IterationState(
                chapter="化学平衡",
                file_seq="01",
                knowledge_point="01. 平衡状态",
                exam_sections=ExamSections(
                    knowledge_point="01. 平衡状态",
                    blind_exam="Q",
                    student_instructions="S",
                    answer_key="A",
                    architect_instructions="W",
                ),
            )
            audit = AuditResult(
                route=Route.FAIL_KNOWLEDGE_GAP,
                exam_id="01. 平衡状态",
                bottleneck_report="缺少平衡状态定义",
            )

            asyncio.run(TreeEngine._step4_writer(engine, iter_state, audit))

        self.assertEqual(
            [hit["text"] for hit in engine.writer.retrieved_context],
            ["source chunk", "finished chunk"],
        )


if __name__ == "__main__":
    unittest.main()
