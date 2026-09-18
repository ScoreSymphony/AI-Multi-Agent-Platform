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
    SideEffectClassification,
    SQLiteExternalEffectRecoveryRepository,
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


def _request(*, invocation_id: str = "publish-1", key: str | None = "publish-key") -> CapabilityInvocation:
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
    return provider, recovery, EgressCapabilityInvoker(
        registry,
        external_effect_recovery=recovery,
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
    assert (
        record.disposition
        is ExternalEffectRecoveryDisposition.UNCERTAIN_MANUAL_REVIEW
    )

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
    assert (
        record.disposition
        is ExternalEffectRecoveryDisposition.RECONCILE_WITH_PROVIDER
    )

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
        capability=(
            await provider.capability_registrations()
        )[0].capability,
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

    assert len(results) == 1
    assert results[0].status is ExternalEffectRecoveryStatus.SUCCEEDED
    assert results[0].reason == "provider_reconciliation_confirmed_applied"
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
    assert (
        record.disposition
        is ExternalEffectRecoveryDisposition.UNCERTAIN_MANUAL_REVIEW
    )
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
