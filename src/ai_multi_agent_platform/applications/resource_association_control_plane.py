"""Control Plane discovery for declarative Application resource associations."""

from __future__ import annotations

from hashlib import sha256

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue
from ai_multi_agent_platform.control_plane.extensions import (
    ControlPlane,
    ControlPlaneModule,
    ResourceService,
)
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.control_plane.module_registry import install_control_plane_modules

from .definition import Application
from .repository import ApplicationRepository
from .resource_associations import (
    ApplicationResourceAssociationResolver,
    ApplicationResourceResolution,
)

APPLICATION_RESOURCE_HANDLER_COLLECTION = "application-resource-handlers"
APPLICATION_RESOURCE_HANDLER_MODULE = "applications.resource-associations"


class ApplicationResourceHandlerService(ResourceService):
    """Read installed Applications as generic media/resource-type handlers."""

    def __init__(
        self,
        repository: ApplicationRepository,
        resolver: ApplicationResourceAssociationResolver,
    ) -> None:
        self._repository = repository
        self._resolver = resolver

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return _all_handler_resources(self._repository, self._resolver)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        for resource in _all_handler_resources(self._repository, self._resolver):
            if resource["id"] == resource_id:
                return resource
        raise ContractError(
            ErrorCode.NOT_FOUND,
            f"application resource handler not found: {resource_id}",
        )


def application_resource_handler_module(
    repository: ApplicationRepository,
) -> ControlPlaneModule:
    """Build the Application-owned optional resource-association discovery module."""

    resolver = ApplicationResourceAssociationResolver(repository)
    return ControlPlaneModule(
        name=APPLICATION_RESOURCE_HANDLER_MODULE,
        resource_services={
            APPLICATION_RESOURCE_HANDLER_COLLECTION: ApplicationResourceHandlerService(
                repository,
                resolver,
            )
        },
        requires=frozenset({"applications"}),
    )


def register_application_resource_handlers(
    control_plane: ControlPlane,
    repository: ApplicationRepository,
) -> None:
    """Register generic Application handler discovery on the canonical Control Plane."""

    install_control_plane_modules(
        control_plane,
        (application_resource_handler_module(repository),),
    )


def _all_handler_resources(
    repository: ApplicationRepository,
    resolver: ApplicationResourceAssociationResolver,
) -> tuple[dict[str, JsonValue], ...]:
    resources: list[dict[str, JsonValue]] = []
    emitted: set[tuple[str, str, str]] = set()
    for application in repository.list_applications():
        for association in application.manifest.resource_associations:
            for media_type in association.media_types:
                normalized = _normalized_media_type(media_type)
                key = (_application_ref(application), "media_type", normalized)
                if key in emitted:
                    continue
                emitted.add(key)
                resolution = _single_resolution(
                    resolver.resolve(media_type=normalized),
                    application,
                )
                resources.append(
                    _handler_resource(
                        resolution,
                        association_kind="media_type",
                        association_value=normalized,
                    )
                )
            for resource_type in association.resource_types:
                normalized = resource_type.strip().casefold()
                key = (_application_ref(application), "resource_type", normalized)
                if key in emitted:
                    continue
                emitted.add(key)
                resolution = _single_resolution(
                    resolver.resolve(resource_type=normalized),
                    application,
                )
                resources.append(
                    _handler_resource(
                        resolution,
                        association_kind="resource_type",
                        association_value=normalized,
                    )
                )
    return tuple(sorted(resources, key=lambda item: str(item["id"])))


def _single_resolution(
    resolutions: tuple[ApplicationResourceResolution, ...],
    application: Application,
) -> ApplicationResourceResolution:
    for resolution in resolutions:
        if (
            resolution.application.application_id == application.application_id
            and resolution.application.version == application.version
        ):
            return resolution
    raise RuntimeError("installed Application association could not resolve itself")


def _handler_resource(
    resolution: ApplicationResourceResolution,
    *,
    association_kind: str,
    association_value: str,
) -> dict[str, JsonValue]:
    application = resolution.application
    open_instances: list[JsonValue] = []
    for instance in resolution.openable_instances:
        endpoint = instance.open_endpoint(application.manifest)
        if endpoint is None or application.manifest.ui is None:
            continue
        open_instances.append(
            {
                "instance_id": instance.instance_id,
                "health": instance.health.value,
                "observed_state": instance.observed_state.value,
                "endpoint_ref": endpoint.endpoint_ref,
                "uri": endpoint.uri,
                "open_mode": application.manifest.ui.open_mode.value,
            }
        )
    identifier = _handler_id(
        resolution.application_ref,
        association_kind,
        association_value,
    )
    return {
        "id": identifier,
        "type": "application-resource-handler",
        "application_ref": resolution.application_ref,
        "application_id": application.application_id,
        "application_version": application.version,
        "name": application.name,
        "runtime_id": application.runtime_id,
        "association_kind": association_kind,
        "association_value": association_value,
        "media_type": association_value if association_kind == "media_type" else None,
        "resource_type": association_value if association_kind == "resource_type" else None,
        "instance_ids": [instance.instance_id for instance in resolution.instances],
        "open_instances": open_instances,
    }


def _handler_id(application_ref: str, association_kind: str, association_value: str) -> str:
    digest = sha256(
        f"{application_ref}\0{association_kind}\0{association_value}".encode("utf-8")
    ).hexdigest()[:32]
    return f"application_resource_handler_{digest}"


def _application_ref(application: Application) -> str:
    return f"{application.application_id}@{application.version}"


def _normalized_media_type(value: str) -> str:
    return value.split(";", maxsplit=1)[0].strip().casefold()


__all__ = [
    "APPLICATION_RESOURCE_HANDLER_COLLECTION",
    "APPLICATION_RESOURCE_HANDLER_MODULE",
    "ApplicationResourceHandlerService",
    "application_resource_handler_module",
    "register_application_resource_handlers",
]
