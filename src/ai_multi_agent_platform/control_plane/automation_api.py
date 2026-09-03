"""Control Plane composition for issue #18 Automation management and trigger delivery."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from datetime import datetime
from typing import Any, cast

from ai_multi_agent_platform.automation import (
    Automation,
    AutomationEventSink,
    AutomationRepository,
    AutomationService,
    AutomationState,
    IdentityContext,
    InMemoryAutomationRepository,
    MissedSchedulePolicy,
    OverlapPolicy,
    ReferenceScheduler,
    RetryPolicy,
    TaskTemplate,
    TriggerDefinition,
    TriggerDelivery,
    TriggerType,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.interfaces import (
    AuthorizationProvider,
    EventProvider,
    ProviderContract,
)
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.kernel.repository import EventRepository
from ai_multi_agent_platform.models import ModelRegistry
from ai_multi_agent_platform.search import SearchProvider
from ai_multi_agent_platform.task_management import TaskManagementService
from ai_multi_agent_platform.workspaces import RunWorkspaceBindingRepository, WorkspaceProvider

from .extensions import CommandHandler, ResourceService
from .http import HTTPRequest, HTTPResponse
from .models import ActorContext, PageQuery, RequestContext
from .search_contract import ControlPlane as _BaseControlPlane
from .search_contract import ControlPlaneHTTP as _BaseControlPlaneHTTP
from .search_contract import build_openapi as _build_base_openapi
from .service import ScopeStore

AUTOMATION_COLLECTION = "automations"
DELIVERY_COLLECTION = "automation-deliveries"
AUTOMATION_COMMANDS = (
    "automation.create",
    "automation.update",
    "automation.pause",
    "automation.resume",
    "automation.disable",
    "automation.test",
    "automation.webhook",
    "automation.event",
    "automation.evaluate",
    "automation.retry-delivery",
)

WebhookVerifier = Callable[
    [Automation, str, dict[str, JsonValue], dict[str, JsonValue]], Awaitable[bool]
]


class _AutomationResources(ResourceService):
    def __init__(self, service: AutomationService) -> None:
        self._service = service

    async def list_resources(
        self, context: RequestContext, query: PageQuery
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(_automation_resource(item) for item in await self._service.list_automations())

    async def get_resource(self, context: RequestContext, resource_id: str) -> dict[str, JsonValue]:
        del context
        return _automation_resource(await self._service.get_automation(resource_id))


class _DeliveryResources(ResourceService):
    def __init__(self, service: AutomationService) -> None:
        self._service = service

    async def list_resources(
        self, context: RequestContext, query: PageQuery
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(_delivery_resource(item) for item in await self._service.list_deliveries())

    async def get_resource(self, context: RequestContext, resource_id: str) -> dict[str, JsonValue]:
        del context
        return _delivery_resource(await self._service.get_delivery(resource_id))


class ControlPlane(_BaseControlPlane):
    """Current composed Control Plane plus canonical Automation management."""

    def __init__(
        self,
        *,
        kernel: PlatformKernel,
        events: EventRepository,
        scopes: ScopeStore | None = None,
        authorization: AuthorizationProvider | None = None,
        live_events: EventProvider | None = None,
        health_providers: tuple[ProviderContract, ...] = (),
        model_registry: ModelRegistry | None = None,
        resource_services: Mapping[str, ResourceService] | None = None,
        command_handlers: Mapping[str, CommandHandler] | None = None,
        task_management: TaskManagementService | None = None,
        workspace_provider: WorkspaceProvider | None = None,
        run_workspace_bindings: RunWorkspaceBindingRepository | None = None,
        search_provider: SearchProvider | None = None,
        automation_repository: AutomationRepository | None = None,
        automation_service: AutomationService | None = None,
        automation_event_sink: AutomationEventSink | None = None,
        webhook_verifier: WebhookVerifier | None = None,
    ) -> None:
        supplied_collections = set((resource_services or {}).keys())
        reserved_collections = {AUTOMATION_COLLECTION, DELIVERY_COLLECTION}
        conflicts = sorted(supplied_collections & reserved_collections)
        if conflicts:
            raise ValueError(
                f"resource_services conflict with canonical automation routes: {conflicts}"
            )
        supplied_commands = set((command_handlers or {}).keys())
        command_conflicts = sorted(supplied_commands & set(AUTOMATION_COMMANDS))
        if command_conflicts:
            raise ValueError(
                f"command_handlers conflict with canonical automation commands: {command_conflicts}"
            )
        super().__init__(
            kernel=kernel,
            events=events,
            scopes=scopes,
            authorization=authorization,
            live_events=live_events,
            health_providers=health_providers,
            model_registry=model_registry,
            resource_services=resource_services,
            command_handlers=command_handlers,
            task_management=task_management,
            workspace_provider=workspace_provider,
            run_workspace_bindings=run_workspace_bindings,
            search_provider=search_provider,
        )
        repository = automation_repository or InMemoryAutomationRepository()
        self._automation_service = automation_service or AutomationService(
            repository=repository,
            task_creator=self._create_task_from_automation,
            event_sink=automation_event_sink,
        )
        self._automation_scheduler = ReferenceScheduler(self._automation_service)
        self._webhook_verifier = webhook_verifier
        super().register_resource_service(
            AUTOMATION_COLLECTION, _AutomationResources(self._automation_service)
        )
        super().register_resource_service(
            DELIVERY_COLLECTION, _DeliveryResources(self._automation_service)
        )
        super().register_command("automation.create", self._automation_create_command)
        super().register_command("automation.update", self._automation_update_command)
        super().register_command("automation.pause", self._automation_pause_command)
        super().register_command("automation.resume", self._automation_resume_command)
        super().register_command("automation.disable", self._automation_disable_command)
        super().register_command("automation.test", self._automation_test_command)
        super().register_command("automation.webhook", self._automation_webhook_command)
        super().register_command("automation.event", self._automation_event_command)
        super().register_command("automation.evaluate", self._automation_evaluate_command)
        super().register_command("automation.retry-delivery", self._automation_retry_command)

    @property
    def automation_service(self) -> AutomationService:
        return self._automation_service

    @property
    def automation_scheduler(self) -> ReferenceScheduler:
        return self._automation_scheduler

    def register_resource_service(self, collection: str, service: ResourceService) -> None:
        if collection in {AUTOMATION_COLLECTION, DELIVERY_COLLECTION}:
            raise ValueError(
                f"extension collection conflicts with canonical automation route: {collection}"
            )
        super().register_resource_service(collection, service)

    def register_command(self, command: str, handler: CommandHandler) -> None:
        if command in AUTOMATION_COMMANDS:
            raise ValueError(
                f"extension command conflicts with canonical automation command: {command}"
            )
        super().register_command(command, handler)

    async def _create_task_from_automation(
        self,
        automation: Automation,
        delivery: TriggerDelivery,
        payload: dict[str, JsonValue],
        idempotency_key: str,
    ) -> str:
        context = RequestContext(
            request_id=f"automation:{delivery.id}",
            correlation_id=delivery.id,
            actor=ActorContext(
                principal_ref=automation.identity.principal_ref,
                owner_type=cast(Any, automation.identity.owner_type),
                owner_id=automation.identity.owner_id,
            ),
            idempotency_key=idempotency_key,
        )
        resource = await self.create_task(context, payload)
        task_id = resource.get("id")
        if not isinstance(task_id, str):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "canonical task creation did not return a task id",
            )
        return task_id

    async def _automation_create_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if resource_ref != AUTOMATION_COLLECTION:
            raise ValueError("automation.create resource_ref must be 'automations'")
        if context.actor.owner_type is None or context.actor.owner_id is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "automation creation requires canonical actor owner context",
            )
        identity = IdentityContext(
            principal_ref=context.actor.principal_ref,
            owner_type=context.actor.owner_type,
            owner_id=context.actor.owner_id,
        )
        trigger = _parse_trigger(_required_object(payload, "trigger"))
        template = _parse_template(_required_object(payload, "task_template"))
        raw_retry = _optional_object(payload, "retry_policy")
        automation = await self._automation_service.create_automation(
            name=_required_string(payload, "name"),
            description=_optional_string(payload, "description") or "",
            identity=identity,
            trigger=trigger,
            task_template=template,
            project_id=_optional_string(payload, "project_id"),
            workspace_id=_optional_string(payload, "workspace_id"),
            deduplication_strategy=_parse_deduplication_strategy(
                _optional_string(payload, "deduplication_strategy") or "delivery_key"
            ),
            retry_policy=None if raw_retry is None else _parse_retry_policy(raw_retry),
            overlap_policy=_parse_overlap_policy(
                _optional_string(payload, "overlap_policy") or "skip_while_processing"
            ),
        )
        return _automation_resource(automation)

    async def _automation_update_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        raw_trigger = payload.get("trigger")
        raw_template = payload.get("task_template")
        raw_retry = _optional_object(payload, "retry_policy")
        raw_overlap = _optional_string(payload, "overlap_policy")
        raw_deduplication = _optional_string(payload, "deduplication_strategy")
        trigger = None if raw_trigger is None else _parse_trigger(_object(raw_trigger, "trigger"))
        template = (
            None
            if raw_template is None
            else _parse_template(_object(raw_template, "task_template"))
        )
        automation = await self._automation_service.update_automation(
            resource_ref,
            name=_optional_string(payload, "name"),
            description=_optional_string(payload, "description"),
            trigger=trigger,
            task_template=template,
            deduplication_strategy=(
                None
                if raw_deduplication is None
                else _parse_deduplication_strategy(raw_deduplication)
            ),
            retry_policy=None if raw_retry is None else _parse_retry_policy(raw_retry),
            overlap_policy=None if raw_overlap is None else _parse_overlap_policy(raw_overlap),
        )
        return _automation_resource(automation)

    async def _automation_pause_command(
        self, context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        del context, payload
        return _automation_resource(
            await self._automation_service.set_state(resource_ref, AutomationState.PAUSED)
        )

    async def _automation_resume_command(
        self, context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        del context, payload
        return _automation_resource(
            await self._automation_service.set_state(resource_ref, AutomationState.ENABLED)
        )

    async def _automation_disable_command(
        self, context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        del context, payload
        return _automation_resource(
            await self._automation_service.set_state(resource_ref, AutomationState.DISABLED)
        )

    async def _automation_test_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        occurrence_id = _optional_string(payload, "occurrence_id") or context.idempotency_key
        if occurrence_id is None:
            raise ValueError("manual test requires occurrence_id or Idempotency-Key")
        event_payload = _optional_object(payload, "payload") or {}
        return _delivery_resource(
            await self._automation_service.test_trigger(
                resource_ref,
                occurrence_id=occurrence_id,
                payload=event_payload,
                fired_at=_optional_time(payload.get("fired_at")),
            )
        )

    async def _automation_webhook_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        automation = await self._automation_service.get_automation(resource_ref)
        if self._webhook_verifier is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "webhook verification boundary is not configured",
                retryable=False,
            )
        source = _required_string(payload, "source")
        event_id = _required_string(payload, "event_id")
        event_payload = _optional_object(payload, "payload") or {}
        self._automation_service.validate_webhook_input(
            event_id=event_id,
            payload=event_payload,
            source=source,
        )
        verification = _optional_object(payload, "verification") or {}
        verified = await self._webhook_verifier(
            automation,
            source,
            event_payload,
            verification,
        )
        delivery = await self._automation_service.deliver_webhook(
            resource_ref,
            event_id=event_id,
            payload=event_payload,
            source=source,
            verified=verified,
            fired_at=_optional_time(payload.get("fired_at")),
        )
        return _delivery_resource(delivery)

    async def _automation_event_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        if resource_ref != AUTOMATION_COLLECTION:
            raise ValueError("automation.event resource_ref must be 'automations'")
        deliveries = await self._automation_service.deliver_platform_event(
            event_id=_required_string(payload, "event_id"),
            event_type=_required_string(payload, "event_type"),
            payload=_optional_object(payload, "payload") or {},
            fired_at=_optional_time(payload.get("fired_at")),
        )
        return {
            "id": f"automation-event:{_required_string(payload, 'event_id')}",
            "type": "automation-event-result",
            "count": len(deliveries),
            "delivery_ids": [item.id for item in deliveries],
        }

    async def _automation_evaluate_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        if resource_ref != AUTOMATION_COLLECTION:
            raise ValueError("automation.evaluate resource_ref must be 'automations'")
        deliveries = await self._automation_scheduler.tick(now=_optional_time(payload.get("now")))
        return {
            "id": "automation-evaluation",
            "type": "automation-evaluation-result",
            "count": len(deliveries),
            "delivery_ids": [item.id for item in deliveries],
        }

    async def _automation_retry_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context, payload
        return _delivery_resource(await self._automation_service.retry_delivery(resource_ref))


class ControlPlaneHTTP(_BaseControlPlaneHTTP):
    """Preserve current composed HTTP features and annotate Automation OpenAPI."""

    async def handle(self, request: HTTPRequest) -> HTTPResponse:
        response = await super().handle(request)
        if (
            request.method == "GET"
            and request.path.endswith("/openapi.json")
            and response.status == 200
            and isinstance(response.body, dict)
        ):
            control_plane = cast(ControlPlane, self._control_plane)
            external_collections = tuple(
                collection
                for collection in control_plane.registered_collections
                if collection not in {AUTOMATION_COLLECTION, DELIVERY_COLLECTION}
            )
            external_commands = tuple(
                command
                for command in control_plane.registered_commands
                if command not in AUTOMATION_COMMANDS
            )
            specification = _augment_automation_openapi(
                cast(dict[str, Any], deepcopy(response.body)),
                extension_collections=external_collections,
                extension_commands=external_commands,
            )
            return HTTPResponse(
                status=response.status,
                body=cast(dict[str, JsonValue], specification),
                headers=dict(response.headers),
            )
        return response


def build_openapi(
    *,
    extension_collections: tuple[str, ...] = (),
    extension_commands: tuple[str, ...] = (),
) -> dict[str, Any]:
    collections = tuple(
        sorted({*extension_collections, AUTOMATION_COLLECTION, DELIVERY_COLLECTION})
    )
    commands = tuple(sorted({*extension_commands, *AUTOMATION_COMMANDS}))
    specification = _build_base_openapi(
        extension_collections=collections,
        extension_commands=commands,
    )
    return _augment_automation_openapi(
        specification,
        extension_collections=extension_collections,
        extension_commands=extension_commands,
    )


def _augment_automation_openapi(
    specification: dict[str, Any],
    *,
    extension_collections: tuple[str, ...],
    extension_commands: tuple[str, ...],
) -> dict[str, Any]:
    specification["x-registered-extension-collections"] = list(sorted(set(extension_collections)))
    specification["x-registered-extension-commands"] = list(sorted(set(extension_commands)))
    specification["x-automation"] = {
        "invariant": "trigger -> automation -> canonical task -> normal lifecycle",
        "collections": [AUTOMATION_COLLECTION, DELIVERY_COLLECTION],
        "commands": list(AUTOMATION_COMMANDS),
        "scheduler": "replaceable deterministic reference scheduler",
        "broker_required": False,
        "frontend_required": False,
        "webhook_verification": "trusted verifier boundary required",
        "webhook_admission": "bounded canonical JSON validated before verifier execution",
        "overlap_policies": [policy.value for policy in OverlapPolicy],
        "deduplication_strategies": ["delivery_key"],
    }
    return specification


def _automation_resource(automation: Automation) -> dict[str, JsonValue]:
    return {
        "id": automation.id,
        "type": "automation",
        "name": automation.name,
        "description": automation.description,
        "project_id": automation.project_id,
        "workspace_id": automation.workspace_id,
        "state": automation.state.value,
        "identity": {
            "principal_ref": automation.identity.principal_ref,
            "owner_type": automation.identity.owner_type,
            "owner_id": automation.identity.owner_id,
        },
        "trigger": _trigger_resource(automation.trigger),
        "task_template": {
            "title": automation.task_template.title,
            "objective": automation.task_template.objective,
            "project_id": automation.task_template.project_id,
            "workspace_id": automation.task_template.workspace_id,
            "payload": dict(automation.task_template.payload),
        },
        "deduplication_strategy": automation.deduplication_strategy,
        "retry_policy": {
            "max_attempts": automation.retry_policy.max_attempts,
            "base_backoff_seconds": automation.retry_policy.base_backoff_seconds,
        },
        "overlap_policy": automation.overlap_policy.value,
        "created_at": automation.created_at.isoformat(),
        "updated_at": automation.updated_at.isoformat(),
        "revision": automation.revision,
        "last_evaluated_at": (
            None
            if automation.last_evaluated_at is None
            else automation.last_evaluated_at.isoformat()
        ),
        "next_evaluation_at": (
            None
            if automation.next_evaluation_at is None
            else automation.next_evaluation_at.isoformat()
        ),
    }


def _trigger_resource(trigger: TriggerDefinition) -> dict[str, JsonValue]:
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


def _delivery_resource(delivery: TriggerDelivery) -> dict[str, JsonValue]:
    return {
        "id": delivery.id,
        "type": "automation-delivery",
        "automation_id": delivery.automation_id,
        "trigger_type": delivery.trigger_type.value,
        "source": delivery.source,
        "dedupe_key": delivery.dedupe_key,
        "fired_at": delivery.fired_at.isoformat(),
        "received_at": delivery.received_at.isoformat(),
        "payload": dict(delivery.payload),
        "status": delivery.status.value,
        "attempt": delivery.attempt,
        "generated_task_id": delivery.generated_task_id,
        "error_code": delivery.error_code,
        "error_message": delivery.error_message,
        "processing_duration_ms": delivery.processing_duration_ms,
    }


def _parse_trigger(payload: dict[str, JsonValue]) -> TriggerDefinition:
    forbidden = {"secret", "token", "signing_key"} & set(payload)
    if forbidden:
        raise ValueError("webhook secrets must be referenced, never embedded")
    raw_type = _required_string(payload, "type")
    try:
        trigger_type = TriggerType(raw_type)
    except ValueError as exc:
        raise ValueError(f"unsupported trigger type: {raw_type}") from exc
    raw_interval = payload.get("interval_seconds")
    interval: float | None
    if raw_interval is None:
        interval = None
    elif isinstance(raw_interval, (int, float)) and not isinstance(raw_interval, bool):
        interval = float(raw_interval)
    else:
        raise ValueError("interval_seconds must be numeric")
    raw_policy = _optional_string(payload, "missed_schedule_policy") or "coalesce"
    try:
        policy = MissedSchedulePolicy(raw_policy)
    except ValueError as exc:
        raise ValueError(f"unsupported missed_schedule_policy: {raw_policy}") from exc
    return TriggerDefinition(
        type=trigger_type,
        timezone=_optional_string(payload, "timezone") or "UTC",
        at=_optional_time(payload.get("at")),
        interval_seconds=interval,
        event_type=_optional_string(payload, "event_type"),
        filters=_optional_object(payload, "filters") or {},
        webhook_source=_optional_string(payload, "webhook_source"),
        verification_ref=_optional_string(payload, "verification_ref"),
        missed_schedule_policy=policy,
    )


def _parse_template(payload: dict[str, JsonValue]) -> TaskTemplate:
    return TaskTemplate(
        title=_required_string(payload, "title"),
        objective=_required_string(payload, "objective"),
        project_id=_optional_string(payload, "project_id"),
        workspace_id=_optional_string(payload, "workspace_id"),
        payload=_optional_object(payload, "payload") or {},
    )


def _parse_retry_policy(payload: dict[str, JsonValue]) -> RetryPolicy:
    raw_attempts = payload.get("max_attempts", 3)
    if not isinstance(raw_attempts, int) or isinstance(raw_attempts, bool):
        raise ValueError("retry_policy.max_attempts must be an integer")
    raw_backoff = payload.get("base_backoff_seconds", 1.0)
    if not isinstance(raw_backoff, (int, float)) or isinstance(raw_backoff, bool):
        raise ValueError("retry_policy.base_backoff_seconds must be numeric")
    return RetryPolicy(
        max_attempts=raw_attempts,
        base_backoff_seconds=float(raw_backoff),
    )


def _parse_overlap_policy(value: str) -> OverlapPolicy:
    try:
        return OverlapPolicy(value)
    except ValueError as exc:
        raise ValueError(f"unsupported overlap_policy: {value}") from exc


def _parse_deduplication_strategy(value: str) -> str:
    if value != "delivery_key":
        raise ValueError(f"unsupported deduplication_strategy: {value}")
    return value


def _required_string(payload: dict[str, JsonValue], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    return value


def _optional_string(payload: dict[str, JsonValue], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    return value


def _object(value: JsonValue, name: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _required_object(payload: dict[str, JsonValue], name: str) -> dict[str, JsonValue]:
    value = payload.get(name)
    if value is None:
        raise ValueError(f"{name} is required")
    return _object(value, name)


def _optional_object(payload: dict[str, JsonValue], name: str) -> dict[str, JsonValue] | None:
    value = payload.get(name)
    return None if value is None else _object(value, name)


def _optional_time(value: JsonValue | None) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO-8601 string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed
