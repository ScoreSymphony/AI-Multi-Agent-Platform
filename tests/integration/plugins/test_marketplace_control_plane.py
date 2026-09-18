from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext, paginate
from ai_multi_agent_platform.distribution import (
    MARKETPLACE_INSTALL_COMMAND,
    MARKETPLACE_PREVIEW_COMMAND,
    MARKETPLACE_UNINSTALL_COMMAND,
    MARKETPLACE_UPDATE_COMMAND,
    REGISTRY_ACTIVATE_COMMAND,
    REGISTRY_COLLECTION,
    REGISTRY_PREVIEW_COMMAND,
    ArtifactIntegrity,
    DistributionService,
    JsonRegistryInstallationStore,
    LocalRegistryProvider,
    MarketplaceKindHandlerRegistry,
    MultiRegistryProvider,
    RegistryCommandHandlers,
    RegistryCompatibility,
    RegistryDependency,
    RegistryItem,
    RegistryItemType,
    RegistryManifestReference,
    RegistryResourceService,
    RegistrySource,
    RegistryUnavailableError,
    RegistryValidationContextResolver,
    TrustStatus,
    ValidationContext,
    VersionRange,
    register_distribution_control_plane,
)


class StaticValidationContext(RegistryValidationContextResolver):
    def __init__(self, context: ValidationContext) -> None:
        self.context = context

    async def resolve(self, context: RequestContext) -> ValidationContext:
        del context
        return self.context


class RecordingHandler:
    kind = RegistryItemType.APPLICATION

    def __init__(self, *, fail_install: bool = False) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.fail_install = fail_install

    def inspect_requirements(self, item: RegistryItem) -> dict[str, object]:
        return {"kind": item.kind, "required_capabilities": sorted(item.required_capabilities)}

    async def install(self, item: RegistryItem, artifact: bytes) -> object:
        del artifact
        if self.fail_install:
            raise RuntimeError("owner failed")
        self.calls.append(("install", item.item_id, item.version))
        return item.item_id

    async def update(self, item: RegistryItem, artifact: bytes) -> object:
        del artifact
        self.calls.append(("update", item.item_id, item.version))
        return item.item_id

    async def uninstall(self, item: RegistryItem) -> object:
        self.calls.append(("uninstall", item.item_id, item.version))
        return item.item_id

    async def status(self, item: RegistryItem) -> object:
        return {"owner_state": "installed", "item_id": item.item_id}

    def describe(self, item: RegistryItem) -> dict[str, object]:
        return {"manifest_kind": item.kind, "owner": "application"}


class RecordingControlPlane:
    def __init__(self) -> None:
        self.resources: dict[str, object] = {}
        self.commands: dict[str, object] = {}

    def register_resource_service(self, collection: str, service: object) -> None:
        self.resources[collection] = service

    def register_command(self, command: str, handler: object) -> None:
        self.commands[command] = handler


class UnavailableProvider:
    provider_id = "unavailable"

    def search(self, query: object) -> tuple[RegistryItem, ...]:
        del query
        raise RegistryUnavailableError("offline")

    def get(self, item_id: str, version: str | None = None) -> RegistryItem:
        del item_id, version
        raise RegistryUnavailableError("offline")

    def fetch_artifact(self, item_id: str, version: str) -> bytes:
        del item_id, version
        raise RegistryUnavailableError("offline")


class DriftingProvider:
    provider_id = "drifting"

    def __init__(
        self,
        before: RegistryItem,
        after: RegistryItem,
        artifact: bytes,
    ) -> None:
        self.before = before
        self.after = after
        self.artifact = artifact
        self.get_calls = 0

    def search(self, query: object) -> tuple[RegistryItem, ...]:
        del query
        return (self.before,)

    def get(self, item_id: str, version: str | None = None) -> RegistryItem:
        assert item_id == self.before.item_id
        assert version in {None, self.before.version}
        self.get_calls += 1
        return self.before if self.get_calls == 1 else self.after

    def fetch_artifact(self, item_id: str, version: str) -> bytes:
        assert item_id == self.before.item_id
        assert version == self.before.version
        return self.artifact


def _context(**kwargs: object) -> ValidationContext:
    return ValidationContext("1.0.0", **kwargs)  # type: ignore[arg-type]


def _request() -> RequestContext:
    return RequestContext("request_marketplace", "corr_marketplace")


def _source(item_id: str, version: str) -> RegistrySource:
    return RegistrySource(
        "https://example.invalid/repo",
        f"{item_id}@{version}",
        revision=f"v{version}",
    )


def _tool(
    item_id: str,
    name: str,
    *,
    version: str = "1.0.0",
    supported: VersionRange | None = None,
    deprecated: bool = False,
    yanked: bool = False,
) -> RegistryItem:
    return RegistryItem(
        item_id=item_id,
        item_type=RegistryItemType.TOOL,
        name=name,
        description=f"{name} description",
        version=version,
        publisher="example",
        source=_source(item_id, version),
        license="MIT",
        provenance="source-release",
        supported_platform=supported or VersionRange(),
        trust_status=TrustStatus.REVIEWED,
        tags=frozenset({"developer"}),
        categories=frozenset({"tools"}),
        released_at="2026-09-17",
        deprecated=deprecated,
        yanked=yanked,
    )


def _application(
    item_id: str,
    version: str,
    *,
    supported: VersionRange | None = None,
    dependencies: tuple[RegistryDependency, ...] = (),
    requested_permissions: frozenset[str] = frozenset(),
    integrity: ArtifactIntegrity | None = None,
    with_manifest: bool = True,
) -> RegistryItem:
    return RegistryItem(
        item_id=item_id,
        item_type=RegistryItemType.APPLICATION,
        name=f"Application {item_id}",
        description="Application fixture",
        version=version,
        publisher="example",
        source=_source(item_id, version),
        license="MIT",
        provenance="source-release",
        supported_platform=supported or VersionRange(),
        dependencies=dependencies,
        requested_permissions=requested_permissions,
        integrity=integrity or ArtifactIntegrity(),
        trust_status=TrustStatus.REVIEWED,
        tags=frozenset({"developer"}),
        categories=frozenset({"applications"}),
        released_at="2026-09-18",
        manifest=(
            RegistryManifestReference(
                kind="application",
                reference=f"applications/{item_id}-{version}.json",
                schema_version="1",
            )
            if with_manifest
            else None
        ),
    )


def _skill(item_id: str, version: str) -> RegistryItem:
    return RegistryItem(
        item_id=item_id,
        item_type=RegistryItemType.SKILL,
        name=f"Skill {item_id}",
        description="Skill fixture",
        version=version,
        publisher="example",
        source=_source(item_id, version),
        license="MIT",
        provenance="source-release",
        trust_status=TrustStatus.REVIEWED,
        tags=frozenset({"developer"}),
        categories=frozenset({"skills"}),
        released_at="2026-09-18",
    )


def _future_kind() -> RegistryItem:
    return RegistryItem(
        item_id="example.notebook",
        item_type="notebook_extension",
        name="Notebook Extension",
        description="Future-kind fixture",
        version="1.0.0",
        publisher="example",
        source=_source("example.notebook", "1.0.0"),
        license="MIT",
        provenance="source-release",
        trust_status=TrustStatus.REVIEWED,
        manifest=RegistryManifestReference(
            kind="notebook_extension",
            reference="manifests/notebook.json",
            schema_version="1",
        ),
    )


def test_cross_kind_search_kind_filters_and_future_kind() -> None:
    items = (
        _tool("example.tool", "Example Tool"),
        _application("example.app", "1.0.0"),
        _future_kind(),
    )
    service = RegistryResourceService(DistributionService(LocalRegistryProvider(items)))
    context = _request()

    all_items = asyncio.run(service.list_resources(context, PageQuery(search="example")))
    assert {item["kind"] for item in all_items} == {
        "tool",
        "application",
        "notebook_extension",
    }

    applications = asyncio.run(
        service.list_resources(
            context,
            PageQuery(filters={"kind": "application"}),
        )
    )
    assert [item["item_id"] for item in applications] == ["example.app"]

    future = asyncio.run(
        service.list_resources(
            context,
            PageQuery(filters={"kind": "notebook_extension"}),
        )
    )
    assert [item["item_id"] for item in future] == ["example.notebook"]

    with pytest.raises(ContractError) as invalid:
        asyncio.run(
            service.list_resources(
                context,
                PageQuery(filters={"kind": "INVALID KIND"}),
            )
        )
    assert invalid.value.code is ErrorCode.INVALID_REQUEST


def test_search_filters_installed_update_deprecated_yanked_and_compatibility(
    tmp_path: Path,
) -> None:
    current = _application("example.app", "1.0.0")
    update = _application("example.app", "2.0.0")
    deprecated = _tool("example.old", "Old Tool", deprecated=True)
    yanked = _tool("example.yanked", "Yanked Tool", yanked=True)
    incompatible = _tool(
        "example.future",
        "Future Tool",
        supported=VersionRange("2.0.0"),
    )
    store = JsonRegistryInstallationStore(tmp_path / "installations.json")
    store.record(current, provider_id="local")
    service = RegistryResourceService(
        DistributionService(
            LocalRegistryProvider((current, update, deprecated, yanked, incompatible)),
            installations=store,
        ),
        StaticValidationContext(_context()),
    )
    context = _request()

    updates = asyncio.run(
        service.list_resources(
            context,
            PageQuery(filters={"update_available": "true"}),
        )
    )
    assert [(item["item_id"], item["version"]) for item in updates] == [("example.app", "2.0.0")]

    deprecated_items = asyncio.run(
        service.list_resources(
            context,
            PageQuery(filters={"deprecated": "true"}),
        )
    )
    assert [item["item_id"] for item in deprecated_items] == ["example.old"]

    yanked_items = asyncio.run(
        service.list_resources(
            context,
            PageQuery(filters={"yanked": "true"}),
        )
    )
    assert [item["item_id"] for item in yanked_items] == ["example.yanked"]

    incompatible_items = asyncio.run(
        service.list_resources(
            context,
            PageQuery(
                filters={
                    "compatible": "false",
                    "platform_version": "1.0.0",
                }
            ),
        )
    )
    assert [item["item_id"] for item in incompatible_items] == ["example.future"]


def test_marketplace_sort_validation_and_canonical_pagination_are_deterministic() -> None:
    service = RegistryResourceService(
        DistributionService(
            LocalRegistryProvider(
                (
                    _tool("example.zeta", "Zeta"),
                    _tool("example.alpha", "Alpha"),
                    _tool("example.beta", "Beta"),
                )
            )
        )
    )
    context = _request()
    query = PageQuery(limit=2, sort="name", direction="asc")
    resources = asyncio.run(service.list_resources(context, query))

    first = paginate(resources, query)
    assert [item["name"] for item in first["items"]] == ["Alpha", "Beta"]  # type: ignore[index]
    assert first["next_cursor"] is not None

    second = paginate(
        resources,
        PageQuery(
            limit=2,
            cursor=str(first["next_cursor"]),
            sort="name",
            direction="asc",
        ),
    )
    assert [item["name"] for item in second["items"]] == ["Zeta"]  # type: ignore[index]

    with pytest.raises(ContractError) as invalid_sort:
        asyncio.run(service.list_resources(context, PageQuery(sort="relevance")))
    assert invalid_sort.value.code is ErrorCode.INVALID_REQUEST


def test_version_sort_is_numeric_and_deterministic() -> None:
    items = (
        _application("example.sort", "2.0.0"),
        _application("example.sort", "10.0.0"),
        _application("example.other", "3.0.0"),
    )
    service = RegistryResourceService(DistributionService(LocalRegistryProvider(items)))

    resources = asyncio.run(
        service.list_resources(
            _request(),
            PageQuery(sort="version", direction="desc"),
        )
    )

    assert [(item["item_id"], item["version"]) for item in resources] == [
        ("example.sort", "10.0.0"),
        ("example.other", "3.0.0"),
        ("example.sort", "2.0.0"),
    ]


def test_compatible_filter_uses_full_current_environment() -> None:
    compatible = replace(
        _tool("example.runtime-ok", "Runtime OK"),
        compatibility=RegistryCompatibility(
            operating_systems=frozenset({"linux"}),
            architectures=frozenset({"x86_64"}),
            required_runtimes=frozenset({"python"}),
        ),
    )
    incompatible = replace(
        _tool("example.runtime-missing", "Runtime Missing"),
        compatibility=RegistryCompatibility(
            operating_systems=frozenset({"linux"}),
            architectures=frozenset({"x86_64"}),
            required_runtimes=frozenset({"docker"}),
        ),
    )
    service = RegistryResourceService(
        DistributionService(LocalRegistryProvider((compatible, incompatible))),
        StaticValidationContext(
            _context(
                operating_system="linux",
                architecture="x86_64",
                available_runtimes=frozenset({"python"}),
            )
        ),
    )

    installable = asyncio.run(
        service.list_resources(
            _request(),
            PageQuery(filters={"compatible": "true"}),
        )
    )
    blocked = asyncio.run(
        service.list_resources(
            _request(),
            PageQuery(filters={"compatible": "false"}),
        )
    )

    assert [item["item_id"] for item in installable] == ["example.runtime-ok"]
    assert [item["item_id"] for item in blocked] == ["example.runtime-missing"]
    assert installable[0]["compatibility"]["compatible"] is True  # type: ignore[index]
    assert blocked[0]["compatibility"]["missing_runtimes"] == ["docker"]  # type: ignore[index]


def test_plugin_route_exposes_owner_extension_and_supports_marketplace_uninstall(
    tmp_path: Path,
) -> None:
    class RecordingPluginHandler(RecordingHandler):
        kind = RegistryItemType.PLUGIN

    item = RegistryItem(
        item_id="example.plugin",
        item_type=RegistryItemType.PLUGIN,
        name="Plugin",
        description="Plugin fixture",
        version="1.0.0",
        publisher="example",
        source=_source("example.plugin", "1.0.0"),
        license="MIT",
        provenance="source-release",
        trust_status=TrustStatus.REVIEWED,
    )
    provider = LocalRegistryProvider((item,))
    store = JsonRegistryInstallationStore(tmp_path / "plugin-owner.json")
    store.record(item, provider_id=provider.provider_id)
    handler = RecordingPluginHandler()
    distribution = DistributionService(
        provider,
        installations=store,
        kind_handlers=MarketplaceKindHandlerRegistry((handler,)),
    )

    detail = asyncio.run(
        RegistryResourceService(distribution).get_resource(_request(), item.item_id)
    )
    owner_extension = detail["owner_extension"]
    assert isinstance(owner_extension, dict)
    assert owner_extension["handler_available"] is True
    assert owner_extension["status"] == {
        "owner_state": "installed",
        "item_id": item.item_id,
    }

    commands = RegistryCommandHandlers(
        distribution,
        StaticValidationContext(_context()),
    )
    asyncio.run(commands.marketplace_uninstall(_request(), item.item_id, {}))

    assert handler.calls[-1] == ("uninstall", item.item_id, item.version)
    assert store.get(item.item_id) is None


def test_detail_exposes_manifest_update_state_compatibility_and_owner_extension(
    tmp_path: Path,
) -> None:
    current = _application("example.app", "1.0.0")
    candidate = _application("example.app", "2.0.0")
    artifacts = {
        (current.item_id, current.version): b"app-v1",
        (candidate.item_id, candidate.version): b"app-v2",
    }
    store = JsonRegistryInstallationStore(tmp_path / "installations.json")
    store.record(current, provider_id="local")
    handler = RecordingHandler()
    service = RegistryResourceService(
        DistributionService(
            LocalRegistryProvider((current, candidate), artifacts),
            installations=store,
            kind_handlers=MarketplaceKindHandlerRegistry((handler,)),
        ),
        StaticValidationContext(_context()),
    )

    detail = asyncio.run(service.get_resource(_request(), "example.app@2.0.0"))

    assert detail["kind"] == "application"
    assert detail["manifest_reference"] == {
        "kind": "application",
        "reference": "applications/example.app-2.0.0.json",
        "schema_version": "1",
    }
    assert detail["compatibility"]["platform_compatible"] is True  # type: ignore[index]
    assert detail["update_state"] == {
        "installed": True,
        "installed_version": "1.0.0",
        "candidate_version": "2.0.0",
        "pinned_version": None,
        "update_available": True,
    }
    assert detail["owner_extension"]["handler_available"] is True  # type: ignore[index]
    assert detail["owner_extension"]["status"]["owner_state"] == "installed"  # type: ignore[index]
    assert detail["owner_extension"]["status_version"] == "1.0.0"  # type: ignore[index]
    assert detail["owner_extension"]["details"]["owner"] == "application"  # type: ignore[index]


def test_manifestless_application_remains_manual_for_catalog_compatibility() -> None:
    legacy = _application("example.legacy-app", "1.0.0", with_manifest=False)
    service = RegistryResourceService(DistributionService(LocalRegistryProvider((legacy,))))

    detail = asyncio.run(service.get_resource(_request(), legacy.item_id))

    assert detail["kind"] == "application"
    assert detail["route"] == "manual"
    assert detail["route_available"] is False
    assert detail["manifest_reference"] is None
    assert detail["owner_extension"] is None


def test_marketplace_commands_install_update_uninstall_through_handler_and_state(
    tmp_path: Path,
) -> None:
    class RecordingSkillHandler(RecordingHandler):
        kind = RegistryItemType.SKILL

    v1 = _skill("example.skill", "1.0.0")
    v2 = _skill("example.skill", "2.0.0")
    provider = LocalRegistryProvider(
        (v1, v2),
        {
            (v1.item_id, v1.version): b"v1",
            (v2.item_id, v2.version): b"v2",
        },
    )
    store = JsonRegistryInstallationStore(tmp_path / "installations.json")
    handler = RecordingSkillHandler()
    distribution = DistributionService(
        provider,
        installations=store,
        kind_handlers=MarketplaceKindHandlerRegistry((handler,)),
    )
    commands = RegistryCommandHandlers(
        distribution,
        StaticValidationContext(_context()),
    )

    installed = asyncio.run(
        commands.marketplace_install(
            _request(),
            v1.item_id,
            {"version": v1.version},
        )
    )
    assert installed["action"] == "install"
    assert store.get(v1.item_id) is not None
    assert handler.calls == [("install", v1.item_id, "1.0.0")]

    updated = asyncio.run(
        commands.marketplace_update(
            _request(),
            v2.item_id,
            {"version": v2.version},
        )
    )
    assert updated["action"] == "update"
    assert store.get(v2.item_id).current.version == "2.0.0"  # type: ignore[union-attr]
    assert handler.calls[-1] == ("update", v2.item_id, "2.0.0")

    removed = asyncio.run(commands.marketplace_uninstall(_request(), v2.item_id, {}))
    assert removed["action"] == "uninstall"
    assert store.get(v2.item_id) is None
    assert handler.calls[-1] == ("uninstall", v2.item_id, "2.0.0")


def test_control_plane_registers_marketplace_aliases_without_breaking_registry_commands(
    tmp_path: Path,
) -> None:
    item = _application("example.app", "1.0.0")
    handler = RecordingHandler()
    distribution = DistributionService(
        LocalRegistryProvider(
            (item,),
            {(item.item_id, item.version): b"app"},
        ),
        installations=JsonRegistryInstallationStore(tmp_path / "installations.json"),
        kind_handlers=MarketplaceKindHandlerRegistry((handler,)),
    )
    control_plane = RecordingControlPlane()

    register_distribution_control_plane(
        control_plane,  # type: ignore[arg-type]
        distribution,
        validation_context_resolver=StaticValidationContext(_context()),
    )

    assert set(control_plane.resources) == {REGISTRY_COLLECTION}
    assert {
        REGISTRY_PREVIEW_COMMAND,
        REGISTRY_ACTIVATE_COMMAND,
        MARKETPLACE_PREVIEW_COMMAND,
        MARKETPLACE_INSTALL_COMMAND,
        MARKETPLACE_UPDATE_COMMAND,
        MARKETPLACE_UNINSTALL_COMMAND,
    } <= set(control_plane.commands)


def test_marketplace_preview_reflects_actual_route_availability() -> None:
    item = _tool("example.preview-tool", "Preview Tool")
    distribution = DistributionService(
        LocalRegistryProvider(
            (item,),
            {(item.item_id, item.version): b"tool"},
        )
    )
    commands = RegistryCommandHandlers(
        distribution,
        StaticValidationContext(_context()),
    )

    registry_preview = asyncio.run(
        commands.preview(
            _request(),
            item.item_id,
            {"version": item.version},
        )
    )
    marketplace_preview = asyncio.run(
        commands.marketplace_preview(
            _request(),
            item.item_id,
            {"version": item.version},
        )
    )

    assert registry_preview["activation_allowed"] is True
    assert registry_preview["item"]["route_available"] is None  # type: ignore[index]
    assert marketplace_preview["activation_allowed"] is False
    assert marketplace_preview["item"]["route_available"] is False  # type: ignore[index]
    assert "decision" not in registry_preview
    assert marketplace_preview["decision"]["operation"] == "install"  # type: ignore[index]
    assert marketplace_preview["decision"]["compatibility"]["compatible"] is True  # type: ignore[index]


def test_handler_unavailable_fails_with_typed_marketplace_error(tmp_path: Path) -> None:
    skill = RegistryItem(
        item_id="example.skill",
        item_type=RegistryItemType.SKILL,
        name="Skill",
        description="Skill fixture",
        version="1.0.0",
        publisher="example",
        source=_source("example.skill", "1.0.0"),
        license="MIT",
        provenance="source-release",
        trust_status=TrustStatus.REVIEWED,
        manifest=RegistryManifestReference(kind="skill", reference="skills/example.json"),
    )
    distribution = DistributionService(
        LocalRegistryProvider(
            (skill,),
            {(skill.item_id, skill.version): b"skill"},
        ),
        installations=JsonRegistryInstallationStore(tmp_path / "installations.json"),
    )
    resolver = StaticValidationContext(_context())
    commands = RegistryCommandHandlers(distribution, resolver)
    control_plane = RecordingControlPlane()
    register_distribution_control_plane(
        control_plane,  # type: ignore[arg-type]
        distribution,
        validation_context_resolver=resolver,
    )
    assert {
        MARKETPLACE_INSTALL_COMMAND,
        MARKETPLACE_UPDATE_COMMAND,
        MARKETPLACE_UNINSTALL_COMMAND,
    } <= set(control_plane.commands)
    assert REGISTRY_ACTIVATE_COMMAND not in control_plane.commands

    preview = asyncio.run(
        commands.marketplace_preview(
            _request(),
            skill.item_id,
            {"version": skill.version},
        )
    )
    assert preview["activation_allowed"] is False

    with pytest.raises(ContractError) as error:
        asyncio.run(
            commands.marketplace_install(
                _request(),
                skill.item_id,
                {"version": skill.version},
            )
        )
    assert error.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
    assert error.value.details["marketplace_reason"] == "missing_handler"


@pytest.mark.parametrize(
    ("item", "artifact", "expected_code", "reason"),
    [
        (
            _application(
                "example.incompatible",
                "1.0.0",
                supported=VersionRange("2.0.0"),
            ),
            b"component",
            ErrorCode.NO_COMPATIBLE_ROUTE,
            "compatibility_block",
        ),
        (
            _application(
                "example.dependency",
                "1.0.0",
                dependencies=(RegistryDependency("example.missing"),),
            ),
            b"component",
            ErrorCode.CONFLICT,
            "dependency_block",
        ),
        (
            _application(
                "example.integrity",
                "1.0.0",
                integrity=ArtifactIntegrity(sha256="0" * 64),
            ),
            b"component",
            ErrorCode.PERMANENT_FAILURE,
            "integrity_failure",
        ),
        (
            _application(
                "example.policy",
                "1.0.0",
                requested_permissions=frozenset({"filesystem.write"}),
            ),
            b"component",
            ErrorCode.FORBIDDEN,
            "policy_block",
        ),
    ],
)
def test_marketplace_validation_blocks_have_canonical_error_contracts(
    tmp_path: Path,
    item: RegistryItem,
    artifact: bytes,
    expected_code: ErrorCode,
    reason: str,
) -> None:
    handler = RecordingHandler()
    distribution = DistributionService(
        LocalRegistryProvider(
            (item,),
            {(item.item_id, item.version): artifact},
        ),
        installations=JsonRegistryInstallationStore(tmp_path / f"{item.item_id}.json"),
        kind_handlers=MarketplaceKindHandlerRegistry((handler,)),
    )
    commands = RegistryCommandHandlers(
        distribution,
        StaticValidationContext(_context()),
    )

    with pytest.raises(ContractError) as error:
        asyncio.run(
            commands.marketplace_install(
                _request(),
                item.item_id,
                {"version": item.version},
            )
        )
    assert error.value.code is expected_code
    assert error.value.details["marketplace_reason"] == reason


def test_uninstall_fails_closed_when_registry_metadata_drifts_after_preview(
    tmp_path: Path,
) -> None:
    item = _application("example.drift", "1.0.0")
    changed = replace(item, description="changed after preview")
    store = JsonRegistryInstallationStore(tmp_path / "drift-installations.json")
    store.record(item, provider_id="drifting")
    handler = RecordingHandler()
    commands = RegistryCommandHandlers(
        DistributionService(
            DriftingProvider(item, changed, b"component"),  # type: ignore[arg-type]
            installations=store,
            kind_handlers=MarketplaceKindHandlerRegistry((handler,)),
        ),
        StaticValidationContext(_context()),
    )

    with pytest.raises(ContractError) as error:
        asyncio.run(commands.marketplace_uninstall(_request(), item.item_id, {}))

    assert error.value.code is ErrorCode.CONFLICT
    assert error.value.details["marketplace_reason"] == "preview_drift"
    assert handler.calls == []
    assert store.get(item.item_id) is not None


def test_provider_and_owner_failures_use_canonical_error_boundaries(tmp_path: Path) -> None:
    unavailable = RegistryResourceService(
        DistributionService(UnavailableProvider())  # type: ignore[arg-type]
    )
    with pytest.raises(ContractError) as provider_error:
        asyncio.run(unavailable.list_resources(_request(), PageQuery()))
    assert provider_error.value.code is ErrorCode.UNAVAILABLE
    assert provider_error.value.retryable is True
    assert provider_error.value.details["marketplace_reason"] == "provider_failure"

    item = _application("example.owner-failure", "1.0.0")
    failing = RecordingHandler(fail_install=True)
    commands = RegistryCommandHandlers(
        DistributionService(
            LocalRegistryProvider(
                (item,),
                {(item.item_id, item.version): b"component"},
            ),
            installations=JsonRegistryInstallationStore(tmp_path / "owner-failure.json"),
            kind_handlers=MarketplaceKindHandlerRegistry((failing,)),
        ),
        StaticValidationContext(_context()),
    )
    with pytest.raises(ContractError) as owner_error:
        asyncio.run(
            commands.marketplace_install(
                _request(),
                item.item_id,
                {"version": item.version},
            )
        )
    assert owner_error.value.code is ErrorCode.BACKEND_ERROR
    assert owner_error.value.details["marketplace_reason"] == "owner_failure"


def test_marketplace_item_not_found_is_canonical_not_found() -> None:
    service = RegistryResourceService(DistributionService(LocalRegistryProvider()))

    with pytest.raises(ContractError) as error:
        asyncio.run(service.get_resource(_request(), "missing.item"))

    assert error.value.code is ErrorCode.NOT_FOUND


def test_marketplace_uninstall_enforces_persisted_reverse_dependency_decision(
    tmp_path: Path,
) -> None:
    target = _application("example.target", "1.0.0")
    dependent = _application(
        "example.dependent",
        "1.0.0",
        dependencies=(RegistryDependency(target.item_id),),
    )
    artifacts = {
        (target.item_id, target.version): b"target",
        (dependent.item_id, dependent.version): b"dependent",
    }
    store = JsonRegistryInstallationStore(tmp_path / "reverse-dependencies.json")
    store.record(target, provider_id="local")
    store.record(dependent, provider_id="local")
    handler = RecordingHandler()
    commands = RegistryCommandHandlers(
        DistributionService(
            LocalRegistryProvider((target, dependent), artifacts),
            installations=store,
            kind_handlers=MarketplaceKindHandlerRegistry((handler,)),
        ),
        StaticValidationContext(_context()),
    )

    with pytest.raises(ContractError) as error:
        asyncio.run(commands.marketplace_uninstall(_request(), target.item_id, {}))

    assert error.value.code is ErrorCode.CONFLICT
    assert error.value.details["marketplace_reason"] == "dependency_block"
    assert store.get(target.item_id) is not None
    assert handler.calls == []


def test_marketplace_multi_source_discovery_detail_and_preview_are_source_qualified(
    tmp_path: Path,
) -> None:
    source_a_item = _application("example.shared", "1.0.0")
    source_b_item = replace(
        source_a_item,
        publisher="publisher-b",
        source=RegistrySource(
            "https://example.invalid/source-b",
            "example.shared@1.0.0",
            revision="source-b-v1",
        ),
    )
    source_a = LocalRegistryProvider(
        (source_a_item,),
        {(source_a_item.item_id, source_a_item.version): b"source-a"},
        provider_id="source-a",
    )
    source_b = LocalRegistryProvider(
        (source_b_item,),
        {(source_b_item.item_id, source_b_item.version): b"source-b"},
        provider_id="source-b",
    )
    distribution = DistributionService(
        MultiRegistryProvider((source_a, source_b)),
        installations=JsonRegistryInstallationStore(tmp_path / "multi-source.json"),
        kind_handlers=MarketplaceKindHandlerRegistry((RecordingHandler(),)),
    )
    service = RegistryResourceService(distribution, StaticValidationContext(_context()))
    commands = RegistryCommandHandlers(distribution, StaticValidationContext(_context()))

    source_b_results = asyncio.run(
        service.list_resources(
            _request(),
            PageQuery(filters={"source": "source-b"}),
        )
    )
    assert len(source_b_results) == 1
    assert source_b_results[0]["source_registry"] == "source-b"
    assert source_b_results[0]["qualified_id"] == "source-b::example.shared@1.0.0"

    detail = asyncio.run(
        service.get_resource(
            _request(),
            "source-b::example.shared@1.0.0",
        )
    )
    assert detail["publisher"] == "publisher-b"
    assert detail["source_registry"] == "source-b"

    with pytest.raises(ContractError) as ambiguous:
        asyncio.run(service.get_resource(_request(), "example.shared@1.0.0"))
    assert ambiguous.value.code is ErrorCode.CONFLICT
    assert ambiguous.value.details["marketplace_reason"] == "source_ambiguous"

    preview = asyncio.run(
        commands.marketplace_preview(
            _request(),
            source_b_item.item_id,
            {
                "version": source_b_item.version,
                "source_registry": "source-b",
            },
        )
    )
    assert preview["decision"]["provenance_diff"]["candidate_source_registry"] == "source-b"  # type: ignore[index]
    assert preview["item"]["source_registry"] == "source-b"  # type: ignore[index]


def test_marketplace_preview_serializes_structured_decision_findings(
    tmp_path: Path,
) -> None:
    item = _application(
        "example.decision",
        "1.0.0",
        dependencies=(RegistryDependency("example.missing"),),
        requested_permissions=frozenset({"filesystem.write"}),
    )
    commands = RegistryCommandHandlers(
        DistributionService(
            LocalRegistryProvider(
                (item,),
                {(item.item_id, item.version): b"component"},
            ),
            installations=JsonRegistryInstallationStore(tmp_path / "decision.json"),
            kind_handlers=MarketplaceKindHandlerRegistry((RecordingHandler(),)),
        ),
        StaticValidationContext(_context()),
    )

    preview = asyncio.run(
        commands.marketplace_preview(
            _request(),
            item.item_id,
            {"version": item.version},
        )
    )

    decision = preview["decision"]
    assert decision["operation"] == "install"  # type: ignore[index]
    assert decision["dependency_blocked"] is True  # type: ignore[index]
    assert decision["dependencies"][0]["status"] == "missing"  # type: ignore[index]
    assert decision["permission_diff"]["added"] == ["filesystem.write"]  # type: ignore[index]
    findings = preview["findings"]
    assert {finding["category"] for finding in findings} >= {"dependency", "permission"}  # type: ignore[index]
