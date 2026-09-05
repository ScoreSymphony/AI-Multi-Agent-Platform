"""Production-shaped single-node composition for issue #39 and first-run onboarding."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.agents import (
    AgentRuntime,
    AgentService,
    JsonAgentRepository,
    register_agent_control_plane,
    register_standard_agent_control_plane,
)
from ai_multi_agent_platform.capabilities import CapabilityRegistry
from ai_multi_agent_platform.configuration import SecretProvider
from ai_multi_agent_platform.control_plane import (
    AuthenticatedControlPlaneHTTP,
    ControlPlaneASGI,
    evaluation_command_handlers,
    evaluation_resource_services,
)
from ai_multi_agent_platform.control_plane.approval_portability_composition import ControlPlane
from ai_multi_agent_platform.control_plane.sqlite_scope import SqliteScopeStore
from ai_multi_agent_platform.conversations import (
    ConversationService,
    JsonConversationRepository,
    ModelRuntimeConversationResponseProvider,
)
from ai_multi_agent_platform.data import LocalFileProvider
from ai_multi_agent_platform.domain import RunStatus, TaskStatus
from ai_multi_agent_platform.evaluation import EvaluationService, SqliteEvaluationRepository
from ai_multi_agent_platform.evaluation.single_node import build_single_node_evaluation
from ai_multi_agent_platform.execution import ExecutorLifecycleBackend, ReferenceExecutor
from ai_multi_agent_platform.kernel import (
    EventSourcedTaskRepository,
    PlatformKernel,
    SqliteKernelRepository,
)
from ai_multi_agent_platform.models import JsonModelRegistryStore, ModelRegistry, ModelRuntime
from ai_multi_agent_platform.onboarding import (
    FirstRunAgentLifecycleBackend,
    FirstRunTaskService,
    JsonModelProviderSetupStore,
    JsonOnboardingCommandStore,
    OnboardingModelAdapter,
    OnboardingService,
    register_onboarding_control_plane,
)
from ai_multi_agent_platform.orchestration import ReferenceOrchestrator
from ai_multi_agent_platform.portability.composition import build_agent_portability_workflow
from ai_multi_agent_platform.repositories import (
    RepositoryManagementService,
    RepositoryRegistry,
    RepositoryRunIntegration,
    RepositoryService,
    RepositoryWorkspaceSourceResolver,
    SqliteRepositoryBindingCatalog,
    SqliteRepositoryProvenanceStore,
    restore_managed_local_repositories,
)
from ai_multi_agent_platform.repositories.control_plane import register_repository_control_plane
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationGate,
    LocalAuthenticationService,
    LocalPrincipalPolicy,
    LocalUserAccount,
)
from ai_multi_agent_platform.security.sqlite_authentication import SqliteAuthenticationStore
from ai_multi_agent_platform.security.sqlite_authorization import SqliteLocalAuthorizationProvider
from ai_multi_agent_platform.templates import (
    AgentTeamTemplateExporter,
    AgentTemplateExporter,
    AutomationTemplateExporter,
    ContextualTemplateHandlerRegistry,
    JsonTemplateRepository,
    PlatformTemplateEnvironmentResolver,
    ProjectTemplateExporter,
    TemplateApplicationService,
    WorkspaceStructureTemplateExporter,
    register_agent_template_handlers,
    register_automation_template_handler,
    register_project_template_handler,
    register_workspace_structure_template_handler,
)
from ai_multi_agent_platform.templates.agent_team_control_plane import (
    register_agent_team_template_control_plane,
)
from ai_multi_agent_platform.templates.compensation import register_template_compensators
from ai_multi_agent_platform.templates.control_plane import register_template_control_plane
from ai_multi_agent_platform.templates.project_control_plane import (
    register_project_template_control_plane,
)
from ai_multi_agent_platform.templates.workspace_structure_control_plane import (
    register_workspace_structure_template_control_plane,
)
from ai_multi_agent_platform.verification import (
    CanonicalVerificationRuntime,
    KernelFileVerificationEvidenceResolver,
    SqliteVerificationCompletionAuthority,
    SqliteVerificationService,
)
from ai_multi_agent_platform.verification.control_plane import register_verification_control_plane
from ai_multi_agent_platform.verification.observability import VerificationTimelineReader
from ai_multi_agent_platform.workspaces.compensation import CompensatingSqliteWorkspaceProvider

from .config import SingleNodeConfig

_REFERENCE_EXECUTION_WORKSPACE = "reference"
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
    repository_registry: RepositoryRegistry
    repository_catalog: SqliteRepositoryBindingCatalog
    repository_provenance: SqliteRepositoryProvenanceStore
    repositories: RepositoryService
    repository_management: RepositoryManagementService
    repository_run_integration: RepositoryRunIntegration
    agents: AgentService
    conversations: ConversationService
    agent_runtime: AgentRuntime
    capabilities: CapabilityRegistry
    models: ModelRegistry
    model_runtime: ModelRuntime
    onboarding: OnboardingService
    first_task: FirstRunTaskService
    secrets: SecretProvider | None
    templates: TemplateApplicationService
    evaluation_repository: SqliteEvaluationRepository
    evaluation: EvaluationService
    authentication: LocalAuthenticationService
    authorization: SqliteLocalAuthorizationProvider
    verification: SqliteVerificationService
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
) -> SingleNodeDeployment:
    """Build the durable Stage-1 profile without optional external services."""

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
    repository_catalog = SqliteRepositoryBindingCatalog(
        database_dir / "repository-bindings.sqlite3"
    )
    repository_registry = RepositoryRegistry()
    restore_managed_local_repositories(repository_catalog, repository_registry)
    repository_provenance = SqliteRepositoryProvenanceStore(
        database_dir / "repository-provenance.sqlite3"
    )

    agents = AgentService(JsonAgentRepository(database_dir / "agents.json"))
    conversations = ConversationService(
        JsonConversationRepository(database_dir / "conversations.json")
    )
    capabilities = CapabilityRegistry()
    models = ModelRegistry()
    agent_runtime = AgentRuntime(
        agents,
        model_registry=models,
        capability_registry=capabilities,
    )
    onboarding = OnboardingService(
        models=models,
        model_store=JsonModelRegistryStore(database_dir / "models.json"),
        provider_store=JsonModelProviderSetupStore(database_dir / "model-providers.json"),
        command_store=JsonOnboardingCommandStore(database_dir / "onboarding-commands.json"),
        scopes=scopes,
        agents=agents,
        agent_runtime=agent_runtime,
        model_adapters=onboarding_model_adapters,
    )
    onboarding.restore()
    model_runtime = ModelRuntime(models)
    conversation_response_provider = ModelRuntimeConversationResponseProvider(
        model_runtime,
        agents,
        routing_profiles=agent_runtime.routing_profiles,
    )

    template_handlers = ContextualTemplateHandlerRegistry()
    register_agent_template_handlers(template_handlers, agents)
    register_project_template_handler(template_handlers, scopes)
    register_workspace_structure_template_handler(template_handlers, workspaces, scopes)
    register_template_compensators(
        template_handlers,
        agents=agents,
        scopes=scopes,
        workspaces=workspaces,
    )
    templates = TemplateApplicationService(
        JsonTemplateRepository(database_dir / "templates.json"),
        template_handlers,
    )
    agent_template_exporter = AgentTemplateExporter(agents, templates.templates)
    agent_team_template_exporter = AgentTeamTemplateExporter(agents, templates.templates)
    project_template_exporter = ProjectTemplateExporter(scopes, templates.templates)
    workspace_template_exporter = WorkspaceStructureTemplateExporter(
        workspaces,
        templates.templates,
    )

    execution_workspace = config.executor_dir / _REFERENCE_EXECUTION_WORKSPACE
    execution_workspace.mkdir(parents=True, exist_ok=True)
    orchestrator = ReferenceOrchestrator()
    reference_executor = ReferenceExecutor(config.executor_dir)
    reference_lifecycle = ExecutorLifecycleBackend(
        reference_executor,
        workspace=_REFERENCE_EXECUTION_WORKSPACE,
        action="echo",
    )
    lifecycle = FirstRunAgentLifecycleBackend(
        delegate=reference_lifecycle,
        tasks=EventSourcedTaskRepository(kernel_repository),
        agents=agent_runtime,
        models=model_runtime,
    )
    verification_path = database_dir / "verification.sqlite3"
    verification = SqliteVerificationService(
        verification_path,
        require_canonical_subjects=True,
        require_canonical_results=True,
    )
    verification_completion = SqliteVerificationCompletionAuthority(verification, verification_path)
    kernel = PlatformKernel(
        orchestrator=orchestrator,
        lifecycle=lifecycle,
        repository=kernel_repository,
        completion_authority=verification_completion,
    )
    verification_evidence = KernelFileVerificationEvidenceResolver(
        kernel, kernel_repository, files, agents.repository
    )
    verification_runtime = CanonicalVerificationRuntime(
        verification_completion, verification_evidence
    )
    first_task = FirstRunTaskService(
        onboarding=onboarding,
        kernel=kernel,
        scopes=scopes,
        agents=agents,
    )
    evaluation_composition = build_single_node_evaluation(
        database_path=database_dir / "evaluation.sqlite3",
        kernel=kernel,
        agents=agents.repository,
        orchestrator=orchestrator,
        executor=reference_executor,
    )

    authentication_store = SqliteAuthenticationStore(database_dir / "authentication.sqlite3")
    authentication = LocalAuthenticationService(store=authentication_store)
    authorization = SqliteLocalAuthorizationProvider(database_dir / "authorization.sqlite3")
    authorization_gate = AuthorizationGate(authorization)
    repositories = RepositoryService(repository_registry, authorization_gate)
    repository_management = RepositoryManagementService(
        repository_registry,
        repository_catalog,
        authorization_gate,
        managed_local_root=config.repositories_dir,
    )
    repository_run_integration = RepositoryRunIntegration(
        repository_registry,
        repository_provenance,
        workspaces,
        files,
        kernel,
    )

    portability_workflow = build_agent_portability_workflow(
        agents=agents.repository,
        models=models,
        scopes=scopes,
        platform_version=__version__,
        templates=templates.repository,
    )

    control_plane = ControlPlane(
        kernel=kernel,
        events=kernel_repository,
        scopes=scopes,
        authorization=authorization,
        workspace_provider=workspaces,
        health_providers=(orchestrator, lifecycle, files),
        model_registry=models,
        automation_state_path=database_dir / "automation.sqlite3",
        notification_state_path=database_dir / "notifications.sqlite3",
        conversation_service=conversations,
        conversation_agent_service=agents,
        conversation_file_provider=files,
        conversation_response_provider=conversation_response_provider,
        portability_workflow=portability_workflow,
        approval_gate=AuthorizationGate(authorization),
    )
    resolvers = control_plane.workspace_source_resolvers
    if resolvers is None:
        raise RuntimeError("single-node Control Plane did not initialize Workspace source resolvers")
    resolvers.register(RepositoryWorkspaceSourceResolver(repository_registry, files))
    control_plane.configure_repository_run_integration(repository_run_integration)
    register_repository_control_plane(
        control_plane,
        repositories,
        management=repository_management,
    )
    for collection, service in evaluation_resource_services(evaluation_composition.service).items():
        control_plane.register_resource_service(collection, service)
    for command, handler in evaluation_command_handlers(evaluation_composition.service).items():
        control_plane.register_command(command, handler)
    register_agent_control_plane(control_plane, agents, runtime=agent_runtime)
    register_standard_agent_control_plane(control_plane, agents)
    register_onboarding_control_plane(control_plane, onboarding, first_task=first_task)
    register_automation_template_handler(template_handlers, control_plane.automation_service)
    register_template_compensators(
        template_handlers,
        automations=control_plane.automation_service,
    )
    automation_template_exporter = AutomationTemplateExporter(
        control_plane.automation_service,
        templates.templates,
    )
    template_environment = PlatformTemplateEnvironmentResolver(
        workspaces=workspaces,
        capabilities=lambda: (
            capability.capability_id
            for capability in capabilities.inventory_capabilities(include_unavailable=False)
        ),
    )
    register_template_control_plane(
        control_plane,
        templates,
        environment_resolver=template_environment,
        agent_exporter=agent_template_exporter,
        automation_exporter=automation_template_exporter,
    )
    register_agent_team_template_control_plane(
        control_plane,
        templates.repository,
        agent_team_template_exporter,
    )
    register_project_template_control_plane(
        control_plane,
        templates.repository,
        project_template_exporter,
    )
    register_workspace_structure_template_control_plane(
        control_plane,
        templates.repository,
        workspace_template_exporter,
    )
    register_verification_control_plane(
        control_plane,
        verification,
        verification_completion,
        verification_evidence,
        verification_runtime,
    )
    control_plane.bind_observability_timeline(VerificationTimelineReader(verification))

    http = AuthenticatedControlPlaneHTTP(
        control_plane,
        authentication,
        secure_cookie=config.secure_cookie,
    )
    app = ControlPlaneASGI(http)

    return SingleNodeDeployment(
        config=config,
        kernel_repository=kernel_repository,
        scopes=scopes,
        files=files,
        workspaces=workspaces,
        repository_registry=repository_registry,
        repository_catalog=repository_catalog,
        repository_provenance=repository_provenance,
        repositories=repositories,
        repository_management=repository_management,
        repository_run_integration=repository_run_integration,
        agents=agents,
        conversations=conversations,
        agent_runtime=agent_runtime,
        capabilities=capabilities,
        models=models,
        model_runtime=model_runtime,
        onboarding=onboarding,
        first_task=first_task,
        secrets=secret_provider,
        templates=templates,
        evaluation_repository=evaluation_composition.repository,
        evaluation=evaluation_composition.service,
        authentication=authentication,
        authorization=authorization,
        verification=verification,
        verification_runtime=verification_runtime,
        kernel=kernel,
        control_plane=control_plane,
        http=http,
        app=app,
    )
