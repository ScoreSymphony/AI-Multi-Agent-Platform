"""Self-hosted deployment profiles and operator composition helpers."""

from collections.abc import Iterable

from ai_multi_agent_platform.accounting import AccountingService
from ai_multi_agent_platform.application_distribution import (
    ApplicationReleaseGateCoordinator,
    ReleaseGatePolicy,
    StaticReleaseGatePolicy,
)
from ai_multi_agent_platform.configuration import SecretProvider
from ai_multi_agent_platform.distributed import DistributedRuntime
from ai_multi_agent_platform.observability import InMemoryExporter
from ai_multi_agent_platform.onboarding import OnboardingModelAdapter
from ai_multi_agent_platform.repositories import RepositoryDiscoveryResolver
from ai_multi_agent_platform.verification import CanonicalVerificationAccess

from .advanced_profiles import (
    AdvancedDeploymentProfile,
    AdvancedDeploymentProfileError,
    ControlPlaneBinding,
    DeploymentNode,
    OptionalServiceBinding,
    WorkerHostBinding,
    load_advanced_deployment_profile,
    parse_advanced_deployment_profile,
)
from .config import SingleNodeConfig, load_single_node_config
from .distributed_control_plane import (
    DeploymentWorkerProtocolService,
    build_worker_protocol_app,
)
from .durable_connectors import (
    SingleNodeDeployment,
    SingleNodeSmokeResult,
)
from .durable_connectors import (
    build_single_node_deployment as _build_single_node_deployment,
)
from .egress_bindings import EgressDeploymentBindings
from .handoff_composition import (
    HandoffDeploymentComposition,
    build_single_node_handoff_composition,
)
from .release_gate_policy import load_application_release_gate_policy
from .startup_recovery import (
    SingleNodeStartupRecoveryResult,
    load_startup_recovery_report,
    reconcile_single_node_startup,
    require_blocked_startup_run,
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
    application_release_gate_policy: ReleaseGatePolicy | None = None,
) -> SingleNodeDeployment:
    """Build the public durable profile and attach canonical application release gates.

    The durable connector composition remains the low-level owner of application build/release
    infrastructure. This public wrapper binds release-gate projection to the already composed
    canonical Verification and Evaluation stores. Gate semantics stay explicitly injectable so
    application distribution does not invent a second policy authority.
    """

    deployment = _build_single_node_deployment(
        config,
        onboarding_model_adapters=onboarding_model_adapters,
        secret_provider=secret_provider,
        accounting_service=accounting_service,
        observability_exporter=observability_exporter,
        distributed_runtime=distributed_runtime,
        enable_distributed_execution=enable_distributed_execution,
        repository_discovery_resolver=repository_discovery_resolver,
    )
    gate_policy = (
        application_release_gate_policy
        if application_release_gate_policy is not None
        else StaticReleaseGatePolicy()
    )
    deployment.application_releases.gate_coordinator = ApplicationReleaseGateCoordinator(
        policy=gate_policy,
        files=deployment.files,
        verification_access=CanonicalVerificationAccess(deployment.verification),
        evaluations=deployment.evaluation_repository,
        evaluation_service=deployment.evaluation,
    )
    return deployment


__all__ = [
    "AdvancedDeploymentProfile",
    "AdvancedDeploymentProfileError",
    "ControlPlaneBinding",
    "DeploymentNode",
    "DeploymentWorkerProtocolService",
    "EgressDeploymentBindings",
    "HandoffDeploymentComposition",
    "OptionalServiceBinding",
    "SingleNodeConfig",
    "SingleNodeDeployment",
    "SingleNodeSmokeResult",
    "SingleNodeStartupRecoveryResult",
    "WorkerHostBinding",
    "build_single_node_deployment",
    "build_single_node_handoff_composition",
    "build_worker_protocol_app",
    "load_advanced_deployment_profile",
    "load_application_release_gate_policy",
    "load_single_node_config",
    "load_startup_recovery_report",
    "parse_advanced_deployment_profile",
    "reconcile_single_node_startup",
    "require_blocked_startup_run",
]
