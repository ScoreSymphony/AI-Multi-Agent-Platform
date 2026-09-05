"""Concrete reusable Template integration for canonical Automations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from ai_multi_agent_platform.automation import (
    Automation,
    AutomationService,
    IdentityContext,
    MissedSchedulePolicy,
    OverlapPolicy,
    RetryPolicy,
    TaskTemplate,
    TriggerDefinition,
    TriggerType,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import FrozenJsonValue, JsonValue
from ai_multi_agent_platform.domain import OwnerRef

from .application import ContextualTemplateHandlerRegistry, TemplateInstantiationContext
from .models import (
    TemplateConfiguration,
    TemplateContent,
    TemplateInstantiationProvenance,
    TemplateProvenance,
    TemplateRequirements,
    TemplateResourceChange,
    TemplateResourceRef,
    TemplateRevision,
    TemplateTrust,
    TemplateType,
)
from .service import TemplateService


@dataclass(frozen=True, slots=True)
class _AutomationConfiguration:
    name: str
    description: str
    trigger: TriggerDefinition
    task_template: TaskTemplate
    project_id: str | None
    workspace_id: str | None
    deduplication_strategy: str
    retry_policy: RetryPolicy
    overlap_policy: OverlapPolicy


@dataclass(slots=True)
class AutomationTemplateHandler:
    """Instantiate ordinary canonical Automation resources through AutomationService."""

    service: AutomationService
    template_type = TemplateType.AUTOMATION

    def preview(self, revision: TemplateRevision) -> tuple[TemplateResourceChange, ...]:
        _configuration(revision)
        return (
            TemplateResourceChange(
                resource_type="automation",
                action="create",
                description=f"Create Automation from {revision.template_id}@{revision.revision}",
            ),
        )

    async def instantiate(
        self,
        revision: TemplateRevision,
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> tuple[TemplateResourceRef, ...]:
        del context
        config = _configuration(revision)
        owner = provenance.applied_by
        automation = await self.service.create_automation(
            name=config.name,
            description=config.description,
            identity=IdentityContext(
                principal_ref=owner.id,
                owner_type=owner.type,
                owner_id=owner.id,
            ),
            trigger=config.trigger,
            task_template=config.task_template,
            project_id=config.project_id,
            workspace_id=config.workspace_id,
            deduplication_strategy=config.deduplication_strategy,
            retry_policy=config.retry_policy,
            overlap_policy=config.overlap_policy,
        )
        return (TemplateResourceRef(resource_type="automation", resource_id=automation.id),)


@dataclass(slots=True)
class AutomationTemplateExporter:
    """Snapshot reusable Automation configuration without runtime identity or source scope."""

    automations: AutomationService
    templates: TemplateService

    async def create_from_automation(
        self,
        automation_id: str,
        *,
        owner_ref: OwnerRef,
        author: str,
        name: str | None = None,
    ) -> TemplateRevision:
        source = await self.automations.get_automation(automation_id)
        content = TemplateContent(
            name=name or source.name,
            description=f"Template exported from Automation {source.id}@{source.revision}",
            template_type=TemplateType.AUTOMATION,
            configuration=TemplateConfiguration(payload=_export_configuration(source)),
            requirements=TemplateRequirements(),
            provenance=TemplateProvenance(
                author=author,
                source="canonical-automation-export",
                trust=TemplateTrust.LOCAL,
                metadata={
                    "source_resource_type": "automation",
                    "source_resource_id": source.id,
                    "source_resource_revision": source.revision,
                    "source_project_id": source.project_id,
                    "source_workspace_id": source.workspace_id,
                    "source_task_project_id": source.task_template.project_id,
                    "source_task_workspace_id": source.task_template.workspace_id,
                },
            ),
            tags=("automation", "exported"),
        )
        return self.templates.create_draft(
            owner_ref=owner_ref,
            content=content,
        )


def register_automation_template_handler(
    registry: ContextualTemplateHandlerRegistry,
    service: AutomationService,
) -> None:
    registry.register(AutomationTemplateHandler(service))


def _configuration(revision: TemplateRevision) -> _AutomationConfiguration:
    payload = revision.content.configuration.payload
    if payload is None:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "canonical Automation handler requires an inline Template payload",
        )
    data = cast(Mapping[str, object], payload)
    try:
        trigger = _trigger(_required_mapping(data, "trigger"))
        task_template = _task_template(_required_mapping(data, "task_template"))
        retry_policy = _retry_policy(_optional_mapping(data, "retry_policy") or {})
        overlap_policy = OverlapPolicy(
            _optional_string(data, "overlap_policy") or OverlapPolicy.SKIP_WHILE_PROCESSING.value
        )
    except (TypeError, ValueError) as exc:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"invalid Automation template configuration: {exc}",
        ) from exc

    deduplication = _optional_string(data, "deduplication_strategy") or "delivery_key"
    if deduplication != "delivery_key":
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"unsupported Automation deduplication strategy: {deduplication}",
        )
    return _AutomationConfiguration(
        name=_required_string(data, "name"),
        description=_optional_text(data, "description") or "",
        trigger=trigger,
        task_template=task_template,
        project_id=_optional_string(data, "project_id"),
        workspace_id=_optional_string(data, "workspace_id"),
        deduplication_strategy=deduplication,
        retry_policy=retry_policy,
        overlap_policy=overlap_policy,
    )


def _trigger(data: Mapping[str, object]) -> TriggerDefinition:
    forbidden = {"secret", "token", "signing_key"} & set(data)
    if forbidden:
        raise ValueError("webhook secrets must be referenced, never embedded")
    trigger_type = TriggerType(_required_string(data, "type"))
    raw_interval = data.get("interval_seconds")
    interval: float | None = None
    if raw_interval is not None:
        if isinstance(raw_interval, bool) or not isinstance(raw_interval, int | float):
            raise ValueError("interval_seconds must be numeric")
        interval = float(raw_interval)
    return TriggerDefinition(
        type=trigger_type,
        timezone=_optional_string(data, "timezone") or "UTC",
        at=_optional_time(data.get("at")),
        interval_seconds=interval,
        event_type=_optional_string(data, "event_type"),
        filters=_json_object(data.get("filters", {}), "trigger.filters"),
        webhook_source=_optional_string(data, "webhook_source"),
        verification_ref=_optional_string(data, "verification_ref"),
        missed_schedule_policy=MissedSchedulePolicy(
            _optional_string(data, "missed_schedule_policy") or MissedSchedulePolicy.COALESCE.value
        ),
    )


def _task_template(data: Mapping[str, object]) -> TaskTemplate:
    return TaskTemplate(
        title=_required_string(data, "title"),
        objective=_required_string(data, "objective"),
        project_id=_optional_string(data, "project_id"),
        workspace_id=_optional_string(data, "workspace_id"),
        payload=_json_object(data.get("payload", {}), "task_template.payload"),
    )


def _retry_policy(data: Mapping[str, object]) -> RetryPolicy:
    attempts = data.get("max_attempts", 3)
    if isinstance(attempts, bool) or not isinstance(attempts, int):
        raise ValueError("retry_policy.max_attempts must be an integer")
    backoff = data.get("base_backoff_seconds", 1.0)
    if isinstance(backoff, bool) or not isinstance(backoff, int | float):
        raise ValueError("retry_policy.base_backoff_seconds must be numeric")
    return RetryPolicy(
        max_attempts=attempts,
        base_backoff_seconds=float(backoff),
    )


def _export_configuration(source: Automation) -> Mapping[str, FrozenJsonValue]:
    trigger: dict[str, FrozenJsonValue] = {
        "type": source.trigger.type.value,
        "timezone": source.trigger.timezone,
        "at": None if source.trigger.at is None else source.trigger.at.isoformat(),
        "interval_seconds": source.trigger.interval_seconds,
        "event_type": source.trigger.event_type,
        "filters": _freeze_json(source.trigger.filters),
        "webhook_source": source.trigger.webhook_source,
        "verification_ref": source.trigger.verification_ref,
        "missed_schedule_policy": source.trigger.missed_schedule_policy.value,
    }
    task_template: dict[str, FrozenJsonValue] = {
        "title": source.task_template.title,
        "objective": source.task_template.objective,
        "project_id": None,
        "workspace_id": None,
        "payload": _freeze_json(source.task_template.payload),
    }
    return {
        "name": source.name,
        "description": source.description,
        "trigger": trigger,
        "task_template": task_template,
        "project_id": None,
        "workspace_id": None,
        "deduplication_strategy": source.deduplication_strategy,
        "retry_policy": {
            "max_attempts": source.retry_policy.max_attempts,
            "base_backoff_seconds": source.retry_policy.base_backoff_seconds,
        },
        "overlap_policy": source.overlap_policy.value,
    }


def _freeze_json(value: JsonValue) -> FrozenJsonValue:
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, dict):
        return {key: _freeze_json(item) for key, item in value.items()}
    return value


def _json_object(value: object, name: str) -> dict[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return {str(key): _json_value(item) for key, item in value.items()}


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    raise ValueError(f"value is not JSON-compatible: {type(value).__name__}")


def _required_mapping(data: Mapping[str, object], name: str) -> Mapping[str, object]:
    value = data.get(name)
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return cast(Mapping[str, object], value)


def _optional_mapping(data: Mapping[str, object], name: str) -> Mapping[str, object] | None:
    value = data.get(name)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return cast(Mapping[str, object], value)


def _required_string(data: Mapping[str, object], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    return value


def _optional_string(data: Mapping[str, object], name: str) -> str | None:
    value = data.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string or null")
    return value


def _optional_text(data: Mapping[str, object], name: str) -> str | None:
    value = data.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")
    return value


def _optional_time(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("trigger.at must be an ISO-8601 string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("trigger.at must be timezone-aware")
    return parsed
