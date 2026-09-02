"""Deterministic in-memory providers for contract and core development tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.interfaces import (
    AuthorizationProvider,
    CapabilityProvider,
    EventProvider,
    FileProvider,
    KnowledgeProvider,
    LifecycleBackend,
    MemoryProvider,
    ModelProvider,
    ModelRouter,
    NodeProvider,
    Orchestrator,
    ToolProvider,
    WorkerProvider,
)
from ai_multi_agent_platform.contracts.types import (
    AdapterMetadata,
    AuthorizationDecision,
    AuthorizationRequest,
    Capability,
    CapabilityKind,
    ExecutionHandle,
    ExecutionRequest,
    ExecutionSnapshot,
    ExecutionStatus,
    HealthStatus,
    JsonValue,
    KnowledgeHit,
    KnowledgeQuery,
    ModelRequest,
    ModelResponse,
    ModelSelection,
    NodeDescriptor,
    OperationContext,
    OperationControl,
    PlanRequest,
    PlanResponse,
    PlanStepProposal,
    PlatformEvent,
    ProviderDescriptor,
    StoredObject,
    ToolInvocation,
    ToolResult,
    WorkerDescriptor,
)


@dataclass(frozen=True, slots=True)
class FakeFailure:
    """Configurable canonical failure emitted by a reference provider."""

    code: ErrorCode
    message: str = "configured fake failure"
    retryable: bool = False


def _descriptor(
    provider_id: str,
    provider_type: str,
    kind: CapabilityKind,
    *operations: str,
) -> ProviderDescriptor:
    capability = Capability(
        name=f"{provider_type}.basic",
        kind=kind,
        supported_operations=operations,
        adapter_metadata=(
            AdapterMetadata(namespace="fake", values={"implementation": "in-memory"}),
        ),
    )
    return ProviderDescriptor(
        provider_id=provider_id,
        provider_type=provider_type,
        supported_operations=operations,
        capabilities=(capability,),
        health=HealthStatus.HEALTHY,
        available=True,
        limits={"max_concurrency": 1},
        resources={"reference_units": 1},
        adapter_metadata=(
            AdapterMetadata(namespace="fake", values={"implementation": "in-memory"}),
        ),
    )


def _raise_configured_failure(provider_id: str, failure: FakeFailure | None) -> None:
    if failure is None:
        return
    raise ContractError(
        failure.code,
        failure.message,
        retryable=failure.retryable,
        provider_id=provider_id,
        adapter_metadata=(AdapterMetadata(namespace="fake", values={"configured": True}),),
    )


class FakeCapabilityProvider(CapabilityProvider):
    descriptor = _descriptor(
        "fake-capabilities",
        "capability-registry",
        CapabilityKind.TOOL,
        "list_capabilities",
    )

    def __init__(
        self,
        capabilities: tuple[Capability, ...] = (),
        *,
        failure: FakeFailure | None = None,
    ) -> None:
        self.capabilities = capabilities or self.descriptor.capabilities
        self.failure = failure
        self.calls: list[tuple[OperationContext, CapabilityKind | None]] = []

    async def list_capabilities(
        self,
        context: OperationContext,
        *,
        kind: CapabilityKind | None = None,
    ) -> tuple[Capability, ...]:
        self.calls.append((context, kind))
        _raise_configured_failure(self.descriptor.provider_id, self.failure)
        if kind is None:
            return self.capabilities
        return tuple(capability for capability in self.capabilities if capability.kind is kind)


class FakeOrchestrator(Orchestrator):
    descriptor = _descriptor(
        "fake-orchestrator",
        "orchestrator",
        CapabilityKind.ORCHESTRATION,
        "plan",
    )

    def __init__(
        self,
        *,
        summary_prefix: str = "Plan for",
        failure: FakeFailure | None = None,
    ) -> None:
        self.summary_prefix = summary_prefix
        self.failure = failure
        self.calls: list[PlanRequest] = []

    async def plan(self, request: PlanRequest) -> PlanResponse:
        self.calls.append(request)
        _raise_configured_failure(self.descriptor.provider_id, self.failure)
        return PlanResponse(
            summary=f"{self.summary_prefix} {request.objective}",
            steps=(
                PlanStepProposal(
                    key="step-1",
                    title="Execute requested work",
                    objective=request.objective,
                ),
            ),
        )


class FakeLifecycleBackend(LifecycleBackend):
    descriptor = _descriptor(
        "fake-lifecycle",
        "lifecycle",
        CapabilityKind.EXECUTION,
        "start",
        "get",
        "cancel",
    )

    def __init__(
        self,
        *,
        start_failure: FakeFailure | None = None,
        get_failure: FakeFailure | None = None,
        cancel_failure: FakeFailure | None = None,
    ) -> None:
        self._runs: dict[str, ExecutionSnapshot] = {}
        self._handles: dict[str, ExecutionHandle] = {}
        self.start_failure = start_failure
        self.get_failure = get_failure
        self.cancel_failure = cancel_failure
        self.start_calls: list[ExecutionRequest] = []
        self.get_calls: list[tuple[str, OperationContext]] = []
        self.cancel_calls: list[tuple[str, OperationContext]] = []

    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        self.start_calls.append(request)
        _raise_configured_failure(self.descriptor.provider_id, self.start_failure)
        existing = self._handles.get(request.run_id)
        if existing is not None:
            return existing
        snapshot = ExecutionSnapshot(run_id=request.run_id, status=ExecutionStatus.RUNNING)
        handle = ExecutionHandle(run_id=request.run_id, backend_ref=f"fake:{request.run_id}")
        self._runs[request.run_id] = snapshot
        self._handles[request.run_id] = handle
        return handle

    async def get(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        self.get_calls.append((run_id, context))
        _raise_configured_failure(self.descriptor.provider_id, self.get_failure)
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"Run not found: {run_id}") from exc

    async def cancel(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        self.cancel_calls.append((run_id, context))
        _raise_configured_failure(self.descriptor.provider_id, self.cancel_failure)
        current = await self.get(run_id, context)
        if current.status is ExecutionStatus.CANCELLED:
            return current
        if current.status in {
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMED_OUT,
        }:
            return current
        cancelled = ExecutionSnapshot(run_id=current.run_id, status=ExecutionStatus.CANCELLED)
        self._runs[run_id] = cancelled
        return cancelled

    def complete(
        self,
        run_id: str,
        *,
        status: ExecutionStatus = ExecutionStatus.SUCCEEDED,
        output: dict[str, JsonValue] | None = None,
    ) -> None:
        """Deterministically move an existing fake run to a chosen terminal state."""

        if run_id not in self._runs:
            raise KeyError(run_id)
        if status not in {
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.TIMED_OUT,
        }:
            raise ValueError("complete() requires a terminal execution status")
        self._runs[run_id] = ExecutionSnapshot(
            run_id=run_id,
            status=status,
            output=output or {},
        )


class FakeModelProvider(ModelProvider):
    descriptor = _descriptor(
        "fake-model",
        "model",
        CapabilityKind.MODEL,
        "generate",
    )

    def __init__(
        self,
        *,
        response_text: str | None = None,
        model_ref: str = "fake-model/default",
        failure: FakeFailure | None = None,
    ) -> None:
        self.response_text = response_text
        self.model_ref = model_ref
        self.failure = failure
        self.calls: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        _raise_configured_failure(self.descriptor.provider_id, self.failure)
        text = self.response_text
        if text is None:
            text = "\n".join(request.messages)
        return ModelResponse(
            request_id=request.request_id,
            text=text,
            model_ref=self.model_ref,
        )


class FakeModelRouter(ModelRouter):
    descriptor = _descriptor(
        "fake-router",
        "model-router",
        CapabilityKind.MODEL,
        "select_provider",
    )

    def __init__(
        self,
        provider_id: str = "fake-model",
        *,
        model_ref: str | None = None,
        failure: FakeFailure | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.model_ref = model_ref
        self.failure = failure
        self.calls: list[ModelRequest] = []

    async def select_provider(self, request: ModelRequest) -> ModelSelection:
        self.calls.append(request)
        _raise_configured_failure(self.descriptor.provider_id, self.failure)
        return ModelSelection(provider_id=self.provider_id, model_ref=self.model_ref)


class FakeToolProvider(ToolProvider):
    descriptor = _descriptor(
        "fake-tools",
        "tool",
        CapabilityKind.TOOL,
        "invoke",
    )

    def __init__(
        self,
        *,
        fixed_output: JsonValue = None,
        echo_arguments: bool = True,
        failure: FakeFailure | None = None,
    ) -> None:
        self.fixed_output = fixed_output
        self.echo_arguments = echo_arguments
        self.failure = failure
        self.calls: list[ToolInvocation] = []

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        self.calls.append(invocation)
        _raise_configured_failure(self.descriptor.provider_id, self.failure)
        output: JsonValue = self.fixed_output
        if self.echo_arguments:
            output = invocation.arguments_json()
        return ToolResult(invocation_id=invocation.invocation_id, output=output)


class FakeMemoryProvider(MemoryProvider):
    descriptor = _descriptor(
        "fake-memory",
        "memory",
        CapabilityKind.MEMORY,
        "put",
        "get",
    )

    def __init__(self, *, failure: FakeFailure | None = None) -> None:
        self._values: dict[tuple[str, str], JsonValue] = {}
        self.failure = failure
        self.calls: list[str] = []

    async def put(
        self,
        namespace: str,
        key: str,
        value: JsonValue,
        context: OperationContext,
        *,
        metadata: dict[str, JsonValue] | None = None,
    ) -> StoredObject:
        del context
        self.calls.append("put")
        _raise_configured_failure(self.descriptor.provider_id, self.failure)
        self._values[(namespace, key)] = value
        return StoredObject(
            object_ref=f"memory:{namespace}:{key}",
            metadata=metadata or {},
        )

    async def get(self, namespace: str, key: str, context: OperationContext) -> JsonValue:
        del context
        self.calls.append("get")
        _raise_configured_failure(self.descriptor.provider_id, self.failure)
        try:
            return self._values[(namespace, key)]
        except KeyError as exc:
            message = f"Memory key not found: {namespace}/{key}"
            raise ContractError(ErrorCode.NOT_FOUND, message) from exc


class FakeFileProvider(FileProvider):
    descriptor = _descriptor(
        "fake-files",
        "file",
        CapabilityKind.FILE,
        "write",
        "read",
    )

    def __init__(self, *, failure: FakeFailure | None = None) -> None:
        self._objects: dict[str, bytes] = {}
        self.failure = failure
        self.calls: list[str] = []

    async def write(
        self,
        object_ref: str,
        data: bytes,
        context: OperationContext,
        *,
        metadata: dict[str, JsonValue] | None = None,
    ) -> StoredObject:
        del context
        self.calls.append("write")
        _raise_configured_failure(self.descriptor.provider_id, self.failure)
        self._objects[object_ref] = data
        stored_metadata = dict(metadata or {})
        stored_metadata["size"] = len(data)
        return StoredObject(object_ref=object_ref, metadata=stored_metadata)

    async def read(self, object_ref: str, context: OperationContext) -> bytes:
        del context
        self.calls.append("read")
        _raise_configured_failure(self.descriptor.provider_id, self.failure)
        try:
            return self._objects[object_ref]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"Object not found: {object_ref}") from exc


class FakeKnowledgeProvider(KnowledgeProvider):
    descriptor = _descriptor(
        "fake-knowledge",
        "knowledge",
        CapabilityKind.KNOWLEDGE,
        "index",
        "query",
        "get",
    )

    def __init__(
        self,
        hits: tuple[KnowledgeHit, ...] = (),
        *,
        failure: FakeFailure | None = None,
    ) -> None:
        self._hits = {hit.ref: hit for hit in hits}
        self.failure = failure
        self.calls: list[str] = []

    async def index(
        self,
        source_ref: str,
        content: str,
        context: OperationContext,
    ) -> StoredObject:
        del context
        self.calls.append("index")
        _raise_configured_failure(self.descriptor.provider_id, self.failure)
        self._hits[source_ref] = KnowledgeHit(ref=source_ref, content=content, score=1.0)
        return StoredObject(object_ref=source_ref)

    async def query(self, request: KnowledgeQuery) -> tuple[KnowledgeHit, ...]:
        self.calls.append("query")
        _raise_configured_failure(self.descriptor.provider_id, self.failure)
        query = request.query.casefold()
        return tuple(
            hit for hit in self._hits.values() if query in hit.content.casefold() or not query
        )

    async def get(self, source_ref: str, context: OperationContext) -> KnowledgeHit:
        del context
        self.calls.append("get")
        _raise_configured_failure(self.descriptor.provider_id, self.failure)
        try:
            return self._hits[source_ref]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"Knowledge source not found: {source_ref}",
            ) from exc


class FakeEventProvider(EventProvider):
    descriptor = _descriptor(
        "fake-events",
        "event",
        CapabilityKind.EVENT,
        "publish",
        "read",
        "subscribe",
    )

    def __init__(self, *, failure: FakeFailure | None = None) -> None:
        self._events: list[PlatformEvent] = []
        self.failure = failure
        self.publish_calls: list[PlatformEvent] = []
        self.read_calls: list[tuple[str, str | None, OperationControl | None]] = []
        self.subscribe_calls: list[tuple[str, str | None, OperationControl | None]] = []

    async def publish(self, event: PlatformEvent) -> None:
        self.publish_calls.append(event)
        _raise_configured_failure(self.descriptor.provider_id, self.failure)
        if any(existing.id == event.id for existing in self._events):
            return
        self._events.append(event)

    async def read(
        self,
        correlation_id: str,
        *,
        after_event_id: str | None = None,
        control: OperationControl | None = None,
    ) -> tuple[PlatformEvent, ...]:
        self.read_calls.append((correlation_id, after_event_id, control))
        _raise_configured_failure(self.descriptor.provider_id, self.failure)
        events = [event for event in self._events if event.correlation_id == correlation_id]
        if after_event_id is None:
            return tuple(events)
        for index, event in enumerate(events):
            if event.id == after_event_id:
                return tuple(events[index + 1 :])
        raise ContractError(ErrorCode.NOT_FOUND, f"Event cursor not found: {after_event_id}")

    def subscribe(
        self,
        correlation_id: str,
        *,
        after_event_id: str | None = None,
        control: OperationControl | None = None,
    ) -> AsyncIterator[PlatformEvent]:
        self.subscribe_calls.append((correlation_id, after_event_id, control))

        async def iterator() -> AsyncIterator[PlatformEvent]:
            for event in await self.read(
                correlation_id,
                after_event_id=after_event_id,
                control=control,
            ):
                yield event

        return iterator()


class FakeAuthorizationProvider(AuthorizationProvider):
    descriptor = _descriptor(
        "fake-auth",
        "authorization",
        CapabilityKind.AUTHORIZATION,
        "authorize",
    )

    def __init__(
        self,
        *,
        allowed: bool = True,
        failure: FakeFailure | None = None,
    ) -> None:
        self.allowed = allowed
        self.failure = failure
        self.calls: list[AuthorizationRequest] = []

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        _raise_configured_failure(self.descriptor.provider_id, self.failure)
        return AuthorizationDecision(allowed=self.allowed, reason="fake-policy")


class FakeNodeProvider(NodeProvider):
    descriptor = _descriptor(
        "fake-nodes",
        "node",
        CapabilityKind.NODE,
        "register_node",
        "list_nodes",
    )

    def __init__(
        self,
        nodes: tuple[NodeDescriptor, ...] = (),
        *,
        failure: FakeFailure | None = None,
    ) -> None:
        self._nodes = {node.node_id: node for node in nodes}
        self.failure = failure
        self.calls: list[str] = []

    async def register_node(
        self,
        node: NodeDescriptor,
        context: OperationContext,
    ) -> NodeDescriptor:
        del context
        self.calls.append("register_node")
        _raise_configured_failure(self.descriptor.provider_id, self.failure)
        self._nodes[node.node_id] = node
        return node

    async def list_nodes(self, context: OperationContext) -> tuple[NodeDescriptor, ...]:
        del context
        self.calls.append("list_nodes")
        _raise_configured_failure(self.descriptor.provider_id, self.failure)
        return tuple(self._nodes.values())


class FakeWorkerProvider(WorkerProvider):
    descriptor = _descriptor(
        "fake-workers",
        "worker",
        CapabilityKind.WORKER,
        "register_worker",
        "list_workers",
        "dispatch",
    )

    def __init__(
        self,
        workers: tuple[WorkerDescriptor, ...] = (),
        *,
        failure: FakeFailure | None = None,
    ) -> None:
        self._workers = {worker.worker_id: worker for worker in workers}
        self.failure = failure
        self.calls: list[str] = []
        self.dispatch_calls: list[tuple[str, ExecutionRequest]] = []

    async def register_worker(
        self,
        worker: WorkerDescriptor,
        context: OperationContext,
    ) -> WorkerDescriptor:
        del context
        self.calls.append("register_worker")
        _raise_configured_failure(self.descriptor.provider_id, self.failure)
        self._workers[worker.worker_id] = worker
        return worker

    async def list_workers(self, context: OperationContext) -> tuple[WorkerDescriptor, ...]:
        del context
        self.calls.append("list_workers")
        _raise_configured_failure(self.descriptor.provider_id, self.failure)
        return tuple(self._workers.values())

    async def dispatch(self, worker_id: str, request: ExecutionRequest) -> ExecutionHandle:
        self.calls.append("dispatch")
        self.dispatch_calls.append((worker_id, request))
        _raise_configured_failure(self.descriptor.provider_id, self.failure)
        if worker_id not in self._workers:
            raise ContractError(ErrorCode.NOT_FOUND, f"Worker not found: {worker_id}")
        return ExecutionHandle(run_id=request.run_id, backend_ref=f"worker:{worker_id}")
