"""WriterAgent: CREATE/OPTIMIZE branch-span drafts from Bottleneck Reports.

TODO (step 7): draft(branch_span, writer_instructions, bottleneck, current_draft,
                     prior_scope, source_rag) -> WriterResult
See docs/REBUILD-DESIGN.md §6.
"""

from __future__ import annotations

from tree.agents.base import Agent


class WriterAgent(Agent):
    role = "writer"

    async def draft(self, *args, **kwargs):
        raise NotImplementedError("Writer.draft — implement in step 7")
