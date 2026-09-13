from __future__ import annotations

import pytest

from ai_multi_agent_platform.distribution import (
    InstalledRegistryItem,
    LocalRegistryProvider,
    RegistryItem,
    RegistryItemType,
    RegistryQuery,
    RegistrySource,
)


def _item(**overrides: object) -> RegistryItem:
    values: dict[str, object] = {
        "item_id": "example.valid",
        "item_type": RegistryItemType.TEMPLATE,
        "name": "Example",
        "description": "Domain validation fixture",
        "version": "1.0.0",
        "publisher": "example",
        "source": RegistrySource("https://example.invalid/repo", "asset@1.0.0"),
        "license": "MIT",
        "provenance": "source-release",
    }
    values.update(overrides)
    return RegistryItem(**values)  # type: ignore[arg-type]


def test_registry_item_rejects_invalid_identity_version_and_blank_metadata() -> None:
    with pytest.raises(ValueError, match="item_id"):
        _item(item_id="Invalid ID")
    with pytest.raises(ValueError, match="version"):
        _item(version="v1")
    with pytest.raises(ValueError, match="publisher"):
        _item(publisher="   ")
    with pytest.raises(ValueError, match="required_plugins"):
        _item(required_plugins=("",))


def test_registry_query_rejects_invalid_filters_before_provider_use() -> None:
    with pytest.raises(ValueError, match="query text"):
        RegistryQuery(text=" ")
    with pytest.raises(ValueError, match="version"):
        RegistryQuery(platform_version="latest")
    with pytest.raises(ValueError, match="update_for_item_id"):
        RegistryQuery(update_for_item_id="Bad ID")


def test_installed_registry_item_validates_persisted_update_metadata() -> None:
    with pytest.raises(ValueError, match="source_registry"):
        InstalledRegistryItem("example.valid", "1.0.0", " ")
    with pytest.raises(ValueError, match="version"):
        InstalledRegistryItem(
            "example.valid",
            "1.0.0",
            "local",
            pinned_version="v2",
        )


def test_local_registry_orders_ids_ascending_and_versions_descending() -> None:
    provider = LocalRegistryProvider(
        (
            _item(item_id="zeta.item", version="1.0.0"),
            _item(item_id="alpha.item", version="1.0.0"),
            _item(item_id="alpha.item", version="2.0.0"),
        )
    )
    assert [(item.item_id, item.version) for item in provider.search(RegistryQuery())] == [
        ("alpha.item", "2.0.0"),
        ("alpha.item", "1.0.0"),
        ("zeta.item", "1.0.0"),
    ]


def test_local_registry_rejects_duplicate_identity_and_blank_provider_id() -> None:
    item = _item()
    with pytest.raises(ValueError, match="duplicate"):
        LocalRegistryProvider((item, item))
    with pytest.raises(ValueError, match="provider_id"):
        LocalRegistryProvider(provider_id=" ")
