"""Egress-enforced canonical capability invocation runtime."""

from __future__ import annotations

from collections.abc import Callable

from ai_multi_agent_platform.contracts import (
    DataClassification,
    EgressRequest,
    EgressTarget,
    EgressTargetKind,
    EgressTargetPosture,
    digest_egress_payload,
)
from ai_multi_agent_platform.security.egress import EgressGate

from .invocation import (
    ApprovalHook,
    CanonicalInvocationBindingHook,
    CapabilityInvoker as BaseCapabilityInvoker,
    GovernanceBindingHook,
    InvocationObserver,
    PolicyHook,
)
from .registry import CapabilityRegistry
from .types import (
    CapabilityInvocation,
    CapabilityInvocationResult,
    CapabilitySpec,
    SideEffectClassification,
)

type CapabilityClassificationResolver = Callable[
    [CapabilityInvocation, CapabilitySpec], DataClassification
]


class EgressCapabilityInvoker(BaseCapabilityInvoker):
    """Default invoker that evaluates cross-process/trust calls through the egress gate."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        policy_hook: PolicyHook | None = None,
        canonical_binding_hook: CanonicalInvocationBindingHook | None = None,
        governance_binding_hook: GovernanceBindingHook | None = None,
        approval_hook: ApprovalHook | None = None,
        observer: InvocationObserver | None = None,
        egress_gate: EgressGate | None = None,
        classification_resolver: CapabilityClassificationResolver | None = None,
    ) -> None:
        super().__init__(
            registry,
            policy_hook=policy_hook,
            canonical_binding_hook=canonical_binding_hook,
            governance_binding_hook=governance_binding_hook,
            approval_hook=approval_hook,
            observer=observer,
        )
        self._egress_registry = registry
        self.egress_gate = egress_gate or EgressGate()
        self._classification_resolver = classification_resolver

    async def invoke(self, request: CapabilityInvocation) -> CapabilityInvocationResult:
        registration, _provider = self._egress_registry.resolve(
            request.capability_id,
            version=request.version,
            compatibility=request.compatibility,
            granted_permissions=request.granted_permissions,
            available_worker_capabilities=request.available_worker_capabilities,
        )
        capability = registration.capability
        posture = _capability_posture(capability, registration.node_id, registration.worker_id)
        if posture is not EgressTargetPosture.LOCAL:
            classification = (
                self._classification_resolver(request, capability)
                if self._classification_resolver is not None
                else DataClassification.INTERNAL
            )
            await self.egress_gate.enforce(
                EgressRequest(
                    request_id=f"capability:{request.invocation_id}:{capability.capability_id}",
                    target=EgressTarget(
                        kind=EgressTargetKind.CAPABILITY,
                        target_id=capability.capability_id,
                        posture=posture,
                    ),
                    context=request.context,
                    classification=classification,
                    resource_type="capability_invocation",
                    payload_digest=digest_egress_payload(dict(request.arguments)),
                    task_id=request.trace.task_id,
                    run_id=request.trace.run_id,
                    capability_id=capability.capability_id,
                    policy_descriptors={
                        "provider_id": registration.provider_id,
                        "side_effects": capability.side_effects.value,
                        "node_id": registration.node_id,
                        "worker_id": registration.worker_id,
                    },
                )
            )
        return await super().invoke(request)


def _capability_posture(
    capability: CapabilitySpec,
    node_id: str | None,
    worker_id: str | None,
) -> EgressTargetPosture:
    if capability.side_effects is SideEffectClassification.EXTERNAL or "external" in capability.tags:
        return EgressTargetPosture.EXTERNAL
    if node_id is not None or worker_id is not None:
        return EgressTargetPosture.INTERNAL
    return EgressTargetPosture.LOCAL
