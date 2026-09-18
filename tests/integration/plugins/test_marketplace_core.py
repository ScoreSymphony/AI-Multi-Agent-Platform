from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.distribution import (
    DistributionRoute,
    DistributionService,
    JsonRegistryInstallationStore,
    LocalRegistryProvider,
    MarketplaceKindDescriptor,
    MarketplaceKindHandlerRegistry,
    MarketplaceKindRegistry,
    RegistryItem,
    RegistryItemType,
    RegistryManifestReference,
    RegistryQuery,
    RegistrySource,
    TrustStatus,
    ValidationContext,
    marketplace_kind_registry_with_builtins,
    registry_item_from_document,
)


def _item(
    item_type: RegistryItemType | str,
    *,
    item_id: str | None = None,
    manifest: RegistryManifestReference | None = None,
) -> RegistryItem:
    kind = item_type.value if isinstance(item_type, RegistryItemType) else item_type
    return RegistryItem(
        item_id=item_id or f"example.{kind}",
        item_type=item_type,
        name=f"Example {kind}",
        description=f"Reference {kind} component",
        version="1.0.0",
        publisher="example",
        source=RegistrySource("https://example.invalid/repo", f"{kind}@1.0.0"),
        license="MIT",
        provenance="source-release",
        trust_status=TrustStatus.REVIEWED,
        manifest=manifest,
    )


def _document(*, item_type: str, manifest: dict[str, str] | None = None) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": "3",
        "item_id": f"example.{item_type}",
        "item_type": item_type,
        "name": f"Example {item_type}",
        "description": "Reference component",
        "version": "1.0.0",
        "publisher": "example",
        "source": {
            "repository": "https://example.invalid/repo",
            "package_reference": f"{item_type}@1.0.0",
        },
        "license": "MIT",
        "provenance": "source-release",
        "supported_platform": {},
        "dependencies": [],
        "requested_permissions": [],
        "required_capabilities": [],
        "integrity": {},
        "trust_status": "reviewed",
        "deprecated": False,
        "yanked": False,
    }
    if manifest is not None:
        document["manifest"] = manifest
    return document


def test_builtin_marketplace_kinds_include_new_first_class_families() -> None:
    registry = marketplace_kind_registry_with_builtins()
    kinds = {descriptor.kind_value for descriptor in registry.list()}

    assert {"tool", "skill", "plugin", "connector", "application", "template"} <= kinds
    assert registry.require("application").default_route is DistributionRoute.KIND_HANDLER
    assert (
        registry.require(RegistryItemType.TOOL).default_route is DistributionRoute.PORTABLE_IMPORT
    )
    assert (
        registry.require(RegistryItemType.CONNECTOR).default_route
        is DistributionRoute.PORTABLE_IMPORT
    )
    assert registry.require(RegistryItemType.APPLICATION).supports_update is False


def test_manifest_backed_tool_and_connector_use_owner_handlers_without_regressing_legacy_routes() -> None:
    tool = _item(
        RegistryItemType.TOOL,
        manifest=RegistryManifestReference(kind="tool", reference="tools/example.json"),
    )
    connector = _item(
        RegistryItemType.CONNECTOR,
        manifest=RegistryManifestReference(
            kind="connector",
            reference="connectors/example.json",
        ),
    )
    legacy_tool = _item(RegistryItemType.TOOL, item_id="example.legacy-tool")
    legacy_connector = _item(
        RegistryItemType.CONNECTOR,
        item_id="example.legacy-connector",
    )

    assert tool.route is DistributionRoute.KIND_HANDLER
    assert connector.route is DistributionRoute.KIND_HANDLER
    assert legacy_tool.route is DistributionRoute.PORTABLE_IMPORT
    assert legacy_connector.route is DistributionRoute.PORTABLE_IMPORT


def test_new_marketplace_kind_can_be_registered_without_enum_change() -> None:
    registry = MarketplaceKindRegistry()
    descriptor = MarketplaceKindDescriptor(
        kind="notebook_extension",
        display_name="Notebook Extension",
        default_route=DistributionRoute.KIND_HANDLER,
    )

    registry.register(descriptor)

    assert registry.require("notebook_extension") is descriptor
    with pytest.raises(ValueError, match="already registered"):
        registry.register(descriptor)


def test_future_kind_requires_kind_specific_manifest_reference() -> None:
    with pytest.raises(ValueError, match="manifest reference"):
        _item("notebook_extension")

    item = _item(
        "notebook_extension",
        manifest=RegistryManifestReference(
            kind="notebook_extension",
            reference="manifests/notebook-extension.json",
            schema_version="1",
        ),
    )

    assert item.kind == "notebook_extension"
    assert item.route is DistributionRoute.KIND_HANDLER


def test_manifest_kind_must_match_marketplace_kind() -> None:
    with pytest.raises(ValueError, match="manifest kind"):
        _item(
            RegistryItemType.APPLICATION,
            manifest=RegistryManifestReference(kind="skill", reference="skill.json"),
        )


def test_schema_v3_preserves_unknown_kind_and_manifest_reference() -> None:
    item = registry_item_from_document(
        _document(
            item_type="notebook_extension",
            manifest={
                "kind": "notebook_extension",
                "reference": "manifests/notebook-extension.json",
                "schema_version": "1",
            },
        )
    )

    assert item.kind == "notebook_extension"
    assert item.manifest is not None
    assert item.manifest.kind_value == "notebook_extension"
    assert item.manifest.reference == "manifests/notebook-extension.json"


def test_schema_v3_parses_known_application_kind_back_to_enum() -> None:
    item = registry_item_from_document(
        _document(
            item_type="application",
            manifest={"kind": "application", "reference": "applications/example.json"},
        )
    )

    assert item.item_type is RegistryItemType.APPLICATION
    assert item.route is DistributionRoute.KIND_HANDLER


def test_cross_kind_search_accepts_known_and_future_kind_filters() -> None:
    tool = _item(RegistryItemType.TOOL)
    application = _item(RegistryItemType.APPLICATION)
    future = _item(
        "notebook_extension",
        manifest=RegistryManifestReference(
            kind="notebook_extension", reference="manifests/notebook-extension.json"
        ),
    )
    provider = LocalRegistryProvider((future, application, tool))

    assert provider.search(RegistryQuery(item_types=frozenset({"application"}))) == (application,)
    assert provider.search(RegistryQuery(item_types=frozenset({"notebook_extension"}))) == (future,)
    assert provider.search(RegistryQuery(text="notebook_extension")) == (future,)


def test_future_kind_installation_state_survives_restart(tmp_path: Path) -> None:
    item = _item(
        "notebook_extension",
        manifest=RegistryManifestReference(
            kind="notebook_extension", reference="manifests/notebook-extension.json"
        ),
    )
    path = tmp_path / "registry-installations.json"
    store = JsonRegistryInstallationStore(path)
    store.record(item, provider_id="local", artifact_sha256="0" * 64)

    reloaded = JsonRegistryInstallationStore(path)
    installation = reloaded.get(item.item_id)

    assert installation is not None
    assert installation.current.item_type is not None
    assert installation.current.item_type.value == "notebook_extension"


class RecordingApplicationHandler:
    kind = RegistryItemType.APPLICATION

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def inspect_requirements(self, item: RegistryItem) -> dict[str, object]:
        return {"kind": item.kind}

    async def install(self, item: RegistryItem, artifact: bytes) -> object:
        self.calls.append(("install", item.item_id))
        return artifact

    async def update(self, item: RegistryItem, artifact: bytes) -> object:
        self.calls.append(("update", item.item_id))
        return artifact

    async def uninstall(self, item: RegistryItem) -> object:
        self.calls.append(("uninstall", item.item_id))
        return None

    async def status(self, item: RegistryItem) -> object:
        return {"item_id": item.item_id}

    def describe(self, item: RegistryItem) -> dict[str, object]:
        return {"kind": item.kind}


def test_distribution_service_dispatches_application_to_registered_owner_handler() -> None:
    item = _item(
        RegistryItemType.APPLICATION,
        manifest=RegistryManifestReference(
            kind="application", reference="applications/example.json"
        ),
    )
    artifact = b"application-manifest"
    provider = LocalRegistryProvider((item,), {(item.item_id, item.version): artifact})
    handler = RecordingApplicationHandler()
    handlers = MarketplaceKindHandlerRegistry((handler,))
    service = DistributionService(provider, kind_handlers=handlers)
    context = ValidationContext("0.0.1")

    preview = service.preview(item.item_id, item.version, context)
    assert preview.route is DistributionRoute.KIND_HANDLER
    assert preview.activation_allowed is True

    result = asyncio.run(service.activate(preview, context, authorized=True))

    assert result == artifact
    assert handler.calls == [("install", item.item_id)]


def test_kind_handler_route_is_not_installable_without_owner_handler() -> None:
    item = _item(RegistryItemType.SKILL)
    provider = LocalRegistryProvider((item,), {(item.item_id, item.version): b"skill"})
    service = DistributionService(provider)

    preview = service.preview(item.item_id, item.version, ValidationContext("0.0.1"))

    assert preview.route is DistributionRoute.KIND_HANDLER
    assert preview.activation_allowed is False


def test_portable_template_route_remains_unchanged() -> None:
    registry = marketplace_kind_registry_with_builtins()
    assert (
        registry.require(RegistryItemType.TEMPLATE).default_route
        is DistributionRoute.PORTABLE_IMPORT
    )
    assert (
        registry.require(RegistryItemType.WORKFLOW).default_route
        is DistributionRoute.PORTABLE_IMPORT
    )


def test_missing_kind_handler_fails_with_typed_owner_capability_error() -> None:
    item = _item(RegistryItemType.SKILL)
    provider = LocalRegistryProvider((item,), {(item.item_id, item.version): b"skill"})
    service = DistributionService(provider)
    context = ValidationContext("0.0.1")
    preview = service.preview(item.item_id, item.version, context)

    with pytest.raises(ContractError) as missing:
        asyncio.run(service.activate(preview, context, authorized=True))

    assert missing.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
