"""Default distribution composition for the single-node operator entrypoint.

Concrete adapter selection belongs at this outer composition boundary, never in canonical core
contracts. The OpenAI-compatible onboarding bridge and optional Registry are installed here without
choosing a model, endpoint, provider account, hosted marketplace or paid service on the operator's
behalf.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.configuration import LocalSecretProvider
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.deployment import (
    SingleNodeDeployment,
    load_application_release_gate_policy,
)
from ai_multi_agent_platform.deployment.config import SingleNodeConfig
from ai_multi_agent_platform.deployment.product_composition import (
    build_product_single_node_deployment,
)
from ai_multi_agent_platform.deployment.server import main as run_server
from ai_multi_agent_platform.distribution import (
    CanonicalDistributionRouter,
    DistributionService,
    FilesystemRegistryProvider,
    HmacSha256SignatureVerifier,
    JsonRegistryInstallationStore,
    MarketplaceKindHandlerRegistry,
    PlatformRegistryValidationContextResolver,
    PluginRegistryArtifactInstaller,
    RegistryItemType,
    load_hmac_signature_keys,
    reconcile_registry_plugins,
    register_distribution_control_plane,
)
from ai_multi_agent_platform.distribution.control_plane import RegistryCommandHandlers
from ai_multi_agent_platform.onboarding import (
    JsonSetupProfileStore,
    JsonSetupSessionStore,
    OnboardingComponentSetupService,
    SingleNodeComponentDiscoverySource,
    register_component_setup_control_plane,
    register_setup_lifecycle_control_plane,
)
from ai_multi_agent_platform.onboarding.setup_registry_planning import (
    DependencyAwareBrowserFirstSetupService,
)
from ai_multi_agent_platform.plugins import (
    CapabilityRegistryBinder,
    ConnectorRegistryBinder,
    DiscoveredPlugin,
    ExecutorRegistryBinder,
    ExtensionType,
    ModelProviderRegistryBinder,
    OrchestratorRegistryBinder,
    PluginCatalog,
    PluginManifest,
    PluginPermission,
    PluginRegistry,
    StaticPluginSource,
)
from ai_multi_agent_platform.repositories import RepositoryCapabilityProvider
from ai_multi_agent_platform.repositories.intelligence import (
    WorkspaceAwareRepositoryIntelligenceProvider,
)
from ai_multi_agent_platform.repositories.intelligence.wiring import (
    AuthorizedRepositorySnapshotLoader,
    AuthorizedRunWorkspaceSnapshotLoader,
)

from .application_runtime import ApplicationRuntimeComposition, compose_application_runtime
from .hermes_plugin import HermesOrchestratorPlugin, hermes_plugin_manifest
from .marketplace_owner_handlers import (
    AgentMarketplaceKindHandler,
    AgentTeamMarketplaceKindHandler,
    ApplicationMarketplaceKindHandler,
    PluginExtensionMarketplaceKindHandler,
    PluginMarketplaceKindHandler,
    SkillMarketplaceKindHandler,
)
from .onboarding_openai_compatible import OpenAICompatibleOnboardingAdapter
from .setup_registry import DistributionSetupRegistryPort


def _repository_actor_ref(context: OperationContext) -> str:
    """Resolve the canonical owner identity used by repository authorization.

    Repository capabilities must never invent a privileged fallback identity when an invocation
    is missing canonical ownership metadata. Agent/runtime invocations therefore enter the
    RepositoryService policy boundary as the OperationContext owner or fail closed.
    """

    if context.owner_id is None:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "repository capability invocation requires an owner identity",
        )
    return context.owner_id


def build_default_single_node_deployment(
    config: SingleNodeConfig,
    *,
    enable_distributed_execution: bool = False,
) -> SingleNodeDeployment:
    """Build the shipped single-node profile with optional distributed execution."""

    secrets = LocalSecretProvider()
    release_gate_policy = (
        None
        if config.application_release_gate_policy is None
        else load_application_release_gate_policy(config.application_release_gate_policy)
    )
    deployment = build_product_single_node_deployment(
        config,
        onboarding_model_adapters=(OpenAICompatibleOnboardingAdapter(secret_provider=secrets),),
        secret_provider=secrets,
        enable_distributed_execution=enable_distributed_execution,
        application_release_gate_policy=release_gate_policy,
    )
    applications = compose_application_runtime(
        config,
        deployment,
        secret_provider=secrets,
    )
    component_setup = OnboardingComponentSetupService(
        SingleNodeComponentDiscoverySource(
            deployment.models,
            distributed_runtime=deployment.distributed_runtime,
        ),
        JsonSetupProfileStore(deployment.config.database_dir / "component-setup-profiles.json"),
    )
    register_component_setup_control_plane(deployment.control_plane, component_setup)
    asyncio.run(
        deployment.capabilities.register_provider(
            RepositoryCapabilityProvider(
                deployment.repositories,
                actor_resolver=_repository_actor_ref,
            )
        )
    )
    asyncio.run(
        deployment.capabilities.register_provider(
            WorkspaceAwareRepositoryIntelligenceProvider(
                AuthorizedRepositorySnapshotLoader(
                    deployment.repositories,
                    actor_resolver=_repository_actor_ref,
                ),
                AuthorizedRunWorkspaceSnapshotLoader(
                    deployment.run_workspace_bindings,
                    deployment.workspaces,
                    deployment.files,
                    deployment.repository_workspace_execution,
                    deployment.approval_gate,
                    actor_resolver=_repository_actor_ref,
                ),
            )
        )
    )
    distribution, registry_commands = _configure_registry(config, deployment, applications)
    setup_registry = (
        None
        if distribution is None
        else DistributionSetupRegistryPort(distribution, registry_commands)
    )
    setup_lifecycle = DependencyAwareBrowserFirstSetupService(
        component_setup,
        deployment.onboarding,
        JsonSetupSessionStore(deployment.config.database_dir / "setup-sessions.json"),
        registry=setup_registry,
    )
    register_setup_lifecycle_control_plane(deployment.control_plane, setup_lifecycle)
    return deployment


async def _single_node_plugin_permissions(
    context: RequestContext,
    manifest: PluginManifest,
) -> frozenset[PluginPermission]:
    """Grant permissions only to exact platform-composed plugin manifests."""

    del context
    if manifest == hermes_plugin_manifest():
        return manifest.requested_permissions
    return frozenset()


def _registry_plugin_runtime(deployment: SingleNodeDeployment) -> PluginRegistry:
    plugin_registry = deployment.control_plane.plugin_registry
    if plugin_registry is not None:
        return plugin_registry

    plugin_registry = PluginRegistry(
        platform_version=__version__,
        supported_interfaces={
            ExtensionType.ORCHESTRATOR: frozenset({"1.0"}),
            ExtensionType.EXECUTOR: frozenset({"1.0"}),
            ExtensionType.MODEL_PROVIDER: frozenset({"1.0"}),
            ExtensionType.CAPABILITY_PROVIDER: frozenset({"1.0"}),
            ExtensionType.CONNECTOR_PROVIDER: frozenset({"1.0"}),
        },
        binders={
            ExtensionType.ORCHESTRATOR: OrchestratorRegistryBinder(
                deployment.orchestrators,
                agent_mappers=deployment.agent_orchestrator_mappers,
            ),
            ExtensionType.EXECUTOR: ExecutorRegistryBinder(deployment.executors),
            ExtensionType.MODEL_PROVIDER: ModelProviderRegistryBinder(deployment.models),
            ExtensionType.CAPABILITY_PROVIDER: CapabilityRegistryBinder(deployment.capabilities),
            ExtensionType.CONNECTOR_PROVIDER: ConnectorRegistryBinder(deployment.connectors),
        },
    )
    hermes_manifest = hermes_plugin_manifest()
    plugin_catalog = PluginCatalog(
        StaticPluginSource(
            DiscoveredPlugin(
                manifest=hermes_manifest,
                runtime_factory=HermesOrchestratorPlugin,
                install_source="bundled:hermes-adapter",
            )
        )
    )
    deployment.control_plane.attach_plugin_runtime(
        plugin_registry,
        plugin_catalog=plugin_catalog,
        plugin_permission_resolver=_single_node_plugin_permissions,
    )
    return plugin_registry


def _marketplace_kind_handlers(
    deployment: SingleNodeDeployment,
    applications: ApplicationRuntimeComposition,
    *,
    plugin_registry: PluginRegistry,
    plugin_installer: PluginRegistryArtifactInstaller,
) -> MarketplaceKindHandlerRegistry:
    return MarketplaceKindHandlerRegistry(
        (
            AgentMarketplaceKindHandler(deployment.agents),
            AgentTeamMarketplaceKindHandler(deployment.agents),
            PluginMarketplaceKindHandler(plugin_installer, plugin_registry),
            PluginExtensionMarketplaceKindHandler(
                kind=RegistryItemType.ORCHESTRATOR,
                extension_type=ExtensionType.ORCHESTRATOR,
                installer=plugin_installer,
                registry=plugin_registry,
            ),
            PluginExtensionMarketplaceKindHandler(
                kind=RegistryItemType.EXECUTOR,
                extension_type=ExtensionType.EXECUTOR,
                installer=plugin_installer,
                registry=plugin_registry,
            ),
            PluginExtensionMarketplaceKindHandler(
                kind=RegistryItemType.MODEL_PROVIDER,
                extension_type=ExtensionType.MODEL_PROVIDER,
                installer=plugin_installer,
                registry=plugin_registry,
            ),
            PluginExtensionMarketplaceKindHandler(
                kind=RegistryItemType.CAPABILITY_PROVIDER,
                extension_type=ExtensionType.CAPABILITY_PROVIDER,
                installer=plugin_installer,
                registry=plugin_registry,
            ),
            PluginExtensionMarketplaceKindHandler(
                kind=RegistryItemType.TOOL,
                extension_type=ExtensionType.CAPABILITY_PROVIDER,
                installer=plugin_installer,
                registry=plugin_registry,
            ),
            SkillMarketplaceKindHandler(deployment.context.skills),
            PluginExtensionMarketplaceKindHandler(
                kind=RegistryItemType.CONNECTOR,
                extension_type=ExtensionType.CONNECTOR_PROVIDER,
                installer=plugin_installer,
                registry=plugin_registry,
            ),
            ApplicationMarketplaceKindHandler(
                applications.lifecycle,
                applications.repository,
                applications.runtimes,
            ),
        )
    )


def _registry_validation_resolver(
    deployment: SingleNodeDeployment,
    installations: JsonRegistryInstallationStore,
    plugin_registry: PluginRegistry,
    applications: ApplicationRuntimeComposition,
) -> PlatformRegistryValidationContextResolver:
    return PlatformRegistryValidationContextResolver(
        platform_version=__version__,
        installations=installations,
        capabilities=lambda: (
            capability.capability_id
            for capability in deployment.capabilities.inventory_capabilities(
                include_unavailable=False
            )
        ),
        plugins=lambda: (snapshot.plugin_id for snapshot in plugin_registry.list_plugins()),
        connectors=lambda: (
            definition.id for definition in deployment.connector_registry.definitions()
        ),
        models=lambda: (model.config_id for model in deployment.models.list_models(enabled=True)),
        runtimes=applications.runtimes.list_runtime_ids,
        grantable_permissions=lambda context: (
            action.value
            for action in deployment.authorization.globally_grantable_actions(
                context.actor.principal_ref,
                actor_type=context.actor.actor_type,
            )
        ),
    )


def _configure_registry(
    config: SingleNodeConfig,
    deployment: SingleNodeDeployment,
    applications: ApplicationRuntimeComposition,
) -> tuple[DistributionService | None, RegistryCommandHandlers | None]:
    """Attach the optional Registry only when an operator configures a local catalog."""

    if config.registry_catalog is None:
        return None, None

    provider = FilesystemRegistryProvider(config.registry_catalog)
    installations = JsonRegistryInstallationStore(
        deployment.config.database_dir / "registry-installations.json"
    )
    plugin_registry = _registry_plugin_runtime(deployment)
    signature_verifier = (
        HmacSha256SignatureVerifier(load_hmac_signature_keys(config.registry_signature_keys))
        if config.registry_signature_keys is not None
        else None
    )
    asyncio.run(
        reconcile_registry_plugins(
            provider,
            installations,
            plugin_registry,
            signature_verifier=signature_verifier,
        )
    )

    plugin_installer = PluginRegistryArtifactInstaller(plugin_registry)
    portability = deployment.control_plane.portability_workflow
    if portability is None:
        raise RuntimeError(
            "single-node Registry composition requires the canonical portability workflow"
        )
    distribution = DistributionService(
        provider,
        CanonicalDistributionRouter(
            plugin_installer=plugin_installer,
            portability=portability,
        ),
        installations=installations,
        signature_verifier=signature_verifier,
        kind_handlers=_marketplace_kind_handlers(
            deployment,
            applications,
            plugin_registry=plugin_registry,
            plugin_installer=plugin_installer,
        ),
    )
    validation = _registry_validation_resolver(
        deployment,
        installations,
        plugin_registry,
        applications,
    )
    register_distribution_control_plane(
        deployment.control_plane,
        distribution,
        validation_context_resolver=validation,
    )
    return distribution, RegistryCommandHandlers(distribution, validation)


def main(argv: Sequence[str] | None = None) -> int:
    return run_server(argv, deployment_builder=build_default_single_node_deployment)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
