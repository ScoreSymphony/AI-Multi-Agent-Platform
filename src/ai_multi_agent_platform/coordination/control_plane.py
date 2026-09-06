"""Backend-neutral Control Plane adapters for durable Plan/Step progress."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import CommandHandler
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .models import PlanCoordinationProjection, StepCoordinationProjection
from .service import DurablePlanStepCoordinator


class CoordinatorPlanResourceService:
    """Read-only extension resource; UI/CLI surfaces remain owned by downstream #421."""

    def __init__(self, coordinator: DurablePlanStepCoordinator) -> None:
        self._coordinator = coordinator

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(
            _projection_resource(self._coordinator.projection(state.plan.id))
            for state in self._coordinator.repository.list_active_plans()
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        return _projection_resource(self._coordinator.projection(resource_id))


class CoordinatorCommandHandlers:
    """Authorization remains at the generic Control Plane command boundary."""

    def __init__(self, coordinator: DurablePlanStepCoordinator) -> None:
        self._coordinator = coordinator

    async def reconcile(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context, payload
        return _projection_resource(await self._coordinator.reconcile_plan(resource_ref))

    async def cancel(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del payload
        if context.idempotency_key is None:
            raise ContractError(ErrorCode.INVALID_REQUEST, "idempotency key is required")
        return _projection_resource(
            await self._coordinator.cancel_plan(
                resource_ref,
                idempotency_key=context.idempotency_key,
            )
        )


def coordination_resource_services(
    coordinator: DurablePlanStepCoordinator,
) -> dict[str, CoordinatorPlanResourceService]:
    return {"plan-coordination": CoordinatorPlanResourceService(coordinator)}


def coordination_command_handlers(
    coordinator: DurablePlanStepCoordinator,
) -> dict[str, CommandHandler]:
    handlers = CoordinatorCommandHandlers(coordinator)
    return {
        "coordination.reconcile": handlers.reconcile,
        "coordination.cancel": handlers.cancel,
    }


def _projection_resource(projection: PlanCoordinationProjection) -> dict[str, JsonValue]:
    return {
        "id": projection.plan_id,
        "task_id": projection.task_id,
        "plan_revision": projection.plan_revision,
        "steps": [_step_resource(step) for step in projection.steps],
    }


def _step_resource(step: StepCoordinationProjection) -> dict[str, JsonValue]:
    return {
        "id": step.step_id,
        "status": step.status.value,
        "coordination_phase": step.phase.value,
        "dependency_ids": list(step.dependency_ids),
        "satisfied_dependency_ids": list(step.satisfied_dependency_ids),
        "latest_run_id": step.latest_run_id,
        "current_attempt": step.current_attempt,
        "retry_due_at": step.retry_due_at.isoformat() if step.retry_due_at is not None else None,
        "wait_type": step.wait_type.value if step.wait_type is not None else None,
        "wait_deadline_at": (
            step.wait_deadline_at.isoformat() if step.wait_deadline_at is not None else None
        ),
        "reconciliation": step.reconciliation.value,
    }
