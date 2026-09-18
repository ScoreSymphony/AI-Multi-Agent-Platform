"""Reusable failure injection around external-effect dispatch/acknowledgement boundaries."""

from __future__ import annotations

import asyncio
from enum import StrEnum

from ai_multi_agent_platform.capabilities import (
    CapabilityRegistration,
    CapabilitySpec,
    CapabilityToolProvider,
    ExternalEffectIdempotency,
    ExternalEffectObservation,
    ExternalEffectObservationStatus,
    ExternalEffectReconciler,
    ExternalEffectReconciliationRequest,
    ExternalEffectRecoveryPolicy,
    SideEffectClassification,
)
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    HealthStatus,
    ProviderDescriptor,
    ToolInvocation,
    ToolResult,
)


class ExternalEffectFault(StrEnum):
    NONE = "none"
    TIMEOUT_AFTER_EFFECT = "timeout_after_effect"
    FAILURE_AFTER_EFFECT = "failure_after_effect"


class FailureInjectingExternalEffectProvider(CapabilityToolProvider, ExternalEffectReconciler):
    """Deterministic external provider for uncertain dispatch/ack recovery tests.

    The effect is recorded before the injected acknowledgement fault. For declared guaranteed
    idempotency the provider deduplicates by the propagated idempotency key, modelling a native
    provider guarantee rather than weaker platform-side emulation.
    """

    def __init__(
        self,
        policy: ExternalEffectRecoveryPolicy,
        *,
        provider_id: str = "testing-external-effect-provider",
        capability_id: str = "testing.external-effect",
        provider_tool_ref: str = "external-effect",
        timeout_seconds: float = 0.01,
        fault: ExternalEffectFault = ExternalEffectFault.TIMEOUT_AFTER_EFFECT,
    ) -> None:
        self.policy = policy
        self.provider_id = provider_id
        self.capability_id = capability_id
        self.provider_tool_ref = provider_tool_ref
        self.timeout_seconds = timeout_seconds
        self.fault = fault
        self.effects = 0
        self.reconciliations = 0
        self.applied = False
        self.observation_status: ExternalEffectObservationStatus | None = None
        self._seen_idempotency_keys: set[str] = set()

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id=self.provider_id,
            provider_type="failure-injection",
        )

    async def health(self) -> HealthStatus:
        return HealthStatus.HEALTHY

    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        return (
            CapabilityRegistration(
                capability=CapabilitySpec(
                    capability_id=self.capability_id,
                    name="Failure-injected external effect",
                    version="1.0",
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    side_effects=SideEffectClassification.EXTERNAL,
                    timeout_seconds=self.timeout_seconds,
                    external_effect_recovery=self.policy,
                    health=HealthStatus.HEALTHY,
                ),
                provider_id=self.provider_id,
                provider_tool_ref=self.provider_tool_ref,
            ),
        )

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        idempotency_key = invocation.context.control.idempotency_key
        if self.policy.idempotency is ExternalEffectIdempotency.GUARANTEED:
            if idempotency_key is None:
                raise AssertionError(
                    "guaranteed-idempotent failure injector requires an idempotency key"
                )
            if idempotency_key not in self._seen_idempotency_keys:
                self._seen_idempotency_keys.add(idempotency_key)
                self.effects += 1
        else:
            self.effects += 1
        self.applied = True

        if self.fault is ExternalEffectFault.TIMEOUT_AFTER_EFFECT:
            await asyncio.sleep(self.timeout_seconds * 5)
        if self.fault is ExternalEffectFault.FAILURE_AFTER_EFFECT:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "injected acknowledgement failure after external effect",
                retryable=True,
                provider_id=self.provider_id,
            )
        return ToolResult(
            invocation_id=invocation.invocation_id,
            output={"applied": True},
        )

    async def reconcile_external_effect(
        self,
        request: ExternalEffectReconciliationRequest,
    ) -> ExternalEffectObservation:
        if request.provider_id != self.provider_id:
            raise AssertionError("reconciliation routed to the wrong provider")
        if request.capability_id != self.capability_id:
            raise AssertionError("reconciliation routed to the wrong capability")
        self.reconciliations += 1
        status = self.observation_status
        if status is None:
            status = (
                ExternalEffectObservationStatus.APPLIED
                if self.applied
                else ExternalEffectObservationStatus.NOT_APPLIED
            )
        return ExternalEffectObservation(
            status=status,
            result_ref="failure-injection-result"
            if status is ExternalEffectObservationStatus.APPLIED
            else None,
        )


__all__ = [
    "ExternalEffectFault",
    "FailureInjectingExternalEffectProvider",
]
