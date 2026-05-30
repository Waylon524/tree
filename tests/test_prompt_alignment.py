from tree.agents.prompts import ARCHIVIST_PROMPT, EXAMINER_PROMPT, WRITER_PROMPT


def test_examiner_prompt_makes_planner_authoritative() -> None:
    assert "describe the planner-selected node" in EXAMINER_PROMPT
    assert "copy the selected graph node's required_nodes exactly" in EXAMINER_PROMPT
    assert "decide whether to open another chapter" not in EXAMINER_PROMPT
    assert "Before selecting a new knowledge point" not in EXAMINER_PROMPT


def test_writer_prompt_requires_integrated_selected_node_draft() -> None:
    assert "integrate all source chunks that belong to the selected node" in WRITER_PROMPT
    assert "Do not split the selected node by chunk, exercise number, example variant" in WRITER_PROMPT


def test_archivist_prompt_avoids_promoting_exercise_numbers_to_section_headings() -> None:
    assert "Do not promote individual exercise numbers" in ARCHIVIST_PROMPT
    assert "Keep exercise groups together" in ARCHIVIST_PROMPT
