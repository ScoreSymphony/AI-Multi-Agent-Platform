from __future__ import annotations

from dataclasses import replace

import pytest

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.plugins import (
    ExtensionType,
    PluginRegistry,
    reference_manifest,
    validate_manifest_document,
)


def test_plugin_configuration_validation_does_not_expose_rejected_value() -> None:
    private_value = "credential-like-private-plugin-value"
    manifest = replace(
        reference_manifest(),
        configuration_schema={
            "type": "object",
            "properties": {"token": {"type": "string", "pattern": "^allowed$"}},
            "required": ["token"],
            "additionalProperties": False,
        },
    )
    registry = PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={ExtensionType.CAPABILITY_PROVIDER: frozenset({"1.0"})},
    )
    registry.install(manifest)

    with pytest.raises(ContractError) as caught:
        registry.configure(manifest.plugin_id, {"token": private_value})

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
    assert str(caught.value) == f"invalid configuration for plugin {manifest.plugin_id!r}"
    assert private_value not in str(caught.value)


def test_plugin_manifest_validation_does_not_expose_rejected_value() -> None:
    private_value = "credential-like-private-extension-type"
    document: dict[str, object] = {
        "plugin_id": "redaction.plugin",
        "name": "Redaction plugin",
        "description": "Plugin manifest redaction fixture",
        "plugin_version": "1.0.0",
        "manifest_version": "1",
        "author": "tests",
        "provenance": {"source": "test", "license": "MIT"},
        "supported_platform": {"minimum": "0.0.1", "maximum": "0.0.1"},
        "extensions": [
            {
                "extension_id": "transport.redaction",
                "extension_type": private_value,
                "interface_version": "1.0",
                "entrypoint": "redaction:Runtime",
                "metadata": {},
            }
        ],
        "capabilities": ["transport.messages"],
        "requested_permissions": [],
        "configuration_version": "1.0",
        "configuration_schema": {"type": "object", "additionalProperties": False},
        "dependencies": [],
        "optional_external_services": [],
        "state_version": "1.0",
        "state_migrations": [],
        "ui_metadata": {},
    }

    with pytest.raises(ContractError) as caught:
        validate_manifest_document(document)

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
    assert str(caught.value) == "invalid plugin manifest at extensions.0.extension_type"
    assert private_value not in str(caught.value)
