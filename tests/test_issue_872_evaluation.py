from __future__ import annotations

import asyncio

from ai_multi_agent_platform.coding_batches import (
    CheckState,
    CodingBatchCoordinator,
    CodingBatchRepairCoordinator,
    CodingWorkItem,
    CombinedValidationEvidence,
    InMemoryCodingBatchStore,
    IntegrationState,
    RequiredCheck,
    VerificationEvidence,
    WorkstreamResult,
    WorkstreamState,
)
from ai_multi_agent_platform.domain import OwnerRef, Plan, Step, new_id
from ai_multi_agent_platform.evaluation import (
    CodingBatchEvaluationCaseExecutor,
    ConfigurationSnapshot,
    DeterministicAssertionEvaluator,
    EvaluationOutcome,
    EvaluationRunner,
    EvaluationRunSummary,
    InMemoryEvaluationRepository,
    MetricThresholdEvaluator,
    canonical_coding_batch_quality_suite,
)

BASE = "a" * 40
REV_A = "1" * 40
REV_B = "2" * 40
INTEGRATED = "3" * 40
REPAIRED = "4" * 40


def _item(
    work_item_id: str,
    *,
    task_id: str,
    plan_id: str,
    path: str,
    scope: str,
) -> CodingWorkItem:
    return CodingWorkItem(
        work_item_id=work_item_id,
        task_id=task_id,
        plan_id=plan_id,
        step_id=new_id("step"),
        affected_paths=(path,),
        semantic_scopes=(scope,),
    )


def _produce_and_accept(
    coordinator: CodingBatchCoordinator,
    batch_id: str,
    workstream_id: str,
    *,
    revision: str,
    path: str,
) -> None:
    coordinator.materialize_workstream(
        batch_id,
        workstream_id,
        workspace_id=new_id("workspace"),
        snapshot_id=new_id("snapshot"),
        agent_revision=f"agent-{workstream_id}@1",
        agent_run_id=f"agent-run-{workstream_id}",
    )
    coordinator.start_workstream(batch_id, workstream_id)
    coordinator.record_result(
        batch_id,
        workstream_id,
        WorkstreamResult(
            output_revision=revision,
            changed_paths=(path,),
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


def _validated_independent_batch() -> tuple[
    CodingBatchCoordinator,
    InMemoryCodingBatchStore,
    str,
    str,
]:
    store = InMemoryCodingBatchStore()
    coordinator = CodingBatchCoordinator(store)
    task_id = new_id("task")
    plan_id = new_id("plan")
    batch = coordinator.create_batch(
        request_key="issue-872-evaluation-independent",
        repository_id="repo-platform",
        target_ref="main",
        base_revision=BASE,
        work_items=(
            _item(
                "A",
                task_id=task_id,
                plan_id=plan_id,
                path="src/a.py",
                scope="module:a",
            ),
            _item(
                "B",
                task_id=task_id,
                plan_id=plan_id,
                path="frontend/b.ts",
                scope="module:b",
            ),
        ),
    )
    _produce_and_accept(
        coordinator,
        batch.batch_id,
        "A",
        revision=REV_A,
        path="src/a.py",
    )
    _produce_and_accept(
        coordinator,
        batch.batch_id,
        "B",
        revision=REV_B,
        path="frontend/b.ts",
    )
    candidate = coordinator.build_integration_candidate(
        batch.batch_id,
        current_target_revision=BASE,
    )
    coordinator.record_integrated_revision(
        batch.batch_id,
        candidate.integration_id,
        integrated_revision=INTEGRATED,
    )
    coordinator.record_combined_validation(
        batch.batch_id,
        candidate.integration_id,
        CombinedValidationEvidence(
            subject_revision=INTEGRATED,
            verification_id="verification-combined-evaluation",
            tests_passed=True,
            required_checks=(
                RequiredCheck("unit", INTEGRATED, CheckState.PASS),
                RequiredCheck("typecheck", INTEGRATED, CheckState.PASS),
            ),
        ),
    )
    return coordinator, store, batch.batch_id, candidate.integration_id


def _run_quality(
    coordinator: CodingBatchCoordinator,
    batch_id: str,
    integration_id: str,
    *,
    expect_repair: bool = False,
) -> EvaluationRunSummary:
    async def scenario() -> EvaluationRunSummary:
        runner = EvaluationRunner(
            repository=InMemoryEvaluationRepository(),
            executor=CodingBatchEvaluationCaseExecutor(coordinator),
            evaluators=(
                DeterministicAssertionEvaluator(),
                MetricThresholdEvaluator(),
            ),
        )
        return await runner.run_suite(
            suite=canonical_coding_batch_quality_suite(
                batch_id,
                integration_id,
                expect_repair=expect_repair,
            ),
            snapshot=ConfigurationSnapshot(platform_version="0.0.1"),
        )

    return asyncio.run(scenario())


def test_validated_parallel_batch_passes_deterministic_issue_19_quality_suite() -> None:
    coordinator, _store, batch_id, integration_id = _validated_independent_batch()

    summary = _run_quality(coordinator, batch_id, integration_id)

    assert summary.run.status.value == "completed"
    assert len(summary.results) == 2
    assert {result.outcome for result in summary.results} == {EvaluationOutcome.PASSED}
    metric_result = next(
        result
        for result in summary.results
        if result.evaluator.evaluator_id == "reference.metric-threshold"
    )
    metrics = {metric.metric_name: metric for metric in metric_result.metrics}
    assert metrics["workstream_acceptance_rate"].value == 1.0
    assert metrics["workstream_verification_rate"].value == 1.0
    assert metrics["workspace_isolation_rate"].value == 1.0
    assert metrics["agent_run_provenance_rate"].value == 1.0
    assert metrics["combined_validation_rate"].value == 1.0
    assert metrics["stale_base_rate"].value == 0.0
    assert metrics["unresolved_conflict_rate"].value == 0.0


def test_stale_target_is_reported_as_issue_19_regression_evidence() -> None:
    coordinator, store, batch_id, integration_id = _validated_independent_batch()
    batch = coordinator.get(batch_id)
    candidate = batch.integration_candidate(integration_id)
    repairs = CodingBatchRepairCoordinator(store)
    moved = repairs.reconcile_target_revision(
        batch_id,
        integration_id,
        current_target_revision="f" * 40,
    )
    assert moved.state is IntegrationState.BLOCKED
    assert moved.stale_base is True

    summary = _run_quality(coordinator, batch_id, integration_id)

    assert any(result.outcome is EvaluationOutcome.FAILED for result in summary.results)
    metric_result = next(
        result
        for result in summary.results
        if result.evaluator.evaluator_id == "reference.metric-threshold"
    )
    metrics = {metric.metric_name: metric for metric in metric_result.metrics}
    assert metrics["stale_base_rate"].value == 1.0
    assert metrics["stale_base_rate"].passed is False
    assert metrics["combined_validation_rate"].value == 0.0
    assert metrics["combined_validation_rate"].passed is False
    assert coordinator.get(batch_id).integration_candidate(integration_id).validation is None
    assert candidate.validation is not None


def test_verified_repair_passes_issue_19_repair_quality_fixture() -> None:
    owner = OwnerRef(type="user", id="issue-872-evaluation-repair")
    task_id = new_id("task")
    plan_id = new_id("plan")
    store = InMemoryCodingBatchStore()
    coordinator = CodingBatchCoordinator(store)
    batch = coordinator.create_batch(
        request_key="issue-872-evaluation-repair",
        repository_id="repo-platform",
        target_ref="main",
        base_revision=BASE,
        work_items=(
            _item(
                "A",
                task_id=task_id,
                plan_id=plan_id,
                path="src/a.py",
                scope="shared-contract",
            ),
            _item(
                "B",
                task_id=task_id,
                plan_id=plan_id,
                path="src/b.py",
                scope="shared-contract",
            ),
        ),
    )
    _produce_and_accept(
        coordinator,
        batch.batch_id,
        "A",
        revision=REV_A,
        path="src/a.py",
    )
    assert coordinator.get(batch.batch_id).workstream("B").state is WorkstreamState.READY
    _produce_and_accept(
        coordinator,
        batch.batch_id,
        "B",
        revision=REV_B,
        path="src/b.py",
    )
    candidate = coordinator.build_integration_candidate(
        batch.batch_id,
        current_target_revision=BASE,
    )
    assert candidate.state is IntegrationState.BLOCKED
    assert candidate.conflicts

    repair_plan = Plan(task_id=task_id, owner_ref=owner, active=True)
    repair_step = Step(
        plan_id=repair_plan.id,
        title="repair semantic integration conflict",
        owner_ref=owner,
    )
    repairs = CodingBatchRepairCoordinator(store, max_attempts=2)
    attempt = repairs.bind_repair_step(
        batch.batch_id,
        candidate.integration_id,
        repair_plan=repair_plan,
        repair_step=repair_step,
        target_revision=BASE,
    )
    repaired = repairs.record_repair_result(
        batch.batch_id,
        candidate.integration_id,
        attempt.repair_id,
        output_revision=REPAIRED,
        verification=VerificationEvidence(
            verification_id="verification-repair-evaluation",
            subject_revision=REPAIRED,
            passed=True,
        ),
    )
    assert repaired.state is IntegrationState.VALIDATING
    coordinator.record_combined_validation(
        batch.batch_id,
        candidate.integration_id,
        CombinedValidationEvidence(
            subject_revision=REPAIRED,
            verification_id="verification-repair-combined-evaluation",
            tests_passed=True,
            required_checks=(RequiredCheck("unit", REPAIRED, CheckState.PASS),),
        ),
    )

    summary = _run_quality(
        coordinator,
        batch.batch_id,
        candidate.integration_id,
        expect_repair=True,
    )

    assert {result.outcome for result in summary.results} == {EvaluationOutcome.PASSED}
    metric_result = next(
        result
        for result in summary.results
        if result.evaluator.evaluator_id == "reference.metric-threshold"
    )
    metrics = {metric.metric_name: metric for metric in metric_result.metrics}
    assert metrics["repair_verification_rate"].value == 1.0
    assert metrics["repair_expectation_rate"].value == 1.0
    assert metrics["unresolved_conflict_rate"].value == 0.0
