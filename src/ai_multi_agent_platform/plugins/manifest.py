"""Versioned JSON manifest validation for plugins."""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import ValidationError  # type: ignore[import-untyped]

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode

from .models import PLUGIN_MANIFEST_VERSION, ExtensionType, PluginPermission

_VERSION_SCHEMA: dict[str, Any] = {
    "type": "string",
    "pattern": r"^\d+(?:\.\d+){0,2}$",
}
_VERSION_RANGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "minimum": {"type": ["string", "null"], "pattern": r"^\d+(?:\.\d+){0,2}$"},
        "maximum": {"type": ["string", "null"], "pattern": r"^\d+(?:\.\d+){0,2}$"},
    },
    "additionalProperties": False,
}

PLUGIN_MANIFEST_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://ai-multi-agent-platform.invalid/schemas/plugin-manifest-v1.json",
    "type": "object",
    "required": [
        "plugin_id",
        "name",
        "description",
        "plugin_version",
        "manifest_version",
        "author",
        "provenance",
        "supported_platform",
        "extensions",
        "requested_permissions",
        "configuration_version",
        "configuration_schema",
        "dependencies",
        "state_version",
        "state_migrations",
    ],
    "properties": {
        "plugin_id": {"type": "string", "pattern": "^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$"},
        "name": {"type": "string", "minLength": 1},
        "description": {"type": "string", "minLength": 1},
        "plugin_version": _VERSION_SCHEMA,
        "manifest_version": {"const": PLUGIN_MANIFEST_VERSION},
        "author": {"type": "string", "minLength": 1},
        "provenance": {
            "type": "object",
            "required": ["source", "license"],
            "properties": {
                "source": {"type": "string", "minLength": 1},
                "license": {"type": "string", "minLength": 1},
                "source_repository": {"type": ["string", "null"]},
                "revision": {"type": ["string", "null"]},
                "checksum": {"type": ["string", "null"]},
                "trust_source": {"type": ["string", "null"]},
                "local_modifications": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "supported_platform": _VERSION_RANGE_SCHEMA,
        "extensions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["extension_id", "extension_type", "interface_version", "entrypoint"],
                "properties": {
                    "extension_id": {
                        "type": "string",
                        "pattern": "^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$",
                    },
                    "extension_type": {"enum": [item.value for item in ExtensionType]},
                    "interface_version": _VERSION_SCHEMA,
                    "entrypoint": {"type": "string", "minLength": 1},
                    "metadata": {"type": "object"},
                },
                "additionalProperties": False,
            },
        },
        "requested_permissions": {
            "type": "array",
            "items": {"enum": [item.value for item in PluginPermission]},
            "uniqueItems": True,
        },
        "configuration_version": _VERSION_SCHEMA,
        "configuration_schema": {"type": "object"},
        "dependencies": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["plugin_id", "version_range", "optional"],
                "properties": {
                    "plugin_id": {
                        "type": "string",
                        "pattern": "^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$",
                    },
                    "version_range": _VERSION_RANGE_SCHEMA,
                    "optional": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
        },
        "optional_external_services": {"type": "array", "items": {"type": "string"}},
        "state_version": _VERSION_SCHEMA,
        "state_migrations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["migration_id", "from_version", "to_version"],
                "properties": {
                    "migration_id": {
                        "type": "string",
                        "pattern": "^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$",
                    },
                    "from_version": _VERSION_SCHEMA,
                    "to_version": _VERSION_SCHEMA,
                },
                "additionalProperties": False,
            },
        },
        "ui_metadata": {"type": "object"},
    },
    "additionalProperties": False,
}


def validate_manifest_document(document: object) -> None:
    """Validate a serialized manifest before constructing runtime objects."""

    try:
        Draft202012Validator(PLUGIN_MANIFEST_SCHEMA).validate(document)
    except ValidationError as exc:
        path = ".".join(str(part) for part in exc.absolute_path)
        detail = f" at {path}" if path else ""
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"invalid plugin manifest{detail}: {exc.message}",
        ) from exc
