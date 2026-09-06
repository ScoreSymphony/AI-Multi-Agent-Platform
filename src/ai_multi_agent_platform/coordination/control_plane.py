"""Backend-neutral Control Plane adapters for durable Plan/Step progress."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import CommandHandler
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .models import (
    PlanCoordinationProjection,
    StepCoordinationProjection,
    StepCoordinationRecord,
)
from .repair import CoordinatorRepairAction, CoordinatorRepairService
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
            self._resource(self._coordinator.projection(state.plan.id))
            for state in self._coordinator.repository.list_active_plans()
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        return self._resource(self._coordinator.projection(resource_id))

    def _resource(self, projection: PlanCoordinationProjection) -> dict[str, JsonValue]:
        return _projection_resource(
            projection,
            records=_record_map(self._coordinator, projection.plan_id),
        )


class CoordinatorCommandHandlers:
    """Authorization remains at the generic Control Plane command boundary."""

    def __init__(self, coordinator: DurablePlanStepCoordinator) -> None:
        self._coordinator = coordinator
        self._repair = CoordinatorRepairService(coordinator)

    async def reconcile(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context, payload
        return self._resource(await self._coordinator.reconcile_plan(resource_ref))

    async def cancel(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del payload
        if context.idempotency_key is None:
            raise ContractError(ErrorCode.INVALID_REQUEST, "idempotency key is required")
        return self._resource(
            await self._coordinator.cancel_plan(
                resource_ref,
                idempotency_key=context.idempotency_key,
            )
        )

    async def repair(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if context.idempotency_key is None:
            raise ContractError(ErrorCode.INVALID_REQUEST, "idempotency key is required")
        step_id = payload.get("step_id")
        action_raw = payload.get("action")
        expected_revision = payload.get("expected_revision")
        if not isinstance(step_id, str) or not step_id.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "repair step_id is required")
        if not isinstance(action_raw, str):
            raise ContractError(ErrorCode.INVALID_REQUEST, "repair action is required")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "repair expected_revision must be an integer",
            )
        try:
            action = CoordinatorRepairAction(action_raw)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"unsupported coordinator repair action: {action_raw}",
            ) from exc
        return self._resource(
            await self._repair.repair_step(
                plan_id=resource_ref,
                step_id=step_id,
                action=action,
                expected_revision=expected_revision,
                idempotency_key=context.idempotency_key,
            )
        )

    def _resource(self, projection: PlanCoordinationProjection) -> dict[str, JsonValue]:
        return _projection_resource(
            projection,
            records=_record_map(self._coordinator, projection.plan_id),
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
        "coordination.repair": handlers.repair,
    }


def _record_map(
    coordinator: DurablePlanStepCoordinator,
    plan_id: str,
) -> dict[str, StepCoordinationRecord]:
    return {record.step_id: record for record in coordinator.repository.list_step_records(plan_id)}


def _projection_resource(
    projection: PlanCoordinationProjection,
    *,
    records: dict[str, StepCoordinationRecord] | None = None,
) -> dict[str, JsonValue]:
    return {
        "id": projection.plan_id,
        "task_id": projection.task_id,
        "plan_revision": projection.plan_revision,
        "steps": [
            _step_resource(
                step,
                record=None if records is None else records.get(step.step_id),
            )
            for step in projection.steps
        ],
    }


def _step_resource(
    step: StepCoordinationProjection,
    *,
    record: StepCoordinationRecord | None = None,
) -> dict[str, JsonValue]:
    return {
        "id": step.step_id,
        "status": step.status.value,
        "coordination_phase": step.phase.value,
        "coordination_revision": None if record is None else record.revision,
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
        "reconciliation_detail": None if record is None else record.reconciliation_detail,
    }
