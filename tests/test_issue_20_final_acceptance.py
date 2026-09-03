from __future__ import annotations

from dataclasses import replace

import pytest

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.plugins import (
    ExtensionType,
    reference_manifest,
    validate_manifest_document,
)


REQUIRED_EXTENSION_TYPES = {
    "orchestrator",
    "executor",
    "model_provider",
    "model_routing_policy",
    "capability_provider",
    "memory_provider",
    "file_provider",
    "knowledge_provider",
    "event_provider",
    "transport_provider",
    "authorization_provider",
    "observability_exporter",
    "automation_provider",
    "evaluator",
    "node_provider",
    "worker_provider",
    "connector_provider",
    "frontend_extension",
    "configuration_extension",
}


def _manifest_document(*, extension_type: str = "transport_provider") -> dict[str, object]:
    return {
        "plugin_id": "acceptance.plugin",
        "name": "Acceptance plugin",
        "description": "Issue 20 acceptance manifest",
        "plugin_version": "1.0.0",
        "manifest_version": "1",
        "author": "tests",
        "provenance": {"source": "test", "license": "MIT"},
        "supported_platform": {"minimum": "0.0.1", "maximum": "0.0.1"},
        "extensions": [
            {
                "extension_id": "transport.acceptance",
                "extension_type": extension_type,
                "interface_version": "1.0",
                "entrypoint": "acceptance:Runtime",
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


def test_issue_20_reserves_every_required_extension_category() -> None:
    assert REQUIRED_EXTENSION_TYPES <= {extension.value for extension in ExtensionType}


def test_manifest_v1_requires_explicit_capability_declarations() -> None:
    document = _manifest_document()
    validate_manifest_document(document)

    missing = dict(document)
    del missing["capabilities"]
    with pytest.raises(ContractError) as caught:
        validate_manifest_document(missing)
    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION


def test_manifest_v1_accepts_transport_and_configuration_extensions() -> None:
    validate_manifest_document(_manifest_document(extension_type="transport_provider"))
    validate_manifest_document(_manifest_document(extension_type="configuration_extension"))


def test_manifest_model_rejects_duplicate_capability_ids() -> None:
    manifest = reference_manifest()
    with pytest.raises(ValueError, match="duplicate capabilities"):
        replace(manifest, capabilities=("plugin.echo", "plugin.echo"))


def test_reference_plugin_explicitly_declares_its_capability() -> None:
    manifest = reference_manifest()
    assert manifest.capabilities == ("plugin.echo",)
