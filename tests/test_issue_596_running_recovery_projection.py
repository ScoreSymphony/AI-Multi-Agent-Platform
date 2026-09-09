from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.capabilities import (
    CapabilityInvoker,
    CapabilityRegistry,
    CompensationDescriptor,
    ReversibilityClassification,
)
from ai_multi_agent_platform.compensation import (
    CompensationControlPlaneProjection,
    CompensationCoordinator,
    CompensationGroup,
    CompensationReconciliation,
    CompensationRequest,
    CompensationResult,
    CompensationStatus,
    CompensationTrigger,
    CompletedSideEffect,
    InMemoryCompensationRepository,
    new_compensation_action_id,
    new_compensation_group_id,
    new_compensation_id,
)
from ai_multi_agent_platform.contracts import ErrorCode
from ai_multi_agent_platform.domain import new_id


class _WouldSucceedReconciler:
    def __init__(self) -> None:
        self.calls = 0

    async def reconcile(
        self,
        request: CompensationRequest,
        action: CompletedSideEffect,
        result: CompensationResult,
    ) -> CompensationReconciliation:
        del request, action, result
        self.calls += 1
        return CompensationReconciliation(
            outcome_known=True,
            succeeded=True,
            result_ref="undo:record-1",
        )


def _group() -> CompensationGroup:
    return CompensationGroup(
        group_id=new_compensation_group_id(),
        task_id=new_id("task"),
        plan_id=new_id("plan"),
        plan_revision=1,
        project_id=new_id("project"),
    )


def _action(group: CompensationGroup) -> CompletedSideEffect:
    return CompletedSideEffect(
        action_id=new_compensation_action_id(),
        group_id=group.group_id,
        task_id=group.task_id,
        plan_id=group.plan_id,
        plan_revision=group.plan_revision,
        project_id=group.project_id,
        step_id=new_id("step"),
        run_id=new_id("run"),
        tool_invocation_id=new_id("tool_invocation"),
        agent_id=new_id("agent"),
        capability_id="external.reference.create",
        capability_version="1.0",
        reversibility=ReversibilityClassification.REVERSIBLE,
        compensation=CompensationDescriptor(capability_id="external.reference.undo"),
        original_arguments={"resource": "record-1"},
        compensation_arguments={"resource": "record-1"},
        execution_order=1,
        original_result_ref="create:record-1",
        external_resource_ref="record-1",
    )


def _request(
    action: CompletedSideEffect,
    *,
    key: str,
    trigger: CompensationTrigger,
    requested_at: datetime,
) -> CompensationRequest:
    descriptor = action.compensation
    assert descriptor is not None
    return CompensationRequest(
        compensation_id=new_compensation_id(),
        idempotency_key=key,
        group_id=action.group_id,
        action_id=action.action_id,
        original_task_id=action.task_id,
        original_plan_id=action.plan_id,
        original_plan_revision=action.plan_revision,
        original_project_id=action.project_id,
        original_step_id=action.step_id,
        original_run_id=action.run_id,
        original_tool_invocation_id=action.tool_invocation_id,
        original_result_ref=action.original_result_ref,
        external_resource_ref=action.external_resource_ref,
        requested_capability_id=descriptor.capability_id,
        requested_capability_version=descriptor.version,
        trigger=trigger,
        reason="persisted recovery intent",
        actor_ref="service:coordination",
        correlation_id=f"corr-{trigger.value}",
        requested_at=requested_at,
    )


def test_running_mixed_identity_recovery_bypasses_reconciler_and_surfaces_conflict() -> None:
    async def scenario() -> None:
        repository = InMemoryCompensationRepository()
        reconciler = _WouldSucceedReconciler()
        coordinator = CompensationCoordinator(
            repository,
            CapabilityInvoker(CapabilityRegistry()),
            reconciler=reconciler,
        )
        group = coordinator.register_group(_group())
        action = coordinator.record_completed_side_effect(_action(group))
        canonical_key = (
            f"compensation:{action.group_id}:{action.action_id}:plan-revision-{action.plan_revision}"
        )
        started = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)

        legacy = repository.create_request(
            _request(
                action,
                key=f"{canonical_key}:{CompensationTrigger.DOWNSTREAM_FAILURE.value}",
                trigger=CompensationTrigger.DOWNSTREAM_FAILURE,
                requested_at=started,
            )
        )
        repository.save_result(
            CompensationResult(
                compensation_id=legacy.compensation_id,
                status=CompensationStatus.RUNNING,
                started_at=started,
            )
        )
        canonical = repository.create_request(
            _request(
                action,
                key=canonical_key,
                trigger=CompensationTrigger.MANUAL,
                requested_at=started + timedelta(seconds=1),
            )
        )
        repository.save_result(
            CompensationResult(
                compensation_id=canonical.compensation_id,
                status=CompensationStatus.SUCCEEDED,
                result_ref="undo:record-1",
                completed_at=started + timedelta(seconds=2),
            )
        )

        async def context_factory(request, completed_action):
            del request, completed_action
            raise AssertionError("RUNNING mixed-identity recovery must not request a new context")

        projection = await coordinator.recover_group(
            group.group_id,
            context_factory=context_factory,
        )

        legacy_result = repository.get_result(legacy.compensation_id)
        canonical_result = repository.get_result(canonical.compensation_id)
        assert reconciler.calls == 0
        assert legacy_result is not None
        assert legacy_result.status is CompensationStatus.RECONCILIATION_REQUIRED
        assert legacy_result.manual_intervention_required
        assert "manual reconciliation" in (legacy_result.error_message or "")
        assert canonical_result is not None
        assert canonical_result.status is CompensationStatus.SUCCEEDED

        assert projection.manual_intervention_required
        assert len(projection.actions) == 1
        projected = projection.actions[0]
        assert projected.request is not None
        assert projected.request.compensation_id == legacy.compensation_id
        assert projected.result is not None
        assert projected.result.status is CompensationStatus.RECONCILIATION_REQUIRED

        operator_view = CompensationControlPlaneProjection(repository).get_group(group.group_id)
        assert operator_view.manual_intervention_required
        assert len(operator_view.actions) == 1
        action_view = operator_view.actions[0]
        assert action_view.compensation_id == legacy.compensation_id
        assert action_view.status is CompensationStatus.RECONCILIATION_REQUIRED
        assert action_view.manual_intervention_required

    asyncio.run(scenario())


def test_terminal_mixed_identity_history_is_projected_without_mutating_results() -> None:
    async def scenario() -> None:
        repository = InMemoryCompensationRepository()
        reconciler = _WouldSucceedReconciler()
        coordinator = CompensationCoordinator(
            repository,
            CapabilityInvoker(CapabilityRegistry()),
            reconciler=reconciler,
        )
        group = coordinator.register_group(_group())
        action = coordinator.record_completed_side_effect(_action(group))
        canonical_key = (
            f"compensation:{action.group_id}:{action.action_id}:plan-revision-{action.plan_revision}"
        )
        started = datetime(2026, 9, 9, 13, 0, tzinfo=UTC)

        legacy = repository.create_request(
            _request(
                action,
                key=f"{canonical_key}:{CompensationTrigger.DOWNSTREAM_FAILURE.value}",
                trigger=CompensationTrigger.DOWNSTREAM_FAILURE,
                requested_at=started,
            )
        )
        repository.save_result(
            CompensationResult(
                compensation_id=legacy.compensation_id,
                status=CompensationStatus.FAILED,
                error_code=ErrorCode.PERMANENT_FAILURE.value,
                error_message="legacy provider outcome failed",
                manual_intervention_required=True,
                completed_at=started + timedelta(seconds=1),
            )
        )
        canonical = repository.create_request(
            _request(
                action,
                key=canonical_key,
                trigger=CompensationTrigger.MANUAL,
                requested_at=started + timedelta(seconds=2),
            )
        )
        repository.save_result(
            CompensationResult(
                compensation_id=canonical.compensation_id,
                status=CompensationStatus.SUCCEEDED,
                result_ref="undo:record-1",
                completed_at=started + timedelta(seconds=3),
            )
        )

        before = coordinator.projection(group.group_id)
        assert before.manual_intervention_required
        projected_before = before.actions[0]
        assert projected_before.request is not None
        assert projected_before.request.compensation_id == canonical.compensation_id
        assert projected_before.result is not None
        assert projected_before.result.status is CompensationStatus.RECONCILIATION_REQUIRED
        assert projected_before.result.error_code == ErrorCode.CONFLICT.value
        assert projected_before.result.manual_intervention_required

        operator_before = CompensationControlPlaneProjection(repository).get_group(group.group_id)
        assert operator_before.manual_intervention_required
        operator_action = operator_before.actions[0]
        assert operator_action.compensation_id == canonical.compensation_id
        assert operator_action.status is CompensationStatus.RECONCILIATION_REQUIRED
        assert operator_action.error_code == ErrorCode.CONFLICT.value
        assert operator_action.manual_intervention_required

        async def context_factory(request, completed_action):
            del request, completed_action
            raise AssertionError("terminal mixed-identity recovery must not execute or request context")

        recovered = await coordinator.recover_group(
            group.group_id,
            context_factory=context_factory,
        )
        assert reconciler.calls == 0
        assert recovered.manual_intervention_required
        assert recovered.actions[0].result is not None
        assert recovered.actions[0].result.status is CompensationStatus.RECONCILIATION_REQUIRED

        stored_legacy = repository.get_result(legacy.compensation_id)
        stored_canonical = repository.get_result(canonical.compensation_id)
        assert stored_legacy is not None
        assert stored_legacy.status is CompensationStatus.FAILED
        assert stored_legacy.error_code == ErrorCode.PERMANENT_FAILURE.value
        assert stored_canonical is not None
        assert stored_canonical.status is CompensationStatus.SUCCEEDED
        assert stored_canonical.result_ref == "undo:record-1"

    asyncio.run(scenario())
