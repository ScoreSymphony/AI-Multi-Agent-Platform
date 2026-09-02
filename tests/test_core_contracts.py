from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import (
    AuthorizationRequest,
    Capability,
    CapabilityKind,
    ContractError,
    ErrorCode,
    ExecutionRequest,
    KnowledgeHit,
    KnowledgeQuery,
    ModelRequest,
    NodeDescriptor,
    OperationContext,
    PlanRequest,
    PlatformEvent,
    ToolInvocation,
    WorkerDescriptor,
)
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeEventProvider,
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
)

CTX = OperationContext(correlation_id="corr-test", owner_type="user", owner_id="user-test")


def test_every_reference_provider_exposes_neutral_capabilities() -> None:
    providers = (
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
        capabilities = asyncio.run(provider.discover_capabilities())
        assert provider.descriptor.contract_version == "1.0"
        assert capabilities == provider.descriptor.capabilities
        assert capabilities


def test_orchestrator_model_router_and_model_are_replaceable() -> None:
    orchestrator = FakeOrchestrator()
    router = FakeModelRouter(provider_id="fake-model")
    model = FakeModelProvider()

    plan = asyncio.run(
        orchestrator.plan(PlanRequest(task_id="task-demo", context=CTX, objective="demo"))
    )
    request = ModelRequest(request_id="req-1", messages=("hello", "world"), context=CTX)
    provider_id = asyncio.run(router.select_provider(request))
    response = asyncio.run(model.generate(request))

    assert plan.plan_ref == "plan:task-demo"
    assert provider_id == "fake-model"
    assert response.text == "hello\nworld"
    assert response.model_ref == "fake-model/default"


def test_lifecycle_backend_uses_canonical_run_id_and_normalized_errors() -> None:
    backend = FakeLifecycleBackend()
    request = ExecutionRequest(
        run_id="run-demo",
        subject_type="task",
        subject_id="task-demo",
        context=CTX,
    )

    handle = asyncio.run(backend.start(request))
    running = asyncio.run(backend.get("run-demo", CTX))
    cancelled = asyncio.run(backend.cancel("run-demo", CTX))

    assert handle.run_id == "run-demo"
    assert running.status == "running"
    assert cancelled.status == "cancelled"

    with pytest.raises(ContractError) as error:
        asyncio.run(backend.get("missing", CTX))

    assert error.value.code is ErrorCode.NOT_FOUND


def test_tool_memory_and_file_providers_use_normalized_values() -> None:
    tool = FakeToolProvider()
    memory = FakeMemoryProvider()
    files = FakeFileProvider()

    tool_result = asyncio.run(
        tool.invoke(
            ToolInvocation(
                invocation_id="invoke-1",
                tool_ref="tool.echo",
                arguments={"value": "hello"},
                context=CTX,
            )
        )
    )
    stored_memory = asyncio.run(memory.put("agent", "name", "Ada", CTX))
    memory_value = asyncio.run(memory.get("agent", "name", CTX))
    stored_file = asyncio.run(files.write("artifact-demo", b"payload", CTX))
    file_value = asyncio.run(files.read("artifact-demo", CTX))

    assert tool_result.output == {"value": "hello"}
    assert stored_memory.object_ref == "memory:agent:name"
    assert memory_value == "Ada"
    assert stored_file.metadata["size"] == 7
    assert file_value == b"payload"


def test_event_and_knowledge_providers_preserve_correlation() -> None:
    events = FakeEventProvider()
    knowledge = FakeKnowledgeProvider((KnowledgeHit(ref="doc-1", content="answer", score=1.0),))

    event = PlatformEvent(
        event_id="event-1",
        event_type="task.ready",
        subject_type="task",
        subject_id="task-demo",
        context=CTX,
    )
    asyncio.run(events.publish(event))
    read_back = asyncio.run(events.read("corr-test"))
    hits = asyncio.run(knowledge.query(KnowledgeQuery(query="question", context=CTX)))

    assert read_back == (event,)
    assert hits[0].ref == "doc-1"


def test_authorization_nodes_and_workers_are_provider_neutral() -> None:
    capability = Capability(name="python", kind=CapabilityKind.EXECUTION)
    node = NodeDescriptor(node_id="node-1", capabilities=(capability,))
    worker = WorkerDescriptor(worker_id="worker-1", node_id="node-1", capabilities=(capability,))

    authorization = FakeAuthorizationProvider(allowed=True)
    nodes = FakeNodeProvider((node,))
    workers = FakeWorkerProvider((worker,))

    decision = asyncio.run(
        authorization.authorize(
            AuthorizationRequest(
                principal_ref="user:test",
                action="execute",
                resource_ref="task:test",
                context=CTX,
            )
        )
    )
    listed_nodes = asyncio.run(nodes.list_nodes(CTX))
    listed_workers = asyncio.run(workers.list_workers(CTX))
    handle = asyncio.run(
        workers.dispatch(
            "worker-1",
            ExecutionRequest(
                run_id="run-1",
                subject_type="task",
                subject_id="task-1",
                context=CTX,
            ),
        )
    )

    assert decision.allowed is True
    assert listed_nodes == (node,)
    assert listed_workers == (worker,)
    assert handle.run_id == "run-1"


def test_contract_error_carries_stable_machine_readable_semantics() -> None:
    error = ContractError(
        ErrorCode.UNAVAILABLE,
        "temporary outage",
        retryable=True,
        provider_id="provider-x",
        details={"attempt": 2},
    )

    assert error.code is ErrorCode.UNAVAILABLE
    assert error.retryable is True
    assert error.provider_id == "provider-x"
    assert error.details == {"attempt": 2}
