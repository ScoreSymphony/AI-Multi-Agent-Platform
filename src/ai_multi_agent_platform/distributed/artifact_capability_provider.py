"""Distributed capability that writes a canonical Workspace artifact through an Executor Worker."""

from __future__ import annotations

from ai_multi_agent_platform.capabilities import (
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
from .tool_lineage import bind_worker_job_to_tool_invocation

WORKSPACE_ARTIFACT_CAPABILITY_ID = "tool.workspace.write_artifact"
DISTRIBUTED_WORKSPACE_ARTIFACT_TOOL_REF = "distributed.executor.write_artifact"


class DistributedExecutorArtifactProvider(CapabilityToolProvider):
    """Write one text artifact through canonical Capability -> Executor -> Worker boundaries."""

    def __init__(
        self,
        runtime: DistributedRuntime,
        *,
        worker_id: str,
        workspace_bindings: RunWorkspaceBindingRepository,
        provider_id: str = "distributed.executor.reference-artifact",
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
                    name=WORKSPACE_ARTIFACT_CAPABILITY_ID,
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
                f"distributed artifact Worker is not registered: {self.worker_id}",
                provider_id=self.provider_id,
            ) from exc
        spec = CapabilitySpec(
            capability_id=WORKSPACE_ARTIFACT_CAPABILITY_ID,
            name="Write Workspace Artifact",
            description=(
                "Write UTF-8 text into the exact Run Workspace through the canonical Executor "
                "and Worker boundaries and return the resulting canonical Artifact reference."
            ),
            version="1.0",
            input_schema={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            output_schema={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"path": {"type": "string", "minLength": 1}},
                "required": ["path"],
                "additionalProperties": False,
            },
            tags=("distributed", "executor", "workspace", "artifact", "reference"),
            side_effects=SideEffectClassification.LOCAL_WRITE,
            health=HealthStatus.HEALTHY,
            available=True,
        )
        return (
            CapabilityRegistration(
                capability=spec,
                provider_id=self.provider_id,
                provider_tool_ref=DISTRIBUTED_WORKSPACE_ARTIFACT_TOOL_REF,
                priority=200,
                node_id=worker.node_id,
                worker_id=worker.worker_id,
            ),
        )

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        if invocation.tool_ref != DISTRIBUTED_WORKSPACE_ARTIFACT_TOOL_REF:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"distributed artifact provider does not expose {invocation.tool_ref!r}",
                provider_id=self.provider_id,
            )
        if invocation.task_id is None or invocation.run_id is None or invocation.agent_id is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "distributed artifact invocation requires canonical Task/Run/Agent trace IDs",
                provider_id=self.provider_id,
            )
        tool_invocation_id = invocation.context.causation_id
        if tool_invocation_id is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "distributed artifact invocation requires canonical ToolInvocation causation",
                provider_id=self.provider_id,
            )

        binding = await self.workspace_bindings.get(invocation.run_id)
        if binding is None:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "distributed artifact Run has no canonical Workspace binding",
                provider_id=self.provider_id,
            )
        if binding.task_id != invocation.task_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "distributed artifact Workspace binding belongs to another Task",
                provider_id=self.provider_id,
            )

        arguments = invocation.arguments_json()
        path = arguments.get("path")
        content = arguments.get("content")
        if not isinstance(path, str) or not path.strip() or not isinstance(content, str):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "distributed artifact path/content are invalid",
                provider_id=self.provider_id,
            )

        try:
            job = bind_worker_job_to_tool_invocation(
                WorkerJobRequest(
                    execution=ExecutionRequest(
                        run_id=invocation.run_id,
                        subject_type="task",
                        subject_id=invocation.task_id,
                        context=invocation.context,
                        input=executor_worker_input(
                            action="write_artifact",
                            arguments={"path": path, "content": content},
                        ),
                    ),
                    requirements=JobRequirements(
                        executor_type=self.executor_type,
                        capability_refs=(WORKSPACE_ARTIFACT_CAPABILITY_ID,),
                        preferred_worker_ids=(self.worker_id,),
                    ),
                    workspace_ref=binding.workspace_id,
                    snapshot_ref=binding.workspace_snapshot_id,
                    timeout_seconds=invocation.context.control.timeout_seconds,
                    idempotency_key=f"capability:{tool_invocation_id}",
                ),
                tool_invocation_id,
            )
        except ValueError as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "distributed artifact causation is not a canonical ToolInvocation identity",
                provider_id=self.provider_id,
            ) from exc

        worker_job_id = job.worker_job_id
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
                "distributed artifact Worker did not return a terminal execution result",
                provider_id=self.provider_id,
            )
        if result.status is not JobResultStatus.SUCCEEDED:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                result.error_category or "distributed artifact execution failed",
                provider_id=self.provider_id,
            )
        returned_path = result.execution.output.get("artifact")
        if returned_path != path:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "distributed artifact execution returned a mismatched artifact path",
                provider_id=self.provider_id,
            )
        if not result.artifact_refs:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "distributed artifact execution returned no canonical Artifact reference",
                provider_id=self.provider_id,
            )

        worker = self.runtime.registry.get_worker(record.worker_id)
        return ToolResult(
            invocation_id=invocation.invocation_id,
            output={"path": path},
            artifact_refs=result.artifact_refs,
            evidence_refs=(worker_job_id, *result.artifact_refs),
            adapter_metadata=(
                AdapterMetadata(
                    namespace="distributed-workspace-artifact",
                    values={
                        "tool_invocation_id": tool_invocation_id,
                        "worker_job_id": worker_job_id,
                        "worker_id": record.worker_id,
                        "node_id": worker.node_id,
                        "run_id": invocation.run_id,
                        "task_id": invocation.task_id,
                        "agent_id": invocation.agent_id,
                        "workspace_id": binding.workspace_id,
                        "workspace_snapshot_id": binding.workspace_snapshot_id,
                        "artifact_ids": list(result.artifact_refs),
                    },
                ),
            ),
        )


__all__ = [
    "DISTRIBUTED_WORKSPACE_ARTIFACT_TOOL_REF",
    "DistributedExecutorArtifactProvider",
    "WORKSPACE_ARTIFACT_CAPABILITY_ID",
]
