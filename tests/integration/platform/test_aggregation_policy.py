from __future__ import annotations

import pytest

from ai_multi_agent_platform.coding_batches import (
    BatchAggregationPolicy,
    CodingBatchCoordinator,
    CodingWorkItem,
    VerificationEvidence,
    WorkstreamResult,
)

BASE = "a" * 40


def _item(name: str, *, dependencies: tuple[str, ...] = ()) -> CodingWorkItem:
    return CodingWorkItem(
        work_item_id=name,
        task_id=f"task-{name}",
        plan_id="plan-aggregation",
        step_id=f"step-{name}",
        dependencies=dependencies,
        affected_paths=(f"src/{name}.py",),
    )


def _batch(
    policy: BatchAggregationPolicy,
    *items: CodingWorkItem,
) -> tuple[CodingBatchCoordinator, str]:
    coordinator = CodingBatchCoordinator()
    batch = coordinator.create_batch(
        request_key=f"request-aggregation-{policy.value}",
        repository_id="repo-platform",
        target_ref="main",
        base_revision=BASE,
        work_items=items,
        aggregation_policy=policy,
    )
    return coordinator, batch.batch_id


def _accept(
    coordinator: CodingBatchCoordinator,
    batch_id: str,
    workstream_id: str,
    revision: str,
) -> None:
    coordinator.materialize_workstream(
        batch_id,
        workstream_id,
        workspace_id=f"workspace-{workstream_id}",
        snapshot_id=f"snapshot-{workstream_id}",
        agent_revision="developer@1",
        agent_run_id=f"agent-run-{workstream_id}",
    )
    coordinator.start_workstream(batch_id, workstream_id)
    coordinator.record_result(
        batch_id,
        workstream_id,
        WorkstreamResult(
            output_revision=revision,
            changed_paths=(f"src/{workstream_id}.py",),
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


def test_all_required_rejects_partial_accepted_subset() -> None:
    coordinator, batch_id = _batch(
        BatchAggregationPolicy.ALL_REQUIRED,
        _item("a"),
        _item("b"),
    )
    _accept(coordinator, batch_id, "a", "1" * 40)

    with pytest.raises(
        ValueError,
        match="all_required aggregation cannot integrate a partial batch",
    ):
        coordinator.build_integration_candidate(
            batch_id,
            current_target_revision=BASE,
        )


def test_best_effort_allows_verified_independent_subset_after_sibling_failure() -> None:
    coordinator, batch_id = _batch(
        BatchAggregationPolicy.BEST_EFFORT,
        _item("a"),
        _item("b"),
    )
    _accept(coordinator, batch_id, "a", "1" * 40)
    coordinator.fail_workstream(batch_id, "b", "implementation failed")

    candidate = coordinator.build_integration_candidate(
        batch_id,
        current_target_revision=BASE,
    )

    assert candidate.ordered_workstream_ids == ("a",)
    assert candidate.ordered_revisions == ("1" * 40,)


def test_dependency_closed_rejects_manual_selection_without_accepted_predecessor() -> None:
    coordinator, batch_id = _batch(
        BatchAggregationPolicy.DEPENDENCY_CLOSED,
        _item("a"),
        _item("b", dependencies=("a",)),
    )
    _accept(coordinator, batch_id, "a", "1" * 40)
    _accept(coordinator, batch_id, "b", "2" * 40)

    with pytest.raises(
        ValueError,
        match="dependency_closed aggregation requires selected dependencies",
    ):
        coordinator.build_integration_candidate(
            batch_id,
            current_target_revision=BASE,
            workstream_ids=("b",),
        )

    candidate = coordinator.build_integration_candidate(
        batch_id,
        current_target_revision=BASE,
        workstream_ids=("a", "b"),
    )
    assert candidate.ordered_workstream_ids == ("a", "b")


def test_manual_selection_keeps_explicit_accepted_subset() -> None:
    coordinator, batch_id = _batch(
        BatchAggregationPolicy.MANUAL_SELECTION,
        _item("a"),
        _item("b"),
    )
    _accept(coordinator, batch_id, "a", "1" * 40)
    _accept(coordinator, batch_id, "b", "2" * 40)

    candidate = coordinator.build_integration_candidate(
        batch_id,
        current_target_revision=BASE,
        workstream_ids=("b",),
    )

    assert candidate.ordered_workstream_ids == ("b",)
    assert candidate.ordered_revisions == ("2" * 40,)
