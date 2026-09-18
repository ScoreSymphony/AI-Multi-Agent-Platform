"""Execution, Verification, kernel and Evaluation builders."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.accounting import AccountingService
from ai_multi_agent_platform.contracts import (
    ExecutionHandle,
    ExecutionRequest,
    ExecutionSnapshot,
    LifecycleBackend,
    OperationContext,
    ProviderDescriptor,
)
from ai_multi_agent_platform.coordination import (
    DurablePlanStepCoordinator,
    SQLiteCoordinatorRepository,
)
from ai_multi_agent_platform.distributed import (
    DistributedLifecycleBackend,
    DistributedRegistry,
    DistributedRuntime,
    JobRequirements,
)
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
from ai_multi_agent_platform.execution import ExecutorLifecycleBackend, ExecutorRegistry, ReferenceExecutor
from ai_multi_agent_platform.kernel import EventSourcedTaskRepository, PlatformKernel
from ai_multi_agent_platform.observability import (
    ObservabilityEventProvider,
    ObservedExecutor,
    ObservedOrchestrator,
)
from ai_multi_agent_platform.onboarding import FirstRunAgentLifecycleBackend, FirstRunTaskService
from ai_multi_agent_platform.orchestration import OrchestratorRegistry, ReferenceOrchestrator
from ai_multi_agent_platform.security import AuthorizedLifecycleBackend
from ai_multi_agent_platform.verification import (
    CanonicalVerificationRuntime,
    KernelFileVerificationEvidenceResolver,
    SqliteVerificationCompletionAuthority,
    SqliteVerificationService,
    runtime_verification_completion,
)

from ..config import SingleNodeConfig
from .foundation import ObservabilityBundle, SecurityBundle, StorageBundle
from .repositories import RepositoryFoundationBundle
from .services import RuntimeServicesBundle

_REFERENCE_EXECUTION_WORKSPACE = "reference"


class StartupLifecycleBinding(LifecycleBackend):
    """Stable kernel lifecycle participant whose final delegate is bound during composition."""

    def __init__(self, delegate: LifecycleBackend) -> None:
        self._delegate = delegate
        self._finalized = False

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._delegate.descriptor

    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        return await self._delegate.start(request)

    async def get(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        return await self._delegate.get(run_id, context)

    async def cancel(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        return await self._delegate.cancel(run_id, context)

    def bind_final(self, lifecycle: LifecycleBackend) -> None:
        """Replace the startup delegate exactly once before the deployment is exposed."""

        if self._finalized:
            raise RuntimeError("single-node startup lifecycle is already finalized")
        self._delegate = lifecycle
        self._finalized = True


@dataclass(frozen=True, slots=True)
class ExecutionBundle:
    """Reference/distributed execution participants before kernel construction."""

    reference_orchestrator: ReferenceOrchestrator
    reference_executor: ReferenceExecutor
    orchestrator: ObservedOrchestrator
    executor: ObservedExecutor
    orchestrators: OrchestratorRegistry
    executors: ExecutorRegistry
    execution_lifecycle: LifecycleBackend
    pre_authorization_lifecycle: LifecycleBackend
    lifecycle: StartupLifecycleBinding
    distributed_runtime: DistributedRuntime | None


@dataclass(frozen=True, slots=True)
class VerificationBundle:
    """Verification service and its public completion authority."""

    verification: SqliteVerificationService
    completion_authority: SqliteVerificationCompletionAuthority


@dataclass(frozen=True, slots=True)
class KernelBundle:
    """Canonical Task/Run kernel plus kernel-dependent authorities."""

    kernel: PlatformKernel
    coordination_repository: SQLiteCoordinatorRepository
    coordination: DurablePlanStepCoordinator
    verification_evidence: KernelFileVerificationEvidenceResolver
    verification_runtime: CanonicalVerificationRuntime
    first_task: FirstRunTaskService


@dataclass(frozen=True, slots=True)
class EvaluationBundle:
    """Explicit boundary around canonical single-node Evaluation composition."""

    composition: SingleNodeEvaluationComposition


def build_execution(
    config: SingleNodeConfig,
    storage: StorageBundle,
    security: SecurityBundle,
    observability: ObservabilityBundle,
    runtime: RuntimeServicesBundle,
    repositories: RepositoryFoundationBundle,
    *,
    distributed_runtime: DistributedRuntime | None = None,
    enable_distributed_execution: bool = False,
) -> ExecutionBundle:
    """Build execution participants without constructing or mutating the kernel."""

    execution_workspace = storage.workspaces.materialization_root / _REFERENCE_EXECUTION_WORKSPACE
    execution_workspace.mkdir(parents=True, exist_ok=True)
    reference_orchestrator = ReferenceOrchestrator()
    reference_executor = ReferenceExecutor(storage.workspaces.materialization_root)
    orchestrator = ObservedOrchestrator(reference_orchestrator, observability.telemetry)
    executor = ObservedExecutor(reference_executor, observability.telemetry)
    orchestrators = OrchestratorRegistry(
        {orchestrator.descriptor.provider_id: orchestrator}
    )
    executors = ExecutorRegistry()
    executors.register(executor.descriptor.executor_id, executor)

    reference_lifecycle = ExecutorLifecycleBackend(
        executor,
        workspace=_REFERENCE_EXECUTION_WORKSPACE,
        action="echo",
        workspace_resolver=repositories.repository_workspace_execution.resolve_execution_workspace,
        terminal_result_observer=(
            repositories.repository_workspace_execution.observe_terminal_result
        ),
    )
    effective_distributed_runtime = distributed_runtime
    if enable_distributed_execution and effective_distributed_runtime is None:
        effective_distributed_runtime = DistributedRuntime(
            DistributedRegistry(),
            authorization=security.authorization,
        )

    execution_lifecycle: LifecycleBackend = reference_lifecycle
    if enable_distributed_execution:
        if effective_distributed_runtime is None:
            raise AssertionError("distributed execution enabled without a distributed runtime")
        execution_lifecycle = DistributedLifecycleBackend(
            effective_distributed_runtime,
            requirements=JobRequirements(executor_type="reference"),
            workspace_bindings=storage.run_workspace_bindings,
        )

    pre_authorization_lifecycle: LifecycleBackend = FirstRunAgentLifecycleBackend(
        delegate=execution_lifecycle,
        tasks=EventSourcedTaskRepository(storage.kernel_repository),
        agents=runtime.agent_runtime,
        models=runtime.model_runtime,
    )
    authorized_lifecycle = AuthorizedLifecycleBackend(
        pre_authorization_lifecycle,
        security.approval_gate,
        allow_internal_service_reads=True,
    )
    lifecycle = StartupLifecycleBinding(authorized_lifecycle)
    return ExecutionBundle(
        reference_orchestrator=reference_orchestrator,
        reference_executor=reference_executor,
        orchestrator=orchestrator,
        executor=executor,
        orchestrators=orchestrators,
        executors=executors,
        execution_lifecycle=execution_lifecycle,
        pre_authorization_lifecycle=pre_authorization_lifecycle,
        lifecycle=lifecycle,
        distributed_runtime=effective_distributed_runtime,
    )


def build_verification(config: SingleNodeConfig) -> VerificationBundle:
    """Build durable Verification and expose completion authority directly."""

    verification_path = config.database_dir / "verification.sqlite3"
    verification = SqliteVerificationService(
        verification_path,
        require_canonical_subjects=True,
        require_canonical_results=True,
    )
    return VerificationBundle(
        verification=verification,
        completion_authority=SqliteVerificationCompletionAuthority(
            verification,
            verification_path,
        ),
    )


def build_kernel(
    config: SingleNodeConfig,
    storage: StorageBundle,
    observability: ObservabilityBundle,
    runtime: RuntimeServicesBundle,
    execution: ExecutionBundle,
    verification: VerificationBundle,
) -> KernelBundle:
    """Build the canonical kernel from explicit final construction authorities."""

    kernel = PlatformKernel(
        orchestrator=execution.orchestrator,
        lifecycle=execution.lifecycle,
        repository=storage.kernel_repository,
        event_sink=ObservabilityEventProvider(observability.telemetry),
        completion_authority=verification.completion_authority,
        async_completion_authority=runtime_verification_completion(
            verification.completion_authority
        ),
    )
    coordination_repository = SQLiteCoordinatorRepository(
        config.database_dir / "coordination.sqlite3"
    )
    coordination = DurablePlanStepCoordinator(
        repository=coordination_repository,
        kernel=kernel,
        coordinator_id="single-node",
        telemetry=observability.telemetry,
    )
    verification_evidence = KernelFileVerificationEvidenceResolver(
        kernel,
        storage.kernel_repository,
        storage.files,
        runtime.agents.repository,
    )
    verification_runtime = CanonicalVerificationRuntime(
        verification.completion_authority,
        verification_evidence,
    )
    first_task = FirstRunTaskService(
        onboarding=runtime.onboarding,
        kernel=kernel,
        scopes=storage.scopes,
        agents=runtime.agents,
        workspace_provider=storage.workspaces,
    )
    return KernelBundle(
        kernel=kernel,
        coordination_repository=coordination_repository,
        coordination=coordination,
        verification_evidence=verification_evidence,
        verification_runtime=verification_runtime,
        first_task=first_task,
    )


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
) -> EvaluationBundle:
    """Build Evaluation after the canonical kernel is available."""

    evidence_providers: list[EvaluationEvidenceProvider] = [
        CoordinationEvaluationEvidenceProvider(kernel.coordination_repository)
    ]
    if accounting_service is not None:
        evidence_providers.append(AccountingEvaluationEvidenceProvider(accounting_service))
    evidence_providers.append(
        InMemoryObservabilityEvaluationEvidenceProvider(observability.exporter)
    )
    composition = build_single_node_evaluation(
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
    return EvaluationBundle(composition=composition)
