"""Portable, non-runnable canonical Task/Run history for issue #79."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, PlatformEvent
from ai_multi_agent_platform.domain import (
    Event,
    ExternalRef,
    OwnerRef,
    Provenance,
    Run,
    RunStatus,
    Task,
    TaskStatus,
)
from ai_multi_agent_platform.kernel.models import RunState, TaskState, TERMINAL_RUN_STATUSES
from ai_multi_agent_platform.kernel.repository import EventRepository
from ai_multi_agent_platform.kernel.state import reduce_run, reduce_task

from .dependencies import resource_dependency
from .models import DependencyRequirement, IdPolicy, PortableResource
from .registry import ImportContext, ResourceExport, ResourceSerializerRegistry

TASK_HISTORY_PORTABLE_SCHEMA_VERSION = "1"
TASK_HISTORY_RESOURCE_TYPE = "task_history"

_TERMINAL_TASK_STATUSES = frozenset(
    {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    }
)


@dataclass(frozen=True, slots=True)
class HistoricalRunSnapshot:
    """One terminal Run projection stripped of live execution authority/state."""

    run: Run
    revision: int
    output: dict[str, JsonValue]
    artifact_ids: tuple[str, ...] = ()
    result_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.revision < 1:
            raise ValueError("historical Run revision must be >= 1")
        if self.run.status not in TERMINAL_RUN_STATUSES:
            raise ValueError("historical Run must be terminal")
        if self.run.trace_id is not None or self.run.worker_id is not None:
            raise ValueError("historical Run must not carry live trace or worker state")
        object.__setattr__(self, "output", dict(self.output))
        object.__setattr__(self, "artifact_ids", tuple(self.artifact_ids))
        object.__setattr__(self, "result_ids", tuple(self.result_ids))


@dataclass(frozen=True, slots=True)
class HistoricalTaskSnapshot:
    """Canonical terminal Task history that can never be activated by portable import."""

    task: Task
    revision: int
    plan_ref: str | None
    step_ids: tuple[str, ...]
    artifact_ids: tuple[str, ...]
    result_ids: tuple[str, ...]
    runs: tuple[HistoricalRunSnapshot, ...]
    events: tuple[PlatformEvent, ...]

    def __post_init__(self) -> None:
        if self.revision < 1:
            raise ValueError("historical Task revision must be >= 1")
        if self.task.status not in _TERMINAL_TASK_STATUSES:
            raise ValueError("only terminal Tasks can be exported as historical portability data")
        run_ids = tuple(item.run.id for item in self.runs)
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("historical Task Run IDs must be unique")
        for item in self.runs:
            if item.run.correlation_id != self.task.id:
                raise ValueError("historical Run correlation must match the Task identity")
        for event in self.events:
            if event.correlation_id != self.task.id:
                raise ValueError("historical Event correlation must match the Task identity")
            if event.trace_id is not None:
                raise ValueError("historical Events must not carry active trace IDs")
        object.__setattr__(self, "step_ids", tuple(self.step_ids))
        object.__setattr__(self, "artifact_ids", tuple(self.artifact_ids))
        object.__setattr__(self, "result_ids", tuple(self.result_ids))
        object.__setattr__(self, "runs", tuple(self.runs))
        object.__setattr__(self, "events", tuple(self.events))

    @property
    def run_ids(self) -> tuple[str, ...]:
        return tuple(item.run.id for item in self.runs)


async def snapshot_task_history(
    events: EventRepository,
    task_id: str,
) -> HistoricalTaskSnapshot:
    """Reduce one canonical stream and snapshot it only after it is fully terminal."""

    stream = await events.read_events(task_id)
    task_state = reduce_task(stream, task_id)
    if task_state.task.status not in _TERMINAL_TASK_STATUSES:
        raise ContractError(
            ErrorCode.CONFLICT,
            "active or waiting Tasks cannot be exported as portable history",
            details={"task_id": task_id, "status": task_state.task.status.value},
        )

    run_snapshots: list[HistoricalRunSnapshot] = []
    for run_id in task_state.run_ids:
        run_state = reduce_run(stream, run_id)
        if run_state.run.status not in TERMINAL_RUN_STATUSES:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Task history contains a non-terminal Run",
                details={"task_id": task_id, "run_id": run_id, "status": run_state.run.status.value},
            )
        run_snapshots.append(_historical_run(run_state))

    sanitized_events = tuple(replace(event, trace_id=None) for event in stream)
    return HistoricalTaskSnapshot(
        task=task_state.task,
        revision=task_state.revision,
        plan_ref=task_state.plan_ref,
        step_ids=task_state.step_ids,
        artifact_ids=task_state.artifact_ids,
        result_ids=task_state.result_ids,
        runs=tuple(run_snapshots),
        events=sanitized_events,
    )


class TaskHistoryPortableCodec:
    resource_type = TASK_HISTORY_RESOURCE_TYPE

    def serialize(self, value: object) -> ResourceExport:
        snapshot = _require_snapshot(value)
        _validate_snapshot(snapshot)
        return ResourceExport(
            resource_id=snapshot.task.id,
            resource_version=TASK_HISTORY_PORTABLE_SCHEMA_VERSION,
            payload={
                "schema_version": TASK_HISTORY_PORTABLE_SCHEMA_VERSION,
                "historical_only": True,
                "task": _task_to_json(snapshot.task),
                "revision": snapshot.revision,
                "plan_ref": snapshot.plan_ref,
                "step_ids": list(snapshot.step_ids),
                "artifact_ids": list(snapshot.artifact_ids),
                "result_ids": list(snapshot.result_ids),
                "runs": [_historical_run_to_json(item) for item in snapshot.runs],
                "events": [_event_to_json(item) for item in snapshot.events],
            },
            id_policy=IdPolicy.HISTORICAL_PRESERVE,
            dependencies=_history_dependencies(snapshot),
        )

    def deserialize(self, resource: PortableResource, context: ImportContext) -> object:
        if resource.resource_type != self.resource_type:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"Task history codec cannot deserialize resource type {resource.resource_type!r}",
            )
        if resource.id_policy is not IdPolicy.HISTORICAL_PRESERVE:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Task history resources must preserve historical canonical identity",
            )
        try:
            if resource.payload.get("schema_version") != TASK_HISTORY_PORTABLE_SCHEMA_VERSION:
                raise ContractError(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    "unsupported portable Task history schema version",
                    details={"supported_schema_version": TASK_HISTORY_PORTABLE_SCHEMA_VERSION},
                )
            if resource.payload.get("historical_only") is not True:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "portable Task history must be explicitly marked historical-only",
                )
            snapshot = _history_from_payload(resource.payload)
            if snapshot.task.id != resource.resource_id:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "portable Task history payload identity disagrees with its resource ID",
                )
            return _remap_history(snapshot, context)
        except ContractError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "invalid portable Task history payload",
                details={"resource_id": resource.resource_id},
            ) from exc


def register_task_history_portability_codec(registry: ResourceSerializerRegistry) -> None:
    registry.register(TaskHistoryPortableCodec())


def _historical_run(state: RunState) -> HistoricalRunSnapshot:
    return HistoricalRunSnapshot(
        run=replace(state.run, trace_id=None, worker_id=None),
        revision=state.revision,
        output=dict(state.output),
        artifact_ids=state.artifact_ids,
        result_ids=state.result_ids,
    )


def _validate_snapshot(snapshot: HistoricalTaskSnapshot) -> None:
    try:
        HistoricalTaskSnapshot(
            task=snapshot.task,
            revision=snapshot.revision,
            plan_ref=snapshot.plan_ref,
            step_ids=snapshot.step_ids,
            artifact_ids=snapshot.artifact_ids,
            result_ids=snapshot.result_ids,
            runs=snapshot.runs,
            events=snapshot.events,
        )
    except ValueError as exc:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "Task history violates the portable historical-only contract",
            details={"task_id": snapshot.task.id},
        ) from exc


def _require_snapshot(value: object) -> HistoricalTaskSnapshot:
    if not isinstance(value, HistoricalTaskSnapshot):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "Task history portable codec requires a HistoricalTaskSnapshot",
        )
    return value


def _history_dependencies(snapshot: HistoricalTaskSnapshot) -> tuple[DependencyRequirement, ...]:
    dependencies: set[DependencyRequirement] = set()
    task = snapshot.task
    if task.project_id is not None:
        dependencies.add(
            resource_dependency("project", task.project_id, purpose="Task history project/privacy scope")
        )
    if task.goal_id is not None:
        dependencies.add(
            resource_dependency(
                "goal",
                task.goal_id,
                purpose="Historical Task goal reference",
                required=False,
            )
        )
    if snapshot.plan_ref is not None:
        dependencies.add(
            resource_dependency(
                "plan",
                snapshot.plan_ref,
                purpose="Historical Task plan reference",
                required=False,
            )
        )
    for step_id in snapshot.step_ids:
        dependencies.add(
            resource_dependency("step", step_id, purpose="Historical Task step", required=False)
        )
    for artifact_id in _all_artifact_ids(snapshot):
        dependencies.add(
            resource_dependency(
                "artifact",
                artifact_id,
                purpose="Historical Task artifact",
                required=False,
            )
        )
    for result_id in _all_result_ids(snapshot):
        dependencies.add(
            resource_dependency(
                "result",
                result_id,
                purpose="Historical Task result",
                required=False,
            )
        )
    return tuple(
        sorted(
            dependencies,
            key=lambda item: (item.kind.value, item.identifier, item.purpose or ""),
        )
    )


def _all_artifact_ids(snapshot: HistoricalTaskSnapshot) -> tuple[str, ...]:
    values = set(snapshot.artifact_ids)
    for item in snapshot.runs:
        values.update(item.artifact_ids)
    return tuple(sorted(values))


def _all_result_ids(snapshot: HistoricalTaskSnapshot) -> tuple[str, ...]:
    values = set(snapshot.result_ids)
    for item in snapshot.runs:
        values.update(item.result_ids)
    return tuple(sorted(values))


def _remap_history(snapshot: HistoricalTaskSnapshot, context: ImportContext) -> HistoricalTaskSnapshot:
    source_task_id = snapshot.task.id
    target_task_id = context.remap(TASK_HISTORY_RESOURCE_TYPE, source_task_id)
    task = replace(
        snapshot.task,
        id=target_task_id,
        goal_id=_remap_optional(context, "goal", snapshot.task.goal_id),
        project_id=_remap_optional(context, "project", snapshot.task.project_id),
    )
    runs = tuple(
        replace(
            item,
            run=replace(
                item.run,
                subject_id=(
                    target_task_id
                    if item.run.subject_type == "task"
                    else context.remap("step", item.run.subject_id)
                ),
                correlation_id=target_task_id,
                project_id=_remap_optional(context, "project", item.run.project_id),
                trace_id=None,
                worker_id=None,
            ),
            artifact_ids=tuple(context.remap("artifact", value) for value in item.artifact_ids),
            result_ids=tuple(context.remap("result", value) for value in item.result_ids),
        )
        for item in snapshot.runs
    )
    events = tuple(
        replace(
            event,
            subject_id=_remap_event_subject(context, event, source_task_id, target_task_id),
            correlation_id=target_task_id,
            project_id=_remap_optional(context, "project", event.project_id),
            trace_id=None,
        )
        for event in snapshot.events
    )
    return HistoricalTaskSnapshot(
        task=task,
        revision=snapshot.revision,
        plan_ref=_remap_optional(context, "plan", snapshot.plan_ref),
        step_ids=tuple(context.remap("step", value) for value in snapshot.step_ids),
        artifact_ids=tuple(context.remap("artifact", value) for value in snapshot.artifact_ids),
        result_ids=tuple(context.remap("result", value) for value in snapshot.result_ids),
        runs=runs,
        events=events,
    )


def _remap_event_subject(
    context: ImportContext,
    event: PlatformEvent,
    source_task_id: str,
    target_task_id: str,
) -> str:
    if event.subject_type == "task" and event.subject_id == source_task_id:
        return target_task_id
    return context.remap(event.subject_type, event.subject_id)


def _remap_optional(context: ImportContext, kind: str, value: str | None) -> str | None:
    if value is None:
        return None
    return context.remap(kind, value)


def _history_from_payload(payload: Mapping[str, JsonValue]) -> HistoricalTaskSnapshot:
    task = _task_from_json(payload.get("task"))
    runs_raw = _array(payload.get("runs"), "runs")
    events_raw = _array(payload.get("events"), "events")
    return HistoricalTaskSnapshot(
        task=task,
        revision=_positive_int(payload.get("revision"), "revision"),
        plan_ref=_optional_string(payload.get("plan_ref"), "plan_ref"),
        step_ids=_string_tuple(payload.get("step_ids"), "step_ids"),
        artifact_ids=_string_tuple(payload.get("artifact_ids"), "artifact_ids"),
        result_ids=_string_tuple(payload.get("result_ids"), "result_ids"),
        runs=tuple(_historical_run_from_json(value) for value in runs_raw),
        events=tuple(_event_from_json(value) for value in events_raw),
    )


def _historical_run_to_json(value: HistoricalRunSnapshot) -> dict[str, JsonValue]:
    return {
        "run": _run_to_json(value.run),
        "revision": value.revision,
        "output": dict(value.output),
        "artifact_ids": list(value.artifact_ids),
        "result_ids": list(value.result_ids),
    }


def _historical_run_from_json(value: JsonValue) -> HistoricalRunSnapshot:
    data = _object(value, "HistoricalRunSnapshot")
    return HistoricalRunSnapshot(
        run=_run_from_json(data.get("run")),
        revision=_positive_int(data.get("revision"), "revision"),
        output=_object(data.get("output"), "output"),
        artifact_ids=_string_tuple(data.get("artifact_ids"), "artifact_ids"),
        result_ids=_string_tuple(data.get("result_ids"), "result_ids"),
    )


def _task_to_json(value: Task) -> dict[str, JsonValue]:
    return {
        "id": value.id,
        "title": value.title,
        "owner_ref": _owner_to_json(value.owner_ref),
        "status": value.status.value,
        "goal_id": value.goal_id,
        "project_id": value.project_id,
        "description": value.description,
        "correlation_id": value.correlation_id,
        "causation_id": value.causation_id,
        "created_at": value.created_at.isoformat(),
        "updated_at": value.updated_at.isoformat(),
        "schema_version": value.schema_version,
        "provenance": _provenance_to_json(value.provenance),
        "external_refs": [_external_ref_to_json(item) for item in value.external_refs],
        "metadata": _require_json_object(value.metadata, "Task.metadata"),
    }


def _task_from_json(value: JsonValue | None) -> Task:
    data = _object(value, "Task")
    return Task(
        id=_string(data, "id"),
        title=_string(data, "title"),
        owner_ref=_owner_from_json(data.get("owner_ref")),
        status=TaskStatus(_string(data, "status")),
        goal_id=_optional_string(data.get("goal_id"), "goal_id"),
        project_id=_optional_string(data.get("project_id"), "project_id"),
        description=_string_allow_blank(data, "description"),
        correlation_id=_optional_string(data.get("correlation_id"), "correlation_id"),
        causation_id=_optional_string(data.get("causation_id"), "causation_id"),
        created_at=_timestamp(data.get("created_at"), "created_at"),
        updated_at=_timestamp(data.get("updated_at"), "updated_at"),
        schema_version=_string(data, "schema_version"),
        provenance=_provenance_from_json(data.get("provenance")),
        external_refs=_external_refs_from_json(data.get("external_refs")),
        metadata=_object(data.get("metadata"), "Task.metadata"),
    )


def _run_to_json(value: Run) -> dict[str, JsonValue]:
    if value.trace_id is not None or value.worker_id is not None:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "portable historical Run contains live trace or worker state",
            details={"run_id": value.id},
        )
    return {
        "id": value.id,
        "subject_type": value.subject_type,
        "subject_id": value.subject_id,
        "owner_ref": _owner_to_json(value.owner_ref),
        "correlation_id": value.correlation_id,
        "attempt": value.attempt,
        "status": value.status.value,
        "project_id": value.project_id,
        "causation_id": value.causation_id,
        "created_at": value.created_at.isoformat(),
        "updated_at": value.updated_at.isoformat(),
        "started_at": None if value.started_at is None else value.started_at.isoformat(),
        "finished_at": None if value.finished_at is None else value.finished_at.isoformat(),
        "schema_version": value.schema_version,
        "provenance": _provenance_to_json(value.provenance),
        "external_refs": [_external_ref_to_json(item) for item in value.external_refs],
        "metadata": _require_json_object(value.metadata, "Run.metadata"),
    }


def _run_from_json(value: JsonValue | None) -> Run:
    data = _object(value, "Run")
    subject_type = _string(data, "subject_type")
    if subject_type not in {"task", "step"}:
        raise ValueError("Run.subject_type must be task or step")
    return Run(
        id=_string(data, "id"),
        subject_type=cast(Any, subject_type),
        subject_id=_string(data, "subject_id"),
        owner_ref=_owner_from_json(data.get("owner_ref")),
        correlation_id=_string(data, "correlation_id"),
        attempt=_positive_int(data.get("attempt"), "attempt"),
        status=RunStatus(_string(data, "status")),
        project_id=_optional_string(data.get("project_id"), "project_id"),
        causation_id=_optional_string(data.get("causation_id"), "causation_id"),
        trace_id=None,
        worker_id=None,
        created_at=_timestamp(data.get("created_at"), "created_at"),
        updated_at=_timestamp(data.get("updated_at"), "updated_at"),
        started_at=_optional_timestamp(data.get("started_at"), "started_at"),
        finished_at=_optional_timestamp(data.get("finished_at"), "finished_at"),
        schema_version=_string(data, "schema_version"),
        provenance=_provenance_from_json(data.get("provenance")),
        external_refs=_external_refs_from_json(data.get("external_refs")),
        metadata=_object(data.get("metadata"), "Run.metadata"),
    )


def _event_to_json(value: PlatformEvent) -> dict[str, JsonValue]:
    if value.trace_id is not None:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "portable historical Event contains an active trace ID",
            details={"event_id": value.id},
        )
    return {
        "id": value.id,
        "event_type": value.event_type,
        "subject_type": value.subject_type,
        "subject_id": value.subject_id,
        "correlation_id": value.correlation_id,
        "owner_ref": None if value.owner_ref is None else _owner_to_json(value.owner_ref),
        "project_id": value.project_id,
        "causation_id": value.causation_id,
        "occurred_at": value.occurred_at.isoformat(),
        "payload": _require_json_object(value.payload, "Event.payload"),
        "schema_version": value.schema_version,
        "provenance": _provenance_to_json(value.provenance),
        "external_refs": [_external_ref_to_json(item) for item in value.external_refs],
    }


def _event_from_json(value: JsonValue) -> Event:
    data = _object(value, "Event")
    raw_owner = data.get("owner_ref")
    return Event(
        id=_string(data, "id"),
        event_type=_string(data, "event_type"),
        subject_type=_string(data, "subject_type"),
        subject_id=_string(data, "subject_id"),
        correlation_id=_string(data, "correlation_id"),
        owner_ref=None if raw_owner is None else _owner_from_json(raw_owner),
        project_id=_optional_string(data.get("project_id"), "project_id"),
        causation_id=_optional_string(data.get("causation_id"), "causation_id"),
        trace_id=None,
        occurred_at=_timestamp(data.get("occurred_at"), "occurred_at"),
        payload=_object(data.get("payload"), "Event.payload"),
        schema_version=_string(data, "schema_version"),
        provenance=_provenance_from_json(data.get("provenance")),
        external_refs=_external_refs_from_json(data.get("external_refs")),
    )


def _owner_to_json(value: OwnerRef) -> dict[str, JsonValue]:
    return {"type": value.type, "id": value.id}


def _owner_from_json(value: JsonValue | None) -> OwnerRef:
    data = _object(value, "OwnerRef")
    owner_type = _string(data, "type")
    if owner_type not in {"user", "organization", "team", "service"}:
        raise ValueError("unsupported OwnerRef.type")
    return OwnerRef(type=cast(Any, owner_type), id=_string(data, "id"))


def _external_ref_to_json(value: ExternalRef) -> dict[str, JsonValue]:
    return {"system": value.system, "kind": value.kind, "value": value.value}


def _external_refs_from_json(value: JsonValue | None) -> tuple[ExternalRef, ...]:
    return tuple(
        ExternalRef(
            system=_string(_object(item, "ExternalRef"), "system"),
            kind=_string(_object(item, "ExternalRef"), "kind"),
            value=_string(_object(item, "ExternalRef"), "value"),
        )
        for item in _array(value, "external_refs")
    )


def _provenance_to_json(value: Provenance | None) -> JsonValue:
    if value is None:
        return None
    return {
        "source": value.source,
        "actor_ref": value.actor_ref,
        "details": _require_json_object(value.details, "Provenance.details"),
    }


def _provenance_from_json(value: JsonValue | None) -> Provenance | None:
    if value is None:
        return None
    data = _object(value, "Provenance")
    return Provenance(
        source=_string(data, "source"),
        actor_ref=_optional_string(data.get("actor_ref"), "actor_ref"),
        details=_object(data.get("details"), "Provenance.details"),
    )


def _require_json_object(value: Mapping[str, Any], field_name: str) -> dict[str, JsonValue]:
    copied = dict(value)
    if not all(isinstance(key, str) and _is_json_value(item) for key, item in copied.items()):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"{field_name} contains non-portable non-JSON values",
        )
    return cast(dict[str, JsonValue], copied)


def _object(value: object, field_name: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    if not all(isinstance(key, str) and _is_json_value(item) for key, item in value.items()):
        raise ValueError(f"{field_name} contains non-JSON values")
    return cast(dict[str, JsonValue], value)


def _array(value: object, field_name: str) -> list[JsonValue]:
    if not isinstance(value, list) or not all(_is_json_value(item) for item in value):
        raise ValueError(f"{field_name} must be a JSON array")
    return cast(list[JsonValue], value)


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list | tuple):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


def _string(data: Mapping[str, JsonValue], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string")
    return value


def _string_allow_blank(data: Mapping[str, JsonValue], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _optional_string(value: JsonValue | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string or null")
    return value


def _string_tuple(value: JsonValue | None, field_name: str) -> tuple[str, ...]:
    raw = _array(value, field_name)
    if not all(isinstance(item, str) and item.strip() for item in raw):
        raise ValueError(f"{field_name} must contain only non-blank strings")
    return tuple(cast(list[str], raw))


def _positive_int(value: JsonValue | None, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _timestamp(value: JsonValue | None, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed


def _optional_timestamp(value: JsonValue | None, field_name: str) -> datetime | None:
    if value is None:
        return None
    return _timestamp(value, field_name)
