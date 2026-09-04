from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

import pytest

from ai_multi_agent_platform.connectors import (
    REFERENCE_CONNECTOR_TYPE,
    REFERENCE_CONNECTOR_VERSION,
    Connection,
    ConnectorResourceQuery,
    ExternalNativeReference,
    ExternalResourceReference,
    ReferenceConnectorProvider,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.domain import new_id


def test_optional_connector_hooks_fail_explicitly_when_not_supported() -> None:
    provider = ReferenceConnectorProvider()
    connection = Connection(
        id=new_id("connection"),
        connector_type_id=REFERENCE_CONNECTOR_TYPE,
        connector_version=REFERENCE_CONNECTOR_VERSION,
        owner_type="human",
        owner_id="user:connector-optional-hooks",
        display_name="Optional-hook fixture",
    )
    context = OperationContext(
        correlation_id="connector-optional-hooks",
        owner_type="human",
        owner_id="user:connector-optional-hooks",
    )
    resource = ExternalResourceReference(
        id=new_id("external_resource"),
        connection_id=connection.id,
        resource_type="record",
        native_reference=ExternalNativeReference(
            namespace=REFERENCE_CONNECTOR_TYPE,
            native_id="alpha",
        ),
    )
    subscription = ExternalNativeReference(
        namespace=REFERENCE_CONNECTOR_TYPE,
        native_id="subscription-1",
    )
    query = ConnectorResourceQuery(
        connection_id=connection.id,
        resource_type="record",
        context=context,
        query={"q": "alpha"},
    )

    operations: tuple[tuple[str, Coroutine[Any, Any, object]], ...] = (
        ("resource.search", provider.search_resources(query)),
        (
            "event.subscribe",
            provider.subscribe_events(
                connection,
                ("record.changed",),
                configuration={},
                context=context,
            ),
        ),
        (
            "event.unsubscribe",
            provider.unsubscribe_events(connection, subscription, context=context),
        ),
        (
            "event.normalize",
            provider.normalize_external_event(connection, {"event": "changed"}, context),
        ),
        ("file.import", provider.import_file_content(connection, resource, context)),
        (
            "file.export",
            provider.export_file_content(
                connection,
                b"payload",
                resource_type="record",
                metadata={"name": "export.bin"},
                context=context,
            ),
        ),
        ("knowledge.ingest", provider.read_knowledge_content(connection, resource, context)),
    )

    for operation, awaitable in operations:
        with pytest.raises(ContractError) as exc_info:
            asyncio.run(awaitable)
        assert exc_info.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
        assert exc_info.value.details["operation"] == operation
