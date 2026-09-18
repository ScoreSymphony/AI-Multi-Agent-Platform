"""Explicit canonical Control Plane composition for Automation.

Automation remains owned by ``AutomationService``. This composition keeps the existing
scheduler, object-scoped authorization, audit context and Search projection semantics,
but publishes the northbound collections and commands through one named module instead
of relying on the historical Automation/Search ControlPlane MRO stack.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from ai_multi_agent_platform.automation import (
    Automation,
    AutomationEventSink,
    AutomationRepository,
    AutomationService,
    InMemoryAutomationRepository,
    ReferenceScheduler,
    TriggerDelivery,
    automation_change_actor,
    automation_creation_idempotency_key,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .automation_api import (
    AUTOMATION_COLLECTION,
    AUTOMATION_COMMANDS,
    DELIVERY_COLLECTION,
    WebhookVerifier,
    _automation_resource,
    _delivery_resource,
)
from .automation_api import ControlPlane as _LegacyAutomationControlPlane
from .extensions import ControlPlaneModule, ResourceService
from .models import ActorContext, PageQuery, RequestContext, paginate
from .module_registry import install_control_plane_modules
from .search_checkpoint_contract import ControlPlane as _SearchCheckpointControlPlane

AUTOMATION_MODULE = "automation"
_CONFIGURATION_COMMANDS = frozenset(
    {
        "automation.create",
        "automation.update",
        "automation.pause",
        "automation.resume",
        "automation.disable",
        "automation.invalidate",
        "automation.revalidate",
    }
)


class _OwnedAutomationResources(ResourceService):
    def __init__(self, control_plane: ControlPlane) -> None:
        self._control_plane = control_plane

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(
            _owned_automation_resource(item)
            for item in await self._control_plane.automation_service.list_automations()
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        return _owned_automation_resource(
            await self._control_plane.automation_service.get_automation(resource_id)
        )


class _OwnedDeliveryResources(ResourceService):
    def __init__(self, control_plane: ControlPlane) -> None:
        self._control_plane = control_plane

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        resources: list[dict[str, JsonValue]] = []
        for delivery in await self._control_plane.automation_service.list_deliveries():
            automation = await self._control_plane.automation_service.get_automation(
                delivery.automation_id
            )
            resources.append(_owned_delivery_resource(delivery, automation))
        return tuple(resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        delivery = await self._control_plane.automation_service.get_delivery(resource_id)
        automation = await self._control_plane.automation_service.get_automation(
            delivery.automation_id
        )
        return _owned_delivery_resource(delivery, automation)


class ControlPlane(_SearchCheckpointControlPlane):
    """Automation façade with explicit module ownership and linear Search composition."""

    def __init__(
        self,
        *args: Any,
        automation_repository: AutomationRepository | None = None,
        automation_service: AutomationService | None = None,
        automation_event_sink: AutomationEventSink | None = None,
        webhook_verifier: WebhookVerifier | None = None,
        **kwargs: Any,
    ) -> None:
        supplied_resources = kwargs.get("resource_services")
        if isinstance(supplied_resources, Mapping):
            conflicts = sorted(
                set(supplied_resources).intersection({AUTOMATION_COLLECTION, DELIVERY_COLLECTION})
            )
            if conflicts:
                raise ValueError(
                    f"resource_services conflict with canonical automation routes: {conflicts}"
                )
        supplied_commands = kwargs.get("command_handlers")
        if isinstance(supplied_commands, Mapping):
            conflicts = sorted(set(supplied_commands).intersection(AUTOMATION_COMMANDS))
            if conflicts:
                raise ValueError(
                    f"command_handlers conflict with canonical automation commands: {conflicts}"
                )

        super().__init__(*args, **kwargs)
        repository = automation_repository or InMemoryAutomationRepository()
        self._automation_service = automation_service or AutomationService(
            repository=repository,
            task_creator=self._create_task_from_automation,
            event_sink=automation_event_sink,
        )
        self._automation_scheduler = ReferenceScheduler(self._automation_service)
        self._webhook_verifier = webhook_verifier

        handlers = {
            "automation.create": self._automation_create_command,
            "automation.update": self._automation_update_command,
            "automation.pause": self._automation_pause_command,
            "automation.resume": self._automation_resume_command,
            "automation.disable": self._automation_disable_command,
            "automation.invalidate": self._automation_invalidate_command,
            "automation.revalidate": self._automation_revalidate_command,
            "automation.test": self._automation_test_command,
            "automation.webhook": self._automation_webhook_command,
            "automation.event": self._automation_event_command,
            "automation.evaluate": self._automation_evaluate_command,
            "automation.retry-delivery": self._automation_retry_command,
        }
        if frozenset(handlers) != frozenset(AUTOMATION_COMMANDS):
            raise RuntimeError("explicit Automation module command inventory is incomplete")

        install_control_plane_modules(
            self,
            (
                ControlPlaneModule(
                    name=AUTOMATION_MODULE,
                    resource_services={
                        AUTOMATION_COLLECTION: _OwnedAutomationResources(self),
                        DELIVERY_COLLECTION: _OwnedDeliveryResources(self),
                    },
                    command_handlers=handlers,
                ),
            ),
        )

    @property
    def automation_service(self) -> AutomationService:
        return self._automation_service

    @property
    def automation_scheduler(self) -> ReferenceScheduler:
        return self._automation_scheduler

    async def list_extension_resources(
        self,
        context: RequestContext,
        collection: str,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        if collection == AUTOMATION_COLLECTION:
            resources: list[dict[str, JsonValue]] = []
            for automation in await self.automation_service.list_automations():
                if await self._automation_allowed(context, "automation:list", automation):
                    resources.append(_owned_automation_resource(automation))
            return paginate(resources, query)
        if collection == DELIVERY_COLLECTION:
            resources = []
            for delivery in await self.automation_service.list_deliveries():
                automation = await self.automation_service.get_automation(delivery.automation_id)
                if await self._automation_allowed(
                    context,
                    "automation-delivery:list",
                    automation,
                    resource_ref=delivery.id,
                ):
                    resources.append(_owned_delivery_resource(delivery, automation))
            return paginate(resources, query)
        return await super().list_extension_resources(context, collection, query)

    async def get_extension_resource(
        self,
        context: RequestContext,
        collection: str,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        if collection == AUTOMATION_COLLECTION:
            automation = await self.automation_service.get_automation(resource_id)
            await self._authorize_automation(context, "automation:read", automation)
            return _owned_automation_resource(automation)
        if collection == DELIVERY_COLLECTION:
            delivery = await self.automation_service.get_delivery(resource_id)
            automation = await self.automation_service.get_automation(delivery.automation_id)
            await self._authorize_automation(
                context,
                "automation-delivery:read",
                automation,
                resource_ref=delivery.id,
            )
            return _owned_delivery_resource(delivery, automation)
        return await super().get_extension_resource(context, collection, resource_id)

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
        idempotency_key = context.idempotency_key
        if idempotency_key is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Idempotency-Key is required for mutating commands",
            )
        with (
            automation_change_actor(context.actor.principal_ref),
            automation_creation_idempotency_key(idempotency_key),
        ):
            return await self._call_legacy_command(
                _LegacyAutomationControlPlane._automation_create_command,
                context,
                resource_ref,
                payload,
            )

    async def _automation_update_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await self._configuration_command(
            context,
            "automation.update",
            resource_ref,
            payload,
            _LegacyAutomationControlPlane._automation_update_command,
        )

    async def _automation_pause_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await self._configuration_command(
            context,
            "automation.pause",
            resource_ref,
            payload,
            _LegacyAutomationControlPlane._automation_pause_command,
        )

    async def _automation_resume_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await self._configuration_command(
            context,
            "automation.resume",
            resource_ref,
            payload,
            _LegacyAutomationControlPlane._automation_resume_command,
        )

    async def _automation_disable_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await self._configuration_command(
            context,
            "automation.disable",
            resource_ref,
            payload,
            _LegacyAutomationControlPlane._automation_disable_command,
        )

    async def _automation_invalidate_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        with automation_change_actor(context.actor.principal_ref):
            await self._authorize_automation_target(
                context,
                "automation.invalidate",
                resource_ref,
            )
            reason_code = payload.get("reason_code")
            if not isinstance(reason_code, str):
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "automation.invalidate requires a categorical reason_code",
                )
            invalidated = await self.automation_service.invalidate_automation(
                resource_ref,
                reason_code=reason_code,
            )
            return _owned_automation_resource(invalidated)

    async def _automation_revalidate_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del payload
        with automation_change_actor(context.actor.principal_ref):
            await self._authorize_automation_target(
                context,
                "automation.revalidate",
                resource_ref,
            )
            revalidated = await self.automation_service.revalidate_automation(resource_ref)
            return _owned_automation_resource(revalidated)

    async def _automation_test_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        await self._authorize_automation_target(context, "automation.test", resource_ref)
        return await self._call_legacy_command(
            _LegacyAutomationControlPlane._automation_test_command,
            context,
            resource_ref,
            payload,
        )

    async def _automation_webhook_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        await self._authorize_automation_target(context, "automation.webhook", resource_ref)
        return await self._call_legacy_command(
            _LegacyAutomationControlPlane._automation_webhook_command,
            context,
            resource_ref,
            payload,
        )

    async def _automation_event_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await self._call_legacy_command(
            _LegacyAutomationControlPlane._automation_event_command,
            context,
            resource_ref,
            payload,
        )

    async def _automation_evaluate_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await self._call_legacy_command(
            _LegacyAutomationControlPlane._automation_evaluate_command,
            context,
            resource_ref,
            payload,
        )

    async def _automation_retry_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del payload
        delivery = await self.automation_service.get_delivery(resource_ref)
        automation = await self.automation_service.get_automation(delivery.automation_id)
        await self._authorize_automation(
            context,
            "automation.retry-delivery",
            automation,
            resource_ref=delivery.id,
        )
        retried = await self.automation_service.retry_delivery(resource_ref)
        latest = await self.automation_service.get_automation(retried.automation_id)
        return _owned_delivery_resource(retried, latest)

    async def _configuration_command(
        self,
        context: RequestContext,
        command: str,
        resource_ref: str,
        payload: dict[str, JsonValue],
        handler: Any,
    ) -> dict[str, JsonValue]:
        if command not in _CONFIGURATION_COMMANDS:
            raise RuntimeError(f"not an Automation configuration command: {command}")
        with automation_change_actor(context.actor.principal_ref):
            await self._authorize_automation_target(context, command, resource_ref)
            return await self._call_legacy_command(
                handler,
                context,
                resource_ref,
                payload,
            )

    async def _call_legacy_command(
        self,
        handler: Any,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], await handler(self, context, resource_ref, payload))

    async def _authorize_automation_target(
        self,
        context: RequestContext,
        action: str,
        automation_id: str,
    ) -> None:
        automation = await self.automation_service.get_automation(automation_id)
        await self._authorize_automation(context, action, automation)

    async def _authorize_automation(
        self,
        context: RequestContext,
        action: str,
        automation: Automation,
        *,
        resource_ref: str | None = None,
    ) -> None:
        await self._authorize(
            context,
            action,
            resource_ref or automation.id,
            owner_type=automation.identity.owner_type,
            owner_id=automation.identity.owner_id,
            project_id=automation.project_id,
        )

    async def _automation_allowed(
        self,
        context: RequestContext,
        action: str,
        automation: Automation,
        *,
        resource_ref: str | None = None,
    ) -> bool:
        return await self._allowed(
            context,
            action,
            resource_ref or automation.id,
            owner_type=automation.identity.owner_type,
            owner_id=automation.identity.owner_id,
            project_id=automation.project_id,
        )


def _owned_automation_resource(automation: Automation) -> dict[str, JsonValue]:
    resource = _automation_resource(automation)
    resource["owner_ref"] = {
        "type": automation.identity.owner_type,
        "id": automation.identity.owner_id,
    }
    resource["invalidation_reason_code"] = automation.invalidation_reason_code
    resource["invalidated_at"] = (
        None if automation.invalidated_at is None else automation.invalidated_at.isoformat()
    )
    resource["state_before_invalid"] = (
        None if automation.state_before_invalid is None else automation.state_before_invalid.value
    )
    return resource


def _owned_delivery_resource(
    delivery: TriggerDelivery,
    automation: Automation,
) -> dict[str, JsonValue]:
    resource = _delivery_resource(delivery)
    resource["owner_ref"] = {
        "type": automation.identity.owner_type,
        "id": automation.identity.owner_id,
    }
    resource["project_id"] = automation.project_id
    resource["workspace_id"] = automation.workspace_id
    resource["retryable"] = delivery.retryable
    resource["last_failed_at"] = (
        None if delivery.last_failed_at is None else delivery.last_failed_at.isoformat()
    )
    resource["next_retry_at"] = (
        None if delivery.next_retry_at is None else delivery.next_retry_at.isoformat()
    )
    resource["retry_exhausted_at"] = (
        None if delivery.retry_exhausted_at is None else delivery.retry_exhausted_at.isoformat()
    )
    return resource


__all__ = ["AUTOMATION_MODULE", "ControlPlane"]
