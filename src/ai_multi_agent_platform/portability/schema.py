"""Versioned JSON Schema for portable import/export packages."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import ValidationError  # type: ignore[import-untyped]

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode

from .models import (
    PORTABLE_FORMAT_VERSION,
    PORTABLE_INTEGRITY_ALGORITHM,
    DependencyKind,
    ExclusionCategory,
    IdPolicy,
)

_NONBLANK = {"type": "string", "minLength": 1}
_CHECKSUM = {"type": "string", "pattern": "^[0-9a-f]{64}$"}

_DEPENDENCY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["kind", "identifier", "required", "version_constraint", "purpose"],
    "properties": {
        "kind": {"enum": [item.value for item in DependencyKind]},
        "identifier": _NONBLANK,
        "required": {"type": "boolean"},
        "version_constraint": {"type": ["string", "null"], "minLength": 1},
        "purpose": {"type": ["string", "null"], "minLength": 1},
    },
    "additionalProperties": False,
}

_RESOURCE_DESCRIPTOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "resource_type",
        "resource_id",
        "resource_version",
        "id_policy",
        "checksum",
        "dependencies",
    ],
    "properties": {
        "resource_type": _NONBLANK,
        "resource_id": _NONBLANK,
        "resource_version": _NONBLANK,
        "id_policy": {"enum": [item.value for item in IdPolicy]},
        "checksum": _CHECKSUM,
        "dependencies": {"type": "array", "items": _DEPENDENCY_SCHEMA},
    },
    "additionalProperties": False,
}

_RESOURCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "resource_type",
        "resource_id",
        "resource_version",
        "id_policy",
        "dependencies",
        "payload",
        "checksum",
    ],
    "properties": {
        "resource_type": _NONBLANK,
        "resource_id": _NONBLANK,
        "resource_version": _NONBLANK,
        "id_policy": {"enum": [item.value for item in IdPolicy]},
        "dependencies": {"type": "array", "items": _DEPENDENCY_SCHEMA},
        "payload": {"type": "object"},
        "checksum": _CHECKSUM,
    },
    "additionalProperties": False,
}

_EXCLUSION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["category", "path", "reason", "resource_type", "resource_id"],
    "properties": {
        "category": {"enum": [item.value for item in ExclusionCategory]},
        "path": _NONBLANK,
        "reason": _NONBLANK,
        "resource_type": {"type": ["string", "null"], "minLength": 1},
        "resource_id": {"type": ["string", "null"], "minLength": 1},
    },
    "additionalProperties": False,
}

_PROVENANCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["source", "author", "source_instance_id", "metadata"],
    "properties": {
        "source": _NONBLANK,
        "author": {"type": ["string", "null"], "minLength": 1},
        "source_instance_id": {"type": ["string", "null"], "minLength": 1},
        "metadata": {"type": "object"},
    },
    "additionalProperties": False,
}

_COMPATIBILITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "minimum_platform_version",
        "maximum_platform_version",
        "contract_versions",
    ],
    "properties": {
        "minimum_platform_version": {"type": ["string", "null"], "minLength": 1},
        "maximum_platform_version": {"type": ["string", "null"], "minLength": 1},
        "contract_versions": {
            "type": "object",
            "additionalProperties": {"type": "string", "minLength": 1},
        },
    },
    "additionalProperties": False,
}

PORTABLE_PACKAGE_SCHEMA_V1: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://ai-multi-agent-platform.invalid/schemas/portable-package-v1.json",
    "type": "object",
    "required": ["manifest", "resources", "checksum"],
    "properties": {
        "manifest": {
            "type": "object",
            "required": [
                "format_version",
                "source_platform_version",
                "created_at",
                "integrity_algorithm",
                "resources",
                "requirements",
                "provenance",
                "compatibility",
                "excluded_state",
            ],
            "properties": {
                "format_version": {"const": PORTABLE_FORMAT_VERSION},
                "source_platform_version": _NONBLANK,
                "created_at": {"type": "string", "format": "date-time"},
                "integrity_algorithm": {"const": PORTABLE_INTEGRITY_ALGORITHM},
                "resources": {"type": "array", "items": _RESOURCE_DESCRIPTOR_SCHEMA},
                "requirements": {"type": "array", "items": _DEPENDENCY_SCHEMA},
                "provenance": _PROVENANCE_SCHEMA,
                "compatibility": _COMPATIBILITY_SCHEMA,
                "excluded_state": {"type": "array", "items": _EXCLUSION_SCHEMA},
            },
            "additionalProperties": False,
        },
        "resources": {"type": "array", "items": _RESOURCE_SCHEMA},
        "checksum": _CHECKSUM,
    },
    "additionalProperties": False,
}


def validate_package_document(document: object) -> None:
    """Validate an imported document before constructing portable package models."""

    if isinstance(document, Mapping):
        manifest = document.get("manifest")
        if isinstance(manifest, Mapping):
            format_version = manifest.get("format_version")
            if isinstance(format_version, str) and format_version != PORTABLE_FORMAT_VERSION:
                raise ContractError(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    f"unsupported portable package format version: {format_version}",
                    details={"supported_format_version": PORTABLE_FORMAT_VERSION},
                )

    try:
        Draft202012Validator(PORTABLE_PACKAGE_SCHEMA_V1).validate(document)
    except ValidationError as exc:
        path = ".".join(str(part) for part in exc.absolute_path)
        detail = f" at {path}" if path else ""
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"invalid portable package{detail}: {exc.message}",
        ) from exc
