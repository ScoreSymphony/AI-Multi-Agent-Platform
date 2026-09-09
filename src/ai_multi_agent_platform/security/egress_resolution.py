"""Resolve durable EgressProfiles without taking provider identity ownership."""

from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.contracts import (
    EgressDecision,
    EgressOutcome,
    EgressPolicyPort,
    EgressReasonCode,
    EgressRequest,
    EgressTarget,
    EgressTargetPosture,
)

from .egress_profiles import EgressProfileRepository


class RepositoryBackedEgressPolicy(EgressPolicyPort):
    """Decorate any canonical policy with project-aware durable profile resolution.

    The request continues to name the provider/model/capability/connector using the owning
    registry's canonical target ID. This adapter only resolves the current policy revision for
    that identity and scope, then delegates the actual disclosure decision.

    Production deployments may require every external target to have an explicit profile. In that
    mode a profileless external destination is treated as unverified and fails closed before any
    provider call. Inline profiles already attached by an owning domain remain valid inputs; the
    durable repository only overrides them when an exact scoped profile exists.
    """

    version = "repository-backed-egress-policy/v2"

    def __init__(
        self,
        repository: EgressProfileRepository,
        delegate: EgressPolicyPort,
        *,
        require_external_profile: bool = False,
    ) -> None:
        self.repository = repository
        self.delegate = delegate
        self.require_external_profile = require_external_profile

    async def evaluate(self, request: EgressRequest) -> EgressDecision:
        profile = self.repository.resolve(
            request.target.kind,
            request.target.target_id,
            request.context.project_id,
        )
        if profile is None:
            if (
                self.require_external_profile
                and request.target.profile is None
                and request.target.effective_posture is EgressTargetPosture.EXTERNAL
            ):
                return EgressDecision(
                    request_id=request.request_id,
                    outcome=EgressOutcome.UNKNOWN_BLOCKED,
                    target_kind=request.target.kind,
                    target_id=request.target.target_id,
                    effective_classification=request.classification,
                    reason_code=EgressReasonCode.UNVERIFIED_PROFILE,
                    policy_version=self.version,
                    audit_metadata={
                        "target_posture": EgressTargetPosture.EXTERNAL.value,
                        "external_profile_required": True,
                    },
                )
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
