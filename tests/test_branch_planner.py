from pathlib import Path

from tree.curriculum.branches import (
    build_branch_prior_scope,
    branch_context_for_run,
    rebuild_branch_plan,
    start_ready_branch_runs,
    validate_branch_covered_node_ids,
)
from tree.curriculum.chapter_naming import next_tree_id
from tree.curriculum.graph import rebuild_knowledge_graph
from tree.curriculum.ledger import reconcile_finished_outputs, update_finished_record
from tree.engine import TreeEngine, _branch_plan_blockage, _chapter_name_from_required_nodes
from tree.io import file_ops
from tree.state.manager import StateManager
from tree.state.models import BranchRunRecord, CoverageSnapshot, PipelineState


def _candidate(
    node_id: str,
    concepts: list[str],
    *,
    prerequisites: list[str] | None = None,
    chunks: list[str] | None = None,
    lines: int = 320,
    **extra: object,
) -> dict:
    candidate = {
        "candidate_id": node_id,
        "status": "pending",
        "title_hint": concepts[0],
        "primary_source_collection": node_id,
        "source_collections": [node_id],
        "core_concepts": concepts,
        "prerequisite_concepts": prerequisites or [],
        "prerequisite_candidates": [],
        "representative_chunks": [
            {"chunk_ref": chunk, "core_concepts": concepts, "summary": " ".join(concepts)}
            for chunk in chunks or [f"{node_id}#000"]
        ],
        "selection_priority": 0.5,
        "estimated_output_lines": lines,
    }
    candidate.update(extra)
    return candidate


def _nodes(*candidates: dict) -> dict:
    return {"version": 1, "kind": "candidate_nodes", "chapter_candidates": list(candidates)}


def _ledger_record(path: str, graph_node_id: str, concepts: list[str]) -> dict:
    return {
        "chapter": path.split("/")[1],
        "file_seq": Path(path).stem.split(".", 1)[0],
        "filename": Path(path).name,
        "path": path,
        "knowledge_point": concepts[0],
        "covered_concepts": concepts,
        "prerequisites": [],
        "hit_chunks": [],
        "source_collections": [],
        "graph_node_id": graph_node_id,
        "required_nodes": [],
    }


def _ledger_record_many(path: str, covered_node_ids: list[str], concepts: list[str]) -> dict:
    record = _ledger_record(path, covered_node_ids[0], concepts)
    record["covered_node_ids"] = covered_node_ids
    return record


def _ledger(*records: dict) -> dict:
    return {"version": 1, "records": list(records)}


def test_dag_branch_segmentation_uses_structural_nodes_without_size_split(tmp_path: Path) -> None:
    graph = rebuild_knowledge_graph(
        tmp_path,
        _nodes(
            _candidate("candidate:root", ["根概念"], lines=400),
            _candidate("candidate:middle", ["中间概念"], prerequisites=["根概念"], lines=1400),
            _candidate("candidate:fork", ["分叉概念"], prerequisites=["中间概念"], lines=1600),
            _candidate("candidate:left", ["左侧应用"], prerequisites=["分叉概念"], lines=1300),
            _candidate("candidate:right", ["右侧应用"], prerequisites=["分叉概念"], lines=1300),
        ),
        _ledger(),
    )

    plan = rebuild_branch_plan(tmp_path, graph, _ledger())
    branches = plan["branches"]["branches"]

    assert ["candidate:root", "candidate:middle", "candidate:fork"] in [
        branch["node_ids"] for branch in branches
    ]
    assert ["candidate:fork", "candidate:left"] in [branch["node_ids"] for branch in branches]
    assert ["candidate:fork", "candidate:right"] in [branch["node_ids"] for branch in branches]
    root_branch = next(branch for branch in branches if branch["start_node_id"] == "candidate:root")
    left_branch = next(branch for branch in branches if branch["end_node_id"] == "candidate:left")
    assert root_branch["coverage_node_ids"] == [
        "candidate:root",
        "candidate:middle",
        "candidate:fork",
    ]
    assert left_branch["coverage_node_ids"] == ["candidate:left"]
    assert root_branch["length_stats"]["estimated_output_lines"] > 3000


def test_junction_downstream_branch_waits_for_all_upstream_branches(tmp_path: Path) -> None:
    graph = rebuild_knowledge_graph(
        tmp_path,
        _nodes(
            _candidate("candidate:alpha-root", ["甲根"]),
            _candidate("candidate:alpha-mid", ["甲中"], prerequisites=["甲根"]),
            _candidate("candidate:beta-root", ["乙根"]),
            _candidate("candidate:beta-mid", ["乙中"], prerequisites=["乙根"]),
            _candidate("candidate:junction", ["汇合点"], prerequisites=["甲中", "乙中"]),
            _candidate("candidate:after", ["后续点"], prerequisites=["汇合点"]),
        ),
        _ledger(),
    )

    empty_plan = rebuild_branch_plan(tmp_path, graph, _ledger())
    blocked = next(
        branch
        for branch in empty_plan["branches"]["branches"]
        if branch["start_node_id"] == "candidate:junction"
    )
    assert blocked["status"] == "blocked"

    covered = _ledger(
        _ledger_record("outputs/tree-001/01.alpha-root.md", "candidate:alpha-root", ["甲根"]),
        _ledger_record("outputs/tree-001/02.alpha-mid.md", "candidate:alpha-mid", ["甲中"]),
        _ledger_record("outputs/tree-002/01.beta-root.md", "candidate:beta-root", ["乙根"]),
        _ledger_record("outputs/tree-002/02.beta-mid.md", "candidate:beta-mid", ["乙中"]),
        _ledger_record("outputs/tree-002/03.junction.md", "candidate:junction", ["汇合点"]),
    )
    ready_plan = rebuild_branch_plan(tmp_path, graph, covered)
    ready = next(
        branch
        for branch in ready_plan["branches"]["branches"]
        if branch["start_node_id"] == "candidate:junction"
    )

    assert set(ready["upstream_branch_ids"]) == {
        branch["branch_id"]
        for branch in ready_plan["branches"]["branches"]
        if branch["end_node_id"] == "candidate:junction"
    }
    assert ready["status"] == "ready"


def test_ready_branches_start_parallel_runs_with_fixed_snapshots(tmp_path: Path) -> None:
    graph = rebuild_knowledge_graph(
        tmp_path,
        _nodes(
            _candidate("candidate:left-root", ["左根"]),
            _candidate("candidate:right-root", ["右根"]),
            _candidate("candidate:third-root", ["三根"]),
        ),
        _ledger(),
    )
    plan = rebuild_branch_plan(tmp_path, graph, _ledger())

    state = start_ready_branch_runs(
        PipelineState(),
        plan["branches"],
        _ledger(),
        max_active_branch_runs=2,
        now="2026-05-30T12:00:00Z",
    )
    rerun = start_ready_branch_runs(
        state,
        plan["branches"],
        _ledger(_ledger_record("outputs/tree-999/01.new.md", "candidate:new", ["新概念"])),
        max_active_branch_runs=2,
        now="2026-05-30T12:05:00Z",
    )

    assert len(state.branch_runs) == 2
    assert len({run.branch_id for run in state.branch_runs}) == 2
    assert all(run.status == "running" for run in state.branch_runs)
    assert state.branch_runs[0].coverage_snapshot.finished_output_ids == []
    assert rerun.branch_runs == state.branch_runs


def test_branch_context_uses_snapshot_not_later_finished_outputs(tmp_path: Path) -> None:
    graph = rebuild_knowledge_graph(
        tmp_path,
        _nodes(_candidate("candidate:root", ["根概念"])),
        _ledger(),
    )
    plan = rebuild_branch_plan(tmp_path, graph, _ledger())
    state = start_ready_branch_runs(
        PipelineState(),
        plan["branches"],
        _ledger(),
        max_active_branch_runs=1,
        now="2026-05-30T12:00:00Z",
    )
    later_ledger = _ledger(
        _ledger_record("outputs/tree-999/01.later.md", "candidate:later", ["后续完成"])
    )

    context = branch_context_for_run(state.branch_runs[0], plan["branches"], later_ledger)

    assert "candidate:root" in context
    assert "candidate:later" not in context
    assert "Snapshot finished outputs: none" in context


def test_canonical_merge_leak_blocks_duplicate_planned_nodes(tmp_path: Path) -> None:
    graph = rebuild_knowledge_graph(
        tmp_path,
        _nodes(
            _candidate("candidate:first", ["重复概念"], chunks=["same#001"]),
            _candidate("candidate:second", ["重复概念"], chunks=["same#001"]),
        ),
        _ledger(),
    )

    plan = rebuild_branch_plan(tmp_path, graph, _ledger())

    assert any(item["kind"] == "canonical_merge_leak" for item in plan["dag"]["diagnostics"])
    assert all(branch["status"] == "blocked" for branch in plan["branches"]["branches"])
    assert "canonical_merge_leak" in _branch_plan_blockage(plan["branches"])


def test_canonical_merge_pending_nodes_do_not_enter_ready_branches(tmp_path: Path) -> None:
    graph = rebuild_knowledge_graph(
        tmp_path,
        {
            "version": 1,
            "kind": "candidate_nodes",
            "diagnostics": [
                {
                    "kind": "canonical_merge_pending",
                    "nodes": ["candidate:forced-a", "candidate:forced-b"],
                    "group_ids": ["kg:forced:a", "kg:forced:b"],
                    "reason": "Strongly similar groups require AI merge review.",
                }
            ],
            "chapter_candidates": [
                _candidate("candidate:forced-a", ["受迫振动", "共振"]),
                _candidate("candidate:forced-b", ["受迫振动", "共振", "稳态"]),
                _candidate("candidate:foundation", ["简谐振动"]),
            ],
        },
        _ledger(),
    )

    plan = rebuild_branch_plan(tmp_path, graph, _ledger())

    blocked = [
        branch
        for branch in plan["branches"]["branches"]
        if set(branch["node_ids"]) & {"candidate:forced-a", "candidate:forced-b"}
    ]
    assert blocked
    assert all(branch["status"] == "blocked" for branch in blocked)
    assert any(branch["status"] == "ready" for branch in plan["branches"]["branches"])


def test_noise_and_review_nodes_are_not_schedulable_as_root(tmp_path: Path) -> None:
    graph = rebuild_knowledge_graph(
        tmp_path,
        _nodes(
            _candidate(
                "candidate:review",
                ["温故知新"],
                title_hint="温故知新",
                core_concepts=[],
                teaching_roles=["review"],
                source_types=["review"],
                representative_chunks=[
                    {
                        "chunk_ref": "lesson#000",
                        "core_concepts": [],
                        "teaching_role": "review",
                        "source_type": "review",
                    }
                ],
            ),
            _candidate("candidate:foundation", ["基础概念"]),
        ),
        _ledger(),
    )

    plan = rebuild_branch_plan(tmp_path, graph, _ledger())

    review_branches = [
        branch
        for branch in plan["branches"]["branches"]
        if "candidate:review" in branch["node_ids"]
    ]
    assert review_branches
    assert all(branch["status"] == "blocked" for branch in review_branches)
    assert any(
        branch["status"] == "ready" and "candidate:foundation" in branch["node_ids"]
        for branch in plan["branches"]["branches"]
    )


def test_auxiliary_only_nodes_are_not_scheduled_as_ready_branches(tmp_path: Path) -> None:
    graph = rebuild_knowledge_graph(
        tmp_path,
        _nodes(
            _candidate(
                "candidate:aux",
                ["§"],
                title_hint="§",
                core_concepts=[],
                canonicalization_status="auxiliary_only",
                schedulable=False,
                blocked_reason="auxiliary_only",
            ),
            _candidate("candidate:foundation", ["基础概念"]),
        ),
        _ledger(),
    )

    plan = rebuild_branch_plan(tmp_path, graph, _ledger())

    aux_branches = [
        branch for branch in plan["branches"]["branches"] if "candidate:aux" in branch["node_ids"]
    ]
    assert aux_branches
    assert all(branch["status"] == "blocked" for branch in aux_branches)
    assert any(
        branch["status"] == "ready" and "candidate:foundation" in branch["node_ids"]
        for branch in plan["branches"]["branches"]
    )


def test_reconcile_finished_outputs_reads_branch_isolated_paths(tmp_path: Path) -> None:
    draft = file_ops.write_draft(tmp_path, "tree-001/branch-001", "01.根概念.md", "# 根概念\n")
    assert draft.exists()
    finished = file_ops.move_draft_to_finished(tmp_path, "tree-001/branch-001", draft.name)
    update_finished_record(
        tmp_path,
        "tree-001/branch-001",
        finished,
        graph_node_id="candidate:root",
    )

    ledger = reconcile_finished_outputs(tmp_path)

    assert any(
        record.get("path") == "outputs/tree-001/branch-001/01.根概念.md"
        for record in ledger["records"]
    )
    record = next(
        item
        for item in ledger["records"]
        if item.get("path") == "outputs/tree-001/branch-001/01.根概念.md"
    )
    assert record["execution_path"] == "tree-001/branch-001"
    assert record["tree_id"] == "tree-001"
    assert record["branch_id"] == "branch-001"
    assert record["chapter"] == "tree-001/branch-001"


def test_state_manager_preserves_branch_runs_when_updating_chapters(tmp_path: Path) -> None:
    manager = StateManager(tmp_path / ".tree/runtime/pipeline-state.json")
    state = PipelineState(
        branch_runs=[
            BranchRunRecord(
                branch_id="branch:abc",
                run_id="run:abc",
                coverage_snapshot=CoverageSnapshot(finished_output_ids=["finished:outputs/tree-001/01.a.md"]),
            )
        ]
    )

    state = manager.add_chapter(
        state,
        "tree-001/branch-abc",
        branch_id="branch:abc",
        branch_run_id="run:abc",
    )
    state = manager.add_file_completed(state, "tree-001/branch-abc", "01.a.md")
    state = manager.complete_chapter(state, "tree-001/branch-abc")

    assert state.branch_runs[0].run_id == "run:abc"
    assert state.chapters[0].branch_id == "branch:abc"
    assert state.chapters[0].execution_path == "tree-001/branch-abc"
    assert state.chapters[0].tree_id == "tree-001"
    assert state.chapters[0].outputs_completed == ["01.a.md"]


def test_crash_reconcile_updates_branch_run_completed_outputs(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs/tree-001/branch-abc"
    output_dir.mkdir(parents=True)
    output_path = output_dir / "01.root.md"
    output_path.write_text("# 根概念\n", encoding="utf-8")

    manager = StateManager(tmp_path / ".tree/runtime/pipeline-state.json")
    state = PipelineState(
        branch_runs=[
            BranchRunRecord(
                branch_id="branch:abc",
                run_id="run:abc",
                execution_path="tree-001/branch-abc",
            )
        ]
    )
    state = manager.add_chapter(
        state,
        "tree-001/branch-abc",
        graph_node_id="candidate:root",
        branch_id="branch:abc",
        branch_run_id="run:abc",
    )
    manager.save(state)

    fake_engine = type("FakeEngine", (), {})()
    fake_engine.settings = type("Settings", (), {"project_root": tmp_path})()
    fake_engine.state_mgr = manager
    fake_engine._index_finished_output_or_raise = lambda _chapter, _path: 1

    reconciled = TreeEngine._reconcile_finished_outputs(fake_engine, state, "tree-001/branch-abc")

    assert reconciled.chapters[0].outputs_completed == ["01.root.md"]
    assert reconciled.branch_runs[0].outputs_completed == ["01.root.md"]


def test_nested_branch_paths_keep_tree_id_and_chapter_owner() -> None:
    state = PipelineState()
    manager = StateManager(Path("/tmp/unused-state.json"))
    state = manager.add_chapter(state, "tree-001/branch-abc")

    assert next_tree_id(state) == "tree-002"
    assert (
        _chapter_name_from_required_nodes(
            ["finished:outputs/tree-001/branch-abc/01.根概念.md"]
        )
        == "tree-001/branch-abc"
    )


def test_legacy_chapter_record_loads_as_branch_execution() -> None:
    state = PipelineState.model_validate(
        {
            "chapters": [
                {
                    "chapter_name": "tree-003/branch-main",
                    "status": "in_progress",
                    "files_completed": ["01.root.md"],
                    "chapter_title": "力学基础",
                    "required_nodes": ["candidate:middle"],
                    "graph_node_id": "candidate:middle",
                    "branch_id": "branch:main",
                    "branch_run_id": "run:main",
                }
            ]
        }
    )

    execution = state.chapters[0]

    assert execution.execution_path == "tree-003/branch-main"
    assert execution.chapter_name == "tree-003/branch-main"
    assert execution.tree_id == "tree-003"
    assert execution.outputs_completed == ["01.root.md"]
    assert execution.files_completed == ["01.root.md"]
    assert execution.display_title == "力学基础"
    assert execution.chapter_title == "力学基础"
    assert execution.coverage_node_ids == ["candidate:middle"]
    assert execution.current_start_node_id == "candidate:middle"


def test_downstream_branch_execution_inherits_upstream_tree_id(tmp_path: Path) -> None:
    graph = rebuild_knowledge_graph(
        tmp_path,
        _nodes(
            _candidate("candidate:root", ["根概念"]),
            _candidate("candidate:fork", ["分支点"], prerequisites=["根概念"]),
            _candidate("candidate:leaf", ["叶子"], prerequisites=["分支点"]),
            _candidate("candidate:sibling", ["旁支"], prerequisites=["分支点"]),
        ),
        _ledger(),
    )
    ledger = _ledger(
        _ledger_record_many(
            "outputs/tree-007/branch-root/01.root-fork.md",
            ["candidate:root", "candidate:fork"],
            ["根概念", "分支点"],
        )
    )
    output_dir = tmp_path / "outputs/tree-007/branch-root"
    output_dir.mkdir(parents=True)
    output_path = output_dir / "01.root-fork.md"
    output_path.write_text("# 根概念与分支点\n", encoding="utf-8")
    update_finished_record(
        tmp_path,
        "tree-007/branch-root",
        output_path,
        graph_node_id="candidate:root",
        covered_node_ids=["candidate:root", "candidate:fork"],
    )
    plan = rebuild_branch_plan(tmp_path, graph, ledger)
    state_mgr = StateManager(tmp_path / ".tree/runtime/pipeline-state.json")
    state = PipelineState(
        chapters=[
            state_mgr.add_chapter(
                PipelineState(),
                "tree-007/branch-root",
                branch_id=next(
                    branch["branch_id"]
                    for branch in plan["branches"]["branches"]
                    if branch["end_node_id"] == "candidate:fork"
                ),
            ).chapters[0].model_copy(update={"status": "completed"})
        ]
    )
    state_mgr.save(state)

    fake_engine = type("FakeEngine", (), {})()
    fake_engine.settings = type("Settings", (), {"project_root": tmp_path, "max_active_branch_runs": 2})()
    fake_engine.state_mgr = state_mgr

    updated = TreeEngine._activate_ready_branch_runs(fake_engine, state)
    downstream = next(item for item in updated.chapters if item.branch_id and item.status == "in_progress")

    assert downstream.tree_id == "tree-007"
    assert downstream.execution_path.startswith("tree-007/")


def test_branch_span_validation_requires_first_missing_contiguous_nodes(tmp_path: Path) -> None:
    graph = rebuild_knowledge_graph(
        tmp_path,
        _nodes(
            _candidate("candidate:root", ["根概念"]),
            _candidate("candidate:middle", ["中间概念"], prerequisites=["根概念"]),
            _candidate("candidate:leaf", ["叶子概念"], prerequisites=["中间概念"]),
        ),
        _ledger(_ledger_record("outputs/tree-001/branch-root/01.root.md", "candidate:root", ["根概念"])),
    )
    plan = rebuild_branch_plan(
        tmp_path,
        graph,
        _ledger(_ledger_record("outputs/tree-001/branch-root/01.root.md", "candidate:root", ["根概念"])),
    )
    branch = plan["branches"]["branches"][0]

    assert validate_branch_covered_node_ids(["candidate:middle", "candidate:leaf"], branch) == [
        "candidate:middle",
        "candidate:leaf",
    ]

    try:
        validate_branch_covered_node_ids(["candidate:leaf"], branch)
    except ValueError as exc:
        assert "first missing" in str(exc)
    else:
        raise AssertionError("span not starting at first missing should fail")

    try:
        validate_branch_covered_node_ids(["candidate:middle", "candidate:outside"], branch)
    except ValueError as exc:
        assert "outside active branch" in str(exc)
    else:
        raise AssertionError("outside node should fail")


def test_branch_coverage_uses_multi_node_ledger_records(tmp_path: Path) -> None:
    graph = rebuild_knowledge_graph(
        tmp_path,
        _nodes(
            _candidate("candidate:root", ["根概念"]),
            _candidate("candidate:middle", ["中间概念"], prerequisites=["根概念"]),
            _candidate("candidate:leaf", ["叶子概念"], prerequisites=["中间概念"]),
        ),
        _ledger(),
    )

    plan = rebuild_branch_plan(
        tmp_path,
        graph,
        _ledger(
            _ledger_record_many(
                "outputs/tree-001/branch-root/01.root-middle.md",
                ["candidate:root", "candidate:middle"],
                ["根概念", "中间概念"],
            )
        ),
    )
    branch = plan["branches"]["branches"][0]

    assert branch["coverage"]["covered_node_ids"] == ["candidate:root", "candidate:middle"]
    assert branch["coverage"]["missing_node_ids"] == ["candidate:leaf"]
    assert branch["status"] == "ready"


def test_branch_prior_scope_uses_dag_ancestor_closure_and_current_branch_prefix(tmp_path: Path) -> None:
    graph = rebuild_knowledge_graph(
        tmp_path,
        _nodes(
            _candidate("candidate:a-root", ["甲根"]),
            _candidate("candidate:a-mid", ["甲中"], prerequisites=["甲根"]),
            _candidate("candidate:b-root", ["乙根"]),
            _candidate("candidate:b-mid", ["乙中"], prerequisites=["乙根"]),
            _candidate("candidate:junction", ["汇合"], prerequisites=["甲中", "乙中"]),
            _candidate("candidate:after", ["后续"], prerequisites=["汇合"]),
            _candidate("candidate:sibling", ["旁支"]),
        ),
        _ledger(),
    )
    ledger = _ledger(
        _ledger_record("outputs/tree-001/branch-a/01.a-root.md", "candidate:a-root", ["甲根"]),
        _ledger_record("outputs/tree-001/branch-a/02.a-mid.md", "candidate:a-mid", ["甲中"]),
        _ledger_record("outputs/tree-002/branch-b/01.b-root.md", "candidate:b-root", ["乙根"]),
        _ledger_record("outputs/tree-002/branch-b/02.b-mid.md", "candidate:b-mid", ["乙中"]),
        _ledger_record("outputs/tree-003/branch-j/01.junction.md", "candidate:junction", ["汇合"]),
        _ledger_record("outputs/tree-999/branch-sibling/01.sibling.md", "candidate:sibling", ["旁支"]),
    )
    plan = rebuild_branch_plan(tmp_path, graph, ledger)
    branch = next(
        item for item in plan["branches"]["branches"] if item["coverage_node_ids"] == ["candidate:after"]
    )
    run = BranchRunRecord(
        branch_id=branch["branch_id"],
        run_id="run:j-after",
        chapter_name="tree-004/branch-after",
        coverage_snapshot=CoverageSnapshot(
            finished_output_ids=[
                "finished:outputs/tree-001/branch-a/01.a-root.md",
                "finished:outputs/tree-001/branch-a/02.a-mid.md",
                "finished:outputs/tree-002/branch-b/01.b-root.md",
                "finished:outputs/tree-002/branch-b/02.b-mid.md",
                "finished:outputs/tree-003/branch-j/01.junction.md",
                "finished:outputs/tree-999/branch-sibling/01.sibling.md",
            ]
        ),
    )

    scope = build_branch_prior_scope(
        run,
        plan["dag"],
        plan["branches"],
        ledger,
        covered_node_ids=["candidate:after"],
    )

    assert scope.allowed_paths == {
        "outputs/tree-001/branch-a/01.a-root.md",
        "outputs/tree-001/branch-a/02.a-mid.md",
        "outputs/tree-002/branch-b/01.b-root.md",
        "outputs/tree-002/branch-b/02.b-mid.md",
        "outputs/tree-003/branch-j/01.junction.md",
    }
    assert "outputs/tree-999/branch-sibling/01.sibling.md" not in scope.allowed_paths


def test_branch_prior_scope_allows_current_branch_files_before_span_without_snapshot(tmp_path: Path) -> None:
    graph = rebuild_knowledge_graph(
        tmp_path,
        _nodes(
            _candidate("candidate:root", ["根概念"]),
            _candidate("candidate:middle", ["中间概念"], prerequisites=["根概念"]),
            _candidate("candidate:leaf", ["叶子概念"], prerequisites=["中间概念"]),
        ),
        _ledger(),
    )
    ledger = _ledger(
        _ledger_record("outputs/tree-001/branch-main/01.root.md", "candidate:root", ["根概念"]),
        _ledger_record("outputs/tree-001/branch-main/02.middle.md", "candidate:middle", ["中间概念"]),
        _ledger_record("outputs/tree-001/branch-main/03.leaf.md", "candidate:leaf", ["叶子概念"]),
    )
    plan = rebuild_branch_plan(tmp_path, graph, ledger, running_branch_ids=set())
    branch = plan["branches"]["branches"][0]
    run = BranchRunRecord(
        branch_id=branch["branch_id"],
        run_id="run:main",
        chapter_name="tree-001/branch-main",
        coverage_snapshot=CoverageSnapshot(finished_output_ids=[]),
    )

    scope = build_branch_prior_scope(
        run,
        plan["dag"],
        plan["branches"],
        ledger,
        covered_node_ids=["candidate:leaf"],
    )

    assert scope.allowed_paths == {
        "outputs/tree-001/branch-main/01.root.md",
        "outputs/tree-001/branch-main/02.middle.md",
    }
    assert "outputs/tree-001/branch-main/03.leaf.md" not in scope.allowed_paths
