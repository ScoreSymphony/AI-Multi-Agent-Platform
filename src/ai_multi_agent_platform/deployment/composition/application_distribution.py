"""Application build/release composition for the durable single-node profile."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ai_multi_agent_platform.application_distribution import (
    ApplicationBuildLifecycleBackend,
    ApplicationCommandExecutor,
    ApplicationDistributionService,
    ApplicationReleaseGateCoordinator,
    DistributedApplicationBuildLifecycleBackend,
    DistributedBuildTargetMatcher,
    GitHubReleasePublisher,
    JsonApplicationReleaseRepository,
    LocalBuildTargetMatcher,
    ReleaseGatePolicy,
    StaticReleaseGatePolicy,
)
from ai_multi_agent_platform.application_distribution.control_plane import (
    register_application_distribution_control_plane,
)
from ai_multi_agent_platform.configuration import SecretProvider
from ai_multi_agent_platform.connectors import DurableGitHubReleaseConnectorProvider
from ai_multi_agent_platform.control_plane.approval_portability_composition import ControlPlane
from ai_multi_agent_platform.data import LocalFileProvider
from ai_multi_agent_platform.distributed import DistributedRuntime
from ai_multi_agent_platform.evaluation import EvaluationService, SqliteEvaluationRepository
from ai_multi_agent_platform.kernel import PlatformKernel, SqliteKernelRepository
from ai_multi_agent_platform.orchestration import ReferenceOrchestrator
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    AuthorizedLifecycleBackend,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.security.sqlite_authorization import SqliteLocalAuthorizationProvider
from ai_multi_agent_platform.verification import (
    CanonicalVerificationAccess,
    SqliteVerificationService,
)
from ai_multi_agent_platform.workspaces import SqliteRunWorkspaceBindingRepository
from ai_multi_agent_platform.workspaces.compensation import CompensatingSqliteWorkspaceProvider

from ..config import SingleNodeConfig
from .integrations import ConnectorFoundationBundle, EgressConnectorBundle

_APPLICATION_BUILD_PRINCIPAL = "service:application-distribution"
_APPLICATION_BUILD_SECRET_PRINCIPAL = "service:application-build-secrets"
_GITHUB_RELEASE_CONNECTOR_PRINCIPAL = "connector.github-releases"


@dataclass(frozen=True, slots=True)
class ApplicationDistributionBundle:
    """Durable application-release repository, build kernel and distribution service."""

    repository: JsonApplicationReleaseRepository
    build_kernel: PlatformKernel
    service: ApplicationDistributionService


def build_application_distribution(
    config: SingleNodeConfig,
    connector_foundation: ConnectorFoundationBundle,
    connectors: EgressConnectorBundle,
    *,
    kernel_repository: SqliteKernelRepository,
    files: LocalFileProvider,
    workspaces: CompensatingSqliteWorkspaceProvider,
    run_workspace_bindings: SqliteRunWorkspaceBindingRepository,
    authorization: SqliteLocalAuthorizationProvider,
    approval_gate: AuthorizationGate,
    secrets: SecretProvider | None,
    distributed_runtime: DistributedRuntime | None,
    enable_distributed_execution: bool,
    verification: SqliteVerificationService,
    evaluation_repository: SqliteEvaluationRepository,
    evaluation: EvaluationService,
    control_plane: ControlPlane,
    release_gate_policy: ReleaseGatePolicy | None = None,
) -> ApplicationDistributionBundle:
    """Build application distribution with release gates bound at construction time."""

    _install_application_policies(authorization, secrets=secrets is not None)
    repository = JsonApplicationReleaseRepository(config.database_dir / "application-releases.json")
    if enable_distributed_execution and distributed_runtime is not None:
        backend = DistributedApplicationBuildLifecycleBackend(
            repository,
            files,
            run_workspace_bindings,
            distributed_runtime,
        )
        target_matcher = DistributedBuildTargetMatcher(
            distributed_runtime.registry,
            scheduler=distributed_runtime.scheduler,
        )
    else:
        backend = ApplicationBuildLifecycleBackend(
            repository,
            workspaces,
            files,
            run_workspace_bindings,
            ApplicationCommandExecutor(workspaces.materialization_root),
            secret_provider=secrets,
            secret_consumer_ref=_APPLICATION_BUILD_SECRET_PRINCIPAL,
        )
        target_matcher = LocalBuildTargetMatcher()

    build_kernel = PlatformKernel(
        orchestrator=ReferenceOrchestrator(),
        lifecycle=AuthorizedLifecycleBackend(
            backend,
            approval_gate,
            allow_internal_service_reads=True,
        ),
        repository=kernel_repository,
    )
    gate_coordinator = ApplicationReleaseGateCoordinator(
        policy=release_gate_policy or StaticReleaseGatePolicy(),
        files=files,
        verification_access=CanonicalVerificationAccess(verification),
        evaluations=evaluation_repository,
        evaluation_service=evaluation,
    )
    service = ApplicationDistributionService(
        repository,
        kernel=build_kernel,
        files=files,
        workspaces=workspaces,
        run_workspace_bindings=run_workspace_bindings,
        authorization_gate=approval_gate,
        target_matcher=target_matcher,
        gate_coordinator=gate_coordinator,
    )

    if secrets is not None:
        _install_github_release_policy(authorization)
        github_releases = DurableGitHubReleaseConnectorProvider(
            secrets,
            files,
            connector_foundation.repository,
        )
        asyncio.run(connectors.connectors.register_provider(github_releases))
        service.register_publisher(GitHubReleasePublisher(connectors.connectors))

    register_application_distribution_control_plane(control_plane, service)
    return ApplicationDistributionBundle(
        repository=repository,
        build_kernel=build_kernel,
        service=service,
    )


def _install_application_policies(
    authorization: SqliteLocalAuthorizationProvider,
    *,
    secrets: bool,
) -> None:
    if not authorization.has_policy(_APPLICATION_BUILD_PRINCIPAL):
        authorization.register(
            LocalPrincipalPolicy(
                principal_ref=_APPLICATION_BUILD_PRINCIPAL,
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
    if secrets and not authorization.has_policy(_APPLICATION_BUILD_SECRET_PRINCIPAL):
        authorization.register(
            LocalPrincipalPolicy(
                principal_ref=_APPLICATION_BUILD_SECRET_PRINCIPAL,
                actor_types=frozenset({ActorType.SERVICE}),
                allowed_actions=frozenset({AuthorizationAction.INVOKE_SENSITIVE_CAPABILITY}),
                resource_types=frozenset({ResourceType.SECRET_REFERENCE}),
            )
        )


def _install_github_release_policy(authorization: SqliteLocalAuthorizationProvider) -> None:
    if authorization.has_policy(_GITHUB_RELEASE_CONNECTOR_PRINCIPAL):
        return
    authorization.register(
        LocalPrincipalPolicy(
            principal_ref=_GITHUB_RELEASE_CONNECTOR_PRINCIPAL,
            actor_types=frozenset({ActorType.SERVICE}),
            allowed_actions=frozenset({AuthorizationAction.INVOKE_SENSITIVE_CAPABILITY}),
            resource_types=frozenset({ResourceType.SECRET_REFERENCE}),
        )
    )
