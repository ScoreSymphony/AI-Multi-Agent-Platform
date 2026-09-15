"""Connector foundation and governed egress composition for single-node deployment."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.connectors import (
    ConnectorRegistry,
    ConnectorService,
    SqliteConnectorRepository,
)
from ai_multi_agent_platform.connectors.control_plane import register_connector_control_plane
from ai_multi_agent_platform.control_plane.approval_portability_composition import ControlPlane
from ai_multi_agent_platform.models import ModelRuntime
from ai_multi_agent_platform.observability import EgressTelemetryAuditSink, Telemetry
from ai_multi_agent_platform.repositories import RepositoryDiscoveryResolver
from ai_multi_agent_platform.repositories.connector_bootstrap import (
    connector_repository_discovery_resolver,
)
from ai_multi_agent_platform.security import AuthorizationGate, build_durable_egress_runtime
from ai_multi_agent_platform.security.sqlite_authorization import SqliteLocalAuthorizationProvider

from ..config import SingleNodeConfig
from ..egress_bindings import EgressDeploymentBindings


@dataclass(frozen=True, slots=True)
class ConnectorFoundationBundle:
    """Connector state required before repository discovery is composed."""

    repository: SqliteConnectorRepository
    registry: ConnectorRegistry
    repository_discovery_resolver: RepositoryDiscoveryResolver


@dataclass(frozen=True, slots=True)
class EgressConnectorBundle:
    """One durable egress authority and its connector service."""

    egress: EgressDeploymentBindings
    connectors: ConnectorService


def build_connector_foundation(config: SingleNodeConfig) -> ConnectorFoundationBundle:
    """Build durable Connector Definitions/Connections before repository restoration."""

    repository = SqliteConnectorRepository(config.database_dir / "connectors.sqlite3")
    registry = ConnectorRegistry()
    return ConnectorFoundationBundle(
        repository=repository,
        registry=registry,
        repository_discovery_resolver=connector_repository_discovery_resolver(repository, registry),
    )


def build_egress_connectors(
    config: SingleNodeConfig,
    foundation: ConnectorFoundationBundle,
    *,
    authorization: SqliteLocalAuthorizationProvider,
    approval_gate: AuthorizationGate,
    telemetry: Telemetry,
    model_runtime: ModelRuntime,
    control_plane: ControlPlane,
) -> EgressConnectorBundle:
    """Build the single durable egress policy runtime and connector service."""

    egress_runtime = build_durable_egress_runtime(
        config.database_dir / "egress-profiles.json",
        authorization=authorization,
        approval_gate=approval_gate,
        audit_sink=EgressTelemetryAuditSink(telemetry),
    )
    egress = EgressDeploymentBindings(egress_runtime)
    model_runtime.egress_gate = egress.runtime.gate
    connectors = egress.connector_service(
        foundation.repository,
        foundation.registry,
        authorization_gate=approval_gate,
    )
    register_connector_control_plane(control_plane, connectors)
    egress.register_control_plane(control_plane)
    return EgressConnectorBundle(egress=egress, connectors=connectors)
