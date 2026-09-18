from __future__ import annotations

import asyncio

import pytest
from jsonschema import ValidationError

from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.distribution import (
    DistributionRoute,
    DistributionService,
    LocalRegistryProvider,
    RegistryItem,
    RegistryItemType,
    RegistryQuery,
    RegistryResourceService,
    RegistrySource,
    registry_item_from_document,
)


def _application_item() -> RegistryItem:
    return RegistryItem(
        item_id="example-application",
        item_type=RegistryItemType.APPLICATION,
        name="Example Application",
        description="Managed Application catalog fixture",
        version="1.2.0",
        publisher="example",
        source=RegistrySource(
            repository="https://example.invalid/application",
            package_reference="application-manifest@1.2.0",
            revision="release-1.2.0",
        ),
        license="MIT",
        provenance="reviewed application manifest",
        required_capabilities=frozenset({"local", "process"}),
        tags=frozenset({"maturity:beta"}),
        categories=frozenset({"application"}),
    )


def _application_document(*, schema_version: str) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "item_id": "example-application",
        "item_type": "application",
        "name": "Example Application",
        "description": "Managed Application catalog fixture",
        "version": "1.2.0",
        "publisher": "example",
        "source": {
            "repository": "https://example.invalid/application",
            "package_reference": "application-manifest@1.2.0",
            "revision": "release-1.2.0",
        },
        "license": "MIT",
        "provenance": "reviewed application manifest",
        "supported_platform": {"minimum": "1.0.0", "maximum": "2.0.0"},
        "dependencies": [],
        "requested_permissions": [],
        "required_capabilities": ["local", "process"],
        "tags": ["maturity:beta"],
        "categories": ["application"],
        "integrity": {},
        "trust_status": "reviewed",
        "deprecated": False,
        "yanked": False,
    }


def test_application_registry_items_are_catalog_only_and_manual_by_default() -> None:
    item = _application_item()

    assert item.item_type is RegistryItemType.APPLICATION
    assert item.route is DistributionRoute.MANUAL

    provider = LocalRegistryProvider((item,))
    assert provider.search(
        RegistryQuery(item_types=frozenset({RegistryItemType.APPLICATION}))
    ) == (item,)


def test_registry_v2_accepts_application_definitions_without_changing_v1() -> None:
    item = registry_item_from_document(_application_document(schema_version="2"))

    assert item.item_type is RegistryItemType.APPLICATION
    assert item.route is DistributionRoute.MANUAL
    assert item.required_capabilities == frozenset({"local", "process"})
    assert item.source.revision == "release-1.2.0"

    with pytest.raises(ValidationError):
        registry_item_from_document(_application_document(schema_version="1"))


def test_registry_control_plane_discovers_application_catalog_metadata() -> None:
    item = _application_item()
    service = RegistryResourceService(
        DistributionService(LocalRegistryProvider((item,)))
    )
    context = RequestContext(
        request_id="request-application-catalog",
        correlation_id="correlation-application-catalog",
    )

    listed = asyncio.run(
        service.list_resources(
            context,
            PageQuery(filters={"item_type": "application"}),
        )
    )

    assert len(listed) == 1
    resource = listed[0]
    assert resource["id"] == "example-application@1.2.0"
    assert resource["item_type"] == "application"
    assert resource["version"] == "1.2.0"
    assert resource["source"] == {
        "repository": "https://example.invalid/application",
        "package_reference": "application-manifest@1.2.0",
        "revision": "release-1.2.0",
    }
    assert resource["provenance"] == "reviewed application manifest"
    assert resource["required_capabilities"] == ["local", "process"]
    assert resource["tags"] == ["maturity:beta"]
    assert resource["route"] == "manual"
