"""Automatic Verification reviewer composition for the durable single-node profile."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.agents import AgentRuntime
from ai_multi_agent_platform.data import LocalFileProvider
from ai_multi_agent_platform.kernel import (
    EventSourcedRunRepository,
    EventSourcedTaskRepository,
    PlatformKernel,
    SqliteKernelRepository,
)
from ai_multi_agent_platform.models import ModelRuntime
from ai_multi_agent_platform.verification import (
    CanonicalVerificationRuntime,
    SqliteVerificationCompletionAuthority,
    SqliteVerificationService,
)
from ai_multi_agent_platform.verification.agent_repair import (
    KernelAgentRepairExecutor,
    ProducerAgentRepairBindingProvider,
)
from ai_multi_agent_platform.verification.async_agent_workflow import (
    AsyncAutomaticReviewerWorkflow,
)
from ai_multi_agent_platform.verification.output_workflow import (
    AutomaticReviewerOutputCoordinator,
    PolicyMetadataReviewerResolver,
    install_automatic_reviewer_output_observer,
)
from ai_multi_agent_platform.verification.reference_reviewer import ModelRuntimeReviewerExecutor
from ai_multi_agent_platform.verification.repair import VerificationRepairRuntime
from ai_multi_agent_platform.verification.reviewer_input import (
    KernelFileReviewerSubjectInputProvider,
)
from ai_multi_agent_platform.verification.reviewer_recovery import (
    AutomaticReviewerStartupReconciler,
)


@dataclass(frozen=True, slots=True)
class ReviewerBundle:
    """Automatic reviewer workflow plus its startup reconciler."""

    workflow: AsyncAutomaticReviewerWorkflow
    recovery: AutomaticReviewerStartupReconciler


def build_reviewer(
    *,
    kernel: PlatformKernel,
    kernel_repository: SqliteKernelRepository,
    files: LocalFileProvider,
    verification: SqliteVerificationService,
    completion: SqliteVerificationCompletionAuthority,
    verification_runtime: CanonicalVerificationRuntime,
    agent_runtime: AgentRuntime,
    model_runtime: ModelRuntime,
) -> ReviewerBundle:
    """Build and install the canonical automatic-review output workflow."""

    reviewer_inputs = KernelFileReviewerSubjectInputProvider(
        tasks=EventSourcedTaskRepository(kernel_repository),
        runs=EventSourcedRunRepository(kernel_repository),
        files=files,
    )
    repair_runtime = VerificationRepairRuntime(
        verification,
        completion,
        kernel,
        binding_provider=ProducerAgentRepairBindingProvider(),
    )
    workflow = AsyncAutomaticReviewerWorkflow(
        runtime=verification_runtime,
        completion=completion,
        agents=agent_runtime,
        resolver=PolicyMetadataReviewerResolver(completion),
        executor=ModelRuntimeReviewerExecutor(
            agents=agent_runtime,
            models=model_runtime,
            inputs=reviewer_inputs,
        ),
        repair_runtime=repair_runtime,
        repair_executor=KernelAgentRepairExecutor(kernel),
    )
    recovery = AutomaticReviewerStartupReconciler(
        workflow=workflow,
        agents=agent_runtime,
        verification=verification,
        tasks=kernel,
    )
    output = AutomaticReviewerOutputCoordinator(
        kernel=kernel,
        runtime=verification_runtime,
        completion=completion,
        reviewer=workflow,
    )
    install_automatic_reviewer_output_observer(kernel, output)
    return ReviewerBundle(workflow=workflow, recovery=recovery)
