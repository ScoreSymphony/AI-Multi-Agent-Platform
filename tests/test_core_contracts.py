from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    AuthorizationRequest,
    Capability,
    CapabilityKind,
    ContractError,
    ErrorCode,
    ExecutionRequest,
    ExecutionStatus,
    HealthStatus,
    KnowledgeQuery,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelSelection,
    NodeDescriptor,
    OperationContext,
    OperationControl,
    PlanRequest,
    PlanStepProposal,
    PlatformEvent,
    ProviderDescriptor,
    RetryMode,
    ToolInvocation,
    WorkerDescriptor,
)
from ai_multi_agent_platform.domain import (
    Event,
    OwnerRef,
    Run,
    RunStatus,
    Task,
    Tool,
    new_id,
)
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeCapabilityProvider,
    FakeEventProvider,
    FakeFailure,
    FakeFileProvider,
    FakeKnowledgeProvider,
    FakeLifecycleBackend,
    FakeMemoryProvider,
    FakeModelProvider,
    FakeModelRouter,
    FakeNodeProvider,
    FakeOrchestrator,
    FakeToolProvider,
    FakeWorkerProvider,
    assert_canonical_error,
    assert_knowledge_provider_contract,
    assert_lifecycle_backend_contract,
    assert_model_provider_contract,
    assert_namespaced_adapter_metadata,
    assert_node_provider_contract,
    assert_provider_contract,
    assert_tool_provider_contract,
    assert_worker_provider_contract,
)

OWNER = OwnerRef(type="user", id="user-test")
CTX = OperationContext(correlation_id="corr-test", owner_type="user", owner_id="user-test")


class AlternateModelProvider(ModelProvider):
    """Second implementation proving the model seam is not shaped around one fake."""

    descriptor = ProviderDescriptor(
        provider_id="alternate-model",
        provider_type="model",
        supported_operations=("generate",),
        capabilities=(
            Capability(
                name="model.text",
                kind=CapabilityKind.MODEL,
                supported_operations=("generate",),
                modalities=("text",),
            ),
        ),
        health=HealthStatus.HEALTHY,
    )

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            request_id=request.request_id,
            text=" | ".join(request.messages).upper(),
            model_ref="alternate/test-model",
        )


class RawBackendError(RuntimeError):
    pass


class RawFailingBackend:
    def generate(self) -> str:
        raise RawBackendError("raw provider failure")


class TranslatingModelProvider(ModelProvider):
    descriptor = ProviderDescriptor(
        provider_id="translating-model",
        provider_type="model",
        supported_operations=("generate",),
        capabilities=(
            Capability(
                name="model.text",
                kind=CapabilityKind.MODEL,
                supported_operations=("generate",),
            ),
        ),
        health=HealthStatus.DEGRADED,
    )

    def __init__(self) -> None:
        self.backend = RawFailingBackend()

    async def generate(self, request: ModelRequest) -> ModelResponse:
        try:
            text = self.backend.generate()
        except RawBackendError as exc:
            raise ContractError(
                ErrorCode.TRANSIENT_FAILURE,
                "model backend failed",
                retryable=True,
                provider_id=self.descriptor.provider_id,
                adapter_metadata=(
                    AdapterMetadata(
                        namespace="raw-test",
                        values={"exception_type": type(exc).__name__},
                    ),
                ),
            ) from exc
        return ModelResponse(
            request_id=request.request_id,
            text=text,
            model_ref="raw-test/model",
        )


def test_contracts_reuse_canonical_run_status_and_event_type() -> None:
    assert ExecutionStatus is RunStatus
    assert PlatformEvent is Event
    assert ExecutionStatus.STARTING is RunStatus.STARTING


def test_canonical_identifier_validation_is_enforced_at_provider_boundaries() -> None:
    with pytest.raises(ValueError):
        PlanRequest(task_id="task-invalid", context=CTX, objective="invalid")

    with pytest.raises(ValueError):
        ExecutionRequest(
            run_id="run-invalid",
            subject_type="task",
            subject_id=new_id("task"),
            context=CTX,
        )

    with pytest.raises(ValueError):
        ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id="task-invalid",
            context=CTX,
        )

    with pytest.raises(ValueError):
        ToolInvocation(
            invocation_id="invoke-invalid",
            tool_ref="tool.invalid",
            arguments={},
            context=CTX,
        )

    with pytest.raises(ValueError):
        NodeDescriptor(node_id="node-invalid")

    with pytest.raises(ValueError):
        WorkerDescriptor(worker_id="worker-invalid", node_id=new_id("node"))


def test_orchestrator_returns_proposal_content_not_canonical_plan_identity() -> None:
    task = Task(title="Plan task", description="Plan it", owner_ref=OWNER)
    response = asyncio.run(
        FakeOrchestrator().plan(
            PlanRequest(task_id=task.id, context=CTX, objective="Plan canonical work")
        )
    )

    assert response.summary
    assert response.steps[0].key == "step-1"
    assert not hasattr(response, "plan_ref")
    assert not hasattr(response, "step_refs")

    with pytest.raises(ValueError):
        PlanStepProposal(key="same", title="Self dependency", depends_on=("same",))


def test_every_reference_provider_passes_common_conformance() -> None:
    providers = (
        FakeCapabilityProvider(),
        FakeOrchestrator(),
        FakeLifecycleBackend(),
        FakeModelProvider(),
        FakeModelRouter(),
        FakeToolProvider(),
        FakeMemoryProvider(),
        FakeFileProvider(),
        FakeKnowledgeProvider(),
        FakeEventProvider(),
        FakeAuthorizationProvider(),
        FakeNodeProvider(),
        FakeWorkerProvider(),
    )

    for provider in providers:
        asyncio.run(assert_provider_contract(provider))
        assert provider.descriptor.supported_operations
        assert provider.descriptor.health is HealthStatus.HEALTHY


def test_reusable_interface_conformance_checks_run_against_reference_providers() -> None:
    task_id = new_id("task")
    run_id = new_id("run")
    tool_id = new_id("tool")
    node_id = new_id("node")
    worker_id = new_id("worker")
    model_request = ModelRequest(request_id="req-1", messages=("hello",), context=CTX)
    tool_invocation = ToolInvocation(
        invocation_id="invoke-1",
        tool_ref=tool_id,
        arguments={"value": "hello"},
        context=CTX,
    )
    execution_request = ExecutionRequest(
        run_id=run_id,
        subject_type="task",
        subject_id=task_id,
        context=CTX,
    )
    node = NodeDescriptor(node_id=node_id)
    worker = WorkerDescriptor(worker_id=worker_id, node_id=node.node_id)

    asyncio.run(assert_model_provider_contract(FakeModelProvider(), model_request))
    asyncio.run(assert_tool_provider_contract(FakeToolProvider(), tool_invocation))
    asyncio.run(assert_lifecycle_backend_contract(FakeLifecycleBackend(), execution_request))
    asyncio.run(assert_knowledge_provider_contract(FakeKnowledgeProvider(), CTX))
    asyncio.run(assert_node_provider_contract(FakeNodeProvider(), node, CTX))
    asyncio.run(assert_worker_provider_contract(FakeWorkerProvider(), worker, execution_request))


def test_second_model_implementation_passes_same_contract_without_domain_changes() -> None:
    task = Task(title="Stable task", owner_ref=OWNER)
    request = ModelRequest(
        request_id="req-replace",
        messages=("same", "canonical", "request"),
        context=OperationContext(correlation_id=task.id),
    )
    original_task_id = task.id

    asyncio.run(assert_model_provider_contract(FakeModelProvider(), request))
    asyncio.run(assert_model_provider_contract(AlternateModelProvider(), request))

    alternate_response = asyncio.run(AlternateModelProvider().generate(request))
    assert alternate_response.request_id == request.request_id
    assert alternate_response.text == "SAME | CANONICAL | REQUEST"
    assert task.id == original_task_id


def test_model_router_returns_typed_selection() -> None:
    request = ModelRequest(request_id="req-route", messages=("route",), context=CTX)
    selection = asyncio.run(FakeModelRouter(model_ref="fake-model/small").select_provider(request))

    assert isinstance(selection, ModelSelection)
    assert selection.provider_id == "fake-model"
    assert selection.model_ref == "fake-model/small"


def test_backend_private_exception_is_translated_to_canonical_error() -> None:
    provider = TranslatingModelProvider()
    request = ModelRequest(request_id="req-error", messages=("hello",), context=CTX)

    async def operation() -> object:
        return await provider.generate(request)

    error = asyncio.run(
        assert_canonical_error(operation, expected_code=ErrorCode.TRANSIENT_FAILURE)
    )

    assert error.retryable is True
    assert error.provider_id == "translating-model"
    assert error.adapter_metadata[0].namespace == "raw-test"


def test_operation_control_defines_timeout_retry_and_idempotency_expectations() -> None:
    control = OperationControl(
        timeout_seconds=5.0,
        idempotency_key="idem-model-1",
        retry_mode=RetryMode.IDEMPOTENT,
    )
    context = OperationContext(correlation_id="corr-control", control=control)
    model = FakeModelProvider(response_text="deterministic")
    request = ModelRequest(request_id="req-control", messages=("input",), context=context)

    response = asyncio.run(model.generate(request))

    assert response.text == "deterministic"
    assert model.calls == [request]
    assert model.calls[0].context.control.timeout_seconds == 5.0
    assert model.calls[0].context.control.idempotency_key == "idem-model-1"
    assert model.calls[0].context.control.retry_mode is RetryMode.IDEMPOTENT

    with pytest.raises(ValueError):
        OperationControl(timeout_seconds=0)


def test_configurable_fake_failure_covers_timeout_and_call_recording() -> None:
    model = FakeModelProvider(
        failure=FakeFailure(ErrorCode.TIMEOUT, "model timed out", retryable=True)
    )
    request = ModelRequest(request_id="req-timeout", messages=("hello",), context=CTX)

    with pytest.raises(ContractError) as captured:
        asyncio.run(model.generate(request))

    assert captured.value.code is ErrorCode.TIMEOUT
    assert captured.value.retryable is True
    assert model.calls == [request]


def test_lifecycle_start_and_cancel_are_idempotent_for_same_canonical_run() -> None:
    task_id = new_id("task")
    run_id = new_id("run")
    backend = FakeLifecycleBackend()
    request = ExecutionRequest(
        run_id=run_id,
        subject_type="task",
        subject_id=task_id,
        context=OperationContext(
            correlation_id=task_id,
            control=OperationControl(
                idempotency_key="start-run-idempotent",
                retry_mode=RetryMode.IDEMPOTENT,
            ),
        ),
    )

    first = asyncio.run(backend.start(request))
    second = asyncio.run(backend.start(request))
    first_cancel = asyncio.run(backend.cancel(request.run_id, request.context))
    second_cancel = asyncio.run(backend.cancel(request.run_id, request.context))

    assert first == second
    assert first_cancel.status is ExecutionStatus.CANCELLED
    assert second_cancel == first_cancel
    assert len(backend.start_calls) == 2
    assert len(backend.cancel_calls) == 2


def test_fake_only_canonical_task_flow_uses_replaceable_contracts() -> None:
    task = Task(title="Fake flow", description="execute fake flow", owner_ref=OWNER)
    run = Run(
        subject_type="task",
        subject_id=task.id,
        owner_ref=OWNER,
        correlation_id=task.id,
    )
    tool_entity = Tool(name="Reference tool", owner_ref=OWNER)
    context = OperationContext(correlation_id=task.id, owner_type="user", owner_id="user-test")
    orchestrator = FakeOrchestrator()
    model = FakeModelProvider(response_text="model-result")
    tool = FakeToolProvider(fixed_output={"tool": "ok"}, echo_arguments=False)
    lifecycle = FakeLifecycleBackend()

    plan = asyncio.run(
        orchestrator.plan(PlanRequest(task_id=task.id, context=context, objective=task.description))
    )
    model_response = asyncio.run(
        model.generate(
            ModelRequest(request_id="req-flow", messages=(plan.summary,), context=context)
        )
    )
    tool_result = asyncio.run(
        tool.invoke(
            ToolInvocation(
                invocation_id="tool-flow",
                tool_ref=tool_entity.id,
                arguments={"model_ref": model_response.model_ref},
                context=context,
            )
        )
    )
    handle = asyncio.run(
        lifecycle.start(
            ExecutionRequest(
                run_id=run.id,
                subject_type=run.subject_type,
                subject_id=run.subject_id,
                context=context,
                input={
                    "plan_summary": plan.summary,
                    "proposal_steps": [step.key for step in plan.steps],
                    "model_text": model_response.text,
                    "tool_output": tool_result.output,
                },
            )
        )
    )

    assert handle.run_id == run.id
    assert orchestrator.calls[0].task_id == task.id
    assert model.calls[0].context.correlation_id == task.id
    assert tool.calls[0].context.correlation_id == task.id


def test_capability_metadata_and_adapter_metadata_are_explicit() -> None:
    provider = FakeModelProvider()
    descriptor = provider.descriptor

    assert descriptor.supported_operations == ("generate",)
    assert descriptor.health is HealthStatus.HEALTHY
    assert descriptor.limits["max_concurrency"] == 1
    assert descriptor.adapter_metadata[0].namespace == "fake"
    assert descriptor.capabilities[0].supported_operations == ("generate",)

    with pytest.raises(AssertionError):
        assert_namespaced_adapter_metadata(
            (
                AdapterMetadata(namespace="same", values={"a": 1}),
                AdapterMetadata(namespace="same", values={"b": 2}),
            )
        )


def test_knowledge_node_and_worker_registration_use_canonical_compute_ids() -> None:
    knowledge = FakeKnowledgeProvider()
    stored = asyncio.run(knowledge.index("doc-1", "searchable answer", CTX))
    retrieved = asyncio.run(knowledge.get("doc-1", CTX))
    hits = asyncio.run(knowledge.query(KnowledgeQuery(query="answer", context=CTX)))

    capability = Capability(name="python", kind=CapabilityKind.EXECUTION)
    node = NodeDescriptor(node_id=new_id("node"), capabilities=(capability,))
    worker = WorkerDescriptor(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        capabilities=(capability,),
    )
    nodes = FakeNodeProvider()
    workers = FakeWorkerProvider()

    asyncio.run(nodes.register_node(node, CTX))
    asyncio.run(workers.register_worker(worker, CTX))

    assert stored.object_ref == "doc-1"
    assert retrieved.ref == "doc-1"
    assert hits[0].ref == "doc-1"
    assert asyncio.run(nodes.list_nodes(CTX)) == (node,)
    assert asyncio.run(workers.list_workers(CTX)) == (worker,)


def test_other_provider_families_preserve_portable_values_and_canonical_event() -> None:
    capability = Capability(name="chat", kind=CapabilityKind.MODEL)
    registry = FakeCapabilityProvider((capability,))
    memory = FakeMemoryProvider()
    files = FakeFileProvider()
    events = FakeEventProvider()
    authorization = FakeAuthorizationProvider(allowed=True)
    task_id = new_id("task")
    event = Event(
        event_type="task.ready",
        subject_type="task",
        subject_id=task_id,
        correlation_id="corr-test",
    )

    listed = asyncio.run(registry.list_capabilities(CTX, kind=CapabilityKind.MODEL))
    stored_memory = asyncio.run(memory.put("agent", "name", "Ada", CTX))
    memory_value = asyncio.run(memory.get("agent", "name", CTX))
    stored_file = asyncio.run(files.write("artifact-demo", b"payload", CTX))
    file_value = asyncio.run(files.read("artifact-demo", CTX))
    asyncio.run(events.publish(event))
    decision = asyncio.run(
        authorization.authorize(
            AuthorizationRequest(
                principal_ref="user:test",
                action="execute",
                resource_ref=task_id,
                context=CTX,
            )
        )
    )

    assert listed == (capability,)
    assert stored_memory.object_ref == "memory:agent:name"
    assert memory_value == "Ada"
    assert stored_file.metadata["size"] == 7
    assert file_value == b"payload"
    assert asyncio.run(events.read("corr-test")) == (event,)
    assert isinstance(event, PlatformEvent)
    assert decision.allowed is True


def test_error_taxonomy_covers_issue_five_required_failure_classes() -> None:
    required = {
        ErrorCode.INVALID_REQUEST,
        ErrorCode.UNAVAILABLE,
        ErrorCode.TIMEOUT,
        ErrorCode.CANCELLED,
        ErrorCode.RATE_LIMITED,
        ErrorCode.RESOURCE_EXHAUSTED,
        ErrorCode.FORBIDDEN,
        ErrorCode.UNSUPPORTED_CAPABILITY,
        ErrorCode.TRANSIENT_FAILURE,
        ErrorCode.PERMANENT_FAILURE,
        ErrorCode.CONTRACT_VIOLATION,
    }

    assert required.issubset(set(ErrorCode))
