"""Built-in agent prompts."""

from __future__ import annotations

from tree.agents.prompts.archivist import (
    ARCHIVIST_CLEAN_PROMPT,
    ARCHIVIST_MTU_PROMPT,
    ARCHIVIST_PROMPT,
)
from tree.agents.prompts.dagger import DAGGER_PREREQUISITES_PROMPT, DAGGER_PROMPT
from tree.agents.prompts.examiner import EXAMINER_PROMPT
from tree.agents.prompts.student import STUDENT_PROMPT
from tree.agents.prompts.writer import WRITER_PROMPT

PROMPTS = {
    "examiner": EXAMINER_PROMPT,
    "student": STUDENT_PROMPT,
    "writer": WRITER_PROMPT,
    "archivist": ARCHIVIST_PROMPT,
    "archivist_clean": ARCHIVIST_CLEAN_PROMPT,
    "archivist_mtu": ARCHIVIST_MTU_PROMPT,
    "dagger": DAGGER_PROMPT,
    "dagger_prerequisites": DAGGER_PREREQUISITES_PROMPT,
}


def get_prompt(name: str) -> str:
    try:
        return PROMPTS[name]
    except KeyError as exc:
        raise KeyError(f"Unknown agent prompt: {name}") from exc


__all__ = ["PROMPTS", "get_prompt", "DAGGER_PROMPT", "DAGGER_PREREQUISITES_PROMPT"]
