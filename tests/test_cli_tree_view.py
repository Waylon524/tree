from tree.cli import _current_tree_view_model


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
