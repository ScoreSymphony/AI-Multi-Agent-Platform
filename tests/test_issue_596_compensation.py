from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from ai_multi_agent_platform.capabilities import (
    CapabilityInvoker,
    CapabilityRegistration,
    CapabilityRegistry,
    CapabilitySpec,
    CompensationDescriptor,
    CompensationIdempotency,
    PolicyDecision,
    ReversibilityClassification,
    SideEffectClassification,
)
from ai_multi_agent_platform.capabilities.provider import CapabilityToolProvider
from ai_multi_agent_platform.compensation import (
    CompensationAutomation,
    CompensationCoordinator,
    CompensationExecutionContext,
    CompensationFailureMode,
    CompensationGroup,
    CompensationPolicy,
    CompensationReconciliation,
    CompensationResult,
    CompensationStatus,
    CompensationTrigger,
    CompletedSideEffect,
    InMemoryCompensationRepository,
    SQLiteCompensationRepository,
    new_compensation_action_id,
    new_compensation_group_id,
)
from ai_multi_agent_platform.contracts.domain_mapping import map_tool_invocation_to_domain
from ai_multi_agent_platform.contracts.types import (
    Capability,
    CapabilityKind,
    HealthStatus,
    OperationContext,
    ProviderDescriptor,
    ToolInvocation,
    ToolResult,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.domain import ToolInvocation as DomainToolInvocation


class UndoProvider(CapabilityToolProvider):
    def __init__(self, *, required_approval: bool = False, fail: bool = False) -> None:
        self.calls: list[ToolInvocation] = []
        self.fail = fail
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
            required_approvals=("external.undo",) if required_approval else (),
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
        if self.fail:
            raise RuntimeError("reference compensation failed")
        return ToolResult(
            invocation_id=invocation.invocation_id,
            output={"removed": invocation.arguments["resource"]},
            result_ref=f"undo:{invocation.arguments['resource']}",
            evidence_refs=(f"evidence:{invocation.arguments['resource']}",),
        )


async def _canonical_binding(
    request,
    registration: CapabilityRegistration,
    provider_invocation: ToolInvocation,
) -> DomainToolInvocation:
    return map_tool_invocation_to_domain(
        provider_invocation,
        canonical_tool_id=new_id("tool"),
        owner_ref=OwnerRef(type="user", id="user-1"),
        canonical_project_id=request.trace.project_id,
    )


def _group(
    *,
    automation: CompensationAutomation = CompensationAutomation.NEVER,
    failure_mode: CompensationFailureMode = CompensationFailureMode.STOP_AND_ESCALATE,
) -> CompensationGroup:
    return CompensationGroup(
        group_id=new_compensation_group_id(),
        task_id=new_id("task"),
        plan_id=new_id("plan"),
        plan_revision=1,
        project_id=new_id("project"),
        policy=CompensationPolicy(automation=automation, failure_mode=failure_mode),
    )


def _action(
    group: CompensationGroup,
    *,
    resource: str,
    execution_order: int,
    depends_on: tuple[str, ...] = (),
    reversibility: ReversibilityClassification = ReversibilityClassification.REVERSIBLE,
    descriptor: CompensationDescriptor | None = None,
) -> CompletedSideEffect:
    if descriptor is None and reversibility is ReversibilityClassification.REVERSIBLE:
        descriptor = CompensationDescriptor(
            capability_id="external.reference.undo",
            required_original_argument_keys=("resource",),
            side_effects=SideEffectClassification.DESTRUCTIVE,
            idempotency=CompensationIdempotency.GUARANTEED,
        )
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
        reversibility=reversibility,
        compensation=descriptor,
        original_arguments={"resource": resource},
        compensation_arguments={"resource": resource},
        execution_order=execution_order,
        depends_on_action_ids=depends_on,
        original_result_ref=f"create:{resource}",
        external_resource_ref=resource,
        evidence_refs=(f"original:{resource}",),
    )


def _execution_context(group: CompensationGroup, ordinal: int = 1) -> CompensationExecutionContext:
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
    provider: UndoProvider,
    *,
    policy_hook=None,
    approval_hook=None,
    reconciler=None,
) -> CompensationCoordinator:
    registry = CapabilityRegistry()
    await registry.register_provider(provider)
    invoker = CapabilityInvoker(
        registry,
        policy_hook=policy_hook,
        canonical_binding_hook=_canonical_binding,
        approval_hook=approval_hook,
    )
    return CompensationCoordinator(
        InMemoryCompensationRepository(),
        invoker,
        reconciler=reconciler,
    )


def test_reversible_action_uses_ordinary_capability_invocation_and_is_idempotent() -> None:
    async def scenario() -> None:
        provider = UndoProvider()
        coordinator = await _coordinator(provider)
        group = coordinator.register_group(_group())
        action = coordinator.record_completed_side_effect(
            _action(group, resource="record-1", execution_order=1)
        )

        first = coordinator.request_compensation(
            action.action_id,
            trigger=CompensationTrigger.MANUAL,
            reason="undo reference mutation",
            actor_ref="user:user-1",
            correlation_id="corr-compensation",
        )
        duplicate = coordinator.request_compensation(
            action.action_id,
            trigger=CompensationTrigger.MANUAL,
            reason="duplicate delivery",
            actor_ref="user:user-1",
            correlation_id="corr-duplicate",
        )
        assert duplicate.compensation_id == first.compensation_id

        result = await coordinator.execute(first.compensation_id, _execution_context(group))
        repeated = await coordinator.execute(first.compensation_id, _execution_context(group, 2))

        assert result.status is CompensationStatus.SUCCEEDED
        assert repeated == result
        assert len(provider.calls) == 1
        assert provider.calls[0].context.control.idempotency_key == first.idempotency_key
        assert result.result_ref == "undo:record-1"
        assert result.evidence_refs == ("evidence:record-1",)
        assert coordinator.repository.get_action(action.action_id).original_result_ref == "create:record-1"

    asyncio.run(scenario())


def test_irreversible_and_partial_metadata_never_fabricate_undo() -> None:
    async def scenario() -> None:
        provider = UndoProvider()
        coordinator = await _coordinator(provider)
        group = coordinator.register_group(_group())
        irreversible = coordinator.record_completed_side_effect(
            _action(
                group,
                resource="email-1",
                execution_order=1,
                reversibility=ReversibilityClassification.IRREVERSIBLE,
                descriptor=None,
            )
        )
        partial = coordinator.record_completed_side_effect(
            _action(
                group,
                resource="record-partial",
                execution_order=2,
                reversibility=ReversibilityClassification.PARTIALLY_REVERSIBLE,
                descriptor=None,
            )
        )

        for action in (irreversible, partial):
            request = coordinator.request_compensation(
                action.action_id,
                trigger=CompensationTrigger.MANUAL,
                reason="operator recovery",
                actor_ref="user:user-1",
                correlation_id="corr-no-fake-undo",
            )
            result = coordinator.repository.get_result(request.compensation_id)
            assert result is not None
            assert result.status is CompensationStatus.NOT_COMPENSABLE
            assert result.manual_intervention_required
        assert provider.calls == []

    asyncio.run(scenario())


def test_group_compensation_runs_in_reverse_dependency_order() -> None:
    async def scenario() -> None:
        provider = UndoProvider()
        coordinator = await _coordinator(provider)
        group = coordinator.register_group(
            _group(
                automation=CompensationAutomation.DOWNSTREAM_FAILURE,
                failure_mode=CompensationFailureMode.CONTINUE_AND_ESCALATE,
            )
        )
        first = coordinator.record_completed_side_effect(
            _action(group, resource="first", execution_order=1)
        )
        coordinator.record_completed_side_effect(
            _action(
                group,
                resource="second",
                execution_order=2,
                depends_on=(first.action_id,),
            )
        )
        ordinal = 0

        async def context_factory(request, action) -> CompensationExecutionContext:
            nonlocal ordinal
            ordinal += 1
            return _execution_context(group, ordinal)

        projection = await coordinator.compensate_group(
            group.group_id,
            trigger=CompensationTrigger.DOWNSTREAM_FAILURE,
            reason="later step failed",
            actor_ref="service:coordination",
            correlation_id="corr-plan-failure",
            context_factory=context_factory,
            current_plan_revision=1,
        )

        assert [call.arguments["resource"] for call in provider.calls] == ["second", "first"]
        assert all(
            item.result is not None and item.result.status is CompensationStatus.SUCCEEDED
            for item in projection.actions
        )

    asyncio.run(scenario())


def test_compensation_approval_required_and_policy_denial_remain_auditable() -> None:
    async def scenario() -> None:
        approval_provider = UndoProvider(required_approval=True)

        async def not_approved(request, capability, invocation) -> bool:
            return False

        approval_coordinator = await _coordinator(
            approval_provider,
            approval_hook=not_approved,
        )
        approval_group = approval_coordinator.register_group(_group())
        approval_action = approval_coordinator.record_completed_side_effect(
            _action(approval_group, resource="approval", execution_order=1)
        )
        approval_request = approval_coordinator.request_compensation(
            approval_action.action_id,
            trigger=CompensationTrigger.MANUAL,
            reason="approval path",
            actor_ref="user:user-1",
            correlation_id="corr-approval",
        )
        approval_result = await approval_coordinator.execute(
            approval_request.compensation_id,
            _execution_context(approval_group),
        )
        assert approval_result.status is CompensationStatus.APPROVAL_REQUIRED
        assert not approval_result.manual_intervention_required
        assert approval_result.canonical_tool_invocation_id is not None
        assert approval_provider.calls == []

        denied_provider = UndoProvider()

        async def deny(request, capability) -> PolicyDecision:
            return PolicyDecision.DENY

        denied_coordinator = await _coordinator(denied_provider, policy_hook=deny)
        denied_group = denied_coordinator.register_group(_group())
        denied_action = denied_coordinator.record_completed_side_effect(
            _action(denied_group, resource="denied", execution_order=1)
        )
        denied_request = denied_coordinator.request_compensation(
            denied_action.action_id,
            trigger=CompensationTrigger.MANUAL,
            reason="denied path",
            actor_ref="user:user-1",
            correlation_id="corr-denied",
        )
        denied_result = await denied_coordinator.execute(
            denied_request.compensation_id,
            _execution_context(denied_group),
        )
        assert denied_result.status is CompensationStatus.DENIED
        assert denied_result.manual_intervention_required
        assert denied_provider.calls == []

    asyncio.run(scenario())


def test_restart_after_possible_external_effect_refuses_blind_repeat(tmp_path: Path) -> None:
    async def scenario() -> None:
        provider = UndoProvider()
        registry = CapabilityRegistry()
        await registry.register_provider(provider)
        invoker = CapabilityInvoker(registry)
        database = tmp_path / "compensation.sqlite3"
        repository = SQLiteCompensationRepository(database)
        coordinator = CompensationCoordinator(repository, invoker)
        group = coordinator.register_group(_group())
        action = coordinator.record_completed_side_effect(
            _action(group, resource="ambiguous", execution_order=1)
        )
        request = coordinator.request_compensation(
            action.action_id,
            trigger=CompensationTrigger.MANUAL,
            reason="restart test",
            actor_ref="service:recovery",
            correlation_id="corr-restart",
        )
        context = _execution_context(group)
        repository.save_result(
            CompensationResult(
                compensation_id=request.compensation_id,
                status=CompensationStatus.RUNNING,
                execution_task_id=context.task_id,
                execution_run_id=context.run_id,
                execution_agent_id=context.agent_id,
                invocation_id=context.invocation_id,
            )
        )

        restarted_repository = SQLiteCompensationRepository(database)
        restarted = CompensationCoordinator(restarted_repository, invoker)
        projection = await restarted.recover_group(
            group.group_id,
            context_factory=lambda request, action: _async_context(group),
        )

        result = projection.actions[0].result
        assert result is not None
        assert result.status is CompensationStatus.RECONCILIATION_REQUIRED
        assert result.manual_intervention_required
        assert provider.calls == []

    asyncio.run(scenario())


async def _async_context(group: CompensationGroup) -> CompensationExecutionContext:
    return _execution_context(group)


def test_reconciliation_evidence_can_acknowledge_success_without_reexecution() -> None:
    class KnownSuccessReconciler:
        async def reconcile(self, request, action, result) -> CompensationReconciliation:
            return CompensationReconciliation(
                outcome_known=True,
                succeeded=True,
                result_ref="reconciled:done",
                evidence_refs=("reconciled:evidence",),
            )

    async def scenario() -> None:
        provider = UndoProvider()
        coordinator = await _coordinator(provider, reconciler=KnownSuccessReconciler())
        group = coordinator.register_group(_group())
        action = coordinator.record_completed_side_effect(
            _action(group, resource="reconcile", execution_order=1)
        )
        request = coordinator.request_compensation(
            action.action_id,
            trigger=CompensationTrigger.MANUAL,
            reason="recover acknowledgment",
            actor_ref="service:recovery",
            correlation_id="corr-reconcile",
        )
        context = _execution_context(group)
        coordinator.repository.save_result(
            CompensationResult(
                compensation_id=request.compensation_id,
                status=CompensationStatus.RUNNING,
                execution_task_id=context.task_id,
                execution_run_id=context.run_id,
                execution_agent_id=context.agent_id,
                invocation_id=context.invocation_id,
            )
        )
        result = await coordinator.execute(request.compensation_id, context)

        assert result.status is CompensationStatus.SUCCEEDED
        assert result.result_ref == "reconciled:done"
        assert result.evidence_refs == ("reconciled:evidence",)
        assert provider.calls == []

    asyncio.run(scenario())


def test_provider_failure_is_visible_and_requires_manual_intervention() -> None:
    async def scenario() -> None:
        provider = UndoProvider(fail=True)
        coordinator = await _coordinator(provider)
        group = coordinator.register_group(_group())
        action = coordinator.record_completed_side_effect(
            _action(group, resource="failure", execution_order=1)
        )
        request = coordinator.request_compensation(
            action.action_id,
            trigger=CompensationTrigger.MANUAL,
            reason="failure path",
            actor_ref="service:recovery",
            correlation_id="corr-failure",
        )
        result = await coordinator.execute(request.compensation_id, _execution_context(group))

        assert result.status is CompensationStatus.FAILED
        assert result.manual_intervention_required
        assert result.error_code == "backend_error"
        assert len(provider.calls) == 1

    asyncio.run(scenario())


def test_newer_plan_revision_is_not_compensated_without_explicit_policy() -> None:
    async def scenario() -> None:
        provider = UndoProvider()
        coordinator = await _coordinator(provider)
        group = coordinator.register_group(_group(automation=CompensationAutomation.DOWNSTREAM_FAILURE))
        coordinator.record_completed_side_effect(_action(group, resource="old", execution_order=1))

        try:
            await coordinator.compensate_group(
                group.group_id,
                trigger=CompensationTrigger.DOWNSTREAM_FAILURE,
                reason="stale plan failure",
                actor_ref="service:coordination",
                correlation_id="corr-stale",
                context_factory=lambda request, action: _async_context(group),
                current_plan_revision=2,
            )
        except Exception as exc:
            assert getattr(exc, "code", None).value == "conflict"
        else:
            raise AssertionError("stale Plan revision compensation must fail closed")
        assert provider.calls == []

    asyncio.run(scenario())
