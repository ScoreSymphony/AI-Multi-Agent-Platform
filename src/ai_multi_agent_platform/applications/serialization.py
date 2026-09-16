"""Canonical JSON serialization for durable Application state."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import cast

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.security import SecretReference

from .definition import Application
from .manifest import application_manifest_from_document
from .models import (
    ApplicationDesiredState,
    ApplicationEndpointResolution,
    ApplicationHealthStatus,
    ApplicationInstance,
    ApplicationManifest,
    ApplicationObservedState,
    ApplicationServiceState,
    ApplicationVolumeBinding,
    ApplicationVolumeKind,
)


def encode_application(application: Application) -> str:
    return _encode(
        {
            "manifest": application_manifest_to_document(application.manifest),
            "runtime_id": application.runtime_id,
            "source_ref": application.source_ref,
            "provenance": _json_object(application.provenance),
            "installed_at": application.installed_at.isoformat(),
        }
    )


def decode_application(encoded: str) -> Application:
    data = _decode(encoded)
    return Application(
        manifest=application_manifest_from_document(_object(data.get("manifest"), "manifest")),
        runtime_id=_string(data, "runtime_id"),
        source_ref=_optional_string(data.get("source_ref"), "source_ref"),
        provenance=_object(data.get("provenance"), "provenance"),
        installed_at=_timestamp(data.get("installed_at"), "installed_at"),
    )


def encode_application_instance(instance: ApplicationInstance) -> str:
    return _encode(
        {
            "application_id": instance.application_id,
            "application_version": instance.application_version,
            "runtime_id": instance.runtime_id,
            "desired_state": instance.desired_state.value,
            "observed_state": instance.observed_state.value,
            "health": instance.health.value,
            "instance_id": instance.instance_id,
            "node_id": instance.node_id,
            "configuration": _json_object(instance.configuration),
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
            "revision": instance.revision,
            "created_at": instance.created_at.isoformat(),
            "updated_at": instance.updated_at.isoformat(),
        }
    )


def decode_application_instance(encoded: str) -> ApplicationInstance:
    data = _decode(encoded)
    raw_secrets = _object(data.get("secret_bindings"), "secret_bindings")
    return ApplicationInstance(
        application_id=_string(data, "application_id"),
        application_version=_string(data, "application_version"),
        runtime_id=_string(data, "runtime_id"),
        desired_state=ApplicationDesiredState(_string(data, "desired_state")),
        observed_state=ApplicationObservedState(_string(data, "observed_state")),
        health=ApplicationHealthStatus(_string(data, "health")),
        instance_id=_string(data, "instance_id"),
        node_id=_optional_string(data.get("node_id"), "node_id"),
        configuration=_object(data.get("configuration"), "configuration"),
        secret_bindings={
            name: _secret_reference_from_json(value) for name, value in raw_secrets.items()
        },
        volume_bindings=tuple(
            _volume_binding_from_json(item)
            for item in _array(data.get("volume_bindings"), "volume_bindings")
        ),
        service_states=tuple(
            _service_state_from_json(item)
            for item in _array(data.get("service_states"), "service_states")
        ),
        endpoints=tuple(
            _endpoint_resolution_from_json(item)
            for item in _array(data.get("endpoints"), "endpoints")
        ),
        revision=_positive_int(data.get("revision"), "revision"),
        created_at=_timestamp(data.get("created_at"), "created_at"),
        updated_at=_timestamp(data.get("updated_at"), "updated_at"),
    )


def application_manifest_to_document(manifest: ApplicationManifest) -> dict[str, JsonValue]:
    return {
        "schema_version": manifest.schema_version,
        "application_id": manifest.application_id,
        "name": manifest.name,
        "version": manifest.version,
        "description": manifest.description,
        "services": [
            {
                "service_id": service.service_id,
                "runtime": service.runtime.value,
                "image": service.image,
                "process": list(service.process),
                "command": list(service.command),
                "depends_on": list(service.depends_on),
                "endpoints": [
                    {
                        "name": endpoint.name,
                        "target_port": endpoint.target_port,
                        "protocol": endpoint.protocol.value,
                        "exposure": endpoint.exposure.value,
                        "path": endpoint.path,
                    }
                    for endpoint in service.endpoints
                ],
                "mounts": [
                    {
                        "volume_name": mount.volume_name,
                        "target": mount.target,
                        "read_only": mount.read_only,
                    }
                    for mount in service.mounts
                ],
                "health_check": (
                    None
                    if service.health_check is None
                    else {
                        "kind": service.health_check.kind.value,
                        "endpoint_name": service.health_check.endpoint_name,
                        "command": list(service.health_check.command),
                        "interval_seconds": service.health_check.interval_seconds,
                        "timeout_seconds": service.health_check.timeout_seconds,
                        "retries": service.health_check.retries,
                    }
                ),
            }
            for service in manifest.services
        ],
        "volumes": [
            {"name": volume.name, "kind": volume.kind.value, "required": volume.required}
            for volume in manifest.volumes
        ],
        "configuration": [
            {
                "name": item.name,
                "value_type": item.value_type.value,
                "required": item.required,
                "default": _json_value(item.default),
                "mutable": item.mutable,
                "environment_variable": item.environment_variable,
            }
            for item in manifest.configuration
        ],
        "secrets": [
            {
                "name": item.name,
                "required": item.required,
                "environment_variable": item.environment_variable,
            }
            for item in manifest.secrets
        ],
        "resources": {
            "cpu_cores": manifest.resources.cpu_cores,
            "memory_bytes": manifest.resources.memory_bytes,
            "gpu_count": manifest.resources.gpu_count,
            "disk_bytes": manifest.resources.disk_bytes,
            "architectures": list(manifest.resources.architectures),
            "operating_systems": list(manifest.resources.operating_systems),
            "required_capabilities": list(manifest.resources.required_capabilities),
            "required_labels": list(manifest.resources.required_labels),
        },
        "ui": (
            None
            if manifest.ui is None
            else {
                "endpoint_ref": manifest.ui.endpoint_ref,
                "open_mode": manifest.ui.open_mode.value,
            }
        ),
        "resource_associations": [
            {
                "media_types": list(association.media_types),
                "resource_types": list(association.resource_types),
            }
            for association in manifest.resource_associations
        ],
        "maturity": manifest.maturity.value,
        "runtime_requirements": list(manifest.runtime_requirements),
    }


def _volume_binding_from_json(value: object) -> ApplicationVolumeBinding:
    data = _object(value, "volume_binding")
    return ApplicationVolumeBinding(
        volume_name=_string(data, "volume_name"),
        kind=ApplicationVolumeKind(_string(data, "kind")),
        source_ref=_string(data, "source_ref"),
        read_only=_boolean(data.get("read_only"), "read_only"),
    )


def _service_state_from_json(value: object) -> ApplicationServiceState:
    data = _object(value, "service_state")
    return ApplicationServiceState(
        service_id=_string(data, "service_id"),
        observed_state=ApplicationObservedState(_string(data, "observed_state")),
        health=ApplicationHealthStatus(_string(data, "health")),
        message=_optional_string(data.get("message"), "message"),
    )


def _endpoint_resolution_from_json(value: object) -> ApplicationEndpointResolution:
    data = _object(value, "endpoint")
    from .models import ApplicationEndpointExposure

    return ApplicationEndpointResolution(
        endpoint_ref=_string(data, "endpoint_ref"),
        uri=_string(data, "uri"),
        exposure=ApplicationEndpointExposure(_string(data, "exposure")),
    )


def _secret_reference_from_json(value: object) -> SecretReference:
    data = _object(value, "secret_reference")
    return SecretReference(
        provider=_string(data, "provider"),
        secret_id=_string(data, "secret_id"),
        scope=_string(data, "scope"),
        version=_optional_string(data.get("version"), "secret_reference.version"),
        metadata=_object(data.get("metadata"), "secret_reference.metadata"),
    )


def _encode(value: Mapping[str, JsonValue]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _decode(encoded: str) -> dict[str, JsonValue]:
    value = json.loads(encoded)
    if not isinstance(value, dict):
        raise ValueError("persisted application payload must be a JSON object")
    return cast(dict[str, JsonValue], value)


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    raise TypeError(f"value is not JSON-compatible: {type(value).__name__}")


def _json_object(value: Mapping[str, object]) -> dict[str, JsonValue]:
    return {str(key): _json_value(item) for key, item in value.items()}


def _object(value: object, field_name: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return cast(dict[str, JsonValue], value)


def _array(value: object, field_name: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array")
    return cast(list[JsonValue], value)


def _string(data: Mapping[str, object], field_name: str) -> str:
    value = data.get(field_name)
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string or null")
    return value


def _boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _timestamp(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO timestamp string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed
