from __future__ import annotations

from pathlib import Path

import pytest

from ai_multi_agent_platform.capabilities import (
    CapabilityInvoker,
    CapabilityRegistry,
    CompensationDescriptor,
    ReversibilityClassification,
)
from ai_multi_agent_platform.compensation import (
    CompensationCoordinator,
    CompensationGroup,
    CompensationRequest,
    CompensationTrigger,
    CompletedSideEffect,
    SQLiteCompensationRepository,
    new_compensation_action_id,
    new_compensation_group_id,
    new_compensation_id,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import new_id


def test_upgrade_with_legacy_and_canonical_requests_fails_closed(tmp_path: Path) -> None:
    repository = SQLiteCompensationRepository(tmp_path / "compensation.sqlite3")
    coordinator = CompensationCoordinator(
        repository,
        CapabilityInvoker(CapabilityRegistry()),
    )
    group = coordinator.register_group(
        CompensationGroup(
            group_id=new_compensation_group_id(),
            task_id=new_id("task"),
            plan_id=new_id("plan"),
            plan_revision=1,
            project_id=new_id("project"),
        )
    )
    descriptor = CompensationDescriptor(capability_id="external.reference.undo")
    action = coordinator.record_completed_side_effect(
        CompletedSideEffect(
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
            compensation=descriptor,
            original_arguments={"resource": "record-1"},
            compensation_arguments={"resource": "record-1"},
            execution_order=1,
            original_result_ref="create:record-1",
            external_resource_ref="record-1",
        )
    )
    canonical_key = (
        f"compensation:{action.group_id}:{action.action_id}:"
        f"plan-revision-{action.plan_revision}"
    )

    def request(key: str, trigger: CompensationTrigger) -> CompensationRequest:
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
        )

    repository.create_request(
        request(canonical_key, CompensationTrigger.MANUAL)
    )
    repository.create_request(
        request(
            f"{canonical_key}:{CompensationTrigger.DOWNSTREAM_FAILURE.value}",
            CompensationTrigger.DOWNSTREAM_FAILURE,
        )
    )

    with pytest.raises(ContractError) as captured:
        coordinator.request_compensation(
            action.action_id,
            trigger=CompensationTrigger.MANUAL,
            reason="recovery after mixed-version history",
            actor_ref="user:user-1",
            correlation_id="corr-mixed-history",
        )

    assert captured.value.code is ErrorCode.CONFLICT
    assert "manual reconciliation" in captured.value.message
    assert len(repository.list_requests(group.group_id)) == 2
