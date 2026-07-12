"""Tests for incremental material scanning (step 6)."""

from __future__ import annotations

from tree.planner.manifest import scan_materials


def _write(root, rel, text):
    path = root / "materials" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_scan_classifies_new_unchanged_changed_inactive(tmp_path):
    _write(tmp_path, "课件/ch1.md", "# 化学平衡\n内容")
    first = scan_materials(tmp_path)
    assert len(first["materials"]) == 1
    m = first["materials"][0]
    assert m["status"] == "new"
    assert m["collection"] == "课件"
    assert m["source_file"] == "ch1.md"
    assert first["inactive_materials"] == []

    second = scan_materials(tmp_path, previous=first)
    assert second["materials"][0]["status"] == "unchanged"

    _write(tmp_path, "课件/ch1.md", "# 化学平衡\n内容更长一些以改变大小")
    third = scan_materials(tmp_path, previous=second)
    assert third["materials"][0]["status"] == "changed"


def test_scan_reports_inactive_when_file_removed(tmp_path):
    _write(tmp_path, "作业/hw1.md", "题目")
    first = scan_materials(tmp_path)
    (tmp_path / "materials" / "作业" / "hw1.md").unlink()
    second = scan_materials(tmp_path, previous=first)
    assert second["materials"] == []
    assert second["inactive_materials"] == ["作业/hw1.md"]


def test_scan_default_collection_for_top_level_file(tmp_path):
    _write(tmp_path, "loose.md", "x")
    result = scan_materials(tmp_path)
    assert result["materials"][0]["collection"] == "default"


def test_scan_ignores_unsupported_files(tmp_path):
    _write(tmp_path, "课件/notes.xyz", "x")
    assert scan_materials(tmp_path)["materials"] == []


def test_scan_detects_same_size_content_change_even_when_mtime_is_restored(tmp_path):
    path = _write(tmp_path, "课件/ch1.md", "AAAA")
    first = scan_materials(tmp_path)
    original_mtime = path.stat().st_mtime

    path.write_text("BBBB", encoding="utf-8")
    import os

    os.utime(path, (original_mtime, original_mtime))
    second = scan_materials(tmp_path, previous=first)

    assert second["materials"][0]["status"] == "changed"
    assert second["materials"][0]["fingerprint"].startswith("sha256:")


def test_scan_uses_full_relative_path_as_source_identity(tmp_path):
    _write(tmp_path, "课件/week1/lecture.md", "one")
    _write(tmp_path, "课件/week2/lecture.md", "two")

    materials = scan_materials(tmp_path)["materials"]

    assert {item["source_id"] for item in materials} == {
        "课件/week1/lecture.md",
        "课件/week2/lecture.md",
    }
