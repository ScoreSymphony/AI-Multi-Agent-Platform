"""Control Plane resource and command surface for durable Goal lifecycle (#597)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.goals import (
    AutonomyPolicy,
    GoalConstraints,
    GoalCriterionKind,
    GoalEvidence,
    GoalService,
    GoalState,
    GoalTaskState,
    ObservationPolicy,
    SuccessCriterion,
    TaskGenerationPolicy,
    TaskRevisionPolicy,
)
from ai_multi_agent_platform.goals.repository import goal_state_to_json

from .extensions import CommandHandler, ResourceService
from .models import PageQuery, RequestContext

GOAL_COLLECTION = "goals"
GOAL_COMMANDS = (
    "goal.create",
    "goal.activate",
    "goal.pause",
    "goal.resume",
    "goal.cancel",
    "goal.revise",
    "goal.review",
    "goal.attach-task",
    "goal.record-task-outcome",
)


class GoalResourceService(ResourceService):
    def __init__(self, service: GoalService) -> None:
        self._service = service

    async def list_resources(
        self, context: RequestContext, query: PageQuery
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(_goal_resource(item) for item in await self._service.list_goals())

    async def get_resource(self, context: RequestContext, resource_id: str) -> dict[str, JsonValue]:
        del context
        return _goal_resource(await self._service.get_goal(resource_id))


def goal_resource_services(service: GoalService) -> dict[str, ResourceService]:
    return {GOAL_COLLECTION: GoalResourceService(service)}


def goal_command_handlers(service: GoalService) -> dict[str, CommandHandler]:
    async def create(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if resource_ref != GOAL_COLLECTION:
            raise ContractError(
                ErrorCode.INVALID_REQUEST, "goal.create resource_ref must be 'goals'"
            )
        owner_type = context.actor.owner_type
        owner_id = context.actor.owner_id
        if owner_type is None or owner_id is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Goal creation requires canonical owner context",
            )
        raw_deadline = _optional_string(payload, "deadline")
        state = await service.create_goal(
            idempotency_key=_idempotency(context),
            title=_required_string(payload, "title"),
            objective=_required_string(payload, "objective"),
            owner_ref=OwnerRef(type=cast(Any, owner_type), id=owner_id),
            project_id=_optional_string(payload, "project_id"),
            success_criteria=_parse_criteria(_required_array(payload, "success_criteria")),
            constraints=_parse_constraints(_optional_object(payload, "constraints")),
            observation_policy=_parse_observation(_optional_object(payload, "observation_policy")),
            task_generation_policy=_parse_task_generation(
                _optional_object(payload, "task_generation_policy")
            ),
            autonomy_policy=_parse_autonomy(_optional_object(payload, "autonomy_policy")),
            deadline=None if raw_deadline is None else _parse_datetime(raw_deadline, "deadline"),
            actor_ref=context.actor.principal_ref,
            goal_id=_optional_string(payload, "goal_id"),
        )
        return _goal_resource(state)

    async def activate(
        context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        del payload
        return _goal_resource(
            await service.activate_goal(
                goal_id=resource_ref,
                idempotency_key=_idempotency(context),
                actor_ref=context.actor.principal_ref,
            )
        )

    async def pause(
        context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        del payload
        return _goal_resource(
            await service.pause_goal(
                goal_id=resource_ref,
                idempotency_key=_idempotency(context),
                actor_ref=context.actor.principal_ref,
            )
        )

    async def resume(
        context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        del payload
        return _goal_resource(
            await service.resume_goal(
                goal_id=resource_ref,
                idempotency_key=_idempotency(context),
                actor_ref=context.actor.principal_ref,
            )
        )

    async def cancel(
        context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        return _goal_resource(
            await service.cancel_goal(
                goal_id=resource_ref,
                idempotency_key=_idempotency(context),
                reason=_required_string(payload, "reason"),
                actor_ref=context.actor.principal_ref,
            )
        )

    async def revise(
        context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        deadline_present = "deadline" in payload
        raw_deadline = _optional_string(payload, "deadline")
        raw_criteria = payload.get("success_criteria")
        if raw_criteria is not None and not isinstance(raw_criteria, list):
            raise ContractError(ErrorCode.INVALID_REQUEST, "success_criteria must be an array")
        active_task_policy = _optional_string(payload, "active_task_policy") or "retain"
        if active_task_policy not in {"retain", "supersede"}:
            raise ContractError(ErrorCode.INVALID_REQUEST, "invalid active_task_policy")
        state = await service.revise_goal(
            goal_id=resource_ref,
            idempotency_key=_idempotency(context),
            expected_revision=_required_int(payload, "expected_revision"),
            title=_optional_string(payload, "title"),
            objective=_optional_string(payload, "objective"),
            success_criteria=(None if raw_criteria is None else _parse_criteria(raw_criteria)),
            constraints=(
                None
                if "constraints" not in payload
                else _parse_constraints(_object(payload["constraints"], "constraints"))
            ),
            observation_policy=(
                None
                if "observation_policy" not in payload
                else _parse_observation(
                    _object(payload["observation_policy"], "observation_policy")
                )
            ),
            task_generation_policy=(
                None
                if "task_generation_policy" not in payload
                else _parse_task_generation(
                    _object(payload["task_generation_policy"], "task_generation_policy")
                )
            ),
            autonomy_policy=(
                None
                if "autonomy_policy" not in payload
                else _parse_autonomy(_object(payload["autonomy_policy"], "autonomy_policy"))
            ),
            deadline=None if raw_deadline is None else _parse_datetime(raw_deadline, "deadline"),
            replace_deadline=deadline_present,
            active_task_policy=cast(TaskRevisionPolicy, active_task_policy),
            reopen_terminal=_optional_bool(payload, "reopen_terminal") or False,
            actor_ref=context.actor.principal_ref,
        )
        return _goal_resource(state)

    async def review(
        context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        raw_next = _optional_string(payload, "next_review_at")
        raw_evidence = payload.get("evidence", [])
        if not isinstance(raw_evidence, list):
            raise ContractError(ErrorCode.INVALID_REQUEST, "evidence must be an array")
        return _goal_resource(
            await service.review_goal(
                goal_id=resource_ref,
                idempotency_key=_idempotency(context),
                expected_revision=_required_int(payload, "expected_revision"),
                trigger_ref=_required_string(payload, "trigger_ref"),
                evidence=tuple(_parse_evidence(item) for item in raw_evidence),
                next_review_at=(
                    None if raw_next is None else _parse_datetime(raw_next, "next_review_at")
                ),
                actor_ref=context.actor.principal_ref,
            )
        )

    async def attach_task(
        context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        return _goal_resource(
            await service.attach_task(
                goal_id=resource_ref,
                task_id=_required_string(payload, "task_id"),
                expected_revision=_required_int(payload, "expected_revision"),
                idempotency_key=_idempotency(context),
                actor_ref=context.actor.principal_ref,
            )
        )

    async def record_task_outcome(
        context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        try:
            task_state = GoalTaskState(_required_string(payload, "task_state"))
        except ValueError as exc:
            raise ContractError(ErrorCode.INVALID_REQUEST, "invalid Goal Task outcome") from exc
        return _goal_resource(
            await service.record_task_outcome(
                goal_id=resource_ref,
                task_id=_required_string(payload, "task_id"),
                task_state=task_state,
                idempotency_key=_idempotency(context),
                actor_ref=context.actor.principal_ref,
            )
        )

    return {
        "goal.create": create,
        "goal.activate": activate,
        "goal.pause": pause,
        "goal.resume": resume,
        "goal.cancel": cancel,
        "goal.revise": revise,
        "goal.review": review,
        "goal.attach-task": attach_task,
        "goal.record-task-outcome": record_task_outcome,
    }


def _goal_resource(state: GoalState) -> dict[str, JsonValue]:
    resource = goal_state_to_json(state)
    resource["id"] = state.goal_id
    resource["type"] = "goal"
    resource["version"] = str(state.revision)
    resource["active_task_ids"] = list(state.active_task_ids)
    resource["stream_revision"] = state.stream_revision
    return resource


def _parse_criteria(values: list[JsonValue]) -> tuple[SuccessCriterion, ...]:
    result: list[SuccessCriterion] = []
    for raw in values:
        value = _object(raw, "success_criteria[]")
        try:
            kind = GoalCriterionKind(_required_string(value, "kind"))
        except ValueError as exc:
            raise ContractError(ErrorCode.INVALID_REQUEST, "invalid Goal criterion kind") from exc
        operator = _optional_string(value, "operator") or "truthy"
        if operator not in {"eq", "gte", "lte", "gt", "lt", "truthy"}:
            raise ContractError(ErrorCode.INVALID_REQUEST, "invalid Goal criterion operator")
        result.append(
            SuccessCriterion(
                criterion_id=_required_string(value, "criterion_id"),
                kind=kind,
                description=_required_string(value, "description"),
                operator=cast(Any, operator),
                target=value.get("target", True),
                required=_optional_bool(value, "required") is not False,
            )
        )
    if not result:
        raise ContractError(ErrorCode.INVALID_REQUEST, "success_criteria must not be empty")
    return tuple(result)


def _parse_constraints(value: dict[str, JsonValue] | None) -> GoalConstraints:
    if value is None:
        return GoalConstraints()
    return GoalConstraints(
        requirements=_string_tuple(value, "requirements"),
        out_of_scope=_string_tuple(value, "out_of_scope"),
        risk_requirements=_string_tuple(value, "risk_requirements"),
        data_requirements=_string_tuple(value, "data_requirements"),
        security_requirements=_string_tuple(value, "security_requirements"),
    )


def _parse_observation(value: dict[str, JsonValue] | None) -> ObservationPolicy:
    if value is None:
        return ObservationPolicy()
    return ObservationPolicy(
        automation_id=_optional_string(value, "automation_id"),
        review_interval_seconds=_optional_int(value, "review_interval_seconds"),
        event_types=_string_tuple(value, "event_types"),
    )


def _parse_task_generation(value: dict[str, JsonValue] | None) -> TaskGenerationPolicy:
    if value is None:
        return TaskGenerationPolicy()
    return TaskGenerationPolicy(
        enabled=_optional_bool(value, "enabled") is not False,
        task_title=_optional_string(value, "task_title"),
        proposal_required=_optional_bool(value, "proposal_required") or False,
    )


def _parse_autonomy(value: dict[str, JsonValue] | None) -> AutonomyPolicy:
    if value is None:
        return AutonomyPolicy()
    max_tasks = _optional_int(value, "max_tasks_per_review")
    failed_cycles = _optional_int(value, "max_consecutive_failed_cycles")
    return AutonomyPolicy(
        max_tasks_per_review=1 if max_tasks is None else max_tasks,
        max_consecutive_failed_cycles=3 if failed_cycles is None else failed_cycles,
        human_checkpoint_required=_optional_bool(value, "human_checkpoint_required") or False,
    )


def _parse_evidence(raw: JsonValue) -> GoalEvidence:
    value = _object(raw, "evidence[]")
    observed = _optional_string(value, "observed_at")
    evidence_id = _optional_string(value, "evidence_id")
    kwargs: dict[str, Any] = {
        "criterion_id": _required_string(value, "criterion_id"),
        "kind": _required_string(value, "kind"),
        "value": value.get("value"),
        "source_ref": _required_string(value, "source_ref"),
        "verified": _required_bool(value, "verified"),
        "actor_ref": _optional_string(value, "actor_ref"),
    }
    if observed is not None:
        kwargs["observed_at"] = _parse_datetime(observed, "observed_at")
    if evidence_id is not None:
        kwargs["evidence_id"] = evidence_id
    return GoalEvidence(**kwargs)


def _idempotency(context: RequestContext) -> str:
    if context.idempotency_key is None:
        raise ContractError(ErrorCode.INVALID_REQUEST, "Idempotency-Key is required")
    return context.idempotency_key


def _object(value: JsonValue, field: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field} must be an object")
    return value


def _required_array(payload: dict[str, JsonValue], key: str) -> list[JsonValue]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be an array")
    return value


def _optional_object(payload: dict[str, JsonValue], key: str) -> dict[str, JsonValue] | None:
    value = payload.get(key)
    if value is None:
        return None
    return _object(value, key)


def _required_string(payload: dict[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string")
    return value


def _optional_string(payload: dict[str, JsonValue], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string/null")
    return value


def _required_int(payload: dict[str, JsonValue], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be an integer")
    return value


def _optional_int(payload: dict[str, JsonValue], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be an integer/null")
    return value


def _required_bool(payload: dict[str, JsonValue], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be boolean")
    return value


def _optional_bool(payload: dict[str, JsonValue], key: str) -> bool | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be boolean/null")
    return value


def _string_tuple(payload: dict[str, JsonValue], key: str) -> tuple[str, ...]:
    value = payload.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be an array of strings")
    return tuple(cast(list[str], value))


def _parse_datetime(value: str, field: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field} must be ISO-8601") from exc


__all__ = [
    "GOAL_COLLECTION",
    "GOAL_COMMANDS",
    "GoalResourceService",
    "goal_command_handlers",
    "goal_resource_services",
]
