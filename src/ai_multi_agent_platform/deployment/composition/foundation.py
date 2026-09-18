"""Foundation builders for the supported single-node composition."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.configuration import SecretProvider
from ai_multi_agent_platform.control_plane.sqlite_scope import SqliteScopeStore
from ai_multi_agent_platform.data import LocalFileProvider
from ai_multi_agent_platform.kernel import SqliteKernelRepository
from ai_multi_agent_platform.observability import (
    InMemoryExporter,
    ObservedAuthorizationProvider,
    Telemetry,
)
from ai_multi_agent_platform.repositories import (
    RepositoryRegistry,
    SqliteRepositoryBindingCatalog,
    SqliteRepositoryProvenanceStore,
    restore_managed_local_repositories,
)
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    AuthorizedSecretProvider,
    ControlPlaneAuthorizationBridge,
    LocalAuthenticationService,
    LocalPrincipalPolicy,
    ResourceType,
    SqliteApprovalService,
    SqliteAuthorizationAuditSink,
)
from ai_multi_agent_platform.security.sqlite_authentication import SqliteAuthenticationStore
from ai_multi_agent_platform.security.sqlite_authorization import SqliteLocalAuthorizationProvider
from ai_multi_agent_platform.workspaces import SqliteRunWorkspaceBindingRepository
from ai_multi_agent_platform.workspaces.compensation import CompensatingSqliteWorkspaceProvider

from ..config import SingleNodeConfig
from ..persistence_health import SingleNodePersistenceHealthProvider

_EVALUATION_PROJECT_KEY = "evaluation-system-project-v1"
_EVALUATION_OWNER_ID = "evaluation-single-node"
_EVALUATION_PRINCIPAL = f"service:{_EVALUATION_OWNER_ID}"
_PLATFORM_SERVICE_PRINCIPAL = "service:platform"


@dataclass(frozen=True, slots=True)
class StorageBundle:
    """Canonical durable storage primitives used by later builders."""

    kernel_repository: SqliteKernelRepository
    scopes: SqliteScopeStore
    files: LocalFileProvider
    workspaces: CompensatingSqliteWorkspaceProvider
    run_workspace_bindings: SqliteRunWorkspaceBindingRepository
    repository_registry: RepositoryRegistry
    repository_catalog: SqliteRepositoryBindingCatalog
    repository_provenance: SqliteRepositoryProvenanceStore
    evaluation_project_id: str
    persistence_health: SingleNodePersistenceHealthProvider


@dataclass(frozen=True, slots=True)
class ObservabilityBundle:
    """Observability foundation shared by all runtime layers."""

    exporter: InMemoryExporter
    telemetry: Telemetry


@dataclass(frozen=True, slots=True)
class SecurityBundle:
    """Authentication, authorization, approval and protected-secret authorities."""

    authentication: LocalAuthenticationService
    authorization: SqliteLocalAuthorizationProvider
    authorization_audit: SqliteAuthorizationAuditSink
    approval_gate: AuthorizationGate
    control_plane_authorization: ControlPlaneAuthorizationBridge
    secrets: SecretProvider | None


@dataclass(frozen=True, slots=True)
class SingleNodeFoundationBundle:
    """Storage, observability and security built before runtime services."""

    storage: StorageBundle
    observability: ObservabilityBundle
    security: SecurityBundle


def build_storage(config: SingleNodeConfig) -> StorageBundle:
    """Construct storage without depending on runtime or Control Plane services."""

    config.prepare_directories()
    database_dir = config.database_dir
    kernel_repository = SqliteKernelRepository(database_dir / "kernel.sqlite3")
    scopes = SqliteScopeStore(database_dir / "scopes.sqlite3")
    files = LocalFileProvider(config.files_dir, database_dir / "files.sqlite3")
    workspaces = CompensatingSqliteWorkspaceProvider(
        config.workspaces_dir,
        files,
        database_dir / "workspaces.sqlite3",
    )
    run_workspace_bindings = SqliteRunWorkspaceBindingRepository(
        database_dir / "run-workspace-bindings.sqlite3"
    )
    repository_catalog = SqliteRepositoryBindingCatalog(
        database_dir / "repository-bindings.sqlite3"
    )
    repository_registry = RepositoryRegistry()
    restore_managed_local_repositories(repository_catalog, repository_registry)
    repository_provenance = SqliteRepositoryProvenanceStore(
        database_dir / "repository-provenance.sqlite3"
    )
    persistence_health = SingleNodePersistenceHealthProvider(config)
    evaluation_project = scopes.create_project(
        key=_EVALUATION_PROJECT_KEY,
        name="Platform Evaluation",
        owner_type="service",
        owner_id=_EVALUATION_OWNER_ID,
    )
    return StorageBundle(
        kernel_repository=kernel_repository,
        scopes=scopes,
        files=files,
        workspaces=workspaces,
        run_workspace_bindings=run_workspace_bindings,
        repository_registry=repository_registry,
        repository_catalog=repository_catalog,
        repository_provenance=repository_provenance,
        evaluation_project_id=evaluation_project.id,
        persistence_health=persistence_health,
    )


def build_observability(
    exporter: InMemoryExporter | None = None,
) -> ObservabilityBundle:
    """Construct the default in-memory observability foundation or use an override."""

    effective_exporter = exporter or InMemoryExporter()
    return ObservabilityBundle(
        exporter=effective_exporter,
        telemetry=Telemetry(effective_exporter),
    )


def build_security(
    config: SingleNodeConfig,
    observability: ObservabilityBundle,
    *,
    secret_provider: SecretProvider | None = None,
) -> SecurityBundle:
    """Construct security authorities before runtime/execution layers."""

    database_dir = config.database_dir
    authentication = LocalAuthenticationService(
        store=SqliteAuthenticationStore(database_dir / "authentication.sqlite3")
    )
    authorization = SqliteLocalAuthorizationProvider(database_dir / "authorization.sqlite3")

    if not authorization.has_policy(_EVALUATION_PRINCIPAL):
        authorization.register(
            LocalPrincipalPolicy(
                principal_ref=_EVALUATION_PRINCIPAL,
                actor_types=frozenset({ActorType.SERVICE}),
                allowed_actions=frozenset(
                    {
                        AuthorizationAction.EXECUTE,
                        AuthorizationAction.READ,
                        AuthorizationAction.VIEW,
                        AuthorizationAction.MODIFY,
                    }
                ),
                resource_types=frozenset(
                    {
                        ResourceType.RUN,
                        ResourceType.TASK,
                        ResourceType.AGENT,
                        ResourceType.FILE,
                        ResourceType.MEMORY,
                    }
                ),
            )
        )
    if not authorization.has_policy(_PLATFORM_SERVICE_PRINCIPAL):
        authorization.register(
            LocalPrincipalPolicy(
                principal_ref=_PLATFORM_SERVICE_PRINCIPAL,
                actor_types=frozenset({ActorType.SERVICE}),
                allowed_actions=frozenset(
                    {
                        AuthorizationAction.READ,
                        AuthorizationAction.MANAGE_CREDENTIALS,
                    }
                ),
                resource_types=frozenset({ResourceType.SECRET_REFERENCE}),
            )
        )

    observed_authorization = ObservedAuthorizationProvider(authorization, observability.telemetry)
    approval_service = SqliteApprovalService(database_dir / "approvals.sqlite3")
    authorization_audit = SqliteAuthorizationAuditSink(database_dir / "authorization-audit.sqlite3")
    approval_gate = AuthorizationGate(
        observed_authorization,
        approvals=approval_service,
        audit_sink=authorization_audit,
    )
    protected_secret_provider: SecretProvider | None = (
        AuthorizedSecretProvider(secret_provider, approval_gate)
        if secret_provider is not None
        else None
    )
    return SecurityBundle(
        authentication=authentication,
        authorization=authorization,
        authorization_audit=authorization_audit,
        approval_gate=approval_gate,
        control_plane_authorization=ControlPlaneAuthorizationBridge(approval_gate),
        secrets=protected_secret_provider,
    )


def build_single_node_foundation(
    config: SingleNodeConfig,
    *,
    secret_provider: SecretProvider | None = None,
    observability_exporter: InMemoryExporter | None = None,
) -> SingleNodeFoundationBundle:
    """Build the explicit pre-runtime foundation for staged composition."""

    storage = build_storage(config)
    observability = build_observability(observability_exporter)
    storage.persistence_health._telemetry = observability.telemetry
    security = build_security(
        config,
        observability,
        secret_provider=secret_provider,
    )
    return SingleNodeFoundationBundle(
        storage=storage,
        observability=observability,
        security=security,
    )
