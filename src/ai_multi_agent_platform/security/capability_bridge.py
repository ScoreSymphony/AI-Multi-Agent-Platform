"""Bridge issue-#12 capability hooks to the canonical issue-#15 authorization gate."""

from __future__ import annotations

from ai_multi_agent_platform.capabilities.types import (
    CapabilityInvocation,
    CapabilitySpec,
    CredentialRequirement,
    PolicyDecision,
    SafetyClassification,
    SideEffectClassification,
)
from ai_multi_agent_platform.domain import ToolInvocation as DomainToolInvocation

from .approvals import ApprovalRecord
from .authorization import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationContext,
    ProposedAction,
    ResourceType,
    RiskClassification,
)
from .enforcement import AuthorizationGate


class CapabilityAuthorizationBridge:
    """Policy/approval hooks for ``CapabilityInvoker``.

    The bridge keeps policy authority outside the capability registry while ensuring the
    exact invocation arguments participate in the approval digest.
    """

    def __init__(self, gate: AuthorizationGate) -> None:
        self._gate = gate
        self._actions: dict[str, ProposedAction] = {}

    async def policy_hook(
        self,
        request: CapabilityInvocation,
        capability: CapabilitySpec,
    ) -> PolicyDecision:
        action = self._proposed_action(request, capability)
        self._actions[request.invocation_id] = action
        decision = await self._gate.decide(action, risk=_risk_for(capability))
        if decision.allowed:
            return PolicyDecision.ALLOW
        if decision.requires_approval:
            return PolicyDecision.REQUIRE_APPROVAL
        return PolicyDecision.DENY

    async def approval_hook(
        self,
        request: CapabilityInvocation,
        capability: CapabilitySpec,
        canonical_invocation: DomainToolInvocation,
    ) -> bool:
        del canonical_invocation
        action = self._actions.get(request.invocation_id)
        if action is None:
            action = self._proposed_action(request, capability)
            self._actions[request.invocation_id] = action
        if self._gate.approvals.find_valid_for(action) is not None:
            return True
        await self._gate.ensure_pending_approval_with_event(
            action,
            reason="capability metadata or authorization policy requires approval",
            policy_id="capability:approval-gate",
            risk=_risk_for(capability),
        )
        return False

    def pending_approval(self, invocation_id: str) -> ApprovalRecord | None:
        action = self._actions.get(invocation_id)
        if action is None:
            return None
        return self._gate.approvals.pending_for(action)

    @staticmethod
    def _proposed_action(
        request: CapabilityInvocation,
        capability: CapabilitySpec,
    ) -> ProposedAction:
        sensitive = (
            capability.safety is not SafetyClassification.STANDARD
            or capability.side_effects
            in {SideEffectClassification.EXTERNAL, SideEffectClassification.DESTRUCTIVE}
            or capability.credential_requirement is CredentialRequirement.REQUIRED
        )
        return ProposedAction(
            AuthorizationContext(
                actor=ActorIdentity(request.trace.agent_id, ActorType.AGENT),
                action=(
                    AuthorizationAction.INVOKE_SENSITIVE_CAPABILITY
                    if sensitive
                    else AuthorizationAction.EXECUTE
                ),
                resource_type=ResourceType.CAPABILITY,
                resource_id=capability.capability_id,
                operation=request.context,
                task_id=request.trace.task_id,
                run_id=request.trace.run_id,
                agent_id=request.trace.agent_id,
                capability_ref=capability.capability_id,
                side_effect=capability.side_effects.value,
                security_labels=(capability.safety.value,),
            ),
            payload=dict(request.arguments),
        )


def _risk_for(capability: CapabilitySpec) -> RiskClassification:
    if capability.side_effects is SideEffectClassification.DESTRUCTIVE:
        return RiskClassification.CRITICAL
    if capability.safety is SafetyClassification.SENSITIVE:
        return RiskClassification.HIGH
    if (
        capability.safety is SafetyClassification.RESTRICTED
        or capability.side_effects is SideEffectClassification.EXTERNAL
        or capability.credential_requirement is CredentialRequirement.REQUIRED
    ):
        return RiskClassification.ELEVATED
    return RiskClassification.STANDARD
