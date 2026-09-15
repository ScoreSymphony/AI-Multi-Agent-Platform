"""Self-hosted deployment profiles and operator composition helpers."""

from collections.abc import Iterable
from dataclasses import dataclass, fields
from typing import Any

from ai_multi_agent_platform.accounting import AccountingService, SQLiteUsageStore
from ai_multi_agent_platform.application_distribution import (
    ApplicationReleaseGateCoordinator,
    ReleaseGatePolicy,
    StaticReleaseGatePolicy,
)
from ai_multi_agent_platform.configuration import SecretProvider
from ai_multi_agent_platform.distributed import DistributedRuntime
from ai_multi_agent_platform.execution.budgets import (
    SQLiteTaskBudgetStore,
    TaskBudgetCapabilityInvoker,
    TaskBudgetEnforcementService,
    TaskBudgetModelRuntime,
    TaskBudgetPolicyMutationService,
    as_capability_invoker,
    as_model_runtime,
    register_task_budget_control_plane,
)
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
    SingleNodeDeployment as DurableSingleNodeDeployment,
)
from .durable_connectors import (
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


@dataclass(slots=True)
class SingleNodeDeployment(DurableSingleNodeDeployment):
    """Public durable deployment plus canonical #902 Task-budget authorities."""

    task_budgets: TaskBudgetEnforcementService
    task_budget_mutations: TaskBudgetPolicyMutationService


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
    """Build the public durable profile with release gates and autonomous Task budgets.

    #76 remains the sole usage/cost ledger. #902 contributes only policy, reservation and admission
    state and therefore reuses the exact AccountingService instance composed into the Control Plane.
    Normal public composition supplies durable local SQLite stores when callers do not inject their
    own accounting backend.
    """

    config.prepare_directories()
    effective_accounting = accounting_service or AccountingService(
        SQLiteUsageStore(config.database_dir / "accounting.sqlite3")
    )
    base = _build_single_node_deployment(
        config,
        onboarding_model_adapters=onboarding_model_adapters,
        secret_provider=secret_provider,
        accounting_service=effective_accounting,
        observability_exporter=observability_exporter,
        distributed_runtime=distributed_runtime,
        enable_distributed_execution=enable_distributed_execution,
        repository_discovery_resolver=repository_discovery_resolver,
    )

    task_budgets = TaskBudgetEnforcementService(
        SQLiteTaskBudgetStore(config.database_dir / "task-execution-budgets.sqlite3"),
        effective_accounting,
    )
    task_budget_mutations = TaskBudgetPolicyMutationService(
        task_budgets,
        base.approval_gate,
    )
    register_task_budget_control_plane(
        base.control_plane,
        task_budgets,
        task_budget_mutations,
    )

    # Replanning already owns a narrow admission seam on the #902 branch. Bind the same durable
    # authority used by model/tool execution rather than maintaining an independent counter.
    base.planning._budget_admission = task_budgets  # noqa: SLF001 - composition seam

    budgeted_models = TaskBudgetModelRuntime(base.model_runtime, task_budgets)
    base.model_runtime = as_model_runtime(budgeted_models)
    base.context.lifecycle._models = base.model_runtime  # noqa: SLF001 - composition seam

    # The Context lifecycle creates the canonical Agent Capability turn. Decorate its provider
    # boundaries rather than relying on each Agent caller to remember to perform admission.
    capability_turn = base.context.lifecycle._capability_turn  # noqa: SLF001 - composition seam
    if capability_turn is not None:
        capability_turn._models = base.model_runtime  # noqa: SLF001 - composition seam
        capability_turn._budget_admission = None  # noqa: SLF001 - avoid duplicate reservations
        capability_turn._invoker = as_capability_invoker(  # noqa: SLF001 - composition seam
            TaskBudgetCapabilityInvoker(capability_turn._invoker, task_budgets)  # noqa: SLF001
        )

    # Automatic Verification reviewers are Task-scoped autonomous model work as well. They retain
    # their existing workflow authority while sharing the same decorated ModelRuntime boundary.
    reviewer_executor = getattr(base.automatic_reviewer, "_executor", None)
    if reviewer_executor is not None and hasattr(reviewer_executor, "_models"):
        reviewer_executor._models = base.model_runtime  # noqa: SLF001

    base_values: dict[str, Any] = {
        field.name: getattr(base, field.name) for field in fields(DurableSingleNodeDeployment)
    }
    deployment = SingleNodeDeployment(
        **base_values,
        task_budgets=task_budgets,
        task_budget_mutations=task_budget_mutations,
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
