"""Event-sourced repository for durable Goal state and revision history."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any, Protocol, cast, runtime_checkable

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, PlatformEvent
from ai_multi_agent_platform.contracts.interfaces import EventProvider
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import Goal, OwnerRef, Provenance, new_id
from ai_multi_agent_platform.kernel.repository import CommandRecord, EventRepository

from .models import (
    AutonomyPolicy,
    CriterionEvaluation,
    CriterionOperator,
    GoalConstraints,
    GoalCriterionKind,
    GoalCriterionState,
    GoalEvidence,
    GoalProgress,
    GoalReview,
    GoalState,
    GoalStatus,
    GoalTaskLink,
    GoalTaskState,
    ObservationPolicy,
    SuccessCriterion,
    TaskGenerationPolicy,
)


@runtime_checkable
class GoalRepository(Protocol):
    async def get_goal(self, goal_id: str) -> GoalState: ...

    async def get_goal_revision(self, goal_id: str, revision: int) -> GoalState: ...

    async def list_goals(self) -> tuple[GoalState, ...]: ...

    async def find_command(
        self, scope: str, idempotency_key: str, operation: str
    ) -> CommandRecord | None: ...

    async def commit_snapshot(
        self,
        *,
        previous: GoalState | None,
        current: GoalState,
        event_type: str,
        idempotency_key: str,
        operation: str,
        command_scope: str,
        actor_ref: str | None,
        details: dict[str, JsonValue] | None = None,
    ) -> GoalState: ...


class EventSourcedGoalRepository(GoalRepository):
    """Store Goal truth in the same append-only repository used by canonical Tasks/Runs."""

    def __init__(self, events: EventRepository, *, event_sink: EventProvider | None = None) -> None:
        self._events = events
        self._event_sink = event_sink

    async def get_goal(self, goal_id: str) -> GoalState:
        events = await self._events.read_events(goal_id)
        if not events:
            raise ContractError(ErrorCode.NOT_FOUND, f"goal not found: {goal_id}")
        return _reduce(events, goal_id)

    async def get_goal_revision(self, goal_id: str, revision: int) -> GoalState:
        if revision < 1:
            raise ContractError(ErrorCode.INVALID_REQUEST, "goal revision must be >= 1")
        events = await self._events.read_events(goal_id)
        if not events:
            raise ContractError(ErrorCode.NOT_FOUND, f"goal not found: {goal_id}")
        for index, event in enumerate(events, start=1):
            snapshot = _snapshot_from_event(event, stream_revision=index)
            if snapshot is not None and snapshot.revision == revision:
                return snapshot
        raise ContractError(
            ErrorCode.NOT_FOUND,
            f"goal revision not found: {goal_id}@{revision}",
        )

    async def list_goals(self) -> tuple[GoalState, ...]:
        states: list[GoalState] = []
        for stream_id in await self._events.list_stream_ids():
            if not stream_id.startswith("goal_"):
                continue
            events = await self._events.read_events(stream_id)
            if events and any(event.event_type == "goal.created" for event in events):
                states.append(_reduce(events, stream_id))
        return tuple(sorted(states, key=lambda state: state.goal.created_at))

    async def find_command(
        self, scope: str, idempotency_key: str, operation: str
    ) -> CommandRecord | None:
        record = await self._events.find_command(scope, idempotency_key)
        if record is None:
            return None
        if record.operation != operation:
            raise ContractError(
                ErrorCode.CONFLICT,
                "idempotency key was already used for a different Goal operation",
                details={
                    "scope": scope,
                    "idempotency_key": idempotency_key,
                    "existing_operation": record.operation,
                    "requested_operation": operation,
                },
            )
        return record

    async def commit_snapshot(
        self,
        *,
        previous: GoalState | None,
        current: GoalState,
        event_type: str,
        idempotency_key: str,
        operation: str,
        command_scope: str,
        actor_ref: str | None,
        details: dict[str, JsonValue] | None = None,
    ) -> GoalState:
        if not idempotency_key.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "idempotency_key must not be blank")
        existing = await self.find_command(command_scope, idempotency_key, operation)
        if existing is not None:
            if existing.stream_id != current.goal_id:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "idempotent Goal command points at a different Goal",
                )
            return await self.get_goal(existing.result_id)

        expected_revision = 0 if previous is None else previous.stream_revision
        event_id = new_id("event")
        payload: dict[str, JsonValue] = {"snapshot": goal_state_to_json(current)}
        if details:
            payload["details"] = dict(details)
        event = PlatformEvent(
            id=event_id,
            event_type=event_type,
            subject_type="goal",
            subject_id=current.goal_id,
            correlation_id=current.goal_id,
            owner_ref=current.owner_ref,
            project_id=current.project_id,
            causation_id=idempotency_key,
            payload=payload,
            provenance=Provenance(source="goal-service", actor_ref=actor_ref),
        )
        command = CommandRecord(
            scope=command_scope,
            idempotency_key=idempotency_key,
            operation=operation,
            stream_id=current.goal_id,
            result_id=current.goal_id,
            event_id=event.id,
        )
        result = await self._events.commit(
            stream_id=current.goal_id,
            expected_revision=expected_revision,
            events=(event,),
            command=command,
        )
        if not result.applied:
            duplicate = result.command
            if duplicate is None or duplicate.operation != operation:
                raise ContractError(ErrorCode.CONFLICT, "Goal command commit conflicted")
            return await self.get_goal(duplicate.result_id)
        if self._event_sink is not None:
            await self._event_sink.publish(event)
        return replace(current, stream_revision=result.revision)


def _reduce(events: tuple[PlatformEvent, ...], goal_id: str) -> GoalState:
    current: GoalState | None = None
    for index, event in enumerate(events, start=1):
        if event.correlation_id != goal_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                f"event {event.id} is in the wrong Goal stream",
            )
        if event.subject_type != "goal" or event.subject_id != goal_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                f"event {event.id} is not attributed to Goal {goal_id}",
            )
        snapshot = _snapshot_from_event(event, stream_revision=index)
        if snapshot is not None:
            current = snapshot
    if current is None:
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"Goal stream has no snapshot: {goal_id}")
    return current


def _snapshot_from_event(event: PlatformEvent, *, stream_revision: int) -> GoalState | None:
    raw = event.payload.get("snapshot")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, "Goal snapshot must be an object")
    return goal_state_from_json(cast(dict[str, JsonValue], raw), stream_revision=stream_revision)


def goal_state_to_json(state: GoalState) -> dict[str, JsonValue]:
    return {
        "goal_id": state.goal_id,
        "title": state.title,
        "objective": state.objective,
        "owner_ref": {"type": state.owner_ref.type, "id": state.owner_ref.id},
        "project_id": state.project_id,
        "created_at": state.goal.created_at.isoformat(),
        "updated_at": state.goal.updated_at.isoformat(),
        "revision": state.revision,
        "digest": state.digest,
        "status": state.status.value,
        "progress": state.progress.value,
        "success_criteria": [_criterion_to_json(value) for value in state.success_criteria],
        "constraints": _constraints_to_json(state.constraints),
        "observation_policy": _observation_to_json(state.observation_policy),
        "task_generation_policy": _task_generation_to_json(state.task_generation_policy),
        "autonomy_policy": _autonomy_to_json(state.autonomy_policy),
        "deadline": None if state.deadline is None else state.deadline.isoformat(),
        "linked_tasks": [_task_link_to_json(value) for value in state.linked_tasks],
        "evidence": [_evidence_to_json(value) for value in state.evidence],
        "reviews": [_review_to_json(value) for value in state.reviews],
        "consecutive_failed_cycles": state.consecutive_failed_cycles,
        "next_review_at": None if state.next_review_at is None else state.next_review_at.isoformat(),
        "terminal_reason": state.terminal_reason,
        "created_actor_ref": state.created_actor_ref,
        "updated_actor_ref": state.updated_actor_ref,
    }


def goal_state_from_json(payload: dict[str, JsonValue], *, stream_revision: int) -> GoalState:
    owner = _object(payload, "owner_ref")
    owner_type = _string(owner, "type")
    if owner_type not in {"user", "organization", "team", "service"}:
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, "invalid Goal owner type")
    goal = Goal(
        id=_string(payload, "goal_id"),
        title=_string(payload, "title"),
        description=_string(payload, "objective"),
        owner_ref=OwnerRef(type=cast(Any, owner_type), id=_string(owner, "id")),
        project_id=_optional_string(payload, "project_id"),
        created_at=_datetime(payload, "created_at"),
        updated_at=_datetime(payload, "updated_at"),
        provenance=Provenance(
            source="goal-service",
            actor_ref=_optional_string(payload, "created_actor_ref"),
        ),
    )
    return GoalState(
        goal=goal,
        revision=_int(payload, "revision"),
        digest=_string(payload, "digest"),
        status=GoalStatus(_string(payload, "status")),
        progress=GoalProgress(_string(payload, "progress")),
        success_criteria=tuple(
            _criterion_from_json(item) for item in _object_list(payload, "success_criteria")
        ),
        constraints=_constraints_from_json(_object(payload, "constraints")),
        observation_policy=_observation_from_json(_object(payload, "observation_policy")),
        task_generation_policy=_task_generation_from_json(
            _object(payload, "task_generation_policy")
        ),
        autonomy_policy=_autonomy_from_json(_object(payload, "autonomy_policy")),
        deadline=_optional_datetime(payload, "deadline"),
        linked_tasks=tuple(
            _task_link_from_json(item) for item in _object_list(payload, "linked_tasks")
        ),
        evidence=tuple(_evidence_from_json(item) for item in _object_list(payload, "evidence")),
        reviews=tuple(_review_from_json(item) for item in _object_list(payload, "reviews")),
        consecutive_failed_cycles=_int(payload, "consecutive_failed_cycles"),
        next_review_at=_optional_datetime(payload, "next_review_at"),
        terminal_reason=_optional_string(payload, "terminal_reason"),
        created_actor_ref=_optional_string(payload, "created_actor_ref"),
        updated_actor_ref=_optional_string(payload, "updated_actor_ref"),
        stream_revision=stream_revision,
    )


def _criterion_to_json(value: SuccessCriterion) -> dict[str, JsonValue]:
    return {
        "criterion_id": value.criterion_id,
        "kind": value.kind.value,
        "description": value.description,
        "operator": value.operator,
        "target": value.target,
        "required": value.required,
    }


def _criterion_from_json(value: dict[str, JsonValue]) -> SuccessCriterion:
    operator = _string(value, "operator")
    if operator not in {"eq", "gte", "lte", "gt", "lt", "truthy"}:
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, "invalid Goal criterion operator")
    return SuccessCriterion(
        criterion_id=_string(value, "criterion_id"),
        kind=GoalCriterionKind(_string(value, "kind")),
        description=_string(value, "description"),
        operator=cast(CriterionOperator, operator),
        target=value.get("target"),
        required=_bool(value, "required"),
    )


def _constraints_to_json(value: GoalConstraints) -> dict[str, JsonValue]:
    return {
        "requirements": list(value.requirements),
        "out_of_scope": list(value.out_of_scope),
        "risk_requirements": list(value.risk_requirements),
        "data_requirements": list(value.data_requirements),
        "security_requirements": list(value.security_requirements),
    }


def _constraints_from_json(value: dict[str, JsonValue]) -> GoalConstraints:
    return GoalConstraints(
        requirements=_string_tuple(value, "requirements"),
        out_of_scope=_string_tuple(value, "out_of_scope"),
        risk_requirements=_string_tuple(value, "risk_requirements"),
        data_requirements=_string_tuple(value, "data_requirements"),
        security_requirements=_string_tuple(value, "security_requirements"),
    )


def _observation_to_json(value: ObservationPolicy) -> dict[str, JsonValue]:
    return {
        "automation_id": value.automation_id,
        "review_interval_seconds": value.review_interval_seconds,
        "event_types": list(value.event_types),
    }


def _observation_from_json(value: dict[str, JsonValue]) -> ObservationPolicy:
    return ObservationPolicy(
        automation_id=_optional_string(value, "automation_id"),
        review_interval_seconds=_optional_int(value, "review_interval_seconds"),
        event_types=_string_tuple(value, "event_types"),
    )


def _task_generation_to_json(value: TaskGenerationPolicy) -> dict[str, JsonValue]:
    return {
        "enabled": value.enabled,
        "task_title": value.task_title,
        "proposal_required": value.proposal_required,
    }


def _task_generation_from_json(value: dict[str, JsonValue]) -> TaskGenerationPolicy:
    return TaskGenerationPolicy(
        enabled=_bool(value, "enabled"),
        task_title=_optional_string(value, "task_title"),
        proposal_required=_bool(value, "proposal_required"),
    )


def _autonomy_to_json(value: AutonomyPolicy) -> dict[str, JsonValue]:
    return {
        "max_tasks_per_review": value.max_tasks_per_review,
        "max_consecutive_failed_cycles": value.max_consecutive_failed_cycles,
        "human_checkpoint_required": value.human_checkpoint_required,
    }


def _autonomy_from_json(value: dict[str, JsonValue]) -> AutonomyPolicy:
    return AutonomyPolicy(
        max_tasks_per_review=_int(value, "max_tasks_per_review"),
        max_consecutive_failed_cycles=_int(value, "max_consecutive_failed_cycles"),
        human_checkpoint_required=_bool(value, "human_checkpoint_required"),
    )


def _task_link_to_json(value: GoalTaskLink) -> dict[str, JsonValue]:
    return {
        "task_id": value.task_id,
        "goal_revision": value.goal_revision,
        "review_id": value.review_id,
        "task_state": value.task_state.value,
        "valid_for_current_revision": value.valid_for_current_revision,
        "created_at": value.created_at.isoformat(),
    }


def _task_link_from_json(value: dict[str, JsonValue]) -> GoalTaskLink:
    return GoalTaskLink(
        task_id=_string(value, "task_id"),
        goal_revision=_int(value, "goal_revision"),
        review_id=_optional_string(value, "review_id"),
        task_state=GoalTaskState(_string(value, "task_state")),
        valid_for_current_revision=_bool(value, "valid_for_current_revision"),
        created_at=_datetime(value, "created_at"),
    )


def _evidence_to_json(value: GoalEvidence) -> dict[str, JsonValue]:
    return {
        "evidence_id": value.evidence_id,
        "criterion_id": value.criterion_id,
        "kind": value.kind,
        "value": value.value,
        "source_ref": value.source_ref,
        "verified": value.verified,
        "actor_ref": value.actor_ref,
        "observed_at": value.observed_at.isoformat(),
    }


def _evidence_from_json(value: dict[str, JsonValue]) -> GoalEvidence:
    return GoalEvidence(
        evidence_id=_string(value, "evidence_id"),
        criterion_id=_string(value, "criterion_id"),
        kind=_string(value, "kind"),
        value=value.get("value"),
        source_ref=_string(value, "source_ref"),
        verified=_bool(value, "verified"),
        actor_ref=_optional_string(value, "actor_ref"),
        observed_at=_datetime(value, "observed_at"),
    )


def _evaluation_to_json(value: CriterionEvaluation) -> dict[str, JsonValue]:
    return {
        "criterion_id": value.criterion_id,
        "state": value.state.value,
        "evidence_ids": list(value.evidence_ids),
        "reason": value.reason,
    }


def _evaluation_from_json(value: dict[str, JsonValue]) -> CriterionEvaluation:
    return CriterionEvaluation(
        criterion_id=_string(value, "criterion_id"),
        state=GoalCriterionState(_string(value, "state")),
        evidence_ids=_string_tuple(value, "evidence_ids"),
        reason=_string_allow_blank(value, "reason"),
    )


def _review_to_json(value: GoalReview) -> dict[str, JsonValue]:
    return {
        "review_id": value.review_id,
        "goal_revision": value.goal_revision,
        "trigger_ref": value.trigger_ref,
        "criterion_evaluations": [
            _evaluation_to_json(item) for item in value.criterion_evaluations
        ],
        "generated_task_ids": list(value.generated_task_ids),
        "work_required": value.work_required,
        "decision_reason": value.decision_reason,
        "reviewed_at": value.reviewed_at.isoformat(),
    }


def _review_from_json(value: dict[str, JsonValue]) -> GoalReview:
    return GoalReview(
        review_id=_string(value, "review_id"),
        goal_revision=_int(value, "goal_revision"),
        trigger_ref=_string(value, "trigger_ref"),
        criterion_evaluations=tuple(
            _evaluation_from_json(item)
            for item in _object_list(value, "criterion_evaluations")
        ),
        generated_task_ids=_string_tuple(value, "generated_task_ids"),
        work_required=_bool(value, "work_required"),
        decision_reason=_string_allow_blank(value, "decision_reason"),
        reviewed_at=_datetime(value, "reviewed_at"),
    )


def _object(payload: dict[str, JsonValue], key: str) -> dict[str, JsonValue]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"Goal field {key} must be an object")
    return cast(dict[str, JsonValue], value)


def _object_list(payload: dict[str, JsonValue], key: str) -> tuple[dict[str, JsonValue], ...]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"Goal field {key} must be an array")
    result: list[dict[str, JsonValue]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                f"Goal field {key} must contain only objects",
            )
        result.append(cast(dict[str, JsonValue], item))
    return tuple(result)


def _string(payload: dict[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"Goal field {key} must be non-blank")
    return value


def _string_allow_blank(payload: dict[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"Goal field {key} must be a string")
    return value


def _optional_string(payload: dict[str, JsonValue], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"Goal field {key} must be string/null")
    return value


def _int(payload: dict[str, JsonValue], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"Goal field {key} must be integer")
    return value


def _optional_int(payload: dict[str, JsonValue], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"Goal field {key} must be integer/null")
    return value


def _bool(payload: dict[str, JsonValue], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"Goal field {key} must be boolean")
    return value


def _string_tuple(payload: dict[str, JsonValue], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            f"Goal field {key} must be an array of strings",
        )
    return tuple(cast(list[str], value))


def _datetime(payload: dict[str, JsonValue], key: str) -> datetime:
    value = _string(payload, key)
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            f"Goal field {key} has invalid timestamp",
        ) from exc


def _optional_datetime(payload: dict[str, JsonValue], key: str) -> datetime | None:
    value = _optional_string(payload, key)
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            f"Goal field {key} has invalid timestamp",
        ) from exc


__all__ = [
    "EventSourcedGoalRepository",
    "GoalRepository",
    "goal_state_from_json",
    "goal_state_to_json",
]
