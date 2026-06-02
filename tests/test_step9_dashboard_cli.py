"""Step 9 CLI/dashboard inspection tests."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from tree.cli.app import app
from tree.cli import theme
from tree.cli.dashboard.dag_view import render_dag
from tree.cli.dashboard.model import build_watch_model
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
                {"node_id": "n1", "title": "A", "collections": ["课件"]},
                {"node_id": "n2", "title": "B", "collections": ["课件"]},
            ]},
        ),
    )
    write_json_atomic(
        paths.knowledge_dag_path(root),
        envelope(
            schema="tree.knowledge-dag",
            data={
                "nodes": [
                    {"node_id": "n1", "title": "A", "collections": ["课件"]},
                    {"node_id": "n2", "title": "B", "collections": ["课件"]},
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
