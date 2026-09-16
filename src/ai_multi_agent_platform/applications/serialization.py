"""Canonical JSON-safe serialization for durable Application state."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.security import SecretReference

from .definition import Application
from .manifest import application_manifest_from_document
from .models import (
    ApplicationDesiredState,
    ApplicationEndpointExposure,
    ApplicationEndpointResolution,
    ApplicationHealthStatus,
    ApplicationInstance,
    ApplicationManifest,
    ApplicationObservedState,
    ApplicationServiceState,
    ApplicationVolumeBinding,
    ApplicationVolumeKind,
)


def manifest_to_document(manifest: ApplicationManifest) -> dict[str, JsonValue]:
    """Return the validated manifest as a JSON-safe provider-neutral document."""

    services: list[JsonValue] = []
    for service in manifest.services:
        endpoints: list[JsonValue] = [
            {
                "name": endpoint.name,
                "target_port": endpoint.target_port,
                "protocol": endpoint.protocol.value,
                "exposure": endpoint.exposure.value,
                "path": endpoint.path,
            }
            for endpoint in service.endpoints
        ]
        mounts: list[JsonValue] = [
            {
                "volume_name": mount.volume_name,
                "target": mount.target,
                "read_only": mount.read_only,
            }
            for mount in service.mounts
        ]
        health_check: JsonValue = None
        if service.health_check is not None:
            health_check = {
                "kind": service.health_check.kind.value,
                "endpoint_name": service.health_check.endpoint_name,
                "command": list(service.health_check.command),
                "interval_seconds": service.health_check.interval_seconds,
                "timeout_seconds": service.health_check.timeout_seconds,
                "retries": service.health_check.retries,
            }
        services.append(
            {
                "service_id": service.service_id,
                "runtime": service.runtime.value,
                "image": service.image,
                "process": list(service.process),
                "command": list(service.command),
                "depends_on": list(service.depends_on),
                "endpoints": endpoints,
                "mounts": mounts,
                "health_check": health_check,
            }
        )

    volumes: list[JsonValue] = [
        {"name": volume.name, "kind": volume.kind.value, "required": volume.required}
        for volume in manifest.volumes
    ]
    configuration: list[JsonValue] = [
        {
            "name": item.name,
            "value_type": item.value_type.value,
            "required": item.required,
            "default": item.default,
            "mutable": item.mutable,
            "environment_variable": item.environment_variable,
        }
        for item in manifest.configuration
    ]
    secrets: list[JsonValue] = [
        {
            "name": item.name,
            "required": item.required,
            "environment_variable": item.environment_variable,
        }
        for item in manifest.secrets
    ]
    associations: list[JsonValue] = [
        {
            "media_types": list(item.media_types),
            "resource_types": list(item.resource_types),
        }
        for item in manifest.resource_associations
    ]
    resources: dict[str, JsonValue] = {
        "cpu_cores": manifest.resources.cpu_cores,
        "memory_bytes": manifest.resources.memory_bytes,
        "gpu_count": manifest.resources.gpu_count,
        "disk_bytes": manifest.resources.disk_bytes,
        "architectures": list(manifest.resources.architectures),
        "operating_systems": list(manifest.resources.operating_systems),
        "required_capabilities": list(manifest.resources.required_capabilities),
        "required_labels": list(manifest.resources.required_labels),
    }
    ui: JsonValue = None
    if manifest.ui is not None:
        ui = {
            "endpoint_ref": manifest.ui.endpoint_ref,
            "open_mode": manifest.ui.open_mode.value,
        }
    return {
        "schema_version": manifest.schema_version,
        "application_id": manifest.application_id,
        "name": manifest.name,
        "version": manifest.version,
        "description": manifest.description,
        "services": services,
        "volumes": volumes,
        "configuration": configuration,
        "secrets": secrets,
        "resources": resources,
        "ui": ui,
        "resource_associations": associations,
        "maturity": manifest.maturity.value,
        "runtime_requirements": list(manifest.runtime_requirements),
    }


def application_to_document(application: Application) -> dict[str, JsonValue]:
    return {
        "manifest": manifest_to_document(application.manifest),
        "runtime_id": application.runtime_id,
        "source_ref": application.source_ref,
        "provenance": dict(application.provenance),
        "installed_at": application.installed_at.isoformat(),
    }


def application_from_document(document: Mapping[str, Any]) -> Application:
    raw = dict(document)
    return Application(
        manifest=application_manifest_from_document(_object(raw.get("manifest"), "manifest")),
        runtime_id=_string(raw.get("runtime_id"), "runtime_id"),
        source_ref=_optional_string(raw.get("source_ref"), "source_ref"),
        provenance=cast(Mapping[str, JsonValue], _object(raw.get("provenance", {}), "provenance")),
        installed_at=_datetime(raw.get("installed_at"), "installed_at"),
    )


def instance_to_document(instance: ApplicationInstance) -> dict[str, JsonValue]:
    secrets: dict[str, JsonValue] = {
        name: reference.to_dict() for name, reference in instance.secret_bindings.items()
    }
    volume_bindings: list[JsonValue] = [
        {
            "volume_name": binding.volume_name,
            "kind": binding.kind.value,
            "source_ref": binding.source_ref,
            "read_only": binding.read_only,
        }
        for binding in instance.volume_bindings
    ]
    service_states: list[JsonValue] = [
        {
            "service_id": state.service_id,
            "observed_state": state.observed_state.value,
            "health": state.health.value,
            "message": state.message,
        }
        for state in instance.service_states
    ]
    endpoints: list[JsonValue] = [
        {
            "endpoint_ref": endpoint.endpoint_ref,
            "uri": endpoint.uri,
            "exposure": endpoint.exposure.value,
        }
        for endpoint in instance.endpoints
    ]
    return {
        "application_id": instance.application_id,
        "application_version": instance.application_version,
        "runtime_id": instance.runtime_id,
        "desired_state": instance.desired_state.value,
        "observed_state": instance.observed_state.value,
        "health": instance.health.value,
        "instance_id": instance.instance_id,
        "node_id": instance.node_id,
        "configuration": dict(instance.configuration),
        "secret_bindings": secrets,
        "volume_bindings": volume_bindings,
        "service_states": service_states,
        "endpoints": endpoints,
        "revision": instance.revision,
        "created_at": instance.created_at.isoformat(),
        "updated_at": instance.updated_at.isoformat(),
    }


def instance_from_document(document: Mapping[str, Any]) -> ApplicationInstance:
    raw = dict(document)
    secret_bindings = {
        name: _secret_reference(_object(value, f"secret_bindings.{name}"))
        for name, value in _object(raw.get("secret_bindings", {}), "secret_bindings").items()
    }
    volume_bindings = tuple(
        ApplicationVolumeBinding(
            volume_name=_string(item.get("volume_name"), "volume_name"),
            kind=ApplicationVolumeKind(_string(item.get("kind"), "volume kind")),
            source_ref=_string(item.get("source_ref"), "source_ref"),
            read_only=_boolean(item.get("read_only", False), "read_only"),
        )
        for item in _object_list(raw.get("volume_bindings", []), "volume_bindings")
    )
    service_states = tuple(
        ApplicationServiceState(
            service_id=_string(item.get("service_id"), "service_id"),
            observed_state=ApplicationObservedState(
                _string(item.get("observed_state"), "observed_state")
            ),
            health=ApplicationHealthStatus(_string(item.get("health"), "health")),
            message=_optional_string(item.get("message"), "message"),
        )
        for item in _object_list(raw.get("service_states", []), "service_states")
    )
    endpoints = tuple(
        ApplicationEndpointResolution(
            endpoint_ref=_string(item.get("endpoint_ref"), "endpoint_ref"),
            uri=_string(item.get("uri"), "uri"),
            exposure=ApplicationEndpointExposure(_string(item.get("exposure"), "exposure")),
        )
        for item in _object_list(raw.get("endpoints", []), "endpoints")
    )
    return ApplicationInstance(
        application_id=_string(raw.get("application_id"), "application_id"),
        application_version=_string(raw.get("application_version"), "application_version"),
        runtime_id=_string(raw.get("runtime_id"), "runtime_id"),
        desired_state=ApplicationDesiredState(_string(raw.get("desired_state"), "desired_state")),
        observed_state=ApplicationObservedState(
            _string(raw.get("observed_state"), "observed_state")
        ),
        health=ApplicationHealthStatus(_string(raw.get("health"), "health")),
        instance_id=_string(raw.get("instance_id"), "instance_id"),
        node_id=_optional_string(raw.get("node_id"), "node_id"),
        configuration=cast(
            Mapping[str, JsonValue], _object(raw.get("configuration", {}), "configuration")
        ),
        secret_bindings=secret_bindings,
        volume_bindings=volume_bindings,
        service_states=service_states,
        endpoints=endpoints,
        revision=_integer(raw.get("revision"), "revision"),
        created_at=_datetime(raw.get("created_at"), "created_at"),
        updated_at=_datetime(raw.get("updated_at"), "updated_at"),
    )


def _secret_reference(raw: dict[str, Any]) -> SecretReference:
    return SecretReference(
        provider=_string(raw.get("provider"), "secret provider"),
        secret_id=_string(raw.get("secret_id"), "secret_id"),
        scope=_string(raw.get("scope"), "secret scope"),
        version=_optional_string(raw.get("version"), "secret version"),
        metadata=cast(Mapping[str, JsonValue], _object(raw.get("metadata", {}), "secret metadata")),
    )


def _object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    return dict(value)


def _object_list(value: Any, field_name: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array")
    return tuple(_object(item, field_name) for item in value)


def _string(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _string(value, field_name)


def _boolean(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _integer(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _datetime(value: Any, field_name: str) -> datetime:
    text = _string(value, field_name)
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 datetime") from exc
