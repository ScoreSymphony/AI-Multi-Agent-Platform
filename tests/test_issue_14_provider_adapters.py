from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import (
    Capability,
    CapabilityKind,
    ExecutionRequest,
    NodeDescriptor,
    OperationContext,
    WorkerDescriptor,
)
from ai_multi_agent_platform.distributed import (
    DistributedNodeProvider,
    DistributedRegistry,
    DistributedRuntime,
    DistributedWorkerProvider,
    LocalWorker,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.testing.conformance import (
    assert_node_provider_contract,
    assert_worker_provider_contract,
)
from ai_multi_agent_platform.testing.fakes import FakeLifecycleBackend


def _context() -> OperationContext:
    return OperationContext(correlation_id="issue-14-provider-contract")


def _capability() -> Capability:
    return Capability(name=new_id("cap"), kind=CapabilityKind.EXECUTION)


def _node(capability: Capability) -> NodeDescriptor:
    return NodeDescriptor(
        node_id=new_id("node"),
        capabilities=(capability,),
        metadata={"display_name": "provider-node"},
    )


def _worker(node: NodeDescriptor, capability: Capability) -> WorkerDescriptor:
    return WorkerDescriptor(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        capabilities=(capability,),
    )


def _request(context: OperationContext, *, run_id: str | None = None, value: str = "one") -> ExecutionRequest:
    return ExecutionRequest(
        run_id=run_id or new_id("run"),
        subject_type="task",
        subject_id=new_id("task"),
        context=context,
        input={"value": value},
    )


def test_distributed_providers_pass_reusable_node_and_worker_conformance() -> None:
    async def scenario() -> None:
        context = _context()
        runtime = DistributedRuntime(DistributedRegistry())
        nodes = DistributedNodeProvider(runtime)
        workers = DistributedWorkerProvider(runtime)
        capability = _capability()
        node = _node(capability)
        worker = _worker(node, capability)

        await assert_node_provider_contract(nodes, node, context)
        registered_worker = await workers.register_worker(worker, context)
        assert registered_worker.capabilities == worker.capabilities

        lifecycle = FakeLifecycleBackend()
        runtime.attach_worker(LocalWorker(worker.worker_id, lifecycle))
        request = _request(context)
        await assert_worker_provider_contract(workers, worker, request)

        record = runtime.records()[0]
        assert record.worker_id == worker.worker_id
        assert record.job.execution.run_id == request.run_id
        assert len(lifecycle.start_calls) == 1

    asyncio.run(scenario())


def test_exact_worker_provider_dispatch_never_falls_back_to_another_worker() -> None:
    async def scenario() -> None:
        context = _context()
        runtime = DistributedRuntime(DistributedRegistry())
        nodes = DistributedNodeProvider(runtime)
        workers = DistributedWorkerProvider(runtime)
        capability = _capability()
        node = _node(capability)
        first = _worker(node, capability)
        second = _worker(node, capability)
        await nodes.register_node(node, context)
        await workers.register_worker(first, context)
        await workers.register_worker(second, context)

        first_lifecycle = FakeLifecycleBackend()
        second_lifecycle = FakeLifecycleBackend()
        runtime.attach_worker(LocalWorker(first.worker_id, first_lifecycle))
        runtime.attach_worker(LocalWorker(second.worker_id, second_lifecycle))
        runtime.set_worker_draining(first.worker_id, draining=True)

        with pytest.raises(ContractError) as rejected:
            await workers.dispatch(first.worker_id, _request(context))
        assert rejected.value.code is ErrorCode.UNAVAILABLE
        assert first_lifecycle.start_calls == []
        assert second_lifecycle.start_calls == []

        handle = await workers.dispatch(second.worker_id, _request(context))
        assert handle.run_id == second_lifecycle.start_calls[0].run_id
        assert len(second_lifecycle.start_calls) == 1

    asyncio.run(scenario())


def test_provider_dispatch_is_idempotent_for_same_worker_and_run() -> None:
    async def scenario() -> None:
        context = _context()
        runtime = DistributedRuntime(DistributedRegistry())
        nodes = DistributedNodeProvider(runtime)
        workers = DistributedWorkerProvider(runtime)
        capability = _capability()
        node = _node(capability)
        worker = _worker(node, capability)
        await nodes.register_node(node, context)
        await workers.register_worker(worker, context)

        lifecycle = FakeLifecycleBackend()
        runtime.attach_worker(LocalWorker(worker.worker_id, lifecycle))
        request = _request(context)
        first = await workers.dispatch(worker.worker_id, request)
        second = await workers.dispatch(worker.worker_id, request)

        assert first == second
        assert len(runtime.records()) == 1
        assert len(lifecycle.start_calls) == 1

        changed = ExecutionRequest(
            run_id=request.run_id,
            subject_type=request.subject_type,
            subject_id=request.subject_id,
            context=request.context,
            input={"value": "changed"},
        )
        with pytest.raises(ContractError) as conflict:
            await workers.dispatch(worker.worker_id, changed)
        assert conflict.value.code is ErrorCode.CONFLICT
        assert len(runtime.records()) == 1
        assert len(lifecycle.start_calls) == 1

    asyncio.run(scenario())


def test_provider_reregistration_preserves_sibling_worker_state() -> None:
    async def scenario() -> None:
        context = _context()
        runtime = DistributedRuntime(DistributedRegistry())
        nodes = DistributedNodeProvider(runtime)
        workers = DistributedWorkerProvider(runtime)
        capability = _capability()
        node = _node(capability)
        first = _worker(node, capability)
        second = _worker(node, capability)
        await nodes.register_node(node, context)
        await workers.register_worker(first, context)
        await workers.register_worker(second, context)
        runtime.set_worker_draining(second.worker_id, draining=True)

        await nodes.register_node(node, context)
        assert runtime.registry.get_worker(second.worker_id).draining is True

        await workers.register_worker(first, context)
        sibling = runtime.registry.get_worker(second.worker_id)
        assert sibling.draining is True
        assert sibling.node_id == node.node_id
        assert {item.worker_id for item in runtime.registry.list_workers()} == {
            first.worker_id,
            second.worker_id,
        }

    asyncio.run(scenario())


def test_worker_provider_rejects_unknown_node_with_canonical_error() -> None:
    async def scenario() -> None:
        context = _context()
        runtime = DistributedRuntime(DistributedRegistry())
        workers = DistributedWorkerProvider(runtime)
        capability = _capability()
        missing_node = _node(capability)
        worker = _worker(missing_node, capability)

        with pytest.raises(ContractError) as missing:
            await workers.register_worker(worker, context)
        assert missing.value.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())
