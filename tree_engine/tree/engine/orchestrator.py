"""Top-level foreground run loop (Step 8)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tree.agents.archivist import ArchivistAgent
from tree.agents.dagger import DaggerAgent
from tree.agents.examiner import ExaminerAgent
from tree.agents.student import StudentAgent
from tree.agents.writer import WriterAgent
from tree.config import DEFAULT_SOURCE_MTU_CHUNK_TOKENS, Settings
from tree.engine.node_run import (
    NodeRunner,
    ledger_covered_node_ids,
    ledger_output_ids,
    reconcile_ledger_generation,
)
from tree.engine.ingest_driver import prepare_sources
from tree.io import paths
from tree.model.client import LLMClient
from tree.observability.progress import ProgressTracker
from tree.planner.pipeline import load_dag
from tree.planner.schedule import start_ready_node_runs
from tree.state.manager import StateManager

if TYPE_CHECKING:
    from tree.rag.indexer import RAGIndexer


class TreeEngine:
    def __init__(
        self,
        settings: Settings,
        *,
        client: LLMClient | None = None,
        agents: Any | None = None,
        rag_client: Any | None = None,
        rag_indexer: "RAGIndexer | None" = None,
        retriever: Any | None = None,
        node_runner: NodeRunner | None = None,
        branch_runner: NodeRunner | None = None,
    ):
        self.settings = settings
        self.root = Path(settings.project_root)
        paths.ensure_workspace_dirs(self.root)
        self.client = client
        if agents is None:
            self.client = self.client or LLMClient(settings)
        self.agents = agents or _Agents(
            examiner=ExaminerAgent(
                self.client,
                max_format_retries=settings.max_format_retries,
                project_root=self.root,
            ),
            student=StudentAgent(self.client, project_root=self.root),
            writer=WriterAgent(self.client, project_root=self.root),
            archivist=ArchivistAgent(self.client, project_root=self.root),
            dagger=DaggerAgent(self.client, project_root=self.root),
        )
        self.archivist = getattr(self.agents, "archivist", None)
        self.examiner = getattr(self.agents, "examiner", None)
        self.student = getattr(self.agents, "student", None)
        self.writer = getattr(self.agents, "writer", None)
        self.state_mgr = StateManager(paths.pipeline_state_path(self.root))
        self.progress = ProgressTracker(self.root)
        self.rag_client = rag_client
        self.rag_indexer = rag_indexer
        self.retriever = retriever
        node_runner = node_runner or branch_runner
        if node_runner is None:
            self.rag_client = self.rag_client or _make_rag_client(self.root)
            self.rag_indexer = self.rag_indexer or _make_rag_indexer(
                self.rag_client,
                source_mtu_chunk_tokens=getattr(
                    settings, "source_mtu_chunk_tokens", DEFAULT_SOURCE_MTU_CHUNK_TOKENS
                ),
            )
            self.retriever = self.retriever or Retriever(self.rag_client, self.rag_indexer, self.root)
        self.node_runner = node_runner or NodeRunner(
            root=self.root,
            settings=settings,
            examiner=self.examiner,
            student=self.student,
            writer=self.writer,
            retriever=self.retriever,
            state_mgr=self.state_mgr,
        )

    async def run(self) -> None:
        """Run until all DAG nodes are covered, or until the planner is blocked."""
        self.progress.begin_run()
        try:
            await self._run_foreground()
        except asyncio.CancelledError:
            self.progress.stop()
            raise
        except Exception as exc:
            self.progress.fail_active_stage(
                f"{type(exc).__name__}: {exc}",
                code="engine_run_failed",
            )
            raise

    async def _run_foreground(self) -> None:
        _clear_stale_run_logs(self.root)
        await self.prepare_sources()
        reconcile_ledger_generation(self.root)
        self._prune_state_to_current_dag()
        state = self.state_mgr.retry_failed_node_executions(self.state_mgr.load())
        self.state_mgr.save(state)
        self._refresh_noderun_progress(status="running")
        running: dict[str, asyncio.Task[str]] = {}
        had_failure = False

        while True:
            state = self.state_mgr.load()
            state = self._activate_ready_node_runs(state)
            in_progress = self.state_mgr.find_in_progress_all(state)

            for item in in_progress:
                if len(running) >= self.settings.max_active_node_runs:
                    break
                if item.node_id not in running:
                    running[item.node_id] = asyncio.create_task(self.node_runner.run_one(item.node_id))

            if not running:
                if _all_nodes_covered(self.root):
                    self._refresh_noderun_progress(status="complete", message="All nodes complete")
                    self.progress.complete("WOODS_COMPLETE — all source nodes covered.")
                    return
                if had_failure:
                    done, total = self._node_completion_counts()
                    if done > 0:
                        message = f"TREE_PARTIAL — {done}/{total} nodes complete; failed nodes need attention."
                        self._refresh_noderun_progress(status="partial", message=message)
                        self.progress.update({"phase": "partial", "message": message})
                    else:
                        self._refresh_noderun_progress(status="failed", message="NodeRun failed")
                        self.progress.update({"phase": "failed", "message": "TREE_FAILED — NodeRun failed."})
                    return
                self._refresh_noderun_progress(status="blocked", message="No ready node runs")
                self.progress.update({"phase": "blocked", "message": "TREE_BLOCKED — no ready node runs."})
                return

            self._refresh_noderun_progress(
                status="running",
                active=list(running),
                message="Running active nodes",
            )
            try:
                completed, _pending = await asyncio.wait(
                    running.values(),
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                for task in running.values():
                    task.cancel()
                await asyncio.gather(*running.values(), return_exceptions=True)
                raise
            for task in completed:
                node_id = next(key for key, value in running.items() if value is task)
                running.pop(node_id, None)
                try:
                    await task
                except Exception as exc:  # noqa: BLE001
                    had_failure = True
                    self._mark_node_failed(node_id, exc)
                    self._refresh_noderun_progress(
                        status="failed",
                        active=[node_id],
                        message=f"NodeRun failed: {node_id}",
                    )
                    continue
                self._refresh_noderun_progress(
                    status="running",
                    active=list(running),
                    message="Running active nodes",
                )

    async def prepare_sources(self) -> dict[str, Any]:
        return await prepare_sources(self)

    def _activate_ready_node_runs(self, state: Any) -> Any:
        updated = start_ready_node_runs(
            state,
            load_dag(self.root),
            covered_node_ids=ledger_covered_node_ids(self.root),
            max_active=self.settings.max_active_node_runs,
            finished_output_ids=ledger_output_ids(self.root),
            now=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self.state_mgr.save(updated)
        return updated

    def _mark_node_failed(self, node_id: str, exc: Exception) -> None:
        message = f"{type(exc).__name__}: {exc}"
        state = self.state_mgr.load()
        state = self.state_mgr.mark_node_execution_failed(state, node_id, message)
        self.state_mgr.save(state)
        self._append_progress_error(node_id, message)

    def _prune_state_to_current_dag(self) -> None:
        valid_node_ids = {str(node.get("node_id")) for node in load_dag(self.root).get("nodes", []) if node.get("node_id")}
        if not valid_node_ids:
            return
        state = self.state_mgr.load()
        state.node_executions = [
            item for item in state.node_executions if item.node_id in valid_node_ids
        ]
        state.node_runs = [item for item in state.node_runs if item.node_id in valid_node_ids]
        self.state_mgr.save(state)

    def _append_progress_error(self, node_id: str, message: str) -> None:
        try:
            self.progress.record_error(
                stage="noderun",
                code="node_run_failed",
                resource=node_id,
                message=message,
                recoverable=True,
                action="Retry this failed node; its exam, draft, and bottleneck history are preserved.",
            )
        except Exception:
            return

    def _refresh_noderun_progress(
        self,
        *,
        status: str | None = None,
        active: list[str] | None = None,
        message: str | None = None,
    ) -> None:
        nodes = load_dag(self.root).get("nodes", [])
        covered = ledger_covered_node_ids(self.root)
        total = len(nodes)
        done = sum(1 for node in nodes if node.get("node_id") in covered)
        try:
            self.progress.set_stage(
                "noderun",
                total=total,
                done=done,
                status=status or ("complete" if total and done >= total else "running"),
                active=active or [],
                message=message or f"{done}/{total} nodes complete",
            )
        except Exception:
            return

    def _node_completion_counts(self) -> tuple[int, int]:
        nodes = load_dag(self.root).get("nodes", [])
        covered = ledger_covered_node_ids(self.root)
        return sum(1 for node in nodes if node.get("node_id") in covered), len(nodes)


class Retriever:
    """RAG adapter consumed by NodeRunner."""

    def __init__(self, rag_client: Any, rag_indexer: Any, root: Path):
        self.rag = rag_client
        self.indexer = rag_indexer
        self.root = root

    def source_hits(
        self, query: str, *, collections: list[str], node_ids: list[str], top_k: int
    ) -> list[dict[str, Any]]:
        filters: dict[str, Any] = {"content_kind": "source"}
        if collections:
            filters["source_collection"] = collections
        if node_ids:
            filters["node_id"] = node_ids
        return self.rag.query(query, top_k=top_k, filters=filters, include_drafts=False)

    def source_evidence(self, mtu_ids: list[str]) -> list[dict[str, Any]]:
        """Return at least one deterministic stored chunk for every member MTU."""
        evidence: list[dict[str, Any]] = []
        for mtu_id in dict.fromkeys(item for item in mtu_ids if item):
            hits = self.rag.scroll_chunks(
                filters={"content_kind": "source", "mtu_id": mtu_id},
                include_drafts=False,
            )
            if not hits:
                raise RuntimeError(f"Missing required source evidence for MTU {mtu_id}")
            hits.sort(key=lambda hit: int((hit.get("metadata") or {}).get("chunk_index", 0)))
            evidence.append(hits[0])
        return evidence

    def finished_hits(
        self, query: str, *, allowed_paths: set[str], top_k: int
    ) -> list[dict[str, Any]]:
        if not allowed_paths:
            return []
        return self.rag.query(
            query,
            top_k=top_k,
            filters={"content_kind": "finished", "path": sorted(allowed_paths)},
            include_drafts=False,
        )

    def index_finished(self, node_id: str, path: Path) -> int:
        return self.indexer.index_finished_file(self.root, node_id, path)


class _Agents:
    def __init__(self, **agents: Any):
        self.__dict__.update(agents)


def _make_rag_client(root: Path) -> Any:
    from tree.rag.client import RAGClient

    return RAGClient(store_path=paths.rag_store_path(root))


def _make_rag_indexer(
    rag_client: Any,
    *,
    source_mtu_chunk_tokens: int = DEFAULT_SOURCE_MTU_CHUNK_TOKENS,
) -> Any:
    from tree.rag.indexer import RAGIndexer

    return RAGIndexer(rag_client, source_mtu_chunk_tokens=source_mtu_chunk_tokens)


def _clear_stale_run_logs(root: Path) -> None:
    """Drop diagnostic logs from prior runs so the error panel is run-scoped."""
    temp_root = paths.pipeline_temp_root(root)
    if not temp_root.exists():
        return
    for log_path in temp_root.glob("*.log"):
        try:
            log_path.unlink()
        except OSError:
            pass


def _all_nodes_covered(root: Path) -> bool:
    nodes = load_dag(root).get("nodes", [])
    if not nodes:
        return True
    covered = ledger_covered_node_ids(root)
    return all(node.get("node_id") in covered for node in nodes)
