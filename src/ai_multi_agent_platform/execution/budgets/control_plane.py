"""Control Plane projection and mutation surface for Task execution budgets (#902)."""

from __future__ import annotations

from collections.abc import Mapping
from importlib import import_module
from typing import Any

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue, OperationContext

from .governance import TaskBudgetPolicyMutationService
from .models import (
    BudgetConsumptionSource,
    BudgetDimension,
    BudgetDimensionSnapshot,
    BudgetExhaustionAction,
    TaskBudgetLimit,
    TaskBudgetPolicy,
    UnavailableMetricPolicy,
)
from .service import TaskBudgetEnforcementService

TASK_BUDGET_MODULE = "task-execution-budgets"
TASK_BUDGET_COLLECTION = "task-execution-budgets"
TASK_BUDGET_CONFIGURE_COMMAND = "task-budget.configure"
TASK_BUDGET_REVISE_COMMAND = "task-budget.revise"
TASK_BUDGET_COMMANDS = (TASK_BUDGET_CONFIGURE_COMMAND, TASK_BUDGET_REVISE_COMMAND)


def _control_plane_extensions() -> Any:
    """Load the extension vocabulary without a static execution -> control_plane edge."""

    return import_module("ai_multi_agent_platform.control_plane.extensions")


def _security() -> Any:
    """Load actor vocabulary without a static execution -> security edge."""

    return import_module("ai_multi_agent_platform.security")


class TaskBudgetResourceService:
    """Task-scoped projection of configured limits, consumption and reservation state."""

    def __init__(self, control_plane: Any, budgets: TaskBudgetEnforcementService) -> None:
        self._control_plane = control_plane
        self._budgets = budgets

    async def list_resources(
        self,
        context: Any,
        query: Any,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        resources: list[dict[str, JsonValue]] = []
        for task_id in await self._control_plane._task_ids():  # noqa: SLF001 - extension seam
            policy = await self._budgets.policy(task_id)
            if policy is None:
                continue
            task = await self._control_plane._kernel.get_task(task_id)  # noqa: SLF001
            if not await self._control_plane._allowed_for_task(  # noqa: SLF001
                context,
                "task-budget:list",
                task_id,
                task,
            ):
                continue
            resources.append(await _budget_resource(self._budgets, task_id))
        resources.sort(key=lambda item: str(item["id"]))
        return tuple(resources)

    async def get_resource(
        self,
        context: Any,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        task = await self._control_plane._kernel.get_task(resource_id)  # noqa: SLF001
        await self._control_plane._authorize_for_task(  # noqa: SLF001
            context,
            "task-budget:read",
            resource_id,
            task,
        )
        if await self._budgets.policy(resource_id) is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                "Task execution budget policy does not exist",
                details={"task_id": resource_id},
            )
        return await _budget_resource(self._budgets, resource_id)


class TaskBudgetCommandHandlers:
    def __init__(
        self,
        control_plane: Any,
        budgets: TaskBudgetEnforcementService,
        mutations: TaskBudgetPolicyMutationService,
    ) -> None:
        self._control_plane = control_plane
        self._budgets = budgets
        self._mutations = mutations

    async def configure(
        self,
        context: Any,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        task = await self._control_plane._kernel.get_task(resource_ref)  # noqa: SLF001
        candidate = TaskBudgetPolicy(
            task_id=resource_ref,
            limits=_limits(payload),
            started_at=task.task.created_at,
            version=1,
            provenance=_request_provenance(context, "configure"),
        )
        await self._mutations.configure(
            candidate,
            actor=_actor(context),
            operation=_operation(
                context,
                task.task.owner_ref.type,
                task.task.owner_ref.id,
                task.task.project_id,
            ),
            approval_id=_optional_string(payload.get("approval_id"), "approval_id"),
        )
        return await _budget_resource(self._budgets, resource_ref)

    async def revise(
        self,
        context: Any,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        task = await self._control_plane._kernel.get_task(resource_ref)  # noqa: SLF001
        current = await self._budgets.policy(resource_ref)
        if current is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                "Task execution budget policy does not exist",
                details={"task_id": resource_ref},
            )
        candidate = TaskBudgetPolicy(
            task_id=resource_ref,
            limits=_limits(payload),
            started_at=current.started_at,
            version=current.version + 1,
            provenance=_request_provenance(context, "revise"),
        )
        await self._mutations.revise(
            candidate,
            actor=_actor(context),
            operation=_operation(
                context,
                task.task.owner_ref.type,
                task.task.owner_ref.id,
                task.task.project_id,
            ),
            approval_id=_optional_string(payload.get("approval_id"), "approval_id"),
        )
        return await _budget_resource(self._budgets, resource_ref)


async def _handler_owned_authorization(
    context: Any,
    resource_ref: str,
    payload: dict[str, JsonValue],
) -> None:
    """Defer mutation authorization to the exact payload-bound #15 budget handler."""

    del context, resource_ref, payload


def register_task_budget_control_plane(
    control_plane: Any,
    budgets: TaskBudgetEnforcementService,
    mutations: TaskBudgetPolicyMutationService,
) -> None:
    """Register the canonical #902 read and mutation surface on #32."""

    handlers = TaskBudgetCommandHandlers(control_plane, budgets, mutations)
    extensions = _control_plane_extensions()
    control_plane.register_modules(
        (
            extensions.ControlPlaneModule(
                name=TASK_BUDGET_MODULE,
                resource_services={
                    TASK_BUDGET_COLLECTION: TaskBudgetResourceService(control_plane, budgets),
                },
                command_handlers={
                    TASK_BUDGET_CONFIGURE_COMMAND: handlers.configure,
                    TASK_BUDGET_REVISE_COMMAND: handlers.revise,
                },
                command_authorizers={
                    TASK_BUDGET_CONFIGURE_COMMAND: _handler_owned_authorization,
                    TASK_BUDGET_REVISE_COMMAND: _handler_owned_authorization,
                },
            ),
        )
    )


async def _budget_resource(
    budgets: TaskBudgetEnforcementService,
    task_id: str,
) -> dict[str, JsonValue]:
    policy = await budgets.policy(task_id)
    if policy is None:
        raise ContractError(ErrorCode.NOT_FOUND, "Task execution budget policy does not exist")
    snapshot = await budgets.snapshot(task_id)
    history = await budgets.policy_history(task_id)
    return {
        "id": task_id,
        "task_id": task_id,
        "policy_version": policy.version,
        "started_at": policy.started_at.isoformat(),
        "updated_at": policy.updated_at.isoformat(),
        "limits": [_limit_resource(limit) for limit in policy.limits],
        "dimensions": [_dimension_resource(item) for item in snapshot.dimensions],
        "warnings": [item.limit.dimension.value for item in snapshot.dimensions if item.warning],
        "blocking_dimensions": [
            item.limit.dimension.value for item in snapshot.dimensions if item.exhausted
        ],
        "history": [
            {
                "version": version.version,
                "updated_at": version.updated_at.isoformat(),
                "limits": [_limit_resource(limit) for limit in version.limits],
                "provenance": dict(version.provenance),
            }
            for version in history
        ],
        "observed_at": snapshot.observed_at.isoformat(),
        "trace_refs": [f"task:{task_id}"],
    }


def _limit_resource(limit: TaskBudgetLimit) -> dict[str, JsonValue]:
    return {
        "dimension": limit.dimension.value,
        "limit": limit.limit,
        "source": limit.source.value,
        "metric_type": limit.metric_type,
        "unit": limit.unit,
        "warning_fraction": limit.warning_fraction,
        "include_estimated": limit.include_estimated,
        "exhaustion_action": limit.exhaustion_action.value,
        "unavailable_policy": limit.unavailable_policy.value,
    }


def _dimension_resource(item: BudgetDimensionSnapshot) -> dict[str, JsonValue]:
    return {
        "dimension": item.limit.dimension.value,
        "limit": item.limit.limit,
        "consumed": item.consumed,
        "reserved": item.reserved,
        "remaining": item.remaining,
        "fraction": item.fraction,
        "source": item.limit.source.value,
        "metric_type": item.limit.metric_type,
        "unit": item.limit.unit,
        "quality_counts": {
            getattr(quality, "value", str(quality)): count
            for quality, count in item.quality_counts.items()
        },
        "unavailable_count": item.unavailable_count,
        "warning": item.warning,
        "exhausted": item.exhausted,
        "overrun": item.overrun,
    }


def _limits(payload: Mapping[str, JsonValue]) -> tuple[TaskBudgetLimit, ...]:
    raw = payload.get("limits")
    if not isinstance(raw, list) or not raw:
        raise ContractError(ErrorCode.INVALID_REQUEST, "limits must be a non-empty array")
    limits: list[TaskBudgetLimit] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ContractError(ErrorCode.INVALID_REQUEST, f"limits[{index}] must be an object")
        try:
            dimension = BudgetDimension(_required_string(item, "dimension"))
            source = BudgetConsumptionSource(_required_string(item, "source"))
            exhaustion_action = BudgetExhaustionAction(
                _optional_string(item.get("exhaustion_action"), "exhaustion_action") or "block"
            )
            unavailable_policy = UnavailableMetricPolicy(
                _optional_string(item.get("unavailable_policy"), "unavailable_policy") or "block"
            )
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_REQUEST, f"invalid budget limit enum: {exc}"
            ) from exc
        limits.append(
            TaskBudgetLimit(
                dimension=dimension,
                limit=_positive_number(item, "limit"),
                source=source,
                metric_type=_optional_string(item.get("metric_type"), "metric_type"),
                unit=_optional_string(item.get("unit"), "unit"),
                warning_fraction=_optional_fraction(item.get("warning_fraction"), 0.8),
                include_estimated=_optional_bool(item.get("include_estimated"), False),
                exhaustion_action=exhaustion_action,
                unavailable_policy=unavailable_policy,
            )
        )
    return tuple(limits)


def _actor(context: Any) -> Any:
    security = _security()
    if context.actor.actor_type is None:
        return security.infer_actor_identity(context.actor.principal_ref)
    try:
        actor_type = security.ActorType(context.actor.actor_type)
    except ValueError as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, "unsupported actor_type") from exc
    return security.ActorIdentity(context.actor.principal_ref, actor_type)


def _operation(
    context: Any,
    owner_type: str,
    owner_id: str,
    project_id: str | None,
) -> OperationContext:
    return OperationContext(
        correlation_id=context.correlation_id,
        causation_id=context.request_id,
        owner_type=owner_type,
        owner_id=owner_id,
        project_id=project_id,
    )


def _request_provenance(context: Any, operation: str) -> dict[str, JsonValue]:
    return {
        "source": "control-plane",
        "operation": operation,
        "request_id": context.request_id,
        "correlation_id": context.correlation_id,
        "idempotency_key": context.idempotency_key,
    }


def _required_string(payload: Mapping[str, JsonValue], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must be a non-blank string")
    return value


def _optional_string(value: JsonValue, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must be a non-blank string")
    return value


def _positive_number(payload: Mapping[str, JsonValue], field_name: str) -> float:
    value = payload.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must be greater than zero")
    return float(value)


def _optional_fraction(value: JsonValue, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value <= 1:
        raise ContractError(ErrorCode.INVALID_REQUEST, "warning_fraction must be within (0, 1]")
    return float(value)


def _optional_bool(value: JsonValue, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ContractError(ErrorCode.INVALID_REQUEST, "include_estimated must be a boolean")
    return value


__all__ = [
    "TASK_BUDGET_COLLECTION",
    "TASK_BUDGET_COMMANDS",
    "TASK_BUDGET_CONFIGURE_COMMAND",
    "TASK_BUDGET_MODULE",
    "TASK_BUDGET_REVISE_COMMAND",
    "TaskBudgetCommandHandlers",
    "TaskBudgetResourceService",
    "register_task_budget_control_plane",
]