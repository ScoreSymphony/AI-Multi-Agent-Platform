"""Production-shaped single-node composition for issue #39 and first-run onboarding."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.accounting import AccountingService
from ai_multi_agent_platform.agents import (
    AgentRuntime,
    AgentService,
    DurableRoutingProfileAgentRuntime,
    JsonAgentRepository,
    register_standard_agent_control_plane,
)
from ai_multi_agent_platform.agents.routing_profile_control_plane import (
    register_routing_profile_aware_agent_control_plane,
)
from ai_multi_agent_platform.capabilities import CapabilityRegistry
from ai_multi_agent_platform.capability_assignments import (
    CallableCapabilityAssignmentTargetResolver,
    CapabilityAssignmentService,
    JsonCapabilityAssignmentRepository,
)
from ai_multi_agent_platform.configuration import SecretProvider
from ai_multi_agent_platform.contracts import LifecycleBackend
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
    DurableRoutingProfileConversationResponseProvider,
    JsonConversationRepository,
)
from ai_multi_agent_platform.data import LocalFileProvider
from ai_multi_agent_platform.distributed import (
    DistributedLifecycleBackend,
    DistributedRegistry,
    DistributedRuntime,
    JobRequirements,
)
from ai_multi_agent_platform.domain import RunStatus, TaskStatus
from ai_multi_agent_platform.evaluation import (
    AccountingEvaluationEvidenceProvider,
    EvaluationEvidenceProvider,
    EvaluationService,
    InMemoryObservabilityEvaluationEvidenceProvider,
    SqliteEvaluationRepository,
)
from ai_multi_agent_platform.evaluation.single_node import build_single_node_evaluation
from ai_multi_agent_platform.execution import ExecutorLifecycleBackend, ReferenceExecutor
from ai_multi_agent_platform.kernel import (
    EventSourcedTaskRepository,
    PlatformKernel,
    SqliteKernelRepository,
)
from ai_multi_agent_platform.models import (
    JsonModelRegistryStore,
    JsonModelRoutingProfileRepository,
    ModelRegistry,
    ModelRoutingProfileAssignmentGate,
    ModelRoutingProfileRef,
    ModelRoutingProfileService,
    ModelRuntime,
)
from ai_multi_agent_platform.observability import (
    AggregatedHealthProvider,
    InMemoryExporter,
    ObservabilityEventProvider,
    ObservedAuthorizationProvider,
    ObservedExecutor,
    ObservedOrchestrator,
    ProviderHealthDependency,
    Telemetry,
)
from ai_multi_agent_platform.observability.composite import CompositeTimelineReader
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
    RepositoryDiscoveryResolver,
    RepositoryEventRuntimeIngress,
    RepositoryManagementService,
    RepositoryRegistry,
    RepositoryRunIntegration,
    RepositoryService,
    RepositoryWorkspaceExecutionCoordinator,
    RepositoryWorkspaceSourceResolver,
    SqliteRepositoryBindingCatalog,
    SqliteRepositoryProvenanceStore,
    restore_managed_local_repositories,
)
from ai_multi_agent_platform.repositories.control_plane import register_repository_control_plane
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    AuthorizedLifecycleBackend,
    AuthorizedSecretProvider,
    ControlPlaneAuthorizationBridge,
    LocalAuthenticationService,
    LocalPrincipalPolicy,
    LocalUserAccount,
    ResourceType,
    SqliteApprovalService,
    SqliteAuthorizationAuditSink,
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
    register_capability_assignment_template_handler,
    register_project_template_handler,
    register_workflow_template_handler,
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
from ai_multi_agent_platform.workflows import (
    AuthorizedWorkflowService,
    JsonWorkflowRepository,
    WorkflowService,
)
from ai_multi_agent_platform.workspaces import SqliteRunWorkspaceBindingRepository
from ai_multi_agent_platform.workspaces.compensation import CompensatingSqliteWorkspaceProvider

from .config import SingleNodeConfig

_REFERENCE_EXECUTION_WORKSPACE = "reference"
_SMOKE_PROJECT_KEY = "deployment-smoke-project-v1"
_SMOKE_TASK_KEY = "deployment-smoke-task-v1"
_SMOKE_READY_KEY = "deployment-smoke-ready-v1"
_SMOKE_START_KEY = "deployment-smoke-start-v1"
_SMOKE_REFRESH_KEY = "deployment-smoke-refresh-v1"
_EVALUATION_PROJECT_KEY = "evaluation-system-project-v1"
_EVALUATION_OWNER_ID = "evaluation-single-node"
_EVALUATION_PRINCIPAL = f"service:{_EVALUATION_OWNER_ID}"
_PLATFORM_SERVICE_PRINCIPAL = "service:platform"


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
    """Build the durable Stage-1 profile without optional external services.

    ``enable_distributed_execution`` is opt-in. The ordinary #39 profile therefore keeps its local
    reference LifecycleBackend unchanged, while advanced deployment may bind the same canonical
    Task/Run kernel seam to #14 scheduling and Worker dispatch.
    """

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
    repository_workspace_execution = RepositoryWorkspaceExecutionCoordinator(
        run_workspace_bindings,
        workspaces,
        repository_provenance,
        fallback_workspace=_REFERENCE_EXECUTION_WORKSPACE,
    )
    repository_event_ingress = RepositoryEventRuntimeIngress(
        repository_registry,
        kernel_repository,
    )
    evaluation_project = scopes.create_project(
        key=_EVALUATION_PROJECT_KEY,
        name="Platform Evaluation",
        owner_type="service",
        owner_id=_EVALUATION_OWNER_ID,
    )
    agents = AgentService(JsonAgentRepository(database_dir / "agents.json"))
    conversations = ConversationService(
        JsonConversationRepository(database_dir / "conversations.json")
    )
    capabilities = CapabilityRegistry()
    models = ModelRegistry()
    routing_profile_repository = JsonModelRoutingProfileRepository(
        database_dir / "model-routing-profiles.json"
    )
    agent_runtime = DurableRoutingProfileAgentRuntime(
        agents,
        routing_profile_repository=routing_profile_repository,
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
    conversation_response_provider = DurableRoutingProfileConversationResponseProvider(
        model_runtime,
        agents,
        routing_profile_repository=routing_profile_repository,
    )

    effective_observability_exporter = observability_exporter or InMemoryExporter()
    telemetry = Telemetry(effective_observability_exporter)
    authentication_store = SqliteAuthenticationStore(database_dir / "authentication.sqlite3")
    authentication = LocalAuthenticationService(store=authentication_store)
    authorization = SqliteLocalAuthorizationProvider(database_dir / "authorization.sqlite3")
    effective_distributed_runtime = distributed_runtime
    if enable_distributed_execution and effective_distributed_runtime is None:
        effective_distributed_runtime = DistributedRuntime(
            DistributedRegistry(),
            authorization=authorization,
        )
    routing_profiles = ModelRoutingProfileService(
        routing_profile_repository,
        authorization=authorization,
    )
    routing_profile_assignment_gate = ModelRoutingProfileAssignmentGate(
        routing_profile_repository,
        authorization=authorization,
    )
    if not authorization.has_policy(_EVALUATION_PRINCIPAL):
        authorization.register(
            LocalPrincipalPolicy(
                principal_ref=_EVALUATION_PRINCIPAL,
                actor_types=frozenset({ActorType.SERVICE}),
                allowed_actions=frozenset(
                    {
                        AuthorizationAction.EXECUTE,
                        AuthorizationAction.READ,
                        AuthorizationAction.MODIFY,
                    }
                ),
                resource_types=frozenset({ResourceType.RUN}),
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
    observed_authorization = ObservedAuthorizationProvider(authorization, telemetry)
    approval_service = SqliteApprovalService(database_dir / "approvals.sqlite3")
    authorization_audit = SqliteAuthorizationAuditSink(database_dir / "authorization-audit.sqlite3")
    approval_gate = AuthorizationGate(
        observed_authorization,
        approvals=approval_service,
        audit_sink=authorization_audit,
    )
    control_plane_authorization = ControlPlaneAuthorizationBridge(approval_gate)
    protected_secret_provider: SecretProvider | None = (
        AuthorizedSecretProvider(secret_provider, approval_gate)
        if secret_provider is not None
        else None
    )
    workflow_service = WorkflowService(JsonWorkflowRepository(database_dir / "workflows.json"))
    workflows = AuthorizedWorkflowService(workflow_service, approval_gate)

    template_handlers = ContextualTemplateHandlerRegistry()
    register_agent_template_handlers(template_handlers, agents)
    register_project_template_handler(template_handlers, scopes)
    register_workspace_structure_template_handler(template_handlers, workspaces, scopes)
    register_workflow_template_handler(template_handlers, workflows, agents=agents)
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

    execution_workspace = workspaces.materialization_root / _REFERENCE_EXECUTION_WORKSPACE
    execution_workspace.mkdir(parents=True, exist_ok=True)
    reference_orchestrator = ReferenceOrchestrator()
    reference_executor = ReferenceExecutor(workspaces.materialization_root)
    orchestrator = ObservedOrchestrator(reference_orchestrator, telemetry)
    observed_executor = ObservedExecutor(reference_executor, telemetry)
    reference_lifecycle = ExecutorLifecycleBackend(
        observed_executor,
        workspace=_REFERENCE_EXECUTION_WORKSPACE,
        action="echo",
        workspace_resolver=repository_workspace_execution.resolve_execution_workspace,
        terminal_result_observer=repository_workspace_execution.observe_terminal_result,
    )
    execution_lifecycle: LifecycleBackend = reference_lifecycle
    if enable_distributed_execution:
        if effective_distributed_runtime is None:
            raise AssertionError("distributed execution enabled without a distributed runtime")
        execution_lifecycle = DistributedLifecycleBackend(
            effective_distributed_runtime,
            requirements=JobRequirements(executor_type="reference"),
            workspace_bindings=run_workspace_bindings,
        )
    lifecycle = AuthorizedLifecycleBackend(
        FirstRunAgentLifecycleBackend(
            delegate=execution_lifecycle,
            tasks=EventSourcedTaskRepository(kernel_repository),
            agents=agent_runtime,
            models=model_runtime,
        ),
        approval_gate,
        allow_internal_service_reads=True,
    )
    verification_path = database_dir / "verification.sqlite3"
    verification = SqliteVerificationService(
        verification_path,
        require_canonical_subjects=True,
        require_canonical_results=True,
    )
    verification_completion = SqliteVerificationCompletionAuthority(verification, verification_path)
    observability_events = ObservabilityEventProvider(telemetry)
    kernel = PlatformKernel(
        orchestrator=orchestrator,
        lifecycle=lifecycle,
        repository=kernel_repository,
        event_sink=observability_events,
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

    repositories = RepositoryService(repository_registry, approval_gate)
    repository_management = RepositoryManagementService(
        repository_registry,
        repository_catalog,
        approval_gate,
        managed_local_root=config.repositories_dir,
        discovery_resolver=repository_discovery_resolver,
    )
    repository_run_integration = RepositoryRunIntegration(
        repository_registry,
        repository_provenance,
        workspaces,
        files,
        kernel,
    )
    repository_workspace_execution.configure_run_integration(repository_run_integration)
    capability_assignments = CapabilityAssignmentService(
        repository=JsonCapabilityAssignmentRepository(database_dir / "capability-assignments.json"),
        capabilities=capabilities,
        targets=CallableCapabilityAssignmentTargetResolver(
            get_agent=agents.repository.get_agent,
            get_team=agents.repository.get_team,
            get_project=scopes.get_project,
        ),
        authorization=approval_gate,
    )
    register_capability_assignment_template_handler(template_handlers, capability_assignments)

    evaluation_evidence_providers: list[EvaluationEvidenceProvider] = []
    if accounting_service is not None:
        evaluation_evidence_providers.append(
            AccountingEvaluationEvidenceProvider(accounting_service)
        )
    evaluation_evidence_providers.append(
        InMemoryObservabilityEvaluationEvidenceProvider(effective_observability_exporter)
    )
    evaluation_composition = build_single_node_evaluation(
        database_path=database_dir / "evaluation.sqlite3",
        asset_dir=config.evaluation_dir,
        kernel=kernel,
        agents=agents.repository,
        agent_runtime=agent_runtime,
        models=models,
        model_runtime=model_runtime,
        orchestrator=reference_orchestrator,
        executor=reference_executor,
        files=files,
        workspaces=workspaces,
        project_id=evaluation_project.id,
        run_workspace_bindings=run_workspace_bindings,
        evidence_providers=tuple(evaluation_evidence_providers),
        approval_reader=approval_gate.approvals,
        distributed_runtime=effective_distributed_runtime,
    )

    portability_workflow = build_agent_portability_workflow(
        agents=agents.repository,
        models=models,
        scopes=scopes,
        platform_version=__version__,
        capabilities=capabilities,
        templates=templates.repository,
        routing_profiles=routing_profile_repository,
        evaluation=evaluation_composition.service,
        evaluation_fixture_exists=evaluation_composition.fixture_exists,
    )

    health_provider = AggregatedHealthProvider(
        (
            ProviderHealthDependency(orchestrator, required=True, name="orchestrator"),
            ProviderHealthDependency(lifecycle, required=True, name="lifecycle"),
            ProviderHealthDependency(files, required=True, name="files"),
        )
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=kernel_repository,
        scopes=scopes,
        authorization=control_plane_authorization,
        workspace_provider=workspaces,
        run_workspace_bindings=run_workspace_bindings,
        health_providers=(health_provider,),
        model_registry=models,
        automation_state_path=database_dir / "automation.sqlite3",
        notification_state_path=database_dir / "notifications.sqlite3",
        conversation_service=conversations,
        conversation_agent_service=agents,
        conversation_file_provider=files,
        conversation_response_provider=conversation_response_provider,
        portability_workflow=portability_workflow,
        approval_gate=approval_gate,
        accounting_service=accounting_service,
    )
    resolvers = control_plane.workspace_source_resolvers
    if resolvers is None:
        raise RuntimeError(
            "single-node Control Plane did not initialize Workspace source resolvers"
        )
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
    register_routing_profile_aware_agent_control_plane(
        control_plane,
        agents,
        routing_profile_assignment_gate,
        runtime=agent_runtime,
    )
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
        capability_versions=lambda: (
            (capability.capability_id, capability.version)
            for capability in capabilities.inventory_capabilities(include_unavailable=False)
        ),
        model_policies=lambda: (
            ModelRoutingProfileRef(definition.profile_id, definition.current_revision).canonical_ref
            for definition in routing_profile_repository.list_definitions()
            if definition.enabled
        ),
        grantable_permissions=lambda context: (
            action.value
            for action in authorization.globally_grantable_actions(
                context.actor.principal_ref,
                actor_type=context.actor.actor_type,
            )
        ),
        platform_version=__version__,
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
    control_plane.bind_observability_timeline(
        CompositeTimelineReader(
            (
                effective_observability_exporter,
                VerificationTimelineReader(verification),
            )
        )
    )

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
        run_workspace_bindings=run_workspace_bindings,
        repository_registry=repository_registry,
        repository_catalog=repository_catalog,
        repository_provenance=repository_provenance,
        repositories=repositories,
        repository_management=repository_management,
        repository_run_integration=repository_run_integration,
        repository_workspace_execution=repository_workspace_execution,
        repository_event_ingress=repository_event_ingress,
        agents=agents,
        conversations=conversations,
        agent_runtime=agent_runtime,
        capabilities=capabilities,
        capability_assignments=capability_assignments,
        models=models,
        routing_profile_repository=routing_profile_repository,
        routing_profiles=routing_profiles,
        model_runtime=model_runtime,
        onboarding=onboarding,
        first_task=first_task,
        secrets=protected_secret_provider,
        templates=templates,
        workflows=workflows,
        evaluation_repository=evaluation_composition.repository,
        evaluation=evaluation_composition.service,
        accounting_service=accounting_service,
        observability_exporter=effective_observability_exporter,
        telemetry=telemetry,
        health_provider=health_provider,
        distributed_runtime=effective_distributed_runtime,
        authentication=authentication,
        authorization=authorization,
        authorization_audit=authorization_audit,
        approval_gate=approval_gate,
        verification=verification,
        verification_runtime=verification_runtime,
        kernel=kernel,
        control_plane=control_plane,
        http=http,
        app=app,
    )
