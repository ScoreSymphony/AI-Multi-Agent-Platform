from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from ai_multi_agent_platform.adapters.marketplace_owner_handlers import (
    ApplicationMarketplaceKindHandler,
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
    SqliteApplicationRepository,
)
from ai_multi_agent_platform.applications.serialization import application_manifest_to_document
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext, paginate
from ai_multi_agent_platform.distribution import (
    DistributionRoute,
    DistributionService,
    JsonRegistryInstallationStore,
    LocalRegistryProvider,
    MarketplaceKindDescriptor,
    MarketplaceKindHandlerRegistry,
    RegistryDependency,
    RegistryItem,
    RegistryItemType,
    RegistryManifestReference,
    RegistryMaturity,
    RegistryResourceService,
    RegistrySource,
    TrustStatus,
    ValidationContext,
    marketplace_kind_registry_with_builtins,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
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

pytestmark = pytest.mark.asyncio


class _ApplicationRuntime:
    descriptor = ApplicationRuntimeDescriptor(
        runtime_id="acceptance.process",
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


class _FutureOwner:
    kind = "notebook_extension"

    def __init__(self, path: Path) -> None:
        self.path = path

    def inspect_requirements(self, item: RegistryItem) -> dict[str, object]:
        return {"owner_domain": "acceptance_future", "kind": item.kind}

    async def install(self, item: RegistryItem, artifact: bytes) -> object:
        return self._persist(item, artifact, "install")

    async def update(self, item: RegistryItem, artifact: bytes) -> object:
        return self._persist(item, artifact, "update")

    async def uninstall(self, item: RegistryItem) -> object:
        del item
        if self.path.exists():
            self.path.unlink()
        return None

    async def status(self, item: RegistryItem) -> object:
        del item
        return self._load()

    def describe(self, item: RegistryItem) -> dict[str, object]:
        del item
        return self._load()

    def _persist(self, item: RegistryItem, artifact: bytes, operation: str) -> dict[str, object]:
        payload = {
            "item_id": item.item_id,
            "version": item.version,
            "source_registry": item.source_registry,
            "artifact": artifact.decode("utf-8"),
            "operation": operation,
        }
        self.path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        return payload

    def _load(self) -> dict[str, object]:
        if not self.path.exists():
            raise ContractError(ErrorCode.NOT_FOUND, "future owner state not found")
        value = json.loads(self.path.read_text(encoding="utf-8"))
        assert isinstance(value, dict)
        return value


def _source(item_id: str, version: str) -> RegistrySource:
    return RegistrySource(
        "https://example.invalid/acceptance",
        f"{item_id}@{version}",
        revision=f"rev-{version}",
    )


def _item(
    kind: RegistryItemType | str,
    item_id: str,
    version: str,
    *,
    manifest: bool = False,
    maturity: RegistryMaturity = RegistryMaturity.STABLE,
) -> RegistryItem:
    return RegistryItem(
        item_id=item_id,
        item_type=kind,
        name=f"Acceptance {item_id}",
        description=f"Cross-kind acceptance fixture for {item_id}",
        version=version,
        publisher="acceptance",
        source=_source(item_id, version),
        license="MIT",
        provenance="acceptance-release",
        trust_status=TrustStatus.REVIEWED,
        maturity=maturity,
        manifest=(
            RegistryManifestReference(
                kind=kind,
                reference=f"manifests/{item_id}-{version}.json",
                schema_version="1",
            )
            if manifest
            else None
        ),
    )


def _skill_artifacts() -> tuple[RegistryItem, RegistryItem, bytes, bytes, str]:
    first = _item(RegistryItemType.SKILL, "acceptance.skill", "1.0.0")
    second = replace(first, version="1.1.0", source=_source(first.item_id, "1.1.0"))
    owner = OwnerRef(type="user", id="marketplace-acceptance-owner")
    skill_id = new_id("skill")
    first_revision = SkillRevision(
        skill_id=skill_id,
        revision=1,
        profile=SkillProfile(
            name="Acceptance skill",
            purpose_categories=("review",),
            content=SkillContent(content="acceptance skill v1"),
            source=SkillSource(
                source_url="https://example.invalid/acceptance-skill",
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
            content=SkillContent(content="acceptance skill v2"),
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


def _application_artifact() -> tuple[RegistryItem, bytes]:
    item = _item(
        RegistryItemType.APPLICATION,
        "acceptance.application",
        "1.0.0",
        manifest=True,
    )
    manifest = ApplicationManifest(
        application_id=new_id("application"),
        name="Acceptance Application",
        version=item.version,
        description="Cross-kind Marketplace Application",
        services=(
            ApplicationService(
                service_id="app",
                runtime=ApplicationServiceRuntime.PROCESS,
                process=("python", "-m", "acceptance_app"),
            ),
        ),
    )
    return (
        item,
        json.dumps(application_manifest_to_document(manifest), sort_keys=True).encode(),
    )


async def test_cross_kind_marketplace_owner_restart_and_uninstall_acceptance(
    tmp_path: Path,
) -> None:
    skill_v1, skill_v2, skill_artifact_v1, skill_artifact_v2, skill_id = _skill_artifacts()
    skill_v3 = replace(
        skill_v2,
        version="1.2.0",
        source=_source(skill_v2.item_id, "1.2.0"),
    )
    application, application_artifact = _application_artifact()
    future = replace(
        _item(
            "notebook_extension",
            "acceptance.notebook",
            "1.0.0",
            manifest=True,
            maturity=RegistryMaturity.BETA,
        ),
        dependencies=(
            RegistryDependency(
                item_id=skill_v2.item_id,
                optional=True,
            ),
        ),
    )
    tool = _item(RegistryItemType.TOOL, "acceptance.tool", "1.0.0")
    plugin = _item(RegistryItemType.PLUGIN, "acceptance.plugin", "1.0.0")
    connector = _item(RegistryItemType.CONNECTOR, "acceptance.connector", "1.0.0")
    template = _item(RegistryItemType.TEMPLATE, "acceptance.template", "1.0.0")
    workflow = _item(RegistryItemType.WORKFLOW, "acceptance.workflow", "1.0.0")
    artifacts = {
        (skill_v1.item_id, skill_v1.version): skill_artifact_v1,
        (skill_v2.item_id, skill_v2.version): skill_artifact_v2,
        (application.item_id, application.version): application_artifact,
        (future.item_id, future.version): b"future-owner-artifact",
        (tool.item_id, tool.version): b"tool-artifact",
        (plugin.item_id, plugin.version): b"plugin-artifact",
        (connector.item_id, connector.version): b"connector-artifact",
        (template.item_id, template.version): b"portable-template",
        (workflow.item_id, workflow.version): b"portable-workflow",
    }
    provider = LocalRegistryProvider(
        (
            skill_v1,
            skill_v2,
            skill_v3,
            application,
            future,
            tool,
            plugin,
            connector,
            template,
            workflow,
        ),
        artifacts,
        provider_id="acceptance",
    )
    installation_path = tmp_path / "marketplace-installations.json"
    skill_path = tmp_path / "skills.json"
    application_path = tmp_path / "applications.sqlite3"
    future_path = tmp_path / "future-owner.json"

    kinds = marketplace_kind_registry_with_builtins()
    kinds.register(
        MarketplaceKindDescriptor(
            "notebook_extension",
            "Notebook Extension",
            DistributionRoute.KIND_HANDLER,
        )
    )
    skills = SkillService(JsonSkillRepository(skill_path))
    application_repository = SqliteApplicationRepository(application_path)
    application_runtimes = ApplicationRuntimeRegistry((_ApplicationRuntime(),))
    application_lifecycle = ApplicationLifecycleService(
        application_repository,
        application_runtimes,
    )
    future_owner = _FutureOwner(future_path)
    handlers = MarketplaceKindHandlerRegistry(
        (
            SkillMarketplaceKindHandler(skills),
            ApplicationMarketplaceKindHandler(
                application_lifecycle,
                application_repository,
                application_runtimes,
            ),
            future_owner,
        )
    )
    distribution = DistributionService(
        provider,
        installations=JsonRegistryInstallationStore(installation_path),
        kind_handlers=handlers,
        kind_registry=kinds,
    )
    context = ValidationContext("0.0.1")

    resource_service = RegistryResourceService(distribution)
    request = RequestContext("acceptance-request", "acceptance-correlation")
    resources = await resource_service.list_resources(
        request,
        PageQuery(search="acceptance", sort="kind"),
    )
    assert {resource["kind"] for resource in resources} == {
        "application",
        "connector",
        "notebook_extension",
        "plugin",
        "skill",
        "template",
        "tool",
        "workflow",
    }
    assert (
        next(resource for resource in resources if resource["kind"] == "notebook_extension")[
            "maturity"
        ]
        == "beta"
    )

    filtered = await resource_service.list_resources(
        request,
        PageQuery(filters={"kind": "skill,connector"}, sort="kind"),
    )
    assert {resource["kind"] for resource in filtered} == {"skill", "connector"}

    first_page = paginate(resources, PageQuery(limit=4, sort="kind"))
    assert len(first_page["items"]) == 4  # type: ignore[arg-type]
    assert first_page["next_cursor"] is not None

    await distribution.activate(
        distribution.preview(skill_v1.item_id, skill_v1.version, context),
        context,
        authorized=True,
    )
    updated_skill = await distribution.activate(
        distribution.preview(skill_v2.item_id, skill_v2.version, context),
        context,
        authorized=True,
    )
    assert updated_skill.revision == 2

    installed_application = await distribution.activate(
        distribution.preview(application.item_id, application.version, context),
        context,
        authorized=True,
    )
    assert installed_application.application_version == application.version

    await distribution.activate(
        distribution.preview(future.item_id, future.version, context),
        context,
        authorized=True,
    )

    restarted_skills = SkillService(JsonSkillRepository(skill_path))
    restarted_application_repository = SqliteApplicationRepository(application_path)
    restarted_application_runtimes = ApplicationRuntimeRegistry((_ApplicationRuntime(),))
    restarted_application_lifecycle = ApplicationLifecycleService(
        restarted_application_repository,
        restarted_application_runtimes,
    )
    restarted_future_owner = _FutureOwner(future_path)
    restarted_distribution = DistributionService(
        provider,
        installations=JsonRegistryInstallationStore(installation_path),
        kind_handlers=MarketplaceKindHandlerRegistry(
            (
                SkillMarketplaceKindHandler(restarted_skills),
                ApplicationMarketplaceKindHandler(
                    restarted_application_lifecycle,
                    restarted_application_repository,
                    restarted_application_runtimes,
                ),
                restarted_future_owner,
            )
        ),
        kind_registry=kinds,
    )

    assert (await restarted_distribution.status(skill_v2.item_id)).revision == 2
    assert (
        await restarted_distribution.status(application.item_id)
    ).application_version == application.version
    assert (await restarted_distribution.status(future.item_id))["operation"] == "install"

    restarted_skill_installation = restarted_distribution.installed(skill_v2.item_id)
    assert restarted_skill_installation is not None
    assert restarted_skill_installation.current.version == "1.1.0"
    assert restarted_skill_installation.current.source_registry == "acceptance"
    assert restarted_skill_installation.current.provenance == skill_v2.provenance
    assert restarted_skill_installation.current.dependencies == skill_v2.dependencies
    assert tuple(
        candidate.version
        for candidate in restarted_distribution.available_updates(skill_v2.item_id)
    ) == ("1.2.0",)

    restarted_application_installation = restarted_distribution.installed(application.item_id)
    assert restarted_application_installation is not None
    assert restarted_application_installation.current.version == application.version
    assert restarted_application_installation.current.source_registry == "acceptance"
    assert restarted_application_installation.current.provenance == application.provenance
    assert restarted_application_installation.current.dependencies == application.dependencies

    restarted_future_installation = restarted_distribution.installed(future.item_id)
    assert restarted_future_installation is not None
    assert restarted_future_installation.current.version == future.version
    assert restarted_future_installation.current.source_registry == "acceptance"
    assert restarted_future_installation.current.provenance == future.provenance
    assert restarted_future_installation.current.dependencies == future.dependencies
    assert restarted_future_installation.current.item_type.value == "notebook_extension"

    await restarted_distribution.uninstall(skill_v2.item_id, authorized=True)
    await restarted_distribution.uninstall(application.item_id, authorized=True)
    await restarted_distribution.uninstall(future.item_id, authorized=True)

    final_store = JsonRegistryInstallationStore(installation_path)
    assert final_store.get(skill_v2.item_id) is None
    assert final_store.get(application.item_id) is None
    assert final_store.get(future.item_id) is None
    with pytest.raises(ContractError) as missing_skill:
        SkillService(JsonSkillRepository(skill_path)).get_skill_revision(skill_id)
    assert missing_skill.value.code is ErrorCode.NOT_FOUND
    assert not future_path.exists()

    final_application_repository = SqliteApplicationRepository(application_path)
    final_application_runtimes = ApplicationRuntimeRegistry((_ApplicationRuntime(),))
    final_handler = ApplicationMarketplaceKindHandler(
        ApplicationLifecycleService(final_application_repository, final_application_runtimes),
        final_application_repository,
        final_application_runtimes,
    )
    with pytest.raises(ContractError) as removed_application:
        await final_handler.status(replace(application, source_registry="acceptance"))
    assert removed_application.value.code is ErrorCode.NOT_FOUND
