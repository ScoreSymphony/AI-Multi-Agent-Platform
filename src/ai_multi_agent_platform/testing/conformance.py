"""Reusable conformance checks for production and reference provider adapters.

The helpers deliberately do not depend on pytest. Adapter test suites can import
and execute them with a configured provider instance in any test framework.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ai_multi_agent_platform.contracts import (
    CONTRACT_VERSION,
    AdapterMetadata,
    AuthorizationProvider,
    AuthorizationRequest,
    CapabilityProvider,
    ContractError,
    ErrorCode,
    EventProvider,
    ExecutionRequest,
    ExecutionStatus,
    FileProvider,
    HealthStatus,
    KnowledgeProvider,
    KnowledgeQuery,
    LifecycleBackend,
    MemoryProvider,
    ModelProvider,
    ModelRequest,
    ModelRouter,
    NodeDescriptor,
    NodeProvider,
    OperationContext,
    Orchestrator,
    PlanRequest,
    PlatformEvent,
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
    if not descriptor.supported_operations:
        raise AssertionError("provider must declare supported operations")
    if await provider.discover_capabilities() != descriptor.capabilities:
        raise AssertionError("capability discovery must match the normalized descriptor")
    if await provider.health() != descriptor.health:
        raise AssertionError("health() must use normalized HealthStatus semantics")
    if not isinstance(descriptor.available, bool):
        raise AssertionError("provider availability must be a normalized boolean")
    if descriptor.health is HealthStatus.UNAVAILABLE and descriptor.available:
        raise AssertionError("unavailable health cannot advertise available=True")

    assert_namespaced_adapter_metadata(descriptor.adapter_metadata)
    for capability in descriptor.capabilities:
        if not capability.name:
            raise AssertionError("capability name must be non-empty")
        if not set(capability.supported_operations).issubset(descriptor.supported_operations):
            raise AssertionError("capability operations must be declared by the provider")
        assert_namespaced_adapter_metadata(capability.adapter_metadata)


async def assert_capability_provider_contract(
    provider: CapabilityProvider,
    context: OperationContext,
) -> None:
    """Verify capability discovery/filtering uses normalized capability records."""

    await assert_provider_contract(provider)
    capabilities = await provider.list_capabilities(context)
    if not isinstance(capabilities, tuple):
        raise AssertionError("capability listing must return a tuple")
    if capabilities:
        kind = capabilities[0].kind
        filtered = await provider.list_capabilities(context, kind=kind)
        if any(capability.kind is not kind for capability in filtered):
            raise AssertionError("capability kind filtering returned a mismatched capability")


async def assert_orchestrator_contract(
    provider: Orchestrator,
    request: PlanRequest,
) -> None:
    """Verify orchestration preserves canonical task identity and normalized results."""

    await assert_provider_contract(provider)
    response = await provider.plan(request)
    if not response.plan_ref:
        raise AssertionError("orchestrator must return a stable plan_ref")
    if not response.summary:
        raise AssertionError("orchestrator must return a non-empty plan summary")
    assert_namespaced_adapter_metadata(response.adapter_metadata)


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


async def assert_model_router_contract(
    provider: ModelRouter,
    request: ModelRequest,
) -> None:
    """Verify model routing returns a stable platform provider identifier."""

    await assert_provider_contract(provider)
    provider_id = await provider.select_provider(request)
    if not isinstance(provider_id, str) or not provider_id:
        raise AssertionError("model router must return a non-empty provider_id string")


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


async def assert_memory_provider_contract(
    provider: MemoryProvider,
    context: OperationContext,
) -> None:
    """Verify memory put/get behavior preserves canonical references and metadata."""

    await assert_provider_contract(provider)
    stored = await provider.put(
        "contract",
        "key",
        {"value": "memory"},
        context,
        metadata={"scope": "contract"},
    )
    if stored.object_ref != "memory:contract:key":
        raise AssertionError("memory provider must return a stable canonical reference")
    if stored.metadata.get("scope") != "contract":
        raise AssertionError("memory provider must preserve canonical metadata")
    value = await provider.get("contract", "key", context)
    if value != {"value": "memory"}:
        raise AssertionError("memory provider must return the stored normalized value")
    assert_namespaced_adapter_metadata(stored.adapter_metadata)


async def assert_file_provider_contract(
    provider: FileProvider,
    context: OperationContext,
) -> None:
    """Verify file write/read behavior preserves canonical references and metadata."""

    await assert_provider_contract(provider)
    stored = await provider.write(
        "artifact:contract-check",
        b"contract-bytes",
        context,
        metadata={"media_type": "application/octet-stream"},
    )
    if stored.object_ref != "artifact:contract-check":
        raise AssertionError("file provider must preserve canonical object_ref")
    if stored.metadata.get("media_type") != "application/octet-stream":
        raise AssertionError("file provider must preserve canonical metadata")
    if await provider.read(stored.object_ref, context) != b"contract-bytes":
        raise AssertionError("file provider must return the bytes that were stored")
    assert_namespaced_adapter_metadata(stored.adapter_metadata)


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
    if (
        first_handle.backend_ref is not None
        and second_handle.backend_ref is not None
        and first_handle.backend_ref != second_handle.backend_ref
    ):
        raise AssertionError("idempotent start must identify the same backend execution")
    assert_namespaced_adapter_metadata(first_handle.adapter_metadata)
    assert_namespaced_adapter_metadata(second_handle.adapter_metadata)

    snapshot = await provider.get(request.run_id, request.context)
    if snapshot.run_id != request.run_id:
        raise AssertionError("lifecycle snapshot must preserve canonical run_id")
    assert_namespaced_adapter_metadata(snapshot.adapter_metadata)

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
    hits = await provider.query(KnowledgeQuery(query="searchable", context=context))
    if source_ref not in {hit.ref for hit in hits}:
        raise AssertionError("knowledge query must return the indexed canonical source")
    assert_namespaced_adapter_metadata(stored.adapter_metadata)
    assert_namespaced_adapter_metadata(retrieved.adapter_metadata)


async def assert_event_provider_contract(
    provider: EventProvider,
    context: OperationContext,
) -> None:
    """Verify publish/read/subscribe preserve canonical event identity and cursors."""

    await assert_provider_contract(provider)
    first = PlatformEvent(
        event_id="event-contract-1",
        event_type="contract.first",
        subject_type="task",
        subject_id="task-contract",
        occurred_at="2026-09-02T00:00:00+00:00",
        context=context,
    )
    second = PlatformEvent(
        event_id="event-contract-2",
        event_type="contract.second",
        subject_type="task",
        subject_id="task-contract",
        occurred_at="2026-09-02T00:00:01+00:00",
        context=context,
    )
    await provider.publish(first)
    await provider.publish(second)

    after = await provider.read(context.correlation_id, after_event_id=first.event_id)
    if tuple(event.event_id for event in after) != (second.event_id,):
        raise AssertionError("event read cursor must preserve stable event ordering")

    streamed = tuple(
        event.event_id
        async for event in provider.subscribe(
            context.correlation_id,
            after_event_id=first.event_id,
        )
    )
    if streamed != (second.event_id,):
        raise AssertionError("event subscription must honor the canonical cursor")
    for event in after:
        assert_namespaced_adapter_metadata(event.adapter_metadata)


async def assert_authorization_provider_contract(
    provider: AuthorizationProvider,
    request: AuthorizationRequest,
) -> None:
    """Verify authorization returns a normalized decision only."""

    await assert_provider_contract(provider)
    decision = await provider.authorize(request)
    if not isinstance(decision.allowed, bool):
        raise AssertionError("authorization decision must expose a boolean allowed value")
    assert_namespaced_adapter_metadata(decision.adapter_metadata)


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
    assert_namespaced_adapter_metadata(registered.adapter_metadata)


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
    assert_namespaced_adapter_metadata(handle.adapter_metadata)


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
