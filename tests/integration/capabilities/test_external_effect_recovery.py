from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ai_multi_agent_platform.capabilities import (
    CapabilityInvocation,
    CapabilityRegistration,
    CapabilityRegistry,
    CapabilitySpec,
    CapabilityToolProvider,
    ExternalEffectIdempotency,
    ExternalEffectObservation,
    ExternalEffectObservationStatus,
    ExternalEffectReconciler,
    ExternalEffectReconciliationRequest,
    ExternalEffectReconciliationSupport,
    ExternalEffectRecoveryCoordinator,
    ExternalEffectRecoveryDisposition,
    ExternalEffectRecoveryPolicy,
    ExternalEffectRecoveryStatus,
    InMemoryExternalEffectRecoveryRepository,
    InvocationRecord,
    InvocationStatus,
    InvocationTrace,
    PolicyDecision,
    SideEffectClassification,
    SQLiteExternalEffectRecoveryRepository,
    bind_canonical_capability_invocation,
    external_effect_recovery_resource,
)
from ai_multi_agent_platform.capabilities.egress import EgressCapabilityInvoker
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    HealthStatus,
    OperationContext,
    OperationControl,
    ProviderDescriptor,
    ToolInvocation,
    ToolResult,
)
from ai_multi_agent_platform.contracts.types import AdapterMetadata
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.observability import (
    InMemoryExporter,
    ObservabilityExternalEffectRecoveryObserver,
    Telemetry,
)
from ai_multi_agent_platform.testing import (
    ExternalEffectFault,
    FailureInjectingExternalEffectProvider,
)


class _ExternalProvider(CapabilityToolProvider, ExternalEffectReconciler):
    def __init__(
        self,
        policy: ExternalEffectRecoveryPolicy,
        *,
        timeout_seconds: float = 0.01,
    ) -> None:
        self.policy = policy
        self.timeout_seconds = timeout_seconds
        self.mode = "timeout"
        self.effects = 0
        self.reconciliations = 0
        self.applied = False
        self._seen_keys: set[str] = set()

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="test-external-provider",
            provider_type="test",
        )

    async def health(self) -> HealthStatus:
        return HealthStatus.HEALTHY

    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        return (
            CapabilityRegistration(
                capability=CapabilitySpec(
                    capability_id="external.publish",
                    name="External publish",
                    version="1.0",
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    side_effects=SideEffectClassification.EXTERNAL,
                    timeout_seconds=self.timeout_seconds,
                    external_effect_recovery=self.policy,
                    health=HealthStatus.HEALTHY,
                ),
                provider_id=self.descriptor.provider_id,
                provider_tool_ref="publish",
            ),
        )

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        key = invocation.context.control.idempotency_key
        if self.policy.idempotency is ExternalEffectIdempotency.GUARANTEED:
            if key is None:
                raise AssertionError("guaranteed-idempotent test provider requires a key")
            if key not in self._seen_keys:
                self._seen_keys.add(key)
                self.effects += 1
        else:
            self.effects += 1
        self.applied = True
        if self.mode == "timeout":
            await asyncio.sleep(self.timeout_seconds * 5)
        return ToolResult(
            invocation_id=invocation.invocation_id,
            output={"published": True},
        )

    async def reconcile_external_effect(
        self,
        request: ExternalEffectReconciliationRequest,
    ) -> ExternalEffectObservation:
        assert request.provider_id == self.descriptor.provider_id
        self.reconciliations += 1
        return ExternalEffectObservation(
            status=(
                ExternalEffectObservationStatus.APPLIED
                if self.applied
                else ExternalEffectObservationStatus.NOT_APPLIED
            ),
            result_ref="external-result" if self.applied else None,
        )


def _request(
    *, invocation_id: str = "publish-1", key: str | None = "publish-key"
) -> CapabilityInvocation:
    correlation_id = new_id("correlation")
    return CapabilityInvocation(
        invocation_id=invocation_id,
        capability_id="external.publish",
        arguments={"payload": "value"},
        context=OperationContext(
            correlation_id=correlation_id,
            control=OperationControl(idempotency_key=key),
        ),
        trace=InvocationTrace(
            correlation_id=correlation_id,
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=new_id("agent"),
        ),
    )


async def _runtime(
    policy: ExternalEffectRecoveryPolicy,
    *,
    repository: InMemoryExternalEffectRecoveryRepository
    | SQLiteExternalEffectRecoveryRepository
    | None = None,
) -> tuple[_ExternalProvider, ExternalEffectRecoveryCoordinator, EgressCapabilityInvoker]:
    provider = _ExternalProvider(policy)
    registry = CapabilityRegistry()
    await registry.register_provider(provider)
    recovery = ExternalEffectRecoveryCoordinator(
        repository or InMemoryExternalEffectRecoveryRepository()
    )
    recovery.register_reconciler(provider.descriptor.provider_id, provider)
    return (
        provider,
        recovery,
        EgressCapabilityInvoker(
            registry,
            external_effect_recovery=recovery,
        ),
    )


@pytest.mark.asyncio
async def test_non_idempotent_timeout_blocks_blind_replay_and_is_not_retryable() -> None:
    provider, recovery, invoker = await _runtime(
        ExternalEffectRecoveryPolicy(
            idempotency=ExternalEffectIdempotency.NONE,
            reconciliation=ExternalEffectReconciliationSupport.UNSUPPORTED,
        )
    )
    request = _request(key="unsafe-key")

    with pytest.raises(ContractError) as caught:
        await invoker.invoke(request)

    assert caught.value.code is ErrorCode.TIMEOUT
    assert caught.value.retryable is False
    record = recovery.find_record_by_invocation(request.invocation_id)
    assert record is not None
    assert record.status is ExternalEffectRecoveryStatus.BLOCKED
    assert record.disposition is ExternalEffectRecoveryDisposition.UNCERTAIN_MANUAL_REVIEW

    with pytest.raises(ContractError) as replay:
        await invoker.invoke(request)

    assert replay.value.code is ErrorCode.CONFLICT
    assert provider.effects == 1


@pytest.mark.asyncio
async def test_guaranteed_idempotency_allows_same_identity_retry_without_duplicate_effect() -> None:
    provider, recovery, invoker = await _runtime(
        ExternalEffectRecoveryPolicy(
            idempotency=ExternalEffectIdempotency.GUARANTEED,
            reconciliation=ExternalEffectReconciliationSupport.UNSUPPORTED,
        )
    )
    request = _request(key="stable-key")

    with pytest.raises(ContractError) as caught:
        await invoker.invoke(request)

    assert caught.value.code is ErrorCode.TIMEOUT
    assert caught.value.retryable is True
    record = recovery.find_record_by_invocation(request.invocation_id)
    assert record is not None
    assert record.disposition is ExternalEffectRecoveryDisposition.SAFE_TO_RETRY

    provider.mode = "success"
    result = await invoker.invoke(request)

    assert result.status is InvocationStatus.SUCCEEDED
    assert provider.effects == 1
    record = recovery.find_record_by_invocation(request.invocation_id)
    assert record is not None
    assert record.status is ExternalEffectRecoveryStatus.SUCCEEDED
    assert record.dispatch_attempts == 2


@pytest.mark.asyncio
async def test_provider_reconciliation_confirms_effect_without_reexecution() -> None:
    provider, recovery, invoker = await _runtime(
        ExternalEffectRecoveryPolicy(
            idempotency=ExternalEffectIdempotency.NONE,
            reconciliation=ExternalEffectReconciliationSupport.SUPPORTED,
        )
    )
    request = _request(key="reconcile-key")

    with pytest.raises(ContractError):
        await invoker.invoke(request)

    record = recovery.find_record_by_invocation(request.invocation_id)
    assert record is not None
    assert record.disposition is ExternalEffectRecoveryDisposition.RECONCILE_WITH_PROVIDER

    resolved = await recovery.reconcile_effect(record.effect_id)
    repeated = await recovery.reconcile_effect(record.effect_id)

    assert resolved.status is ExternalEffectRecoveryStatus.SUCCEEDED
    assert repeated == resolved
    assert provider.effects == 1
    assert provider.reconciliations == 1


@pytest.mark.asyncio
async def test_restart_reconciles_dispatch_with_missing_canonical_ack(tmp_path) -> None:
    repository = SQLiteExternalEffectRecoveryRepository(tmp_path / "effects.sqlite3")
    provider, recovery, _invoker = await _runtime(
        ExternalEffectRecoveryPolicy(
            idempotency=ExternalEffectIdempotency.NONE,
            reconciliation=ExternalEffectReconciliationSupport.SUPPORTED,
        ),
        repository=repository,
    )
    request = _request(invocation_id="crash-window", key="crash-key")
    registration = CapabilityRegistration(
        capability=(await provider.capability_registrations())[0].capability,
        provider_id=provider.descriptor.provider_id,
        provider_tool_ref="publish",
    )
    await recovery.prepare_attempt(request, registration.capability, registration)
    await recovery.observe(
        InvocationRecord(
            invocation_id=request.invocation_id,
            capability_id=request.capability_id,
            capability_version="1.0",
            provider_id=provider.descriptor.provider_id,
            provider_tool_ref="publish",
            status=InvocationStatus.RUNNING,
            trace=request.trace,
        )
    )
    provider.applied = True

    restarted = ExternalEffectRecoveryCoordinator(
        SQLiteExternalEffectRecoveryRepository(tmp_path / "effects.sqlite3")
    )
    restarted.register_reconciler(provider.descriptor.provider_id, provider)
    results = await restarted.reconcile_all()
    repeated = await restarted.reconcile_all()

    assert len(results) == 1
    assert results[0].status is ExternalEffectRecoveryStatus.SUCCEEDED
    assert results[0].reason == "provider_reconciliation_confirmed_applied"
    assert repeated == ()
    assert provider.reconciliations == 1


@pytest.mark.asyncio
async def test_late_success_callback_cannot_overwrite_manual_review() -> None:
    provider, recovery, invoker = await _runtime(
        ExternalEffectRecoveryPolicy(
            idempotency=ExternalEffectIdempotency.NONE,
            reconciliation=ExternalEffectReconciliationSupport.UNSUPPORTED,
        )
    )
    request = _request(key="late-key")
    with pytest.raises(ContractError):
        await invoker.invoke(request)

    await recovery.observe(
        InvocationRecord(
            invocation_id=request.invocation_id,
            capability_id=request.capability_id,
            capability_version="1.0",
            provider_id=provider.descriptor.provider_id,
            provider_tool_ref="publish",
            status=InvocationStatus.SUCCEEDED,
            trace=request.trace,
        )
    )

    record = recovery.find_record_by_invocation(request.invocation_id)
    assert record is not None
    assert record.disposition is ExternalEffectRecoveryDisposition.UNCERTAIN_MANUAL_REVIEW
    assert record.duplicate_callbacks_ignored == 1


@pytest.mark.asyncio
async def test_operator_projection_redacts_private_idempotency_and_metadata_values() -> None:
    provider, recovery, invoker = await _runtime(
        ExternalEffectRecoveryPolicy(
            idempotency=ExternalEffectIdempotency.NONE,
            reconciliation=ExternalEffectReconciliationSupport.UNSUPPORTED,
        )
    )
    request = _request(key="private-retry-key")
    with pytest.raises(ContractError):
        await invoker.invoke(request)
    record = recovery.find_record_by_invocation(request.invocation_id)
    assert record is not None
    record = replace(
        record,
        adapter_metadata=(
            AdapterMetadata(namespace="provider-native", values={"native_id": "secret-42"}),
        ),
    )
    recovery.repository.save(record)

    resource = external_effect_recovery_resource(record)
    serialized = repr(resource)

    assert resource["idempotency_key_present"] is True
    assert resource["adapter_metadata_namespaces"] == ["provider-native"]
    assert "private-retry-key" not in serialized
    assert "secret-42" not in serialized
    assert "provider_tool_ref" not in resource


@pytest.mark.asyncio
async def test_policy_denial_prevents_dispatch_and_never_creates_uncertain_effect_record() -> None:
    provider = _ExternalProvider(
        ExternalEffectRecoveryPolicy(
            idempotency=ExternalEffectIdempotency.NONE,
            reconciliation=ExternalEffectReconciliationSupport.UNSUPPORTED,
        )
    )
    registry = CapabilityRegistry()
    await registry.register_provider(provider)
    recovery = ExternalEffectRecoveryCoordinator(InMemoryExternalEffectRecoveryRepository())

    async def deny(
        request: CapabilityInvocation,
        capability: CapabilitySpec,
    ) -> PolicyDecision:
        del request, capability
        return PolicyDecision.DENY

    invoker = EgressCapabilityInvoker(
        registry,
        policy_hook=deny,
        external_effect_recovery=recovery,
    )

    with pytest.raises(ContractError) as caught:
        await invoker.invoke(_request(invocation_id="denied-effect", key="denied-key"))

    assert caught.value.code is ErrorCode.FORBIDDEN
    assert provider.effects == 0
    assert recovery.list_records() == ()


@pytest.mark.asyncio
async def test_operator_authorized_retry_still_requires_normal_invocation_approval() -> None:
    provider = _ExternalProvider(
        ExternalEffectRecoveryPolicy(
            idempotency=ExternalEffectIdempotency.NONE,
            reconciliation=ExternalEffectReconciliationSupport.UNSUPPORTED,
        )
    )
    registry = CapabilityRegistry()
    await registry.register_provider(provider)
    recovery = ExternalEffectRecoveryCoordinator(InMemoryExternalEffectRecoveryRepository())
    recovery.register_reconciler(provider.descriptor.provider_id, provider)
    approval_calls = 0

    async def deny_retry_approval(
        request: CapabilityInvocation,
        capability: CapabilitySpec,
        canonical_invocation: object,
    ) -> bool:
        nonlocal approval_calls
        del request, capability, canonical_invocation
        approval_calls += 1
        return False

    invoker = EgressCapabilityInvoker(
        registry,
        canonical_binding_hook=bind_canonical_capability_invocation,
        approval_hook=deny_retry_approval,
        external_effect_recovery=recovery,
    )
    project_id = new_id("project")
    request = _request(invocation_id="operator-approved-retry", key="operator-retry-key")
    request = replace(
        request,
        context=replace(
            request.context,
            owner_type="user",
            owner_id="operator-reviewer",
            project_id=project_id,
        ),
        trace=replace(request.trace, project_id=project_id),
    )

    with pytest.raises(ContractError):
        await invoker.invoke(request)

    record = recovery.find_record_by_invocation(request.invocation_id)
    assert record is not None
    assert record.disposition is ExternalEffectRecoveryDisposition.UNCERTAIN_MANUAL_REVIEW
    await recovery.authorize_retry(
        record.effect_id,
        actor="operator:reviewer",
        reason="reviewed provider evidence",
    )

    provider.mode = "success"
    with pytest.raises(ContractError) as retry:
        await invoker.invoke(replace(request, require_approval=True))

    assert retry.value.code is ErrorCode.FORBIDDEN
    assert approval_calls == 1
    assert provider.effects == 1
    record = recovery.find_record_by_invocation(request.invocation_id)
    assert record is not None
    assert record.disposition is ExternalEffectRecoveryDisposition.SAFE_TO_RETRY


@pytest.mark.asyncio
async def test_cancellation_after_external_dispatch_becomes_uncertain_and_non_retryable() -> None:
    provider = _ExternalProvider(
        ExternalEffectRecoveryPolicy(
            idempotency=ExternalEffectIdempotency.NONE,
            reconciliation=ExternalEffectReconciliationSupport.UNSUPPORTED,
        ),
        timeout_seconds=2.0,
    )
    registry = CapabilityRegistry()
    await registry.register_provider(provider)
    recovery = ExternalEffectRecoveryCoordinator(InMemoryExternalEffectRecoveryRepository())
    invoker = EgressCapabilityInvoker(
        registry,
        external_effect_recovery=recovery,
    )
    request = _request(invocation_id="cancelled-effect", key="cancelled-key")

    task = asyncio.create_task(invoker.invoke(request))
    for _ in range(100):
        if provider.effects:
            break
        await asyncio.sleep(0)
    assert provider.effects == 1
    task.cancel()

    with pytest.raises(ContractError) as caught:
        await task

    assert caught.value.code is ErrorCode.CANCELLED
    assert caught.value.retryable is False
    record = recovery.find_record_by_invocation(request.invocation_id)
    assert record is not None
    assert record.status is ExternalEffectRecoveryStatus.BLOCKED
    assert record.disposition is ExternalEffectRecoveryDisposition.UNCERTAIN_MANUAL_REVIEW


@pytest.mark.asyncio
async def test_recovery_telemetry_reports_uncertainty_without_private_recovery_values() -> None:
    provider = _ExternalProvider(
        ExternalEffectRecoveryPolicy(
            idempotency=ExternalEffectIdempotency.NONE,
            reconciliation=ExternalEffectReconciliationSupport.UNSUPPORTED,
        )
    )
    registry = CapabilityRegistry()
    await registry.register_provider(provider)
    exporter = InMemoryExporter()
    telemetry = Telemetry(exporter)
    recovery = ExternalEffectRecoveryCoordinator(
        InMemoryExternalEffectRecoveryRepository(),
        event_observer=ObservabilityExternalEffectRecoveryObserver(telemetry),
    )
    invoker = EgressCapabilityInvoker(
        registry,
        external_effect_recovery=recovery,
    )
    request = _request(
        invocation_id="telemetry-effect",
        key="private-telemetry-idempotency-key",
    )

    with pytest.raises(ContractError):
        await invoker.invoke(request)

    record = recovery.find_record_by_invocation(request.invocation_id)
    assert record is not None
    await recovery.authorize_retry(
        record.effect_id,
        actor="operator:telemetry-reviewer",
        reason="provider evidence reviewed",
    )

    event_names = {entry.event_name for entry in exporter.logs}
    assert "external_effect.uncertain_outcome_detected" in event_names
    assert "external_effect.retry_unsafe" in event_names
    assert "external_effect.manual_review_required" in event_names
    assert "external_effect.operator_action_applied" in event_names
    assert "external_effect.retry_safe" in event_names

    public_telemetry = repr(
        [(entry.event_name, entry.context.fields(), entry.attributes) for entry in exporter.logs]
    )
    assert "private-telemetry-idempotency-key" not in public_telemetry
    assert "provider_tool_ref" not in public_telemetry


@pytest.mark.asyncio
async def test_reusable_failure_injector_models_effect_before_lost_acknowledgement() -> None:
    provider = FailureInjectingExternalEffectProvider(
        ExternalEffectRecoveryPolicy(
            idempotency=ExternalEffectIdempotency.NONE,
            reconciliation=ExternalEffectReconciliationSupport.UNSUPPORTED,
        ),
        fault=ExternalEffectFault.TIMEOUT_AFTER_EFFECT,
    )
    registry = CapabilityRegistry()
    await registry.register_provider(provider)
    recovery = ExternalEffectRecoveryCoordinator(InMemoryExternalEffectRecoveryRepository())
    invoker = EgressCapabilityInvoker(
        registry,
        external_effect_recovery=recovery,
    )
    correlation_id = new_id("correlation")
    request = CapabilityInvocation(
        invocation_id="failure-injector-lost-ack",
        capability_id=provider.capability_id,
        arguments={"payload": "value"},
        context=OperationContext(
            correlation_id=correlation_id,
            control=OperationControl(idempotency_key="failure-injector-key"),
        ),
        trace=InvocationTrace(
            correlation_id=correlation_id,
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=new_id("agent"),
        ),
    )

    with pytest.raises(ContractError) as caught:
        await invoker.invoke(request)

    assert caught.value.code is ErrorCode.TIMEOUT
    assert provider.effects == 1
    record = recovery.find_record_by_invocation(request.invocation_id)
    assert record is not None
    assert record.disposition is ExternalEffectRecoveryDisposition.UNCERTAIN_MANUAL_REVIEW
