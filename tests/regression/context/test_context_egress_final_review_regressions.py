from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace
from typing import Any

import pytest

from ai_multi_agent_platform.agents import AgentRevisionRef, ModelFallbackPolicy
from ai_multi_agent_platform.context import ContextBudget
from ai_multi_agent_platform.context.models import (
    ContextBudgetUsage,
    ContextBundle,
    ContextDataClassification,
    ContextEntry,
    ContextEntryRole,
    ContextSourceRef,
    ContextSourceType,
)
from ai_multi_agent_platform.context.operational import OperationalContextBoundAgentRuntime
from ai_multi_agent_platform.contracts import (
    ContractError,
    DataClassification,
    EgressDecision,
    EgressOutcome,
    EgressReasonCode,
    EgressTarget,
    EgressTargetKind,
    EgressTargetPosture,
    ErrorCode,
    ModelRequest,
    ModelSelection,
    OperationContext,
)
from ai_multi_agent_platform.deployment.handoff_composition import (
    _OperationalProductionHandoffRuntime,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.models import RoutingRequirements
from ai_multi_agent_platform.security import ActorIdentity, ActorType, EgressGate


def _bundle(*, classification: ContextDataClassification) -> ContextBundle:
    content = "final review context"
    encoded = content.encode("utf-8")
    entry = ContextEntry(
        ordinal=0,
        source=ContextSourceRef(ContextSourceType.TASK, new_id("task")),
        role=ContextEntryRole.CONTEXT,
        selection_reason="final review regression",
        mandatory=True,
        content_digest=hashlib.sha256(encoded).hexdigest(),
        inline_content=content,
        data_classification=classification,
        estimated_tokens=5,
        content_bytes=len(encoded),
    )
    return ContextBundle(
        context_bundle_id=new_id("context_bundle"),
        task_id=new_id("task"),
        run_id=new_id("run"),
        agent_id=new_id("agent"),
        agent_revision=1,
        entries=(entry,),
        omissions=(),
        budget=ContextBudget(max_tokens=64),
        usage=ContextBudgetUsage(
            estimated_tokens=entry.estimated_tokens,
            bytes=entry.content_bytes,
            items=1,
        ),
        resolver_version="final-review/v1",
        policy_version="final-review/v1",
        actor_ref="user:final-review",
    )


class _RoutingAgentRuntime:
    def __init__(self) -> None:
        self.service = self

    def get_agent_revision(self, agent_id: str, revision: int) -> Any:
        return SimpleNamespace(
            agent_id=agent_id,
            revision=revision,
            profile=SimpleNamespace(
                model=SimpleNamespace(fallback=ModelFallbackPolicy.ROUTE),
            ),
        )

    def _effective_model_requirements(
        self,
        agent: Any,
        task_override: RoutingRequirements | None,
        runtime_requirements: RoutingRequirements,
    ) -> RoutingRequirements:
        del agent, task_override, runtime_requirements
        return RoutingRequirements()


class _CandidateRouter:
    def _candidate_configs(self, requirements: RoutingRequirements) -> tuple[Any, ...]:
        del requirements
        return (
            SimpleNamespace(config_id="blocked-context-model"),
            SimpleNamespace(config_id="allowed-context-model"),
        )


class _CandidateModelRuntime:
    def __init__(self) -> None:
        self.router = _CandidateRouter()
        self.requests: list[ModelRequest] = []

    async def select(self, request: ModelRequest) -> ModelSelection:
        self.requests.append(request)
        model_id = request.requirements.get("model_config_id")
        assert isinstance(model_id, str)
        provider_id = (
            "blocked-context-provider"
            if model_id == "blocked-context-model"
            else "allowed-context-provider"
        )
        return ModelSelection(provider_id=provider_id, model_ref=model_id)


class _ProviderContextTargetResolver:
    def resolve(self, spec: Any, bundle: ContextBundle) -> EgressTarget:
        del bundle
        assert isinstance(spec.selected_provider_id, str)
        return EgressTarget(
            kind=EgressTargetKind.CONTEXT_EXPORT,
            target_id=spec.selected_provider_id,
            posture=EgressTargetPosture.EXTERNAL,
        )


class _ProviderContextPolicy:
    version = "final-review-context-policy/v1"

    def __init__(self) -> None:
        self.seen_targets: list[str] = []

    async def evaluate(self, request: Any) -> EgressDecision:
        self.seen_targets.append(request.target.target_id)
        allowed = request.target.target_id == "allowed-context-provider"
        return EgressDecision(
            request_id=request.request_id,
            outcome=EgressOutcome.ALLOW if allowed else EgressOutcome.DENY,
            target_kind=request.target.kind,
            target_id=request.target.target_id,
            effective_classification=request.classification,
            reason_code=(
                EgressReasonCode.ALLOWED if allowed else EgressReasonCode.TARGET_POLICY_DENIED
            ),
            policy_version=self.version,
            audit_metadata={"target_posture": request.target.effective_posture.value},
        )


def test_context_routing_filters_context_export_policy_before_model_pin() -> None:
    async def scenario() -> None:
        bundle = _bundle(classification=ContextDataClassification.RESTRICTED)
        model_runtime = _CandidateModelRuntime()
        policy = _ProviderContextPolicy()
        runtime = OperationalContextBoundAgentRuntime(
            _RoutingAgentRuntime(),  # type: ignore[arg-type]
            model_runtime=model_runtime,  # type: ignore[arg-type]
            egress_gate=EgressGate(policy),  # type: ignore[arg-type]
            target_resolver=_ProviderContextTargetResolver(),
        )

        requirements = await runtime._classification_aware_route(  # noqa: SLF001
            bundle=bundle,
            operation=OperationContext(correlation_id=bundle.task_id),
            task_model_override=None,
            runtime_requirements=RoutingRequirements(),
        )

        assert requirements.explicit_model_id == "allowed-context-model"
        assert [request.requirements["model_config_id"] for request in model_runtime.requests] == [
            "blocked-context-model",
            "allowed-context-model",
        ]
        assert policy.seen_targets == [
            "blocked-context-provider",
            "allowed-context-provider",
        ]
        assert all(
            request.requirements["data_classification"] == DataClassification.RESTRICTED.value
            for request in model_runtime.requests
        )

    asyncio.run(scenario())


class _TaskRepository:
    def __init__(self, task_id: str, project_id: str) -> None:
        self.task_id = task_id
        self.project_id = project_id

    async def get_task(self, task_id: str) -> Any:
        assert task_id == self.task_id
        return SimpleNamespace(task=SimpleNamespace(project_id=self.project_id))


class _HandoffService:
    def __init__(self, handoff: Any) -> None:
        self.handoff = handoff

    def get_handoff(self, handoff_id: str, revision: int) -> Any:
        assert handoff_id == self.handoff.handoff_id
        assert revision == self.handoff.revision
        return self.handoff


class _CapturingAssembly:
    def __init__(self) -> None:
        self.operation: OperationContext | None = None

    async def assemble(self, request: Any, *, adapters: Any) -> Any:
        del adapters
        self.operation = request.operation
        return SimpleNamespace()


class _CapturingContextRuntime:
    def __init__(self) -> None:
        self.operation: OperationContext | None = None

    async def start_agent_binding(self, *, operation: OperationContext, **kwargs: Any) -> Any:
        del kwargs
        self.operation = operation
        return SimpleNamespace(agent_run=SimpleNamespace(), binding=SimpleNamespace())


class _CapturingHandoffRuntime(_OperationalProductionHandoffRuntime):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.consume_operation: OperationContext | None = None

    async def consume_handoff(self, handoff_id: str, revision: int, **kwargs: Any) -> Any:
        self.consume_operation = kwargs["operation"]
        return SimpleNamespace(handoff=self.service.get_handoff(handoff_id, revision))

    def _execution_identity(self, consumer: Any, consumer_agent: Any) -> tuple[Any, None]:
        del consumer_agent
        return consumer, None

    def _require_bundle_contains_handoff(self, bundle: Any, runtime_context: Any) -> None:
        del bundle, runtime_context

    def _require_agent_run_matches(self, record: Any, consumer: Any, execution_agent: Any) -> None:
        del record, consumer, execution_agent


def _handoff_runtime_fixture() -> tuple[
    _CapturingHandoffRuntime,
    str,
    str,
    str,
    AgentRevisionRef,
    ActorIdentity,
    _CapturingAssembly,
    _CapturingContextRuntime,
]:
    task_id = new_id("task")
    project_id = new_id("project")
    run_id = new_id("run")
    agent_id = new_id("agent")
    consumer = AgentRevisionRef(agent_id, 1)
    actor = ActorIdentity(agent_id, ActorType.AGENT)
    handoff = SimpleNamespace(
        handoff_id=new_id("handoff"),
        revision=1,
        task_id=task_id,
        content=SimpleNamespace(plan_id=None, consumer_step_id=None),
        content_digest="handoff-digest",
    )
    assembly = _CapturingAssembly()
    context_runtime = _CapturingContextRuntime()
    runtime = _CapturingHandoffRuntime(
        service=_HandoffService(handoff),  # type: ignore[arg-type]
        coordinated=object(),  # type: ignore[arg-type]
        repository=object(),  # type: ignore[arg-type]
        references=object(),  # type: ignore[arg-type]
        agents=object(),  # type: ignore[arg-type]
        context_assembly=assembly,  # type: ignore[arg-type]
        context_runtime=context_runtime,  # type: ignore[arg-type]
        audit=object(),  # type: ignore[arg-type]
        tasks=_TaskRepository(task_id, project_id),  # type: ignore[arg-type]
    )
    return (
        runtime,
        handoff.handoff_id,
        project_id,
        run_id,
        consumer,
        actor,
        assembly,
        context_runtime,
    )


def test_handoff_binds_canonical_task_project_before_context_egress_path() -> None:
    async def scenario() -> None:
        runtime, handoff_id, project_id, run_id, consumer, actor, assembly, context_runtime = (
            _handoff_runtime_fixture()
        )
        operation = OperationContext(correlation_id="handoff-final-review")

        await runtime.start_consumer(
            handoff_id,
            1,
            consuming_run_id=run_id,
            consumer=consumer,
            consumer_actor=actor,
            operation=operation,
            budget=ContextBudget(max_tokens=32),
        )

        assert runtime.consume_operation is not None
        assert runtime.consume_operation.project_id == project_id
        assert assembly.operation is not None
        assert assembly.operation.project_id == project_id
        assert context_runtime.operation is not None
        assert context_runtime.operation.project_id == project_id

    asyncio.run(scenario())


def test_handoff_rejects_conflicting_caller_project_before_consumption() -> None:
    async def scenario() -> None:
        runtime, handoff_id, _project_id, run_id, consumer, actor, _assembly, _context_runtime = (
            _handoff_runtime_fixture()
        )
        with pytest.raises(ContractError) as exc_info:
            await runtime.start_consumer(
                handoff_id,
                1,
                consuming_run_id=run_id,
                consumer=consumer,
                consumer_actor=actor,
                operation=OperationContext(
                    correlation_id="handoff-final-review-conflict",
                    project_id=new_id("project"),
                ),
                budget=ContextBudget(max_tokens=32),
            )
        assert exc_info.value.code is ErrorCode.NOT_FOUND
        assert runtime.consume_operation is None

    asyncio.run(scenario())
