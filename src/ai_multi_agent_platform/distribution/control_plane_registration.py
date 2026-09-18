"""Marketplace Control Plane module registration and authorization binding."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import (
    CommandAuthorizer,
    CommandHandler,
    ControlPlane,
    ControlPlaneModule,
    ResourceService,
)
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.control_plane.module_registry import install_control_plane_modules
from ai_multi_agent_platform.control_plane.service import _payload_digest

from .control_plane import (
    MARKETPLACE_INSTALL_COMMAND,
    MARKETPLACE_KIND_COLLECTION,
    MARKETPLACE_PREVIEW_COMMAND,
    MARKETPLACE_UNINSTALL_COMMAND,
    MARKETPLACE_UPDATE_COMMAND,
    REGISTRY_ACTIVATE_COMMAND,
    REGISTRY_COLLECTION,
    REGISTRY_PIN_COMMAND,
    REGISTRY_PREVIEW_COMMAND,
    REGISTRY_UNPIN_COMMAND,
    MarketplaceKindResourceService,
    RegistryCommandHandlers,
    RegistryResourceService,
    RegistryValidationContextResolver,
)
from .control_plane_projection import _decision_resource
from .control_plane_query import optional_source_registry as _optional_source_registry
from .control_plane_query import required_version as _required_version
from .service import DistributionPreview, DistributionService, DistributionUninstallPreview

MARKETPLACE_MODULE = "marketplace"


def register_distribution_control_plane_module(
    control_plane: ControlPlane,
    distribution: DistributionService,
    *,
    validation_context_resolver: RegistryValidationContextResolver | None = None,
) -> None:
    """Register the Registry/Marketplace surface when a provider is configured."""

    if not distribution.enabled:
        return

    resources: dict[str, ResourceService] = {
        REGISTRY_COLLECTION: RegistryResourceService(
            distribution,
            validation_context_resolver,
        ),
        MARKETPLACE_KIND_COLLECTION: MarketplaceKindResourceService(distribution),
    }
    handlers: RegistryCommandHandlers | None = None
    commands: dict[str, CommandHandler] = {}
    if validation_context_resolver is not None:
        handlers = RegistryCommandHandlers(distribution, validation_context_resolver)
        commands = _distribution_commands(distribution, handlers)

    if isinstance(control_plane, ControlPlane):
        authorizers: dict[str, CommandAuthorizer] = {}
        if handlers is not None:
            authorizers = {
                command: _marketplace_command_authorizer(
                    control_plane,
                    handlers,
                    command,
                )
                for command in commands
            }
        install_control_plane_modules(
            control_plane,
            (
                ControlPlaneModule(
                    name=MARKETPLACE_MODULE,
                    resource_services=resources,
                    command_handlers=commands,
                    command_authorizers=authorizers,
                ),
            ),
        )
        return

    for collection, service in resources.items():
        control_plane.register_resource_service(collection, service)
    for command, handler in commands.items():
        control_plane.register_command(command, handler)


def _distribution_commands(
    distribution: DistributionService,
    handlers: RegistryCommandHandlers,
) -> dict[str, CommandHandler]:
    commands: dict[str, CommandHandler] = {
        REGISTRY_PREVIEW_COMMAND: handlers.preview,
        MARKETPLACE_PREVIEW_COMMAND: handlers.marketplace_preview,
    }
    if distribution.activation_enabled:
        commands[REGISTRY_ACTIVATE_COMMAND] = handlers.activate
    if distribution.installation_state_enabled:
        commands.update(
            {
                MARKETPLACE_INSTALL_COMMAND: handlers.marketplace_install,
                MARKETPLACE_UPDATE_COMMAND: handlers.marketplace_update,
                MARKETPLACE_UNINSTALL_COMMAND: handlers.marketplace_uninstall,
                REGISTRY_PIN_COMMAND: handlers.pin,
                REGISTRY_UNPIN_COMMAND: handlers.unpin,
            }
        )
    return commands


def _marketplace_command_authorizer(
    control_plane: ControlPlane,
    handlers: RegistryCommandHandlers,
    command: str,
) -> CommandAuthorizer:
    async def authorize(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> None:
        if command in {
            MARKETPLACE_INSTALL_COMMAND,
            MARKETPLACE_UPDATE_COMMAND,
        }:
            await control_plane._authorize(
                context,
                MARKETPLACE_PREVIEW_COMMAND,
                resource_ref,
                request_payload_digest=_payload_digest(payload),
            )
            version = _required_version(payload, command)
            source_registry = _optional_source_registry(payload)
            _, candidate_preview = await handlers._resolve_preview(
                context,
                resource_ref,
                version,
                source_registry=source_registry,
            )
            await control_plane._authorize(
                context,
                command,
                resource_ref,
                request_payload_digest=_payload_digest(
                    _marketplace_candidate_authorization_payload(candidate_preview)
                ),
            )
            return

        if command == MARKETPLACE_UNINSTALL_COMMAND:
            await control_plane._authorize(
                context,
                MARKETPLACE_PREVIEW_COMMAND,
                resource_ref,
                request_payload_digest=_payload_digest(payload),
            )
            uninstall_preview = handlers._resolve_uninstall_preview(resource_ref, payload)
            await control_plane._authorize(
                context,
                command,
                resource_ref,
                request_payload_digest=_payload_digest(
                    _marketplace_uninstall_authorization_payload(uninstall_preview)
                ),
            )
            return

        await control_plane._authorize(
            context,
            command,
            resource_ref,
            request_payload_digest=_payload_digest(payload),
        )

    return authorize


def _marketplace_candidate_authorization_payload(
    preview: DistributionPreview,
) -> dict[str, JsonValue]:
    return {
        "item_id": preview.item.item_id,
        "version": preview.item.version,
        "source_registry": preview.item.source_registry,
        "artifact_sha256": preview.artifact_sha256,
        "route": preview.route.value,
        "decision": _decision_resource(preview.decision),
    }


def _marketplace_uninstall_authorization_payload(
    preview: DistributionUninstallPreview,
) -> dict[str, JsonValue]:
    return {
        "item_id": preview.item.item_id,
        "version": preview.item.version,
        "source_registry": preview.item.source_registry,
        "route": preview.route.value,
        "decision": _decision_resource(preview.decision),
    }
