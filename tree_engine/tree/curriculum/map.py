"""Compatibility wrapper for the renamed candidate node generator."""

from __future__ import annotations

from tree.curriculum.candidate_nodes import (
    CandidateNodeBuilder as CurriculumMapBuilder,
    build_candidate_nodes_context as build_curriculum_map_context,
    load_candidate_nodes as load_curriculum_map,
    rebuild_candidate_nodes as rebuild_curriculum_map,
    rebuild_candidate_nodes_with_ai as rebuild_curriculum_map_with_ai,
    save_candidate_nodes as save_curriculum_map,
)

__all__ = [
    "CurriculumMapBuilder",
    "build_curriculum_map_context",
    "load_curriculum_map",
    "rebuild_curriculum_map",
    "rebuild_curriculum_map_with_ai",
    "save_curriculum_map",
]
