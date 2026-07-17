"""Tests for cross-platform filesystem helpers."""

from __future__ import annotations

from pathlib import Path

from tree.io import file_ops


def test_write_text_bypasses_windows_newline_translation(tmp_path, monkeypatch):
    """Persisted bytes must match the bytes hashed before NodeRun publication."""

    def windows_text_write(
        path: Path,
        text: str,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        del errors, newline
        assert encoding == "utf-8"
        return path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))

    monkeypatch.setattr(Path, "write_text", windows_text_write)
    output = tmp_path / "nested" / "lesson.md"

    file_ops.write_text(output, "第一行\n第二行\n")

    assert output.read_bytes() == "第一行\n第二行\n".encode("utf-8")
