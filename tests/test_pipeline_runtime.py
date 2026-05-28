from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from tree.cli import app
from tree.engine import persist_writer_result
from tree.io.git_ops import git_add_commit
from tree.state.models import ArchitectResult, ExamSections, IterationState


class PipelineRuntimeTests(unittest.TestCase):
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
