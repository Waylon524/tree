"""Tests for the simplified Markdown chunker (step 3)."""

from __future__ import annotations

from types import SimpleNamespace

from tree.rag.chunker import chunk_markdown, chunk_mtu


def test_chunk_markdown_splits_by_heading():
    text = (
        "## 质点与参考系\n\n**质点**是忽略形状大小的有质量的点。\n\n"
        "## 位移\n\n位移是位置矢量的改变量。\n"
    )
    chunks = chunk_markdown("01", text, source_collection="力学")
    assert len(chunks) == 2
    assert chunks[0]["section_id"].startswith("质点")
    assert "质点" in chunks[0]["concepts"]
    assert all(c["source_collection"] == "力学" for c in chunks)


def test_chunk_mtu_carries_metadata():
    mtu = SimpleNamespace(
        mtu_id="mtu:abc",
        collection="课件",
        source_file="ch1.md",
        title="化学平衡状态",
        keywords=["可逆反应", "动态平衡"],
        unit_kind="concept",
        line_range=(1, 28),
    )
    text = "## 化学平衡状态\n\n**化学平衡**是正逆反应速率相等的动态状态。\n"
    chunks = chunk_mtu(mtu, text)
    assert chunks, "expected at least one chunk"
    for c in chunks:
        assert c["mtu_id"] == "mtu:abc"
        assert c["title"] == "化学平衡状态"
        assert c["keywords"] == ["可逆反应", "动态平衡"]
        assert c["line_range"] == [1, 28]


def test_chunk_mtu_handles_plain_text_without_heading():
    mtu = SimpleNamespace(
        mtu_id="mtu:x",
        collection="作业",
        source_file="hw.md",
        title="练习",
        keywords=[],
        unit_kind="exercise",
        line_range=(5, 9),
    )
    chunks = chunk_mtu(mtu, "求解平衡常数 K 的表达式。")
    assert len(chunks) == 1
    assert chunks[0]["mtu_id"] == "mtu:x"
