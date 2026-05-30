from pathlib import Path

from tree.curriculum.graph import rebuild_knowledge_graph


def _candidate(
    candidate_id: str,
    concepts: list[str],
    *,
    prerequisites: list[str] | None = None,
    chunks: list[str] | None = None,
    sources: list[str] | None = None,
    priority: float = 0.5,
) -> dict:
    return {
        "candidate_id": candidate_id,
        "status": "pending",
        "title_hint": candidate_id.rsplit(":", 1)[-1],
        "primary_source_collection": (sources or ["source"])[0],
        "source_collections": sources or ["source"],
        "core_concepts": concepts,
        "prerequisite_concepts": prerequisites or [],
        "prerequisite_candidates": [],
        "representative_chunks": [
            {"chunk_ref": chunk, "core_concepts": concepts, "summary": " ".join(concepts)}
            for chunk in chunks or []
        ],
        "selection_priority": priority,
    }


def _ledger_record(
    path: str,
    concepts: list[str],
    *,
    prerequisites: list[str] | None = None,
    chunks: list[str] | None = None,
    sources: list[str] | None = None,
    graph_node_id: str | None = None,
    required_nodes: list[str] | None = None,
) -> dict:
    return {
        "chapter": "chapter",
        "file_seq": path.split("/")[-1].split(".", 1)[0],
        "filename": path.split("/")[-1],
        "path": path,
        "knowledge_point": concepts[0],
        "covered_concepts": concepts,
        "prerequisites": prerequisites or [],
        "hit_chunks": chunks or [],
        "source_collections": sources or [],
        "graph_node_id": graph_node_id,
        "required_nodes": required_nodes or [],
    }


def _candidate_nodes(*candidates: dict) -> dict:
    return {"version": 1, "kind": "candidate_nodes", "chapter_candidates": list(candidates)}


def test_planner_selects_only_one_root_before_finished_outputs(tmp_path: Path) -> None:
    graph = rebuild_knowledge_graph(
        tmp_path,
        _candidate_nodes(
            _candidate("candidate:root", ["变量"], chunks=["source#001"], priority=0.9),
            _candidate("candidate:branch", ["循环"], prerequisites=["变量"], chunks=["source#002"]),
        ),
        {"version": 1, "records": []},
    )

    selected = graph["planner"]["selected_node"]
    selected_node = next(node for node in graph["nodes"] if node["node_id"] == selected)

    assert graph["planner"]["mode"] == "incremental_forest_v1"
    assert selected == "candidate:root"
    assert selected_node["is_new_root"] is True
    assert selected_node["parent_output"] is None
    assert not [edge for edge in graph["edges"] if edge["relation"] == "backbone"]


def test_finished_output_covers_duplicate_candidate(tmp_path: Path) -> None:
    graph = rebuild_knowledge_graph(
        tmp_path,
        _candidate_nodes(
            _candidate("candidate:variables", ["变量"], chunks=["source#001"], sources=["source"]),
            _candidate("candidate:loops", ["循环"], prerequisites=["变量"], chunks=["source#002"], sources=["source"]),
        ),
        {
            "version": 1,
            "records": [
                _ledger_record(
                    "outputs/01/01.variables.md",
                    ["变量"],
                    chunks=["source#001"],
                    sources=["source"],
                    graph_node_id="candidate:variables",
                )
            ],
        },
    )

    covered = next(node for node in graph["nodes"] if node["node_id"] == "candidate:variables")
    selected = next(node for node in graph["nodes"] if node["planner_selected"])

    assert covered["status"] == "covered"
    assert covered["covered_by_output"] == "finished:outputs/01/01.variables.md"
    assert selected["node_id"] == "candidate:loops"
    assert selected["parent_output"] == "finished:outputs/01/01.variables.md"


def test_planner_selects_attachable_branch_with_best_parent_output(tmp_path: Path) -> None:
    graph = rebuild_knowledge_graph(
        tmp_path,
        _candidate_nodes(
            _candidate("candidate:loops", ["循环"], prerequisites=["变量"], chunks=["source#002"], sources=["source"]),
            _candidate("candidate:database", ["数据库事务"], chunks=["db#001"], sources=["database"]),
        ),
        {
            "version": 1,
            "records": [
                _ledger_record(
                    "outputs/01/01.variables.md",
                    ["变量"],
                    chunks=["source#001"],
                    sources=["source"],
                )
            ],
        },
    )

    selected = next(node for node in graph["nodes"] if node["planner_selected"])

    assert selected["node_id"] == "candidate:loops"
    assert selected["is_new_root"] is False
    assert selected["parent_output"] == "finished:outputs/01/01.variables.md"
    assert selected["branch_score"] > 0.18


def test_planner_reselects_root_when_remaining_candidates_are_distant(tmp_path: Path) -> None:
    graph = rebuild_knowledge_graph(
        tmp_path,
        _candidate_nodes(
            _candidate("candidate:database", ["数据库事务"], chunks=["db#001"], sources=["database"]),
        ),
        {
            "version": 1,
            "records": [
                _ledger_record(
                    "outputs/01/01.variables.md",
                    ["变量"],
                    chunks=["source#001"],
                    sources=["source"],
                )
            ],
        },
    )

    selected = next(node for node in graph["nodes"] if node["planner_selected"])

    assert selected["node_id"] == "candidate:database"
    assert selected["is_new_root"] is True
    assert selected["parent_output"] is None
    assert graph["planner"]["selection_mode"] == "new_root"


def test_selected_branch_connects_all_strong_supporting_parent_outputs(tmp_path: Path) -> None:
    graph = rebuild_knowledge_graph(
        tmp_path,
        _candidate_nodes(
            _candidate(
                "candidate:list-comprehension",
                ["列表推导式"],
                prerequisites=["变量", "条件判断"],
                chunks=["source#003"],
                sources=["source"],
            ),
            _candidate("candidate:database", ["数据库事务"], chunks=["db#001"], sources=["database"]),
        ),
        {
            "version": 1,
            "records": [
                _ledger_record(
                    "outputs/01/01.variables.md",
                    ["变量"],
                    chunks=["source#001"],
                    sources=["source"],
                ),
                _ledger_record(
                    "outputs/01/02.conditionals.md",
                    ["条件判断"],
                    chunks=["source#002"],
                    sources=["source"],
                ),
            ],
        },
    )

    selected = next(node for node in graph["nodes"] if node["planner_selected"])
    required = set(selected["required_nodes"])
    support_ids = {item["node_id"] for item in selected["supporting_parents"]}

    assert selected["node_id"] == "candidate:list-comprehension"
    assert selected["parent_output"] in required
    assert "finished:outputs/01/01.variables.md" in required
    assert "finished:outputs/01/02.conditionals.md" in required
    assert support_ids == required
    assert len([edge for edge in graph["edges"] if edge["relation"] == "supporting_parent"]) == 1


def test_candidate_distance_metrics_are_explicit(tmp_path: Path) -> None:
    graph = rebuild_knowledge_graph(
        tmp_path,
        _candidate_nodes(
            _candidate("candidate:loops", ["循环"], prerequisites=["变量"], chunks=["source#002"], sources=["source"]),
            _candidate("candidate:database", ["数据库事务"], chunks=["db#001"], sources=["database"]),
        ),
        {
            "version": 1,
            "records": [
                _ledger_record(
                    "outputs/01/01.variables.md",
                    ["变量"],
                    chunks=["source#001"],
                    sources=["source"],
                )
            ],
        },
    )

    loops = next(node for node in graph["nodes"] if node["node_id"] == "candidate:loops")

    assert loops["nearest_finished_output"] == "finished:outputs/01/01.variables.md"
    assert loops["tree_distance"] == round(1 - loops["support_score"], 4)
    assert loops["distance_components"] == {
        "concept_distance": 1.0,
        "chunk_distance": 1.0,
        "source_distance": 0.0,
        "affinity_distance": loops["distance_components"]["affinity_distance"],
        "prerequisite_gap": 0.0,
    }
    assert 0 <= loops["distance_components"]["affinity_distance"] <= 1


def test_planner_trace_records_candidate_ranking_and_reasons(tmp_path: Path) -> None:
    graph = rebuild_knowledge_graph(
        tmp_path,
        _candidate_nodes(
            _candidate("candidate:loops", ["循环"], prerequisites=["变量"], chunks=["source#002"], sources=["source"]),
            _candidate("candidate:database", ["数据库事务"], chunks=["db#001"], sources=["database"]),
        ),
        {
            "version": 1,
            "records": [
                _ledger_record(
                    "outputs/01/01.variables.md",
                    ["变量"],
                    chunks=["source#001"],
                    sources=["source"],
                )
            ],
        },
    )

    trace = graph["planner"]["trace"]
    candidate_entries = trace["candidate_ranking"]
    selected_entry = next(item for item in candidate_entries if item["selected"])
    rejected_entry = next(item for item in candidate_entries if not item["selected"])

    assert trace["selection_mode"] == "branch"
    assert trace["selected_node"] == "candidate:loops"
    assert selected_entry["node_id"] == "candidate:loops"
    assert selected_entry["reason"] == "selected"
    assert selected_entry["tree_distance"] < rejected_entry["tree_distance"]
    assert rejected_entry["reason"] in {"lower_support_score", "new_root_distance"}
