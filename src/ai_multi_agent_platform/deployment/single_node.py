"""Production-shaped single-node composition and first-run onboarding."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ai_multi_agent_platform.accounting import AccountingService
from ai_multi_agent_platform.agents import (
    AgentOrchestratorMapperRegistry,
    AgentRuntime,
    AgentService,
)
from ai_multi_agent_platform.capabilities import CapabilityRegistry
from ai_multi_agent_platform.capabilities.assignments import CapabilityAssignmentService
from ai_multi_agent_platform.configuration import SecretProvider
from ai_multi_agent_platform.contracts import LifecycleBackend
from ai_multi_agent_platform.control_plane import AuthenticatedControlPlaneHTTP, ControlPlaneASGI
from ai_multi_agent_platform.control_plane.approval_portability_composition import ControlPlane
from ai_multi_agent_platform.control_plane.sqlite_scope import SqliteScopeStore
from ai_multi_agent_platform.conversations import ConversationService
from ai_multi_agent_platform.coordination import (
    DurablePlanStepCoordinator,
    SQLiteCoordinatorRepository,
)
from ai_multi_agent_platform.data import LocalFileProvider
from ai_multi_agent_platform.distributed import DistributedRuntime
from ai_multi_agent_platform.domain import RunStatus, TaskStatus
from ai_multi_agent_platform.evaluation import EvaluationService, SqliteEvaluationRepository
from ai_multi_agent_platform.execution import ExecutorRegistry
from ai_multi_agent_platform.kernel import PlatformKernel, SqliteKernelRepository
from ai_multi_agent_platform.models import (
    JsonModelRoutingProfileRepository,
    ModelRegistry,
    ModelRoutingProfileService,
    ModelRuntime,
)
from ai_multi_agent_platform.observability import (
    AggregatedHealthProvider,
    InMemoryExporter,
    Telemetry,
)
from ai_multi_agent_platform.onboarding import (
    FirstRunTaskService,
    OnboardingModelAdapter,
    OnboardingService,
)
from ai_multi_agent_platform.orchestration import OrchestratorRegistry
from ai_multi_agent_platform.repositories import (
    RepositoryDiscoveryResolver,
    RepositoryEventRuntimeIngress,
    RepositoryManagementService,
    RepositoryRegistry,
    RepositoryRunIntegration,
    RepositoryService,
    RepositoryWorkspaceExecutionCoordinator,
    SqliteRepositoryBindingCatalog,
    SqliteRepositoryProvenanceStore,
)
from ai_multi_agent_platform.research import ResearchService
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationGate,
    LocalAuthenticationService,
    LocalPrincipalPolicy,
    LocalUserAccount,
    SqliteAuthorizationAuditSink,
)
from ai_multi_agent_platform.security.sqlite_authorization import SqliteLocalAuthorizationProvider
from ai_multi_agent_platform.templates import TemplateApplicationService
from ai_multi_agent_platform.verification import (
    CanonicalVerificationRuntime,
    SqliteVerificationCompletionAuthority,
    SqliteVerificationService,
)
from ai_multi_agent_platform.workflows import AuthorizedWorkflowService
from ai_multi_agent_platform.workspaces import SqliteRunWorkspaceBindingRepository
from ai_multi_agent_platform.workspaces.compensation import CompensatingSqliteWorkspaceProvider

from .composition import (
    build_control_plane,
    build_evaluation,
    build_execution,
    build_health,
    build_http,
    build_kernel,
    build_platform_services,
    build_repository_foundation,
    build_repository_runtime,
    build_runtime_services,
    build_verification,
)
from .composition.control_plane import ControlPlaneBundle, HealthBundle, HttpBundle
from .composition.execution import (
    EvaluationBundle,
    ExecutionBundle,
    KernelBundle,
    StartupLifecycleBinding,
    VerificationBundle,
)
from .composition.foundation import (
    SingleNodeFoundationBundle,
    build_single_node_foundation,
)
from .composition.repositories import RepositoryFoundationBundle, RepositoryRuntimeBundle
from .composition.services import (
    ModelRuntimeFactory,
    PlatformServicesBundle,
    RuntimeServicesBundle,
)
from .config import SingleNodeConfig
from .drain import SingleNodeDrainController
from .persistence_health import SingleNodePersistenceHealthProvider

_SMOKE_PROJECT_KEY = "deployment-smoke-project-v1"
_SMOKE_TASK_KEY = "deployment-smoke-task-v1"
_SMOKE_READY_KEY = "deployment-smoke-ready-v1"
_SMOKE_START_KEY = "deployment-smoke-start-v1"
_SMOKE_REFRESH_KEY = "deployment-smoke-refresh-v1"


@dataclass(frozen=True, slots=True)
class SingleNodeSmokeResult:
    """Canonical identifiers and terminal state from the built-in deployment smoke."""

    task_id: str
    run_id: str
    task_status: TaskStatus
    run_status: RunStatus


@dataclass(slots=True)
class SingleNodeDeployment:
    """All long-lived components for one self-hosted single-machine process."""

    config: SingleNodeConfig
    kernel_repository: SqliteKernelRepository
    scopes: SqliteScopeStore
    files: LocalFileProvider
    workspaces: CompensatingSqliteWorkspaceProvider
    run_workspace_bindings: SqliteRunWorkspaceBindingRepository
    repository_registry: RepositoryRegistry
    repository_catalog: SqliteRepositoryBindingCatalog
    repository_provenance: SqliteRepositoryProvenanceStore
    repositories: RepositoryService
    repository_management: RepositoryManagementService
    repository_run_integration: RepositoryRunIntegration
    repository_workspace_execution: RepositoryWorkspaceExecutionCoordinator
    repository_event_ingress: RepositoryEventRuntimeIngress
    agents: AgentService
    conversations: ConversationService
    agent_runtime: AgentRuntime
    agent_orchestrator_mappers: AgentOrchestratorMapperRegistry
    capabilities: CapabilityRegistry
    capability_assignments: CapabilityAssignmentService
    models: ModelRegistry
    orchestrators: OrchestratorRegistry
    executors: ExecutorRegistry
    routing_profile_repository: JsonModelRoutingProfileRepository
    routing_profiles: ModelRoutingProfileService
    model_runtime: ModelRuntime
    onboarding: OnboardingService
    first_task: FirstRunTaskService
    secrets: SecretProvider | None
    templates: TemplateApplicationService
    workflows: AuthorizedWorkflowService
    coordination_repository: SQLiteCoordinatorRepository
    coordination: DurablePlanStepCoordinator
    research: ResearchService
    evaluation_repository: SqliteEvaluationRepository
    evaluation: EvaluationService
    accounting_service: AccountingService | None
    observability_exporter: InMemoryExporter
    telemetry: Telemetry
    health_provider: AggregatedHealthProvider
    persistence_health: SingleNodePersistenceHealthProvider
    drain: SingleNodeDrainController
    distributed_runtime: DistributedRuntime | None
    pre_authorization_lifecycle: LifecycleBackend
    lifecycle_binding: StartupLifecycleBinding
    authentication: LocalAuthenticationService
    authorization: SqliteLocalAuthorizationProvider
    authorization_audit: SqliteAuthorizationAuditSink
    approval_gate: AuthorizationGate
    verification: SqliteVerificationService
    verification_completion: SqliteVerificationCompletionAuthority
    verification_runtime: CanonicalVerificationRuntime
    kernel: PlatformKernel
    control_plane: ControlPlane
    http: AuthenticatedControlPlaneHTTP
    app: ControlPlaneASGI

    def bootstrap_admin(self, username: str, password: str) -> LocalUserAccount:
        """Create or recover the first local user and explicitly grant #15 admin policy."""

        if not self.authentication.store.users:
            account = self.authentication.bootstrap_first_admin(username, password)
        else:
            existing_account = self.authentication.store.user_by_username(username)
            if existing_account is None or len(self.authentication.store.users) != 1:
                raise ValueError(
                    "deployment bootstrap is available only for the first existing local user"
                )
            self.authentication.authenticate_password(username, password)
            account = existing_account

        if not self.authorization.has_policy(account.user_id):
            self.authorization.register(
                LocalPrincipalPolicy(
                    principal_ref=account.user_id,
                    actor_types=frozenset({ActorType.HUMAN}),
                    administrator=True,
                )
            )
        return account

    def reset_admin_password(self, username: str, new_password: str) -> LocalUserAccount:
        """Reset the sole local administrator password from the trusted operator boundary."""

        existing_account = self.authentication.store.user_by_username(username)
        if existing_account is None or len(self.authentication.store.users) != 1:
            raise ValueError(
                "administrator password recovery requires the sole existing local user"
            )
        if not self.authorization.has_administrator_policy(
            existing_account.user_id,
            actor_type=ActorType.HUMAN,
        ):
            raise ValueError(
                "administrator password recovery requires an existing human administrator policy"
            )
        self.authentication.reset_local_password(
            existing_account.user_id,
            new_password,
            operator_ref="service:local-password-recovery-operator",
            invalidate_sessions=True,
        )
        return self.authentication.store.user_by_username(username) or existing_account

    async def run_reference_smoke(self) -> SingleNodeSmokeResult:
        """Run one retry-safe canonical Task/Run through the local reference execution path."""

        accounts = tuple(self.authentication.store.users.values())
        if len(accounts) != 1:
            raise ValueError(
                "single-node smoke requires exactly one bootstrapped local administrator"
            )
        account = accounts[0]
        if not self.authorization.has_policy(account.user_id):
            raise ValueError("single-node smoke requires the administrator policy to be installed")

        project = self.scopes.create_project(
            key=_SMOKE_PROJECT_KEY,
            name="Deployment smoke",
            owner_type="user",
            owner_id=account.user_id,
        )
        task = await self.kernel.create_task(
            idempotency_key=_SMOKE_TASK_KEY,
            title="Single-node deployment smoke",
            objective="Verify canonical local reference execution without optional services",
            owner_type="user",
            owner_id=account.user_id,
            project_id=project.id,
        )
        await self.kernel.ready_task(idempotency_key=_SMOKE_READY_KEY, task_id=task.task_id)
        run = await self.kernel.start_task(
            idempotency_key=_SMOKE_START_KEY,
            task_id=task.task_id,
        )
        refreshed = await self.kernel.refresh_run(
            idempotency_key=_SMOKE_REFRESH_KEY,
            task_id=task.task_id,
            run_id=run.run_id,
        )
        persisted_task = await self.kernel.get_task(task.task_id)
        if refreshed.status is not RunStatus.SUCCEEDED:
            raise RuntimeError(f"single-node smoke run did not succeed: {refreshed.status.value}")
        if persisted_task.status is not TaskStatus.SUCCEEDED:
            raise RuntimeError(
                f"single-node smoke task did not succeed: {persisted_task.status.value}"
            )
        return SingleNodeSmokeResult(
            task_id=task.task_id,
            run_id=refreshed.run_id,
            task_status=persisted_task.status,
            run_status=refreshed.status,
        )


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
    """Build the base profile through explicit foundation and runtime stages."""

    foundation = build_single_node_foundation(
        config,
        secret_provider=secret_provider,
        observability_exporter=observability_exporter,
    )
    return build_single_node_deployment_from_foundation(
        config,
        foundation,
        onboarding_model_adapters=onboarding_model_adapters,
        accounting_service=accounting_service,
        distributed_runtime=distributed_runtime,
        enable_distributed_execution=enable_distributed_execution,
        repository_discovery_resolver=repository_discovery_resolver,
    )


def _build_runtime_execution_stage(
    config: SingleNodeConfig,
    foundation: SingleNodeFoundationBundle,
    *,
    onboarding_model_adapters: Iterable[OnboardingModelAdapter],
    distributed_runtime: DistributedRuntime | None,
    enable_distributed_execution: bool,
    repository_discovery_resolver: RepositoryDiscoveryResolver | None,
    model_runtime_factory: ModelRuntimeFactory | None,
) -> tuple[
    RuntimeServicesBundle,
    PlatformServicesBundle,
    RepositoryFoundationBundle,
    ExecutionBundle,
]:
    storage = foundation.storage
    security = foundation.security
    runtime = build_runtime_services(
        config,
        storage,
        security,
        onboarding_model_adapters=onboarding_model_adapters,
        model_runtime_factory=model_runtime_factory,
    )
    platform_services = build_platform_services(config, storage, security, runtime)
    repositories = build_repository_foundation(
        config,
        storage,
        security,
        repository_discovery_resolver=repository_discovery_resolver,
    )
    execution = build_execution(
        config,
        storage,
        security,
        foundation.observability,
        runtime,
        repositories,
        distributed_runtime=distributed_runtime,
        enable_distributed_execution=enable_distributed_execution,
    )
    return runtime, platform_services, repositories, execution


def build_single_node_deployment_from_foundation(
    config: SingleNodeConfig,
    foundation: SingleNodeFoundationBundle,
    *,
    onboarding_model_adapters: Iterable[OnboardingModelAdapter] = (),
    accounting_service: AccountingService | None = None,
    distributed_runtime: DistributedRuntime | None = None,
    enable_distributed_execution: bool = False,
    repository_discovery_resolver: RepositoryDiscoveryResolver | None = None,
    model_runtime_factory: ModelRuntimeFactory | None = None,
) -> SingleNodeDeployment:
    """Continue composition from a pre-runtime foundation with explicit runtime factories."""

    storage = foundation.storage
    observability = foundation.observability
    security = foundation.security
    runtime, platform_services, repository_foundation, execution = _build_runtime_execution_stage(
        config,
        foundation,
        onboarding_model_adapters=onboarding_model_adapters,
        distributed_runtime=distributed_runtime,
        enable_distributed_execution=enable_distributed_execution,
        repository_discovery_resolver=repository_discovery_resolver,
        model_runtime_factory=model_runtime_factory,
    )
    verification = build_verification(config)
    kernel = build_kernel(
        config,
        storage,
        observability,
        runtime,
        execution,
        verification,
    )
    repository_runtime = build_repository_runtime(
        storage,
        repository_foundation,
        kernel.kernel,
    )
    evaluation = build_evaluation(
        config,
        storage,
        security,
        observability,
        runtime,
        execution,
        kernel,
        accounting_service=accounting_service,
    )
    health, drain, control_plane, http = _build_northbound_control_plane(
        config=config,
        foundation=foundation,
        runtime=runtime,
        platform_services=platform_services,
        repositories=repository_foundation,
        repository_runtime=repository_runtime,
        execution=execution,
        verification=verification,
        kernel=kernel,
        evaluation=evaluation,
        accounting_service=accounting_service,
    )
    return _assemble_deployment(
        config=config,
        foundation=foundation,
        runtime=runtime,
        platform_services=platform_services,
        repositories=repository_foundation,
        repository_runtime=repository_runtime,
        execution=execution,
        verification=verification,
        kernel=kernel,
        evaluation=evaluation,
        health=health,
        drain=drain,
        control_plane=control_plane,
        http=http,
        accounting_service=accounting_service,
    )


def _build_northbound_control_plane(
    *,
    config: SingleNodeConfig,
    foundation: SingleNodeFoundationBundle,
    runtime: RuntimeServicesBundle,
    platform_services: PlatformServicesBundle,
    repositories: RepositoryFoundationBundle,
    repository_runtime: RepositoryRuntimeBundle,
    execution: ExecutionBundle,
    verification: VerificationBundle,
    kernel: KernelBundle,
    evaluation: EvaluationBundle,
    accounting_service: AccountingService | None,
) -> tuple[HealthBundle, SingleNodeDrainController, ControlPlaneBundle, HttpBundle]:
    """Compose drain-aware health, Control Plane and authenticated northbound transport."""

    storage = foundation.storage
    observability = foundation.observability
    security = foundation.security
    drain = SingleNodeDrainController(
        timeout_seconds=config.shutdown_timeout_seconds,
        telemetry=observability.telemetry,
    )
    health = build_health(
        config,
        storage,
        execution,
        observability,
        draining=lambda: drain.draining,
    )
    control_plane = build_control_plane(
        config,
        storage,
        security,
        observability,
        runtime,
        platform_services,
        repositories,
        repository_runtime,
        verification,
        kernel,
        evaluation,
        health,
        accounting_service=accounting_service,
    )
    drain.register_quiesce_callback(control_plane.control_plane.request_automation_runtime_stop)
    drain.register_quiesce_callback(control_plane.control_plane.request_notification_runtime_stop)
    http = build_http(config, security, control_plane, drain)
    return health, drain, control_plane, http


def _assemble_deployment(
    *,
    config: SingleNodeConfig,
    foundation: SingleNodeFoundationBundle,
    runtime: RuntimeServicesBundle,
    platform_services: PlatformServicesBundle,
    repositories: RepositoryFoundationBundle,
    repository_runtime: RepositoryRuntimeBundle,
    execution: ExecutionBundle,
    verification: VerificationBundle,
    kernel: KernelBundle,
    evaluation: EvaluationBundle,
    health: HealthBundle,
    drain: SingleNodeDrainController,
    control_plane: ControlPlaneBundle,
    http: HttpBundle,
    accounting_service: AccountingService | None,
) -> SingleNodeDeployment:
    storage = foundation.storage
    observability = foundation.observability
    security = foundation.security
    return SingleNodeDeployment(
        config=config,
        kernel_repository=storage.kernel_repository,
        scopes=storage.scopes,
        files=storage.files,
        workspaces=storage.workspaces,
        run_workspace_bindings=storage.run_workspace_bindings,
        repository_registry=storage.repository_registry,
        repository_catalog=storage.repository_catalog,
        repository_provenance=storage.repository_provenance,
        repositories=repositories.repositories,
        repository_management=repositories.repository_management,
        repository_run_integration=repository_runtime.repository_run_integration,
        repository_workspace_execution=repositories.repository_workspace_execution,
        repository_event_ingress=repositories.repository_event_ingress,
        agents=runtime.agents,
        conversations=runtime.conversations,
        agent_runtime=runtime.agent_runtime,
        agent_orchestrator_mappers=runtime.orchestrator_mappers,
        capabilities=runtime.capabilities,
        capability_assignments=platform_services.capability_assignments,
        models=runtime.models,
        orchestrators=execution.orchestrators,
        executors=execution.executors,
        routing_profile_repository=runtime.routing_profile_repository,
        routing_profiles=runtime.routing_profiles,
        model_runtime=runtime.model_runtime,
        onboarding=runtime.onboarding,
        first_task=kernel.first_task,
        secrets=security.secrets,
        templates=platform_services.templates,
        workflows=platform_services.workflows,
        coordination_repository=kernel.coordination_repository,
        coordination=kernel.coordination,
        research=platform_services.research,
        evaluation_repository=evaluation.composition.repository,
        evaluation=evaluation.composition.service,
        accounting_service=accounting_service,
        observability_exporter=observability.exporter,
        telemetry=observability.telemetry,
        health_provider=health.provider,
        persistence_health=health.persistence,
        drain=drain,
        distributed_runtime=execution.distributed_runtime,
        pre_authorization_lifecycle=execution.pre_authorization_lifecycle,
        lifecycle_binding=execution.lifecycle,
        authentication=security.authentication,
        authorization=security.authorization,
        authorization_audit=security.authorization_audit,
        approval_gate=security.approval_gate,
        verification=verification.verification,
        verification_completion=verification.completion_authority,
        verification_runtime=kernel.verification_runtime,
        kernel=kernel.kernel,
        control_plane=control_plane.control_plane,
        http=http.http,
        app=http.app,
    )


__all__ = [
    "SingleNodeDeployment",
    "SingleNodeSmokeResult",
    "build_single_node_deployment",
    "build_single_node_deployment_from_foundation",
]
