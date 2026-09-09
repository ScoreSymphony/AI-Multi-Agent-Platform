from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.capabilities import (
    CapabilityInvoker,
    CapabilityRegistration,
    CapabilityRegistry,
    CapabilitySpec,
    CompensationDescriptor,
    ReversibilityClassification,
    SideEffectClassification,
    bind_canonical_capability_invocation,
)
from ai_multi_agent_platform.capabilities.provider import CapabilityToolProvider
from ai_multi_agent_platform.compensation import (
    CompensationCoordinator,
    CompensationExecutionContext,
    CompensationGroup,
    CompensationRequest,
    CompensationResult,
    CompensationStatus,
    CompensationTrigger,
    CompletedSideEffect,
    InMemoryCompensationRepository,
    SQLiteCompensationRepository,
    new_compensation_action_id,
    new_compensation_group_id,
    new_compensation_id,
)
from ai_multi_agent_platform.contracts.types import (
    Capability,
    CapabilityKind,
    HealthStatus,
    OperationContext,
    ProviderDescriptor,
    ToolInvocation,
    ToolResult,
)
from ai_multi_agent_platform.domain import new_id


class _UndoProvider(CapabilityToolProvider):
    def __init__(self) -> None:
        self.calls: list[ToolInvocation] = []
        self.spec = CapabilitySpec(
            capability_id="external.reference.undo",
            name="Reference undo",
            input_schema={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"resource": {"type": "string"}},
                "required": ["resource"],
                "additionalProperties": False,
            },
            side_effects=SideEffectClassification.DESTRUCTIVE,
            required_approvals=(),
        )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="reference.undo",
            provider_type="test",
            capabilities=(
                Capability(
                    name=self.spec.capability_id,
                    kind=CapabilityKind.TOOL,
                    supported_operations=("invoke",),
                ),
            ),
            health=HealthStatus.HEALTHY,
            available=True,
        )

    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        return (
            CapabilityRegistration(
                capability=self.spec,
                provider_id="reference.undo",
                provider_tool_ref="reference.undo",
            ),
        )

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        self.calls.append(invocation)
        resource = str(invocation.arguments["resource"])
        return ToolResult(
            invocation_id=invocation.invocation_id,
            output={"removed": resource},
            result_ref=f"undo:{resource}",
            evidence_refs=(f"evidence:{resource}",),
        )


def _group() -> CompensationGroup:
    return CompensationGroup(
        group_id=new_compensation_group_id(),
        task_id=new_id("task"),
        plan_id=new_id("plan"),
        plan_revision=1,
        project_id=new_id("project"),
    )


def _action(
    group: CompensationGroup,
    *,
    completed_at: datetime | None = None,
    requires_approval: bool = False,
    window_seconds: float | None = None,
) -> CompletedSideEffect:
    descriptor = CompensationDescriptor(
        capability_id="external.reference.undo",
        requires_approval=requires_approval,
        window_seconds=window_seconds,
    )
    kwargs = {} if completed_at is None else {"completed_at": completed_at}
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
        compensation=descriptor,
        original_arguments={"resource": "record-1"},
        compensation_arguments={"resource": "record-1"},
        execution_order=1,
        original_result_ref="create:record-1",
        external_resource_ref="record-1",
        **kwargs,
    )


def _context(group: CompensationGroup, ordinal: int) -> CompensationExecutionContext:
    return CompensationExecutionContext(
        invocation_id=f"compensation-invocation-{ordinal}",
        operation=OperationContext(
            correlation_id=f"compensation-correlation-{ordinal}",
            causation_id=f"plan-failure-{ordinal}",
            owner_type="user",
            owner_id="user-1",
            project_id=group.project_id,
        ),
        task_id=new_id("task"),
        run_id=new_id("run"),
        agent_id=new_id("agent"),
    )


def _persisted_request(
    action: CompletedSideEffect,
    *,
    key: str,
    trigger: CompensationTrigger,
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
    )


def test_recover_group_fails_closed_for_mixed_canonical_and_legacy_requests(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        repository = SQLiteCompensationRepository(tmp_path / "compensation.sqlite3")
        coordinator = CompensationCoordinator(
            repository,
            CapabilityInvoker(CapabilityRegistry()),
        )
        group = coordinator.register_group(_group())
        action = coordinator.record_completed_side_effect(_action(group))
        canonical_key = (
            f"compensation:{action.group_id}:{action.action_id}:"
            f"plan-revision-{action.plan_revision}"
        )
        canonical = repository.create_request(
            _persisted_request(
                action,
                key=canonical_key,
                trigger=CompensationTrigger.MANUAL,
            )
        )
        legacy = repository.create_request(
            _persisted_request(
                action,
                key=f"{canonical_key}:{CompensationTrigger.DOWNSTREAM_FAILURE.value}",
                trigger=CompensationTrigger.DOWNSTREAM_FAILURE,
            )
        )
        repository.save_result(
            CompensationResult(
                compensation_id=canonical.compensation_id,
                status=CompensationStatus.SUCCEEDED,
                result_ref="undo:record-1",
                completed_at=datetime.now(UTC),
            )
        )

        async def context_factory(request, completed_action):
            assert request.compensation_id == legacy.compensation_id
            assert completed_action.action_id == action.action_id
            return _context(group, 1)

        await coordinator.recover_group(
            group.group_id,
            context_factory=context_factory,
        )

        canonical_result = repository.get_result(canonical.compensation_id)
        legacy_result = repository.get_result(legacy.compensation_id)
        assert canonical_result is not None
        assert canonical_result.status is CompensationStatus.SUCCEEDED
        assert legacy_result is not None
        assert legacy_result.status is CompensationStatus.RECONCILIATION_REQUIRED
        assert legacy_result.manual_intervention_required
        assert "manual reconciliation" in (legacy_result.error_message or "")

    asyncio.run(scenario())


def test_final_boundary_expiry_preserves_prior_approval_invocation_tuple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        from ai_multi_agent_platform.capabilities import invocation as invocation_module
        from ai_multi_agent_platform.compensation import coordinator as coordinator_module
        from ai_multi_agent_platform.compensation import service as service_module

        provider = _UndoProvider()
        registry = CapabilityRegistry()
        await registry.register_provider(provider)
        approval = {"granted": False}

        async def approval_hook(request, capability, invocation) -> bool:
            del request, capability, invocation
            return approval["granted"]

        invoker = CapabilityInvoker(
            registry,
            canonical_binding_hook=bind_canonical_capability_invocation,
            approval_hook=approval_hook,
        )
        repository = InMemoryCompensationRepository()
        coordinator = CompensationCoordinator(
            repository,
            invoker,
            approval_reference_lookup=lambda _: new_id("approval"),
        )
        started = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
        group = coordinator.register_group(_group())
        action = coordinator.record_completed_side_effect(
            _action(
                group,
                completed_at=started,
                requires_approval=True,
                window_seconds=10.0,
            )
        )
        monkeypatch.setattr(service_module, "utc_now", lambda: started + timedelta(seconds=5))
        monkeypatch.setattr(
            coordinator_module,
            "utc_now",
            lambda: started + timedelta(seconds=5),
        )
        request = coordinator.request_compensation(
            action.action_id,
            trigger=CompensationTrigger.MANUAL,
            reason="approval before expiry",
            actor_ref="user:user-1",
            correlation_id="corr-final-boundary-audit",
        )
        awaiting = await coordinator.execute(request.compensation_id, _context(group, 1))
        assert awaiting.status is CompensationStatus.APPROVAL_REQUIRED
        assert awaiting.canonical_tool_invocation_id is not None
        assert awaiting.approval_id is not None

        approval["granted"] = True

        class _AfterDeadline(datetime):
            @classmethod
            def now(cls, tz=None):
                del tz
                return started + timedelta(seconds=11)

        monkeypatch.setattr(invocation_module, "datetime", _AfterDeadline)
        expired = await coordinator.execute(request.compensation_id, _context(group, 2))

        assert expired.status is CompensationStatus.EXPIRED
        assert expired.canonical_tool_invocation_id == awaiting.canonical_tool_invocation_id
        assert expired.provider_id == awaiting.provider_id
        assert expired.approval_id == awaiting.approval_id
        assert expired.invocation_id == awaiting.invocation_id
        assert expired.execution_task_id == awaiting.execution_task_id
        assert expired.execution_run_id == awaiting.execution_run_id
        assert expired.execution_agent_id == awaiting.execution_agent_id
        assert provider.calls == []

    asyncio.run(scenario())
