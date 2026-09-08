"""Resolve durable EgressProfiles without taking provider identity ownership."""

from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.contracts import (
    EgressDecision,
    EgressPolicyPort,
    EgressRequest,
    EgressTarget,
)

from .egress_profiles import EgressProfileRepository


class RepositoryBackedEgressPolicy(EgressPolicyPort):
    """Decorate any canonical policy with project-aware durable profile resolution.

    The request continues to name the provider/model/capability/connector using the owning
    registry's canonical target ID. This adapter only resolves the current policy revision for
    that identity and scope, then delegates the actual disclosure decision.
    """

    def __init__(
        self,
        repository: EgressProfileRepository,
        delegate: EgressPolicyPort,
    ) -> None:
        self.repository = repository
        self.delegate = delegate

    async def evaluate(self, request: EgressRequest) -> EgressDecision:
        profile = self.repository.resolve(
            request.target.kind,
            request.target.target_id,
            request.context.project_id,
        )
        if profile is None:
            return await self.delegate.evaluate(request)

        resolved_target = EgressTarget(
            kind=request.target.kind,
            target_id=request.target.target_id,
            profile=profile,
            policy_metadata=request.target.policy_metadata,
        )
        resolved_request = replace(request, target=resolved_target)
        return await self.delegate.evaluate(resolved_request)


__all__ = ["RepositoryBackedEgressPolicy"]
