"""TREE CLI color theme helpers."""

from __future__ import annotations

from tree.cli import theme


def test_status_colors_success_warning_and_error() -> None:
    assert theme.TREE_GREEN in theme.status("complete")
    assert theme.TREE_GREEN in theme.status("running")
    assert "yellow" in theme.status("pending")
    assert "red" in theme.status("failed")
    assert "red" in theme.status("blocked")


def test_label_and_path_escape_rich_markup() -> None:
    rendered = theme.path("materials/[course]/a.md")

    assert theme.TREE_BROWN in rendered
    assert r"\[course]" in rendered


def test_progress_bar_uses_green_and_brown_segments() -> None:
    rendered = theme.progress_bar(1, 2, width=4)

    assert theme.TREE_GREEN in rendered
    assert theme.TREE_BROWN_DIM in rendered
    assert "██" in rendered
    assert "░░" in rendered
