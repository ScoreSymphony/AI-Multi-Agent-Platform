"""Self-hosted deployment profiles and operator composition helpers."""

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
    build_single_node_deployment,
)
from .egress_bindings import EgressDeploymentBindings
from .handoff_composition import (
    HandoffDeploymentComposition,
    build_single_node_handoff_composition,
)
from .startup_recovery import (
    SingleNodeStartupRecoveryResult,
    load_startup_recovery_report,
    reconcile_single_node_startup,
    require_blocked_startup_run,
)

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
    "load_single_node_config",
    "load_startup_recovery_report",
    "parse_advanced_deployment_profile",
    "reconcile_single_node_startup",
    "require_blocked_startup_run",
]
