"""Production-shaped single-node composition for issue #39 and first-run onboarding."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ai_multi_agent_platform.accounting import AccountingService
from ai_multi_agent_platform.agents import AgentRuntime, AgentService
from ai_multi_agent_platform.capabilities import CapabilityRegistry
from ai_multi_agent_platform.capability_assignments import CapabilityAssignmentService
from ai_multi_agent_platform.configuration import SecretProvider
from ai_multi_agent_platform.control_plane import AuthenticatedControlPlaneHTTP, ControlPlaneASGI
from ai_multi_agent_platform.control_plane.approval_portability_composition import ControlPlane
from ai_multi_agent_platform.control_plane.sqlite_scope import SqliteScopeStore
from ai_multi_agent_platform.conversations import ConversationService
from ai_multi_agent_platform.coordination import DurablePlanStepCoordinator, SQLiteCoordinatorRepository
from ai_multi_agent_platform.data import LocalFileProvider
from ai_multi_agent_platform.distributed import DistributedRuntime
from ai_multi_agent_platform.domain import RunStatus, TaskStatus
from ai_multi_agent_platform.evaluation import EvaluationService, SqliteEvaluationRepository
from ai_multi_agent_platform.kernel import PlatformKernel, SqliteKernelRepository
from ai_multi_agent_platform.models import (
    JsonModelRoutingProfileRepository,
    ModelRegistry,
    ModelRoutingProfileService,
    ModelRuntime,
)
from ai_multi_agent_platform.observability import AggregatedHealthProvider, InMemoryExporter, Telemetry
from ai_multi_agent_platform.onboarding import FirstRunTaskService, OnboardingModelAdapter, OnboardingService
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
    build_http,
    build_kernel,
    build_observability,
    build_platform_services,
    build_repository_foundation,
    build_repository_runtime,
    build_runtime_services,
    build_security,
    build_storage,
    build_verification,
)
from .config import SingleNodeConfig

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
    capabilities: CapabilityRegistry
    capability_assignments: CapabilityAssignmentService
    models: ModelRegistry
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
    distributed_runtime: DistributedRuntime | None
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
        """Create or recover the first local user and explicitly grant #15 admin policy.

        Authentication and authorization remain separate durable records. Re-running this
        operation after an interruption is safe when the same first username/password is
        supplied: the existing identity is verified and a missing administrator policy is
        repaired rather than creating a second user.
        """

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
        await self.kernel.ready_task(
            idempotency_key=_SMOKE_READY_KEY,
            task_id=task.task_id,
        )
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
    """Build the durable Stage-1 profile through explicit typed composition layers."""

    config.prepare_directories()
    storage = build_storage(config)
    observability = build_observability(observability_exporter)
    security = build_security(
        config,
        observability,
        secret_provider=secret_provider,
    )
    runtime = build_runtime_services(
        config,
        storage,
        security,
        onboarding_model_adapters=onboarding_model_adapters,
    )
    platform_services = build_platform_services(config, storage, security, runtime)
    repositories = build_repository_foundation(
        config,
        storage,
        security,
        discovery_resolver=repository_discovery_resolver,
    )
    execution = build_execution(
        storage,
        security,
        observability,
        runtime,
        repositories,
        distributed_runtime=distributed_runtime,
        enable_distributed_execution=enable_distributed_execution,
    )
    verification = build_verification(config)
    kernel = build_kernel(
        config,
        storage,
        execution,
        verification,
        runtime,
        observability,
    )
    repository_runtime = build_repository_runtime(storage, repositories, kernel.kernel)
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
    control_plane = build_control_plane(
        config,
        storage,
        security,
        observability,
        runtime,
        platform_services,
        repositories,
        repository_runtime,
        execution,
        verification,
        kernel,
        evaluation,
        accounting_service=accounting_service,
    )
    http = build_http(config, security, control_plane)

    return SingleNodeDeployment(
        config=config,
        kernel_repository=storage.kernel_repository,
        scopes=storage.scopes,
        files=storage.files,
        workspaces=storage.workspaces,
        run_workspace_bindings=storage.run_workspace_bindings,
        repository_registry=repositories.registry,
        repository_catalog=repositories.catalog,
        repository_provenance=repositories.provenance,
        repositories=repositories.services,
        repository_management=repositories.management,
        repository_run_integration=repository_runtime.run_integration,
        repository_workspace_execution=repositories.workspace_execution,
        repository_event_ingress=repositories.event_ingress,
        agents=runtime.agents,
        conversations=runtime.conversations,
        agent_runtime=runtime.agent_runtime,
        capabilities=runtime.capabilities,
        capability_assignments=platform_services.capability_assignments,
        models=runtime.models,
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
        evaluation_repository=evaluation.repository,
        evaluation=evaluation.service,
        accounting_service=accounting_service,
        observability_exporter=observability.exporter,
        telemetry=observability.telemetry,
        health_provider=control_plane.health_provider,
        distributed_runtime=execution.distributed_runtime,
        authentication=security.authentication,
        authorization=security.authorization,
        authorization_audit=security.authorization_audit,
        approval_gate=security.approval_gate,
        verification=verification.service,
        verification_completion=verification.completion_authority,
        verification_runtime=kernel.verification_runtime,
        kernel=kernel.kernel,
        control_plane=control_plane.control_plane,
        http=http.http,
        app=http.app,
    )
