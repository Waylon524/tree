"""StudentAgent: zero-baseline blind test answering.

TODO (step 7): answer(current_draft, prior_scope, learned_rag_hits, blind_exam) -> str
See docs/REBUILD-DESIGN.md §6.
"""

from __future__ import annotations

from tree.agents.base import Agent


class StudentAgent(Agent):
    role = "student"

    async def answer(self, *args, **kwargs):
        raise NotImplementedError("Student.answer — implement in step 7")
