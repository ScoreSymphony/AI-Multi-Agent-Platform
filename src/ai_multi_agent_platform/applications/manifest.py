"""Validated declarative manifest schema for Application Adapters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jsonschema import Draft202012Validator, ValidationError  # type: ignore[import-untyped]

from .models import (
    APPLICATION_MANIFEST_SCHEMA_VERSION,
    ApplicationConfigValueType,
    ApplicationConfigurationField,
    ApplicationEndpoint,
    ApplicationEndpointExposure,
    ApplicationEndpointProtocol,
    ApplicationHealthCheck,
    ApplicationHealthCheckKind,
    ApplicationManifest,
    ApplicationMaturity,
    ApplicationResourceAssociation,
    ApplicationResourceRequirements,
    ApplicationSecretField,
    ApplicationService,
    ApplicationServiceRuntime,
    ApplicationUi,
    ApplicationUiOpenMode,
    ApplicationVolume,
    ApplicationVolumeKind,
    ApplicationVolumeMount,
)

_IDENTIFIER_PATTERN = r"^[a-z][a-z0-9_.-]{0,127}$"
_APPLICATION_ID_PATTERN = (
    r"^application_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}$"
)

APPLICATION_MANIFEST_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": [
        "schema_version",
        "application_id",
        "name",
        "version",
        "description",
        "services",
    ],
    "properties": {
        "schema_version": {"const": APPLICATION_MANIFEST_SCHEMA_VERSION},
        "application_id": {"type": "string", "pattern": _APPLICATION_ID_PATTERN},
        "name": {"type": "string", "minLength": 1},
        "version": {"type": "string", "minLength": 1},
        "description": {"type": "string", "minLength": 1},
        "maturity": {"enum": ["stable", "beta", "experimental"]},
        "runtime_requirements": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "uniqueItems": True,
        },
        "services": {
            "type": "array",
            "minItems": 1,
            "items": {"$ref": "#/$defs/service"},
        },
        "volumes": {
            "type": "array",
            "items": {"$ref": "#/$defs/volume"},
        },
        "configuration": {
            "type": "array",
            "items": {"$ref": "#/$defs/configuration"},
        },
        "secrets": {
            "type": "array",
            "items": {"$ref": "#/$defs/secret"},
        },
        "resources": {"$ref": "#/$defs/resources"},
        "ui": {
            "oneOf": [
                {"type": "null"},
                {"$ref": "#/$defs/ui"},
            ]
        },
        "resource_associations": {
            "type": "array",
            "items": {"$ref": "#/$defs/resource_association"},
        },
    },
    "$defs": {
        "endpoint": {
            "type": "object",
            "required": ["name", "target_port"],
            "properties": {
                "name": {"type": "string", "pattern": _IDENTIFIER_PATTERN},
                "target_port": {"type": "integer", "minimum": 1, "maximum": 65535},
                "protocol": {"enum": ["http", "https", "tcp"]},
                "exposure": {"enum": ["internal", "user", "api", "metrics"]},
                "path": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "mount": {
            "type": "object",
            "required": ["volume_name", "target"],
            "properties": {
                "volume_name": {"type": "string", "pattern": _IDENTIFIER_PATTERN},
                "target": {"type": "string", "minLength": 1},
                "read_only": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        "health_check": {
            "type": "object",
            "required": ["kind"],
            "properties": {
                "kind": {"enum": ["endpoint", "command"]},
                "endpoint_name": {
                    "type": ["string", "null"],
                    "pattern": _IDENTIFIER_PATTERN,
                },
                "command": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                },
                "interval_seconds": {"type": "number", "exclusiveMinimum": 0},
                "timeout_seconds": {"type": "number", "exclusiveMinimum": 0},
                "retries": {"type": "integer", "minimum": 1},
            },
            "additionalProperties": False,
        },
        "service": {
            "type": "object",
            "required": ["service_id", "runtime"],
            "properties": {
                "service_id": {"type": "string", "pattern": _IDENTIFIER_PATTERN},
                "runtime": {"enum": ["oci_image", "process"]},
                "image": {"type": ["string", "null"]},
                "process": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                },
                "command": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                },
                "depends_on": {
                    "type": "array",
                    "items": {"type": "string", "pattern": _IDENTIFIER_PATTERN},
                    "uniqueItems": True,
                },
                "endpoints": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/endpoint"},
                },
                "mounts": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/mount"},
                },
                "health_check": {
                    "oneOf": [
                        {"type": "null"},
                        {"$ref": "#/$defs/health_check"},
                    ]
                },
            },
            "additionalProperties": False,
        },
        "volume": {
            "type": "object",
            "required": ["name", "kind"],
            "properties": {
                "name": {"type": "string", "pattern": _IDENTIFIER_PATTERN},
                "kind": {"enum": ["workspace", "persistent", "ephemeral"]},
                "required": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        "configuration": {
            "type": "object",
            "required": ["name", "value_type"],
            "properties": {
                "name": {"type": "string", "pattern": _IDENTIFIER_PATTERN},
                "value_type": {"enum": ["string", "integer", "number", "boolean"]},
                "required": {"type": "boolean"},
                "default": {
                    "type": ["string", "integer", "number", "boolean", "null"],
                },
                "mutable": {"type": "boolean"},
                "environment_variable": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "secret": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "pattern": _IDENTIFIER_PATTERN},
                "required": {"type": "boolean"},
                "environment_variable": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "resources": {
            "type": "object",
            "properties": {
                "cpu_cores": {"type": ["number", "null"], "exclusiveMinimum": 0},
                "memory_bytes": {"type": ["integer", "null"], "minimum": 1},
                "gpu_count": {"type": "integer", "minimum": 0},
                "disk_bytes": {"type": ["integer", "null"], "minimum": 1},
                "architectures": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "uniqueItems": True,
                },
                "operating_systems": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "uniqueItems": True,
                },
                "required_capabilities": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "uniqueItems": True,
                },
                "required_labels": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "uniqueItems": True,
                },
            },
            "additionalProperties": False,
        },
        "ui": {
            "type": "object",
            "required": ["endpoint_ref"],
            "properties": {
                "endpoint_ref": {"type": "string", "minLength": 3},
                "open_mode": {"enum": ["embedded", "external"]},
            },
            "additionalProperties": False,
        },
        "resource_association": {
            "type": "object",
            "properties": {
                "media_types": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "uniqueItems": True,
                },
                "resource_types": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "uniqueItems": True,
                },
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}


def validate_application_manifest_document(document: Mapping[str, Any]) -> None:
    Draft202012Validator(APPLICATION_MANIFEST_SCHEMA).validate(dict(document))


def application_manifest_from_document(document: Mapping[str, Any]) -> ApplicationManifest:
    """Validate a provider-neutral document and construct the canonical manifest."""

    validate_application_manifest_document(document)
    raw = dict(document)
    return ApplicationManifest(
        application_id=str(raw["application_id"]),
        name=str(raw["name"]),
        version=str(raw["version"]),
        description=str(raw["description"]),
        services=tuple(_service_from_document(item) for item in _object_list(raw, "services")),
        volumes=tuple(_volume_from_document(item) for item in _object_list(raw, "volumes")),
        configuration=tuple(
            _configuration_from_document(item) for item in _object_list(raw, "configuration")
        ),
        secrets=tuple(_secret_from_document(item) for item in _object_list(raw, "secrets")),
        resources=_resources_from_document(_object(raw.get("resources", {}), "resources")),
        ui=_ui_from_document(raw.get("ui")),
        resource_associations=tuple(
            _association_from_document(item)
            for item in _object_list(raw, "resource_associations")
        ),
        maturity=ApplicationMaturity(str(raw.get("maturity", "beta"))),
        runtime_requirements=_string_tuple(raw.get("runtime_requirements", []), "runtime_requirements"),
        schema_version=str(raw["schema_version"]),
    )


def _service_from_document(raw: dict[str, Any]) -> ApplicationService:
    return ApplicationService(
        service_id=str(raw["service_id"]),
        runtime=ApplicationServiceRuntime(str(raw["runtime"])),
        image=_optional_string(raw.get("image"), "service image"),
        process=_string_tuple(raw.get("process", []), "service process"),
        command=_string_tuple(raw.get("command", []), "service command"),
        depends_on=_string_tuple(raw.get("depends_on", []), "service depends_on"),
        endpoints=tuple(
            _endpoint_from_document(item)
            for item in _object_list(raw, "endpoints")
        ),
        mounts=tuple(
            ApplicationVolumeMount(
                volume_name=str(item["volume_name"]),
                target=str(item["target"]),
                read_only=bool(item.get("read_only", False)),
            )
            for item in _object_list(raw, "mounts")
        ),
        health_check=_health_check_from_document(raw.get("health_check")),
    )


def _endpoint_from_document(raw: dict[str, Any]) -> ApplicationEndpoint:
    return ApplicationEndpoint(
        name=str(raw["name"]),
        target_port=int(raw["target_port"]),
        protocol=ApplicationEndpointProtocol(str(raw.get("protocol", "http"))),
        exposure=ApplicationEndpointExposure(str(raw.get("exposure", "internal"))),
        path=_optional_string(raw.get("path"), "endpoint path"),
    )


def _health_check_from_document(raw: Any) -> ApplicationHealthCheck | None:
    if raw is None:
        return None
    value = _object(raw, "health_check")
    return ApplicationHealthCheck(
        kind=ApplicationHealthCheckKind(str(value["kind"])),
        endpoint_name=_optional_string(value.get("endpoint_name"), "health endpoint_name"),
        command=_string_tuple(value.get("command", []), "health command"),
        interval_seconds=float(value.get("interval_seconds", 10.0)),
        timeout_seconds=float(value.get("timeout_seconds", 2.0)),
        retries=int(value.get("retries", 3)),
    )


def _volume_from_document(raw: dict[str, Any]) -> ApplicationVolume:
    return ApplicationVolume(
        name=str(raw["name"]),
        kind=ApplicationVolumeKind(str(raw["kind"])),
        required=bool(raw.get("required", True)),
    )


def _configuration_from_document(raw: dict[str, Any]) -> ApplicationConfigurationField:
    return ApplicationConfigurationField(
        name=str(raw["name"]),
        value_type=ApplicationConfigValueType(str(raw["value_type"])),
        required=bool(raw.get("required", False)),
        default=raw.get("default"),
        mutable=bool(raw.get("mutable", True)),
        environment_variable=_optional_string(
            raw.get("environment_variable"),
            "configuration environment_variable",
        ),
    )


def _secret_from_document(raw: dict[str, Any]) -> ApplicationSecretField:
    return ApplicationSecretField(
        name=str(raw["name"]),
        required=bool(raw.get("required", True)),
        environment_variable=_optional_string(
            raw.get("environment_variable"),
            "secret environment_variable",
        ),
    )


def _resources_from_document(raw: dict[str, Any]) -> ApplicationResourceRequirements:
    cpu = raw.get("cpu_cores")
    memory = raw.get("memory_bytes")
    disk = raw.get("disk_bytes")
    return ApplicationResourceRequirements(
        cpu_cores=None if cpu is None else float(cpu),
        memory_bytes=None if memory is None else int(memory),
        gpu_count=int(raw.get("gpu_count", 0)),
        disk_bytes=None if disk is None else int(disk),
        architectures=_string_tuple(raw.get("architectures", []), "architectures"),
        operating_systems=_string_tuple(
            raw.get("operating_systems", []),
            "operating_systems",
        ),
        required_capabilities=_string_tuple(
            raw.get("required_capabilities", []),
            "required_capabilities",
        ),
        required_labels=_string_tuple(raw.get("required_labels", []), "required_labels"),
    )


def _ui_from_document(raw: Any) -> ApplicationUi | None:
    if raw is None:
        return None
    value = _object(raw, "ui")
    return ApplicationUi(
        endpoint_ref=str(value["endpoint_ref"]),
        open_mode=ApplicationUiOpenMode(str(value.get("open_mode", "external"))),
    )


def _association_from_document(raw: dict[str, Any]) -> ApplicationResourceAssociation:
    return ApplicationResourceAssociation(
        media_types=_string_tuple(raw.get("media_types", []), "media_types"),
        resource_types=_string_tuple(raw.get("resource_types", []), "resource_types"),
    )


def _object_list(document: Mapping[str, Any], field_name: str) -> tuple[dict[str, Any], ...]:
    raw = document.get(field_name, [])
    if not isinstance(raw, list):
        raise ValidationError(f"{field_name} must be an array")
    return tuple(_object(item, field_name) for item in raw)


def _object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{field_name} must contain objects")
    return dict(value)


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValidationError(f"{field_name} must be an array")
    if any(not isinstance(item, str) for item in value):
        raise ValidationError(f"{field_name} must contain strings")
    return tuple(value)


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string or null")
    return value
