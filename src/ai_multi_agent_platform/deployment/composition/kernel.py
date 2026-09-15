"""Canonical Task/Run kernel construction for single-node deployment."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.coordination import DurablePlanStepCoordinator, SQLiteCoordinatorRepository
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.observability import ObservabilityEventProvider
from ai_multi_agent_platform.onboarding import FirstRunTaskService
from ai_multi_agent_platform.verification import (
    CanonicalVerificationRuntime,
    KernelFileVerificationEvidenceResolver,
    runtime_verification_completion,
)

from ..config import SingleNodeConfig
from .execution import ExecutionBundle
from .observability import ObservabilityBundle
from .runtime_services import RuntimeServicesBundle
from .storage import StorageBundle
from .verification import VerificationBundle


@dataclass(frozen=True, slots=True)
class KernelBundle:
    """Canonical kernel and kernel-dependent coordination/Verification authorities."""

    kernel: PlatformKernel
    coordination_repository: SQLiteCoordinatorRepository
    coordination: DurablePlanStepCoordinator
    verification_evidence: KernelFileVerificationEvidenceResolver
    verification_runtime: CanonicalVerificationRuntime
    first_task: FirstRunTaskService


def build_kernel(
    config: SingleNodeConfig,
    storage: StorageBundle,
    execution: ExecutionBundle,
    verification: VerificationBundle,
    runtime: RuntimeServicesBundle,
    observability: ObservabilityBundle,
) -> KernelBundle:
    """Build the kernel once from explicit lifecycle and completion-authority inputs."""

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
    )
    return KernelBundle(
        kernel=kernel,
        coordination_repository=coordination_repository,
        coordination=coordination,
        verification_evidence=verification_evidence,
        verification_runtime=verification_runtime,
        first_task=first_task,
    )
