"""Step 9 CLI/dashboard inspection tests."""

from __future__ import annotations

import json
import sys

from typer.testing import CliRunner

from tree.cli.app import app
from tree.cli import theme
from tree.cli.dashboard import live
from tree.cli.dashboard.dag_view import render_dag
from tree.cli.dashboard.model import build_watch_model
from tree.cli.dashboard.panels import render_watch, watch_renderable
from tree.io import paths
from tree.planner.store import envelope, write_json_atomic
from tree.state.manager import StateManager
from tree.state.models import NodeExecutionRecord, NodeRunRecord, PipelineState


def _seed_workspace(root):
    material = root / "materials" / "课件" / "ch1.md"
    material.parent.mkdir(parents=True)
    material.write_text("hello", encoding="utf-8")
    paths.ensure_workspace_dirs(root)
    write_json_atomic(
        paths.progress_path(root),
        {
            "phase": "running",
            "message": "working",
            "source_ingest": {},
            "planner": {},
            "learning_loop": {},
            "stages": {
                "ocr": {"label": "OCR", "done": 1, "total": 2, "active": ["ch1.pdf"], "status": "running", "message": ""},
                "clean": {"label": "Clean", "done": 0, "total": 2, "active": [], "status": "pending", "message": ""},
                "cut": {"label": "Cut", "done": 0, "total": 2, "active": [], "status": "pending", "message": ""},
                "embed": {"label": "Embed", "done": 0, "total": 0, "active": [], "status": "pending", "message": ""},
                "cluster": {"label": "Cluster", "done": 0, "total": 0, "active": [], "status": "pending", "message": ""},
                "link": {"label": "Link", "done": 0, "total": 0, "active": [], "status": "pending", "message": ""},
                "noderun": {"label": "NodeRun", "done": 0, "total": 2, "active": ["n1"], "status": "running", "message": ""},
            },
        },
    )
    write_json_atomic(
        paths.knowledge_nodes_path(root),
        envelope(
            schema="tree.knowledge-nodes",
            data={"knowledge_nodes": [
                {"node_id": "n1", "title": "A", "collections": ["课件"], "source_order_index": 0},
                {"node_id": "n2", "title": "B", "collections": ["课件"], "source_order_index": 1},
            ]},
        ),
    )
    write_json_atomic(
        paths.knowledge_dag_path(root),
        envelope(
            schema="tree.knowledge-dag",
            data={
                "nodes": [
                    {"node_id": "n1", "title": "A", "collections": ["课件"], "source_order_index": 0},
                    {"node_id": "n2", "title": "B", "collections": ["课件"], "source_order_index": 1},
                ],
                "edges": [{"from_node_id": "n1", "to_node_id": "n2", "relation": "prerequisite"}],
                "roots": ["n1"],
            },
        ),
    )
    StateManager(paths.pipeline_state_path(root)).save(
        PipelineState(
            node_executions=[NodeExecutionRecord(node_id="n1", status="in_progress")],
            node_runs=[NodeRunRecord(node_id="n1", run_id="n1::run")],
        )
    )


def test_build_watch_model_summarizes_runtime(tmp_path):
    _seed_workspace(tmp_path)

    model = build_watch_model(tmp_path)

    assert model["phase"] == "running"
    assert model["material_count"] == 1
    assert model["node_count"] == 2
    assert model["edge_count"] == 1
    assert model["active_node_runs"] == ["n1"]
    assert model["node_display_labels"]["n1"] == "001. A"


def test_build_watch_model_prefers_planner_node_count_during_link(tmp_path):
    _seed_workspace(tmp_path)
    progress = json.loads(paths.progress_path(tmp_path).read_text(encoding="utf-8"))
    progress["planner"]["node_count"] = 94
    progress["stages"]["link"]["total"] = 94
    progress["stages"]["link"]["status"] = "running"
    write_json_atomic(paths.progress_path(tmp_path), progress)

    model = build_watch_model(tmp_path)

    assert len(model["nodes"]) == 2
    assert model["node_count"] == 94
    assert "nodes 94" in render_watch(tmp_path)


def test_render_dag_marks_active_node(tmp_path):
    _seed_workspace(tmp_path)

    output = render_dag(build_watch_model(tmp_path))

    assert "▶" in output
    assert "n1" in output
    assert "A" in output
    assert "n1 -> n2" in output


def test_status_progress_materials_and_rag_nodes_cli(tmp_path, monkeypatch):
    _seed_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    assert runner.invoke(app, ["status"]).exit_code == 0
    assert "phase" in runner.invoke(app, ["status"]).stdout
    progress = runner.invoke(app, ["progress"])
    assert "working" in progress.stdout
    assert theme.TREE_GREEN not in progress.stdout
    assert json.loads(progress.stdout)["message"] == "working"
    watch = runner.invoke(app, ["watch"])
    assert watch.exit_code == 0
    assert "OCR" in watch.stdout
    assert "Cluster" in watch.stdout
    assert "NodeRun" in watch.stdout
    assert "当前: ch1.pdf" in watch.stdout
    assert "n1 -> n2" not in watch.stdout
    assert "课件/ch1.md" in runner.invoke(app, ["materials"]).stdout
    result = runner.invoke(app, ["rag", "nodes"])
    assert result.exit_code == 0
    assert "n1" in result.stdout
    assert "A" in result.stdout


def test_rag_search_ensures_embedding_service(tmp_path, monkeypatch):
    _seed_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    calls = []

    monkeypatch.setattr("tree.cli.app.ensure_embedding_ready", lambda: calls.append("embed"))
    monkeypatch.setattr("tree.cli.commands.rag.search_text", lambda root, query, top_k=5: "hit")

    result = CliRunner().invoke(app, ["rag", "search", "化学平衡"])

    assert result.exit_code == 0
    assert calls == ["embed"]
    assert "hit" in result.stdout


def test_watch_rendering_wraps_dashboard_and_surfaces_errors(tmp_path):
    _seed_workspace(tmp_path)
    log_path = paths.service_log_path(tmp_path, "engine")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "2026-06-02 ok\n"
        "Exception ignored in: <function QdrantClient.__del__ at 0x123>\n"
        "ImportError: sys.meta_path is None, Python is likely shutting down\n"
        "RuntimeError: planner failed while linking nodes\n",
        encoding="utf-8",
    )
    progress = json.loads(paths.progress_path(tmp_path).read_text(encoding="utf-8"))
    progress["phase"] = "blocked"
    progress["message"] = "TREE_BLOCKED - no ready node runs"
    progress["stages"]["clean"]["done"] = 2
    progress["stages"]["clean"]["status"] = "complete"
    progress["stages"]["cut"]["done"] = 2
    progress["stages"]["cut"]["status"] = "complete"
    progress["stages"]["link"]["status"] = "failed"
    progress["stages"]["link"]["message"] = "invalid prerequisite edge"
    write_json_atomic(paths.progress_path(tmp_path), progress)

    output = render_watch(tmp_path)
    markup = str(watch_renderable(tmp_path).renderable)

    assert "╭" in output
    assert "╰" in output
    assert "Overview" in output
    assert "materials 1" in output
    assert "nodes 2" in output
    assert "edges" not in output
    assert "phase blocked" not in output
    assert "message TREE_BLOCKED" not in output
    assert "active 1" in output
    assert "Stage" in output
    assert "█" in output
    assert "░" in output
    assert "#" not in output
    assert "50%" in output
    assert "RUNNING" in output
    assert "COMPLETE" in output
    assert "DONE" not in output
    assert "WAIT" in output
    assert "FAILED" in output
    assert f"[{theme.TREE_BROWN}]RUNNING" in markup
    assert f"[{theme.TREE_GREEN}]COMPLETE" in markup
    assert "Press ESC" in output
    assert "Errors" in output
    assert "TREE_BLOCKED - no ready node runs" in output
    assert "Link: failed - invalid prerequisite edge" in output
    assert "RuntimeError: planner failed while linking nodes" in output
    assert "QdrantClient.__del__" not in output
    assert "Python is likely shutting down" not in output
    assert "当前: ch1.pdf" in output
    assert "当前: 001. A" in output
    assert "当前: n1" not in output
    assert f"当前: [{theme.TREE_GREEN}]ch1.pdf" in markup
    assert f"当前: [{theme.TREE_BROWN}]001. A" in markup
    assert f"当前: [{theme.TREE_GREEN}]001. A" not in markup


def test_watch_prefers_one_structured_error_over_stage_and_log_duplicates(tmp_path):
    _seed_workspace(tmp_path)
    paths.service_log_path(tmp_path, "engine").write_text(
        "RuntimeError: AES provider is unavailable\n"
        "NameError: name 'open' is not defined\n",
        encoding="utf-8",
    )
    progress = json.loads(paths.progress_path(tmp_path).read_text(encoding="utf-8"))
    progress["phase"] = "failed"
    progress["message"] = "AES provider is unavailable"
    progress["stages"]["ocr"].update(
        {"status": "failed", "message": "AES provider is unavailable"}
    )
    progress["errors"] = [
        {
            "run_id": "run-1",
            "generation_id": "gen-1",
            "stage": "ocr",
            "code": "pdf_crypto_missing",
            "resource": "encrypted.pdf",
            "message": "AES provider is unavailable",
            "retry_count": 0,
            "recoverable": True,
            "action": "Install crypto support and resume.",
        }
    ]
    write_json_atomic(paths.progress_path(tmp_path), progress)

    errors = build_watch_model(tmp_path)["errors"]

    assert errors == [
        "encrypted.pdf: AES provider is unavailable — Action: Install crypto support and resume."
    ]


def test_watch_noderun_pending_does_not_show_stale_current(tmp_path):
    _seed_workspace(tmp_path)
    progress = json.loads(paths.progress_path(tmp_path).read_text(encoding="utf-8"))
    progress["stages"]["noderun"]["status"] = "pending"
    progress["stages"]["noderun"]["active"] = ["n1"]
    progress["stages"]["noderun"]["message"] = ""
    write_json_atomic(paths.progress_path(tmp_path), progress)

    output = render_watch(tmp_path)
    noderun_line = next(line for line in output.splitlines() if "NodeRun" in line)

    assert "WAIT" in noderun_line
    assert "当前:" not in noderun_line


def test_watch_command_delegates_to_live_dashboard(tmp_path, monkeypatch):
    _seed_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr("tree.cli.app.run_watch_panel", lambda root: calls.append(root), raising=False)

    result = CliRunner().invoke(app, ["watch"])

    assert result.exit_code == 0
    assert calls == [tmp_path]


def test_live_watch_loop_exits_on_escape(tmp_path, monkeypatch):
    _seed_workspace(tmp_path)
    printed = []
    updates = []

    class _Console:
        is_terminal = True

        def print(self, value):
            printed.append(value)

    class _Live:
        def __init__(self, renderable, **kwargs):
            self.renderable = renderable

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def update(self, renderable):
            updates.append(renderable)

    monkeypatch.setattr(live, "Live", _Live)
    monkeypatch.setattr(live, "_raw_terminal", lambda stream: _null_context())
    monkeypatch.setattr(live, "_escape_pressed", lambda stream, timeout: True)
    monkeypatch.setattr(live.sys, "stdin", _TtyInput())

    live.run_watch(tmp_path, console=_Console(), refresh_seconds=0.01)

    assert printed == []
    assert updates == []


def test_live_watch_uses_msvcrt_on_windows(monkeypatch):
    calls = []

    class _Msvcrt:
        @staticmethod
        def kbhit():
            calls.append("kbhit")
            return True

        @staticmethod
        def getwch():
            calls.append("getwch")
            return "\x1b"

    monkeypatch.setattr(live.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "msvcrt", _Msvcrt)

    assert live._escape_pressed(_TtyInput(), timeout=0.01) is True
    with live._raw_terminal(_TtyInput()):
        calls.append("raw")

    assert calls == ["kbhit", "getwch", "raw"]


class _TtyInput:
    def isatty(self):
        return True

    def fileno(self):
        return 0


class _null_context:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return False
