"""ExaminerAgent: Phase A compose exam, Phase B dual audit.

Migrated behaviour from the previous engine; node ids are Dagger canonical
KnowledgeNode ids. See docs/LEGACY-DESIGN.md §7 and docs/REBUILD-DESIGN.md §6.

TODO (step 7):
  - compose(active_branch_ctx, prior_scope, retrieved, next_seq) -> ExamSections
  - audit(iter_state, student_answer, retrieved) -> AuditResult
  - format-retry on unparseable sections (max_format_retries)
"""

from __future__ import annotations

from tree.agents.base import Agent


class ExaminerAgent(Agent):
    role = "examiner"

    async def compose(self, *args, **kwargs):
        raise NotImplementedError("Examiner.compose — implement in step 7")

    async def audit(self, *args, **kwargs):
        raise NotImplementedError("Examiner.audit — implement in step 7")
