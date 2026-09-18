from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from ai_multi_agent_platform.adapters.marketplace_owner_handlers import (
    AgentMarketplaceKindHandler,
    ApplicationMarketplaceKindHandler,
    PluginMarketplaceKindHandler,
    SkillMarketplaceKindHandler,
)
from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    InMemoryAgentRepository,
    InstructionSource,
    JsonAgentRepository,
)
from ai_multi_agent_platform.agents.service import AgentService
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
    SqliteApplicationRepository,
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
    RegistryQuery,
    RegistrySource,
    TrustStatus,
    ValidationContext,
    reconcile_registry_plugins,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.portability import AgentPortableCodec, snapshot_agent
from ai_multi_agent_platform.plugins import (
    ExtensionType,
    PluginManifest,
    PluginRegistry,
    reference_manifest,
)
from ai_multi_agent_platform.skills import JsonSkillRepository
from ai_multi_agent_platform.skills.codec import skill_revision_to_json
from ai_multi_agent_platform.skills.models import (
    SkillContent,
    SkillProfile,
    SkillRevision,
    SkillSource,
    SkillTrustStatus,
)
from ai_multi_agent_platform.skills.service import SkillService


class _FailOnceInstallationStore(JsonRegistryInstallationStore):
    def __init__(self, path: Path) -> None:
        self.fail_next_save = False
        super().__init__(path)

    def _save(self) -> None:
        if self.fail_next_save:
            self.fail_next_save = False
            raise OSError("forced marketplace persistence failure")
        super()._save()


class _MutableProvider:
    provider_id = "drifting"

    def __init__(self, item: RegistryItem, artifact: bytes) -> None:
        self.item = item
        self.artifact = artifact

    def search(self, query: RegistryQuery) -> tuple[RegistryItem, ...]:
        del query
        return (self.item,)

    def get(self, item_id: str, version: str | None = None) -> RegistryItem:
        if item_id != self.item.item_id or (version is not None and version != self.item.version):
            raise LookupError(item_id)
        return self.item

    def fetch_artifact(self, item_id: str, version: str) -> bytes:
        if item_id != self.item.item_id or version != self.item.version:
            raise LookupError(item_id)
        return bytes(self.artifact)


class _MutableSkillOwner:
    kind = RegistryItemType.SKILL

    def __init__(self) -> None:
        self.version: str | None = None
        self.fail_operation: str | None = None
        self.calls: list[str] = []

    def inspect_requirements(self, item: RegistryItem) -> dict[str, object]:
        return {"owner_domain": "test_skill_owner", "item_id": item.item_id}

    async def install(self, item: RegistryItem, artifact: bytes) -> object:
        del artifact
        self.calls.append("install")
        self._maybe_fail("install")
        if self.version is None:
            self.version = item.version
        elif self.version != item.version:
            raise ContractError(ErrorCode.CONFLICT, "different owner state already exists")
        return self.version

    async def update(self, item: RegistryItem, artifact: bytes) -> object:
        del artifact
        self.calls.append("update")
        self._maybe_fail("update")
        if self.version == item.version:
            return self.version
        if self.version is None:
            raise ContractError(ErrorCode.NOT_FOUND, "owner state is missing")
        self.version = item.version
        return self.version

    async def uninstall(self, item: RegistryItem) -> object:
        del item
        self.calls.append("uninstall")
        self._maybe_fail("uninstall")
        self.version = None
        return None

    async def status(self, item: RegistryItem) -> object:
        del item
        if self.version is None:
            raise ContractError(ErrorCode.NOT_FOUND, "owner state is missing")
        return self.version

    def describe(self, item: RegistryItem) -> dict[str, object]:
        del item
        return {"owner_domain": "test_skill_owner", "version": self.version}

    def _maybe_fail(self, operation: str) -> None:
        if self.fail_operation == operation:
            self.fail_operation = None
            raise RuntimeError(f"forced owner {operation} failure")


def _item(version: str) -> RegistryItem:
    return RegistryItem(
        item_id="atomicity.skill",
        item_type=RegistryItemType.SKILL,
        name="Atomicity skill",
        description="Marketplace owner atomicity fixture",
        version=version,
        publisher="tests",
        source=RegistrySource(
            "https://example.invalid/atomicity",
            f"atomicity.skill@{version}",
            revision=f"rev-{version}",
        ),
        license="MIT",
        provenance="atomicity-test",
        trust_status=TrustStatus.REVIEWED,
    )


def _service(
    tmp_path: Path,
) -> tuple[
    DistributionService,
    _FailOnceInstallationStore,
    _MutableSkillOwner,
    RegistryItem,
    RegistryItem,
    ValidationContext,
]:
    first = _item("1.0.0")
    second = _item("1.1.0")
    provider = LocalRegistryProvider(
        (first, second),
        {
            (first.item_id, first.version): b"skill-v1",
            (second.item_id, second.version): b"skill-v2",
        },
    )
    store = _FailOnceInstallationStore(tmp_path / "installations.json")
    owner = _MutableSkillOwner()
    service = DistributionService(
        provider,
        installations=store,
        kind_handlers=MarketplaceKindHandlerRegistry((owner,)),
    )
    return service, store, owner, first, second, ValidationContext("0.0.1")


@pytest.mark.asyncio
async def test_owner_failures_never_advance_marketplace_installation_evidence(
    tmp_path: Path,
) -> None:
    service, store, owner, first, second, context = _service(tmp_path)

    owner.fail_operation = "install"
    with pytest.raises(RuntimeError, match="forced owner install failure"):
        await service.activate(
            service.preview(first.item_id, first.version, context),
            context,
            authorized=True,
        )
    assert store.get(first.item_id) is None
    assert owner.version is None

    await service.activate(
        service.preview(first.item_id, first.version, context),
        context,
        authorized=True,
    )
    assert store.get(first.item_id).current.version == first.version  # type: ignore[union-attr]

    owner.fail_operation = "update"
    with pytest.raises(RuntimeError, match="forced owner update failure"):
        await service.activate(
            service.preview(second.item_id, second.version, context),
            context,
            authorized=True,
        )
    assert store.get(first.item_id).current.version == first.version  # type: ignore[union-attr]
    assert owner.version == first.version

    owner.fail_operation = "uninstall"
    with pytest.raises(RuntimeError, match="forced owner uninstall failure"):
        await service.uninstall(first.item_id, authorized=True)
    assert store.get(first.item_id).current.version == first.version  # type: ignore[union-attr]
    assert owner.version == first.version


@pytest.mark.asyncio
async def test_stale_install_preview_is_revalidated_before_owner_mutation(tmp_path: Path) -> None:
    first = _item("1.0.0")
    provider = _MutableProvider(first, b"skill-v1")
    store = JsonRegistryInstallationStore(tmp_path / "stale-install.json")
    owner = _MutableSkillOwner()
    service = DistributionService(
        provider,
        installations=store,
        kind_handlers=MarketplaceKindHandlerRegistry((owner,)),
    )
    context = ValidationContext("0.0.1")
    preview = service.preview(first.item_id, first.version, context)

    provider.item = replace(first, description="metadata drift after preview")

    with pytest.raises(RuntimeError, match="registry metadata changed after preview"):
        await service.activate(preview, context, authorized=True)

    assert owner.calls == []
    assert owner.version is None
    assert store.get(first.item_id) is None


@pytest.mark.asyncio
async def test_persistence_failure_rolls_back_marketplace_state_and_retry_recovers_owner_split(
    tmp_path: Path,
) -> None:
    service, store, owner, first, second, context = _service(tmp_path)

    install_preview = service.preview(first.item_id, first.version, context)
    store.fail_next_save = True
    with pytest.raises(OSError, match="forced marketplace persistence failure"):
        await service.activate(install_preview, context, authorized=True)

    assert store.get(first.item_id) is None
    assert owner.version == first.version

    await service.activate(install_preview, context, authorized=True)
    assert store.get(first.item_id).current.version == first.version  # type: ignore[union-attr]
    assert owner.version == first.version

    update_preview = service.preview(second.item_id, second.version, context)
    store.fail_next_save = True
    with pytest.raises(OSError, match="forced marketplace persistence failure"):
        await service.activate(update_preview, context, authorized=True)

    assert store.get(first.item_id).current.version == first.version  # type: ignore[union-attr]
    assert owner.version == second.version

    await service.activate(update_preview, context, authorized=True)
    assert store.get(first.item_id).current.version == second.version  # type: ignore[union-attr]
    assert owner.version == second.version

    uninstall_preview = service.preview_uninstall(first.item_id)
    store.fail_next_save = True
    with pytest.raises(OSError, match="forced marketplace persistence failure"):
        await service.uninstall(uninstall_preview, authorized=True)

    assert store.get(first.item_id).current.version == second.version  # type: ignore[union-attr]
    assert owner.version is None

    await service.uninstall(uninstall_preview, authorized=True)
    assert store.get(first.item_id) is None
    assert owner.version is None

    assert owner.calls == [
        "install",
        "install",
        "update",
        "update",
        "uninstall",
        "uninstall",
    ]


def _real_skill_fixture() -> tuple[RegistryItem, RegistryItem, bytes, bytes, str]:
    first = RegistryItem(
        item_id="atomicity.real-skill",
        item_type=RegistryItemType.SKILL,
        name="Atomicity real skill",
        description="Real canonical Skill owner recovery fixture",
        version="1.0.0",
        publisher="tests",
        source=RegistrySource(
            "https://example.invalid/atomicity-real-skill",
            "atomicity.real-skill@1.0.0",
            revision="source-1",
        ),
        license="MIT",
        provenance="atomicity-real-skill",
        trust_status=TrustStatus.REVIEWED,
    )
    second = replace(
        first,
        version="1.1.0",
        source=RegistrySource(
            first.source.repository,
            "atomicity.real-skill@1.1.0",
            revision="source-2",
        ),
    )
    skill_id = new_id("skill")
    owner = OwnerRef(type="user", id="atomicity-real-owner")
    first_revision = SkillRevision(
        skill_id=skill_id,
        revision=1,
        profile=SkillProfile(
            name="Atomicity real skill",
            purpose_categories=("recovery",),
            content=SkillContent(content="version one"),
            source=SkillSource(
                source_url=first.source.repository,
                source_revision="source-1",
                license="MIT",
            ),
            trust_status=SkillTrustStatus.DISCOVERED,
            enabled=False,
        ),
        owner_ref=owner,
    )
    second_revision = SkillRevision(
        skill_id=skill_id,
        revision=2,
        profile=replace(
            first_revision.profile,
            content=SkillContent(content="version two"),
        ),
        owner_ref=owner,
    )
    return (
        first,
        second,
        json.dumps(skill_revision_to_json(first_revision), sort_keys=True).encode(),
        json.dumps(skill_revision_to_json(second_revision), sort_keys=True).encode(),
        skill_id,
    )


@pytest.mark.asyncio
async def test_real_skill_owner_recovers_evidence_after_restart_for_install_update_and_uninstall(
    tmp_path: Path,
) -> None:
    first, second, artifact_v1, artifact_v2, skill_id = _real_skill_fixture()
    provider = LocalRegistryProvider(
        (first, second),
        {
            (first.item_id, first.version): artifact_v1,
            (second.item_id, second.version): artifact_v2,
        },
        provider_id="atomicity-real",
    )
    installation_path = tmp_path / "real-skill-installations.json"
    skill_path = tmp_path / "real-skills.json"
    context = ValidationContext("0.0.1")

    store = _FailOnceInstallationStore(installation_path)
    skills = SkillService(JsonSkillRepository(skill_path))
    service = DistributionService(
        provider,
        installations=store,
        kind_handlers=MarketplaceKindHandlerRegistry((SkillMarketplaceKindHandler(skills),)),
    )

    store.fail_next_save = True
    with pytest.raises(OSError, match="forced marketplace persistence failure"):
        await service.activate(
            service.preview(first.item_id, first.version, context),
            context,
            authorized=True,
        )
    assert store.get(first.item_id) is None
    assert SkillService(JsonSkillRepository(skill_path)).get_skill_revision(skill_id).revision == 1

    restarted_skills = SkillService(JsonSkillRepository(skill_path))
    restarted_store = _FailOnceInstallationStore(installation_path)
    restarted = DistributionService(
        provider,
        installations=restarted_store,
        kind_handlers=MarketplaceKindHandlerRegistry(
            (SkillMarketplaceKindHandler(restarted_skills),)
        ),
    )
    await restarted.activate(
        restarted.preview(first.item_id, first.version, context),
        context,
        authorized=True,
    )
    assert restarted_store.get(first.item_id).current.version == "1.0.0"  # type: ignore[union-attr]

    update_preview = restarted.preview(second.item_id, second.version, context)
    restarted_store.fail_next_save = True
    with pytest.raises(OSError, match="forced marketplace persistence failure"):
        await restarted.activate(update_preview, context, authorized=True)
    assert restarted_store.get(first.item_id).current.version == "1.0.0"  # type: ignore[union-attr]
    assert SkillService(JsonSkillRepository(skill_path)).get_skill_revision(skill_id).revision == 2

    after_update_restart_store = _FailOnceInstallationStore(installation_path)
    after_update_restart = DistributionService(
        provider,
        installations=after_update_restart_store,
        kind_handlers=MarketplaceKindHandlerRegistry(
            (SkillMarketplaceKindHandler(SkillService(JsonSkillRepository(skill_path))),)
        ),
    )
    await after_update_restart.activate(
        after_update_restart.preview(second.item_id, second.version, context),
        context,
        authorized=True,
    )
    assert (
        after_update_restart_store.get(first.item_id).current.version  # type: ignore[union-attr]
        == "1.1.0"
    )

    uninstall_preview = after_update_restart.preview_uninstall(first.item_id)
    after_update_restart_store.fail_next_save = True
    with pytest.raises(OSError, match="forced marketplace persistence failure"):
        await after_update_restart.uninstall(uninstall_preview, authorized=True)
    assert (
        after_update_restart_store.get(first.item_id).current.version  # type: ignore[union-attr]
        == "1.1.0"
    )
    with pytest.raises(ContractError) as removed_skill:
        SkillService(JsonSkillRepository(skill_path)).get_skill_revision(skill_id)
    assert removed_skill.value.code is ErrorCode.NOT_FOUND

    final_store = _FailOnceInstallationStore(installation_path)
    final_service = DistributionService(
        provider,
        installations=final_store,
        kind_handlers=MarketplaceKindHandlerRegistry(
            (SkillMarketplaceKindHandler(SkillService(JsonSkillRepository(skill_path))),)
        ),
    )
    await final_service.uninstall(first.item_id, authorized=True)
    assert final_store.get(first.item_id) is None


class _ApplicationRuntime:
    descriptor = ApplicationRuntimeDescriptor(
        runtime_id="atomicity.process",
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


def _real_application_fixture() -> tuple[RegistryItem, bytes]:
    item = RegistryItem(
        item_id="atomicity.application",
        item_type=RegistryItemType.APPLICATION,
        name="Atomicity application",
        description="Real canonical Application owner recovery fixture",
        version="1.0.0",
        publisher="tests",
        source=RegistrySource(
            "https://example.invalid/atomicity-application",
            "atomicity.application@1.0.0",
            revision="source-1",
        ),
        license="MIT",
        provenance="atomicity-application",
        trust_status=TrustStatus.REVIEWED,
        manifest=RegistryManifestReference(
            kind=RegistryItemType.APPLICATION,
            reference="manifests/atomicity.application.json",
            schema_version="1",
        ),
    )
    manifest = ApplicationManifest(
        application_id=new_id("application"),
        name="Atomicity Application",
        version=item.version,
        description="Canonical Application recovery fixture",
        services=(
            ApplicationService(
                service_id="app",
                runtime=ApplicationServiceRuntime.PROCESS,
                process=("python", "-m", "atomicity_application"),
            ),
        ),
    )
    artifact = json.dumps(
        application_manifest_to_document(manifest),
        sort_keys=True,
    ).encode()
    return item, artifact


@pytest.mark.asyncio
async def test_real_application_owner_recovers_evidence_after_restart_for_install_and_uninstall(
    tmp_path: Path,
) -> None:
    item, artifact = _real_application_fixture()
    provider = LocalRegistryProvider(
        (item,),
        {(item.item_id, item.version): artifact},
        provider_id="atomicity-application",
    )
    installation_path = tmp_path / "application-installations.json"
    application_path = tmp_path / "applications.sqlite3"
    context = ValidationContext("0.0.1")

    store = _FailOnceInstallationStore(installation_path)
    repository = SqliteApplicationRepository(application_path)
    runtimes = ApplicationRuntimeRegistry((_ApplicationRuntime(),))
    handler = ApplicationMarketplaceKindHandler(
        ApplicationLifecycleService(repository, runtimes),
        repository,
        runtimes,
    )
    service = DistributionService(
        provider,
        installations=store,
        kind_handlers=MarketplaceKindHandlerRegistry((handler,)),
    )

    store.fail_next_save = True
    with pytest.raises(OSError, match="forced marketplace persistence failure"):
        await service.activate(
            service.preview(item.item_id, item.version, context),
            context,
            authorized=True,
        )

    assert store.get(item.item_id) is None
    assert len(SqliteApplicationRepository(application_path).list_applications()) == 1

    restarted_repository = SqliteApplicationRepository(application_path)
    restarted_runtimes = ApplicationRuntimeRegistry((_ApplicationRuntime(),))
    restarted_handler = ApplicationMarketplaceKindHandler(
        ApplicationLifecycleService(restarted_repository, restarted_runtimes),
        restarted_repository,
        restarted_runtimes,
    )
    restarted_store = _FailOnceInstallationStore(installation_path)
    restarted = DistributionService(
        provider,
        installations=restarted_store,
        kind_handlers=MarketplaceKindHandlerRegistry((restarted_handler,)),
    )

    first_retry = await restarted.activate(
        restarted.preview(item.item_id, item.version, context),
        context,
        authorized=True,
    )
    assert first_retry.application_version == item.version
    installation = restarted_store.get(item.item_id)
    assert installation is not None
    assert installation.current.version == item.version
    assert installation.current.source_registry == "atomicity-application"
    assert len(restarted_repository.list_applications()) == 1
    assert len(restarted_repository.list_instances(application_id=first_retry.application_id)) == 1

    uninstall_preview = restarted.preview_uninstall(item.item_id)
    restarted_store.fail_next_save = True
    with pytest.raises(OSError, match="forced marketplace persistence failure"):
        await restarted.uninstall(uninstall_preview, authorized=True)

    installation_after_failure = restarted_store.get(item.item_id)
    assert installation_after_failure is not None
    assert installation_after_failure.current.version == item.version

    after_remove_repository = SqliteApplicationRepository(application_path)
    after_remove_runtimes = ApplicationRuntimeRegistry((_ApplicationRuntime(),))
    after_remove_handler = ApplicationMarketplaceKindHandler(
        ApplicationLifecycleService(after_remove_repository, after_remove_runtimes),
        after_remove_repository,
        after_remove_runtimes,
    )
    resolved_item = replace(item, source_registry="atomicity-application")
    with pytest.raises(ContractError) as removed:
        await after_remove_handler.status(resolved_item)
    assert removed.value.code is ErrorCode.NOT_FOUND

    final_store = _FailOnceInstallationStore(installation_path)
    final_repository = SqliteApplicationRepository(application_path)
    final_runtimes = ApplicationRuntimeRegistry((_ApplicationRuntime(),))
    final_service = DistributionService(
        provider,
        installations=final_store,
        kind_handlers=MarketplaceKindHandlerRegistry(
            (
                ApplicationMarketplaceKindHandler(
                    ApplicationLifecycleService(final_repository, final_runtimes),
                    final_repository,
                    final_runtimes,
                ),
            )
        ),
    )
    await final_service.uninstall(item.item_id, authorized=True)
    assert final_store.get(item.item_id) is None


class _PluginRouter:
    def __init__(self, installer: PluginRegistryArtifactInstaller) -> None:
        self._installer = installer

    async def install_plugin(self, item: RegistryItem, artifact: bytes) -> object:
        return await self._installer.install_verified_plugin(item, artifact)

    async def import_portable(self, item: RegistryItem, artifact: bytes) -> object:
        del item, artifact
        raise AssertionError("plugin recovery fixture must not use portable import")


def _plugin_item(manifest: PluginManifest) -> RegistryItem:
    return RegistryItem(
        item_id=manifest.plugin_id,
        item_type=RegistryItemType.PLUGIN,
        name=manifest.name,
        description=manifest.description,
        version=manifest.plugin_version,
        publisher=manifest.author,
        source=RegistrySource(
            "https://example.invalid/atomicity-plugin",
            f"{manifest.plugin_id}@{manifest.plugin_version}",
            revision=f"rev-{manifest.plugin_version}",
        ),
        license=manifest.provenance.license,
        provenance="atomicity-plugin",
        trust_status=TrustStatus.REVIEWED,
    )


def _plugin_artifact(manifest: PluginManifest) -> bytes:
    return json.dumps(_manifest_document(manifest), sort_keys=True).encode()


def _plugin_runtime() -> tuple[
    PluginRegistry,
    PluginRegistryArtifactInstaller,
    PluginMarketplaceKindHandler,
    _PluginRouter,
]:
    registry = PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={ExtensionType.CAPABILITY_PROVIDER: frozenset({"1.0"})},
    )
    installer = PluginRegistryArtifactInstaller(registry)
    return (
        registry,
        installer,
        PluginMarketplaceKindHandler(installer, registry),
        _PluginRouter(installer),
    )


@pytest.mark.asyncio
async def test_real_plugin_owner_recovers_install_update_uninstall_evidence_splits(
    tmp_path: Path,
) -> None:
    first_manifest = reference_manifest()
    second_manifest = replace(first_manifest, plugin_version="1.1.0")
    first = _plugin_item(first_manifest)
    second = _plugin_item(second_manifest)
    provider = LocalRegistryProvider(
        (first, second),
        {
            (first.item_id, first.version): _plugin_artifact(first_manifest),
            (second.item_id, second.version): _plugin_artifact(second_manifest),
        },
        provider_id="atomicity-plugin",
    )
    installation_path = tmp_path / "plugin-installations.json"
    context = ValidationContext("0.0.1")

    store = _FailOnceInstallationStore(installation_path)
    registry, _, handler, router = _plugin_runtime()
    service = DistributionService(
        provider,
        router,
        installations=store,
        kind_handlers=MarketplaceKindHandlerRegistry((handler,)),
    )

    store.fail_next_save = True
    with pytest.raises(OSError, match="forced marketplace persistence failure"):
        await service.activate(
            service.preview(first.item_id, first.version, context),
            context,
            authorized=True,
        )
    assert store.get(first.item_id) is None
    assert registry.get(first.item_id).plugin_version == first.version

    install_retry_store = _FailOnceInstallationStore(installation_path)
    install_retry_registry, _, install_retry_handler, install_retry_router = _plugin_runtime()
    assert (
        await reconcile_registry_plugins(
            provider,
            install_retry_store,
            install_retry_registry,
        )
        == ()
    )
    with pytest.raises(ContractError) as missing_after_install_restart:
        install_retry_registry.get(first.item_id)
    assert missing_after_install_restart.value.code is ErrorCode.NOT_FOUND

    install_retry = DistributionService(
        provider,
        install_retry_router,
        installations=install_retry_store,
        kind_handlers=MarketplaceKindHandlerRegistry((install_retry_handler,)),
    )
    await install_retry.activate(
        install_retry.preview(first.item_id, first.version, context),
        context,
        authorized=True,
    )
    assert install_retry_store.get(first.item_id) is not None
    assert install_retry_registry.get(first.item_id).plugin_version == first.version

    update_preview = install_retry.preview(second.item_id, second.version, context)
    install_retry_store.fail_next_save = True
    with pytest.raises(OSError, match="forced marketplace persistence failure"):
        await install_retry.activate(update_preview, context, authorized=True)
    installed_before_restart = install_retry_store.get(first.item_id)
    assert installed_before_restart is not None
    assert installed_before_restart.current.version == first.version
    assert install_retry_registry.get(first.item_id).plugin_version == second.version

    update_retry_store = _FailOnceInstallationStore(installation_path)
    update_retry_registry, _, update_retry_handler, update_retry_router = _plugin_runtime()
    assert await reconcile_registry_plugins(
        provider,
        update_retry_store,
        update_retry_registry,
    ) == (first.item_id,)
    assert update_retry_registry.get(first.item_id).plugin_version == first.version

    update_retry = DistributionService(
        provider,
        update_retry_router,
        installations=update_retry_store,
        kind_handlers=MarketplaceKindHandlerRegistry((update_retry_handler,)),
    )
    await update_retry.activate(
        update_retry.preview(second.item_id, second.version, context),
        context,
        authorized=True,
    )
    updated_evidence = update_retry_store.get(first.item_id)
    assert updated_evidence is not None
    assert updated_evidence.current.version == second.version
    assert update_retry_registry.get(first.item_id).plugin_version == second.version

    uninstall_preview = update_retry.preview_uninstall(first.item_id)
    update_retry_store.fail_next_save = True
    with pytest.raises(OSError, match="forced marketplace persistence failure"):
        await update_retry.uninstall(uninstall_preview, authorized=True)
    evidence_after_remove_failure = update_retry_store.get(first.item_id)
    assert evidence_after_remove_failure is not None
    assert evidence_after_remove_failure.current.version == second.version
    with pytest.raises(ContractError) as removed_before_restart:
        update_retry_registry.get(first.item_id)
    assert removed_before_restart.value.code is ErrorCode.NOT_FOUND

    uninstall_retry_store = _FailOnceInstallationStore(installation_path)
    uninstall_retry_registry, _, uninstall_retry_handler, uninstall_retry_router = _plugin_runtime()
    assert await reconcile_registry_plugins(
        provider,
        uninstall_retry_store,
        uninstall_retry_registry,
    ) == (first.item_id,)
    assert uninstall_retry_registry.get(first.item_id).plugin_version == second.version

    uninstall_retry = DistributionService(
        provider,
        uninstall_retry_router,
        installations=uninstall_retry_store,
        kind_handlers=MarketplaceKindHandlerRegistry((uninstall_retry_handler,)),
    )
    await uninstall_retry.uninstall(first.item_id, authorized=True)
    assert uninstall_retry_store.get(first.item_id) is None
    with pytest.raises(ContractError) as removed_after_retry:
        uninstall_retry_registry.get(first.item_id)
    assert removed_after_retry.value.code is ErrorCode.NOT_FOUND


def _agent_profile(name: str) -> AgentProfile:
    return AgentProfile(
        name=name,
        role="researcher",
        instructions=AgentInstructions(
            role=InstructionSource(content="Research through canonical platform capabilities.")
        ),
    )


def _agent_artifact(repository: InMemoryAgentRepository, agent_id: str) -> bytes:
    exported = AgentPortableCodec().serialize(snapshot_agent(repository, agent_id))
    return json.dumps(exported.payload, sort_keys=True).encode()


@pytest.mark.asyncio
async def test_real_agent_owner_recovers_marketplace_evidence_after_restart(
    tmp_path: Path,
) -> None:
    source_repository = InMemoryAgentRepository()
    source = AgentService(source_repository)
    owner = OwnerRef(type="user", id="atomicity-agent-owner")
    first_revision = source.create_agent(_agent_profile("Atomicity Agent v1"), owner_ref=owner)
    first_artifact = _agent_artifact(source_repository, first_revision.agent_id)
    second_revision = source.update_agent(
        first_revision.agent_id,
        _agent_profile("Atomicity Agent v2"),
    )
    second_artifact = _agent_artifact(source_repository, second_revision.agent_id)

    first = RegistryItem(
        item_id=first_revision.agent_id,
        item_type=RegistryItemType.AGENT,
        name="Atomicity Agent",
        description="Durable Agent Marketplace owner recovery fixture",
        version="1.0.0",
        publisher="tests",
        source=RegistrySource(
            "https://example.invalid/atomicity-agent",
            f"{first_revision.agent_id}@1.0.0",
            revision="source-1",
        ),
        license="MIT",
        provenance="atomicity-agent",
        trust_status=TrustStatus.REVIEWED,
    )
    second = replace(
        first,
        version="1.1.0",
        source=RegistrySource(
            first.source.repository,
            f"{first_revision.agent_id}@1.1.0",
            revision="source-2",
        ),
    )
    provider = LocalRegistryProvider(
        (first, second),
        {
            (first.item_id, first.version): first_artifact,
            (second.item_id, second.version): second_artifact,
        },
        provider_id="atomicity-agent",
    )
    installation_path = tmp_path / "agent-installations.json"
    agent_path = tmp_path / "agents.json"
    context = ValidationContext("0.0.1")

    store = _FailOnceInstallationStore(installation_path)
    service = DistributionService(
        provider,
        installations=store,
        kind_handlers=MarketplaceKindHandlerRegistry(
            (AgentMarketplaceKindHandler(AgentService(JsonAgentRepository(agent_path))),)
        ),
    )

    store.fail_next_save = True
    with pytest.raises(OSError, match="forced marketplace persistence failure"):
        await service.activate(
            service.preview(first.item_id, first.version, context),
            context,
            authorized=True,
        )
    assert store.get(first.item_id) is None
    restored_after_install = JsonAgentRepository(agent_path)
    assert restored_after_install.get_agent(first.item_id).current_revision == 1
    assert restored_after_install.list_agent_runs() == ()

    restarted_store = _FailOnceInstallationStore(installation_path)
    restarted = DistributionService(
        provider,
        installations=restarted_store,
        kind_handlers=MarketplaceKindHandlerRegistry(
            (AgentMarketplaceKindHandler(AgentService(JsonAgentRepository(agent_path))),)
        ),
    )
    await restarted.activate(
        restarted.preview(first.item_id, first.version, context),
        context,
        authorized=True,
    )
    assert restarted_store.get(first.item_id).current.version == "1.0.0"  # type: ignore[union-attr]
    assert JsonAgentRepository(agent_path).get_agent(first.item_id).current_revision == 1

    update_preview = restarted.preview(second.item_id, second.version, context)
    restarted_store.fail_next_save = True
    with pytest.raises(OSError, match="forced marketplace persistence failure"):
        await restarted.activate(update_preview, context, authorized=True)
    assert restarted_store.get(first.item_id).current.version == "1.0.0"  # type: ignore[union-attr]
    assert JsonAgentRepository(agent_path).get_agent(first.item_id).current_revision == 2

    after_update_store = _FailOnceInstallationStore(installation_path)
    after_update = DistributionService(
        provider,
        installations=after_update_store,
        kind_handlers=MarketplaceKindHandlerRegistry(
            (AgentMarketplaceKindHandler(AgentService(JsonAgentRepository(agent_path))),)
        ),
    )
    await after_update.activate(
        after_update.preview(second.item_id, second.version, context),
        context,
        authorized=True,
    )
    assert after_update_store.get(first.item_id).current.version == "1.1.0"  # type: ignore[union-attr]
    assert JsonAgentRepository(agent_path).get_agent(first.item_id).current_revision == 2

    uninstall_preview = after_update.preview_uninstall(first.item_id)
    after_update_store.fail_next_save = True
    with pytest.raises(OSError, match="forced marketplace persistence failure"):
        await after_update.uninstall(uninstall_preview, authorized=True)
    assert after_update_store.get(first.item_id).current.version == "1.1.0"  # type: ignore[union-attr]
    with pytest.raises(ContractError) as removed:
        AgentService(JsonAgentRepository(agent_path)).get_agent_revision(first.item_id)
    assert removed.value.code is ErrorCode.NOT_FOUND

    final_store = _FailOnceInstallationStore(installation_path)
    final_service = DistributionService(
        provider,
        installations=final_store,
        kind_handlers=MarketplaceKindHandlerRegistry(
            (AgentMarketplaceKindHandler(AgentService(JsonAgentRepository(agent_path))),)
        ),
    )
    await final_service.uninstall(first.item_id, authorized=True)
    assert final_store.get(first.item_id) is None
