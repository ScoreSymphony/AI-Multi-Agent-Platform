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
from ai_multi_agent_platform.deployment import SingleNodeDeployment, build_single_node_deployment
from ai_multi_agent_platform.deployment.config import SingleNodeConfig
from ai_multi_agent_platform.deployment.server import main as run_server
from ai_multi_agent_platform.distribution import (
    CanonicalDistributionRouter,
    DistributionService,
    FilesystemRegistryProvider,
    HmacSha256SignatureVerifier,
    JsonRegistryInstallationStore,
    PlatformRegistryValidationContextResolver,
    PluginRegistryArtifactInstaller,
    load_hmac_signature_keys,
    register_distribution_control_plane,
)
from ai_multi_agent_platform.plugins import (
    CapabilityRegistryBinder,
    ExtensionType,
    PluginRegistry,
)
from ai_multi_agent_platform.repositories import RepositoryCapabilityProvider

from .onboarding_openai_compatible import OpenAICompatibleOnboardingAdapter


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
    """Build the shipped profile with installed bridges and optional #240 execution routing."""

    secrets = LocalSecretProvider()
    deployment = build_single_node_deployment(
        config,
        onboarding_model_adapters=(OpenAICompatibleOnboardingAdapter(secret_provider=secrets),),
        secret_provider=secrets,
        enable_distributed_execution=enable_distributed_execution,
    )
    asyncio.run(
        deployment.capabilities.register_provider(
            RepositoryCapabilityProvider(
                deployment.repositories,
                actor_resolver=_repository_actor_ref,
            )
        )
    )
    _configure_registry(config, deployment)
    return deployment


def _configure_registry(config: SingleNodeConfig, deployment: SingleNodeDeployment) -> None:
    """Attach #81 only when an operator explicitly configures a local Registry catalog."""

    if config.registry_catalog is None:
        return

    provider = FilesystemRegistryProvider(config.registry_catalog)
    installations = JsonRegistryInstallationStore(
        deployment.config.database_dir / "registry-installations.json"
    )
    plugin_registry = PluginRegistry(
        platform_version=__version__,
        supported_interfaces={ExtensionType.CAPABILITY_PROVIDER: frozenset({"1.0"})},
        binders={
            ExtensionType.CAPABILITY_PROVIDER: CapabilityRegistryBinder(deployment.capabilities)
        },
    )
    plugin_installer = PluginRegistryArtifactInstaller(plugin_registry)
    portability = deployment.control_plane.portability_workflow
    if portability is None:
        raise RuntimeError("single-node Registry composition requires the canonical #79 workflow")
    router = CanonicalDistributionRouter(
        plugin_installer=plugin_installer,
        portability=portability,
    )
    signature_verifier = None
    if config.registry_signature_keys is not None:
        signature_verifier = HmacSha256SignatureVerifier(
            load_hmac_signature_keys(config.registry_signature_keys)
        )
    distribution = DistributionService(
        provider,
        router,
        installations=installations,
        signature_verifier=signature_verifier,
    )
    validation = PlatformRegistryValidationContextResolver(
        platform_version=__version__,
        installations=installations,
        capabilities=lambda: (
            capability.capability_id
            for capability in deployment.capabilities.inventory_capabilities(
                include_unavailable=False
            )
        ),
        plugins=lambda: (snapshot.plugin_id for snapshot in plugin_registry.list_plugins()),
        models=lambda: (model.config_id for model in deployment.models.list_models(enabled=True)),
        grantable_permissions=lambda context: (
            action.value
            for action in deployment.authorization.globally_grantable_actions(
                context.actor.principal_ref,
                actor_type=context.actor.actor_type,
            )
        ),
    )
    register_distribution_control_plane(
        deployment.control_plane,
        distribution,
        validation_context_resolver=validation,
    )


def main(argv: Sequence[str] | None = None) -> int:
    return run_server(argv, deployment_builder=build_default_single_node_deployment)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
