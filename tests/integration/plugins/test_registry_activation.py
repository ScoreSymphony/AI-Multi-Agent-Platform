from __future__ import annotations

import asyncio
import hashlib

import pytest

from ai_multi_agent_platform.distribution import (
    ArtifactIntegrity,
    DistributionRoute,
    DistributionService,
    InstalledRegistryItem,
    LocalRegistryProvider,
    RegistryItem,
    RegistryItemType,
    RegistrySource,
    TrustStatus,
    ValidationContext,
    VersionRange,
    validate_item,
)


class RecordingRouter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def install_plugin(self, item: RegistryItem, artifact: bytes) -> object:
        self.calls.append(("plugin", item.item_id))
        return artifact

    async def import_portable(self, item: RegistryItem, artifact: bytes) -> object:
        self.calls.append(("portable", item.item_id))
        return artifact


def _item(item_type: RegistryItemType, version: str = "1.0.0") -> RegistryItem:
    payload = f"{item_type.value}:{version}".encode()
    return RegistryItem(
        item_id=f"example.{item_type.value}",
        item_type=item_type,
        name="Example",
        description="Reference item",
        version=version,
        publisher="example",
        source=RegistrySource("https://example.invalid/repo", f"asset@{version}"),
        license="MIT",
        provenance="source-release",
        supported_platform=VersionRange("0.0.1", "1.0.0"),
        integrity=ArtifactIntegrity(hashlib.sha256(payload).hexdigest()),
        trust_status=TrustStatus.REVIEWED,
    )


def _service(item: RegistryItem) -> tuple[DistributionService, RecordingRouter]:
    payload = f"{item.item_type.value}:{item.version}".encode()
    router = RecordingRouter()
    provider = LocalRegistryProvider((item,), {(item.item_id, item.version): payload})
    return DistributionService(provider, router), router


def test_preview_never_activates_item() -> None:
    item = _item(RegistryItemType.PLUGIN)
    service, router = _service(item)
    preview = service.preview(item.item_id, item.version, ValidationContext("0.0.1"))
    assert preview.activation_allowed is True
    assert preview.route is DistributionRoute.PLUGIN
    assert router.calls == []


def test_activation_requires_explicit_authorization() -> None:
    item = _item(RegistryItemType.PLUGIN)
    service, router = _service(item)
    context = ValidationContext("0.0.1")
    preview = service.preview(item.item_id, item.version, context)
    with pytest.raises(PermissionError, match="explicit authorization"):
        asyncio.run(service.activate(preview, context, authorized=False))
    assert router.calls == []


def test_plugin_and_template_use_separate_owner_routes() -> None:
    plugin = _item(RegistryItemType.PLUGIN)
    plugin_service, plugin_router = _service(plugin)
    plugin_preview = plugin_service.preview(
        plugin.item_id, plugin.version, ValidationContext("0.0.1")
    )
    asyncio.run(
        plugin_service.activate(
            plugin_preview,
            ValidationContext("0.0.1"),
            authorized=True,
        )
    )
    assert plugin_router.calls == [("plugin", plugin.item_id)]

    template = _item(RegistryItemType.TEMPLATE)
    template_service, template_router = _service(template)
    template_preview = template_service.preview(
        template.item_id, template.version, ValidationContext("0.0.1")
    )
    asyncio.run(
        template_service.activate(
            template_preview,
            ValidationContext("0.0.1"),
            authorized=True,
        )
    )
    assert template_router.calls == [("portable", template.item_id)]


def test_pinned_version_blocks_other_update_application_but_not_discovery() -> None:
    item = _item(RegistryItemType.PLUGIN, "2.0.0")
    installed = InstalledRegistryItem(
        item_id=item.item_id,
        version="1.0.0",
        source_registry="local",
        pinned_version="1.0.0",
    )
    assert installed.has_update(item) is True
    assert installed.accepts_update(item) is True
    assert installed.can_apply_update(item) is False
    findings = validate_item(
        item,
        f"{item.item_type.value}:{item.version}".encode(),
        ValidationContext("0.0.1", installed_items=(installed,)),
    )
    assert any(finding.code == "version_pinned" for finding in findings)


def test_available_update_is_previewed_but_never_auto_applied() -> None:
    item = _item(RegistryItemType.PLUGIN, "2.0.0")
    installed = InstalledRegistryItem(
        item_id=item.item_id,
        version="1.0.0",
        source_registry="local",
    )
    service, router = _service(item)

    assert installed.accepts_update(item) is True
    preview = service.preview(
        item.item_id,
        item.version,
        ValidationContext("0.0.1", installed_items=(installed,)),
    )
    assert preview.activation_allowed is True
    assert router.calls == []


def test_license_change_is_visible_before_update() -> None:
    item = _item(RegistryItemType.PLUGIN, "2.0.0")
    installed = InstalledRegistryItem(
        item_id=item.item_id,
        version="1.0.0",
        source_registry="local",
        license="Apache-2.0",
        provenance="source-release",
    )
    findings = validate_item(
        item,
        f"{item.item_type.value}:{item.version}".encode(),
        ValidationContext("0.0.1", installed_items=(installed,)),
    )
    assert any(finding.code == "license_changed" for finding in findings)
