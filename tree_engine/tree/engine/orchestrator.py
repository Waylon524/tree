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
from tree.config import Settings
from tree.engine.node_run import NodeRunner, ledger_covered_node_ids, ledger_output_ids
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
            student=StudentAgent(self.client),
            writer=WriterAgent(self.client),
            archivist=ArchivistAgent(self.client),
            dagger=DaggerAgent(self.client),
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
            self.rag_indexer = self.rag_indexer or _make_rag_indexer(self.rag_client)
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
        self.progress.reset()
        await self.prepare_sources()
        self._refresh_noderun_progress(status="running")

        while True:
            state = self.state_mgr.load()
            in_progress = self.state_mgr.find_in_progress_all(state)
            if not in_progress:
                state = self._activate_ready_node_runs(state)
                in_progress = self.state_mgr.find_in_progress_all(state)

            if not in_progress:
                if _all_nodes_covered(self.root):
                    self._refresh_noderun_progress(status="complete", message="All nodes complete")
                    self.progress.complete("WOODS_COMPLETE — all source nodes covered.")
                    return
                self._refresh_noderun_progress(status="blocked", message="No ready node runs")
                self.progress.update({"phase": "blocked", "message": "TREE_BLOCKED — no ready node runs."})
                return

            self._refresh_noderun_progress(
                status="running",
                active=[item.node_id for item in in_progress[: self.settings.max_active_node_runs]],
                message="Running active nodes",
            )
            await asyncio.gather(
                *[
                    self.node_runner.run_one(item.node_id)
                    for item in in_progress[: self.settings.max_active_node_runs]
                ]
            )
            self._refresh_noderun_progress(status="running")

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


class Retriever:
    """RAG adapter consumed by NodeRunner."""

    def __init__(self, rag_client: Any, rag_indexer: Any, root: Path):
        self.rag = rag_client
        self.indexer = rag_indexer
        self.root = root

    def source_hits(
        self, query: str, *, collections: list[str], node_ids: list[str], top_k: int
    ) -> list[dict]:
        filters: dict[str, Any] = {"content_kind": "source"}
        if collections:
            filters["source_collection"] = collections
        if node_ids:
            filters["node_id"] = node_ids
        return self.rag.query(query, top_k=top_k, filters=filters, include_drafts=False)

    def finished_hits(self, query: str, *, allowed_paths: set[str], top_k: int) -> list[dict]:
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


def _make_rag_indexer(rag_client: Any) -> Any:
    from tree.rag.indexer import RAGIndexer

    return RAGIndexer(rag_client)


def _all_nodes_covered(root: Path) -> bool:
    nodes = load_dag(root).get("nodes", [])
    if not nodes:
        return True
    covered = ledger_covered_node_ids(root)
    return all(node.get("node_id") in covered for node in nodes)
