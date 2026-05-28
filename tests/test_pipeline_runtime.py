from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from tree.agents.archivist import ArchivistAgent
from tree.agents.examiner import ExaminerAgent
from tree.agents.loader import AgentLoader
from tree.cli import app
from tree.config import Settings
from tree.engine import TreeEngine, persist_writer_result
from tree.ingest import ingest_path
from tree.io import source_ops
from tree.io.git_ops import git_add_commit
from tree.state.manager import StateManager
from tree.state.models import (
    ArchitectResult,
    ChapterRecord,
    ExamSections,
    IterationState,
    PipelineState,
    Route,
)


class PipelineRuntimeTests(unittest.TestCase):
    def test_source_ops_lists_collections_and_reads_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chapter_dir = root / "source_materials" / "化学平衡"
            chapter_dir.mkdir(parents=True)
            (chapter_dir / "5. 化学平衡通论.md").write_text("# 化学平衡\n内容A", encoding="utf-8")
            (chapter_dir / "notes.txt").write_text("ignore", encoding="utf-8")

            self.assertEqual(source_ops.list_collections(root), ["化学平衡"])
            docs = source_ops.read_collection(root, "化学平衡")

        self.assertEqual(docs[0].path.name, "5. 化学平衡通论.md")
        self.assertEqual(docs[0].content, "# 化学平衡\n内容A")

    def test_state_records_source_collection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mgr = StateManager(Path(tmp) / "pipeline-state.json")
            state = mgr.add_chapter(
                PipelineState(),
                "化学平衡",
                source_collection="化学平衡",
            )

        self.assertEqual(state.chapters[0].chapter_name, "化学平衡")
        self.assertEqual(state.chapters[0].source_collection, "化学平衡")

    def test_examiner_compose_receives_source_materials(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.user_prompt = ""

            async def call(self, role: str, system_prompt: str, user_prompt: str) -> str:
                self.user_prompt = user_prompt
                return """## [Next_Knowledge_Point]
01. 化学平衡状态

## [Blind_Exam]
Q1

## [Student_Instructions]
Use evidence.

## [Answer_Key]
A1

## [Architect_Instructions]
Write narrowly.
"""

        client = FakeClient()
        examiner = ExaminerAgent(client, AgentLoader())
        asyncio.run(
            examiner.compose_exam(
                "01",
                prior_file_contents=[],
                prior_file_paths=[],
                source_material_contents=["# 化学平衡\n可逆反应达到平衡。"],
                source_material_paths=["source_materials/化学平衡/5.md"],
            )
        )

        self.assertIn("Source material paths", client.user_prompt)
        self.assertIn("可逆反应达到平衡", client.user_prompt)

    def test_examiner_audit_repairs_missing_route_format(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, str]] = []

            async def call(self, role: str, system_prompt: str, user_prompt: str) -> str:
                self.calls.append((role, system_prompt, user_prompt))
                if len(self.calls) == 1:
                    return "# Bottleneck Report\n学生缺少公式依据。"
                return (
                    "# Bottleneck Report\n"
                    "学生缺少公式依据。\n\n"
                    "ROUTE: FAIL_KNOWLEDGE_GAP\n"
                    "EXAM_ID: 01. 相变过程的标准自由能变化\n"
                )

        client = FakeClient()
        examiner = ExaminerAgent(client, AgentLoader(), max_format_retries=1)

        result = asyncio.run(
            examiner.audit(
                exam_paper="Q",
                answer_key="A",
                student_answer="student answer",
                draft_text=None,
                prior_file_contents=[],
                prior_file_paths=[],
            )
        )

        self.assertEqual(result.route, Route.FAIL_KNOWLEDGE_GAP)
        self.assertEqual(result.exam_id, "01. 相变过程的标准自由能变化")
        self.assertEqual(len(client.calls), 2)
        self.assertIn("Repair the machine-readable audit format", client.calls[1][2])

    def test_engine_discovers_chapter_from_source_materials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            class FakeExaminer:
                def __init__(self) -> None:
                    self.source_payload = {}

                async def scan_next_chapter(
                    self,
                    pipeline_state_text: str,
                    source_payload: dict[str, list[dict[str, str]]],
                ) -> tuple[str, bool]:
                    self.source_payload = source_payload
                    return "化学平衡", False

            class FakeRag:
                def scroll_chunks(self, filters, include_drafts=False):
                    return [
                        {
                            "text": "# 化学平衡\n内容",
                            "metadata": {
                                "content_kind": "source",
                                "source_collection": "化学平衡",
                                "path": "source_materials/化学平衡/5.md",
                            },
                        }
                    ]

            settings = Settings.from_env(root, require_llm=False)
            engine = object.__new__(TreeEngine)
            engine.settings = settings
            engine.examiner = FakeExaminer()
            engine.rag_client = FakeRag()

            name = asyncio.run(TreeEngine._scan_next_chapter(engine, PipelineState()))

        self.assertEqual(name, "化学平衡")
        self.assertIn("化学平衡", engine.examiner.source_payload)
        self.assertEqual(engine.examiner.source_payload["化学平衡"][0]["content"], "# 化学平衡\n内容")

    def test_engine_ingests_new_raw_materials_before_loop(self) -> None:
        class FakeIndexer:
            def __init__(self) -> None:
                self.indexed: set[Path] = set()
                self.calls: list[tuple[Path, str, Path]] = []

            def is_source_file_indexed(self, root: Path, collection: str, path: Path) -> bool:
                return path in self.indexed

            def index_source_file(self, root: Path, collection: str, path: Path) -> int:
                self.calls.append((root, collection, path))
                self.indexed.add(path)
                return 1

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_dir = root / "raw_materials" / "化学平衡"
            raw_dir.mkdir(parents=True)
            raw_file = raw_dir / "lesson.pdf"
            raw_file.write_bytes(b"%PDF")

            engine = object.__new__(TreeEngine)
            engine.settings = Settings.from_env(root, require_llm=False)
            engine.rag_indexer = FakeIndexer()
            ingest_calls: list[tuple[Path, Path, str, object]] = []

            async def fake_ingest(input_path, output_dir, use_archivist=True, collection=None, indexer=None):
                ingest_calls.append((input_path, output_dir, collection, indexer))
                output_dir.mkdir(parents=True)
                output = output_dir / f"{input_path.stem}.md"
                output.write_text("# structured", encoding="utf-8")
                return [output]

            engine.ingest = fake_ingest

            asyncio.run(TreeEngine._prepare_source_materials_for_loop(engine))
            asyncio.run(TreeEngine._prepare_source_materials_for_loop(engine))

            output = root / "source_materials" / "化学平衡" / "lesson.md"
            manifest = root / "pipeline-temp" / "source-ingest-manifest.json"
            manifest_exists = manifest.exists()
            output_exists = output.exists()

        self.assertEqual(len(ingest_calls), 1)
        self.assertEqual(ingest_calls[0][0], raw_file)
        self.assertEqual(ingest_calls[0][1], root / "source_materials" / "化学平衡")
        self.assertEqual(ingest_calls[0][2], "化学平衡")
        self.assertIs(ingest_calls[0][3], engine.rag_indexer)
        self.assertEqual(engine.rag_indexer.calls, [(root, "化学平衡", output)])
        self.assertTrue(manifest_exists)
        self.assertFalse(output_exists)

    def test_engine_indexes_existing_source_materials_before_loop(self) -> None:
        class FakeIndexer:
            def __init__(self) -> None:
                self.calls: list[tuple[Path, str, Path]] = []

            def is_source_file_indexed(self, root: Path, collection: str, path: Path) -> bool:
                return False

            def index_source_file(self, root: Path, collection: str, path: Path) -> int:
                self.calls.append((root, collection, path))
                return 1

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = root / "source_materials" / "化学平衡"
            source_dir.mkdir(parents=True)
            source = source_dir / "lesson.md"
            source.write_text("# already structured", encoding="utf-8")

            engine = object.__new__(TreeEngine)
            engine.settings = Settings.from_env(root, require_llm=False)
            engine.rag_indexer = FakeIndexer()

            async def fail_if_ingested(*args, **kwargs):
                raise AssertionError("existing source materials should not be OCR'd again")

            engine.ingest = fail_if_ingested

            asyncio.run(TreeEngine._prepare_source_materials_for_loop(engine))
            source_exists = source.exists()

        self.assertEqual(engine.rag_indexer.calls, [(root, "化学平衡", source)])
        self.assertFalse(source_exists)

    def test_run_prepares_source_materials_before_scanning_chapters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events: list[str] = []

            engine = object.__new__(TreeEngine)
            engine.settings = Settings.from_env(root, require_llm=False)
            engine.state_mgr = StateManager(root / "pipeline-state.json")

            class FakeTracer:
                def log_pipeline_start(self) -> None:
                    events.append("start")

                def log_pipeline_complete(self) -> None:
                    events.append("complete")

            async def fake_prepare() -> None:
                events.append("prepare")

            async def fake_scan(state) -> None:
                events.append("scan")
                return None

            engine.tracer = FakeTracer()
            engine._prepare_source_materials_for_loop = fake_prepare
            engine._scan_next_chapter = fake_scan

            asyncio.run(TreeEngine.run(engine))

        self.assertEqual(events, ["start", "prepare", "scan", "complete"])

    def test_step1_reads_current_chapter_from_rag_source_materials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            chapter = ChapterRecord(
                chapter_name="化学平衡",
                status="in_progress",
                source_collection="化学平衡",
            )

            class FakeExaminer:
                def __init__(self) -> None:
                    self.source_material_contents: list[str] = []
                    self.source_material_paths: list[str] = []
                    self.retrieved_context: list[dict] = []

                async def compose_exam(self, *args, **kwargs):
                    self.source_material_contents = kwargs["source_material_contents"]
                    self.source_material_paths = kwargs["source_material_paths"]
                    self.retrieved_context = kwargs["retrieved_context"]
                    return ExamSections(
                        knowledge_point="01. 化学平衡状态",
                        blind_exam="Q",
                        student_instructions="S",
                        answer_key="A",
                        architect_instructions="W",
                    ), False

            class FakeRag:
                def query(self, query_text: str, top_k: int, filters: dict, include_drafts: bool = True):
                    return [{"text": f"{filters['content_kind']} chunk", "metadata": filters}]

                def scroll_chunks(self, filters, include_drafts=False):
                    return [
                        {
                            "text": "# 化学平衡\n源内容",
                            "metadata": {
                                "content_kind": "source",
                                "source_collection": "化学平衡",
                                "path": "source_materials/化学平衡/5.md",
                            },
                        }
                    ]

            settings = Settings.from_env(root, require_llm=False)
            engine = object.__new__(TreeEngine)
            engine.settings = settings
            engine.examiner = FakeExaminer()
            engine.rag_client = FakeRag()

            asyncio.run(TreeEngine._step1_compose(engine, chapter, "01"))

        self.assertEqual(engine.examiner.source_material_contents, [])
        self.assertEqual(engine.examiner.source_material_paths, ["source_materials/化学平衡/5.md"])
        self.assertEqual([hit["text"] for hit in engine.examiner.retrieved_context], ["source chunk", "finished chunk"])

    def test_agent_prompts_are_builtin_without_claude_directory(self) -> None:
        loader = AgentLoader()

        self.assertIn("Examiner", loader.load("examiner"))
        self.assertIn("Evidence-Based Student", loader.load("student"))
        self.assertIn("Content Architect", loader.load("writer"))
        self.assertIn("Archivist", loader.load("archivist"))

    def test_archivist_agent_uses_builtin_prompt(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, str]] = []

            async def call(self, role: str, system_prompt: str, user_prompt: str) -> str:
                self.calls.append((role, system_prompt, user_prompt))
                return "# structured"

        client = FakeClient()
        agent = ArchivistAgent(client, AgentLoader())

        result = asyncio.run(agent.structure("raw OCR text"))

        self.assertEqual(result, "# structured")
        self.assertEqual(client.calls[0][0], "archivist")
        self.assertIn("document structuring specialist", client.calls[0][1])
        self.assertIn("raw OCR text", client.calls[0][2])

    def test_settings_can_load_ocr_only_without_llm_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text(
                "PADDLEOCR_API_URL=https://ocr.example.test/jobs\n"
                "PADDLEOCR_API_TOKEN=token-123\n"
                "PADDLEOCR_MODEL=PaddleOCR-VL-test\n",
                encoding="utf-8",
            )
            env = {
                key: value
                for key, value in os.environ.items()
                if not key.endswith("_API_KEY") and key != "LLM_API_KEY"
            }

            with patch.dict(os.environ, env, clear=True):
                settings = Settings.from_env(root, require_llm=False)

        self.assertEqual(settings.paddleocr_api_url, "https://ocr.example.test/jobs")
        self.assertEqual(settings.paddleocr_api_token, "token-123")
        self.assertEqual(settings.paddleocr_model, "PaddleOCR-VL-test")

    def test_ingest_path_runs_paddleocr_then_archivist_and_writes_markdown(self) -> None:
        class FakeArchivist:
            def __init__(self) -> None:
                self.raw_text = ""

            async def structure(self, raw_text: str) -> str:
                self.raw_text = raw_text
                return "# Clean Markdown\n"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "lesson.pdf"
            input_path.write_bytes(b"%PDF")
            output_dir = root / "source_materials"
            settings = Settings.from_env(root, require_llm=False)
            archivist = FakeArchivist()

            with (
                patch("tree.ingest.get_engine") as get_engine,
                patch("tree.ingest.extract_text", return_value="raw OCR text"),
            ):
                outputs = asyncio.run(
                    ingest_path(input_path, output_dir, settings, archivist=archivist)
                )
            output_text = outputs[0].read_text(encoding="utf-8")

        get_engine.assert_called_once()
        self.assertEqual(archivist.raw_text, "raw OCR text")
        self.assertEqual(len(outputs), 1)
        self.assertEqual(output_text, "# Clean Markdown\n")

    def test_status_does_not_require_llm_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pipeline-state.json").write_text('{"chapters": []}', encoding="utf-8")
            runner = CliRunner()
            env = {
                key: value
                for key, value in os.environ.items()
                if not key.endswith("_API_KEY") and key != "LLM_API_KEY"
            }
            with patch.dict(os.environ, env, clear=True), patch("os.getcwd", return_value=str(root)):
                result = runner.invoke(app, ["status"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("No chapters yet", result.output)

    def test_persist_writer_result_writes_draft_and_returns_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            iter_state = IterationState(
                chapter="力学基础",
                file_seq="01",
                knowledge_point="01. 质点与参考系",
                exam_sections=ExamSections(
                    knowledge_point="01. 质点与参考系",
                    blind_exam="exam",
                    student_instructions="student",
                    answer_key="answer",
                    architect_instructions="architect",
                ),
            )
            result = ArchitectResult(draft_content="# 01. 质点与参考系\n")

            persisted = persist_writer_result(root, iter_state, result)

            self.assertIsNotNone(persisted.draft_path)
            assert persisted.draft_path is not None
            self.assertEqual(persisted.draft_path.name, "01.质点与参考系.md")
            self.assertEqual(persisted.draft_path.read_text(encoding="utf-8"), result.draft_content)

    def test_git_add_commit_can_commit_ignored_runtime_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "tree@example.test"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "T.R.E.E. Test"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            (root / ".gitignore").write_text("/finished_outputs/\n", encoding="utf-8")
            output_dir = root / "finished_outputs" / "chapter"
            output_dir.mkdir(parents=True)
            output = output_dir / "01.demo.md"
            output.write_text("# demo\n", encoding="utf-8")

            git_add_commit(output, "docs: add ignored output", cwd=root)

            listed = subprocess.run(
                ["git", "ls-files", str(output.relative_to(root))],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("finished_outputs/chapter/01.demo.md", listed.stdout)


if __name__ == "__main__":
    unittest.main()
