from tree.curriculum.chapter_naming import (
    build_chapter_naming_context,
    fallback_chapter_title,
    next_tree_id,
    parse_chapter_naming_response,
)
from tree.engine import _chapter_name_from_required_nodes
from tree.state.manager import StateManager
from tree.state.models import PipelineState


def test_next_tree_id_uses_stable_internal_sequence() -> None:
    state = PipelineState()
    mgr = StateManager(__import__("pathlib").Path("/tmp/unused.json"))
    state = mgr.add_chapter(state, "tree-001")
    state = mgr.add_chapter(state, "tree-002")

    assert next_tree_id(state) == "tree-003"


def test_build_chapter_naming_context_uses_finished_tree_concepts() -> None:
    ledger = {
        "records": [
            {
                "chapter": "tree-001",
                "knowledge_point": "变量",
                "covered_concepts": ["变量", "赋值"],
                "source_collections": ["source-a"],
                "summary": "变量用于绑定对象。",
            },
            {
                "chapter": "tree-001",
                "knowledge_point": "条件判断",
                "covered_concepts": ["条件判断", "布尔表达式"],
                "source_collections": ["source-a"],
                "summary": "条件判断根据布尔表达式选择分支。",
            },
            {
                "chapter": "tree-002",
                "knowledge_point": "数据库事务",
                "covered_concepts": ["事务"],
            },
        ]
    }

    context = build_chapter_naming_context(ledger, "tree-001")

    assert context["tree_id"] == "tree-001"
    assert context["file_count"] == 2
    assert context["knowledge_points"] == ["变量", "条件判断"]
    assert "数据库事务" not in context["knowledge_points"]
    assert "变量" in context["top_concepts"]
    assert context["source_collections"] == ["source-a"]


def test_fallback_chapter_title_uses_top_concepts() -> None:
    result = fallback_chapter_title(
        {
            "tree_id": "tree-001",
            "top_concepts": ["变量", "赋值", "条件判断"],
            "knowledge_points": [],
        }
    )

    assert result["chapter_title"] == "变量、赋值、条件判断"


def test_parse_chapter_naming_response_accepts_json() -> None:
    result = parse_chapter_naming_response(
        '{"chapter_title": "程序设计基础", "short_slug": "程序设计", "reason": "覆盖变量与条件"}'
    )

    assert result == {
        "chapter_title": "程序设计基础",
        "short_slug": "程序设计",
        "reason": "覆盖变量与条件",
    }


def test_state_manager_can_reopen_and_name_tree() -> None:
    mgr = StateManager(__import__("pathlib").Path("/tmp/unused.json"))
    state = PipelineState()
    state = mgr.add_chapter(state, "tree-001")
    state = mgr.complete_chapter(state, "tree-001")
    state = mgr.reopen_chapter(
        state,
        "tree-001",
        graph_node_id="candidate:loops",
        required_nodes=["finished:outputs/tree-001/01.variables.md"],
    )

    chapter = state.chapters[0]
    assert chapter.status == "in_progress"
    assert chapter.graph_node_id == "candidate:loops"
    assert chapter.required_nodes == ["finished:outputs/tree-001/01.variables.md"]

    state = mgr.complete_chapter(state, "tree-001")
    state = mgr.set_chapter_title(state, "tree-001", "程序设计基础", "covered variables")
    assert state.chapters[0].chapter_title == "程序设计基础"
    assert state.chapters[0].chapter_naming_reason == "covered variables"


def test_chapter_name_from_required_nodes_extracts_output_tree_id() -> None:
    assert (
        _chapter_name_from_required_nodes(["finished:outputs/tree-001/01.variables.md"])
        == "tree-001"
    )
