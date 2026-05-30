import inspect

from tree.agents.examiner import ExaminerAgent
from tree.agents.prompts import ARCHIVIST_PROMPT, EXAMINER_PROMPT, STUDENT_PROMPT, WRITER_PROMPT
from tree.agents.student import StudentAgent
from tree.agents.writer import WriterAgent


def test_examiner_prompt_makes_planner_authoritative() -> None:
    assert "Covered_Node_IDs is binding" in EXAMINER_PROMPT
    assert "choose a root, choose a branch" in EXAMINER_PROMPT
    assert "decide whether to open another chapter" not in EXAMINER_PROMPT
    assert "Before selecting a new knowledge point" not in EXAMINER_PROMPT
    assert "Chapter Continuation Scan" not in EXAMINER_PROMPT


def test_agent_runtime_prompts_use_branch_span_terms_not_chapter_phase_c() -> None:
    writer_source = inspect.getsource(WriterAgent.create_or_optimize)
    student_source = inspect.getsource(StudentAgent.blind_test)
    examiner_source = inspect.getsource(ExaminerAgent)

    assert "Knowledge point:" not in writer_source
    assert "Declared branch span title" in writer_source
    assert "Current knowledge point draft" not in student_source
    assert "current branch-span draft" in student_source
    assert "Chapter Continuation Scan" not in examiner_source
    assert "scan_next_chapter" not in examiner_source


def test_writer_prompt_requires_integrated_selected_node_draft() -> None:
    assert "integrate all source chunks that belong to its KnowledgeNodes" in WRITER_PROMPT
    assert "Do not split the span by chunk, exercise number, example variant" in WRITER_PROMPT
    assert "Pre-Write Protocol" in WRITER_PROMPT
    assert "Background and application context" in WRITER_PROMPT
    assert "Core concepts and symbol conventions" in WRITER_PROMPT
    assert "Principles and methods" in WRITER_PROMPT
    assert "repair the smallest coherent logic block" in WRITER_PROMPT
    assert "not locked to one rigid solution template" in WRITER_PROMPT
    assert "structure that fits the discipline" in WRITER_PROMPT


def test_writer_prompt_does_not_allow_length_based_refusal() -> None:
    assert "EXAM_TOO_BROAD" not in WRITER_PROMPT
    assert "Target length: 300-500 lines" not in WRITER_PROMPT
    assert "300-500 lines" not in WRITER_PROMPT
    assert "line-count limit" not in WRITER_PROMPT
    assert "Line limit:" not in EXAMINER_PROMPT


def test_prompts_do_not_reference_split_needed_feedback_mechanism() -> None:
    assert "split_needed" not in EXAMINER_PROMPT
    assert "split_needed" not in WRITER_PROMPT


def test_student_prompt_classifies_current_and_prerequisite_gaps() -> None:
    assert "[!! Current Draft Gap]" in STUDENT_PROMPT
    assert "[!! Prerequisite Gap]" in STUDENT_PROMPT
    assert "planner prerequisite relation may be incomplete" in STUDENT_PROMPT


def test_examiner_prompt_focuses_exam_on_selected_node_delta() -> None:
    assert "prerequisite bridge" in EXAMINER_PROMPT
    assert "declared branch-span delta" in EXAMINER_PROMPT
    assert "unrelated future knowledge" in EXAMINER_PROMPT


def test_archivist_prompt_avoids_promoting_exercise_numbers_to_section_headings() -> None:
    assert "Do not promote individual exercise numbers" in ARCHIVIST_PROMPT
    assert "Keep exercise groups together" in ARCHIVIST_PROMPT
