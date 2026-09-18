from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from ai_multi_agent_platform.adapters.hermes import (
    HERMES_ADAPTER_ID,
    HermesAdapterConfig,
    HermesOrchestrator,
)
from ai_multi_agent_platform.adapters.marketplace_owner_handlers import (
    AgentMarketplaceKindHandler,
    AgentTeamMarketplaceKindHandler,
    ApplicationMarketplaceKindHandler,
    PluginExtensionMarketplaceKindHandler,
    PluginMarketplaceKindHandler,
    SkillMarketplaceKindHandler,
)
from ai_multi_agent_platform.agents.models import (
    AgentInstructions,
    AgentProfile,
    AgentRevisionRef,
    AgentTeamMember,
    AgentTeamProfile,
    InstructionSource,
)
from ai_multi_agent_platform.agents.repository import InMemoryAgentRepository
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
    InMemoryApplicationRepository,
)
from ai_multi_agent_platform.applications.serialization import application_manifest_to_document
from ai_multi_agent_platform.connectors import (
    ConnectorDefinition,
    ConnectorRegistry,
    ConnectorService,
    InMemoryConnectorRepository,
    ReferenceConnectorProvider,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.plugin_api import _manifest_document
from ai_multi_agent_platform.distribution import (
    DistributionService,
    JsonRegistryInstallationStore,
    LocalRegistryProvider,
    MarketplaceKindHandlerRegistry,
    PluginRegistryArtifactInstaller,
    RegistryDependency,
    RegistryItem,
    RegistryItemType,
    RegistryManifestReference,
    RegistryPluginReconciliationError,
    RegistryQuery,
    RegistrySource,
    TrustStatus,
    ValidationContext,
    reconcile_registry_plugins,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.execution import ExecutorRegistry, ReferenceExecutor
from ai_multi_agent_platform.models import ModelRegistry
from ai_multi_agent_platform.orchestration import (
    OrchestratorRegistry,
    OrchestratorSelection,
    ReferenceOrchestrator,
)
from ai_multi_agent_platform.plugins import (
    ConnectorRegistryBinder,
    ExecutorRegistryBinder,
    ExtensionRegistration,
    ExtensionType,
    ModelProviderRegistryBinder,
    OrchestratorRegistryBinder,
    PluginContext,
    PluginExtensionSpec,
    PluginHealth,
    PluginHealthReport,
    PluginRegistry,
    reference_manifest,
)
from ai_multi_agent_platform.portability import (
    AgentPortableCodec,
    AgentTeamPortableCodec,
    snapshot_agent,
    snapshot_agent_team,
)
from ai_multi_agent_platform.skills.codec import skill_revision_to_json
from ai_multi_agent_platform.skills.models import (
    SkillContent,
    SkillProfile,
    SkillRevision,
    SkillSource,
    SkillTrustStatus,
)
from ai_multi_agent_platform.skills.repository import InMemorySkillRepository
from ai_multi_agent_platform.skills.service import SkillService
from ai_multi_agent_platform.testing import FakeModelProvider

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


class _PluginRouter:
    def __init__(self, installer: PluginRegistryArtifactInstaller) -> None:
        self._installer = installer
        self.plugin_install_calls = 0

    async def install_plugin(self, item: RegistryItem, artifact: bytes) -> object:
        self.plugin_install_calls += 1
        return await self._installer.install_verified_plugin(item, artifact)

    async def import_portable(self, item: RegistryItem, artifact: bytes) -> object:
        del item, artifact
        raise AssertionError("Plugin compatibility test must not use portable import")


def _agent_profile(name: str) -> AgentProfile:
    return AgentProfile(
        name=name,
        role="researcher",
        instructions=AgentInstructions(
            role=InstructionSource(content="Research through canonical platform capabilities.")
        ),
    )


def _portable_agent_artifact(repository: InMemoryAgentRepository, agent_id: str) -> bytes:
    exported = AgentPortableCodec().serialize(snapshot_agent(repository, agent_id))
    return json.dumps(exported.payload, sort_keys=True).encode("utf-8")


def _portable_team_artifact(repository: InMemoryAgentRepository, team_id: str) -> bytes:
    exported = AgentTeamPortableCodec().serialize(snapshot_agent_team(repository, team_id))
    return json.dumps(exported.payload, sort_keys=True).encode("utf-8")


async def test_malformed_skill_candidate_fails_preview_without_owner_mutation(
    tmp_path,
) -> None:
    item = _item(
        RegistryItemType.SKILL,
        item_id="malformed.skill",
        version="1.0.0",
    )
    artifact = b"{}"
    skills = SkillService(InMemorySkillRepository())
    service = DistributionService(
        LocalRegistryProvider(
            (item,),
            {(item.item_id, item.version): artifact},
        ),
        installations=JsonRegistryInstallationStore(tmp_path / "malformed-skill.json"),
        kind_handlers=MarketplaceKindHandlerRegistry((SkillMarketplaceKindHandler(skills),)),
    )

    preview = service.preview(
        item.item_id,
        item.version,
        ValidationContext("0.0.1"),
    )

    assert preview.activation_allowed is False
    assert any(
        finding.code == "owner_candidate_invalid"
        and "invalid canonical Skill artifact" in finding.message
        for finding in preview.findings
    )
    assert skills.repository.list_skills() == ()
    assert service.installed(item.item_id) is None


async def test_agent_handler_installs_canonical_revisions_without_runtime_instance() -> None:
    owner = OwnerRef(type="user", id="marketplace-agent-owner")
    source_repository = InMemoryAgentRepository()
    source = AgentService(source_repository)
    first = source.create_agent(_agent_profile("Research Agent v1"), owner_ref=owner)
    second = source.update_agent(first.agent_id, _agent_profile("Research Agent v2"))

    item = _item(
        RegistryItemType.AGENT,
        item_id=second.agent_id,
        version="1.0.0",
    )
    target_repository = InMemoryAgentRepository()
    target = AgentService(target_repository)
    handler = AgentMarketplaceKindHandler(target)

    installed = await handler.install(
        item,
        _portable_agent_artifact(source_repository, second.agent_id),
    )

    assert installed.agent_id == second.agent_id
    assert installed.revision == 2
    assert target.get_agent_revision(second.agent_id, 1).profile.name == "Research Agent v1"
    assert target.get_agent_revision(second.agent_id).profile.name == "Research Agent v2"
    assert target_repository.list_agent_runs() == ()
    assert handler.describe(item)["owner_domain"] == "agents"

    retried_install = await handler.install(
        item,
        _portable_agent_artifact(source_repository, second.agent_id),
    )
    assert retried_install.revision == 2
    assert target_repository.list_agent_revisions(second.agent_id) == (
        target.get_agent_revision(second.agent_id, 1),
        target.get_agent_revision(second.agent_id, 2),
    )

    third = source.update_agent(second.agent_id, _agent_profile("Research Agent v3"))
    updated_item = replace(
        item,
        version="1.1.0",
        source=RegistrySource(item.source.repository, f"{item.item_id}@1.1.0"),
    )
    updated = await handler.update(
        updated_item,
        _portable_agent_artifact(source_repository, third.agent_id),
    )
    assert updated.revision == 3
    assert updated.profile.name == "Research Agent v3"

    retried_update = await handler.update(
        updated_item,
        _portable_agent_artifact(source_repository, third.agent_id),
    )
    assert retried_update.revision == 3
    assert len(target_repository.list_agent_revisions(second.agent_id)) == 3

    await handler.uninstall(updated_item)
    await handler.uninstall(updated_item)
    with pytest.raises(ContractError) as missing:
        target.get_agent_revision(second.agent_id)
    assert missing.value.code is ErrorCode.NOT_FOUND
    assert target_repository.list_agent_runs() == ()


async def test_agent_team_handler_uses_canonical_team_owner_and_member_validation() -> None:
    owner = OwnerRef(type="user", id="marketplace-team-owner")
    source_repository = InMemoryAgentRepository()
    source = AgentService(source_repository)
    member = source.create_agent(_agent_profile("Team Researcher"), owner_ref=owner)
    team = source.create_team(
        AgentTeamProfile(
            name="Research Team",
            members=(
                AgentTeamMember(
                    agent=AgentRevisionRef(member.agent_id, member.revision),
                    role="researcher",
                ),
            ),
            leader_agent_id=member.agent_id,
        ),
        owner_ref=owner,
    )

    target_repository = InMemoryAgentRepository()
    target = AgentService(target_repository)
    agent_item = _item(
        RegistryItemType.AGENT,
        item_id=member.agent_id,
        version="1.0.0",
    )
    await AgentMarketplaceKindHandler(target).install(
        agent_item,
        _portable_agent_artifact(source_repository, member.agent_id),
    )

    team_item = _item(
        RegistryItemType.AGENT_TEAM,
        item_id=team.team_id,
        version="1.0.0",
    )
    handler = AgentTeamMarketplaceKindHandler(target)
    installed = await handler.install(
        team_item,
        _portable_team_artifact(source_repository, team.team_id),
    )

    assert installed.team_id == team.team_id
    assert installed.profile.members[0].agent.agent_id == member.agent_id
    assert handler.describe(team_item)["member_count"] == 1
    assert target_repository.list_agent_runs() == ()

    retried_team = await handler.install(
        team_item,
        _portable_team_artifact(source_repository, team.team_id),
    )
    assert retried_team.revision == 1
    assert len(target_repository.list_team_revisions(team.team_id)) == 1

    await handler.uninstall(team_item)
    await handler.uninstall(team_item)
    with pytest.raises(ContractError) as missing:
        target.get_team_revision(team.team_id)
    assert missing.value.code is ErrorCode.NOT_FOUND


async def test_agent_and_team_full_marketplace_flow_uses_canonical_owner(tmp_path) -> None:
    owner = OwnerRef(type="user", id="marketplace-agent-team-e2e-owner")
    source_repository = InMemoryAgentRepository()
    source = AgentService(source_repository)
    member = source.create_agent(_agent_profile("Research Agent"), owner_ref=owner)
    team = source.create_team(
        AgentTeamProfile(
            name="Research Team",
            members=(
                AgentTeamMember(
                    agent=AgentRevisionRef(member.agent_id, member.revision),
                    role="researcher",
                ),
            ),
            leader_agent_id=member.agent_id,
        ),
        owner_ref=owner,
    )

    agent_item = replace(
        _item(
            RegistryItemType.AGENT,
            item_id=member.agent_id,
            version="1.0.0",
        ),
        name="Research Agent",
        description="Portable canonical Research Agent definition.",
    )
    team_item = replace(
        _item(
            RegistryItemType.AGENT_TEAM,
            item_id=team.team_id,
            version="1.0.0",
        ),
        name="Research Team",
        description="Portable canonical Research Agent Team definition.",
        dependencies=(
            RegistryDependency(
                agent_item.item_id,
                item_kind=RegistryItemType.AGENT,
            ),
        ),
    )
    artifacts = {
        (agent_item.item_id, agent_item.version): _portable_agent_artifact(
            source_repository,
            member.agent_id,
        ),
        (team_item.item_id, team_item.version): _portable_team_artifact(
            source_repository,
            team.team_id,
        ),
    }
    target_repository = InMemoryAgentRepository()
    target = AgentService(target_repository)
    service = DistributionService(
        LocalRegistryProvider((agent_item, team_item), artifacts),
        installations=JsonRegistryInstallationStore(
            tmp_path / "agent-team-marketplace-installations.json"
        ),
        kind_handlers=MarketplaceKindHandlerRegistry(
            (
                AgentMarketplaceKindHandler(target),
                AgentTeamMarketplaceKindHandler(target),
            )
        ),
    )
    context = ValidationContext("0.0.1")

    discovered = service.search(
        RegistryQuery(
            text="Research",
            item_types=frozenset({RegistryItemType.AGENT, RegistryItemType.AGENT_TEAM}),
        )
    )
    assert {candidate.item_id for candidate in discovered} == {
        agent_item.item_id,
        team_item.item_id,
    }

    agent_preview = service.preview(agent_item.item_id, agent_item.version, context)
    assert agent_preview.activation_allowed is True
    installed_agent = await service.activate(agent_preview, context, authorized=True)
    assert installed_agent.agent_id == member.agent_id
    assert installed_agent.revision == member.revision
    assert service.installed(agent_item.item_id) is not None
    assert target.get_agent_revision(member.agent_id).profile.name == "Research Agent"
    assert target_repository.list_agent_runs() == ()

    team_preview = service.preview(team_item.item_id, team_item.version, context)
    assert team_preview.activation_allowed is True
    assert team_preview.decision.dependencies[0].item_kind == RegistryItemType.AGENT.value
    installed_team = await service.activate(team_preview, context, authorized=True)
    assert installed_team.team_id == team.team_id
    assert installed_team.profile.members[0].agent.agent_id == member.agent_id
    assert service.installed(team_item.item_id) is not None
    assert target.get_team_revision(team.team_id).profile.name == "Research Team"
    assert target_repository.list_agent_runs() == ()

    await service.uninstall(team_item.item_id, authorized=True)
    await service.uninstall(agent_item.item_id, authorized=True)
    assert service.installed(team_item.item_id) is None
    assert service.installed(agent_item.item_id) is None
    with pytest.raises(ContractError) as missing_team:
        target.get_team_revision(team.team_id)
    assert missing_team.value.code is ErrorCode.NOT_FOUND
    with pytest.raises(ContractError) as missing_agent:
        target.get_agent_revision(member.agent_id)
    assert missing_agent.value.code is ErrorCode.NOT_FOUND
    assert target_repository.list_agent_runs() == ()


async def test_agent_team_handler_fails_without_member_and_leaves_no_partial_team() -> None:
    owner = OwnerRef(type="user", id="marketplace-team-failure-owner")
    source_repository = InMemoryAgentRepository()
    source = AgentService(source_repository)
    member = source.create_agent(_agent_profile("Missing Team Researcher"), owner_ref=owner)
    team = source.create_team(
        AgentTeamProfile(
            name="Unresolved Research Team",
            members=(
                AgentTeamMember(
                    agent=AgentRevisionRef(member.agent_id, member.revision),
                    role="researcher",
                ),
            ),
            leader_agent_id=member.agent_id,
        ),
        owner_ref=owner,
    )
    target_repository = InMemoryAgentRepository()
    target = AgentService(target_repository)
    team_item = _item(
        RegistryItemType.AGENT_TEAM,
        item_id=team.team_id,
        version="1.0.0",
    )
    handler = AgentTeamMarketplaceKindHandler(target)

    with pytest.raises(ContractError) as missing_member:
        await handler.install(
            team_item,
            _portable_team_artifact(source_repository, team.team_id),
        )

    assert missing_member.value.code is ErrorCode.NOT_FOUND
    with pytest.raises(ContractError) as missing_team:
        target.get_team_revision(team.team_id)
    assert missing_team.value.code is ErrorCode.NOT_FOUND
    assert target_repository.list_agent_runs() == ()


class _HermesPluginRuntime:
    def __init__(self, manifest, hermes: HermesOrchestrator) -> None:
        self.manifest = manifest
        self.hermes = hermes
        self.stopped = False

    async def initialize(self, context: PluginContext) -> tuple[ExtensionRegistration, ...]:
        assert context.configuration == {}
        return (ExtensionRegistration(spec=self.manifest.extensions[0], instance=self.hermes),)

    async def health(self) -> PluginHealthReport:
        return PluginHealthReport(PluginHealth.HEALTHY)

    async def shutdown(self) -> None:
        self.stopped = True


async def test_hermes_marketplace_kind_uses_plugin_lifecycle_and_orchestrator_registry() -> None:
    base = reference_manifest()
    manifest = replace(
        base,
        plugin_id="hermes.orchestrator-plugin",
        name="Hermes",
        description="Replaceable Hermes orchestrator adapter.",
        extensions=(
            replace(
                base.extensions[0],
                extension_id="orchestrator.hermes",
                extension_type=ExtensionType.ORCHESTRATOR,
                metadata={
                    "agent_support": True,
                    "team_support": True,
                    "planning": True,
                    "replanning": True,
                    "cancellation": True,
                    "reconciliation": True,
                },
            ),
        ),
        capabilities=(),
        requested_permissions=frozenset(),
        configuration_schema={"type": "object", "additionalProperties": False},
    )
    reference = ReferenceOrchestrator()
    orchestrators = OrchestratorRegistry({reference.descriptor.provider_id: reference})
    plugin_registry = PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={ExtensionType.ORCHESTRATOR: frozenset({"1.0"})},
        binders={ExtensionType.ORCHESTRATOR: OrchestratorRegistryBinder(orchestrators)},
    )
    handler = PluginExtensionMarketplaceKindHandler(
        kind=RegistryItemType.ORCHESTRATOR,
        extension_type=ExtensionType.ORCHESTRATOR,
        installer=PluginRegistryArtifactInstaller(plugin_registry),
        registry=plugin_registry,
    )
    item = _item(
        RegistryItemType.ORCHESTRATOR,
        item_id=manifest.plugin_id,
        version=manifest.plugin_version,
        license_name=manifest.provenance.license,
        manifest=True,
    )

    installed = await handler.install(item, _plugin_artifact(manifest))
    assert installed.state.value == "installed"
    assert HERMES_ADAPTER_ID not in orchestrators.orchestrator_ids

    plugin_registry.configure(manifest.plugin_id, {})
    hermes = HermesOrchestrator(
        HermesAdapterConfig(enabled=True),
        secret_resolver=lambda _: None,
    )
    runtime = _HermesPluginRuntime(manifest, hermes)
    enabled = await plugin_registry.enable(manifest.plugin_id, runtime)

    assert enabled.state.value == "enabled"
    assert orchestrators.select(OrchestratorSelection(HERMES_ADAPTER_ID)) is hermes
    assert reference.descriptor.provider_id in orchestrators.orchestrator_ids

    await plugin_registry.disable(manifest.plugin_id)
    assert HERMES_ADAPTER_ID not in orchestrators.orchestrator_ids
    assert reference.descriptor.provider_id in orchestrators.orchestrator_ids
    assert runtime.stopped is True


class _SingleExtensionRuntime:
    def __init__(self, manifest, instance: object) -> None:
        self.manifest = manifest
        self.instance = instance

    async def initialize(self, context: PluginContext) -> tuple[ExtensionRegistration, ...]:
        assert context.configuration == {}
        return (ExtensionRegistration(spec=self.manifest.extensions[0], instance=self.instance),)

    async def health(self) -> PluginHealthReport:
        return PluginHealthReport(PluginHealth.HEALTHY)

    async def shutdown(self) -> None:
        return None


async def test_hermes_full_marketplace_flow_preserves_replaceable_orchestrator_owner(
    tmp_path,
) -> None:
    base = reference_manifest()
    manifest = replace(
        base,
        plugin_id="hermes.orchestrator-plugin",
        name="Hermes",
        description="Replaceable Hermes orchestrator adapter.",
        extensions=(
            replace(
                base.extensions[0],
                extension_id="orchestrator.hermes",
                extension_type=ExtensionType.ORCHESTRATOR,
                metadata={
                    "agent_support": True,
                    "team_support": True,
                    "planning": True,
                    "replanning": True,
                    "cancellation": True,
                    "reconciliation": True,
                },
            ),
        ),
        capabilities=(),
        requested_permissions=frozenset(),
        configuration_schema={"type": "object", "additionalProperties": False},
    )
    reference = ReferenceOrchestrator()
    orchestrators = OrchestratorRegistry({reference.descriptor.provider_id: reference})
    plugin_registry = PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={ExtensionType.ORCHESTRATOR: frozenset({"1.0"})},
        binders={ExtensionType.ORCHESTRATOR: OrchestratorRegistryBinder(orchestrators)},
    )
    handler = PluginExtensionMarketplaceKindHandler(
        kind=RegistryItemType.ORCHESTRATOR,
        extension_type=ExtensionType.ORCHESTRATOR,
        installer=PluginRegistryArtifactInstaller(plugin_registry),
        registry=plugin_registry,
    )
    item = _item(
        RegistryItemType.ORCHESTRATOR,
        item_id=manifest.plugin_id,
        version=manifest.plugin_version,
        license_name=manifest.provenance.license,
        manifest=True,
    )
    artifact = _plugin_artifact(manifest)
    service = DistributionService(
        LocalRegistryProvider(
            (item,),
            {(item.item_id, item.version): artifact},
        ),
        installations=JsonRegistryInstallationStore(
            tmp_path / "hermes-marketplace-installations.json"
        ),
        kind_handlers=MarketplaceKindHandlerRegistry((handler,)),
    )
    context = ValidationContext("0.0.1")

    discovered = service.search(
        RegistryQuery(
            text="Hermes",
            item_types=frozenset({RegistryItemType.ORCHESTRATOR}),
        )
    )
    assert discovered == (item,)
    requirements = service.inspect_requirements(item)
    assert requirements is not None
    assert requirements["required_extension_type"] == "orchestrator"
    details = service.describe(item.item_id)
    assert details["extension_type"] == "orchestrator"
    extension_metadata = details["extension_metadata"]
    assert isinstance(extension_metadata, dict)
    hermes_metadata = extension_metadata["orchestrator.hermes"]
    assert isinstance(hermes_metadata, dict)
    assert hermes_metadata["planning"] is True
    assert hermes_metadata["team_support"] is True
    assert hermes_metadata["reconciliation"] is True

    preview = service.preview(item.item_id, item.version, context)
    assert preview.activation_allowed is True
    installed = await service.activate(preview, context, authorized=True)
    assert installed.state.value == "installed"
    assert service.installed(item.item_id) is not None
    assert HERMES_ADAPTER_ID not in orchestrators.orchestrator_ids
    assert (
        orchestrators.select(OrchestratorSelection(reference.descriptor.provider_id)) is reference
    )

    plugin_registry.configure(manifest.plugin_id, {})
    hermes = HermesOrchestrator(
        HermesAdapterConfig(enabled=True),
        secret_resolver=lambda _: None,
    )
    runtime = _HermesPluginRuntime(manifest, hermes)
    await plugin_registry.enable(manifest.plugin_id, runtime)

    assert orchestrators.select(OrchestratorSelection(HERMES_ADAPTER_ID)) is hermes
    assert (
        orchestrators.select(OrchestratorSelection(reference.descriptor.provider_id)) is reference
    )

    await plugin_registry.disable(manifest.plugin_id)
    assert HERMES_ADAPTER_ID not in orchestrators.orchestrator_ids
    assert (
        orchestrators.select(OrchestratorSelection(reference.descriptor.provider_id)) is reference
    )

    await service.uninstall(item.item_id, authorized=True)
    assert service.installed(item.item_id) is None
    with pytest.raises(ContractError) as removed:
        plugin_registry.get(manifest.plugin_id)
    assert removed.value.code is ErrorCode.NOT_FOUND


async def test_model_provider_marketplace_install_does_not_create_configured_model() -> None:
    base = reference_manifest()
    manifest = replace(
        base,
        plugin_id="reference.model-provider-plugin",
        name="Reference model provider",
        description="Model provider implementation package.",
        extensions=(
            replace(
                base.extensions[0],
                extension_id="model-provider.reference",
                extension_type=ExtensionType.MODEL_PROVIDER,
            ),
        ),
        capabilities=(),
        requested_permissions=frozenset(),
        configuration_schema={"type": "object", "additionalProperties": False},
    )
    models = ModelRegistry()
    plugin_registry = PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={ExtensionType.MODEL_PROVIDER: frozenset({"1.0"})},
        binders={ExtensionType.MODEL_PROVIDER: ModelProviderRegistryBinder(models)},
    )
    handler = PluginExtensionMarketplaceKindHandler(
        kind=RegistryItemType.MODEL_PROVIDER,
        extension_type=ExtensionType.MODEL_PROVIDER,
        installer=PluginRegistryArtifactInstaller(plugin_registry),
        registry=plugin_registry,
    )
    item = _item(
        RegistryItemType.MODEL_PROVIDER,
        item_id=manifest.plugin_id,
        version=manifest.plugin_version,
        license_name=manifest.provenance.license,
        manifest=True,
    )

    await handler.install(item, _plugin_artifact(manifest))
    assert models.list_providers() == ()
    assert models.list_models() == ()

    plugin_registry.configure(item.item_id, {})
    provider = FakeModelProvider()
    await plugin_registry.enable(
        item.item_id,
        _SingleExtensionRuntime(manifest, provider),
    )

    assert models.get_provider(provider.descriptor.provider_id) is provider
    assert models.list_models() == ()

    await plugin_registry.disable(item.item_id)
    assert models.list_providers() == ()
    assert models.list_models() == ()


async def test_model_provider_marketplace_rejects_plaintext_package_credentials() -> None:
    base = reference_manifest()
    manifest = replace(
        base,
        plugin_id="reference.credential-safe-model-provider",
        extensions=(
            replace(
                base.extensions[0],
                extension_id="model-provider.credential-safe",
                extension_type=ExtensionType.MODEL_PROVIDER,
            ),
        ),
        capabilities=(),
        requested_permissions=frozenset(),
        configuration_schema={
            "type": "object",
            "properties": {
                "api_key": {"type": "string"},
                "credential_ref": {"type": "object"},
            },
            "additionalProperties": False,
        },
    )
    plugin_registry = PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={ExtensionType.MODEL_PROVIDER: frozenset({"1.0"})},
    )
    handler = PluginExtensionMarketplaceKindHandler(
        kind=RegistryItemType.MODEL_PROVIDER,
        extension_type=ExtensionType.MODEL_PROVIDER,
        installer=PluginRegistryArtifactInstaller(plugin_registry),
        registry=plugin_registry,
    )
    item = _item(
        RegistryItemType.MODEL_PROVIDER,
        item_id=manifest.plugin_id,
        version=manifest.plugin_version,
        license_name=manifest.provenance.license,
        manifest=True,
    )
    secret_manifest = replace(
        manifest,
        extensions=(
            replace(
                manifest.extensions[0],
                metadata={"api_key": "plaintext-provider-token"},
            ),
        ),
    )

    with pytest.raises(ContractError) as embedded_secret:
        await handler.install(item, _plugin_artifact(secret_manifest))

    assert embedded_secret.value.code is ErrorCode.INVALID_CONFIGURATION
    assert plugin_registry.list_plugins() == ()

    schema_secret_manifest = replace(
        manifest,
        configuration_schema={
            "type": "object",
            "properties": {
                "api_key": {
                    "type": "string",
                    "default": "plaintext-provider-token",
                }
            },
            "additionalProperties": False,
        },
    )
    with pytest.raises(ContractError) as embedded_schema_secret:
        await handler.install(item, _plugin_artifact(schema_secret_manifest))

    assert embedded_schema_secret.value.code is ErrorCode.INVALID_CONFIGURATION
    assert plugin_registry.list_plugins() == ()

    schema_example_secret = replace(
        manifest,
        configuration_schema={
            "type": "object",
            "properties": {
                "api_key": {
                    "type": "string",
                    "example": "plaintext-provider-token",
                }
            },
            "additionalProperties": False,
        },
    )
    with pytest.raises(ContractError) as embedded_schema_example:
        await handler.install(item, _plugin_artifact(schema_example_secret))

    assert embedded_schema_example.value.code is ErrorCode.INVALID_CONFIGURATION
    assert plugin_registry.list_plugins() == ()

    installed = await handler.install(item, _plugin_artifact(manifest))
    assert installed.plugin_id == item.item_id


async def test_model_provider_secret_package_is_blocked_during_marketplace_preview(
    tmp_path,
) -> None:
    base = reference_manifest()
    manifest = replace(
        base,
        plugin_id="reference.preview-secret-model-provider",
        extensions=(
            replace(
                base.extensions[0],
                extension_id="model-provider.preview-secret",
                extension_type=ExtensionType.MODEL_PROVIDER,
                metadata={"api_key": "plaintext-provider-token"},
            ),
        ),
        capabilities=(),
        requested_permissions=frozenset(),
        configuration_schema={"type": "object", "additionalProperties": False},
    )
    plugin_registry = PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={ExtensionType.MODEL_PROVIDER: frozenset({"1.0"})},
    )
    handler = PluginExtensionMarketplaceKindHandler(
        kind=RegistryItemType.MODEL_PROVIDER,
        extension_type=ExtensionType.MODEL_PROVIDER,
        installer=PluginRegistryArtifactInstaller(plugin_registry),
        registry=plugin_registry,
    )
    item = _item(
        RegistryItemType.MODEL_PROVIDER,
        item_id=manifest.plugin_id,
        version=manifest.plugin_version,
        license_name=manifest.provenance.license,
        manifest=True,
    )
    artifact = _plugin_artifact(manifest)
    service = DistributionService(
        LocalRegistryProvider(
            (item,),
            {(item.item_id, item.version): artifact},
        ),
        installations=JsonRegistryInstallationStore(
            tmp_path / "model-provider-secret-preview.json"
        ),
        kind_handlers=MarketplaceKindHandlerRegistry((handler,)),
    )

    preview = service.preview(
        item.item_id,
        item.version,
        ValidationContext("0.0.1"),
    )

    assert preview.activation_allowed is False
    assert any(
        finding.code == "owner_candidate_invalid" and "credentials" in finding.message.lower()
        for finding in preview.findings
    )
    assert plugin_registry.list_plugins() == ()


async def test_executor_marketplace_install_activates_only_through_plugin_owner(tmp_path) -> None:
    base = reference_manifest()
    manifest = replace(
        base,
        plugin_id="reference.executor-plugin",
        name="Reference executor package",
        description="Executor implementation package.",
        extensions=(
            replace(
                base.extensions[0],
                extension_id="executor.reference",
                extension_type=ExtensionType.EXECUTOR,
            ),
        ),
        capabilities=(),
        requested_permissions=frozenset(),
        configuration_schema={"type": "object", "additionalProperties": False},
    )
    executors = ExecutorRegistry()
    plugin_registry = PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={ExtensionType.EXECUTOR: frozenset({"1.0"})},
        binders={ExtensionType.EXECUTOR: ExecutorRegistryBinder(executors)},
    )
    handler = PluginExtensionMarketplaceKindHandler(
        kind=RegistryItemType.EXECUTOR,
        extension_type=ExtensionType.EXECUTOR,
        installer=PluginRegistryArtifactInstaller(plugin_registry),
        registry=plugin_registry,
    )
    item = _item(
        RegistryItemType.EXECUTOR,
        item_id=manifest.plugin_id,
        version=manifest.plugin_version,
        license_name=manifest.provenance.license,
        manifest=True,
    )

    await handler.install(item, _plugin_artifact(manifest))
    assert executors.executor_ids == ()

    plugin_registry.configure(item.item_id, {})
    executor = ReferenceExecutor(tmp_path)
    await plugin_registry.enable(
        item.item_id,
        _SingleExtensionRuntime(manifest, executor),
    )
    assert executor.descriptor.executor_id in executors.executor_ids

    await plugin_registry.disable(item.item_id)
    assert executors.executor_ids == ()


async def test_plugin_activation_keeps_legacy_route_and_owner_handler_adds_status_remove(
    tmp_path,
) -> None:
    plugin_registry = PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={ExtensionType.CAPABILITY_PROVIDER: frozenset({"1.0"})},
    )
    installer = PluginRegistryArtifactInstaller(plugin_registry)
    router = _PluginRouter(installer)
    handler = PluginMarketplaceKindHandler(installer, plugin_registry)
    manifest = reference_manifest()
    item = _item(
        RegistryItemType.PLUGIN,
        item_id=manifest.plugin_id,
        version=manifest.plugin_version,
        license_name=manifest.provenance.license,
    )
    provider = LocalRegistryProvider(
        (item,),
        {(item.item_id, item.version): _plugin_artifact(manifest)},
    )
    installations = JsonRegistryInstallationStore(tmp_path / "plugin-installations.json")
    service = DistributionService(
        provider,
        router,
        installations=installations,
        kind_handlers=MarketplaceKindHandlerRegistry((handler,)),
    )
    context = ValidationContext("0.0.1")

    preview = service.preview(item.item_id, item.version, context)
    assert preview.route.value == "plugin"

    installed = await service.activate(preview, context, authorized=True)
    assert router.plugin_install_calls == 1
    assert installed.plugin_id == item.item_id
    assert (await service.status(item.item_id)).plugin_id == item.item_id
    assert service.describe(item.item_id)["owner_domain"] == "plugins"

    await service.uninstall(item.item_id, authorized=True)
    assert installations.get(item.item_id) is None
    await handler.uninstall(item)
    with pytest.raises(ContractError) as missing:
        plugin_registry.get(item.item_id)
    assert missing.value.code is ErrorCode.NOT_FOUND


async def test_plugin_installer_rejects_same_version_manifest_drift() -> None:
    plugin_registry = PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={ExtensionType.CAPABILITY_PROVIDER: frozenset({"1.0"})},
    )
    installer = PluginRegistryArtifactInstaller(plugin_registry)
    manifest = reference_manifest()
    item = _item(
        RegistryItemType.PLUGIN,
        item_id=manifest.plugin_id,
        version=manifest.plugin_version,
        license_name=manifest.provenance.license,
    )

    await installer.install_verified_plugin(item, _plugin_artifact(manifest))

    with pytest.raises(ContractError) as drift:
        await installer.install_verified_plugin(
            item,
            _plugin_artifact(
                replace(
                    manifest,
                    description="Conflicting manifest for the same published version",
                )
            ),
        )

    assert drift.value.code is ErrorCode.CONFLICT


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
        manifest=True,
    )
    second = _item(
        RegistryItemType.TOOL,
        item_id=second_manifest.plugin_id,
        version=second_manifest.plugin_version,
        license_name=second_manifest.provenance.license,
        manifest=True,
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
    await handler.uninstall(second)
    with pytest.raises(ContractError) as missing:
        plugin_registry.get(second.item_id)
    assert missing.value.code is ErrorCode.NOT_FOUND


async def test_manifest_backed_tool_reconciles_into_plugin_owner_after_restart(
    tmp_path,
) -> None:
    manifest = reference_manifest()
    item = _item(
        RegistryItemType.TOOL,
        item_id=manifest.plugin_id,
        version=manifest.plugin_version,
        license_name=manifest.provenance.license,
        manifest=True,
    )
    artifact = _plugin_artifact(manifest)
    provider = LocalRegistryProvider(
        (item,),
        {(item.item_id, item.version): artifact},
    )
    installations = JsonRegistryInstallationStore(tmp_path / "restart-installations.json")
    installations.record(
        item,
        provider_id=provider.provider_id,
        artifact_sha256=hashlib.sha256(artifact).hexdigest(),
    )
    plugin_registry = PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={ExtensionType.CAPABILITY_PROVIDER: frozenset({"1.0"})},
    )

    restored = await reconcile_registry_plugins(provider, installations, plugin_registry)

    assert restored == (item.item_id,)
    assert plugin_registry.get(item.item_id).plugin_version == item.version


async def test_semantic_orchestrator_installation_reconciles_only_into_plugin_owner_after_restart(
    tmp_path,
) -> None:
    base = reference_manifest()
    manifest = replace(
        base,
        plugin_id="reference.orchestrator-plugin",
        extensions=(
            replace(
                base.extensions[0],
                extension_id="orchestrator.reference",
                extension_type=ExtensionType.ORCHESTRATOR,
            ),
        ),
        capabilities=(),
        requested_permissions=frozenset(),
    )
    item = _item(
        RegistryItemType.ORCHESTRATOR,
        item_id=manifest.plugin_id,
        version=manifest.plugin_version,
        license_name=manifest.provenance.license,
        manifest=True,
    )
    artifact = _plugin_artifact(manifest)
    provider = LocalRegistryProvider(
        (item,),
        {(item.item_id, item.version): artifact},
    )
    installations = JsonRegistryInstallationStore(tmp_path / "semantic-orchestrator-restart.json")
    installations.record(
        item,
        provider_id=provider.provider_id,
        artifact_sha256=hashlib.sha256(artifact).hexdigest(),
    )
    orchestrators = OrchestratorRegistry({"reference": ReferenceOrchestrator()})
    plugin_registry = PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={ExtensionType.ORCHESTRATOR: frozenset({"1.0"})},
        binders={ExtensionType.ORCHESTRATOR: OrchestratorRegistryBinder(orchestrators)},
    )

    restored = await reconcile_registry_plugins(provider, installations, plugin_registry)

    assert restored == (item.item_id,)
    assert plugin_registry.get(item.item_id).state.value == "installed"
    assert "orchestrator.reference" not in orchestrators.orchestrator_ids
    assert "reference" in orchestrators.orchestrator_ids


async def test_model_provider_reconciliation_rejects_secret_bearing_package(
    tmp_path,
) -> None:
    base = reference_manifest()
    manifest = replace(
        base,
        plugin_id="reference.secret-model-provider",
        extensions=(
            replace(
                base.extensions[0],
                extension_id="model-provider.secret",
                extension_type=ExtensionType.MODEL_PROVIDER,
                metadata={"token": "plaintext-provider-token"},
            ),
        ),
        capabilities=(),
        requested_permissions=frozenset(),
    )
    item = _item(
        RegistryItemType.MODEL_PROVIDER,
        item_id=manifest.plugin_id,
        version=manifest.plugin_version,
        license_name=manifest.provenance.license,
        manifest=True,
    )
    artifact = _plugin_artifact(manifest)
    provider = LocalRegistryProvider(
        (item,),
        {(item.item_id, item.version): artifact},
    )
    installations = JsonRegistryInstallationStore(tmp_path / "secret-model-provider-restart.json")
    installations.record(
        item,
        provider_id=provider.provider_id,
        artifact_sha256=hashlib.sha256(artifact).hexdigest(),
    )
    plugin_registry = PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={ExtensionType.MODEL_PROVIDER: frozenset({"1.0"})},
    )

    with pytest.raises(
        RegistryPluginReconciliationError,
        match="no longer validates",
    ):
        await reconcile_registry_plugins(provider, installations, plugin_registry)

    with pytest.raises(ContractError) as missing:
        plugin_registry.get(item.item_id)
    assert missing.value.code is ErrorCode.NOT_FOUND


async def test_reconciliation_rejects_same_version_with_different_owner_manifest(
    tmp_path,
) -> None:
    manifest = reference_manifest()
    item = _item(
        RegistryItemType.TOOL,
        item_id=manifest.plugin_id,
        version=manifest.plugin_version,
        license_name=manifest.provenance.license,
        manifest=True,
    )
    artifact = _plugin_artifact(manifest)
    provider = LocalRegistryProvider(
        (item,),
        {(item.item_id, item.version): artifact},
    )
    installations = JsonRegistryInstallationStore(tmp_path / "owner-drift-installations.json")
    installations.record(
        item,
        provider_id=provider.provider_id,
        artifact_sha256=hashlib.sha256(artifact).hexdigest(),
    )
    plugin_registry = PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={ExtensionType.CAPABILITY_PROVIDER: frozenset({"1.0"})},
    )
    plugin_registry.install(
        replace(manifest, description="Conflicting pre-existing owner manifest"),
        install_source="manual:test",
    )

    with pytest.raises(
        RegistryPluginReconciliationError,
        match="expected version with a different manifest",
    ):
        await reconcile_registry_plugins(provider, installations, plugin_registry)


async def test_portable_tool_installation_is_not_reconciled_as_plugin_package(tmp_path) -> None:
    item = _item(
        RegistryItemType.TOOL,
        item_id="example.portable-tool",
        version="1.0.0",
    )
    artifact = b"portable-tool"
    provider = LocalRegistryProvider(
        (item,),
        {(item.item_id, item.version): artifact},
    )
    installations = JsonRegistryInstallationStore(tmp_path / "portable-installations.json")
    installations.record(
        item,
        provider_id=provider.provider_id,
        artifact_sha256=hashlib.sha256(artifact).hexdigest(),
    )
    plugin_registry = PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={ExtensionType.CAPABILITY_PROVIDER: frozenset({"1.0"})},
    )

    restored = await reconcile_registry_plugins(provider, installations, plugin_registry)

    assert restored == ()
    with pytest.raises(ContractError) as missing:
        plugin_registry.get(item.item_id)
    assert missing.value.code is ErrorCode.NOT_FOUND


class _FailingConnectorDefinitionRepository(InMemoryConnectorRepository):
    async def save_definition(self, definition: ConnectorDefinition) -> ConnectorDefinition:
        del definition
        raise RuntimeError("definition persistence failed")


async def test_connector_binder_rolls_back_partial_runtime_registration() -> None:
    registry = ConnectorRegistry()
    service = ConnectorService(_FailingConnectorDefinitionRepository(), registry)
    binder = ConnectorRegistryBinder(service)
    provider = ReferenceConnectorProvider()
    registration = ExtensionRegistration(
        spec=PluginExtensionSpec(
            extension_id="reference.connector_provider",
            extension_type=ExtensionType.CONNECTOR_PROVIDER,
            interface_version="1.0",
            entrypoint="tests:connector",
        ),
        instance=provider,
    )

    with pytest.raises(RuntimeError, match="definition persistence failed"):
        await binder.register(registration)

    assert registry.definitions() == ()


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
        manifest=True,
    )

    snapshot = await handler.install(item, _plugin_artifact(connector_manifest))

    assert snapshot.plugin_id == connector_manifest.plugin_id
    assert (await handler.status(item)).plugin_id == connector_manifest.plugin_id
    assert handler.describe(item)["extension_type"] == "connector_provider"
    await handler.uninstall(item)
    await handler.uninstall(item)

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
        manifest=True,
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
        skill_id=new_id("skill"),
        revision=1,
        profile=SkillProfile(
            name="Marketplace review",
            purpose_categories=("review",),
            content=SkillContent(content="review v1"),
            source=SkillSource(
                source_url="https://example.invalid/upstream-skill",
                source_revision="source-rev-1",
                license="MIT",
            ),
            trust_status=SkillTrustStatus.DISCOVERED,
            enabled=False,
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


async def test_skill_handler_keeps_marketplace_sources_distinct() -> None:
    skills = SkillService(InMemorySkillRepository())
    handler = SkillMarketplaceKindHandler(skills)
    official = replace(
        _item(
            RegistryItemType.SKILL,
            item_id="catalog.shared-skill",
            version="1.0.0",
        ),
        source_registry="official",
    )
    private = replace(official, source_registry="private")
    revision = SkillRevision(
        skill_id=new_id("skill"),
        revision=1,
        profile=SkillProfile(
            name="Shared Marketplace skill",
            purpose_categories=("review",),
            content=SkillContent(content="shared skill"),
            source=SkillSource(
                source_url="https://example.invalid/shared-skill",
                source_revision="source-rev-1",
                license="MIT",
            ),
            trust_status=SkillTrustStatus.DISCOVERED,
            enabled=False,
        ),
        owner_ref=OwnerRef(type="user", id="marketplace-skill-owner"),
    )
    artifact = json.dumps(skill_revision_to_json(revision), sort_keys=True).encode()

    await handler.install(official, artifact)

    assert (await handler.status(official)).skill_id == revision.skill_id
    with pytest.raises(ContractError) as missing:
        await handler.status(private)
    assert missing.value.code is ErrorCode.NOT_FOUND


async def test_application_handler_delegates_to_canonical_owner_and_rejects_fake_update(
    tmp_path,
) -> None:
    repository = InMemoryApplicationRepository()
    runtimes = ApplicationRuntimeRegistry((_ApplicationRuntime(),))
    lifecycle = ApplicationLifecycleService(repository, runtimes)
    handler = ApplicationMarketplaceKindHandler(lifecycle, repository, runtimes)

    first_manifest = ApplicationManifest(
        application_id=new_id("application"),
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

    resolved_first = service.get(first.item_id, first.version)
    retry = await handler.install(
        resolved_first,
        provider.fetch_artifact(first.item_id, first.version),
    )
    assert retry.instance_id == installed.instance_id
    assert len(repository.list_applications()) == 1
    assert len(repository.list_instances(application_id=first_manifest.application_id)) == 1

    update_preview = service.preview(second.item_id, second.version, context)
    assert update_preview.activation_allowed is False
    assert any(finding.code == "unsupported_operation" for finding in update_preview.findings)
    with pytest.raises(ContractError) as unsupported:
        await service.activate(update_preview, context, authorized=True)
    assert unsupported.value.code is ErrorCode.UNSUPPORTED_CAPABILITY

    removed = await service.uninstall(first.item_id, authorized=True)
    assert removed.observed_state is ApplicationObservedState.REMOVED
    assert installations.get(first.item_id) is None
    assert await handler.uninstall(resolved_first) is None

    reinstalled = await service.activate(
        service.preview(first.item_id, first.version, context),
        context,
        authorized=True,
    )
    assert reinstalled.instance_id != removed.instance_id
    assert (await service.status(first.item_id)).instance_id == reinstalled.instance_id


async def test_portable_tool_lifecycle_does_not_dispatch_to_plugin_owner(tmp_path) -> None:
    item = _item(
        RegistryItemType.TOOL,
        item_id="example.portable-owner-boundary",
        version="1.0.0",
    )
    artifact = b"portable-tool"
    provider = LocalRegistryProvider(
        (item,),
        {(item.item_id, item.version): artifact},
    )
    installations = JsonRegistryInstallationStore(tmp_path / "portable-owner-boundary.json")
    installations.record(
        item,
        provider_id=provider.provider_id,
        artifact_sha256=hashlib.sha256(artifact).hexdigest(),
    )
    plugin_registry = PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={ExtensionType.CAPABILITY_PROVIDER: frozenset({"1.0"})},
    )
    handler = PluginExtensionMarketplaceKindHandler(
        kind=RegistryItemType.TOOL,
        extension_type=ExtensionType.CAPABILITY_PROVIDER,
        installer=PluginRegistryArtifactInstaller(plugin_registry),
        registry=plugin_registry,
    )
    service = DistributionService(
        provider,
        installations=installations,
        kind_handlers=MarketplaceKindHandlerRegistry((handler,)),
    )

    assert item.route.value == "portable_import"
    preview = service.preview_uninstall(item.item_id)
    assert preview.activation_allowed is False
    assert any(finding.code == "unsupported_operation" for finding in preview.findings)

    with pytest.raises(ContractError) as status_error:
        await service.status(item.item_id)
    assert status_error.value.code is ErrorCode.UNSUPPORTED_CAPABILITY

    with pytest.raises(ContractError) as describe_error:
        service.describe(item.item_id)
    assert describe_error.value.code is ErrorCode.UNSUPPORTED_CAPABILITY

    with pytest.raises(ContractError) as uninstall_error:
        await service.uninstall(item.item_id, authorized=True)
    assert uninstall_error.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
    assert installations.get(item.item_id) is not None


async def test_application_handler_preserves_marketplace_source_identity() -> None:
    repository = InMemoryApplicationRepository()
    runtimes = ApplicationRuntimeRegistry((_ApplicationRuntime(),))
    handler = ApplicationMarketplaceKindHandler(
        ApplicationLifecycleService(repository, runtimes),
        repository,
        runtimes,
    )
    first_manifest = ApplicationManifest(
        application_id=new_id("application"),
        name="Official Marketplace application",
        version="1.0.0",
        description="Official catalog fixture",
        services=(
            ApplicationService(
                service_id="app",
                runtime=ApplicationServiceRuntime.PROCESS,
                process=("python", "-m", "official"),
            ),
        ),
    )
    second_manifest = replace(
        first_manifest,
        application_id=new_id("application"),
        name="Private Marketplace application",
        description="Private catalog fixture",
        services=(
            ApplicationService(
                service_id="app",
                runtime=ApplicationServiceRuntime.PROCESS,
                process=("python", "-m", "private"),
            ),
        ),
    )
    base_item = _item(
        RegistryItemType.APPLICATION,
        item_id="catalog.shared-application",
        version="1.0.0",
        manifest=True,
    )
    official_item = replace(base_item, source_registry="official")
    private_item = replace(base_item, source_registry="private")

    first = await handler.install(
        official_item,
        json.dumps(
            application_manifest_to_document(first_manifest),
            sort_keys=True,
        ).encode(),
    )
    removed = await handler.uninstall(official_item)
    assert removed.observed_state is ApplicationObservedState.REMOVED

    second = await handler.install(
        private_item,
        json.dumps(
            application_manifest_to_document(second_manifest),
            sort_keys=True,
        ).encode(),
    )

    assert second.application_id == second_manifest.application_id
    assert second.instance_id != first.instance_id
    assert (await handler.status(private_item)).instance_id == second.instance_id
    assert handler.describe(private_item)["application_id"] == second_manifest.application_id
