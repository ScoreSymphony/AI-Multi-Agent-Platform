from __future__ import annotations

import pytest

from ai_multi_agent_platform.connectors import ConnectorDefinition, ReferenceConnectorProvider
from ai_multi_agent_platform.domain import new_id


def test_connector_definition_is_a_stable_canonical_resource() -> None:
    provider = ReferenceConnectorProvider()
    first = provider.definition
    second = provider.definition

    assert first.id == second.id
    assert first.id.startswith("connector_definition_")
    assert first.connector_type_id == "reference.local"
    assert first.version == "1.0"


def test_connector_definition_rejects_noncanonical_id() -> None:
    with pytest.raises(ValueError, match="canonical connector_definition id"):
        ConnectorDefinition(
            id="reference.local:1.0",
            connector_type_id="reference.local",
            name="Reference",
            version="1.0",
        )


def test_connector_definition_accepts_platform_uuid_identity() -> None:
    definition = ConnectorDefinition(
        id=new_id("connector_definition"),
        connector_type_id="fixture.local",
        name="Fixture",
        version="1.0",
    )
    assert definition.id.startswith("connector_definition_")
