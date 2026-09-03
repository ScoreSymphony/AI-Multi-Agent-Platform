from __future__ import annotations

from dataclasses import replace

import pytest

from ai_multi_agent_platform.connectors import (
    ConnectorDefinition,
    ConnectorRegistry,
    ReferenceConnectorProvider,
    connector_definition_id,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.domain import new_id


class _WrongIdentityReferenceConnector(ReferenceConnectorProvider):
    @property
    def definition(self) -> ConnectorDefinition:
        return replace(super().definition, id=new_id("connector_definition"))


def test_connector_definition_is_stable_across_provider_instances() -> None:
    first = ReferenceConnectorProvider().definition
    second = ReferenceConnectorProvider().definition

    assert first.id == second.id
    assert first.id == connector_definition_id("reference.local", "1.0")
    assert first.id.startswith("connector_definition_")
    assert first.connector_type_id == "reference.local"
    assert first.version == "1.0"


def test_connector_registry_rejects_adapter_private_definition_identity() -> None:
    with pytest.raises(ContractError) as exc_info:
        ConnectorRegistry().register(_WrongIdentityReferenceConnector())
    assert exc_info.value.code is ErrorCode.CONTRACT_VIOLATION
    assert exc_info.value.details["expected_id"] == connector_definition_id(
        "reference.local", "1.0"
    )


def test_connector_definition_rejects_noncanonical_id() -> None:
    with pytest.raises(ValueError, match="canonical connector_definition id"):
        ConnectorDefinition(
            id="reference.local:1.0",
            connector_type_id="reference.local",
            name="Reference",
            version="1.0",
        )


def test_connector_definition_model_accepts_platform_uuid_identity() -> None:
    definition = ConnectorDefinition(
        id=new_id("connector_definition"),
        connector_type_id="fixture.local",
        name="Fixture",
        version="1.0",
    )
    assert definition.id.startswith("connector_definition_")
