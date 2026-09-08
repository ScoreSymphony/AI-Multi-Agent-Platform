"""Narrow integration hooks from #384 lifecycle signals into compensation policy."""

from __future__ import annotations

from .models import CompensationGroupProjection, CompensationTrigger
from .repository import CompensationRepository
from .service import CompensationCoordinator, ExecutionContextFactory


class PlanCompensationHooks:
    """React to canonical Plan failure/cancellation without owning Plan progression.

    The durable #384 coordinator remains authoritative for Plan and Step state. Its integration
    layer may call these hooks only after the canonical lifecycle transition is committed.
    """

    def __init__(
        self,
        repository: CompensationRepository,
        coordinator: CompensationCoordinator,
    ) -> None:
        self.repository = repository
        self.coordinator = coordinator

    async def after_downstream_failure(
        self,
        plan_id: str,
        plan_revision: int,
        *,
        reason: str,
        actor_ref: str,
        correlation_id: str,
        context_factory: ExecutionContextFactory,
    ) -> tuple[CompensationGroupProjection, ...]:
        return await self._apply(
            plan_id,
            plan_revision,
            trigger=CompensationTrigger.DOWNSTREAM_FAILURE,
            reason=reason,
            actor_ref=actor_ref,
            correlation_id=correlation_id,
            context_factory=context_factory,
        )

    async def after_cancellation(
        self,
        plan_id: str,
        plan_revision: int,
        *,
        reason: str,
        actor_ref: str,
        correlation_id: str,
        context_factory: ExecutionContextFactory,
    ) -> tuple[CompensationGroupProjection, ...]:
        return await self._apply(
            plan_id,
            plan_revision,
            trigger=CompensationTrigger.CANCELLATION,
            reason=reason,
            actor_ref=actor_ref,
            correlation_id=correlation_id,
            context_factory=context_factory,
        )

    async def _apply(
        self,
        plan_id: str,
        plan_revision: int,
        *,
        trigger: CompensationTrigger,
        reason: str,
        actor_ref: str,
        correlation_id: str,
        context_factory: ExecutionContextFactory,
    ) -> tuple[CompensationGroupProjection, ...]:
        projections: list[CompensationGroupProjection] = []
        for group in self.repository.list_groups_for_plan(plan_id):
            if group.plan_revision != plan_revision:
                continue
            projections.append(
                await self.coordinator.compensate_group(
                    group.group_id,
                    trigger=trigger,
                    reason=reason,
                    actor_ref=actor_ref,
                    correlation_id=correlation_id,
                    context_factory=context_factory,
                    current_plan_revision=plan_revision,
                )
            )
        return tuple(projections)
