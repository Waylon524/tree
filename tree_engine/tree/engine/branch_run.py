"""Single BranchRun executor: Step 0 -> 1 -> 2 -> 3 -> 4.

Processes one branch-span file per ``run_one`` call (Examiner composes -> Student
blind test -> Examiner audit -> Writer drafts, looping until PASS), then records
the output and, when the branch is fully covered, completes it.

Prior scope = frozen CoverageSnapshot ancestors + earlier files in the same
branch. The finished ledger (knowledge-ledger.json) is read/written inline here
(no separate module — see project decision). See REBUILD-DESIGN §4/§6.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Protocol

from tree.agents.examiner import ExaminerAgent
from tree.agents.student import StudentAgent
from tree.agents.writer import WriterAgent
from tree.io import file_ops, paths
from tree.observability.limiter import IterationLimiter
from tree.planner.store import read_json, write_json_atomic
from tree.state.manager import StateManager
from tree.state.models import ExamSections, IterationState, Route


class Retriever(Protocol):
    """Injected RAG access (real impl wraps RAGClient/RAGIndexer in step 8)."""

    def source_hits(self, query: str, *, collections: list[str], top_k: int) -> list[dict]: ...
    def finished_hits(self, query: str, *, allowed_paths: set[str], top_k: int) -> list[dict]: ...
    def index_finished(self, execution_path: str, path: Path) -> int: ...


class BranchRunner:
    def __init__(
        self,
        *,
        root: Path,
        settings: Any,
        examiner: ExaminerAgent,
        student: StudentAgent,
        writer: WriterAgent,
        retriever: Retriever,
        state_mgr: StateManager,
    ):
        self.root = root
        self.settings = settings
        self.examiner = examiner
        self.student = student
        self.writer = writer
        self.retriever = retriever
        self.state_mgr = state_mgr
        self.limiter = IterationLimiter(settings.max_iterations)

    async def run_one(self, execution_path: str) -> str:
        """Run one span to PASS. Returns "file_passed" or "branch_complete"."""
        state = self.state_mgr.load()
        be = self.state_mgr.find_execution(state, execution_path)
        if be is None:
            raise RuntimeError(f"No branch execution for {execution_path}")
        run = next((r for r in state.branch_runs if r.run_id == be.branch_run_id), None)
        snapshot = run.coverage_snapshot if run else None

        branch = self._load_branch(be.branch_id or "")
        nodes_by_id = {n["node_id"]: n for n in self._load_nodes()}
        ledger = self._load_ledger()
        covered = _ledger_covered_node_ids(ledger)

        uncovered = [nid for nid in branch.get("coverage_node_ids", []) if nid not in covered]
        if not uncovered:
            self._complete_branch(execution_path, be.branch_run_id)
            return "branch_complete"

        next_seq = str(len(be.outputs_completed) + 1).zfill(2)
        branch_context = _branch_context(branch, nodes_by_id, covered, snapshot)
        prior_paths, prior_contents, allowed_paths = self._prior_scope(execution_path, snapshot, ledger)
        collections = list(be.source_collections or [])

        # Step 1: Examiner composes the exam.
        compose_query = f"{execution_path}\n{next_seq}\n下一知识点命题"
        exam = await self.examiner.compose(
            next_seq=next_seq,
            prior_paths=prior_paths,
            prior_contents=prior_contents,
            retrieved=self.retriever.source_hits(compose_query, collections=collections, top_k=5)
            + self.retriever.finished_hits(compose_query, allowed_paths=allowed_paths, top_k=8),
            branch_context=branch_context,
        )
        exam.covered_node_ids = self._clamp_span(exam.covered_node_ids, uncovered)

        iter_state = IterationState(
            execution_path=execution_path,
            file_seq=next_seq,
            knowledge_point=exam.knowledge_point,
            covered_node_ids=list(exam.covered_node_ids),
            exam_sections=exam,
        )
        await self._iteration_loop(iter_state, be, collections, prior_paths, prior_contents,
                                   allowed_paths, branch_context)

        # Re-check branch completion after recording the output.
        ledger = self._load_ledger()
        covered = _ledger_covered_node_ids(ledger)
        if all(nid in covered for nid in branch.get("coverage_node_ids", [])):
            self._complete_branch(execution_path, be.branch_run_id)
            return "branch_complete"
        return "file_passed"

    async def _iteration_loop(
        self,
        iter_state: IterationState,
        be: Any,
        collections: list[str],
        prior_paths: list[str],
        prior_contents: list[str],
        allowed_paths: set[str],
        branch_context: str,
    ) -> None:
        exam = iter_state.exam_sections
        assert exam is not None
        previous_bottleneck: str | None = None
        iteration = 0
        while True:
            iteration += 1
            self.limiter.check(iter_state.execution_path, iter_state.file_seq, iteration)
            iter_state.iteration = iteration
            draft_text = (
                iter_state.draft_path.read_text(encoding="utf-8")
                if iter_state.draft_path and iter_state.draft_path.exists()
                else None
            )

            # Step 2: Student blind test.
            sq = f"{exam.knowledge_point}\n{exam.blind_exam}"
            answer = await self.student.answer(
                blind_exam=exam.blind_exam,
                prior_paths=prior_paths,
                prior_contents=prior_contents,
                draft_text=draft_text,
                learned_hits=self.retriever.finished_hits(sq, allowed_paths=allowed_paths, top_k=6),
            )

            # Step 3: Examiner audit.
            aq = f"{exam.knowledge_point}\n{exam.blind_exam}\n{answer}"
            audit = await self.examiner.audit(
                exam_paper=exam.blind_exam,
                answer_key=exam.answer_key,
                student_answer=answer,
                draft_text=draft_text,
                prior_paths=prior_paths,
                prior_contents=prior_contents,
                previous_bottleneck=previous_bottleneck,
                retrieved=self.retriever.finished_hits(aq, allowed_paths=allowed_paths, top_k=6)
                + self.retriever.source_hits(aq, collections=collections, top_k=5),
                branch_context=branch_context,
            )

            if audit.route == Route.PASS:
                if not iter_state.draft_path or not iter_state.draft_path.exists():
                    raise RuntimeError("Cannot PASS without a persisted draft.")
                self._handle_pass(iter_state, be, exam)
                return

            # Step 4: Writer creates/optimizes the draft.
            wq = f"{exam.knowledge_point}\n{audit.bottleneck_report}"
            result = await self.writer.draft(
                span_title=exam.knowledge_point,
                file_seq=iter_state.file_seq,
                bottleneck_report=audit.bottleneck_report,
                prior_paths=prior_paths,
                prior_contents=prior_contents,
                draft_text=draft_text,
                previous_bottleneck=previous_bottleneck,
                writer_instructions=exam.writer_instructions,
                retrieved=self.retriever.source_hits(wq, collections=collections, top_k=5)
                + self.retriever.finished_hits(wq, allowed_paths=allowed_paths, top_k=8),
                branch_context=branch_context,
            )
            iter_state.draft_path = self._persist_draft(
                iter_state.execution_path, iter_state.file_seq, exam.knowledge_point, result.draft_content
            )
            previous_bottleneck = audit.bottleneck_report

    # --- PASS / completion ---------------------------------------------------

    def _handle_pass(self, iter_state: IterationState, be: Any, exam: ExamSections) -> None:
        filename = iter_state.draft_path.name  # type: ignore[union-attr]
        slug = _exec_slug(iter_state.execution_path)
        dst = paths.outputs_root(self.root) / slug / filename
        file_ops.move(iter_state.draft_path, dst)  # type: ignore[arg-type]

        self.retriever.index_finished(iter_state.execution_path, dst)
        self._append_ledger_record(
            {
                "execution_path": iter_state.execution_path,
                "output_path": file_ops.relative_to(self.root, dst),
                "title": exam.knowledge_point,
                "node_ids": list(iter_state.covered_node_ids),
                "file_seq": iter_state.file_seq,
            }
        )
        state = self.state_mgr.load()
        state = self.state_mgr.add_output_completed(state, iter_state.execution_path, filename)
        if be.branch_run_id:
            state = self.state_mgr.add_branch_run_file_completed(state, be.branch_run_id, filename)
        self.state_mgr.save(state)

    def _complete_branch(self, execution_path: str, run_id: str | None) -> None:
        state = self.state_mgr.load()
        state = self.state_mgr.complete_branch_execution(state, execution_path)
        if run_id:
            state = self.state_mgr.update_branch_run(state, run_id, status="complete")
        self.state_mgr.save(state)

    # --- prior scope / context ----------------------------------------------

    def _prior_scope(
        self, execution_path: str, snapshot: Any, ledger: dict[str, Any]
    ) -> tuple[list[str], list[str], set[str]]:
        visible = set(getattr(snapshot, "snapshot_visible_ancestor_node_ids", []) or [])
        paths_list: list[str] = []
        contents: list[str] = []
        for record in ledger.get("records", []):
            same_branch = record.get("execution_path") == execution_path
            ancestor = set(record.get("node_ids", [])) <= visible and bool(record.get("node_ids"))
            if not (same_branch or ancestor):
                continue
            rel = record.get("output_path", "")
            abs_path = self.root / rel
            if abs_path.exists():
                paths_list.append(rel)
                contents.append(abs_path.read_text(encoding="utf-8"))
        return paths_list, contents, set(paths_list)

    # --- drafts / outputs ----------------------------------------------------

    def _persist_draft(self, execution_path: str, file_seq: str, title: str, content: str) -> Path:
        path = paths.drafts_root(self.root) / _exec_slug(execution_path) / _draft_filename(file_seq, title)
        file_ops.write_text(path, _strip_front_matter(content))
        return path

    # --- artifact loaders ----------------------------------------------------

    def _load_branch(self, branch_id: str) -> dict[str, Any]:
        from tree.planner.pipeline import load_branches

        return next((b for b in load_branches(self.root) if b["branch_id"] == branch_id), {})

    def _load_nodes(self) -> list[dict[str, Any]]:
        from tree.planner.pipeline import load_nodes

        return load_nodes(self.root)

    # --- ledger (inline) -----------------------------------------------------

    def _load_ledger(self) -> dict[str, Any]:
        return _load_ledger(self.root)

    def _append_ledger_record(self, record: dict[str, Any]) -> None:
        ledger = self._load_ledger()
        ledger.setdefault("records", []).append(record)
        write_json_atomic(paths.knowledge_ledger_path(self.root), ledger)

    @staticmethod
    def _clamp_span(requested: list[str], uncovered: list[str]) -> list[str]:
        """Force a contiguous span starting at the first uncovered branch node."""
        keep = [nid for nid in requested if nid in uncovered]
        span_len = max(1, len(keep))
        return uncovered[:span_len]


# --- module-level ledger helpers (shared with the orchestrator) -------------

def _load_ledger(root: Path) -> dict[str, Any]:
    path = paths.knowledge_ledger_path(root)
    if not path.exists():
        return {"records": []}
    loaded = read_json(path)
    return loaded if isinstance(loaded, dict) else {"records": []}


def ledger_covered_node_ids(root: Path) -> set[str]:
    return _ledger_covered_node_ids(_load_ledger(root))


def _ledger_covered_node_ids(ledger: dict[str, Any]) -> set[str]:
    covered: set[str] = set()
    for record in ledger.get("records", []):
        covered.update(record.get("node_ids", []))
    return covered


def ledger_output_ids(root: Path) -> list[str]:
    return [r.get("output_path", "") for r in _load_ledger(root).get("records", [])]


# --- formatting helpers -----------------------------------------------------

def _branch_context(
    branch: dict[str, Any], nodes_by_id: dict[str, dict], covered: set[str], snapshot: Any
) -> str:
    lines = ["ActiveBranch Context — branch span nodes (teach in order, start at the TARGET):"]
    target_marked = False
    for nid in branch.get("node_ids", []):
        node = nodes_by_id.get(nid, {})
        title = node.get("title", nid)
        keywords = ", ".join(node.get("keywords", [])[:6])
        if nid in covered:
            marker = "[covered]"
        elif not target_marked:
            marker = "[TARGET first-uncovered]"
            target_marked = True
        else:
            marker = "[ ]"
        lines.append(f"  {marker} {nid} | {title} | {keywords}")
    ancestors = sorted(getattr(snapshot, "snapshot_visible_ancestor_node_ids", []) or [])
    if ancestors:
        lines.append("Required ancestor nodes (prerequisites to cite, not reteach): " + ", ".join(ancestors))
    lines.append("Do not jump to sibling/future branches. Cover a contiguous span from the TARGET node.")
    return "\n".join(lines)


def _exec_slug(execution_path: str) -> str:
    return re.sub(r"[^\w.-]", "_", execution_path)


def _draft_filename(file_seq: str, title: str) -> str:
    safe = re.sub(r"[^\w一-鿿.-]", "_", title).strip("_") or "untitled"
    return f"{file_seq}.{safe}.md"


def _strip_front_matter(content: str) -> str:
    text = content.strip()
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :].lstrip()
    return text
