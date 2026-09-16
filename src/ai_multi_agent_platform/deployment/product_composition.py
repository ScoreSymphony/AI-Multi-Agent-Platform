"""Product-level single-node composition for supported operator entrypoints."""

from collections.abc import Iterable

from ai_multi_agent_platform.accounting import AccountingService, SQLiteUsageStore
from ai_multi_agent_platform.application_distribution import ReleaseGatePolicy
from ai_multi_agent_platform.configuration import SecretProvider
from ai_multi_agent_platform.distributed import DistributedRuntime
from ai_multi_agent_platform.observability import InMemoryExporter
from ai_multi_agent_platform.onboarding import OnboardingModelAdapter
from ai_multi_agent_platform.repositories import RepositoryDiscoveryResolver

from .config import SingleNodeConfig
from .durable_connectors import (
    build_single_node_deployment as _build_single_node_deployment,
)
from .task_budget_composition import (
    TaskBudgetSingleNodeDeployment,
    extend_single_node_with_task_budgets,
)


def build_product_single_node_deployment(
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
) -> TaskBudgetSingleNodeDeployment:
    """Build the supported product profile with canonical #902 Task-budget enforcement."""

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
        application_release_gate_policy=application_release_gate_policy,
    )
    deployment = extend_single_node_with_task_budgets(
        base,
        config=config,
        accounting_service=effective_accounting,
    )
    _bind_official_first_run(deployment)
    return deployment


def _bind_official_first_run(deployment: TaskBudgetSingleNodeDeployment) -> None:
    # Keep onboarding importable without a deployment -> onboarding -> deployment cycle.
    from ai_multi_agent_platform.onboarding.multi_agent_first_run import (
        ONBOARDING_RUN_MULTI_AGENT_GOLDEN_PATH_COMMAND,
        MultiAgentFirstRunService,
    )

    service = MultiAgentFirstRunService(
        onboarding=deployment.onboarding,
        kernel=deployment.kernel,
        planning=deployment.planning,
        scopes=deployment.scopes,
        agents=deployment.agents,
        authorization=deployment.authorization,
        coordination=deployment.coordination_repository,
        files=deployment.files,
        verification=deployment.verification,
        verification_runtime=deployment.verification_runtime,
    )
    deployment.control_plane.register_command(
        ONBOARDING_RUN_MULTI_AGENT_GOLDEN_PATH_COMMAND,
        service.run,
    )


__all__ = ["build_product_single_node_deployment"]
