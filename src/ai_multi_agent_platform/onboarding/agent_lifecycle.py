"""Canonical first-run and explicitly bound Agent execution over the Run lifecycle seam."""

from __future__ import annotations

from collections.abc import Mapping

from ai_multi_agent_platform.agents import (
    AgentCapabilityTurn,
    AgentRevision,
    AgentRunStatus,
    AgentRuntime,
)
from ai_multi_agent_platform.agents.execution_profile import (
    AgentExecutionBinding,
    decode_agent_execution_binding,
    decode_agent_step_execution_binding,
)
from ai_multi_agent_platform.capabilities import (
    CapabilityInvoker,
    bind_canonical_capability_invocation,
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
FIRST_RUN_MODEL_REQUIREMENTS = RoutingRequirements(
    modalities=("text",),
    self_hosted_only=True,
)

_PREFLIGHT_TASK_ID = "task_00000000-0000-4000-8000-000000000250"
_PREFLIGHT_RUN_ID = "run_00000000-0000-4000-8000-000000000250"


def preflight_first_run_agent(
    agents: AgentRuntime,
    agent_id: str,
    *,
    project_id: str | None,
    workspace_id: str | None,
) -> AgentRevision:
    """Validate one General Assistant against the exact first-run execution requirements."""

    revision = agents.service.get_agent_revision(agent_id)
    if revision.profile.instructions.role.content is None:
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "first-run General Assistant requires inline role instructions in the reference "
            "execution profile",
        )
    agents.prepare_agent(
        task_id=_PREFLIGHT_TASK_ID,
        run_id=_PREFLIGHT_RUN_ID,
        agent_id=agent_id,
        revision=revision.revision,
        task_model_override=FIRST_RUN_MODEL_REQUIREMENTS,
        task_context={"objective": "first-run execution preflight"},
        project_context={
            "project_id": project_id,
            "workspace_id": workspace_id,
        },
    )
    return revision


class FirstRunAgentLifecycleBackend(LifecycleBackend):
    """Route first-run and explicitly bound Agent Runs through canonical runtime seams.

    A Step binding takes precedence over a Task-wide binding for that exact canonical Step.
    The binding may carry model requirements, capability IDs, Workspace scope and safe Step
    execution context. Unmarked Runs are delegated unchanged.
    """

    def __init__(
        self,
        *,
        delegate: LifecycleBackend,
        tasks: TaskRepository,
        agents: AgentRuntime,
        models: ModelRuntime,
        capability_turn: AgentCapabilityTurn | None = None,
    ) -> None:
        self._delegate = delegate
        self._tasks = tasks
        self._agents = agents
        self._models = models
        self._capability_turn = capability_turn
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
        step_binding = (
            self._step_binding(task.task.metadata, request.subject_id)
            if request.subject_type == "step"
            else None
        )
        generic_binding = step_binding or self._generic_binding(task.task.metadata)
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
            revision = preflight_first_run_agent(
                self._agents,
                agent_id,
                project_id=task.task.project_id,
                workspace_id=workspace_id,
            )
            agent_revision: int | None = revision.revision
            requested_capability_ids: tuple[str, ...] = ()
            task_model_override: RoutingRequirements | None = FIRST_RUN_MODEL_REQUIREMENTS
            available_capability_ids: frozenset[str] = frozenset()
            objective = task.task.description
            task_context: dict[str, JsonValue] = {"objective": objective}
            verification_context: dict[str, JsonValue] = {}
            self_hosted_only = True
        else:
            agent_id = generic_binding.agent_id
            workspace_id = generic_binding.workspace_id
            agent_revision = generic_binding.agent_revision
            requested_capability_ids = generic_binding.capability_ids
            if generic_binding.model_requirements is not None:
                task_model_override = generic_binding.model_requirements
            elif generic_binding.model_config_id is not None:
                task_model_override = RoutingRequirements(
                    explicit_model_id=generic_binding.model_config_id,
                    modalities=("text",),
                )
            else:
                task_model_override = None
            available_capability_ids = (
                frozenset(requested_capability_ids)
                if self._agents.capability_registry is None
                else frozenset()
            )
            objective = generic_binding.objective or task.task.description
            task_context = {
                "objective": objective,
                "input_refs": list(generic_binding.input_refs),
                "output_refs": list(generic_binding.output_refs),
                "expected_evidence": list(generic_binding.expected_evidence),
            }
            verification_context = {
                "policy_refs": list(generic_binding.verification_policy_refs),
                "expected_evidence": list(generic_binding.expected_evidence),
            }
            self_hosted_only = False

        agent_run = await self._agents.start_agent(
            task_id=task.task_id,
            run_id=request.run_id,
            agent_id=agent_id,
            revision=agent_revision,
            task_model_override=task_model_override,
            requested_capability_ids=requested_capability_ids,
            available_capability_ids=available_capability_ids,
            task_context=task_context,
            project_context={
                "project_id": task.task.project_id,
                "workspace_id": workspace_id,
            },
            verification_context=verification_context,
        )
        backend_ref = f"agent-run:{agent_run.agent_run_id}"
        self._backend_refs[request.run_id] = backend_ref
        result_id = new_id("result")

        try:
            resolved_revision = self._agents.service.get_agent_revision(
                agent_run.agent.agent_id,
                agent_run.agent.revision,
            )
            instruction = resolved_revision.profile.instructions.role.content
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

            if agent_run.capability_ids:
                capability_turn = self._resolve_capability_turn()
                turn = await capability_turn.execute(
                    task_id=task.task_id,
                    run_id=request.run_id,
                    agent_id=agent_run.agent.agent_id,
                    model_config_id=agent_run.selected_model_config_id,
                    instruction=instruction,
                    objective=objective,
                    capability_ids=agent_run.capability_ids,
                    capability_versions=dict(agent_run.capability_versions),
                    context=request.context,
                )
                text = turn.text
                model_ref = turn.model_ref
                model_call_refs = turn.model_call_refs
                tool_invocation_refs = turn.tool_invocation_refs
                artifact_refs = turn.artifact_refs
                capability_results = turn.capability_results
                model_usage = turn.model_usage
            else:
                requirements: dict[str, JsonValue] = {
                    "model_config_id": agent_run.selected_model_config_id,
                    "modalities": ["text"],
                }
                if self_hosted_only:
                    requirements["self_hosted_only"] = True
                response = await self._models.generate(
                    ModelRequest(
                        request_id=f"{request.run_id}:model",
                        messages=(instruction, objective),
                        context=request.context,
                        requirements=requirements,
                    )
                )
                text = response.text
                model_ref = response.model_ref
                model_call_refs = (response.request_id,)
                tool_invocation_refs = ()
                artifact_refs = ()
                capability_results = ()
                model_usage = dict(response.usage)
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
            artifact_ids=artifact_refs,
            result_ids=(result_id,),
            model_call_refs=model_call_refs,
            tool_invocation_refs=tool_invocation_refs,
            telemetry={
                "model_usage": model_usage,
                "capability_invocation_count": len(tool_invocation_refs),
            },
        )
        output: dict[str, JsonValue] = {
            "text": text,
            "model_ref": model_ref,
            "agent_run_id": agent_run.agent_run_id,
            "result_id": result_id,
        }
        if capability_results:
            output["capability_results"] = list(capability_results)
            output["tool_invocation_refs"] = list(tool_invocation_refs)
        if artifact_refs:
            output["artifact_refs"] = list(artifact_refs)
        self._snapshots[request.run_id] = ExecutionSnapshot(
            run_id=request.run_id,
            status=ExecutionStatus.SUCCEEDED,
            output=output,
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

    def _resolve_capability_turn(self) -> AgentCapabilityTurn:
        if self._capability_turn is not None:
            return self._capability_turn
        registry = self._agents.capability_registry
        if registry is None:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "Agent execution selected capabilities but AgentRuntime has no CapabilityRegistry",
            )
        self._capability_turn = AgentCapabilityTurn(
            self._models,
            registry,
            CapabilityInvoker(
                registry,
                canonical_binding_hook=bind_canonical_capability_invocation,
            ),
        )
        return self._capability_turn

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

    @staticmethod
    def _step_binding(
        metadata: Mapping[str, JsonValue],
        step_id: str,
    ) -> AgentExecutionBinding | None:
        try:
            return decode_agent_step_execution_binding(metadata, step_id)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                f"invalid canonical Step Agent execution binding: {exc}",
            ) from exc
