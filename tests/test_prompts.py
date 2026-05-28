from tree.agents.prompts import WRITER_PROMPT


def test_writer_prompt_requires_markdown_math_delimiters():
    assert "Do not use `\\(...\\)` or `\\[...\\]`" in WRITER_PROMPT
    assert "Inline math must use single-dollar delimiters" in WRITER_PROMPT
    assert "Display math must use double-dollar delimiters" in WRITER_PROMPT


def test_writer_prompt_requires_renderable_display_math_blocks():
    assert "display math block must be on its own lines" in WRITER_PROMPT
    assert "must not be indented" in WRITER_PROMPT
