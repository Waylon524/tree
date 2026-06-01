"""Step 9 CLI/dashboard inspection tests."""

from __future__ import annotations

from typer.testing import CliRunner

from tree.cli.app import app
from tree.cli.dashboard.dag_view import render_dag
from tree.cli.dashboard.model import build_watch_model
from tree.io import paths
from tree.planner.store import envelope, write_json_atomic
from tree.state.manager import StateManager
from tree.state.models import BranchExecutionRecord, BranchRunRecord, PipelineState


def _seed_workspace(root):
    material = root / "materials" / "课件" / "ch1.md"
    material.parent.mkdir(parents=True)
    material.write_text("hello", encoding="utf-8")
    paths.ensure_workspace_dirs(root)
    write_json_atomic(
        paths.progress_path(root),
        {"phase": "running", "message": "working", "source_ingest": {}, "planner": {}, "learning_loop": {}},
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
    write_json_atomic(
        paths.knowledge_branches_path(root),
        envelope(
            schema="tree.knowledge-branches",
            data={"branches": [
                {"branch_id": "kb:1", "node_ids": ["n1", "n2"], "coverage_node_ids": ["n1", "n2"],
                 "start_node_id": "n1", "end_node_id": "n2", "upstream_branch_ids": [],
                 "downstream_branch_ids": [], "display_order": 0}
            ]},
        ),
    )
    StateManager(paths.pipeline_state_path(root)).save(
        PipelineState(
            branch_executions=[
                BranchExecutionRecord(execution_path="kb:1", status="in_progress", branch_id="kb:1")
            ],
            branch_runs=[BranchRunRecord(branch_id="kb:1", run_id="kb:1::run", execution_path="kb:1")],
        )
    )


def test_build_watch_model_summarizes_runtime(tmp_path):
    _seed_workspace(tmp_path)

    model = build_watch_model(tmp_path)

    assert model["phase"] == "running"
    assert model["material_count"] == 1
    assert model["node_count"] == 2
    assert model["edge_count"] == 1
    assert model["branch_count"] == 1
    assert model["active_branch_runs"] == ["kb:1"]


def test_render_dag_marks_active_branch(tmp_path):
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
    assert "working" in runner.invoke(app, ["progress"]).stdout
    assert "课件/ch1.md" in runner.invoke(app, ["materials"]).stdout
    result = runner.invoke(app, ["rag", "nodes"])
    assert result.exit_code == 0
    assert "n1" in result.stdout
    assert "A" in result.stdout
