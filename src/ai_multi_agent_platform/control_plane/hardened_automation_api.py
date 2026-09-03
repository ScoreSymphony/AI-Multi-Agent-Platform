"""Authorization, audit and Search hardening for the composed Automation Control Plane."""

from __future__ import annotations

from typing import Any

from ai_multi_agent_platform.automation import (
    Automation,
    TriggerDelivery,
    automation_change_actor,
    automation_creation_idempotency_key,
)
from ai_multi_agent_platform.contracts.types import JsonValue

from .automation_api import (
    AUTOMATION_COLLECTION,
    DELIVERY_COLLECTION,
    ControlPlane as _AutomationControlPlane,
    _automation_resource,
    _delivery_resource,
)
from .extensions import ResourceService
from .models import PageQuery, RequestContext, paginate
from .registered_search_contract import ControlPlane as _RegisteredSearchControlPlane

_CONFIGURATION_COMMANDS = frozenset(
    {
        "automation.create",
        "automation.update",
        "automation.pause",
        "automation.resume",
        "automation.disable",
    }
)


class _OwnedAutomationResources(ResourceService):
    def __init__(self, control_plane: ControlPlane) -> None:
        self._control_plane = control_plane

    async def list_resources(
        self, context: RequestContext, query: PageQuery
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(
            _owned_automation_resource(item)
            for item in await self._control_plane.automation_service.list_automations()
        )

    async def get_resource(self, context: RequestContext, resource_id: str) -> dict[str, JsonValue]:
        del context
        return _owned_automation_resource(
            await self._control_plane.automation_service.get_automation(resource_id)
        )


class _OwnedDeliveryResources(ResourceService):
    def __init__(self, control_plane: ControlPlane) -> None:
        self._control_plane = control_plane

    async def list_resources(
        self, context: RequestContext, query: PageQuery
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        resources: list[dict[str, JsonValue]] = []
        for delivery in await self._control_plane.automation_service.list_deliveries():
            automation = await self._control_plane.automation_service.get_automation(
                delivery.automation_id
            )
            resources.append(_owned_delivery_resource(delivery, automation))
        return tuple(resources)

    async def get_resource(self, context: RequestContext, resource_id: str) -> dict[str, JsonValue]:
        del context
        delivery = await self._control_plane.automation_service.get_delivery(resource_id)
        automation = await self._control_plane.automation_service.get_automation(
            delivery.automation_id
        )
        return _owned_delivery_resource(delivery, automation)


class ControlPlane(_AutomationControlPlane, _RegisteredSearchControlPlane):
    """Compose Automation above registered Search with object-scoped authorization."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Canonical Automation resources need top-level owner metadata so registered
        # Search can enforce the same owner policy as direct reads.
        self._resource_services[AUTOMATION_COLLECTION] = _OwnedAutomationResources(self)
        self._resource_services[DELIVERY_COLLECTION] = _OwnedDeliveryResources(self)

    async def execute_command(
        self,
        context: RequestContext,
        command: str,
        resource_ref: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        if command == "automation.create" and context.idempotency_key is not None:
            with (
                automation_change_actor(context.actor.principal_ref),
                automation_creation_idempotency_key(context.idempotency_key),
            ):
                return await super().execute_command(context, command, resource_ref, payload)
        if command in _CONFIGURATION_COMMANDS:
            with automation_change_actor(context.actor.principal_ref):
                return await super().execute_command(context, command, resource_ref, payload)
        return await super().execute_command(context, command, resource_ref, payload)

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
                    context, "automation-delivery:list", automation, resource_ref=delivery.id
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

    async def _automation_update_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        await self._authorize_automation_target(context, "automation.update", resource_ref)
        return await super()._automation_update_command(context, resource_ref, payload)

    async def _automation_pause_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        await self._authorize_automation_target(context, "automation.pause", resource_ref)
        return await super()._automation_pause_command(context, resource_ref, payload)

    async def _automation_resume_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        await self._authorize_automation_target(context, "automation.resume", resource_ref)
        return await super()._automation_resume_command(context, resource_ref, payload)

    async def _automation_disable_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        await self._authorize_automation_target(context, "automation.disable", resource_ref)
        return await super()._automation_disable_command(context, resource_ref, payload)

    async def _automation_test_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        await self._authorize_automation_target(context, "automation.test", resource_ref)
        return await super()._automation_test_command(context, resource_ref, payload)

    async def _automation_webhook_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        await self._authorize_automation_target(context, "automation.webhook", resource_ref)
        return await super()._automation_webhook_command(context, resource_ref, payload)

    async def _automation_retry_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        delivery = await self.automation_service.get_delivery(resource_ref)
        automation = await self.automation_service.get_automation(delivery.automation_id)
        await self._authorize_automation(
            context,
            "automation.retry-delivery",
            automation,
            resource_ref=delivery.id,
        )
        return await super()._automation_retry_command(context, resource_ref, payload)

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
    return resource
