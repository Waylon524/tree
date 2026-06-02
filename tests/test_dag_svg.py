"""Tests for static knowledge DAG SVG rendering."""

from __future__ import annotations

from tree.planner.svg import render_dag_svg


def test_render_dag_svg_uses_indexed_titles_and_prerequisite_edges_only():
    dag = {
        "nodes": [
            {
                "node_id": "n1",
                "title": "根知识点",
                "source_order_index": 0,
                "defines": ["根定义"],
                "collections": ["课件"],
            },
            {
                "node_id": "n2",
                "title": "后续知识点",
                "source_order_index": 1,
                "defines": ["后续定义"],
                "collections": ["课件"],
            },
        ],
        "edges": [
            {
                "from_node_id": "n1",
                "to_node_id": "n2",
                "relation": "prerequisite",
                "required_defines": ["根定义"],
            },
            {"from_node_id": "n2", "to_node_id": "n1", "relation": "order"},
        ],
        "roots": ["n1"],
    }

    svg = render_dag_svg(dag)

    assert svg.startswith("<svg")
    assert 'marker id="arrow"' in svg
    assert "001. 根知识点" in svg
    assert "002. 后续知识点" in svg
    assert "required_defines: 根定义" in svg
    assert "[order]" not in svg


def test_render_dag_svg_is_stable_and_handles_empty_dag():
    empty = render_dag_svg({"nodes": [], "edges": [], "roots": []})
    assert "<svg" in empty
    assert "No knowledge nodes" in empty

    dag = {
        "nodes": [{"node_id": "n1", "title": "A", "source_order_index": 0}],
        "edges": [],
        "roots": ["n1"],
    }
    assert render_dag_svg(dag) == render_dag_svg(dag)
