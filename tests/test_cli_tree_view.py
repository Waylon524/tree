from types import SimpleNamespace

from rich.console import Console

from tree.cli import (
    _build_progress_view,
    _current_tree_view_model,
    _dag_tree_panel,
    build_watch_display_model,
    build_dag_watch_model,
    render_branch_run_slots,
    render_dag_ascii,
    render_dag_legend,
    render_source_processing_panel,
)
from tree.state.models import BranchRunRecord, PipelineState


def _finished(node_id: str, title: str, *, required: list[str] | None = None) -> dict:
    return {
        "node_id": node_id,
        "kind": "finished",
        "status": "finished",
        "title": title,
        "chapter": "tree-001",
        "required_nodes": required or [],
        "core_concepts": [title],
    }


def _candidate(node_id: str, title: str, **extra: object) -> dict:
    node = {
        "node_id": node_id,
        "kind": "candidate",
        "status": "planned",
        "title": title,
        "core_concepts": [title],
        "required_nodes": [],
        "parent_output": None,
        "supporting_parents": [],
        "planner_selected": True,
        "is_new_root": False,
        "branch_score": 0.0,
        "support_score": 0.0,
    }
    node.update(extra)
    return node


def test_current_tree_view_uses_required_nodes_and_selected_candidate_parents() -> None:
    root = _finished("finished:outputs/tree-001/01.variables.md", "变量")
    child = _finished(
        "finished:outputs/tree-001/02.conditionals.md",
        "条件判断",
        required=["finished:outputs/tree-001/01.variables.md"],
    )
    selected = _candidate(
        "candidate:loops",
        "循环",
        parent_output="finished:outputs/tree-001/02.conditionals.md",
        supporting_parents=[
            {"node_id": "finished:outputs/tree-001/01.variables.md", "score": 0.44}
        ],
        branch_score=0.55,
        support_score=0.62,
    )
    graph = {
        "nodes": [root, child, selected],
        "edges": [
            {
                "from": "finished:outputs/tree-001/02.conditionals.md",
                "to": "candidate:loops",
                "relation": "branch",
                "scores": {},
            },
            {
                "from": "finished:outputs/tree-001/01.variables.md",
                "to": "candidate:loops",
                "relation": "supporting_parent",
                "scores": {},
            },
        ],
        "planner": {"selected_node": "candidate:loops", "selection_mode": "branch"},
    }

    model = _current_tree_view_model(graph, active_chapter="tree-001")

    assert model["mode"] == "tree"
    assert model["current_tree"] == "tree-001"
    assert model["selected_node"] == "candidate:loops"
    assert [row["node_id"] for row in model["nodes"]] == [
        "finished:outputs/tree-001/01.variables.md",
        "finished:outputs/tree-001/02.conditionals.md",
        "candidate:loops",
    ]
    assert model["nodes"][2]["marker"] == "▶"
    assert model["nodes"][2]["parents"] == [
        "finished:outputs/tree-001/02.conditionals.md",
        "finished:outputs/tree-001/01.variables.md",
    ]


def test_current_tree_view_degrades_when_parent_reference_is_missing() -> None:
    graph = {
        "nodes": [
            _finished(
                "finished:outputs/tree-001/02.conditionals.md",
                "条件判断",
                required=["finished:outputs/tree-001/01.variables.md"],
            )
        ],
        "edges": [],
        "planner": {},
    }

    model = _current_tree_view_model(graph, active_chapter="tree-001")

    assert model["mode"] == "table"
    assert "missing parent" in model["reason"]
    assert model["nodes"][0]["parent_status"] == "missing"


def test_current_tree_view_allows_new_root_candidate_without_parent() -> None:
    selected = _candidate("candidate:database", "数据库事务", is_new_root=True)
    old_root = _finished("finished:outputs/tree-001/01.variables.md", "变量")
    graph = {
        "nodes": [old_root, selected],
        "edges": [],
        "planner": {"selected_node": "candidate:database", "selection_mode": "new_root"},
    }

    model = _current_tree_view_model(graph, active_chapter="")

    assert model["mode"] == "tree"
    assert [row["node_id"] for row in model["nodes"]] == ["candidate:database"]
    assert model["nodes"][0]["marker"] == "▶"
    assert model["nodes"][0]["parents"] == []


def test_dag_watch_model_numbers_full_dag_and_marks_statuses() -> None:
    dag = {
        "nodes": [
            {"node_id": "candidate:root", "title": "根节点"},
            {"node_id": "candidate:left", "title": "左分支"},
            {"node_id": "candidate:right", "title": "右分支"},
            {"node_id": "candidate:join", "title": "汇合"},
        ],
        "edges": [
            {"from": "candidate:root", "to": "candidate:left"},
            {"from": "candidate:root", "to": "candidate:right"},
            {"from": "candidate:left", "to": "candidate:join"},
            {"from": "candidate:right", "to": "candidate:join"},
        ],
    }
    branches = {
        "branches": [
            {
                "branch_id": "branch:root-left",
                "node_ids": ["candidate:root", "candidate:left"],
                "coverage_node_ids": ["candidate:root", "candidate:left"],
                "start_node_id": "candidate:root",
                "end_node_id": "candidate:left",
                "status": "complete",
                "coverage": {"covered_node_ids": ["candidate:root", "candidate:left"], "missing_node_ids": []},
            },
            {
                "branch_id": "branch:right-join",
                "node_ids": ["candidate:right", "candidate:join"],
                "coverage_node_ids": ["candidate:right", "candidate:join"],
                "start_node_id": "candidate:right",
                "end_node_id": "candidate:join",
                "status": "running",
                "coverage": {"covered_node_ids": [], "missing_node_ids": ["candidate:right", "candidate:join"]},
            },
        ]
    }
    ledger = {
        "records": [
            {"covered_node_ids": ["candidate:root"], "graph_node_id": "candidate:root"}
        ]
    }
    state = PipelineState(
        branch_runs=[
            BranchRunRecord(branch_id="branch:right-join", run_id="run:1", execution_path="tree-001/branch-right")
        ]
    )

    model = build_dag_watch_model(dag, branches, state, ledger)

    assert [node["label"] for node in model["nodes"]] == ["N01", "N02", "N03", "N04"]
    assert model["node_by_id"]["candidate:root"]["status"] == "已完成"
    assert model["node_by_id"]["candidate:join"]["status"] == "未完成"
    assert model["branch_by_id"]["branch:root-left"]["label"] == "B01"
    assert model["branch_by_id"]["branch:root-left"]["status"] == "已完成"
    assert model["branch_by_id"]["branch:right-join"]["status"] == "进行中"


def test_dag_ascii_uses_number_only_nodes_and_running_blink_style() -> None:
    dag = {
        "nodes": [
            {"node_id": "candidate:root", "title": "根节点"},
            {"node_id": "candidate:leaf", "title": "叶子节点"},
        ],
        "edges": [{"from": "candidate:root", "to": "candidate:leaf"}],
    }
    branches = {
        "branches": [
            {
                "branch_id": "branch:main",
                "node_ids": ["candidate:root", "candidate:leaf"],
                "coverage_node_ids": ["candidate:root", "candidate:leaf"],
                "start_node_id": "candidate:root",
                "end_node_id": "candidate:leaf",
                "status": "running",
                "coverage": {"covered_node_ids": ["candidate:root"], "missing_node_ids": ["candidate:leaf"]},
            }
        ]
    }
    state = PipelineState(branch_runs=[BranchRunRecord(branch_id="branch:main", run_id="run:main")])
    model = build_dag_watch_model(
        dag,
        branches,
        state,
        {"records": [{"covered_node_ids": ["candidate:root"]}]},
    )

    text = render_dag_ascii(model, width=100)

    assert "N01" in text.plain
    assert "N02" in text.plain
    assert "根节点" not in text.plain
    assert "叶子节点" not in text.plain
    assert "▶B01" in text.plain
    styles = [str(span.style) for span in text.spans]
    assert any("green" in style for style in styles)
    assert any("blink" in style and "#d2b48c" in style for style in styles)


def test_dag_legend_renders_chinese_titles_and_blocked_diagnostics() -> None:
    model = build_dag_watch_model(
        {
            "nodes": [{"node_id": "candidate:blocked", "title": "阻塞节点"}],
            "edges": [],
            "diagnostics": [{"kind": "canonical_merge_leak", "reason": "重复节点"}],
        },
        {
            "branches": [
                {
                    "branch_id": "branch:blocked",
                    "node_ids": ["candidate:blocked"],
                    "coverage_node_ids": ["candidate:blocked"],
                    "start_node_id": "candidate:blocked",
                    "end_node_id": "candidate:blocked",
                    "status": "blocked",
                    "blocked_reason": "canonical_merge_leak",
                    "coverage": {"covered_node_ids": [], "missing_node_ids": ["candidate:blocked"]},
                }
            ],
            "diagnostics": [{"kind": "canonical_merge_leak", "reason": "重复节点"}],
        },
        PipelineState(),
        {"records": []},
    )

    console = Console(record=True, width=120)
    console.print(render_dag_legend(model))
    output = console.export_text()

    assert "节点图例" in output
    assert "分支图例" in output
    assert "阻塞节点" in output
    assert "阻塞" in output
    assert "canonical_merge_leak" in output


def test_dag_tree_panel_handles_missing_dag_with_friendly_empty_state(tmp_path) -> None:
    console = Console(record=True, width=120)

    console.print(_dag_tree_panel(tmp_path))
    output = console.export_text()

    assert "项目学习图" in output
    assert "暂无 KnowledgeDAG" in output


def test_progress_view_shows_source_processing_before_first_branchrun(tmp_path, monkeypatch) -> None:
    import tree.services

    monkeypatch.setattr(
        tree.services,
        "service_status",
        lambda _root, name: SimpleNamespace(running=False, pid=None, log_path=tmp_path / f"{name}.log"),
    )
    console = Console(record=True, width=140)

    console.print(_build_progress_view(tmp_path))
    output = console.export_text()

    assert "资料处理进度" in output
    assert "项目学习图" not in output


def test_watch_display_model_uses_source_processing_before_first_branchrun() -> None:
    state = PipelineState()
    progress = {
        "phase": "learning_loop",
        "source_ingest": {
            "ocr": {"files_done": 1, "files_total": 2, "current_file": "lesson.pdf"},
            "embedding": {"chunks_done": 3, "chunks_total": 8, "current_chunk": "lesson#003"},
        },
        "learning_loop": {
            "stage": "refresh_branch_plan",
            "stage_label": "Refreshing branch plan",
            "stage_index": 1,
            "stage_total": 6,
        },
    }

    model = build_watch_display_model(progress, state, {}, {}, {"records": []})
    console = Console(record=True, width=120)
    console.print(render_source_processing_panel(model))
    output = console.export_text()

    assert model["mode"] == "source_processing"
    assert "资料处理进度" in output
    assert "OCR" in output
    assert "Embedding" in output
    assert "Planner" in output
    assert "lesson.pdf" in output


def test_watch_display_model_keeps_dag_after_branchrun_exists_without_running() -> None:
    state = PipelineState(
        branch_runs=[
            BranchRunRecord(
                branch_id="branch:main",
                run_id="run:done",
                status="complete",
                execution_path="tree-001/branch-main",
            )
        ]
    )
    dag = {"nodes": [{"node_id": "candidate:root", "title": "根节点"}], "edges": []}
    branches = {
        "branches": [
            {
                "branch_id": "branch:main",
                "node_ids": ["candidate:root"],
                "coverage_node_ids": ["candidate:root"],
                "start_node_id": "candidate:root",
                "end_node_id": "candidate:root",
                "status": "complete",
                "coverage": {"covered_node_ids": ["candidate:root"], "missing_node_ids": []},
            }
        ]
    }

    model = build_watch_display_model({}, state, dag, branches, {"records": []})
    console = Console(record=True, width=120)
    console.print(render_branch_run_slots(model))
    output = console.export_text()

    assert model["mode"] == "dag"
    assert "循环 1" in output
    assert "循环 2" in output
    assert output.count("空闲") == 2


def test_branch_run_slots_show_two_independent_progress_entries() -> None:
    state = PipelineState(
        branch_runs=[
            BranchRunRecord(
                branch_id="branch:left",
                run_id="run:left",
                status="running",
                execution_path="tree-001/branch-left",
            ),
            BranchRunRecord(
                branch_id="branch:right",
                run_id="run:right",
                status="running",
                execution_path="tree-001/branch-right",
            ),
        ]
    )
    progress = {
        "branch_run_progress": {
            "run:left": {
                "stage": "student_blind_test",
                "stage_label": "Student blind test",
                "stage_index": 3,
                "stage_total": 6,
                "execution_path": "tree-001/branch-left",
                "branch_id": "branch:left",
                "file_seq": "02",
                "span_title": "左分支知识",
                "iteration": 2,
            },
            "run:right": {
                "stage": "writer_drafting",
                "stage_label": "Writer drafting",
                "stage_index": 5,
                "stage_total": 6,
                "execution_path": "tree-001/branch-right",
                "branch_id": "branch:right",
                "file_seq": "01",
                "span_title": "右分支知识",
                "iteration": 1,
            },
        }
    }

    model = build_watch_display_model(progress, state, {"nodes": [], "edges": []}, {"branches": []}, {"records": []})
    console = Console(record=True, width=140)
    console.print(render_branch_run_slots(model))
    output = console.export_text()

    assert "循环 1" in output
    assert "循环 2" in output
    assert "Student blind test" in output
    assert "Writer drafting" in output
    assert "左分支知识" in output
    assert "右分支知识" in output
    assert "iteration 2" in output
    assert "iteration 1" in output


def test_branch_run_slots_warn_when_more_than_two_running() -> None:
    state = PipelineState(
        branch_runs=[
            BranchRunRecord(branch_id="branch:1", run_id="run:1", status="running"),
            BranchRunRecord(branch_id="branch:2", run_id="run:2", status="running"),
            BranchRunRecord(branch_id="branch:3", run_id="run:3", status="running"),
        ]
    )

    model = build_watch_display_model({}, state, {"nodes": [], "edges": []}, {"branches": []}, {"records": []})
    console = Console(record=True, width=120)
    console.print(render_branch_run_slots(model))
    output = console.export_text()

    assert "+1 running not shown" in output
