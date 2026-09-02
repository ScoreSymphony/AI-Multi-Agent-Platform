from __future__ import annotations

import asyncio

from ai_multi_agent_platform.contracts import (
    Capability,
    CapabilityKind,
    ExecutionRequest,
    ModelRequest,
    NodeDescriptor,
    OperationContext,
    ToolInvocation,
    WorkerDescriptor,
)
from ai_multi_agent_platform.testing.conformance import (
    assert_knowledge_provider_contract,
    assert_lifecycle_backend_contract,
    assert_model_provider_contract,
    assert_node_provider_contract,
    assert_provider_contract,
    assert_tool_provider_contract,
    assert_worker_provider_contract,
)
from ai_multi_agent_platform.testing.fakes import (
    FakeAuthorizationProvider,
    FakeCapabilityProvider,
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

CTX = OperationContext(correlation_id="contract-suite")


def test_generic_provider_conformance_runs_for_every_reference_provider() -> None:
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


def test_specialized_conformance_checks_are_adapter_reusable() -> None:
    execution = ExecutionRequest(
        run_id="run-contract",
        subject_type="task",
        subject_id="task-contract",
        context=CTX,
    )
    model_request = ModelRequest(
        request_id="model-contract",
        messages=("contract",),
        context=CTX,
    )
    tool_invocation = ToolInvocation(
        invocation_id="tool-contract",
        tool_ref="tool.echo",
        arguments={"value": "contract"},
        context=CTX,
    )
    capability = Capability(name="python", kind=CapabilityKind.EXECUTION)
    node = NodeDescriptor(node_id="node-contract", capabilities=(capability,))
    worker = WorkerDescriptor(
        worker_id="worker-contract",
        node_id=node.node_id,
        capabilities=(capability,),
    )

    asyncio.run(assert_model_provider_contract(FakeModelProvider(), model_request))
    asyncio.run(assert_tool_provider_contract(FakeToolProvider(), tool_invocation))
    asyncio.run(assert_lifecycle_backend_contract(FakeLifecycleBackend(), execution))
    asyncio.run(assert_knowledge_provider_contract(FakeKnowledgeProvider(), CTX))
    asyncio.run(assert_node_provider_contract(FakeNodeProvider(), node, CTX))
    asyncio.run(assert_worker_provider_contract(FakeWorkerProvider(), worker, execution))
