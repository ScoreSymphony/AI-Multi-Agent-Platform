"""Reusable conformance checks for production and reference provider adapters.

The helpers deliberately do not depend on pytest. Adapter test suites can import
and execute them with a configured provider instance in any test framework.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ai_multi_agent_platform.contracts import (
    CONTRACT_VERSION,
    AdapterMetadata,
    ContractError,
    ErrorCode,
    ExecutionRequest,
    ExecutionStatus,
    KnowledgeProvider,
    LifecycleBackend,
    ModelProvider,
    ModelRequest,
    NodeDescriptor,
    NodeProvider,
    OperationContext,
    ProviderContract,
    ToolInvocation,
    ToolProvider,
    WorkerDescriptor,
    WorkerProvider,
)


def assert_namespaced_adapter_metadata(metadata: tuple[AdapterMetadata, ...]) -> None:
    """Require backend-private metadata to use explicit, unique namespaces."""

    namespaces = [item.namespace for item in metadata]
    if len(namespaces) != len(set(namespaces)):
        raise AssertionError("adapter metadata namespaces must be unique")
    if any(not namespace.strip() for namespace in namespaces):
        raise AssertionError("adapter metadata namespace must not be blank")


async def assert_provider_contract(provider: ProviderContract) -> None:
    """Check metadata, health and capability invariants shared by all providers."""

    descriptor = provider.descriptor
    if not descriptor.provider_id:
        raise AssertionError("provider_id must be non-empty")
    if not descriptor.provider_type:
        raise AssertionError("provider_type must be non-empty")
    if descriptor.contract_version != CONTRACT_VERSION:
        raise AssertionError("provider implements an unexpected contract version")
    if await provider.discover_capabilities() != descriptor.capabilities:
        raise AssertionError("capability discovery must match the normalized descriptor")
    if await provider.health() != descriptor.health:
        raise AssertionError("health() must use normalized HealthStatus semantics")

    assert_namespaced_adapter_metadata(descriptor.adapter_metadata)
    for capability in descriptor.capabilities:
        if not capability.name:
            raise AssertionError("capability name must be non-empty")
        assert_namespaced_adapter_metadata(capability.adapter_metadata)


async def assert_model_provider_contract(
    provider: ModelProvider,
    request: ModelRequest,
) -> None:
    """Verify canonical identity preservation and response containment for models."""

    await assert_provider_contract(provider)
    response = await provider.generate(request)
    if response.request_id != request.request_id:
        raise AssertionError("model provider must preserve canonical request_id")
    if not response.model_ref:
        raise AssertionError("model provider must return a stable model_ref")
    assert_namespaced_adapter_metadata(response.adapter_metadata)


async def assert_tool_provider_contract(
    provider: ToolProvider,
    invocation: ToolInvocation,
) -> None:
    """Verify canonical invocation identity preservation for tool providers."""

    await assert_provider_contract(provider)
    result = await provider.invoke(invocation)
    if result.invocation_id != invocation.invocation_id:
        raise AssertionError("tool provider must preserve canonical invocation_id")
    assert_namespaced_adapter_metadata(result.adapter_metadata)


async def assert_lifecycle_backend_contract(
    provider: LifecycleBackend,
    request: ExecutionRequest,
) -> None:
    """Verify Run identity, idempotent start and idempotent cancellation semantics."""

    await assert_provider_contract(provider)
    first_handle = await provider.start(request)
    second_handle = await provider.start(request)
    if first_handle.run_id != request.run_id or second_handle.run_id != request.run_id:
        raise AssertionError("lifecycle backend must preserve canonical run_id")

    snapshot = await provider.get(request.run_id, request.context)
    if snapshot.run_id != request.run_id:
        raise AssertionError("lifecycle snapshot must preserve canonical run_id")

    first_cancel = await provider.cancel(request.run_id, request.context)
    second_cancel = await provider.cancel(request.run_id, request.context)
    terminal = {
        ExecutionStatus.SUCCEEDED,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.TIMED_OUT,
    }
    if first_cancel.status not in terminal or second_cancel.status not in terminal:
        raise AssertionError("cancellation must return a terminal normalized snapshot")
    if first_cancel.run_id != request.run_id or second_cancel.run_id != request.run_id:
        raise AssertionError("cancellation must preserve canonical run_id")


async def assert_knowledge_provider_contract(
    provider: KnowledgeProvider,
    context: OperationContext,
) -> None:
    """Verify index/search/retrieve behavior uses canonical source references."""

    await assert_provider_contract(provider)
    source_ref = "knowledge:contract-check"
    stored = await provider.index(source_ref, "contract searchable content", context)
    if stored.object_ref != source_ref:
        raise AssertionError("knowledge index must preserve canonical source reference")
    retrieved = await provider.get(source_ref, context)
    if retrieved.ref != source_ref:
        raise AssertionError("knowledge get must preserve canonical source reference")


async def assert_node_provider_contract(
    provider: NodeProvider,
    node: NodeDescriptor,
    context: OperationContext,
) -> None:
    """Verify canonical node registration/discovery semantics."""

    await assert_provider_contract(provider)
    registered = await provider.register_node(node, context)
    nodes = await provider.list_nodes(context)
    if registered.node_id != node.node_id:
        raise AssertionError("node provider must preserve canonical node_id")
    if node.node_id not in {item.node_id for item in nodes}:
        raise AssertionError("registered node must be discoverable")


async def assert_worker_provider_contract(
    provider: WorkerProvider,
    worker: WorkerDescriptor,
    request: ExecutionRequest,
) -> None:
    """Verify worker registration/discovery and canonical dispatch identity."""

    await assert_provider_contract(provider)
    registered = await provider.register_worker(worker, request.context)
    workers = await provider.list_workers(request.context)
    if registered.worker_id != worker.worker_id:
        raise AssertionError("worker provider must preserve canonical worker_id")
    if worker.worker_id not in {item.worker_id for item in workers}:
        raise AssertionError("registered worker must be discoverable")
    handle = await provider.dispatch(worker.worker_id, request)
    if handle.run_id != request.run_id:
        raise AssertionError("worker dispatch must preserve canonical run_id")


async def assert_canonical_error(
    operation: Callable[[], Awaitable[object]],
    *,
    expected_code: ErrorCode | None = None,
) -> ContractError:
    """Verify an adapter operation exposes only canonical ContractError semantics."""

    try:
        await operation()
    except ContractError as error:
        if expected_code is not None and error.code is not expected_code:
            raise AssertionError(
                f"expected canonical error {expected_code.value}, got {error.code.value}"
            ) from error
        assert_namespaced_adapter_metadata(error.adapter_metadata)
        return error
    except Exception as error:
        raise AssertionError(
            f"backend-private exception escaped provider boundary: {type(error).__name__}"
        ) from error
    raise AssertionError("operation was expected to fail with ContractError")
