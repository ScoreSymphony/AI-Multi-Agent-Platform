"""Production-shaped single-node composition for issue #39."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.control_plane import (
    AuthenticatedControlPlaneHTTP,
    ControlPlane,
    ControlPlaneASGI,
)
from ai_multi_agent_platform.control_plane.sqlite_scope import SqliteScopeStore
from ai_multi_agent_platform.data import LocalFileProvider
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
from ai_multi_agent_platform.workspaces import SqliteWorkspaceProvider

from .config import SingleNodeConfig

_REFERENCE_EXECUTION_WORKSPACE = "reference"


@dataclass(slots=True)
class SingleNodeDeployment:
    """All long-lived components for one self-hosted single-machine process."""

    config: SingleNodeConfig
    kernel_repository: SqliteKernelRepository
    scopes: SqliteScopeStore
    files: LocalFileProvider
    workspaces: SqliteWorkspaceProvider
    authentication: LocalAuthenticationService
    authorization: SqliteLocalAuthorizationProvider
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
            account = self.authentication.store.user_by_username(username)
            if account is None or len(self.authentication.store.users) != 1:
                raise ValueError(
                    "deployment bootstrap is available only for the first existing local user"
                )
            self.authentication.authenticate_password(username, password)

        if not self.authorization.has_policy(account.user_id):
            self.authorization.register(
                LocalPrincipalPolicy(
                    principal_ref=account.user_id,
                    actor_types=frozenset({ActorType.HUMAN}),
                    administrator=True,
                )
            )
        return account


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

    execution_workspace = config.executor_dir / _REFERENCE_EXECUTION_WORKSPACE
    execution_workspace.mkdir(parents=True, exist_ok=True)
    orchestrator = ReferenceOrchestrator()
    lifecycle = ExecutorLifecycleBackend(
        ReferenceExecutor(config.executor_dir),
        workspace=_REFERENCE_EXECUTION_WORKSPACE,
        action="echo",
    )
    kernel = PlatformKernel(
        orchestrator=orchestrator,
        lifecycle=lifecycle,
        repository=kernel_repository,
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
        authentication=authentication,
        authorization=authorization,
        kernel=kernel,
        control_plane=control_plane,
        http=http,
        app=app,
    )
