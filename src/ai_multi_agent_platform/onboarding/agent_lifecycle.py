"""Canonical first-run and explicitly bound Agent execution over the Run lifecycle seam."""

from __future__ import annotations

from collections.abc import Mapping

from ai_multi_agent_platform.agents import AgentRunStatus, AgentRuntime
from ai_multi_agent_platform.agents.execution_profile import (
    AgentExecutionBinding,
    decode_agent_execution_binding,
)
from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    ContractError,
    ErrorCode,
    ExecutionHandle,
    ExecutionRequest,
    ExecutionSnapshot,
    ExecutionStatus,
    HealthStatus,
    LifecycleBackend,
    ModelRequest,
    OperationContext,
    ProviderDescriptor,
)
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import TaskRepository
from ai_multi_agent_platform.models import ModelRuntime, RoutingRequirements

FIRST_RUN_EXECUTION_PROFILE_KEY = "onboarding.execution_profile"
FIRST_RUN_AGENT_EXECUTION_PROFILE = "general_assistant"
FIRST_RUN_AGENT_ID_KEY = "onboarding.agent_id"
FIRST_RUN_WORKSPACE_ID_KEY = "onboarding.workspace_id"


class FirstRunAgentLifecycleBackend(LifecycleBackend):
    """Route explicitly marked Tasks through AgentRuntime + ModelRuntime.

    The original first-run onboarding profile remains backward-compatible. The same
    backend now also consumes the generic Agent execution metadata contract so normal
    platform features such as Evaluation can bind an exact Agent/model execution
    without inventing a second lifecycle implementation.
    """

    def __init__(
        self,
        *,
        delegate: LifecycleBackend,
        tasks: TaskRepository,
        agents: AgentRuntime,
        models: ModelRuntime,
    ) -> None:
        self._delegate = delegate
        self._tasks = tasks
        self._agents = agents
        self._models = models
        self._snapshots: dict[str, ExecutionSnapshot] = {}
        self._backend_refs: dict[str, str] = {}

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="onboarding-agent-lifecycle",
            provider_type="execution",
            supported_operations=("start", "get", "cancel"),
            health=HealthStatus.HEALTHY,
            available=True,
            resources={"delegate_provider_id": self._delegate.descriptor.provider_id},
        )

    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        task = await self._tasks.get_task(request.context.correlation_id)
        generic_binding = self._generic_binding(task.task.metadata)
        first_run = (
            task.task.metadata.get(FIRST_RUN_EXECUTION_PROFILE_KEY)
            == FIRST_RUN_AGENT_EXECUTION_PROFILE
        )
        if generic_binding is None and not first_run:
            return await self._delegate.start(request)

        existing = self._snapshots.get(request.run_id)
        if existing is not None:
            return self._handle(request.run_id)

        if generic_binding is None:
            agent_id = task.task.metadata.get(FIRST_RUN_AGENT_ID_KEY)
            if not isinstance(agent_id, str) or not agent_id.strip():
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "first-run Agent task is missing its canonical Agent ID",
                )
            workspace_id = task.task.metadata.get(FIRST_RUN_WORKSPACE_ID_KEY)
            if workspace_id is not None and (
                not isinstance(workspace_id, str) or not workspace_id.strip()
            ):
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "first-run Agent task has an invalid Workspace ID",
                )
            agent_revision = None
            requested_capability_ids: tuple[str, ...] = ()
            task_model_override: RoutingRequirements | None = RoutingRequirements(
                modalities=("text",),
                self_hosted_only=True,
            )
            available_capability_ids = frozenset()
            self_hosted_only = True
        else:
            agent_id = generic_binding.agent_id
            workspace_id = generic_binding.workspace_id
            agent_revision = generic_binding.agent_revision
            requested_capability_ids = generic_binding.capability_ids
            task_model_override = (
                None
                if generic_binding.model_config_id is None
                else RoutingRequirements(
                    explicit_model_id=generic_binding.model_config_id,
                    modalities=("text",),
                )
            )
            available_capability_ids = (
                frozenset(requested_capability_ids)
                if self._agents.capability_registry is None
                else frozenset()
            )
            self_hosted_only = False

        agent_run = await self._agents.start_agent(
            task_id=task.task_id,
            run_id=request.run_id,
            agent_id=agent_id,
            revision=agent_revision,
            task_model_override=task_model_override,
            requested_capability_ids=requested_capability_ids,
            available_capability_ids=available_capability_ids,
            task_context={"objective": task.task.description},
            project_context={
                "project_id": task.task.project_id,
                "workspace_id": workspace_id,
            },
        )
        backend_ref = f"agent-run:{agent_run.agent_run_id}"
        self._backend_refs[request.run_id] = backend_ref
        result_id = new_id("result")

        try:
            revision = self._agents.service.get_agent_revision(
                agent_run.agent.agent_id,
                agent_run.agent.revision,
            )
            instruction = revision.profile.instructions.role.content
            if instruction is None:
                raise ContractError(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    "Agent execution requires inline role instructions in the reference "
                    "execution profile",
                )
            if agent_run.selected_model_config_id is None:
                raise ContractError(
                    ErrorCode.NO_COMPATIBLE_ROUTE,
                    "Agent execution did not resolve a canonical model route",
                )
            requirements: dict[str, JsonValue] = {
                "model_config_id": agent_run.selected_model_config_id,
                "modalities": ["text"],
            }
            if self_hosted_only:
                requirements["self_hosted_only"] = True
            response = await self._models.generate(
                ModelRequest(
                    request_id=f"{request.run_id}:model",
                    messages=(instruction, task.task.description),
                    context=request.context,
                    requirements=requirements,
                )
            )
        except ContractError as exc:
            self._agents.finish_agent_run(
                agent_run.agent_run_id,
                status=AgentRunStatus.FAILED,
                error=exc.message,
            )
            self._snapshots[request.run_id] = ExecutionSnapshot(
                run_id=request.run_id,
                status=ExecutionStatus.FAILED,
                output={
                    "error": exc.message,
                    "error_code": exc.code.value,
                    "agent_run_id": agent_run.agent_run_id,
                },
                adapter_metadata=self._metadata(agent_run.agent_run_id),
            )
            return self._handle(request.run_id)

        self._agents.finish_agent_run(
            agent_run.agent_run_id,
            status=AgentRunStatus.SUCCEEDED,
            result_ids=(result_id,),
            model_call_refs=(response.request_id,),
            telemetry={"model_usage": dict(response.usage)},
        )
        self._snapshots[request.run_id] = ExecutionSnapshot(
            run_id=request.run_id,
            status=ExecutionStatus.SUCCEEDED,
            output={
                "text": response.text,
                "model_ref": response.model_ref,
                "agent_run_id": agent_run.agent_run_id,
                "result_id": result_id,
            },
            adapter_metadata=self._metadata(agent_run.agent_run_id),
        )
        return self._handle(request.run_id)

    async def get(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        snapshot = self._snapshots.get(run_id)
        if snapshot is not None:
            return snapshot
        return await self._delegate.get(run_id, context)

    async def cancel(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        snapshot = self._snapshots.get(run_id)
        if snapshot is not None:
            return snapshot
        return await self._delegate.cancel(run_id, context)

    def _handle(self, run_id: str) -> ExecutionHandle:
        return ExecutionHandle(
            run_id=run_id,
            backend_ref=self._backend_refs[run_id],
            adapter_metadata=self._snapshots[run_id].adapter_metadata,
        )

    @staticmethod
    def _metadata(agent_run_id: str) -> tuple[AdapterMetadata, ...]:
        return (
            AdapterMetadata(
                namespace="onboarding-agent-lifecycle",
                values={"agent_run_id": agent_run_id},
            ),
        )

    @staticmethod
    def _generic_binding(metadata: Mapping[str, JsonValue]) -> AgentExecutionBinding | None:
        try:
            return decode_agent_execution_binding(metadata)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                f"invalid canonical Agent execution binding: {exc}",
            ) from exc
