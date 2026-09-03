"""Versioned JSON manifest validation for plugins."""

from __future__ import annotations

from typing import Any, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode

from .models import PLUGIN_MANIFEST_VERSION

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
        "configuration_schema",
        "dependencies",
    ],
    "properties": {
        "plugin_id": {"type": "string", "pattern": "^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$"},
        "name": {"type": "string", "minLength": 1},
        "description": {"type": "string", "minLength": 1},
        "plugin_version": {"type": "string", "minLength": 1},
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
        "supported_platform": {
            "type": "object",
            "properties": {
                "minimum": {"type": ["string", "null"], "pattern": "^\\d+(?:\\.\\d+){0,2}$"},
                "maximum": {"type": ["string", "null"], "pattern": "^\\d+(?:\\.\\d+){0,2}$"},
            },
            "additionalProperties": False,
        },
        "extensions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["extension_id", "extension_type", "interface_version", "entrypoint"],
                "properties": {
                    "extension_id": {"type": "string", "minLength": 1},
                    "extension_type": {"type": "string", "minLength": 1},
                    "interface_version": {"type": "string", "pattern": "^\\d+(?:\\.\\d+){0,2}$"},
                    "entrypoint": {"type": "string", "minLength": 1},
                    "metadata": {"type": "object"},
                },
                "additionalProperties": False,
            },
        },
        "requested_permissions": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        "configuration_schema": {"type": "object"},
        "dependencies": {"type": "array"},
        "optional_external_services": {"type": "array", "items": {"type": "string"}},
        "state_migrations": {"type": "array", "items": {"type": "string"}},
        "ui_metadata": {"type": "object"},
    },
    "additionalProperties": False,
}


def validate_manifest_document(document: object) -> None:
    """Validate a serialized manifest before constructing runtime objects."""

    try:
        Draft202012Validator(cast(dict[str, Any], PLUGIN_MANIFEST_SCHEMA)).validate(document)
    except ValidationError as exc:
        path = ".".join(str(part) for part in exc.absolute_path)
        detail = f" at {path}" if path else ""
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"invalid plugin manifest{detail}: {exc.message}",
        ) from exc
