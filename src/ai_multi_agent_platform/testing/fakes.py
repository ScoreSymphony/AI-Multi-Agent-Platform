"""Small in-memory providers used to prove contract replaceability in tests."""

from __future__ import annotations

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
    AuthorizationDecision,
    AuthorizationRequest,
    Capability,
    CapabilityKind,
    ExecutionHandle,
    ExecutionRequest,
    ExecutionSnapshot,
    JsonValue,
    KnowledgeHit,
    KnowledgeQuery,
    ModelRequest,
    ModelResponse,
    NodeDescriptor,
    OperationContext,
    PlanRequest,
    PlanResponse,
    PlatformEvent,
    ProviderDescriptor,
    StoredObject,
    ToolInvocation,
    ToolResult,
    WorkerDescriptor,
)


def _descriptor(provider_id: str, provider_type: str, kind: CapabilityKind) -> ProviderDescriptor:
    return ProviderDescriptor(
        provider_id=provider_id,
        provider_type=provider_type,
        capabilities=(Capability(name=f"{provider_type}.basic", kind=kind),),
    )


class FakeCapabilityProvider(CapabilityProvider):
    descriptor = _descriptor("fake-capabilities", "capability-registry", CapabilityKind.TOOL)

    def __init__(self, capabilities: tuple[Capability, ...] = ()) -> None:
        self.capabilities = capabilities or self.descriptor.capabilities

    async def list_capabilities(
        self,
        context: OperationContext,
        *,
        kind: CapabilityKind | None = None,
    ) -> tuple[Capability, ...]:
        del context
        if kind is None:
            return self.capabilities
        return tuple(capability for capability in self.capabilities if capability.kind is kind)


class FakeOrchestrator(Orchestrator):
    descriptor = _descriptor("fake-orchestrator", "orchestrator", CapabilityKind.ORCHESTRATION)

    async def plan(self, request: PlanRequest) -> PlanResponse:
        return PlanResponse(
            plan_ref=f"plan:{request.task_id}",
            summary=f"Plan for {request.objective}",
            step_refs=(f"step:{request.task_id}:1",),
        )


class FakeLifecycleBackend(LifecycleBackend):
    descriptor = _descriptor("fake-lifecycle", "lifecycle", CapabilityKind.EXECUTION)

    def __init__(self) -> None:
        self._runs: dict[str, ExecutionSnapshot] = {}

    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        snapshot = ExecutionSnapshot(run_id=request.run_id, status="running")
        self._runs[request.run_id] = snapshot
        return ExecutionHandle(run_id=request.run_id, backend_ref=f"fake:{request.run_id}")

    async def get(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        del context
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"Run not found: {run_id}") from exc

    async def cancel(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        del context
        current = await self.get(run_id, OperationContext(correlation_id="fake-cancel"))
        cancelled = ExecutionSnapshot(run_id=current.run_id, status="cancelled")
        self._runs[run_id] = cancelled
        return cancelled


class FakeModelProvider(ModelProvider):
    descriptor = _descriptor("fake-model", "model", CapabilityKind.MODEL)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            request_id=request.request_id,
            text="\n".join(request.messages),
            model_ref="fake-model/default",
        )


class FakeModelRouter(ModelRouter):
    descriptor = _descriptor("fake-router", "model-router", CapabilityKind.MODEL)

    def __init__(self, provider_id: str = "fake-model") -> None:
        self.provider_id = provider_id

    async def select_provider(self, request: ModelRequest) -> str:
        del request
        return self.provider_id


class FakeToolProvider(ToolProvider):
    descriptor = _descriptor("fake-tools", "tool", CapabilityKind.TOOL)

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        return ToolResult(invocation_id=invocation.invocation_id, output=invocation.arguments)


class FakeMemoryProvider(MemoryProvider):
    descriptor = _descriptor("fake-memory", "memory", CapabilityKind.MEMORY)

    def __init__(self) -> None:
        self._values: dict[tuple[str, str], JsonValue] = {}

    async def put(
        self,
        namespace: str,
        key: str,
        value: JsonValue,
        context: OperationContext,
    ) -> StoredObject:
        del context
        self._values[(namespace, key)] = value
        return StoredObject(object_ref=f"memory:{namespace}:{key}")

    async def get(self, namespace: str, key: str, context: OperationContext) -> JsonValue:
        del context
        try:
            return self._values[(namespace, key)]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"Memory key not found: {namespace}/{key}") from exc


class FakeFileProvider(FileProvider):
    descriptor = _descriptor("fake-files", "file", CapabilityKind.FILE)

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    async def write(
        self,
        object_ref: str,
        data: bytes,
        context: OperationContext,
    ) -> StoredObject:
        del context
        self._objects[object_ref] = data
        return StoredObject(object_ref=object_ref, metadata={"size": len(data)})

    async def read(self, object_ref: str, context: OperationContext) -> bytes:
        del context
        try:
            return self._objects[object_ref]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"Object not found: {object_ref}") from exc


class FakeKnowledgeProvider(KnowledgeProvider):
    descriptor = _descriptor("fake-knowledge", "knowledge", CapabilityKind.KNOWLEDGE)

    def __init__(self, hits: tuple[KnowledgeHit, ...] = ()) -> None:
        self.hits = hits

    async def query(self, request: KnowledgeQuery) -> tuple[KnowledgeHit, ...]:
        del request
        return self.hits


class FakeEventProvider(EventProvider):
    descriptor = _descriptor("fake-events", "event", CapabilityKind.EVENT)

    def __init__(self) -> None:
        self._events: list[PlatformEvent] = []

    async def publish(self, event: PlatformEvent) -> None:
        self._events.append(event)

    async def read(
        self,
        correlation_id: str,
        *,
        after_event_id: str | None = None,
    ) -> tuple[PlatformEvent, ...]:
        events = [event for event in self._events if event.context.correlation_id == correlation_id]
        if after_event_id is None:
            return tuple(events)
        for index, event in enumerate(events):
            if event.event_id == after_event_id:
                return tuple(events[index + 1 :])
        raise ContractError(ErrorCode.NOT_FOUND, f"Event cursor not found: {after_event_id}")


class FakeAuthorizationProvider(AuthorizationProvider):
    descriptor = _descriptor("fake-auth", "authorization", CapabilityKind.AUTHORIZATION)

    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        del request
        return AuthorizationDecision(allowed=self.allowed, reason="fake-policy")


class FakeNodeProvider(NodeProvider):
    descriptor = _descriptor("fake-nodes", "node", CapabilityKind.NODE)

    def __init__(self, nodes: tuple[NodeDescriptor, ...] = ()) -> None:
        self.nodes = nodes

    async def list_nodes(self, context: OperationContext) -> tuple[NodeDescriptor, ...]:
        del context
        return self.nodes


class FakeWorkerProvider(WorkerProvider):
    descriptor = _descriptor("fake-workers", "worker", CapabilityKind.WORKER)

    def __init__(self, workers: tuple[WorkerDescriptor, ...] = ()) -> None:
        self.workers = workers

    async def list_workers(self, context: OperationContext) -> tuple[WorkerDescriptor, ...]:
        del context
        return self.workers

    async def dispatch(self, worker_id: str, request: ExecutionRequest) -> ExecutionHandle:
        if worker_id not in {worker.worker_id for worker in self.workers}:
            raise ContractError(ErrorCode.NOT_FOUND, f"Worker not found: {worker_id}")
        return ExecutionHandle(run_id=request.run_id, backend_ref=f"worker:{worker_id}")
