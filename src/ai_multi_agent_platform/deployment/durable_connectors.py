"""Durable Connector composition for the normal single-node/server runtime."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, fields
from typing import Any

from ai_multi_agent_platform.accounting import AccountingService
from ai_multi_agent_platform.configuration import SecretProvider
from ai_multi_agent_platform.connectors import (
    ConnectorRegistry,
    ConnectorService,
    SqliteConnectorRepository,
)
from ai_multi_agent_platform.connectors.control_plane import register_connector_control_plane
from ai_multi_agent_platform.distributed import DistributedRuntime
from ai_multi_agent_platform.observability import InMemoryExporter
from ai_multi_agent_platform.onboarding import OnboardingModelAdapter
from ai_multi_agent_platform.repositories import RepositoryDiscoveryResolver
from ai_multi_agent_platform.repositories.connector_bootstrap import (
    connector_repository_discovery_resolver,
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
    repository_discovery_resolver: RepositoryDiscoveryResolver | None = None,
) -> SingleNodeDeployment:
    """Build the normal single-node profile with durable Connector source state.

    The lower-level ``deployment.single_node`` composition remains usable by focused tests and
    explicitly minimal/ephemeral profiles. Public deployment/server composition comes through this
    wrapper so Connector Definitions, Connections, external-resource identities and sync
    checkpoints live in ``db/connectors.sqlite3``.
    """

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
        repository_discovery_resolver=effective_repository_resolver,
    )
    connectors = ConnectorService(
        connector_repository,
        connector_registry,
        authorization_gate=base.approval_gate,
    )
    register_connector_control_plane(base.control_plane, connectors)

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
