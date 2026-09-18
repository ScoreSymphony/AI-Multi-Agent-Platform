from __future__ import annotations

import asyncio
import hashlib

from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.distribution import (
    MARKETPLACE_KIND_COLLECTION,
    REGISTRY_ACTIVATE_COMMAND,
    REGISTRY_COLLECTION,
    REGISTRY_PREVIEW_COMMAND,
    ArtifactIntegrity,
    DistributionService,
    InstalledRegistryItem,
    JsonRegistryInstallationStore,
    LocalRegistryProvider,
    PlatformRegistryValidationContextResolver,
    RegistryItem,
    RegistryItemType,
    RegistrySource,
    RegistryValidationContextResolver,
    TrustStatus,
    ValidationContext,
    VersionRange,
    register_distribution_control_plane,
)


class RecordingControlPlane:
    def __init__(self) -> None:
        self.resources: dict[str, object] = {}
        self.commands: dict[str, object] = {}

    def register_resource_service(self, collection: str, service: object) -> None:
        self.resources[collection] = service

    def register_command(self, command: str, handler: object) -> None:
        self.commands[command] = handler


class StaticValidationContext(RegistryValidationContextResolver):
    def __init__(self, context: ValidationContext) -> None:
        self.context = context
        self.calls = 0

    async def resolve(self, context: object) -> ValidationContext:  # type: ignore[override]
        del context
        self.calls += 1
        return self.context


class RecordingRouter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def install_plugin(self, item: RegistryItem, artifact: bytes) -> object:
        del artifact
        self.calls.append(("plugin", item.item_id))
        return item.item_id

    async def import_portable(self, item: RegistryItem, artifact: bytes) -> object:
        del artifact
        self.calls.append(("portable", item.item_id))
        return item.item_id


def _item() -> tuple[RegistryItem, bytes]:
    artifact = b"registry-control-plane"
    item = RegistryItem(
        item_id="example.control-plane",
        item_type=RegistryItemType.TEMPLATE,
        name="Control Plane example",
        description="Registry northbound fixture",
        version="1.0.0",
        publisher="example",
        source=RegistrySource("https://example.invalid/repo", "asset@1.0.0"),
        license="MIT",
        provenance="source-release",
        supported_platform=VersionRange("0.0.1", "1.0.0"),
        integrity=ArtifactIntegrity(sha256=hashlib.sha256(artifact).hexdigest()),
        trust_status=TrustStatus.REVIEWED,
    )
    return item, artifact


def test_platform_validation_resolver_merges_local_and_marketplace_installations(
    tmp_path,
) -> None:
    marketplace_item, _artifact = _item()
    installations = JsonRegistryInstallationStore(tmp_path / "registry-installations.json")
    installations.record(marketplace_item, provider_id="local")
    local_item = InstalledRegistryItem(
        "example.local-owner-item",
        "2.0.0",
        item_type=RegistryItemType.TOOL,
    )
    owner_copy = InstalledRegistryItem(
        marketplace_item.item_id,
        "9.0.0",
        item_type=RegistryItemType.TEMPLATE,
    )
    resolver = PlatformRegistryValidationContextResolver(
        platform_version="0.0.1",
        installations=installations,
        installed_items=lambda: (local_item, owner_copy),
        operating_system="linux",
        architecture="x86_64",
    )

    resolved = asyncio.run(
        resolver.resolve(
            RequestContext(
                request_id="registry-context",
                correlation_id="registry-context",
            )
        )
    )

    by_id = {item.item_id: item for item in resolved.installed_items}
    assert by_id[local_item.item_id].source_registry is None
    assert by_id[local_item.item_id].version == "2.0.0"
    assert by_id[marketplace_item.item_id].source_registry is None
    assert by_id[marketplace_item.item_id].version == owner_copy.version


def test_platform_validation_resolver_enriches_same_version_owner_state_with_marketplace_origin(
    tmp_path,
) -> None:
    marketplace_item, _artifact = _item()
    installations = JsonRegistryInstallationStore(tmp_path / "registry-installations.json")
    installations.record(marketplace_item, provider_id="local")
    owner_copy = InstalledRegistryItem(
        marketplace_item.item_id,
        marketplace_item.version,
        item_type=RegistryItemType.TEMPLATE,
    )
    resolver = PlatformRegistryValidationContextResolver(
        platform_version="0.0.1",
        installations=installations,
        installed_items=lambda: (owner_copy,),
        operating_system="linux",
        architecture="x86_64",
    )

    resolved = asyncio.run(
        resolver.resolve(
            RequestContext(
                request_id="registry-context-same-version",
                correlation_id="registry-context-same-version",
            )
        )
    )

    installed = next(
        item for item in resolved.installed_items if item.item_id == owner_copy.item_id
    )
    assert installed.version == owner_copy.version
    assert installed.source_registry == "local"
    assert installed.provenance == marketplace_item.provenance


def test_disabled_registry_registers_no_northbound_surface() -> None:
    control_plane = RecordingControlPlane()
    register_distribution_control_plane(  # type: ignore[arg-type]
        control_plane,
        DistributionService(None),
    )
    assert control_plane.resources == {}
    assert control_plane.commands == {}


def test_enabled_registry_registers_discovery_but_not_commands_without_resolver() -> None:
    item, artifact = _item()
    distribution = DistributionService(
        LocalRegistryProvider((item,), {(item.item_id, item.version): artifact})
    )
    control_plane = RecordingControlPlane()
    register_distribution_control_plane(control_plane, distribution)  # type: ignore[arg-type]

    assert set(control_plane.resources) == {REGISTRY_COLLECTION, MARKETPLACE_KIND_COLLECTION}
    assert control_plane.commands == {}

    kinds = control_plane.resources[MARKETPLACE_KIND_COLLECTION]
    kind_resources = asyncio.run(kinds.list_resources(object(), PageQuery()))  # type: ignore[attr-defined]
    assert any(resource["kind"] == "application" for resource in kind_resources)

    service = control_plane.resources[REGISTRY_COLLECTION]
    listed = asyncio.run(service.list_resources(object(), PageQuery()))  # type: ignore[attr-defined]
    fetched = asyncio.run(  # type: ignore[attr-defined]
        service.get_resource(object(), f"{item.item_id}@{item.version}")
    )
    assert listed[0]["id"] == f"{item.item_id}@{item.version}"
    assert fetched["item_id"] == item.item_id
    assert fetched["integrity"]["signature_present"] is False  # type: ignore[index]


def test_preview_uses_server_resolved_validation_context_without_activation_router() -> None:
    item, artifact = _item()
    distribution = DistributionService(
        LocalRegistryProvider((item,), {(item.item_id, item.version): artifact})
    )
    resolver = StaticValidationContext(ValidationContext("0.0.1"))
    control_plane = RecordingControlPlane()
    register_distribution_control_plane(  # type: ignore[arg-type]
        control_plane,
        distribution,
        validation_context_resolver=resolver,
    )

    handler = control_plane.commands[REGISTRY_PREVIEW_COMMAND]
    result = asyncio.run(  # type: ignore[operator]
        handler(object(), item.item_id, {"version": item.version})
    )
    assert resolver.calls == 1
    assert result["activation_allowed"] is True
    assert result["route"] == "portable_import"
    assert result["provider_id"] == "local"
    assert REGISTRY_ACTIVATE_COMMAND not in control_plane.commands


def test_activation_command_exists_only_with_owner_router_and_revalidates_server_state() -> None:
    item, artifact = _item()
    router = RecordingRouter()
    distribution = DistributionService(
        LocalRegistryProvider((item,), {(item.item_id, item.version): artifact}),
        router,
    )
    resolver = StaticValidationContext(ValidationContext("0.0.1"))
    control_plane = RecordingControlPlane()
    register_distribution_control_plane(  # type: ignore[arg-type]
        control_plane,
        distribution,
        validation_context_resolver=resolver,
    )

    assert REGISTRY_PREVIEW_COMMAND in control_plane.commands
    assert REGISTRY_ACTIVATE_COMMAND in control_plane.commands

    handler = control_plane.commands[REGISTRY_ACTIVATE_COMMAND]
    result = asyncio.run(  # type: ignore[operator]
        handler(object(), item.item_id, {"version": item.version})
    )
    assert resolver.calls == 1
    assert result == {
        "id": f"{item.item_id}@{item.version}",
        "type": "registry-activation",
        "status": "applied",
        "route": "portable_import",
        "installation": None,
    }
    assert router.calls == [("portable", item.item_id)]
