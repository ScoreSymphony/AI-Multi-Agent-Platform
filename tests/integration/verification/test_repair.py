from __future__ import annotations

from pathlib import Path

import pytest

from ai_multi_agent_platform.coding_batches import (
    CheckState,
    CodingBatchCoordinator,
    CodingBatchRepairCoordinator,
    CodingWorkItem,
    CombinedValidationEvidence,
    InMemoryCodingBatchStore,
    IntegrationState,
    RepairAttemptState,
    RequiredCheck,
    SqliteCodingBatchStore,
    VerificationEvidence,
    WorkstreamResult,
)
from ai_multi_agent_platform.domain import OwnerRef, Plan, Step, Task

BASE = "a" * 40
TARGET_2 = "b" * 40
TARGET_3 = "c" * 40
OUTPUT_A = "1" * 40
OUTPUT_B = "2" * 40
REPAIRED = "3" * 40


def _item(name: str, path: str, *, semantic_scope: str | None = None) -> CodingWorkItem:
    return CodingWorkItem(
        work_item_id=name,
        task_id=f"task-{name}",
        plan_id=f"plan-{name}",
        step_id=f"step-{name}",
        affected_paths=(path,),
        semantic_scopes=() if semantic_scope is None else (semantic_scope,),
    )


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
        WorkstreamResult(revision, (changed_path,), f"digest-{workstream_id}"),
    )
    coordinator.record_verification(
        batch_id,
        workstream_id,
        VerificationEvidence(f"verification-{workstream_id}", revision, True),
    )
    coordinator.accept_workstream(batch_id, workstream_id)


def _canonical_repair_step(title: str = "repair integration") -> tuple[Plan, Step]:
    owner = OwnerRef(type="user", id="issue-872-test")
    task = Task(title=title, owner_ref=owner)
    plan = Plan(task_id=task.id, owner_ref=owner)
    return plan, Step(plan_id=plan.id, title=title, owner_ref=owner)


def _conflict_candidate(
    store: InMemoryCodingBatchStore | SqliteCodingBatchStore,
) -> tuple[CodingBatchCoordinator, str, str]:
    coordinator = CodingBatchCoordinator(store)
    batch = coordinator.create_batch(
        request_key="request-repair-conflict",
        repository_id="repo-platform",
        target_ref="main",
        base_revision=BASE,
        work_items=(
            _item("a", "src/a.py", semantic_scope="runtime-contract"),
            _item("b", "tests/b.py", semantic_scope="runtime-contract"),
        ),
    )
    _accept(coordinator, batch.batch_id, "a", OUTPUT_A, "src/a.py")
    _accept(coordinator, batch.batch_id, "b", OUTPUT_B, "tests/b.py")
    candidate = coordinator.build_integration_candidate(
        batch.batch_id,
        current_target_revision=BASE,
    )
    assert candidate.state is IntegrationState.BLOCKED
    return coordinator, batch.batch_id, candidate.integration_id


def test_conflict_repair_binds_canonical_step_and_requires_fresh_combined_validation() -> None:
    store = InMemoryCodingBatchStore()
    coordinator, batch_id, integration_id = _conflict_candidate(store)
    repairs = CodingBatchRepairCoordinator(store)
    plan, step = _canonical_repair_step()

    repair = repairs.bind_repair_step(
        batch_id,
        integration_id,
        repair_plan=plan,
        repair_step=step,
        target_revision=BASE,
    )
    assert repair.state is RepairAttemptState.BOUND
    assert repair.step_id == step.id
    assert repair.source_conflicts
    assert (
        repairs.bind_repair_step(
            batch_id,
            integration_id,
            repair_plan=plan,
            repair_step=step,
            target_revision=BASE,
        )
        == repair
    )

    repaired = repairs.record_repair_result(
        batch_id,
        integration_id,
        repair.repair_id,
        output_revision=REPAIRED,
        verification=VerificationEvidence("verification-repair", REPAIRED, True),
    )
    assert repaired.state is IntegrationState.VALIDATING
    assert repaired.integrated_revision == REPAIRED
    assert repaired.validation is None
    assert repaired.repair_attempts[-1].state is RepairAttemptState.VERIFIED

    validated = coordinator.record_combined_validation(
        batch_id,
        integration_id,
        CombinedValidationEvidence(
            subject_revision=REPAIRED,
            verification_id="verification-combined-repair",
            tests_passed=True,
            required_checks=(RequiredCheck("test", REPAIRED, CheckState.PASS),),
        ),
    )
    assert validated.state is IntegrationState.VALIDATED


def test_stale_target_repair_is_invalidated_again_when_target_moves() -> None:
    store = InMemoryCodingBatchStore()
    coordinator = CodingBatchCoordinator(store)
    batch = coordinator.create_batch(
        request_key="request-stale-repair",
        repository_id="repo-platform",
        target_ref="main",
        base_revision=BASE,
        work_items=(_item("a", "src/a.py"),),
    )
    _accept(coordinator, batch.batch_id, "a", OUTPUT_A, "src/a.py")
    candidate = coordinator.build_integration_candidate(
        batch.batch_id,
        current_target_revision=TARGET_2,
    )
    repairs = CodingBatchRepairCoordinator(store)
    plan, step = _canonical_repair_step("replay onto moved target")
    repair = repairs.bind_repair_step(
        batch.batch_id,
        candidate.integration_id,
        repair_plan=plan,
        repair_step=step,
        target_revision=TARGET_2,
    )
    repairs.record_repair_result(
        batch.batch_id,
        candidate.integration_id,
        repair.repair_id,
        output_revision=REPAIRED,
        verification=VerificationEvidence("verification-replay", REPAIRED, True),
    )
    coordinator.record_combined_validation(
        batch.batch_id,
        candidate.integration_id,
        CombinedValidationEvidence(
            subject_revision=REPAIRED,
            verification_id="verification-combined",
            tests_passed=True,
            required_checks=(RequiredCheck("test", REPAIRED, CheckState.PASS),),
        ),
    )

    invalidated = repairs.reconcile_target_revision(
        batch.batch_id,
        candidate.integration_id,
        current_target_revision=TARGET_3,
    )
    assert invalidated.state is IntegrationState.BLOCKED
    assert invalidated.stale_base is True
    assert invalidated.validation is None
    assert TARGET_2 in invalidated.blocker_reasons[0]
    assert TARGET_3 in invalidated.blocker_reasons[0]


def test_repair_attempt_budget_is_bounded() -> None:
    store = InMemoryCodingBatchStore()
    _, batch_id, integration_id = _conflict_candidate(store)
    repairs = CodingBatchRepairCoordinator(store, max_attempts=1)
    plan, step = _canonical_repair_step("first repair")
    repair = repairs.bind_repair_step(
        batch_id,
        integration_id,
        repair_plan=plan,
        repair_step=step,
        target_revision=BASE,
    )
    failed = repairs.fail_repair_attempt(
        batch_id,
        integration_id,
        repair.repair_id,
        reason="conflict remains unresolved",
    )
    assert failed.state is IntegrationState.BLOCKED
    assert failed.repair_attempts[-1].state is RepairAttemptState.FAILED

    second_plan, second_step = _canonical_repair_step("second repair")
    with pytest.raises(ValueError, match="attempt limit exhausted"):
        repairs.bind_repair_step(
            batch_id,
            integration_id,
            repair_plan=second_plan,
            repair_step=second_step,
            target_revision=BASE,
        )


def test_sqlite_restart_preserves_active_repair_and_idempotent_result(tmp_path: Path) -> None:
    path = tmp_path / "repair.sqlite3"
    store = SqliteCodingBatchStore(path)
    _, batch_id, integration_id = _conflict_candidate(store)
    repairs = CodingBatchRepairCoordinator(store)
    plan, step = _canonical_repair_step()
    repair = repairs.bind_repair_step(
        batch_id,
        integration_id,
        repair_plan=plan,
        repair_step=step,
        target_revision=BASE,
    )

    restarted_store = SqliteCodingBatchStore(path)
    recovered = restarted_store.get(batch_id)
    assert recovered is not None
    recovered_candidate = recovered.integration_candidate(integration_id)
    assert recovered_candidate.state is IntegrationState.REPAIRING
    assert recovered_candidate.repair_attempts[-1].repair_id == repair.repair_id
    assert recovered_candidate.repair_attempts[-1].source_conflicts

    restarted_repairs = CodingBatchRepairCoordinator(restarted_store)
    evidence = VerificationEvidence("verification-repair", REPAIRED, True)
    repaired = restarted_repairs.record_repair_result(
        batch_id,
        integration_id,
        repair.repair_id,
        output_revision=REPAIRED,
        verification=evidence,
    )
    replay = restarted_repairs.record_repair_result(
        batch_id,
        integration_id,
        repair.repair_id,
        output_revision=REPAIRED,
        verification=evidence,
    )
    assert replay == repaired
    assert replay.repair_attempts[-1].state is RepairAttemptState.VERIFIED
