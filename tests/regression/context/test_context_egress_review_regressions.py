from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace
from typing import Any

from ai_multi_agent_platform.agents import ModelFallbackPolicy, OrchestratorMapping
from ai_multi_agent_platform.context.models import (
    ContextBudget,
    ContextBudgetUsage,
    ContextBundle,
    ContextDataClassification,
    ContextEntry,
    ContextEntryRole,
    ContextSourceRef,
    ContextSourceType,
)
from ai_multi_agent_platform.context.operational import (
    OperationalContextBoundAgentRuntime,
    _OperationalContextMapper,
)
from ai_multi_agent_platform.context.rendering import ReferenceContextRenderer
from ai_multi_agent_platform.contracts import (
    DataClassification,
    EgressDecision,
    EgressOutcome,
    EgressReasonCode,
    EgressTarget,
    EgressTargetKind,
    EgressTargetPosture,
    ErrorCode,
    ExecutionHandle,
    ExecutionRequest,
    ModelRequest,
    ModelSelection,
    OperationContext,
    ProviderDescriptor,
)
from ai_multi_agent_platform.deployment.context_operationalization import (
    _TaskProjectScopeLifecycleBackend,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.models import RoutingRequirements
from ai_multi_agent_platform.security import AuthorizedLifecycleBackend, EgressGate


def _bundle(*, classification: ContextDataClassification) -> ContextBundle:
    content = "canonical context evidence"
    encoded = content.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    entry = ContextEntry(
        ordinal=0,
        source=ContextSourceRef(ContextSourceType.TASK, new_id("task")),
        role=ContextEntryRole.CONTEXT,
        selection_reason="regression coverage",
        mandatory=True,
        content_digest=digest,
        inline_content=content,
        data_classification=classification,
        estimated_tokens=6,
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
        resolver_version="test-resolver/v1",
        policy_version="test-policy/v1",
        actor_ref="user:review-regression",
    )


class _CapturingLifecycle:
    descriptor = ProviderDescriptor(provider_id="test-lifecycle", provider_type="execution")

    def __init__(self) -> None:
        self.request: ExecutionRequest | None = None

    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        self.request = request
        return ExecutionHandle(run_id=request.run_id)

    async def get(self, run_id: str, context: OperationContext) -> Any:
        raise NotImplementedError

    async def cancel(self, run_id: str, context: OperationContext) -> Any:
        raise NotImplementedError


class _ProjectScopedGate:
    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        self.seen_project_id: str | None = None

    async def enforce(self, action: Any) -> None:
        self.seen_project_id = action.context.operation.project_id
        if self.seen_project_id != self.project_id:
            raise AssertionError("authorization ran before canonical Task project binding")


class _TaskRepository:
    def __init__(self, task_id: str, project_id: str) -> None:
        self.task_id = task_id
        self.project_id = project_id

    async def get_task(self, task_id: str) -> Any:
        assert task_id == self.task_id
        return SimpleNamespace(task=SimpleNamespace(project_id=self.project_id))


def test_task_project_scope_is_bound_before_lifecycle_authorization() -> None:
    async def scenario() -> None:
        task_id = new_id("task")
        project_id = new_id("project")
        run_id = new_id("run")
        delegate = _CapturingLifecycle()
        gate = _ProjectScopedGate(project_id)
        authorized = AuthorizedLifecycleBackend(
            delegate,  # type: ignore[arg-type]
            gate,  # type: ignore[arg-type]
        )
        composed = _TaskProjectScopeLifecycleBackend(
            authorized,
            _TaskRepository(task_id, project_id),  # type: ignore[arg-type]
        )

        await composed.start(
            ExecutionRequest(
                run_id=run_id,
                subject_type="task",
                subject_id=task_id,
                context=OperationContext(
                    correlation_id=task_id,
                    owner_type="user",
                    owner_id="review-regression",
                ),
            )
        )

        assert gate.seen_project_id == project_id
        assert delegate.request is not None
        assert delegate.request.context.project_id == project_id

    asyncio.run(scenario())


class _RoutingAgentRuntime:
    def __init__(self) -> None:
        self.service = self

    def get_agent_revision(self, agent_id: str, revision: int) -> Any:
        del agent_id, revision
        return SimpleNamespace(
            profile=SimpleNamespace(
                model=SimpleNamespace(fallback=ModelFallbackPolicy.ROUTE),
            )
        )

    def _effective_model_requirements(
        self,
        agent: Any,
        task_override: RoutingRequirements | None,
        runtime_requirements: RoutingRequirements,
    ) -> RoutingRequirements:
        del agent, task_override, runtime_requirements
        return RoutingRequirements(explicit_model_id="blocked-model")


class _ClassificationAwareModelRuntime:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def select(self, request: ModelRequest) -> ModelSelection:
        self.requests.append(request)
        assert request.requirements["data_classification"] == DataClassification.RESTRICTED.value
        if request.requirements.get("model_config_id") == "blocked-model":
            from ai_multi_agent_platform.contracts import ContractError

            raise ContractError(
                ErrorCode.NO_COMPATIBLE_ROUTE,
                "blocked by egress candidate policy",
            )
        return ModelSelection(provider_id="allowed-provider", model_ref="allowed-model")


def test_context_classification_is_applied_before_model_fallback_selection() -> None:
    async def scenario() -> None:
        bundle = _bundle(classification=ContextDataClassification.RESTRICTED)
        model_runtime = _ClassificationAwareModelRuntime()
        runtime = OperationalContextBoundAgentRuntime(
            _RoutingAgentRuntime(),  # type: ignore[arg-type]
            model_runtime=model_runtime,  # type: ignore[arg-type]
        )

        requirements = await runtime._classification_aware_route(  # noqa: SLF001
            bundle=bundle,
            operation=OperationContext(correlation_id=bundle.task_id),
            task_model_override=None,
            runtime_requirements=RoutingRequirements(),
        )

        assert requirements.explicit_model_id == "allowed-model"
        assert len(model_runtime.requests) == 2
        assert model_runtime.requests[0].requirements["model_config_id"] == "blocked-model"
        assert "model_config_id" not in model_runtime.requests[1].requirements
        assert all(
            request.requirements["data_classification"] == "restricted"
            for request in model_runtime.requests
        )

    asyncio.run(scenario())


class _ResolvedPosturePolicy:
    async def evaluate(self, request: Any) -> EgressDecision:
        return EgressDecision(
            request_id=request.request_id,
            outcome=EgressOutcome.ALLOW,
            target_kind=request.target.kind,
            target_id=request.target.target_id,
            effective_classification=request.classification,
            reason_code=EgressReasonCode.ALLOWED,
            policy_version="resolved-posture-test/v1",
            audit_metadata={"target_posture": EgressTargetPosture.INTERNAL.value},
        )


class _FixedTargetResolver:
    def resolve(self, spec: Any, bundle: ContextBundle) -> EgressTarget:
        del spec, bundle
        return EgressTarget(
            kind=EgressTargetKind.CONTEXT_EXPORT,
            target_id="provider-review-regression",
            posture=EgressTargetPosture.EXTERNAL,
        )


class _ContextAdapter:
    adapter_id = "review-regression-adapter"

    async def map_agent_with_context(
        self,
        spec: Any,
        bundle: ContextBundle,
        rendered: Any,
    ) -> OrchestratorMapping:
        del spec, bundle, rendered
        return OrchestratorMapping(
            adapter_id=self.adapter_id,
            runtime_ref="review-regression-runtime",
        )


def test_context_mapping_records_policy_resolved_egress_posture() -> None:
    async def scenario() -> None:
        bundle = _bundle(classification=ContextDataClassification.INTERNAL)
        adapter = _ContextAdapter()
        gate = EgressGate(_ResolvedPosturePolicy())  # type: ignore[arg-type]
        from ai_multi_agent_platform.context.egress import ContextBundleEgressExporter

        mapper = _OperationalContextMapper(
            bundle=bundle,
            adapter=adapter,  # type: ignore[arg-type]
            renderer=ReferenceContextRenderer(),
            exporter=ContextBundleEgressExporter(
                egress_gate=gate,
                renderer=ReferenceContextRenderer(),
            ),
            operation=OperationContext(correlation_id=bundle.task_id),
            target_resolver=_FixedTargetResolver(),
            content_provider=None,
        )
        spec = SimpleNamespace(
            task_id=bundle.task_id,
            run_id=bundle.run_id,
            agent_revision=SimpleNamespace(
                agent_id=bundle.agent_id,
                revision=bundle.agent_revision,
            ),
            task_context={},
            project_context={},
        )

        mapping = await mapper.map_agent(spec)  # type: ignore[arg-type]

        assert mapping.metadata["context_egress_target_id"] == "provider-review-regression"
        assert mapping.metadata["context_egress_target_posture"] == "internal"

    asyncio.run(scenario())
