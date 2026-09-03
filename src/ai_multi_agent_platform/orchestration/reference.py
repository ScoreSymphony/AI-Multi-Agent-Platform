"""Deterministic platform-owned orchestrator for the self-hosted reference path."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import (
    Capability,
    CapabilityKind,
    HealthStatus,
    Orchestrator,
    PlanRequest,
    PlanResponse,
    PlanStepProposal,
    ProviderDescriptor,
)


class ReferenceOrchestrator(Orchestrator):
    """Create one deterministic execution step without an external orchestrator service."""

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="reference-orchestrator",
            provider_type="orchestrator",
            supported_operations=("plan",),
            capabilities=(
                Capability(
                    name="orchestration.reference-plan",
                    kind=CapabilityKind.ORCHESTRATION,
                    supported_operations=("plan",),
                    features=("deterministic", "single-step", "self-hosted"),
                ),
            ),
            health=HealthStatus.HEALTHY,
            available=True,
        )

    async def plan(self, request: PlanRequest) -> PlanResponse:
        return PlanResponse(
            summary=f"Reference plan for {request.objective}",
            steps=(
                PlanStepProposal(
                    key="step-1",
                    title="Execute requested work",
                    objective=request.objective,
                ),
            ),
        )
