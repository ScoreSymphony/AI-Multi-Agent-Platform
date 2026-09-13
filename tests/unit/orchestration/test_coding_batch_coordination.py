from __future__ import annotations

import pytest

from ai_multi_agent_platform.coding_batches import (
    CodingBatchCoordinator,
    CodingWorkItem,
    VerificationEvidence,
    WorkstreamResult,
    WorkstreamState,
)

BASE = "a" * 40


def _item(
    name: str,
    path: str,
    *,
    dependencies: tuple[str, ...] = (),
) -> CodingWorkItem:
    return CodingWorkItem(
        work_item_id=name,
        task_id=f"task-{name}",
        plan_id="plan-batch",
        step_id=f"step-{name}",
        dependencies=dependencies,
        affected_paths=(path,),
    )


def _coordinator(*items: CodingWorkItem) -> tuple[CodingBatchCoordinator, str]:
    coordinator = CodingBatchCoordinator()
    batch = coordinator.create_batch(
        request_key="request-872-regression",
        repository_id="repo-platform",
        target_ref="main",
        base_revision=BASE,
        work_items=items,
    )
    return coordinator, batch.batch_id


def _accept(
    coordinator: CodingBatchCoordinator,
    batch_id: str,
    workstream_id: str,
    revision: str,
    changed_path: str,
) -> None:
    coordinator.materialize_workstream(
        batch_id,
        workstream_id,
        workspace_id=f"workspace-{workstream_id}",
        snapshot_id=f"snapshot-{workstream_id}",
        agent_revision="agent-revision-1",
        agent_run_id=f"agent-run-{workstream_id}",
    )
    coordinator.start_workstream(batch_id, workstream_id)
    coordinator.record_result(
        batch_id,
        workstream_id,
        WorkstreamResult(
            output_revision=revision,
            changed_paths=(changed_path,),
            diff_digest=f"digest-{workstream_id}",
        ),
    )
    coordinator.record_verification(
        batch_id,
        workstream_id,
        VerificationEvidence(
            verification_id=f"verification-{workstream_id}",
            subject_revision=revision,
            passed=True,
        ),
    )
    coordinator.accept_workstream(batch_id, workstream_id)


def test_dependency_cycle_is_rejected_before_any_workstream_is_created() -> None:
    coordinator = CodingBatchCoordinator()

    with pytest.raises(ValueError, match="dependencies must be acyclic"):
        coordinator.create_batch(
            request_key="request-cycle",
            repository_id="repo-platform",
            target_ref="main",
            base_revision=BASE,
            work_items=(
                _item("a", "src/a.py", dependencies=("b",)),
                _item("b", "src/b.py", dependencies=("a",)),
            ),
        )


def test_integration_order_is_dependency_topological_not_input_order() -> None:
    coordinator, batch_id = _coordinator(
        _item("b", "src/b.py", dependencies=("a",)),
        _item("a", "src/a.py"),
    )
    assert tuple(item.id for item in coordinator.ready_workstreams(batch_id)) == ("a",)

    _accept(coordinator, batch_id, "a", "1" * 40, "src/a.py")
    _accept(coordinator, batch_id, "b", "2" * 40, "src/b.py")

    candidate = coordinator.build_integration_candidate(
        batch_id,
        current_target_revision=BASE,
    )

    assert candidate.ordered_workstream_ids == ("a", "b")
    assert candidate.ordered_revisions == ("1" * 40, "2" * 40)


def test_failed_serialization_blocker_releases_independent_sibling() -> None:
    coordinator, batch_id = _coordinator(
        _item("a", "src/shared.py"),
        _item("b", "src/shared.py"),
    )
    assert tuple(item.id for item in coordinator.ready_workstreams(batch_id)) == ("a",)

    coordinator.fail_workstream(batch_id, "a", "implementation failed")

    assert coordinator.get(batch_id).workstream("b").state is WorkstreamState.READY


def test_failed_hard_dependency_does_not_release_dependent_work() -> None:
    coordinator, batch_id = _coordinator(
        _item("a", "src/a.py"),
        _item("b", "src/b.py", dependencies=("a",)),
    )

    coordinator.fail_workstream(batch_id, "a", "implementation failed")

    dependent = coordinator.get(batch_id).workstream("b")
    assert dependent.state is WorkstreamState.BLOCKED
    assert dependent.blocked_by == ("a",)


def test_failed_verification_releases_only_serialized_sibling() -> None:
    coordinator, batch_id = _coordinator(
        _item("a", "src/shared.py"),
        _item("b", "src/shared.py"),
    )
    coordinator.materialize_workstream(
        batch_id,
        "a",
        workspace_id="workspace-a",
        snapshot_id="snapshot-a",
        agent_revision="agent-revision-1",
        agent_run_id="agent-run-a",
    )
    coordinator.start_workstream(batch_id, "a")
    coordinator.record_result(
        batch_id,
        "a",
        WorkstreamResult("1" * 40, ("src/shared.py",), "digest-a"),
    )

    failed = coordinator.record_verification(
        batch_id,
        "a",
        VerificationEvidence("verification-a", "1" * 40, False),
    )

    assert failed.state is WorkstreamState.FAILED
    assert coordinator.get(batch_id).workstream("b").state is WorkstreamState.READY


def test_unknown_workstream_cannot_enter_integration_candidate() -> None:
    coordinator, batch_id = _coordinator(_item("a", "src/a.py"))
    _accept(coordinator, batch_id, "a", "1" * 40, "src/a.py")

    with pytest.raises(ValueError, match="unknown workstreams"):
        coordinator.build_integration_candidate(
            batch_id,
            current_target_revision=BASE,
            workstream_ids=("a", "missing"),
        )
