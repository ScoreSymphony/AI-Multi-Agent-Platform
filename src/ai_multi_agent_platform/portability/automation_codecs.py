"""Portable canonical Automation definitions for issue #79."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import cast

from ai_multi_agent_platform.automation.models import (
    Automation,
    AutomationState,
    IdentityContext,
    MissedSchedulePolicy,
    OverlapPolicy,
    RetryPolicy,
    TaskTemplate,
    TriggerDefinition,
    TriggerType,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .dependencies import resource_dependency
from .models import (
    DependencyKind,
    DependencyRequirement,
    ExcludedState,
    ExclusionCategory,
    IdPolicy,
    PortableResource,
)
from .registry import ImportContext, ResourceExport, ResourceSerializerRegistry

AUTOMATION_PORTABLE_SCHEMA_VERSION = "1"
AUTOMATION_RESOURCE_TYPE = "automation"


@dataclass(frozen=True, slots=True)
class AutomationPortableSnapshot:
    """Portable configuration plus source lifecycle intent, never scheduler progress."""

    automation: Automation
    source_state: AutomationState

    def __post_init__(self) -> None:
        if self.automation.last_evaluated_at is not None:
            raise ValueError("portable Automation cannot carry last_evaluated_at")
        if self.automation.next_evaluation_at is not None:
            raise ValueError("portable Automation cannot carry next_evaluation_at")
        expected_state = _safe_target_state(self.source_state)
        if self.automation.state is not expected_state:
            raise ValueError("portable Automation must use its safe non-running target state")


def snapshot_automation(automation: Automation) -> AutomationPortableSnapshot:
    """Project an Automation into portable configuration without scheduler cursor state."""

    target_state = _safe_target_state(automation.state)
    if target_state is AutomationState.INVALID:
        portable = replace(
            automation,
            last_evaluated_at=None,
            next_evaluation_at=None,
        )
    else:
        portable = replace(
            automation,
            state=target_state,
            last_evaluated_at=None,
            next_evaluation_at=None,
            invalidation_reason_code=None,
            invalidated_at=None,
            state_before_invalid=None,
        )
    return AutomationPortableSnapshot(automation=portable, source_state=automation.state)


def automation_runtime_exclusions(automation_id: str) -> tuple[ExcludedState, ...]:
    """Describe runtime scheduler/delivery state intentionally omitted from portability."""

    return (
        ExcludedState(
            category=ExclusionCategory.BACKEND_RUNTIME_STATE,
            path="$.automation.scheduler_progress",
            reason=(
                "last/next evaluation cursor state is recomputed by the destination scheduler "
                "after explicit activation"
            ),
            resource_type=AUTOMATION_RESOURCE_TYPE,
            resource_id=automation_id,
        ),
        ExcludedState(
            category=ExclusionCategory.BACKEND_RUNTIME_STATE,
            path="$.automation.trigger_deliveries",
            reason=(
                "trigger delivery processing/retry state is execution history and is not replayed "
                "by portable Automation import"
            ),
            resource_type=AUTOMATION_RESOURCE_TYPE,
            resource_id=automation_id,
        ),
    )


class AutomationPortableCodec:
    resource_type = AUTOMATION_RESOURCE_TYPE

    def __init__(self, *, id_policy: IdPolicy = IdPolicy.PRESERVE) -> None:
        self.id_policy = id_policy

    def serialize(self, value: object) -> ResourceExport:
        snapshot = _require_snapshot(value)
        return ResourceExport(
            resource_id=snapshot.automation.id,
            resource_version=AUTOMATION_PORTABLE_SCHEMA_VERSION,
            payload={
                "schema_version": AUTOMATION_PORTABLE_SCHEMA_VERSION,
                "source_state": snapshot.source_state.value,
                "automation": _automation_to_json(snapshot.automation),
                "activation_required": snapshot.source_state is AutomationState.ENABLED,
                "runtime_state_included": False,
            },
            id_policy=self.id_policy,
            dependencies=_automation_dependencies(snapshot.automation),
        )

    def deserialize(self, resource: PortableResource, context: ImportContext) -> object:
        if resource.resource_type != self.resource_type:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"Automation codec cannot deserialize resource type {resource.resource_type!r}",
            )
        try:
            if resource.payload.get("schema_version") != AUTOMATION_PORTABLE_SCHEMA_VERSION:
                raise ContractError(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    "unsupported portable Automation schema version",
                    details={"supported_schema_version": AUTOMATION_PORTABLE_SCHEMA_VERSION},
                )
            if resource.payload.get("runtime_state_included") is not False:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "portable Automation must explicitly exclude scheduler runtime state",
                )
            source_state = AutomationState(_string_value(resource.payload.get("source_state")))
            automation = _automation_from_json(resource.payload.get("automation"))
            if automation.id != resource.resource_id:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "portable Automation payload identity disagrees with resource ID",
                )
            target_id = context.remap(AUTOMATION_RESOURCE_TYPE, automation.id)
            project_id = _remap_optional(context, "project", automation.project_id)
            workspace_id = _remap_optional(context, "workspace", automation.workspace_id)
            task_template = replace(
                automation.task_template,
                project_id=_remap_optional(
                    context,
                    "project",
                    automation.task_template.project_id,
                ),
                workspace_id=_remap_optional(
                    context,
                    "workspace",
                    automation.task_template.workspace_id,
                ),
            )
            remapped = replace(
                automation,
                id=target_id,
                project_id=project_id,
                workspace_id=workspace_id,
                task_template=task_template,
                last_evaluated_at=None,
                next_evaluation_at=None,
            )
            return AutomationPortableSnapshot(automation=remapped, source_state=source_state)
        except ContractError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "invalid portable Automation payload",
                details={"resource_id": resource.resource_id},
            ) from exc


def register_automation_portability_codec(
    registry: ResourceSerializerRegistry,
    *,
    id_policy: IdPolicy = IdPolicy.PRESERVE,
) -> None:
    registry.register(AutomationPortableCodec(id_policy=id_policy))


def _safe_target_state(source_state: AutomationState) -> AutomationState:
    if source_state is AutomationState.ENABLED:
        return AutomationState.PAUSED
    return source_state


def _require_snapshot(value: object) -> AutomationPortableSnapshot:
    if not isinstance(value, AutomationPortableSnapshot):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "Automation portable codec requires an AutomationPortableSnapshot",
        )
    return value


def _automation_dependencies(automation: Automation) -> tuple[DependencyRequirement, ...]:
    dependencies: set[DependencyRequirement] = set()
    for project_id in (automation.project_id, automation.task_template.project_id):
        if project_id is not None:
            dependencies.add(
                resource_dependency(
                    "project",
                    project_id,
                    purpose="Automation project/task-template scope",
                )
            )
    for workspace_id in (automation.workspace_id, automation.task_template.workspace_id):
        if workspace_id is not None:
            dependencies.add(
                resource_dependency(
                    "workspace",
                    workspace_id,
                    purpose="Automation workspace/task-template scope",
                )
            )
    if automation.trigger.verification_ref is not None:
        dependencies.add(
            DependencyRequirement(
                kind=DependencyKind.SECRET,
                identifier=automation.trigger.verification_ref,
                required=True,
                purpose="Webhook verification secret reference",
            )
        )
    return tuple(
        sorted(
            dependencies,
            key=lambda item: (item.kind.value, item.identifier, item.purpose or ""),
        )
    )


def _automation_to_json(automation: Automation) -> dict[str, JsonValue]:
    return {
        "id": automation.id,
        "name": automation.name,
        "description": automation.description,
        "identity": {
            "principal_ref": automation.identity.principal_ref,
            "owner_type": automation.identity.owner_type,
            "owner_id": automation.identity.owner_id,
        },
        "trigger": _trigger_to_json(automation.trigger),
        "task_template": _task_template_to_json(automation.task_template),
        "project_id": automation.project_id,
        "workspace_id": automation.workspace_id,
        "state": automation.state.value,
        "deduplication_strategy": automation.deduplication_strategy,
        "retry_policy": {
            "max_attempts": automation.retry_policy.max_attempts,
            "base_backoff_seconds": automation.retry_policy.base_backoff_seconds,
        },
        "overlap_policy": automation.overlap_policy.value,
        "created_at": automation.created_at.isoformat(),
        "updated_at": automation.updated_at.isoformat(),
        "revision": automation.revision,
        "invalidation_reason_code": automation.invalidation_reason_code,
        "invalidated_at": (
            None if automation.invalidated_at is None else automation.invalidated_at.isoformat()
        ),
        "state_before_invalid": (
            None
            if automation.state_before_invalid is None
            else automation.state_before_invalid.value
        ),
    }


def _automation_from_json(value: JsonValue | None) -> Automation:
    data = _object(value, "Automation")
    identity = _object(data.get("identity"), "Automation.identity")
    retry = _object(data.get("retry_policy"), "Automation.retry_policy")
    state = AutomationState(_string(data, "state"))
    return Automation(
        id=_string(data, "id"),
        name=_string(data, "name"),
        description=_string_allow_blank(data, "description"),
        identity=IdentityContext(
            principal_ref=_string(identity, "principal_ref"),
            owner_type=_string(identity, "owner_type"),
            owner_id=_string(identity, "owner_id"),
        ),
        trigger=_trigger_from_json(data.get("trigger")),
        task_template=_task_template_from_json(data.get("task_template")),
        project_id=_optional_string(data.get("project_id"), "project_id"),
        workspace_id=_optional_string(data.get("workspace_id"), "workspace_id"),
        state=state,
        deduplication_strategy=_string(data, "deduplication_strategy"),
        retry_policy=RetryPolicy(
            max_attempts=_positive_int(retry.get("max_attempts"), "max_attempts"),
            base_backoff_seconds=_nonnegative_float(
                retry.get("base_backoff_seconds"), "base_backoff_seconds"
            ),
        ),
        overlap_policy=OverlapPolicy(_string(data, "overlap_policy")),
        created_at=_timestamp(data.get("created_at"), "created_at"),
        updated_at=_timestamp(data.get("updated_at"), "updated_at"),
        revision=_positive_int(data.get("revision"), "revision"),
        last_evaluated_at=None,
        next_evaluation_at=None,
        invalidation_reason_code=_optional_string(
            data.get("invalidation_reason_code"), "invalidation_reason_code"
        ),
        invalidated_at=_optional_timestamp(data.get("invalidated_at"), "invalidated_at"),
        state_before_invalid=_optional_automation_state(data.get("state_before_invalid")),
    )


def _trigger_to_json(trigger: TriggerDefinition) -> dict[str, JsonValue]:
    return {
        "type": trigger.type.value,
        "timezone": trigger.timezone,
        "at": None if trigger.at is None else trigger.at.isoformat(),
        "interval_seconds": trigger.interval_seconds,
        "event_type": trigger.event_type,
        "filters": dict(trigger.filters),
        "webhook_source": trigger.webhook_source,
        "verification_ref": trigger.verification_ref,
        "missed_schedule_policy": trigger.missed_schedule_policy.value,
    }


def _trigger_from_json(value: JsonValue | None) -> TriggerDefinition:
    data = _object(value, "TriggerDefinition")
    return TriggerDefinition(
        type=TriggerType(_string(data, "type")),
        timezone=_string(data, "timezone"),
        at=_optional_timestamp(data.get("at"), "trigger.at"),
        interval_seconds=_optional_float(data.get("interval_seconds"), "interval_seconds"),
        event_type=_optional_string(data.get("event_type"), "event_type"),
        filters=_object(data.get("filters"), "TriggerDefinition.filters"),
        webhook_source=_optional_string(data.get("webhook_source"), "webhook_source"),
        verification_ref=_optional_string(data.get("verification_ref"), "verification_ref"),
        missed_schedule_policy=MissedSchedulePolicy(_string(data, "missed_schedule_policy")),
    )


def _task_template_to_json(template: TaskTemplate) -> dict[str, JsonValue]:
    return {
        "title": template.title,
        "objective": template.objective,
        "project_id": template.project_id,
        "workspace_id": template.workspace_id,
        "payload": dict(template.payload),
    }


def _task_template_from_json(value: JsonValue | None) -> TaskTemplate:
    data = _object(value, "TaskTemplate")
    return TaskTemplate(
        title=_string(data, "title"),
        objective=_string(data, "objective"),
        project_id=_optional_string(data.get("project_id"), "task_template.project_id"),
        workspace_id=_optional_string(data.get("workspace_id"), "task_template.workspace_id"),
        payload=_object(data.get("payload"), "TaskTemplate.payload"),
    )


def _remap_optional(context: ImportContext, kind: str, value: str | None) -> str | None:
    if value is None:
        return None
    return context.remap(kind, value)


def _object(value: object, field_name: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    if not all(isinstance(key, str) and _is_json_value(item) for key, item in value.items()):
        raise ValueError(f"{field_name} contains non-JSON values")
    return cast(dict[str, JsonValue], value)


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


def _string(data: dict[str, JsonValue], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string")
    return value


def _string_value(value: JsonValue | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("value must be a non-blank string")
    return value


def _string_allow_blank(data: dict[str, JsonValue], key: str) -> str:
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


def _positive_int(value: JsonValue | None, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _nonnegative_float(value: JsonValue | None, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be numeric")
    result = float(value)
    if result < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return result


def _optional_float(value: JsonValue | None, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be numeric or null")
    return float(value)


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


def _optional_automation_state(value: JsonValue | None) -> AutomationState | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("state_before_invalid must be a string or null")
    return AutomationState(value)
