"""Capability providers backed by the canonical distributed Worker runtime."""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.capabilities import (
    ECHO_CAPABILITY_ID,
    CapabilityRegistration,
    CapabilitySpec,
    CapabilityToolProvider,
    SideEffectClassification,
)
from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    Capability,
    CapabilityKind,
    ContractError,
    ErrorCode,
    ExecutionRequest,
    HealthStatus,
    ProviderDescriptor,
    ToolInvocation,
    ToolResult,
)
from ai_multi_agent_platform.workspaces import RunWorkspaceBindingRepository

from .executor_worker import executor_worker_input
from .models import JobRequirements, JobResultStatus, WorkerJobRequest
from .registry import RegistryError
from .runtime import DistributedRuntime
from .scheduler import NoEligibleWorkerError

DISTRIBUTED_ECHO_TOOL_REF = "distributed.executor.echo"


class DistributedExecutorEchoProvider(CapabilityToolProvider):
    """Execute ``tool.echo`` as a Worker Job behind the generic Executor seam.

    The parent Task/Run/Agent identity arrives through the provider-neutral ToolInvocation.
    ``worker_job_id`` is the subordinate execution identity; the provider never creates a
    second canonical Run. Exact Workspace/Snapshot input is recovered from the immutable
    Run binding before dispatch.
    """

    def __init__(
        self,
        runtime: DistributedRuntime,
        *,
        worker_id: str,
        workspace_bindings: RunWorkspaceBindingRepository,
        provider_id: str = "distributed.executor.reference",
        executor_type: str = "reference",
    ) -> None:
        if not provider_id.strip():
            raise ValueError("provider_id must not be blank")
        if not executor_type.strip():
            raise ValueError("executor_type must not be blank")
        self.runtime = runtime
        self.worker_id = worker_id
        self.workspace_bindings = workspace_bindings
        self.provider_id = provider_id
        self.executor_type = executor_type

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id=self.provider_id,
            provider_type="distributed-executor",
            supported_operations=("invoke", "discover"),
            capabilities=(
                Capability(
                    name=ECHO_CAPABILITY_ID,
                    kind=CapabilityKind.TOOL,
                    supported_operations=("invoke",),
                ),
            ),
            health=HealthStatus.HEALTHY,
            available=True,
        )

    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        try:
            worker = self.runtime.registry.get_worker(self.worker_id)
        except RegistryError as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                f"distributed capability Worker is not registered: {self.worker_id}",
                provider_id=self.provider_id,
            ) from exc
        spec = CapabilitySpec(
            capability_id=ECHO_CAPABILITY_ID,
            name="Distributed Executor Echo",
            description="Echo through the canonical Executor and Worker Job boundaries.",
            version="1.0",
            input_schema={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
                "additionalProperties": False,
            },
            output_schema={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
                "additionalProperties": False,
            },
            tags=("distributed", "executor", "reference"),
            side_effects=SideEffectClassification.NONE,
            health=HealthStatus.HEALTHY,
            available=True,
        )
        return (
            CapabilityRegistration(
                capability=spec,
                provider_id=self.provider_id,
                provider_tool_ref=DISTRIBUTED_ECHO_TOOL_REF,
                priority=200,
                node_id=worker.node_id,
                worker_id=worker.worker_id,
            ),
        )

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        if invocation.tool_ref != DISTRIBUTED_ECHO_TOOL_REF:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"distributed executor provider does not expose {invocation.tool_ref!r}",
                provider_id=self.provider_id,
            )
        if invocation.task_id is None or invocation.run_id is None or invocation.agent_id is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "distributed capability invocation requires canonical Task/Run/Agent trace IDs",
                provider_id=self.provider_id,
            )

        binding = await self.workspace_bindings.get(invocation.run_id)
        if binding is None:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "distributed capability Run has no canonical Workspace binding",
                provider_id=self.provider_id,
            )
        if binding.task_id != invocation.task_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "distributed capability Workspace binding belongs to another Task",
                provider_id=self.provider_id,
            )

        arguments = invocation.arguments_json()
        message = arguments.get("message")
        if not isinstance(message, str):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "distributed echo message must be a string",
                provider_id=self.provider_id,
            )
        worker_job_id = _worker_job_id(self.worker_id, invocation.invocation_id)
        job = WorkerJobRequest(
            worker_job_id=worker_job_id,
            execution=ExecutionRequest(
                run_id=invocation.run_id,
                subject_type="task",
                subject_id=invocation.task_id,
                context=invocation.context,
                input=executor_worker_input(
                    action="echo",
                    arguments={"text": message},
                ),
            ),
            requirements=JobRequirements(
                executor_type=self.executor_type,
                capability_refs=(ECHO_CAPABILITY_ID,),
                preferred_worker_ids=(self.worker_id,),
            ),
            workspace_ref=binding.workspace_id,
            snapshot_ref=binding.workspace_snapshot_id,
            timeout_seconds=invocation.context.control.timeout_seconds,
            idempotency_key=f"capability:{invocation.invocation_id}",
        )
        try:
            record = await self.runtime.dispatch_to_worker(job, self.worker_id)
            result = await self.runtime.result(worker_job_id)
        except NoEligibleWorkerError as exc:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                str(exc),
                retryable=True,
                provider_id=self.provider_id,
            ) from exc
        except RegistryError as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                str(exc),
                provider_id=self.provider_id,
            ) from exc

        if result is None or result.execution is None:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "distributed Worker did not return a terminal execution result",
                provider_id=self.provider_id,
            )
        if result.status is not JobResultStatus.SUCCEEDED:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                result.error_category or "distributed executor capability failed",
                provider_id=self.provider_id,
            )
        text = result.execution.output.get("text")
        if not isinstance(text, str):
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "distributed echo execution returned no text",
                provider_id=self.provider_id,
            )
        worker = self.runtime.registry.get_worker(record.worker_id)
        return ToolResult(
            invocation_id=invocation.invocation_id,
            output={"message": text},
            artifact_refs=result.artifact_refs,
            evidence_refs=(worker_job_id,),
            adapter_metadata=(
                AdapterMetadata(
                    namespace="distributed-capability",
                    values={
                        "worker_job_id": worker_job_id,
                        "worker_id": record.worker_id,
                        "node_id": worker.node_id,
                        "run_id": invocation.run_id,
                        "task_id": invocation.task_id,
                        "agent_id": invocation.agent_id,
                        "workspace_id": binding.workspace_id,
                        "workspace_snapshot_id": binding.workspace_snapshot_id,
                    },
                ),
            ),
        )


def _worker_job_id(worker_id: str, invocation_id: str) -> str:
    value = uuid5(
        NAMESPACE_URL,
        f"ai-multi-agent-platform:capability-worker-job:{worker_id}:{invocation_id}",
    )
    return f"worker_job_{value}"


__all__ = ["DISTRIBUTED_ECHO_TOOL_REF", "DistributedExecutorEchoProvider"]
