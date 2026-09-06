"""Durable Connector composition for the normal single-node/server runtime."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, fields
from typing import Any

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.accounting import AccountingService
from ai_multi_agent_platform.configuration import SecretProvider
from ai_multi_agent_platform.connectors import (
    ConnectorRegistry,
    ConnectorService,
    SqliteConnectorRepository,
)
from ai_multi_agent_platform.connectors.control_plane import register_connector_control_plane
from ai_multi_agent_platform.distributed import DistributedRuntime
from ai_multi_agent_platform.models import ModelRoutingProfileRef
from ai_multi_agent_platform.observability import InMemoryExporter
from ai_multi_agent_platform.onboarding import OnboardingModelAdapter
from ai_multi_agent_platform.repositories import RepositoryDiscoveryResolver
from ai_multi_agent_platform.repositories.connector_bootstrap import (
    connector_repository_discovery_resolver,
)
from ai_multi_agent_platform.templates import (
    AgentTemplateExporter,
    AutomationTemplateExporter,
    PlatformTemplateEnvironmentResolver,
    register_template_control_plane,
)

from .config import SingleNodeConfig
from .single_node import (
    SingleNodeDeployment as BaseSingleNodeDeployment,
)
from .single_node import (
    SingleNodeSmokeResult,
)
from .single_node import (
    build_single_node_deployment as _build_base_single_node_deployment,
)


@dataclass(slots=True)
class SingleNodeDeployment(BaseSingleNodeDeployment):
    """Normal single-node deployment with restart-durable canonical Connector state."""

    connector_repository: SqliteConnectorRepository
    connector_registry: ConnectorRegistry
    connectors: ConnectorService


def build_single_node_deployment(
    config: SingleNodeConfig,
    *,
    onboarding_model_adapters: Iterable[OnboardingModelAdapter] = (),
    secret_provider: SecretProvider | None = None,
    accounting_service: AccountingService | None = None,
    observability_exporter: InMemoryExporter | None = None,
    distributed_runtime: DistributedRuntime | None = None,
    enable_distributed_execution: bool = False,
    repository_discovery_resolver: RepositoryDiscoveryResolver | None = None,
) -> SingleNodeDeployment:
    """Build the normal single-node profile with durable Connector source state.

    The lower-level ``deployment.single_node`` composition remains usable by focused tests and
    explicitly minimal/ephemeral profiles. Public deployment/server composition comes through this
    wrapper so Connector Definitions, Connections, external-resource identities and sync
    checkpoints live in ``db/connectors.sqlite3``.
    """

    # Preserve the base deployment's canonical configuration error boundary before the Connector
    # repository touches a path under the data root. This keeps invalid persistence roots from
    # leaking backend-specific OSError subclasses through the public single-node builder.
    config.prepare_directories()
    connector_repository = SqliteConnectorRepository(config.database_dir / "connectors.sqlite3")
    connector_registry = ConnectorRegistry()
    effective_repository_resolver = repository_discovery_resolver or (
        connector_repository_discovery_resolver(connector_repository, connector_registry)
    )

    base = _build_base_single_node_deployment(
        config,
        onboarding_model_adapters=onboarding_model_adapters,
        secret_provider=secret_provider,
        accounting_service=accounting_service,
        observability_exporter=observability_exporter,
        distributed_runtime=distributed_runtime,
        enable_distributed_execution=enable_distributed_execution,
        repository_discovery_resolver=effective_repository_resolver,
    )
    connectors = ConnectorService(
        connector_repository,
        connector_registry,
        authorization_gate=base.approval_gate,
    )
    register_connector_control_plane(base.control_plane, connectors)

    # The public deployment now has an authoritative canonical Connector inventory. Rebind the
    # Template surface to a resolver that includes exactly those ConnectorDefinition IDs instead
    # of leaving connector requirements permanently fail-closed. The callback closes over the
    # live registry so later provider registration/removal is reflected immediately in preview.
    template_environment = PlatformTemplateEnvironmentResolver(
        workspaces=base.workspaces,
        capabilities=lambda: (
            capability.capability_id
            for capability in base.capabilities.inventory_capabilities(include_unavailable=False)
        ),
        capability_versions=lambda: (
            (capability.capability_id, capability.version)
            for capability in base.capabilities.inventory_capabilities(include_unavailable=False)
        ),
        connectors=lambda: (definition.id for definition in connector_registry.definitions()),
        model_policies=lambda: (
            ModelRoutingProfileRef(definition.profile_id, definition.current_revision).canonical_ref
            for definition in base.routing_profile_repository.list_definitions()
            if definition.enabled
        ),
        grantable_permissions=lambda context: (
            action.value
            for action in base.authorization.globally_grantable_actions(
                context.actor.principal_ref,
                actor_type=context.actor.actor_type,
            )
        ),
        platform_version=__version__,
    )
    register_template_control_plane(
        base.control_plane,
        base.templates,
        environment_resolver=template_environment,
        agent_exporter=AgentTemplateExporter(base.agents, base.templates.templates),
        automation_exporter=AutomationTemplateExporter(
            base.control_plane.automation_service,
            base.templates.templates,
        ),
    )

    base_values: dict[str, Any] = {
        field.name: getattr(base, field.name) for field in fields(BaseSingleNodeDeployment)
    }
    return SingleNodeDeployment(
        **base_values,
        connector_repository=connector_repository,
        connector_registry=connector_registry,
        connectors=connectors,
    )


__all__ = [
    "SingleNodeDeployment",
    "SingleNodeSmokeResult",
    "build_single_node_deployment",
]
