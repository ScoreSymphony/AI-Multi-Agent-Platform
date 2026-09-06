"""Portable JSON metadata schema for registry providers."""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from .items import RegistryItem
from .models import (
    ArtifactIntegrity,
    RegistryDependency,
    RegistryItemType,
    RegistrySource,
    TrustStatus,
    VersionRange,
)

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


def registry_item_from_document(document: dict[str, Any]) -> RegistryItem:
    """Validate and construct one canonical RegistryItem from provider JSON metadata."""

    validate_registry_item_document(document)
    source = document["source"]
    supported_platform = document["supported_platform"]
    integrity = document["integrity"]
    dependencies = tuple(
        RegistryDependency(
            item_id=dependency["item_id"],
            version_range=VersionRange(
                dependency["version_range"].get("minimum"),
                dependency["version_range"].get("maximum"),
            ),
            optional=dependency["optional"],
        )
        for dependency in document["dependencies"]
    )
    return RegistryItem(
        item_id=document["item_id"],
        item_type=RegistryItemType(document["item_type"]),
        name=document["name"],
        description=document["description"],
        version=document["version"],
        publisher=document["publisher"],
        source=RegistrySource(
            repository=source["repository"],
            package_reference=source["package_reference"],
            revision=source.get("revision"),
        ),
        license=document["license"],
        provenance=document["provenance"],
        supported_platform=VersionRange(
            supported_platform.get("minimum"), supported_platform.get("maximum")
        ),
        dependencies=dependencies,
        requested_permissions=frozenset(document["requested_permissions"]),
        required_capabilities=frozenset(document["required_capabilities"]),
        required_plugins=tuple(document.get("required_plugins", [])),
        required_connectors=tuple(document.get("required_connectors", [])),
        required_models=tuple(document.get("required_models", [])),
        tags=frozenset(document.get("tags", [])),
        categories=frozenset(document.get("categories", [])),
        integrity=ArtifactIntegrity(
            sha256=integrity.get("sha256"),
            signature=integrity.get("signature"),
            signature_key_id=integrity.get("signature_key_id"),
        ),
        trust_status=TrustStatus(document["trust_status"]),
        review_reference=document.get("review_reference"),
        released_at=document.get("released_at"),
        changelog=document.get("changelog"),
        deprecated=document["deprecated"],
        yanked=document["yanked"],
    )
