"""Control Plane resources and lifecycle commands for canonical Applications."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue
from ai_multi_agent_platform.control_plane.extensions import (
    CommandAuthorizer,
    CommandHandler,
    ControlPlane,
    ControlPlaneModule,
    ResourceService,
)
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.control_plane.module_registry import install_control_plane_modules
from ai_multi_agent_platform.control_plane.service import _payload_digest
from ai_multi_agent_platform.security import SecretReference

from .definition import Application
from .manifest import application_manifest_from_document
from .models import (
    ApplicationInstallRequest,
    ApplicationInstance,
    ApplicationVolumeBinding,
    ApplicationVolumeKind,
)
from .repository import ApplicationRepository
from .runtime import (
    ApplicationPreparationError,
    ApplicationRuntimeError,
    ApplicationRuntimeUnavailableError,
)
from .serialization import application_manifest_to_document
from .service import ApplicationLifecycleService

APPLICATION_COLLECTION = "applications"
APPLICATION_INSTANCE_COLLECTION = "application-instances"
APPLICATION_COLLECTIONS = (APPLICATION_COLLECTION, APPLICATION_INSTANCE_COLLECTION)
APPLICATION_MODULE = "applications"
APPLICATION_COMMANDS = (
    "application.install",
    "application.start",
    "application.stop",
    "application.restart",
    "application.remove",
    "application.reconcile",
)


class ApplicationResourceService(ResourceService):
    """Read installed immutable Application definitions."""

    def __init__(self, repository: ApplicationRepository) -> None:
        self._repository = repository

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(_application_resource(item) for item in self._repository.list_applications())

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        application_id, version = _parse_application_ref(resource_id)
        return _application_resource(self._repository.get_application(application_id, version))


class ApplicationInstanceResourceService(ResourceService):
    """Read Application Instances and refresh runtime observation on direct reads."""

    def __init__(
        self,
        lifecycle: ApplicationLifecycleService,
        repository: ApplicationRepository,
    ) -> None:
        self._lifecycle = lifecycle
        self._repository = repository

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(
            _instance_resource(self._repository, item) for item in self._repository.list_instances()
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        instance = await _runtime_boundary(self._lifecycle.status(resource_id))
        return _instance_resource(self._repository, instance)


def application_control_plane_module(
    control_plane: ControlPlane,
    lifecycle: ApplicationLifecycleService,
    repository: ApplicationRepository,
) -> ControlPlaneModule:
    """Build the explicitly owned Application Adapter northbound module."""

    command_handlers = _command_handlers(lifecycle, repository)
    return ControlPlaneModule(
        name=APPLICATION_MODULE,
        resource_services={
            APPLICATION_COLLECTION: ApplicationResourceService(repository),
            APPLICATION_INSTANCE_COLLECTION: ApplicationInstanceResourceService(
                lifecycle,
                repository,
            ),
        },
        command_handlers=command_handlers,
        command_authorizers={
            command: _payload_bound_authorizer(control_plane, command)
            for command in command_handlers
        },
    )


def register_application_control_plane(
    control_plane: ControlPlane,
    lifecycle: ApplicationLifecycleService,
    repository: ApplicationRepository,
) -> None:
    """Install Application resources and lifecycle commands into the canonical registry."""

    install_control_plane_modules(
        control_plane,
        (application_control_plane_module(control_plane, lifecycle, repository),),
    )


def _command_handlers(
    lifecycle: ApplicationLifecycleService,
    repository: ApplicationRepository,
) -> dict[str, CommandHandler]:
    async def install(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        request, runtime_id, source_ref = _install_request(resource_ref, payload)
        instance = await _runtime_boundary(
            lifecycle.install(
                request,
                runtime_id=runtime_id,
                source_ref=source_ref,
            )
        )
        return _instance_resource(repository, instance)

    async def transition(
        operation: Callable[[str], Awaitable[ApplicationInstance]],
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_empty_payload(payload)
        instance = await _runtime_boundary(operation(resource_ref))
        return _instance_resource(repository, instance)

    async def start(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        return await transition(lifecycle.start, resource_ref, payload)

    async def stop(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        return await transition(lifecycle.stop, resource_ref, payload)

    async def restart(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        return await transition(lifecycle.restart, resource_ref, payload)

    async def remove(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        return await transition(lifecycle.remove, resource_ref, payload)

    async def reconcile(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        return await transition(lifecycle.reconcile, resource_ref, payload)

    return {
        "application.install": install,
        "application.start": start,
        "application.stop": stop,
        "application.restart": restart,
        "application.remove": remove,
        "application.reconcile": reconcile,
    }


def _payload_bound_authorizer(
    control_plane: ControlPlane,
    command: str,
) -> CommandAuthorizer:
    async def authorize(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> None:
        await control_plane._authorize(
            context,
            command,
            resource_ref,
            request_payload_digest=_payload_digest(payload),
        )

    return authorize


def _install_request(
    resource_ref: str,
    payload: dict[str, JsonValue],
) -> tuple[ApplicationInstallRequest, str, str | None]:
    allowed = {
        "manifest",
        "runtime_id",
        "source_ref",
        "configuration",
        "secret_bindings",
        "volume_bindings",
        "node_id",
    }
    unknown = sorted(set(payload).difference(allowed))
    if unknown:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"unknown application install fields: {unknown!r}",
        )

    manifest = application_manifest_from_document(_required_object(payload, "manifest"))
    if resource_ref != manifest.application_id:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "application.install resource_ref must equal manifest application_id",
            details={
                "resource_ref": resource_ref,
                "application_id": manifest.application_id,
            },
        )
    runtime_id = _required_string(payload, "runtime_id")
    source_ref = _optional_string(payload, "source_ref")
    configuration = _optional_object(payload, "configuration")
    secrets = {
        name: _secret_reference(value, field_name=f"secret_bindings.{name}")
        for name, value in _optional_object(payload, "secret_bindings").items()
    }
    volumes = tuple(_volume_binding(item) for item in _optional_array(payload, "volume_bindings"))
    node_id = _optional_string(payload, "node_id")
    return (
        ApplicationInstallRequest(
            manifest=manifest,
            configuration=configuration,
            secret_bindings=secrets,
            volume_bindings=volumes,
            node_id=node_id,
        ),
        runtime_id,
        source_ref,
    )


async def _runtime_boundary(
    operation: Awaitable[ApplicationInstance],
) -> ApplicationInstance:
    try:
        return await operation
    except ApplicationPreparationError as exc:
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            str(exc),
        ) from exc
    except ApplicationRuntimeUnavailableError as exc:
        raise ContractError(
            ErrorCode.UNAVAILABLE,
            str(exc),
            retryable=True,
        ) from exc
    except ApplicationRuntimeError as exc:
        raise ContractError(
            ErrorCode.BACKEND_ERROR,
            str(exc),
        ) from exc


def _application_ref(application: Application) -> str:
    return f"{application.application_id}@{application.version}"


def _parse_application_ref(resource_id: str) -> tuple[str, str]:
    application_id, separator, version = resource_id.rpartition("@")
    if not separator or not application_id or not version:
        raise ContractError(
            ErrorCode.NOT_FOUND,
            f"application resource not found: {resource_id}",
        )
    return application_id, version


def _application_resource(application: Application) -> dict[str, JsonValue]:
    return {
        "id": _application_ref(application),
        "type": "application",
        "application_id": application.application_id,
        "version": application.version,
        "name": application.name,
        "runtime_id": application.runtime_id,
        "source_ref": application.source_ref,
        "installed_at": application.installed_at.isoformat(),
        "manifest": application_manifest_to_document(application.manifest),
        "provenance": cast(dict[str, JsonValue], dict(application.provenance)),
    }


def _instance_resource(
    repository: ApplicationRepository,
    instance: ApplicationInstance,
) -> dict[str, JsonValue]:
    application = repository.get_application(
        instance.application_id,
        instance.application_version,
    )
    open_endpoint = instance.open_endpoint(application.manifest)
    open_resource: JsonValue = None
    if open_endpoint is not None and application.manifest.ui is not None:
        open_resource = {
            "endpoint_ref": open_endpoint.endpoint_ref,
            "uri": open_endpoint.uri,
            "open_mode": application.manifest.ui.open_mode.value,
        }
    return {
        "id": instance.instance_id,
        "type": "application-instance",
        "application_ref": _application_ref(application),
        "application_id": instance.application_id,
        "application_version": instance.application_version,
        "runtime_id": instance.runtime_id,
        "node_id": instance.node_id,
        "desired_state": instance.desired_state.value,
        "observed_state": instance.observed_state.value,
        "health": instance.health.value,
        "configuration": dict(instance.configuration),
        "secret_bindings": {
            name: reference.to_dict() for name, reference in instance.secret_bindings.items()
        },
        "volume_bindings": [
            {
                "volume_name": binding.volume_name,
                "kind": binding.kind.value,
                "source_ref": binding.source_ref,
                "read_only": binding.read_only,
            }
            for binding in instance.volume_bindings
        ],
        "service_states": [
            {
                "service_id": state.service_id,
                "observed_state": state.observed_state.value,
                "health": state.health.value,
                "message": state.message,
            }
            for state in instance.service_states
        ],
        "endpoints": [
            {
                "endpoint_ref": endpoint.endpoint_ref,
                "uri": endpoint.uri,
                "exposure": endpoint.exposure.value,
            }
            for endpoint in instance.endpoints
        ],
        "open": open_resource,
        "revision": instance.revision,
        "created_at": instance.created_at.isoformat(),
        "updated_at": instance.updated_at.isoformat(),
    }


def _secret_reference(value: JsonValue, *, field_name: str) -> SecretReference:
    data = _json_object(value, field_name)
    metadata = _json_object(data.get("metadata", {}), f"{field_name}.metadata")
    return SecretReference(
        provider=_object_string(data, "provider", field_name),
        secret_id=_object_string(data, "secret_id", field_name),
        scope=_object_string(data, "scope", field_name),
        version=_object_optional_string(data, "version", field_name),
        metadata=metadata,
    )


def _volume_binding(value: JsonValue) -> ApplicationVolumeBinding:
    data = _json_object(value, "volume_bindings[]")
    raw_kind = _object_string(data, "kind", "volume_bindings[]")
    try:
        kind = ApplicationVolumeKind(raw_kind)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"invalid application volume kind: {raw_kind}",
        ) from exc
    read_only = data.get("read_only", False)
    if not isinstance(read_only, bool):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "volume_bindings[].read_only must be a boolean",
        )
    return ApplicationVolumeBinding(
        volume_name=_object_string(data, "volume_name", "volume_bindings[]"),
        kind=kind,
        source_ref=_object_string(data, "source_ref", "volume_bindings[]"),
        read_only=read_only,
    )


def _require_empty_payload(payload: dict[str, JsonValue]) -> None:
    if payload:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "application lifecycle command does not accept a payload",
        )


def _required_object(
    payload: dict[str, JsonValue],
    name: str,
) -> dict[str, JsonValue]:
    if name not in payload:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} is required")
    return _json_object(payload[name], name)


def _optional_object(
    payload: dict[str, JsonValue],
    name: str,
) -> dict[str, JsonValue]:
    value = payload.get(name)
    if value is None:
        return {}
    return _json_object(value, name)


def _optional_array(
    payload: dict[str, JsonValue],
    name: str,
) -> list[JsonValue]:
    value = payload.get(name)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must be an array")
    return value


def _required_string(payload: dict[str, JsonValue], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{name} must be a non-blank string",
        )
    return value


def _optional_string(payload: dict[str, JsonValue], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{name} must be a non-blank string when provided",
        )
    return value


def _json_object(value: JsonValue, field_name: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{field_name} must be an object",
        )
    return cast(dict[str, JsonValue], value)


def _object_string(
    data: dict[str, JsonValue],
    name: str,
    field_name: str,
) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{field_name}.{name} must be a non-blank string",
        )
    return value


def _object_optional_string(
    data: dict[str, JsonValue],
    name: str,
    field_name: str,
) -> str | None:
    value = data.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{field_name}.{name} must be a non-blank string when provided",
        )
    return value


__all__ = [
    "APPLICATION_COLLECTION",
    "APPLICATION_COLLECTIONS",
    "APPLICATION_COMMANDS",
    "APPLICATION_INSTANCE_COLLECTION",
    "APPLICATION_MODULE",
    "ApplicationInstanceResourceService",
    "ApplicationResourceService",
    "application_control_plane_module",
    "register_application_control_plane",
]
