from __future__ import annotations

import asyncio
import hashlib

from ai_multi_agent_platform.control_plane.models import PageQuery
from ai_multi_agent_platform.distribution import (
    REGISTRY_ACTIVATE_COMMAND,
    REGISTRY_COLLECTION,
    REGISTRY_PREVIEW_COMMAND,
    ArtifactIntegrity,
    DistributionService,
    LocalRegistryProvider,
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

    assert set(control_plane.resources) == {REGISTRY_COLLECTION}
    assert control_plane.commands == {}

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
