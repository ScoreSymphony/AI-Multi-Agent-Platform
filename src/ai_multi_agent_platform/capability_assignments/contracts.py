"""Narrow service seams for capability-assignment policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ai_multi_agent_platform.capabilities import CapabilitySpec
from ai_multi_agent_platform.contracts import AuthorizationDecision, OperationContext
from ai_multi_agent_platform.security import ActorIdentity, ProposedAction, RiskClassification

from .models import CapabilityAssignmentTarget


class CapabilityInventory(Protocol):
    """Narrow #12 registry seam used for canonical reference validation."""

    def inventory_capabilities(
        self,
        *,
        include_unavailable: bool = True,
    ) -> tuple[CapabilitySpec, ...]: ...


@dataclass(frozen=True, slots=True)
class ResolvedCapabilityAssignmentTarget:
    """Safe canonical scope projection returned after exact target lookup."""

    project_id: str | None = None
    organization_id: str | None = None


class CapabilityAssignmentTargetResolver(Protocol):
    def resolve(
        self,
        target: CapabilityAssignmentTarget,
    ) -> ResolvedCapabilityAssignmentTarget: ...


class CapabilityAssignmentAuthorizationGate(Protocol):
    async def decide(
        self,
        action: ProposedAction,
        *,
        approval_id: str | None = None,
        risk: RiskClassification = RiskClassification.ELEVATED,
    ) -> AuthorizationDecision: ...

    async def enforce(
        self,
        action: ProposedAction,
        *,
        approval_id: str | None = None,
        risk: RiskClassification = RiskClassification.ELEVATED,
    ) -> AuthorizationDecision: ...


@dataclass(frozen=True, slots=True)
class CapabilityAssignmentAccessContext:
    actor: ActorIdentity
    operation: OperationContext
    approval_id: str | None = None
