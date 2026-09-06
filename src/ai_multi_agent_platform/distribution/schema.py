"""Portable JSON metadata schema for registry providers."""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

REGISTRY_ITEM_SCHEMA_VERSION = "1"
REGISTRY_ITEM_SCHEMA_V1: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": [
        "schema_version",
        "item_id",
        "item_type",
        "name",
        "description",
        "version",
        "publisher",
        "source",
        "license",
        "provenance",
        "supported_platform",
        "dependencies",
        "requested_permissions",
        "required_capabilities",
        "integrity",
        "trust_status",
        "deprecated",
        "yanked",
    ],
    "properties": {
        "schema_version": {"const": REGISTRY_ITEM_SCHEMA_VERSION},
        "item_id": {"type": "string", "pattern": "^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$"},
        "item_type": {
            "enum": [
                "agent",
                "agent_team",
                "tool",
                "plugin",
                "workflow",
                "template",
                "model_configuration",
                "connector",
                "evaluation",
                "documentation",
            ]
        },
        "name": {"type": "string", "minLength": 1},
        "description": {"type": "string", "minLength": 1},
        "version": {"type": "string", "pattern": "^\\d+(?:\\.\\d+){0,2}$"},
        "publisher": {"type": "string", "minLength": 1},
        "source": {
            "type": "object",
            "required": ["repository", "package_reference"],
            "properties": {
                "repository": {"type": "string", "minLength": 1},
                "package_reference": {"type": "string", "minLength": 1},
                "revision": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "license": {"type": "string", "minLength": 1},
        "provenance": {"type": "string", "minLength": 1},
        "supported_platform": {"$ref": "#/$defs/version_range"},
        "dependencies": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["item_id", "version_range", "optional"],
                "properties": {
                    "item_id": {"type": "string"},
                    "version_range": {"$ref": "#/$defs/version_range"},
                    "optional": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
        },
        "requested_permissions": {
            "type": "array",
            "items": {"type": "string"},
            "uniqueItems": True,
        },
        "required_capabilities": {
            "type": "array",
            "items": {"type": "string"},
            "uniqueItems": True,
        },
        "required_plugins": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        "required_connectors": {
            "type": "array",
            "items": {"type": "string"},
            "uniqueItems": True,
        },
        "required_models": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        "tags": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        "categories": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        "integrity": {
            "type": "object",
            "properties": {
                "sha256": {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"},
                "signature": {"type": ["string", "null"]},
                "signature_key_id": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "trust_status": {"enum": ["untrusted", "reviewed", "trusted", "local"]},
        "review_reference": {"type": ["string", "null"]},
        "released_at": {"type": ["string", "null"]},
        "changelog": {"type": ["string", "null"]},
        "deprecated": {"type": "boolean"},
        "yanked": {"type": "boolean"},
    },
    "$defs": {
        "version_range": {
            "type": "object",
            "properties": {
                "minimum": {"type": ["string", "null"]},
                "maximum": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        }
    },
    "additionalProperties": False,
}


def validate_registry_item_document(document: dict[str, Any]) -> None:
    Draft202012Validator(REGISTRY_ITEM_SCHEMA_V1).validate(document)
