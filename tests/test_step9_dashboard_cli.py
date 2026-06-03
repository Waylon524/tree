"""Step 9 CLI/dashboard inspection tests."""

from __future__ import annotations

import json

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
        "2026-06-02 ok\nRuntimeError: planner failed while linking nodes\n",
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
    assert "当前: ch1.pdf" in output
    assert "当前: 001. A" in output
    assert "当前: n1" not in output


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
