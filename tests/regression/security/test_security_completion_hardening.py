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
    CompensationIdempotency,
    ReversibilityClassification,
    SideEffectClassification,
    bind_canonical_capability_invocation,
)
from ai_multi_agent_platform.capabilities.provider import CapabilityToolProvider
from ai_multi_agent_platform.compensation import (
    CompensationCoordinator,
    CompensationExecutionContext,
    CompensationGroup,
    CompensationPolicy,
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
            # Deliberately no required_approvals: #596 must be able to strengthen the
            # requirement for this particular compensating invocation.
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


def _group(*, require_human_approval: bool = False) -> CompensationGroup:
    return CompensationGroup(
        group_id=new_compensation_group_id(),
        task_id=new_id("task"),
        plan_id=new_id("plan"),
        plan_revision=1,
        project_id=new_id("project"),
        policy=CompensationPolicy(require_human_approval=require_human_approval),
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
        required_original_argument_keys=("resource",),
        side_effects=SideEffectClassification.DESTRUCTIVE,
        requires_approval=requires_approval,
        idempotency=CompensationIdempotency.GUARANTEED,
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
        evidence_refs=("original:record-1",),
        **kwargs,
    )


def _context(group: CompensationGroup, ordinal: int = 1) -> CompensationExecutionContext:
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


async def _coordinator(
    repository,
    provider: _UndoProvider,
    *,
    approval_hook=None,
) -> CompensationCoordinator:
    registry = CapabilityRegistry()
    await registry.register_provider(provider)
    invoker = CapabilityInvoker(
        registry,
        canonical_binding_hook=bind_canonical_capability_invocation,
        approval_hook=approval_hook,
    )
    return CompensationCoordinator(repository, invoker)


@pytest.mark.parametrize(
    ("group_requires_approval", "descriptor_requires_approval"),
    [(True, False), (False, True)],
)
def test_compensation_policy_or_descriptor_can_force_fresh_ordinary_approval(
    group_requires_approval: bool,
    descriptor_requires_approval: bool,
) -> None:
    async def scenario() -> None:
        provider = _UndoProvider()

        async def not_approved(request, capability, invocation) -> bool:
            del request, capability, invocation
            return False

        coordinator = await _coordinator(
            InMemoryCompensationRepository(),
            provider,
            approval_hook=not_approved,
        )
        group = coordinator.register_group(_group(require_human_approval=group_requires_approval))
        action = coordinator.record_completed_side_effect(
            _action(group, requires_approval=descriptor_requires_approval)
        )
        request = coordinator.request_compensation(
            action.action_id,
            trigger=CompensationTrigger.MANUAL,
            reason="operator recovery",
            actor_ref="user:user-1",
            correlation_id="corr-approval-hardening",
        )

        result = await coordinator.execute(request.compensation_id, _context(group))

        assert result.status is CompensationStatus.APPROVAL_REQUIRED
        assert result.canonical_tool_invocation_id is not None
        assert provider.calls == []

    asyncio.run(scenario())


def test_expiry_after_approval_wait_preserves_prior_governed_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        from ai_multi_agent_platform.compensation import coordinator as coordinator_module
        from ai_multi_agent_platform.compensation import service as service_module

        provider = _UndoProvider()

        async def not_approved(request, capability, invocation) -> bool:
            del request, capability, invocation
            return False

        repository = InMemoryCompensationRepository()
        coordinator = await _coordinator(
            repository,
            provider,
            approval_hook=not_approved,
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
            correlation_id="corr-approval-expiry",
        )
        awaiting_approval = await coordinator.execute(
            request.compensation_id,
            _context(group),
        )
        assert awaiting_approval.status is CompensationStatus.APPROVAL_REQUIRED
        assert awaiting_approval.canonical_tool_invocation_id is not None

        monkeypatch.setattr(
            coordinator_module,
            "utc_now",
            lambda: started + timedelta(seconds=11),
        )
        expired = await coordinator.execute(request.compensation_id, _context(group, 2))

        assert expired.status is CompensationStatus.EXPIRED
        assert (
            expired.canonical_tool_invocation_id == awaiting_approval.canonical_tool_invocation_id
        )
        assert expired.provider_id == awaiting_approval.provider_id
        assert provider.calls == []

    asyncio.run(scenario())


def test_compensation_window_is_rechecked_immediately_before_provider_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        from ai_multi_agent_platform.capabilities import invocation as invocation_module
        from ai_multi_agent_platform.compensation import coordinator as coordinator_module
        from ai_multi_agent_platform.compensation import service as service_module

        provider = _UndoProvider()

        async def approved(request, capability, invocation) -> bool:
            del request, capability, invocation
            return True

        repository = InMemoryCompensationRepository()
        coordinator = await _coordinator(
            repository,
            provider,
            approval_hook=approved,
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

        class _AfterDeadline(datetime):
            @classmethod
            def now(cls, tz=None):
                del tz
                return started + timedelta(seconds=11)

        monkeypatch.setattr(invocation_module, "datetime", _AfterDeadline)
        request = coordinator.request_compensation(
            action.action_id,
            trigger=CompensationTrigger.MANUAL,
            reason="governance crosses expiry deadline",
            actor_ref="user:user-1",
            correlation_id="corr-final-expiry",
        )
        result = await coordinator.execute(request.compensation_id, _context(group))

        assert result.status is CompensationStatus.EXPIRED
        assert result.canonical_tool_invocation_id is not None
        assert result.manual_intervention_required
        assert provider.calls == []

    asyncio.run(scenario())


def test_sqlite_upgrade_reuses_legacy_trigger_suffixed_request_without_second_undo(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        provider = _UndoProvider()
        repository = SQLiteCompensationRepository(tmp_path / "compensation.sqlite3")
        coordinator = await _coordinator(repository, provider)
        group = coordinator.register_group(_group())
        action = coordinator.record_completed_side_effect(_action(group))
        descriptor = action.compensation
        assert descriptor is not None

        legacy = CompensationRequest(
            compensation_id=new_compensation_id(),
            idempotency_key=(
                f"compensation:{action.group_id}:{action.action_id}:"
                f"plan-revision-{action.plan_revision}:downstream_failure"
            ),
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
            trigger=CompensationTrigger.DOWNSTREAM_FAILURE,
            reason="persisted by pre-hardening #596",
            actor_ref="service:coordination",
            correlation_id="corr-legacy-request",
        )
        repository.create_request(legacy)
        repository.save_result(
            CompensationResult(
                compensation_id=legacy.compensation_id,
                status=CompensationStatus.SUCCEEDED,
                result_ref="undo:record-1",
                evidence_refs=("evidence:record-1",),
                completed_at=datetime.now(UTC),
            )
        )

        recovered = coordinator.request_compensation(
            action.action_id,
            trigger=CompensationTrigger.MANUAL,
            reason="operator retries after upgrade",
            actor_ref="user:user-1",
            correlation_id="corr-after-upgrade",
        )
        result = await coordinator.execute(recovered.compensation_id, _context(group))

        assert recovered.compensation_id == legacy.compensation_id
        assert result.status is CompensationStatus.SUCCEEDED
        assert len(repository.list_requests(group.group_id)) == 1
        assert provider.calls == []

    asyncio.run(scenario())
