"""Execution-layer construction for the single-node deployment."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.contracts import LifecycleBackend
from ai_multi_agent_platform.distributed import (
    DistributedLifecycleBackend,
    DistributedRegistry,
    DistributedRuntime,
    JobRequirements,
)
from ai_multi_agent_platform.execution import ExecutorLifecycleBackend, ReferenceExecutor
from ai_multi_agent_platform.kernel import EventSourcedTaskRepository
from ai_multi_agent_platform.observability import ObservedExecutor, ObservedOrchestrator
from ai_multi_agent_platform.onboarding import FirstRunAgentLifecycleBackend
from ai_multi_agent_platform.orchestration import ReferenceOrchestrator
from ai_multi_agent_platform.security import AuthorizedLifecycleBackend

from .observability import ObservabilityBundle
from .repositories import RepositoryFoundationBundle
from .runtime_services import RuntimeServicesBundle
from .security import SecurityBundle
from .storage import StorageBundle

_REFERENCE_EXECUTION_WORKSPACE = "reference"


@dataclass(frozen=True, slots=True)
class ExecutionBundle:
    """Reference execution authorities and the selected canonical lifecycle backend."""

    reference_orchestrator: ReferenceOrchestrator
    reference_executor: ReferenceExecutor
    orchestrator: ObservedOrchestrator
    lifecycle: LifecycleBackend
    distributed_runtime: DistributedRuntime | None


def build_execution(
    storage: StorageBundle,
    security: SecurityBundle,
    observability: ObservabilityBundle,
    runtime: RuntimeServicesBundle,
    repositories: RepositoryFoundationBundle,
    *,
    distributed_runtime: DistributedRuntime | None = None,
    enable_distributed_execution: bool = False,
) -> ExecutionBundle:
    """Build execution without constructing or mutating the canonical PlatformKernel."""

    effective_distributed_runtime = distributed_runtime
    if enable_distributed_execution and effective_distributed_runtime is None:
        effective_distributed_runtime = DistributedRuntime(
            DistributedRegistry(),
            authorization=security.authorization,
        )

    execution_workspace = storage.workspaces.materialization_root / _REFERENCE_EXECUTION_WORKSPACE
    execution_workspace.mkdir(parents=True, exist_ok=True)
    reference_orchestrator = ReferenceOrchestrator()
    reference_executor = ReferenceExecutor(storage.workspaces.materialization_root)
    orchestrator = ObservedOrchestrator(reference_orchestrator, observability.telemetry)
    observed_executor = ObservedExecutor(reference_executor, observability.telemetry)
    execution_lifecycle: LifecycleBackend = ExecutorLifecycleBackend(
        observed_executor,
        workspace=_REFERENCE_EXECUTION_WORKSPACE,
        action="echo",
        workspace_resolver=repositories.workspace_execution.resolve_execution_workspace,
        terminal_result_observer=repositories.workspace_execution.observe_terminal_result,
    )
    if enable_distributed_execution:
        if effective_distributed_runtime is None:
            raise AssertionError("distributed execution enabled without a distributed runtime")
        execution_lifecycle = DistributedLifecycleBackend(
            effective_distributed_runtime,
            requirements=JobRequirements(executor_type="reference"),
            workspace_bindings=storage.run_workspace_bindings,
        )

    lifecycle = AuthorizedLifecycleBackend(
        FirstRunAgentLifecycleBackend(
            delegate=execution_lifecycle,
            tasks=EventSourcedTaskRepository(storage.kernel_repository),
            agents=runtime.agent_runtime,
            models=runtime.model_runtime,
        ),
        security.approval_gate,
        allow_internal_service_reads=True,
    )
    return ExecutionBundle(
        reference_orchestrator=reference_orchestrator,
        reference_executor=reference_executor,
        orchestrator=orchestrator,
        lifecycle=lifecycle,
        distributed_runtime=effective_distributed_runtime,
    )
