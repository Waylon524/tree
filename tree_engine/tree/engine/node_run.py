"""Single NodeRun executor: Examiner -> Student -> Examiner -> Writer loop."""

from __future__ import annotations

import hashlib
from difflib import SequenceMatcher
import re
from pathlib import Path
from typing import Any, Protocol

from tree.agents.examiner import ExaminerAgent
from tree.agents.student import StudentAgent
from tree.agents.writer import WriterAgent
from tree.io import file_ops, paths
from tree.observability.limiter import IterationLimitExceeded, IterationLimiter
from tree.planner.store import read_envelope_data, read_json, write_json_atomic
from tree.state.manager import StateManager
from tree.state.models import (
    AuditExamDefectKind,
    ExamReconciliationAction,
    ExamSections,
    IterationState,
    Route,
)


class Retriever(Protocol):
    """Injected RAG access consumed by NodeRunner."""

    def source_hits(
        self, query: str, *, collections: list[str], node_ids: list[str], top_k: int
    ) -> list[dict[str, Any]]: ...
    def finished_hits(
        self, query: str, *, allowed_paths: set[str], top_k: int
    ) -> list[dict[str, Any]]: ...
    def index_finished(self, node_id: str, path: Path) -> int: ...


class NodeRunStagnationError(RuntimeError):
    """Raised when repeated audit feedback no longer produces progress."""


class NodeRunner:
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

    async def run_one(self, node_id: str) -> str:
        """Run one KnowledgeNode to PASS. Returns ``node_complete``."""
        state = self.state_mgr.load()
        execution = self.state_mgr.find_execution(state, node_id)
        if execution is None:
            raise RuntimeError(f"No node execution for {node_id}")
        run = self.state_mgr.find_run(state, execution.node_run_id)
        snapshot = run.coverage_snapshot if run else None

        dag = self._load_dag()
        nodes_by_id = {n["node_id"]: n for n in dag.get("nodes", [])}
        node = nodes_by_id.get(node_id)
        if node is None:
            raise RuntimeError(f"Node {node_id} not found in knowledge DAG")

        self._recover_output_transaction(node_id, execution.node_run_id)
        ledger = self._load_ledger()
        if node_id in ledger_covered_node_ids(self.root):
            self._complete_node(node_id, execution.node_run_id)
            return "node_complete"

        file_seq = _node_file_seq(dag.get("nodes", []), node_id)
        node_context = _node_context(node_id, dag, nodes_by_id, snapshot)
        prior_paths, allowed_paths = self._prior_scope(snapshot, ledger)
        collections = list(execution.source_collections or node.get("collections") or [])

        if run and run.exam_sections is not None:
            exam = run.exam_sections
            exam.covered_node_ids = [node_id]
        else:
            compose_query = f"{node_id}\n{node.get('title', '')}\n下一知识点命题"
            exam = await self.examiner.compose(
                next_seq=file_seq,
                prior_paths=prior_paths,
                prior_contents=[],
                retrieved=self._source_evidence_hits(node, compose_query, collections, top_k=5)
                + self.retriever.finished_hits(compose_query, allowed_paths=allowed_paths, top_k=8),
                node_context=node_context,
                expected_covered_node_ids=[node_id],
            )
            exam.covered_node_ids = [node_id]
            self._persist_run_state(
                execution.node_run_id,
                exam_sections=exam,
                status="running",
                last_error=None,
            )

        iter_state = IterationState(
            execution_path=node_id,
            file_seq=file_seq,
            knowledge_point=_clean_title(exam.knowledge_point) or str(node.get("title") or node_id),
            covered_node_ids=[node_id],
            exam_sections=exam,
            iteration=run.current_iteration if run else 0,
            previous_bottleneck=run.previous_bottleneck if run else None,
            draft_path=_existing_draft_path(run.draft_path) if run else None,
        )
        await self._iteration_loop(iter_state, execution, collections, prior_paths, allowed_paths, node_context)
        self._complete_node_if_covered(node_id, execution.node_run_id)
        return "node_complete"

    async def _iteration_loop(
        self,
        iter_state: IterationState,
        execution: Any,
        collections: list[str],
        prior_paths: list[str],
        allowed_paths: set[str],
        node_context: str,
    ) -> None:
        exam = iter_state.exam_sections
        assert exam is not None
        previous_bottleneck = iter_state.previous_bottleneck
        iteration = iter_state.iteration
        node_id = iter_state.covered_node_ids[0]

        while True:
            iteration += 1
            if iteration > self.limiter.max_iterations:
                repaired = await self._try_reconcile_exam_at_limit(
                    iter_state,
                    execution,
                    collections,
                    prior_paths,
                    allowed_paths,
                    node_context,
                )
                if repaired:
                    exam = iter_state.exam_sections
                    assert exam is not None
                    previous_bottleneck = iter_state.previous_bottleneck
                    iteration = iter_state.iteration
                    continue
                raise IterationLimitExceeded(self._iteration_limit_message(iter_state, execution))
            iter_state.iteration = iteration
            self._persist_run_state(
                execution.node_run_id,
                current_iteration=iteration,
                status="running",
                last_error=None,
            )
            draft_text = (
                iter_state.draft_path.read_text(encoding="utf-8")
                if iter_state.draft_path and iter_state.draft_path.exists()
                else None
            )

            sq = f"{exam.knowledge_point}\n{exam.blind_exam}"
            answer = await self.student.answer(
                blind_exam=exam.blind_exam,
                prior_paths=prior_paths,
                draft_text=draft_text,
                learned_hits=self.retriever.finished_hits(sq, allowed_paths=allowed_paths, top_k=6),
            )

            aq = f"{exam.knowledge_point}\n{exam.blind_exam}\n{answer}"
            audit = await self.examiner.audit(
                exam_paper=exam.blind_exam,
                answer_key=exam.answer_key,
                student_answer=answer,
                draft_text=draft_text,
                prior_paths=prior_paths,
                prior_contents=[],
                previous_bottleneck=previous_bottleneck,
                retrieved=self.retriever.finished_hits(aq, allowed_paths=allowed_paths, top_k=6)
                + self._source_evidence_hits(
                    self._node_for_id(node_id), aq, collections, top_k=5
                ),
                node_context=node_context,
            )

            if audit.route == Route.PASS:
                if iter_state.draft_path and iter_state.draft_path.exists():
                    self._handle_pass(iter_state, execution, exam)
                    return
                bottleneck_report = (
                    "Examiner returned PASS, but this NodeRun has no persisted draft yet. "
                    "Write a complete draft for the target KnowledgeNode before PASS can be accepted."
                )
            else:
                bottleneck_report = audit.bottleneck_report
                if audit.exam_defect_kind is not None:
                    repaired = await self._try_reconcile_exam_from_audit_defect(
                        iter_state,
                        execution,
                        collections,
                        prior_paths,
                        allowed_paths,
                        node_context,
                        bottleneck_report=bottleneck_report,
                        defect_kind=audit.exam_defect_kind,
                    )
                    if repaired:
                        exam = iter_state.exam_sections
                        assert exam is not None
                        previous_bottleneck = iter_state.previous_bottleneck
                        iteration = iter_state.iteration
                        continue

            repeat_count = self._record_bottleneck(execution, bottleneck_report)
            if repeat_count >= 2 and iter_state.draft_path and iter_state.draft_path.exists():
                repaired = await self._reconcile_exam(
                    iter_state,
                    execution,
                    collections,
                    prior_paths,
                    allowed_paths,
                    node_context,
                    bottleneck_report=bottleneck_report,
                    answer_key_only=False,
                    require_draft=True,
                    require_bottleneck=True,
                )
                if repaired:
                    exam = iter_state.exam_sections
                    assert exam is not None
                    previous_bottleneck = iter_state.previous_bottleneck
                    iteration = iter_state.iteration
                    continue
                error = (
                    f"NodeRun stagnated for {execution.node_id}: substantially identical audit "
                    f"feedback repeated {repeat_count} times and the one-time exam "
                    "reconciliation could not resolve it. Review the saved draft and exam."
                )
                self._persist_run_state(execution.node_run_id, status="failed", last_error=error)
                raise NodeRunStagnationError(error)

            wq = f"{exam.knowledge_point}\n{bottleneck_report}"
            result = await self.writer.draft(
                span_title=exam.knowledge_point,
                file_seq=iter_state.file_seq,
                bottleneck_report=bottleneck_report,
                prior_paths=prior_paths,
                prior_contents=[],
                draft_text=draft_text,
                previous_bottleneck=previous_bottleneck,
                writer_instructions=exam.writer_instruction_spec or exam.writer_instructions,
                covered_node_ids=[node_id],
                retrieved=self._source_evidence_hits(
                    self._node_for_id(node_id), wq, collections, top_k=5
                )
                + self.retriever.finished_hits(wq, allowed_paths=allowed_paths, top_k=8),
                node_context=node_context,
            )
            iter_state.draft_path = self._persist_draft(
                node_id, iter_state.file_seq, exam.knowledge_point, result.draft_content
            )
            previous_bottleneck = bottleneck_report
            iter_state.previous_bottleneck = previous_bottleneck
            self._persist_run_state(
                execution.node_run_id,
                current_iteration=iteration,
                draft_path=iter_state.draft_path,
                previous_bottleneck=previous_bottleneck,
                status="running",
                last_error=None,
            )

    def _record_bottleneck(self, execution: Any, bottleneck_report: str) -> int:
        state = self.state_mgr.load()
        run = self.state_mgr.find_run(state, execution.node_run_id)
        previous = run.previous_bottleneck if run else None
        repeat_count = int(getattr(run, "bottleneck_repeat_count", 0) or 0)
        repeat_count = repeat_count + 1 if _bottlenecks_equivalent(previous, bottleneck_report) else 1
        history = list(getattr(run, "bottleneck_history", []) or [])
        history.append(bottleneck_report)
        self._persist_run_state(
            execution.node_run_id,
            bottleneck_repeat_count=repeat_count,
            bottleneck_history=history[-20:],
        )
        return repeat_count

    async def _try_reconcile_exam_from_audit_defect(
        self,
        iter_state: IterationState,
        execution: Any,
        collections: list[str],
        prior_paths: list[str],
        allowed_paths: set[str],
        node_context: str,
        *,
        bottleneck_report: str,
        defect_kind: AuditExamDefectKind,
    ) -> bool:
        repair_count = self._exam_repair_count(execution)
        if repair_count > 0:
            error = (
                f"Exam repair already used for {execution.node_id}; audit still reported "
                f"{defect_kind.value}."
            )
            self._persist_run_state(execution.node_run_id, status="failed", last_error=error)
            raise RuntimeError(error)

        repaired = await self._reconcile_exam(
            iter_state,
            execution,
            collections,
            prior_paths,
            allowed_paths,
            node_context,
            bottleneck_report=bottleneck_report,
            answer_key_only=defect_kind is AuditExamDefectKind.ANSWER_KEY_DEFECT,
        )
        if repaired:
            return True

        error = (
            f"Exam repair failed for {execution.node_id} after audit reported "
            f"{defect_kind.value}."
        )
        self._persist_run_state(execution.node_run_id, status="failed", last_error=error)
        raise RuntimeError(error)

    async def _try_reconcile_exam_at_limit(
        self,
        iter_state: IterationState,
        execution: Any,
        collections: list[str],
        prior_paths: list[str],
        allowed_paths: set[str],
        node_context: str,
    ) -> bool:
        return await self._reconcile_exam(
            iter_state,
            execution,
            collections,
            prior_paths,
            allowed_paths,
            node_context,
            bottleneck_report=iter_state.previous_bottleneck or "",
            answer_key_only=False,
            require_draft=True,
            require_bottleneck=True,
        )

    async def _reconcile_exam(
        self,
        iter_state: IterationState,
        execution: Any,
        collections: list[str],
        prior_paths: list[str],
        allowed_paths: set[str],
        node_context: str,
        *,
        bottleneck_report: str,
        answer_key_only: bool,
        require_draft: bool = False,
        require_bottleneck: bool = False,
    ) -> bool:
        exam = iter_state.exam_sections
        node_id = iter_state.covered_node_ids[0] if iter_state.covered_node_ids else execution.node_id
        if (
            exam is None
            or not hasattr(self.examiner, "reconcile_exam")
        ):
            return False
        draft_path = iter_state.draft_path
        has_draft = draft_path is not None and draft_path.exists()
        if require_draft and not has_draft:
            return False
        if require_bottleneck and not bottleneck_report:
            return False

        repair_count = self._exam_repair_count(execution)
        if repair_count > 0:
            return False

        draft_text = draft_path.read_text(encoding="utf-8") if draft_path is not None else "尚未创建"
        query = f"{exam.knowledge_point}\n{bottleneck_report}\nexam answer key reconciliation"
        result = await self.examiner.reconcile_exam(
            exam_paper=exam.blind_exam,
            answer_key=exam.answer_key,
            draft_text=draft_text,
            bottleneck_report=bottleneck_report,
            prior_paths=prior_paths,
            prior_contents=[],
            retrieved=self.retriever.finished_hits(query, allowed_paths=allowed_paths, top_k=6)
            + self.retriever.source_hits(query, collections=collections, node_ids=[node_id], top_k=5),
            node_context=node_context,
            expected_covered_node_ids=[node_id],
        )
        repair_count += 1
        revised = result.exam_sections
        if result.action is not ExamReconciliationAction.REVISE_EXAM or revised is None:
            self._persist_run_state(
                execution.node_run_id,
                exam_repair_count=repair_count,
                status="running",
                last_error=f"Exam reconciliation kept failure: {result.reason}",
            )
            return False
        if revised.covered_node_ids != [node_id]:
            self._persist_run_state(
                execution.node_run_id,
                exam_repair_count=repair_count,
                status="running",
                last_error=(
                    "Exam reconciliation returned invalid Covered_Node_IDs: "
                    + ", ".join(revised.covered_node_ids)
                ),
            )
            return False

        next_exam = exam.model_copy(update={"answer_key": revised.answer_key}) if answer_key_only else revised
        iter_state.exam_sections = next_exam
        iter_state.knowledge_point = _clean_title(next_exam.knowledge_point) or iter_state.knowledge_point
        iter_state.covered_node_ids = [node_id]
        iter_state.iteration = 0
        iter_state.previous_bottleneck = None
        self._persist_run_state(
            execution.node_run_id,
            exam_sections=next_exam,
            current_iteration=0,
            previous_bottleneck=None,
            bottleneck_repeat_count=0,
            exam_repair_count=repair_count,
            status="running",
            last_error=None,
        )
        return True

    def _exam_repair_count(self, execution: Any) -> int:
        state = self.state_mgr.load()
        run = self.state_mgr.find_run(state, execution.node_run_id)
        return int(getattr(run, "exam_repair_count", 0) or 0)

    def _iteration_limit_message(self, iter_state: IterationState, execution: Any) -> str:
        state = self.state_mgr.load()
        run = self.state_mgr.find_run(state, execution.node_run_id)
        repair_count = int(getattr(run, "exam_repair_count", 0) or 0)
        title = _clean_title(iter_state.knowledge_point) or execution.node_id
        return (
            f"{iter_state.execution_path}/{iter_state.file_seq} exceeded "
            f"{self.limiter.max_iterations} iterations for {title} "
            f"(node_id={execution.node_id}, exam_repair_count={repair_count})"
        )

    def _handle_pass(self, iter_state: IterationState, execution: Any, exam: ExamSections) -> None:
        node_id = iter_state.covered_node_ids[0]
        draft_path = iter_state.draft_path
        if draft_path is None or not draft_path.exists():
            raise RuntimeError(f"Cannot publish {node_id}: persisted draft is missing")
        transaction_path = _output_transaction_path(self.root, node_id)
        existing_transaction = _read_optional_json(transaction_path)
        filename = str(existing_transaction.get("filename") or "")
        if not filename:
            filename = _output_filename(
                iter_state.file_seq,
                exam.knowledge_point,
                node_id,
                paths.outputs_root(self.root),
            )
        dst = paths.outputs_root(self.root) / filename
        formatted = self._format_node_draft(
            node_id,
            iter_state.file_seq,
            exam.knowledge_point,
            draft_path.read_text(encoding="utf-8"),
        )
        output_sha256 = _text_sha256(formatted)
        record = {
            "node_id": node_id,
            "node_ids": [node_id],
            "output_path": file_ops.relative_to(self.root, dst),
            "title": _clean_title(exam.knowledge_point),
            "file_seq": iter_state.file_seq,
            "generation_id": _current_generation_id(self.root),
            "output_sha256": output_sha256,
        }
        write_json_atomic(
            transaction_path,
            {
                "schema_version": 1,
                "status": "prepared",
                "node_id": node_id,
                "filename": filename,
                "output_sha256": output_sha256,
                "ledger_record": record,
            },
        )
        file_ops.write_text(draft_path, formatted)
        file_ops.move(draft_path, dst)
        _update_output_transaction(transaction_path, status="file_published")
        self.retriever.index_finished(node_id, dst)
        _update_output_transaction(transaction_path, status="indexed")
        self._append_ledger_record(record)
        _update_output_transaction(transaction_path, status="ledger_committed")
        state = self.state_mgr.load()
        state = self.state_mgr.add_output_completed(state, node_id, filename)
        if execution.node_run_id:
            state = self.state_mgr.add_node_run_file_completed(state, execution.node_run_id, filename)
        self.state_mgr.save(state)
        transaction_path.unlink(missing_ok=True)

    def _recover_output_transaction(self, node_id: str, run_id: str | None) -> None:
        transaction_path = _output_transaction_path(self.root, node_id)
        transaction = _read_optional_json(transaction_path)
        if not transaction:
            return
        record = transaction.get("ledger_record")
        filename = str(transaction.get("filename") or "")
        if not isinstance(record, dict) or not filename:
            transaction_path.unlink(missing_ok=True)
            return
        dst = paths.outputs_root(self.root) / filename
        if not dst.exists():
            # The transaction was prepared before the atomic draft move. The
            # normal NodeRun can safely regenerate from its persisted state.
            transaction_path.unlink(missing_ok=True)
            return
        expected_hash = str(transaction.get("output_sha256") or "")
        if expected_hash and _file_sha256(dst) != expected_hash:
            raise RuntimeError(f"Pending output transaction has modified content: {dst}")
        self.retriever.index_finished(node_id, dst)
        self._append_ledger_record(record)
        self._complete_node(node_id, run_id)
        transaction_path.unlink(missing_ok=True)

    def _complete_node_if_covered(self, node_id: str, run_id: str | None) -> None:
        if node_id in ledger_covered_node_ids(self.root):
            self._complete_node(node_id, run_id)

    def _complete_node(self, node_id: str, run_id: str | None) -> None:
        state = self.state_mgr.load()
        state = self.state_mgr.complete_node_execution(state, node_id)
        if run_id:
            state = self.state_mgr.update_node_run(state, run_id, status="complete")
        self.state_mgr.save(state)

    def _prior_scope(self, snapshot: Any, ledger: dict[str, Any]) -> tuple[list[str], set[str]]:
        visible = set(getattr(snapshot, "snapshot_visible_ancestor_node_ids", []) or [])
        paths_list: list[str] = []
        for record in ledger.get("records", []):
            node_ids = set(record.get("node_ids") or ([record["node_id"]] if record.get("node_id") else []))
            if not node_ids or not node_ids <= visible:
                continue
            rel = record.get("output_path", "")
            if rel and (self.root / rel).exists():
                paths_list.append(rel)
        return paths_list, set(paths_list)

    def _persist_draft(self, node_id: str, file_seq: str, title: str, content: str) -> Path:
        path = paths.drafts_root(self.root) / _exec_slug(node_id) / f"{file_seq}.{_safe_title(title)}.md"
        file_ops.write_text(path, self._format_node_draft(node_id, file_seq, title, content))
        return path

    def _persist_run_state(self, run_id: str | None, **fields: Any) -> None:
        if not run_id:
            return
        state = self.state_mgr.load()
        state = self.state_mgr.update_node_run(state, run_id, **fields)
        self.state_mgr.save(state)

    def _format_node_draft(self, node_id: str, file_seq: str, title: str, content: str) -> str:
        dag = self._load_dag()
        nodes_by_id = {n["node_id"]: n for n in dag.get("nodes", [])}
        body = _strip_existing_program_preamble(_strip_front_matter(content))
        body = _strip_first_h1(body)
        body = _normalize_learning_section(body)
        body = _strip_source_trace(body)
        preamble = _node_draft_preamble(
            node_id=node_id,
            file_seq=file_seq,
            title=title,
            dag=dag,
            nodes_by_id=nodes_by_id,
            ledger=self._load_ledger(),
        )
        source_trace = self._source_trace(node_id)
        formatted = f"{preamble}\n\n{body.strip()}\n"
        if source_trace:
            formatted += f"\n{source_trace}\n"
        return formatted

    def _node_for_id(self, node_id: str) -> dict[str, Any]:
        return next(
            (node for node in self._load_dag().get("nodes", []) if node.get("node_id") == node_id),
            {"node_id": node_id},
        )

    def _source_evidence_hits(
        self,
        node: dict[str, Any],
        query: str,
        collections: list[str],
        *,
        top_k: int,
    ) -> list[dict]:
        semantic = self.retriever.source_hits(
            query,
            collections=collections,
            node_ids=[str(node.get("node_id") or "")],
            top_k=max(top_k, len(node.get("member_mtu_ids") or [])),
        )
        evidence_fn = getattr(self.retriever, "source_evidence", None)
        required = evidence_fn(list(node.get("member_mtu_ids") or [])) if callable(evidence_fn) else []
        return _dedupe_hits([*required, *semantic])

    def _source_trace(self, node_id: str) -> str:
        node = self._node_for_id(node_id)
        member_ids = list(node.get("member_mtu_ids") or [])
        if not member_ids:
            return ""
        mtus = {
            str(raw.get("mtu_id")): raw
            for raw in read_envelope_data(paths.mtus_path(self.root)).get("mtus", [])
            if raw.get("mtu_id")
        }
        lines = ["## 来源追溯", ""]
        missing: list[str] = []
        for mtu_id in member_ids:
            mtu = mtus.get(mtu_id)
            if mtu is None:
                missing.append(mtu_id)
                continue
            source = str(mtu.get("source_id") or f"{mtu.get('collection', '')}/{mtu.get('source_file', '')}")
            span = mtu.get("line_range") or []
            range_text = f"第 {span[0]}–{span[1]} 行" if len(span) == 2 else "行范围未知"
            lines.append(f"- `{source}`，{range_text}（`{mtu_id}`）")
        if missing:
            raise RuntimeError(f"Knowledge node {node_id} references missing MTUs: {missing}")
        return "\n".join(lines)

    def _load_dag(self) -> dict[str, Any]:
        from tree.planner.pipeline import load_dag

        return load_dag(self.root)

    def _load_ledger(self) -> dict[str, Any]:
        return _load_ledger(self.root)

    def _append_ledger_record(self, record: dict[str, Any]) -> None:
        _write_ledger_record(self.root, record)


def _load_ledger(root: Path) -> dict[str, Any]:
    path = paths.knowledge_ledger_path(root)
    if not path.exists():
        return {"records": []}
    loaded = read_json(path)
    return loaded if isinstance(loaded, dict) else {"records": []}


def _write_ledger_record(root: Path, record: dict[str, Any]) -> None:
    ledger = _load_ledger(root)
    records = ledger.setdefault("records", [])
    records[:] = [
        existing
        for existing in records
        if not ({record["node_id"]} & set(existing.get("node_ids", [])))
    ]
    records.append(record)
    write_json_atomic(paths.knowledge_ledger_path(root), ledger)


def stage_output_reindex(root: Path, node_id: str, output_path: Path) -> dict[str, Any]:
    """Stage an edited output so a failed RAG refresh is recoverable by NodeRunner."""
    ledger = _load_ledger(root)
    existing = next(
        (
            record
            for record in ledger.get("records", [])
            if node_id in set(record.get("node_ids") or [record.get("node_id")])
        ),
        None,
    )
    if not isinstance(existing, dict):
        raise RuntimeError(f"No ledger record found for revised node {node_id}")
    record = {
        **existing,
        "generation_id": _current_generation_id(root),
        "output_sha256": _file_sha256(output_path),
    }
    transaction_path = _output_transaction_path(root, node_id)
    write_json_atomic(
        transaction_path,
        {
            "schema_version": 1,
            "status": "file_published",
            "node_id": node_id,
            "filename": output_path.name,
            "output_sha256": record["output_sha256"],
            "ledger_record": record,
        },
    )
    return record


def commit_output_reindex(root: Path, node_id: str, record: dict[str, Any]) -> None:
    _write_ledger_record(root, record)
    _output_transaction_path(root, node_id).unlink(missing_ok=True)


def reconcile_ledger_generation(root: Path) -> int:
    """Archive outputs that do not belong to the committed planner generation."""
    generation_id = _current_generation_id(root)
    if not generation_id:
        return 0
    ledger = _load_ledger(root)
    current: list[dict[str, Any]] = []
    archived = 0
    for record in ledger.get("records", []):
        if not isinstance(record, dict):
            continue
        if str(record.get("generation_id") or "") == generation_id:
            current.append(record)
            continue
        rel = str(record.get("output_path") or "")
        output = root / rel if rel else None
        if output is not None and output.is_file() and output.parent == paths.outputs_root(root):
            old_generation = _exec_slug(str(record.get("generation_id") or "legacy"))
            archive_dir = paths.output_archive_root(root) / old_generation
            archive_dir.mkdir(parents=True, exist_ok=True)
            target = archive_dir / output.name
            if target.exists():
                target = archive_dir / f"{output.stem}--{_node_short(str(record.get('node_id') or 'node'))}{output.suffix}"
            output.replace(target)
            archived += 1
    if current != ledger.get("records", []):
        ledger["records"] = current
        write_json_atomic(paths.knowledge_ledger_path(root), ledger)
    return archived


def _existing_draft_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    candidate = Path(path)
    return candidate if candidate.exists() else None


def ledger_covered_node_ids(root: Path) -> set[str]:
    return _ledger_covered_node_ids({"records": _valid_ledger_records(root)})


def _ledger_covered_node_ids(ledger: dict[str, Any]) -> set[str]:
    covered: set[str] = set()
    for record in ledger.get("records", []):
        if record.get("node_id"):
            covered.add(record["node_id"])
        covered.update(record.get("node_ids", []))
    return covered


def ledger_output_ids(root: Path) -> list[str]:
    return [r.get("output_path", "") for r in _valid_ledger_records(root)]


def _valid_ledger_records(root: Path) -> list[dict[str, Any]]:
    generation_id = _current_generation_id(root)
    valid: list[dict[str, Any]] = []
    for record in _load_ledger(root).get("records", []):
        if not isinstance(record, dict):
            continue
        record_generation = str(record.get("generation_id") or "")
        if record_generation and generation_id and record_generation != generation_id:
            continue
        rel = str(record.get("output_path") or "")
        if not rel:
            continue
        output = root / rel
        if not output.is_file():
            continue
        expected_hash = str(record.get("output_sha256") or "")
        if expected_hash and _file_sha256(output) != expected_hash:
            continue
        valid.append(record)
    return valid


def _current_generation_id(root: Path) -> str:
    try:
        loaded = read_json(paths.material_manifest_path(root))
    except (OSError, ValueError):
        return ""
    return str(loaded.get("generation_id") or "") if isinstance(loaded, dict) else ""


def _output_transaction_path(root: Path, node_id: str) -> Path:
    return paths.output_transactions_root(root) / f"{_exec_slug(node_id)}.json"


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = read_json(path)
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _update_output_transaction(path: Path, **changes: Any) -> None:
    transaction = _read_optional_json(path)
    transaction.update(changes)
    write_json_atomic(path, transaction)


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _node_context(node_id: str, dag: dict[str, Any], nodes_by_id: dict[str, dict], snapshot: Any) -> str:
    parents, children = _prereq_adjacency(dag)
    node = nodes_by_id.get(node_id, {})
    direct = parents.get(node_id, [])
    ancestors = sorted(getattr(snapshot, "snapshot_visible_ancestor_node_ids", []) or [])
    future = sorted(_descendants(children, node_id))
    lines = [
        "ActiveNode Context — teach exactly one KnowledgeNode.",
        f"TARGET {node_id} | {node.get('title', node_id)} | {_defines_text(node)}",
    ]
    if direct:
        lines.append("Direct prerequisite nodes already completed: " + ", ".join(_node_label(n, nodes_by_id) for n in direct))
    if ancestors:
        lines.append("Visible ancestor nodes for learned RAG only: " + ", ".join(ancestors))
    if future:
        lines.append("Forbidden future descendant nodes: " + ", ".join(future))
    lines.append("Covered_Node_IDs must be exactly: " + node_id)
    lines.append("Do not cover sibling, future, or multiple KnowledgeNodes in this NodeRun.")
    return "\n".join(lines)


def _prereq_adjacency(dag: dict[str, Any]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    parents: dict[str, list[str]] = {}
    children: dict[str, list[str]] = {}
    for edge in dag.get("edges") or []:
        if edge.get("relation") != "prerequisite":
            continue
        src, dst = edge.get("from_node_id", ""), edge.get("to_node_id", "")
        if not src or not dst:
            continue
        parents.setdefault(dst, []).append(src)
        children.setdefault(src, []).append(dst)
    for values in parents.values():
        values.sort()
    for values in children.values():
        values.sort()
    return parents, children


def _descendants(children: dict[str, list[str]], node_id: str) -> set[str]:
    found: set[str] = set()
    stack = list(children.get(node_id, []))
    while stack:
        current = stack.pop()
        if current in found:
            continue
        found.add(current)
        stack.extend(children.get(current, []))
    return found


def _node_label(node_id: str, nodes_by_id: dict[str, dict]) -> str:
    node = nodes_by_id.get(node_id, {})
    return f"{node_id} ({node.get('title', node_id)})"


def _defines_text(node: dict[str, Any]) -> str:
    defines = node.get("defines") or node.get("keywords") or []
    return ", ".join(defines[:8])


def _node_draft_preamble(
    *,
    node_id: str,
    file_seq: str,
    title: str,
    dag: dict[str, Any],
    nodes_by_id: dict[str, dict],
    ledger: dict[str, Any],
) -> str:
    lines = [
        f"# {file_seq}. {_clean_title(title)}",
        "",
        "## 先修前置",
        "",
    ]
    parent_edges = [
        edge
        for edge in dag.get("edges", [])
        if edge.get("relation") == "prerequisite" and edge.get("to_node_id") == node_id
    ]
    parent_edges.sort(key=lambda e: nodes_by_id.get(e.get("from_node_id", ""), {}).get("source_order_index", 0))
    if parent_edges:
        lines.append("本节点由 DAG 判定需要先掌握以下已完成节点：")
        for edge in parent_edges:
            parent_id = edge.get("from_node_id", "")
            parent = nodes_by_id.get(parent_id, {})
            parent_title = str(parent.get("title") or parent_id)
            label = _parent_link_label(parent_id, parent_title, ledger)
            required = edge.get("required_defines") or parent.get("defines") or parent.get("keywords") or []
            required_text = "、".join(str(item) for item in required if str(item).strip())
            if required_text:
                lines.append(f"- {label}：相关先修 defines：{required_text}。")
            else:
                lines.append(f"- {label}。")
    else:
        lines.append("本节点无材料内先修节点。")

    external = nodes_by_id.get(node_id, {}).get("external_prerequisites") or []
    if external:
        lines.append("")
        lines.append("材料外基础：")
        for item in external:
            lines.append(f"- {item}")
    return "\n".join(lines).strip()


def _parent_link_label(parent_id: str, parent_title: str, ledger: dict[str, Any]) -> str:
    for record in ledger.get("records", []):
        node_ids = set(record.get("node_ids") or ([record["node_id"]] if record.get("node_id") else []))
        if parent_id not in node_ids:
            continue
        output_path = str(record.get("output_path") or "")
        link = _output_relative_link(output_path)
        if link:
            return f"[{parent_title}]({link})"
    return parent_title


def _output_relative_link(output_path: str) -> str:
    prefix = "outputs/"
    if output_path.startswith(prefix):
        return output_path[len(prefix):]
    return output_path


def _node_file_seq(nodes: list[dict[str, Any]], node_id: str) -> str:
    ordered = sorted(nodes, key=lambda n: (n.get("source_order_index", 0), n.get("node_id", "")))
    for index, node in enumerate(ordered, start=1):
        if node.get("node_id") == node_id:
            return str(index).zfill(3)
    return "001"


def _output_filename(file_seq: str, title: str, node_id: str, output_root: Path) -> str:
    base = f"{file_seq}.{_safe_title(title)}.md"
    if not (output_root / base).exists():
        return base
    return f"{file_seq}.{_safe_title(title)}--{_node_short(node_id)}.md"


def _safe_title(title: str) -> str:
    safe = re.sub(r"[^\w一-鿿.-]", "_", _clean_title(title)).strip("._")
    return safe or "untitled"


def _clean_title(title: str) -> str:
    return re.sub(r"^\s*\d{1,4}[.．、_\-\s]+", "", title.strip()).strip()


def _bottlenecks_equivalent(previous: str | None, current: str) -> bool:
    if not previous:
        return False

    def normalize(value: str) -> str:
        value = re.sub(r"(?im)^\s*#*\s*bottleneck report\s*$", "", value)
        value = re.sub(r"[\W_]+", "", value.casefold())
        return value

    left = normalize(previous)
    right = normalize(current)
    if not left or not right:
        return False
    return left == right or SequenceMatcher(None, left, right).ratio() >= 0.88


def _strip_first_h1(text: str) -> str:
    return re.sub(r"^\s*#\s+.+?(?:\n+|$)", "", text.strip(), count=1)


def _strip_existing_program_preamble(text: str) -> str:
    text = text.strip()
    match = re.match(r"^\s*#\s+.+?\n+##\s+先修前置\s*\n", text, flags=re.DOTALL)
    if not match:
        return text
    next_section = re.search(r"\n##\s+(?!先修前置\b).+", text[match.end():])
    if not next_section:
        return ""
    return text[match.end() + next_section.start() + 1:].strip()


def _normalize_learning_section(text: str) -> str:
    text = text.strip()
    match = re.match(
        r"^##\s+学习目标与先修前置\s*\n(?P<body>.*?)(?=^##\s+|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not match:
        return text
    body = _keep_learning_goal_text(match.group("body").strip())
    replacement = f"## 学习目标\n\n{body.strip()}\n\n" if body.strip() else ""
    return (replacement + text[match.end():].lstrip()).strip()


def _strip_source_trace(text: str) -> str:
    """Remove a prior generated provenance section before reformatting."""
    return re.sub(
        r"\n*##\s+来源追溯\s*\n.*\Z",
        "",
        text.strip(),
        flags=re.MULTILINE | re.DOTALL,
    ).strip()


def _dedupe_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for hit in hits:
        metadata = hit.get("metadata") or {}
        key = str(metadata.get("chunk_id") or metadata.get("mtu_id") or hit.get("text") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(hit)
    return result


def _keep_learning_goal_text(section_body: str) -> str:
    learning_match = re.search(r"(?:\*\*)?学习目标(?:\*\*)?[：:]", section_body)
    prereq_match = re.search(r"(?:\*\*)?先修(?:知识|前置)?(?:\*\*)?[：:]", section_body)
    if learning_match and prereq_match:
        if learning_match.start() < prereq_match.start():
            return section_body[learning_match.start():prereq_match.start()].strip()
        return section_body[learning_match.start():].strip()
    if learning_match:
        return section_body[learning_match.start():].strip()
    if "学习完成后" in section_body:
        return section_body[section_body.index("学习完成后"):].strip()
    return section_body


def _node_short(node_id: str) -> str:
    return re.sub(r"^[a-z]+:", "", node_id).replace("_", "")[:8] or "node"


def _exec_slug(value: str) -> str:
    return re.sub(r"[^\w.-]", "_", value)


def _strip_front_matter(content: str) -> str:
    text = content.strip()
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :].lstrip()
    return text
