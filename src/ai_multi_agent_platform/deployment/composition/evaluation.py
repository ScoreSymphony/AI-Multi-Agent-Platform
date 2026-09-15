"""Evaluation composition boundary for the supported single-node profile."""

from __future__ import annotations

from ai_multi_agent_platform.accounting import AccountingService
from ai_multi_agent_platform.evaluation import (
    AccountingEvaluationEvidenceProvider,
    CoordinationEvaluationEvidenceProvider,
    EvaluationEvidenceProvider,
    InMemoryObservabilityEvaluationEvidenceProvider,
)
from ai_multi_agent_platform.evaluation.single_node import (
    SingleNodeEvaluationComposition,
    build_single_node_evaluation,
)

from ..config import SingleNodeConfig
from .execution import ExecutionBundle
from .kernel import KernelBundle
from .observability import ObservabilityBundle
from .runtime_services import RuntimeServicesBundle
from .security import SecurityBundle
from .storage import StorageBundle


def build_evaluation(
    config: SingleNodeConfig,
    storage: StorageBundle,
    security: SecurityBundle,
    observability: ObservabilityBundle,
    runtime: RuntimeServicesBundle,
    execution: ExecutionBundle,
    kernel: KernelBundle,
    *,
    accounting_service: AccountingService | None = None,
) -> SingleNodeEvaluationComposition:
    """Build Evaluation from completed kernel/runtime authorities without Control Plane mutation."""

    evidence_providers: list[EvaluationEvidenceProvider] = [
        CoordinationEvaluationEvidenceProvider(kernel.coordination_repository)
    ]
    if accounting_service is not None:
        evidence_providers.append(AccountingEvaluationEvidenceProvider(accounting_service))
    evidence_providers.append(
        InMemoryObservabilityEvaluationEvidenceProvider(observability.exporter)
    )
    return build_single_node_evaluation(
        database_path=config.database_dir / "evaluation.sqlite3",
        asset_dir=config.evaluation_dir,
        kernel=kernel.kernel,
        agents=runtime.agents.repository,
        agent_runtime=runtime.agent_runtime,
        models=runtime.models,
        model_runtime=runtime.model_runtime,
        orchestrator=execution.reference_orchestrator,
        executor=execution.reference_executor,
        files=storage.files,
        workspaces=storage.workspaces,
        project_id=storage.evaluation_project_id,
        run_workspace_bindings=storage.run_workspace_bindings,
        evidence_providers=tuple(evidence_providers),
        approval_reader=security.approval_gate.runtime_approvals,
        distributed_runtime=execution.distributed_runtime,
    )
