"""Context-first Agent lifecycle integration for the normal production-shaped Run path."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from ai_multi_agent_platform.agents import (
    AgentCapabilityTurn,
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
from ai_multi_agent_platform.onboarding.agent_lifecycle import (
    FIRST_RUN_AGENT_EXECUTION_PROFILE,
    FIRST_RUN_AGENT_ID_KEY,
    FIRST_RUN_EXECUTION_PROFILE_KEY,
    FIRST_RUN_MODEL_REQUIREMENTS,
    FIRST_RUN_WORKSPACE_ID_KEY,
)
from ai_multi_agent_platform.security import ActorIdentity, ActorType

from .models import ContextBudget
from .operational import OperationalContextBoundAgentRuntime
from .rendering import ContextRenderingError
from .resolver import ContextAssemblyRequest, ContextResolutionError
from .source_adapters import ContextSourceAdapterBinding, OperationalContextAssemblyService


@dataclass(frozen=True, slots=True)
class ContextLifecycleSourceRequest:
    """Canonical identities needed to choose operational Context source bindings."""

    execution: ExecutionRequest
    task_id: str
    run_id: str
    agent_id: str
    agent_revision: int
    project_id: str | None
    workspace_id: str | None
    plan_id: str | None
    step_id: str | None
    objective: str
    execution_binding: AgentExecutionBinding | None


ContextBindingFactory = Callable[
    [ContextLifecycleSourceRequest],
    Sequence[ContextSourceAdapterBinding],
]
SkillBundleResolver = Callable[[ContextLifecycleSourceRequest], tuple[str, str] | None]
ActorResolver = Callable[[OperationContext], ActorIdentity]

_DEFAULT_CONTEXT_BUDGET = ContextBudget(
    max_tokens=64_000,
    max_bytes=256 * 1024,
    max_items=128,
)


class CanonicalContextAgentLifecycleBackend(LifecycleBackend):
    """Execute Agent-bound Runs through exactly one canonical Context Bundle.

    Unmarked executions continue through the supplied delegate. Marked Agent executions no longer
    pass legacy ``task_context``/``project_context`` dictionaries to orchestrator adapters: source
    domains contribute through #590 adapters, #15 resolves visibility, the immutable Bundle derives
    #10 routing constraints, and the exact egress-approved rendering becomes the model input.
    """

    def __init__(
        self,
        *,
        delegate: LifecycleBackend,
        tasks: TaskRepository,
        agents: AgentRuntime,
        models: ModelRuntime,
        assembly: OperationalContextAssemblyService,
        context_runtime: OperationalContextBoundAgentRuntime,
        binding_factory: ContextBindingFactory,
        skill_bundle_resolver: SkillBundleResolver | None = None,
        actor_resolver: ActorResolver | None = None,
        budget: ContextBudget = _DEFAULT_CONTEXT_BUDGET,
        capability_turn: AgentCapabilityTurn | None = None,
    ) -> None:
        self._delegate = delegate
        self._tasks = tasks
        self._agents = agents
        self._models = models
        self._assembly = assembly
        self._context_runtime = context_runtime
        self._binding_factory = binding_factory
        self._skill_bundle_resolver = skill_bundle_resolver
        self._actor_resolver = actor_resolver or _actor_from_operation
        self._budget = budget
        self._capability_turn = capability_turn
        self._snapshots: dict[str, ExecutionSnapshot] = {}
        self._backend_refs: dict[str, str] = {}
        self._context_refs: dict[str, tuple[str, str]] = {}

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="canonical-context-agent-lifecycle",
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
        binding = step_binding or self._generic_binding(task.task.metadata)
        first_run = (
            task.task.metadata.get(FIRST_RUN_EXECUTION_PROFILE_KEY)
            == FIRST_RUN_AGENT_EXECUTION_PROFILE
        )
        if binding is None and not first_run:
            return await self._delegate.start(request)

        existing = self._snapshots.get(request.run_id)
        if existing is not None:
            return self._handle(request.run_id)

        if binding is None:
            agent_id = _required_metadata_string(
                task.task.metadata,
                FIRST_RUN_AGENT_ID_KEY,
                "first-run Agent task is missing its canonical Agent ID",
            )
            workspace_id = _optional_metadata_string(
                task.task.metadata,
                FIRST_RUN_WORKSPACE_ID_KEY,
                "first-run Agent task has an invalid Workspace ID",
            )
            revision = self._agents.service.get_agent_revision(agent_id)
            agent_revision = revision.revision
            requested_capability_ids: tuple[str, ...] = ()
            task_model_override: RoutingRequirements | None = FIRST_RUN_MODEL_REQUIREMENTS
            available_capability_ids: frozenset[str] = frozenset()
            objective = task.task.description
            verification_context: dict[str, JsonValue] = {}
            self_hosted_only = True
        else:
            agent_id = binding.agent_id
            workspace_id = binding.workspace_id
            agent_revision = binding.agent_revision
            requested_capability_ids = binding.capability_ids
            if binding.model_requirements is not None:
                task_model_override = binding.model_requirements
            elif binding.model_config_id is not None:
                task_model_override = RoutingRequirements(
                    explicit_model_id=binding.model_config_id,
                    modalities=("text",),
                )
            else:
                task_model_override = None
            available_capability_ids = (
                frozenset(requested_capability_ids)
                if self._agents.capability_registry is None
                else frozenset()
            )
            objective = binding.objective or task.task.description
            verification_context = {
                "policy_refs": list(binding.verification_policy_refs),
                "expected_evidence": list(binding.expected_evidence),
            }
            self_hosted_only = False

        revision = self._agents.service.get_agent_revision(agent_id, agent_revision)
        actor = self._actor_resolver(request.context)
        source_request = ContextLifecycleSourceRequest(
            execution=request,
            task_id=task.task_id,
            run_id=request.run_id,
            agent_id=revision.agent_id,
            agent_revision=revision.revision,
            project_id=task.task.project_id,
            workspace_id=workspace_id,
            plan_id=task.plan_ref,
            step_id=request.subject_id if request.subject_type == "step" else None,
            objective=objective,
            execution_binding=binding,
        )
        adapter_bindings = tuple(self._binding_factory(source_request))
        skill_ref = (
            self._skill_bundle_resolver(source_request)
            if self._skill_bundle_resolver is not None
            else None
        )

        try:
            bundle = await self._assembly.assemble(
                ContextAssemblyRequest(
                    task_id=task.task_id,
                    run_id=request.run_id,
                    agent_id=revision.agent_id,
                    agent_revision=revision.revision,
                    actor=actor,
                    operation=request.context,
                    candidates=(),
                    budget=self._budget,
                    workspace_id=workspace_id,
                    plan_id=task.plan_ref,
                    step_id=source_request.step_id,
                    skill_bundle_id=skill_ref[0] if skill_ref is not None else None,
                    skill_bundle_digest=skill_ref[1] if skill_ref is not None else None,
                ),
                bindings=adapter_bindings,
            )
            context_execution = await self._context_runtime.start_agent(
                bundle=bundle,
                operation=request.context,
                task_model_override=task_model_override,
                requested_capability_ids=requested_capability_ids,
                available_capability_ids=available_capability_ids,
                verification_context=verification_context,
            )
        except ContextResolutionError as exc:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                f"canonical Context resolution blocked Agent execution: {exc}",
                details={
                    "context_blocker": exc.blocker.reason.value,
                    "source_type": exc.blocker.source.source_type.value,
                    "source_id": exc.blocker.source.source_id,
                },
            ) from exc
        except ContextRenderingError as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                f"canonical Context rendering failed: {exc}",
            ) from exc

        agent_run = context_execution.agent_run
        self._context_refs[request.run_id] = (
            context_execution.binding.context_bundle_id,
            context_execution.binding.context_bundle_digest,
        )
        backend_ref = f"agent-run:{agent_run.agent_run_id}"
        self._backend_refs[request.run_id] = backend_ref
        result_id = new_id("result")

        instruction = context_execution.model_input.system_instruction
        model_objective = context_execution.model_input.user_message
        if agent_run.selected_model_config_id is None:
            raise ContractError(
                ErrorCode.NO_COMPATIBLE_ROUTE,
                "Context-bound Agent execution did not resolve a canonical model route",
            )

        try:
            if agent_run.capability_ids:
                turn = await self._resolve_capability_turn().execute(
                    task_id=task.task_id,
                    run_id=request.run_id,
                    agent_id=agent_run.agent.agent_id,
                    model_config_id=agent_run.selected_model_config_id,
                    instruction=instruction,
                    objective=model_objective,
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
                        messages=(instruction, model_objective),
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
                    "context_bundle_id": bundle.context_bundle_id,
                    "context_bundle_digest": bundle.digest,
                },
                adapter_metadata=self._metadata(request.run_id, agent_run.agent_run_id),
            )
            return self._handle(request.run_id)

        telemetry = dict(agent_run.telemetry)
        telemetry.update(
            {
                "model_usage": model_usage,
                "capability_invocation_count": len(tool_invocation_refs),
                "context_bundle_id": bundle.context_bundle_id,
                "context_bundle_digest": bundle.digest,
            }
        )
        self._agents.finish_agent_run(
            agent_run.agent_run_id,
            status=AgentRunStatus.SUCCEEDED,
            artifact_ids=artifact_refs,
            result_ids=(result_id,),
            model_call_refs=model_call_refs,
            tool_invocation_refs=tool_invocation_refs,
            telemetry=telemetry,
        )
        output: dict[str, JsonValue] = {
            "text": text,
            "model_ref": model_ref,
            "agent_run_id": agent_run.agent_run_id,
            "result_id": result_id,
            "context_bundle_id": bundle.context_bundle_id,
            "context_bundle_digest": bundle.digest,
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
            adapter_metadata=self._metadata(request.run_id, agent_run.agent_run_id),
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

    def _metadata(self, run_id: str, agent_run_id: str) -> tuple[AdapterMetadata, ...]:
        context_ref = self._context_refs.get(run_id)
        values: dict[str, JsonValue] = {"agent_run_id": agent_run_id}
        if context_ref is not None:
            values.update(
                {
                    "context_bundle_id": context_ref[0],
                    "context_bundle_digest": context_ref[1],
                }
            )
        return (AdapterMetadata(namespace="canonical-context-agent-lifecycle", values=values),)

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


def _actor_from_operation(context: OperationContext) -> ActorIdentity:
    if context.owner_id is None or context.owner_type is None:
        raise ContractError(
            ErrorCode.UNAUTHORIZED,
            "Context-bound Agent execution requires canonical owner identity",
        )
    actor_type = {
        "user": ActorType.HUMAN,
        "service": ActorType.SERVICE,
        "agent": ActorType.AGENT,
        "worker": ActorType.WORKER,
        "automation": ActorType.AUTOMATION,
        "integration": ActorType.INTEGRATION,
        "organization": ActorType.HUMAN,
        "team": ActorType.HUMAN,
    }.get(context.owner_type)
    if actor_type is None:
        raise ContractError(
            ErrorCode.UNAUTHORIZED,
            f"unsupported Context actor owner_type: {context.owner_type!r}",
        )
    return ActorIdentity(context.owner_id, actor_type)


def _required_metadata_string(
    metadata: Mapping[str, JsonValue],
    key: str,
    message: str,
) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, message)
    return value


def _optional_metadata_string(
    metadata: Mapping[str, JsonValue],
    key: str,
    message: str,
) -> str | None:
    value = metadata.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, message)
    return value


__all__ = [
    "CanonicalContextAgentLifecycleBackend",
    "ContextBindingFactory",
    "ContextLifecycleSourceRequest",
]
