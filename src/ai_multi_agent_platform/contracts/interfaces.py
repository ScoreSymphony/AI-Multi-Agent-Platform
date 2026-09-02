"""Replaceable platform-owned provider contracts.

These interfaces define architectural seams. Concrete systems such as Hermes,
Forge, LiteLLM, MCP servers, databases or workflow engines implement adapters
behind these contracts rather than becoming part of the canonical domain model.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from .types import (
    AuthorizationDecision,
    AuthorizationRequest,
    Capability,
    CapabilityKind,
    ExecutionHandle,
    ExecutionRequest,
    ExecutionSnapshot,
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
    PlatformEvent,
    ProviderDescriptor,
    StoredObject,
    ToolInvocation,
    ToolResult,
    WorkerDescriptor,
)


class ProviderContract(ABC):
    """Common metadata/capability surface for replaceable providers."""

    @property
    @abstractmethod
    def descriptor(self) -> ProviderDescriptor:
        """Return stable metadata for this provider instance."""

    async def discover_capabilities(self) -> tuple[Capability, ...]:
        """Return provider capabilities without exposing private backend state."""
        return self.descriptor.capabilities

    async def health(self) -> HealthStatus:
        """Return normalized provider health without exposing backend probes."""
        return self.descriptor.health


class CapabilityProvider(ProviderContract):
    @abstractmethod
    async def list_capabilities(
        self,
        context: OperationContext,
        *,
        kind: CapabilityKind | None = None,
    ) -> tuple[Capability, ...]: ...


class Orchestrator(ProviderContract):
    """Turns canonical task intent into provider-neutral planning proposals."""

    @abstractmethod
    async def plan(self, request: PlanRequest) -> PlanResponse:
        """Propose planning content without allocating canonical Plan/Step IDs."""


class LifecycleBackend(ProviderContract):
    """Executes and observes canonical Run attempts."""

    @abstractmethod
    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        """Start one canonical execution attempt idempotently for the same Run ID."""

    @abstractmethod
    async def get(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        """Read the current execution snapshot for a canonical Run ID."""

    @abstractmethod
    async def cancel(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        """Request idempotent cancellation for a canonical Run ID."""


class ModelProvider(ProviderContract):
    @abstractmethod
    async def generate(self, request: ModelRequest) -> ModelResponse: ...


class ModelRouter(ProviderContract):
    @abstractmethod
    async def select_provider(self, request: ModelRequest) -> ModelSelection: ...


class ToolProvider(ProviderContract):
    @abstractmethod
    async def invoke(self, invocation: ToolInvocation) -> ToolResult: ...


class MemoryProvider(ProviderContract):
    @abstractmethod
    async def put(
        self,
        namespace: str,
        key: str,
        value: JsonValue,
        context: OperationContext,
        *,
        metadata: dict[str, JsonValue] | None = None,
    ) -> StoredObject: ...

    @abstractmethod
    async def get(self, namespace: str, key: str, context: OperationContext) -> JsonValue: ...


class FileProvider(ProviderContract):
    @abstractmethod
    async def write(
        self,
        object_ref: str,
        data: bytes,
        context: OperationContext,
        *,
        metadata: dict[str, JsonValue] | None = None,
    ) -> StoredObject: ...

    @abstractmethod
    async def read(self, object_ref: str, context: OperationContext) -> bytes: ...


class KnowledgeProvider(ProviderContract):
    @abstractmethod
    async def index(
        self,
        source_ref: str,
        content: str,
        context: OperationContext,
    ) -> StoredObject: ...

    @abstractmethod
    async def query(self, request: KnowledgeQuery) -> tuple[KnowledgeHit, ...]: ...

    @abstractmethod
    async def get(self, source_ref: str, context: OperationContext) -> KnowledgeHit: ...


class EventProvider(ProviderContract):
    """Persists and subscribes to the canonical domain Event type."""

    @abstractmethod
    async def publish(self, event: PlatformEvent) -> None:
        """Persist/publish one canonical domain Event unchanged.

        Publishing the same canonical ``event.id`` more than once is idempotent:
        providers must not append a second canonical event for that identifier.
        """

    @abstractmethod
    async def read(
        self,
        correlation_id: str,
        *,
        after_event_id: str | None = None,
        control: OperationControl | None = None,
    ) -> tuple[PlatformEvent, ...]: ...

    @abstractmethod
    def subscribe(
        self,
        correlation_id: str,
        *,
        after_event_id: str | None = None,
        control: OperationControl | None = None,
    ) -> AsyncIterator[PlatformEvent]: ...


class AuthorizationProvider(ProviderContract):
    @abstractmethod
    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision: ...


class NodeProvider(ProviderContract):
    @abstractmethod
    async def register_node(
        self,
        node: NodeDescriptor,
        context: OperationContext,
    ) -> NodeDescriptor: ...

    @abstractmethod
    async def list_nodes(self, context: OperationContext) -> tuple[NodeDescriptor, ...]: ...


class WorkerProvider(ProviderContract):
    @abstractmethod
    async def register_worker(
        self,
        worker: WorkerDescriptor,
        context: OperationContext,
    ) -> WorkerDescriptor: ...

    @abstractmethod
    async def list_workers(self, context: OperationContext) -> tuple[WorkerDescriptor, ...]: ...

    @abstractmethod
    async def dispatch(
        self,
        worker_id: str,
        request: ExecutionRequest,
    ) -> ExecutionHandle: ...
