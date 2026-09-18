from __future__ import annotations

import json
from dataclasses import replace

import pytest

from ai_multi_agent_platform.adapters.marketplace_owner_handlers import (
    ApplicationMarketplaceKindHandler,
    PluginExtensionMarketplaceKindHandler,
    SkillMarketplaceKindHandler,
)
from ai_multi_agent_platform.applications import (
    ApplicationDesiredState,
    ApplicationHealthStatus,
    ApplicationInstallRequest,
    ApplicationInstance,
    ApplicationLifecycleService,
    ApplicationManifest,
    ApplicationObservedState,
    ApplicationRuntimeDescriptor,
    ApplicationRuntimeRegistry,
    ApplicationService,
    ApplicationServiceRuntime,
    InMemoryApplicationRepository,
)
from ai_multi_agent_platform.applications.serialization import application_manifest_to_document
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.plugin_api import _manifest_document
from ai_multi_agent_platform.distribution import (
    DistributionService,
    JsonRegistryInstallationStore,
    LocalRegistryProvider,
    MarketplaceKindHandlerRegistry,
    PluginRegistryArtifactInstaller,
    RegistryItem,
    RegistryItemType,
    RegistryManifestReference,
    RegistrySource,
    TrustStatus,
    ValidationContext,
)
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.plugins import (
    ExtensionType,
    PluginRegistry,
    reference_manifest,
)
from ai_multi_agent_platform.skills.codec import skill_revision_to_json
from ai_multi_agent_platform.skills.models import SkillContent, SkillProfile, SkillRevision
from ai_multi_agent_platform.skills.repository import InMemorySkillRepository
from ai_multi_agent_platform.skills.service import SkillService

pytestmark = pytest.mark.asyncio


def _item(
    item_type: RegistryItemType,
    *,
    item_id: str,
    version: str,
    license_name: str = "MIT",
    manifest: bool = False,
) -> RegistryItem:
    return RegistryItem(
        item_id=item_id,
        item_type=item_type,
        name=f"Marketplace {item_type.value}",
        description=f"Marketplace {item_type.value} fixture",
        version=version,
        publisher="example",
        source=RegistrySource(
            "https://example.invalid/marketplace",
            f"{item_id}@{version}",
        ),
        license=license_name,
        provenance="test-release",
        trust_status=TrustStatus.REVIEWED,
        manifest=(
            RegistryManifestReference(
                kind=item_type,
                reference=f"manifests/{item_type.value}.json",
                schema_version="1",
            )
            if manifest
            else None
        ),
    )


def _plugin_artifact(manifest) -> bytes:
    return json.dumps(_manifest_document(manifest), sort_keys=True).encode("utf-8")


async def test_tool_handler_uses_plugin_owner_for_full_package_lifecycle(tmp_path) -> None:
    plugin_registry = PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={ExtensionType.CAPABILITY_PROVIDER: frozenset({"1.0"})},
    )
    installer = PluginRegistryArtifactInstaller(plugin_registry)
    handler = PluginExtensionMarketplaceKindHandler(
        kind=RegistryItemType.TOOL,
        extension_type=ExtensionType.CAPABILITY_PROVIDER,
        installer=installer,
        registry=plugin_registry,
    )

    first_manifest = reference_manifest()
    second_manifest = replace(first_manifest, plugin_version="1.1.0")
    first = _item(
        RegistryItemType.TOOL,
        item_id=first_manifest.plugin_id,
        version=first_manifest.plugin_version,
        license_name=first_manifest.provenance.license,
    )
    second = _item(
        RegistryItemType.TOOL,
        item_id=second_manifest.plugin_id,
        version=second_manifest.plugin_version,
        license_name=second_manifest.provenance.license,
    )
    provider = LocalRegistryProvider(
        (first, second),
        {
            (first.item_id, first.version): _plugin_artifact(first_manifest),
            (second.item_id, second.version): _plugin_artifact(second_manifest),
        },
    )
    installations = JsonRegistryInstallationStore(tmp_path / "installations.json")
    service = DistributionService(
        provider,
        installations=installations,
        kind_handlers=MarketplaceKindHandlerRegistry((handler,)),
    )
    context = ValidationContext("0.0.1")

    first_preview = service.preview(first.item_id, first.version, context)
    assert first_preview.activation_allowed is True
    installed = await service.activate(first_preview, context, authorized=True)
    assert installed.plugin_version == "1.0.0"

    status = await service.status(first.item_id)
    assert status.plugin_version == "1.0.0"
    assert service.describe(first.item_id)["owner_domain"] == "plugins"

    second_preview = service.preview(second.item_id, second.version, context)
    assert second_preview.activation_allowed is True
    updated = await service.activate(second_preview, context, authorized=True)
    assert updated.plugin_version == "1.1.0"

    await service.uninstall(second.item_id, authorized=True)
    assert installations.get(second.item_id) is None
    with pytest.raises(ContractError) as missing:
        plugin_registry.get(second.item_id)
    assert missing.value.code is ErrorCode.NOT_FOUND


async def test_connector_handler_requires_connector_provider_extension() -> None:
    base = reference_manifest()
    connector_manifest = replace(
        base,
        plugin_id="reference.connector-plugin",
        extensions=(
            replace(
                base.extensions[0],
                extension_id="reference.connector",
                extension_type=ExtensionType.CONNECTOR_PROVIDER,
            ),
        ),
        capabilities=(),
    )
    plugin_registry = PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={ExtensionType.CONNECTOR_PROVIDER: frozenset({"1.0"})},
    )
    handler = PluginExtensionMarketplaceKindHandler(
        kind=RegistryItemType.CONNECTOR,
        extension_type=ExtensionType.CONNECTOR_PROVIDER,
        installer=PluginRegistryArtifactInstaller(plugin_registry),
        registry=plugin_registry,
    )
    item = _item(
        RegistryItemType.CONNECTOR,
        item_id=connector_manifest.plugin_id,
        version=connector_manifest.plugin_version,
        license_name=connector_manifest.provenance.license,
    )

    snapshot = await handler.install(item, _plugin_artifact(connector_manifest))

    assert snapshot.plugin_id == connector_manifest.plugin_id
    assert (await handler.status(item)).plugin_id == connector_manifest.plugin_id
    assert handler.describe(item)["extension_type"] == "connector_provider"

    wrong = replace(
        connector_manifest,
        plugin_id="reference.not-a-connector",
        extensions=(base.extensions[0],),
    )
    wrong_item = _item(
        RegistryItemType.CONNECTOR,
        item_id=wrong.plugin_id,
        version=wrong.plugin_version,
        license_name=wrong.provenance.license,
    )
    with pytest.raises(ContractError) as unsupported:
        await handler.install(wrong_item, _plugin_artifact(wrong))
    assert unsupported.value.code is ErrorCode.INVALID_CONFIGURATION


async def test_skill_handler_delegates_revisions_and_removal_to_skill_service(tmp_path) -> None:
    skills = SkillService(InMemorySkillRepository())
    handler = SkillMarketplaceKindHandler(skills)
    first = _item(
        RegistryItemType.SKILL,
        item_id="catalog.review-skill",
        version="1.0.0",
    )
    second = replace(
        first,
        version="1.1.0",
        source=RegistrySource(
            first.source.repository,
            "catalog.review-skill@1.1.0",
        ),
    )
    owner = OwnerRef(type="user", id="marketplace-skill-owner")
    first_revision = SkillRevision(
        skill_id="skill_marketplace_review",
        revision=1,
        profile=SkillProfile(
            name="Marketplace review",
            purpose_categories=("review",),
            content=SkillContent(content="review v1"),
        ),
        owner_ref=owner,
    )
    second_revision = SkillRevision(
        skill_id=first_revision.skill_id,
        revision=2,
        profile=replace(first_revision.profile, content=SkillContent(content="review v2")),
        owner_ref=owner,
    )
    first_artifact = json.dumps(skill_revision_to_json(first_revision), sort_keys=True).encode()
    second_artifact = json.dumps(skill_revision_to_json(second_revision), sort_keys=True).encode()
    provider = LocalRegistryProvider(
        (first, second),
        {
            (first.item_id, first.version): first_artifact,
            (second.item_id, second.version): second_artifact,
        },
    )
    service = DistributionService(
        provider,
        installations=JsonRegistryInstallationStore(tmp_path / "skills-installations.json"),
        kind_handlers=MarketplaceKindHandlerRegistry((handler,)),
    )
    context = ValidationContext("0.0.1")

    installed = await service.activate(
        service.preview(first.item_id, first.version, context),
        context,
        authorized=True,
    )
    assert installed.revision == 1
    assert (await service.status(first.item_id)).revision == 1
    assert service.describe(first.item_id)["skill_id"] == first_revision.skill_id

    updated = await service.activate(
        service.preview(second.item_id, second.version, context),
        context,
        authorized=True,
    )
    assert updated.revision == 2

    await service.uninstall(second.item_id, authorized=True)
    with pytest.raises(ContractError) as missing:
        skills.get_skill_revision(first_revision.skill_id)
    assert missing.value.code is ErrorCode.NOT_FOUND


class _ApplicationRuntime:
    descriptor = ApplicationRuntimeDescriptor(
        runtime_id="test.process",
        supported_service_runtimes=frozenset({ApplicationServiceRuntime.PROCESS}),
    )

    async def prepare(self, request: ApplicationInstallRequest) -> ApplicationInstance:
        return ApplicationInstance(
            application_id=request.manifest.application_id,
            application_version=request.manifest.version,
            runtime_id=self.descriptor.runtime_id,
            desired_state=ApplicationDesiredState.STOPPED,
            observed_state=ApplicationObservedState.STOPPED,
            health=ApplicationHealthStatus.UNKNOWN,
            configuration=request.resolved_configuration(),
            secret_bindings=request.secret_bindings,
            volume_bindings=request.volume_bindings,
        )

    async def status(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        del manifest
        return instance

    async def remove(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> ApplicationInstance:
        del manifest
        return replace(
            instance,
            desired_state=ApplicationDesiredState.REMOVED,
            observed_state=ApplicationObservedState.REMOVED,
        )


async def test_application_handler_delegates_to_1173_and_rejects_fake_update(tmp_path) -> None:
    repository = InMemoryApplicationRepository()
    runtimes = ApplicationRuntimeRegistry((_ApplicationRuntime(),))
    lifecycle = ApplicationLifecycleService(repository, runtimes)
    handler = ApplicationMarketplaceKindHandler(lifecycle, repository, runtimes)

    first_manifest = ApplicationManifest(
        application_id="application_marketplace_demo",
        name="Marketplace demo",
        version="1.0.0",
        description="Application owner delegation fixture",
        services=(
            ApplicationService(
                service_id="app",
                runtime=ApplicationServiceRuntime.PROCESS,
                process=("python", "-m", "example"),
            ),
        ),
    )
    second_manifest = replace(first_manifest, version="1.1.0")
    first = _item(
        RegistryItemType.APPLICATION,
        item_id="catalog.application-demo",
        version=first_manifest.version,
        manifest=True,
    )
    second = replace(
        first,
        version=second_manifest.version,
        source=RegistrySource(
            first.source.repository,
            "catalog.application-demo@1.1.0",
        ),
    )
    provider = LocalRegistryProvider(
        (first, second),
        {
            (first.item_id, first.version): json.dumps(
                application_manifest_to_document(first_manifest),
                sort_keys=True,
            ).encode(),
            (second.item_id, second.version): json.dumps(
                application_manifest_to_document(second_manifest),
                sort_keys=True,
            ).encode(),
        },
    )
    installations = JsonRegistryInstallationStore(tmp_path / "application-installations.json")
    service = DistributionService(
        provider,
        installations=installations,
        kind_handlers=MarketplaceKindHandlerRegistry((handler,)),
    )
    context = ValidationContext("0.0.1")

    installed = await service.activate(
        service.preview(first.item_id, first.version, context),
        context,
        authorized=True,
    )
    assert installed.application_id == first_manifest.application_id
    assert (await service.status(first.item_id)).instance_id == installed.instance_id
    assert service.describe(first.item_id)["runtime_id"] == "test.process"

    update_preview = service.preview(second.item_id, second.version, context)
    assert update_preview.activation_allowed is False
    assert any(finding.code == "unsupported_operation" for finding in update_preview.findings)
    with pytest.raises(ContractError) as unsupported:
        await service.activate(update_preview, context, authorized=True)
    assert unsupported.value.code is ErrorCode.UNSUPPORTED_CAPABILITY

    removed = await service.uninstall(first.item_id, authorized=True)
    assert removed.observed_state is ApplicationObservedState.REMOVED
    assert installations.get(first.item_id) is None
