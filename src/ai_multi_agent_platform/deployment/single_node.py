"""Production-shaped single-node composition for issue #39."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.agents import (
    AgentService,
    JsonAgentRepository,
    register_agent_control_plane,
    register_standard_agent_control_plane,
)
from ai_multi_agent_platform.control_plane import (
    AuthenticatedControlPlaneHTTP,
    ControlPlane,
    ControlPlaneASGI,
)
from ai_multi_agent_platform.control_plane.sqlite_scope import SqliteScopeStore
from ai_multi_agent_platform.data import LocalFileProvider
from ai_multi_agent_platform.domain import RunStatus, TaskStatus
from ai_multi_agent_platform.execution import ExecutorLifecycleBackend, ReferenceExecutor
from ai_multi_agent_platform.kernel import PlatformKernel, SqliteKernelRepository
from ai_multi_agent_platform.orchestration import ReferenceOrchestrator
from ai_multi_agent_platform.security import (
    ActorType,
    LocalAuthenticationService,
    LocalPrincipalPolicy,
    LocalUserAccount,
)
from ai_multi_agent_platform.security.sqlite_authentication import SqliteAuthenticationStore
from ai_multi_agent_platform.security.sqlite_authorization import SqliteLocalAuthorizationProvider
from ai_multi_agent_platform.templates import (
    AgentTemplateExporter,
    ContextualTemplateHandlerRegistry,
    JsonTemplateRepository,
    TemplateApplicationService,
    register_agent_template_handlers,
)
from ai_multi_agent_platform.templates.control_plane import register_template_control_plane
from ai_multi_agent_platform.verification import (
    CanonicalVerificationRuntime,
    KernelFileVerificationEvidenceResolver,
    SqliteVerificationCompletionAuthority,
    SqliteVerificationService,
)
from ai_multi_agent_platform.verification.control_plane import register_verification_control_plane
from ai_multi_agent_platform.verification.observability import VerificationTimelineReader
from ai_multi_agent_platform.workspaces import SqliteWorkspaceProvider

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
    workspaces: SqliteWorkspaceProvider
    agents: AgentService
    templates: TemplateApplicationService
    authentication: LocalAuthenticationService
    authorization: SqliteLocalAuthorizationProvider
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
            run_id=run.run_id,
            task_status=persisted_task.status,
            run_status=refreshed.status,
        )


def build_single_node_deployment(config: SingleNodeConfig) -> SingleNodeDeployment:
    """Build the durable Stage-1 profile without optional external services."""

    config.prepare_directories()
    database_dir = config.database_dir

    kernel_repository = SqliteKernelRepository(database_dir / "kernel.sqlite3")
    scopes = SqliteScopeStore(database_dir / "scopes.sqlite3")
    files = LocalFileProvider(config.files_dir, database_dir / "files.sqlite3")
    workspaces = SqliteWorkspaceProvider(
        config.workspaces_dir,
        files,
        database_dir / "workspaces.sqlite3",
    )
    agents = AgentService(JsonAgentRepository(database_dir / "agents.json"))
    template_handlers = ContextualTemplateHandlerRegistry()
    register_agent_template_handlers(template_handlers, agents)
    templates = TemplateApplicationService(
        JsonTemplateRepository(database_dir / "templates.json"),
        template_handlers,
    )
    agent_template_exporter = AgentTemplateExporter(agents, templates.templates)

    execution_workspace = config.executor_dir / _REFERENCE_EXECUTION_WORKSPACE
    execution_workspace.mkdir(parents=True, exist_ok=True)
    orchestrator = ReferenceOrchestrator()
    lifecycle = ExecutorLifecycleBackend(
        ReferenceExecutor(config.executor_dir),
        workspace=_REFERENCE_EXECUTION_WORKSPACE,
        action="echo",
    )
    verification_path = database_dir / "verification.sqlite3"
    verification = SqliteVerificationService(verification_path)
    verification_completion = SqliteVerificationCompletionAuthority(verification, verification_path)
    kernel = PlatformKernel(
        orchestrator=orchestrator,
        lifecycle=lifecycle,
        repository=kernel_repository,
        completion_authority=verification_completion,
    )
    verification_evidence = KernelFileVerificationEvidenceResolver(kernel, kernel_repository, files)
    verification_runtime = CanonicalVerificationRuntime(
        verification_completion, verification_evidence
    )

    authentication_store = SqliteAuthenticationStore(database_dir / "authentication.sqlite3")
    authentication = LocalAuthenticationService(store=authentication_store)
    authorization = SqliteLocalAuthorizationProvider(database_dir / "authorization.sqlite3")

    control_plane = ControlPlane(
        kernel=kernel,
        events=kernel_repository,
        scopes=scopes,
        authorization=authorization,
        workspace_provider=workspaces,
        health_providers=(orchestrator, lifecycle, files),
        automation_state_path=database_dir / "automation.sqlite3",
    )
    register_agent_control_plane(control_plane, agents)
    register_standard_agent_control_plane(control_plane, agents)
    register_template_control_plane(
        control_plane,
        templates,
        agent_exporter=agent_template_exporter,
    )
    register_verification_control_plane(
        control_plane,
        verification,
        verification_completion,
        verification_evidence,
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
        agents=agents,
        templates=templates,
        authentication=authentication,
        authorization=authorization,
        verification=verification,
        verification_completion=verification_completion,
        verification_runtime=verification_runtime,
        kernel=kernel,
        control_plane=control_plane,
        http=http,
        app=app,
    )
