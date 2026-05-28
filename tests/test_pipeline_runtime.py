from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
import time
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from tree.agents.archivist import ArchivistAgent
from tree.agents.examiner import ExaminerAgent
from tree.agents.loader import AgentLoader
from tree.agents.parsers import ParseError, parse_exam_output
from tree.cli import app
from tree.config import Settings
from tree.engine import TreeEngine, persist_writer_result
from tree.ingest import ingest_path
from tree.io import source_ops
from tree.io.git_ops import git_add_commit
from tree.state.manager import StateManager
from tree.state.models import (
    ChapterRecord,
    ExamSections,
    IterationState,
    PipelineState,
    Route,
    WriterResult,
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

## [Answer_Key]
A1

## [Writer_Instructions]
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

    def test_exam_output_contract_uses_writer_instructions_without_student_instructions(self) -> None:
        sections = parse_exam_output("""## [Next_Knowledge_Point]
01. 化学平衡状态

## [Blind_Exam]
Q1

## [Answer_Key]
A1

## [Writer_Instructions]
Write narrowly.
""")

        self.assertEqual(sections.knowledge_point, "01. 化学平衡状态")
        self.assertEqual(sections.writer_instructions, "Write narrowly.")
        self.assertFalse(hasattr(sections, "student_instructions"))
        self.assertFalse(hasattr(sections, "architect_instructions"))

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

    def test_examiner_compose_repairs_missing_exam_sections(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, str]] = []

            async def call(self, role: str, system_prompt: str, user_prompt: str) -> str:
                self.calls.append((role, system_prompt, user_prompt))
                if len(self.calls) == 1:
                    return "知识点：化学平衡状态\n题目：Q1\n答案：A1"
                return """## [Next_Knowledge_Point]
01. 化学平衡状态

## [Blind_Exam]
Q1

## [Answer_Key]
A1

## [Writer_Instructions]
Write narrowly.
"""

        client = FakeClient()
        examiner = ExaminerAgent(client, AgentLoader(), max_format_retries=1)

        sections, is_complete = asyncio.run(
            examiner.compose_exam(
                "01",
                prior_file_contents=[],
                prior_file_paths=[],
            )
        )

        self.assertFalse(is_complete)
        assert sections is not None
        self.assertEqual(sections.knowledge_point, "01. 化学平衡状态")
        self.assertEqual(len(client.calls), 2)
        self.assertIn("Repair the examiner exam assembly format", client.calls[1][2])

    def test_examiner_scan_repairs_bad_chapter_scan_output(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, str]] = []

            async def call(self, role: str, system_prompt: str, user_prompt: str) -> str:
                self.calls.append((role, system_prompt, user_prompt))
                if len(self.calls) == 1:
                    return "下一章可以叫化学平衡，但是我没有写标准格式。"
                return """## [Next_Knowledge_Point]
化学平衡

## [Blind_Exam]
Q

## [Answer_Key]
A

## [Writer_Instructions]
W
"""

        client = FakeClient()
        examiner = ExaminerAgent(client, AgentLoader(), max_format_retries=1)

        name, is_complete = asyncio.run(
            examiner.scan_next_chapter(
                "{}",
                {"化学平衡": [{"path": "rag:source", "content": "平衡状态"}]},
            )
        )

        self.assertFalse(is_complete)
        self.assertEqual(name, "化学平衡")
        self.assertEqual(len(client.calls), 2)
        self.assertIn("Repair the examiner chapter scan format", client.calls[1][2])

    def test_examiner_logs_unrepairable_exam_output_before_raising(self) -> None:
        class FakeClient:
            async def call(self, role: str, system_prompt: str, user_prompt: str) -> str:
                return "still invalid"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            examiner = ExaminerAgent(
                FakeClient(),
                AgentLoader(),
                max_format_retries=1,
                project_root=root,
            )

            with self.assertRaises(ParseError):
                asyncio.run(
                    examiner.compose_exam(
                        "01",
                        prior_file_contents=[],
                        prior_file_paths=[],
                    )
                )

            failure_files = sorted((root / "pipeline-temp").glob("examiner-format-failure-*.md"))
            failure_text = failure_files[0].read_text(encoding="utf-8")

        self.assertEqual(len(failure_files), 1)
        self.assertIn("still invalid", failure_text)

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

            settings = replace(
                Settings.from_env(root, require_llm=False),
                source_ocr_upload_interval_sec=0,
            )
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
                output_dir.mkdir(parents=True, exist_ok=True)
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
        self.assertIsNone(ingest_calls[0][3])
        self.assertEqual(engine.rag_indexer.calls, [(root, "化学平衡", output)])
        self.assertTrue(manifest_exists)
        self.assertFalse(output_exists)

    def test_engine_ingests_pending_raw_materials_concurrently(self) -> None:
        class FakeIndexer:
            def is_source_file_indexed(self, root: Path, collection: str, path: Path) -> bool:
                return False

            def index_source_file(self, root: Path, collection: str, path: Path) -> int:
                return 1

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_dir = root / "raw_materials" / "课件"
            raw_dir.mkdir(parents=True)
            for idx in range(3):
                (raw_dir / f"lesson-{idx}.pdf").write_bytes(b"%PDF")

            engine = object.__new__(TreeEngine)
            engine.settings = Settings.from_env(root, require_llm=False)
            engine.rag_indexer = FakeIndexer()
            active = 0
            max_active = 0
            lock = asyncio.Lock()

            async def fake_ingest(input_path, output_dir, use_archivist=True, collection=None, indexer=None):
                nonlocal active, max_active
                async with lock:
                    active += 1
                    max_active = max(max_active, active)
                await asyncio.sleep(0.05)
                output_dir.mkdir(parents=True, exist_ok=True)
                output = output_dir / f"{input_path.stem}.md"
                output.write_text("# structured", encoding="utf-8")
                async with lock:
                    active -= 1
                return [output]

            engine.ingest = fake_ingest

            asyncio.run(TreeEngine._prepare_source_materials_for_loop(engine))

        self.assertGreater(max_active, 1)

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
                        answer_key="A",
                        writer_instructions="W",
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

            settings = replace(
                Settings.from_env(root, require_llm=False),
                source_ocr_upload_interval_sec=0,
            )
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
        self.assertIn("Content Writer", loader.load("writer"))
        self.assertIn("Archivist", loader.load("archivist"))

    def test_student_agent_uses_builtin_instructions_not_examiner_supplied_instructions(self) -> None:
        from tree.agents.student import StudentAgent

        class FakeClient:
            def __init__(self) -> None:
                self.system_prompt = ""
                self.user_prompt = ""

            async def call(self, role: str, system_prompt: str, user_prompt: str) -> str:
                self.system_prompt = system_prompt
                self.user_prompt = user_prompt
                return "answer"

        client = FakeClient()
        agent = StudentAgent(client, AgentLoader())

        asyncio.run(
            agent.blind_test(
                blind_exam="Q",
                prior_file_contents=[],
                prior_file_paths=[],
            )
        )

        self.assertIn("Mandatory Answer Protocol", client.system_prompt)
        self.assertNotIn("Student_Instructions", client.system_prompt)
        self.assertNotIn("Student_Instructions", client.user_prompt)

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

    def test_settings_default_to_paddleocr_vl_16_and_rate_limited_uploads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                key: value
                for key, value in os.environ.items()
                if key != "PADDLEOCR_MODEL" and key != "SOURCE_OCR_UPLOAD_INTERVAL_SEC"
            }
            with patch.dict(os.environ, env, clear=True):
                settings = Settings.from_env(Path(tmp), require_llm=False)

        self.assertEqual(settings.paddleocr_model, "PaddleOCR-VL-1.6")
        self.assertEqual(settings.source_ocr_upload_interval_sec, 5.0)

    def test_ocr_engine_submits_paddleocr_vl_16_payload(self) -> None:
        from ingest.ocr_engine import OCREngine

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"data": {"jobId": "job-1"}}

        class FakeClient:
            def __init__(self) -> None:
                self.data = None

            def post(self, url, headers=None, data=None, files=None, json=None):
                self.data = data
                return FakeResponse()

        with tempfile.TemporaryDirectory() as tmp:
            env = {key: value for key, value in os.environ.items() if key != "PADDLEOCR_MODEL"}
            with patch.dict(os.environ, env, clear=True):
                OCREngine._instance = None
                engine = OCREngine(token="token")
                fake_client = FakeClient()
                engine._client = fake_client
                input_path = Path(tmp) / "lesson.pdf"
                input_path.write_bytes(b"%PDF")

                job_id = engine._submit_local(str(input_path))

        self.assertEqual(job_id, "job-1")
        self.assertEqual(fake_client.data["model"], "PaddleOCR-VL-1.6")
        self.assertEqual(
            json.loads(fake_client.data["optionalPayload"]),
            {
                "useDocOrientationClassify": False,
                "useDocUnwarping": False,
                "useChartRecognition": False,
            },
        )
        OCREngine._instance = None

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
            settings = replace(
                Settings.from_env(root, require_llm=False),
                source_ocr_upload_interval_sec=0,
            )
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

    def test_ingest_path_runs_directory_ocr_concurrently(self) -> None:
        class FakeArchivist:
            async def structure(self, raw_text: str) -> str:
                return f"# {raw_text}\n"

        active = 0
        max_active = 0
        lock = threading.Lock()

        def fake_extract(path: Path) -> str:
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return path.stem

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "raw"
            input_dir.mkdir()
            for idx in range(3):
                (input_dir / f"lesson-{idx}.pdf").write_bytes(b"%PDF")
            settings = replace(
                Settings.from_env(root, require_llm=False),
                source_ocr_upload_interval_sec=0,
            )

            with (
                patch("tree.ingest.get_engine"),
                patch("tree.ingest.extract_text", side_effect=fake_extract),
            ):
                outputs = asyncio.run(
                    ingest_path(
                        input_dir,
                        root / "source_materials" / "课件",
                        settings,
                        archivist=FakeArchivist(),
                    )
                )

        self.assertEqual(len(outputs), 3)
        self.assertGreater(max_active, 1)

    def test_ingest_path_rate_limits_ocr_upload_starts(self) -> None:
        starts: list[float] = []
        lock = threading.Lock()

        def fake_extract(path: Path) -> str:
            with lock:
                starts.append(time.perf_counter())
            return path.stem

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "raw"
            input_dir.mkdir()
            for idx in range(3):
                (input_dir / f"lesson-{idx}.pdf").write_bytes(b"%PDF")
            settings = replace(
                Settings.from_env(root, require_llm=False),
                source_ocr_upload_interval_sec=0.02,
            )

            with (
                patch("tree.ingest.get_engine"),
                patch("tree.ingest.extract_text", side_effect=fake_extract),
            ):
                outputs = asyncio.run(
                    ingest_path(
                        input_dir,
                        root / "source_materials" / "课件",
                        settings,
                        archivist=None,
                    )
                )

        self.assertEqual(len(outputs), 3)
        starts.sort()
        gaps = [later - earlier for earlier, later in zip(starts, starts[1:])]
        self.assertTrue(all(gap >= 0.015 for gap in gaps), gaps)

    def test_ingest_path_runs_archivist_calls_concurrently(self) -> None:
        class FakeArchivist:
            async def structure(self, raw_text: str) -> str:
                nonlocal active, max_active
                async with lock:
                    active += 1
                    max_active = max(max_active, active)
                await asyncio.sleep(0.05)
                async with lock:
                    active -= 1
                return f"# {raw_text}\n"

        active = 0
        max_active = 0
        lock = asyncio.Lock()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "raw"
            input_dir.mkdir()
            for idx in range(6):
                (input_dir / f"lesson-{idx}.pdf").write_bytes(b"%PDF")
            settings = replace(
                Settings.from_env(root, require_llm=False),
                source_ocr_upload_interval_sec=0,
            )

            with (
                patch("tree.ingest.get_engine"),
                patch("tree.ingest.extract_text", side_effect=lambda path: path.stem),
            ):
                outputs = asyncio.run(
                    ingest_path(
                        input_dir,
                        root / "source_materials" / "课件",
                        settings,
                        archivist=FakeArchivist(),
                    )
                )

        self.assertEqual(len(outputs), 6)
        self.assertEqual(max_active, 6)

    def test_ingest_path_splits_large_ocr_text_by_headings_without_merging(self) -> None:
        class FakeArchivist:
            def __init__(self) -> None:
                self.calls: list[str] = []

            async def structure(self, raw_text: str) -> str:
                self.calls.append(raw_text)
                return f"STRUCTURED:\n{raw_text}"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "lesson.pdf"
            input_path.write_bytes(b"%PDF")
            settings = replace(
                Settings.from_env(root, require_llm=False),
                source_archivist_chunk_chars=120,
                source_ocr_upload_interval_sec=0,
            )
            archivist = FakeArchivist()
            raw_text = (
                "# 化学平衡通论\n"
                "课程导言\n\n"
                "## 第一节 化学平衡状态\n"
                f"{'A' * 180}\n"
                "### 判断标志\n"
                f"{'B' * 80}\n\n"
                "## 第二节 平衡常数\n"
                f"{'C' * 100}\n"
            )

            with (
                patch("tree.ingest.get_engine"),
                patch("tree.ingest.extract_text", return_value=raw_text),
            ):
                outputs = asyncio.run(
                    ingest_path(
                        input_path,
                        root / "source_materials" / "课件",
                        settings,
                        archivist=archivist,
                    )
                )
            output_text = outputs[0].read_text(encoding="utf-8")

        self.assertEqual([path.name for path in outputs], ["lesson__part-01.md", "lesson__part-02.md"])
        self.assertEqual(len(archivist.calls), 2)
        self.assertIn("## 第一节 化学平衡状态", archivist.calls[0])
        self.assertIn("### 判断标志", archivist.calls[0])
        self.assertNotIn("## 第二节 平衡常数", archivist.calls[0])
        self.assertIn("## 第二节 平衡常数", archivist.calls[1])
        self.assertTrue(output_text.startswith("STRUCTURED:"))

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
                    answer_key="answer",
                    writer_instructions="writer",
                ),
            )
            result = WriterResult(draft_content="# 01. 质点与参考系\n")

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
