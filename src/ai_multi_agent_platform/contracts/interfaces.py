"""Replaceable platform-owned provider contracts.

These interfaces define architectural seams. Concrete systems such as Hermes,
Forge, LiteLLM, MCP servers, databases or workflow engines implement adapters
behind these contracts rather than becoming part of the canonical domain model.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .types import (
    AuthorizationDecision,
    AuthorizationRequest,
    Capability,
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


class ProviderContract(ABC):
    """Common metadata/capability surface for replaceable providers."""

    @property
    @abstractmethod
    def descriptor(self) -> ProviderDescriptor:
        """Return stable metadata for this provider instance."""

    async def discover_capabilities(self) -> tuple[Capability, ...]:
        """Return provider capabilities without exposing private backend state."""

        return self.descriptor.capabilities


class Orchestrator(ProviderContract):
    """Turns canonical task intent into a provider-neutral plan description."""

    @abstractmethod
    async def plan(self, request: PlanRequest) -> PlanResponse:
        """Produce a plan without owning canonical task persistence."""


class LifecycleBackend(ProviderContract):
    """Executes and observes canonical Run attempts."""

    @abstractmethod
    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        """Start one canonical execution attempt."""

    @abstractmethod
    async def get(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        """Read the current execution snapshot for a canonical run."""

    @abstractmethod
    async def cancel(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        """Request cancellation of a canonical run."""


class ModelProvider(ProviderContract):
    """Executes a model request against one model/runtime provider."""

    @abstractmethod
    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Generate a provider-neutral model response."""


class ModelRouter(ProviderContract):
    """Chooses a model provider/reference from neutral request requirements."""

    @abstractmethod
    async def select_provider(self, request: ModelRequest) -> str:
        """Return a platform provider identifier, not an SDK object."""


class ToolProvider(ProviderContract):
    """Invokes tools through native, MCP, HTTP or other adapters."""

    @abstractmethod
    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        """Execute one tool invocation."""


class MemoryProvider(ProviderContract):
    """Stores and retrieves memory entries without fixing a memory product."""

    @abstractmethod
    async def put(
        self,
        namespace: str,
        key: str,
        value: JsonValue,
        context: OperationContext,
    ) -> StoredObject:
        """Store or replace one memory entry."""

    @abstractmethod
    async def get(
        self,
        namespace: str,
        key: str,
        context: OperationContext,
    ) -> JsonValue:
        """Return one memory entry."""


class FileProvider(ProviderContract):
    """Stores artifact/file payload bytes independently from metadata ownership."""

    @abstractmethod
    async def write(
        self,
        object_ref: str,
        data: bytes,
        context: OperationContext,
    ) -> StoredObject:
        """Persist payload bytes for a canonical object reference."""

    @abstractmethod
    async def read(self, object_ref: str, context: OperationContext) -> bytes:
        """Read payload bytes by canonical/storage-neutral reference."""


class KnowledgeProvider(ProviderContract):
    """Queries a replaceable knowledge/index backend."""

    @abstractmethod
    async def query(self, request: KnowledgeQuery) -> tuple[KnowledgeHit, ...]:
        """Return normalized knowledge hits."""


class EventProvider(ProviderContract):
    """Publishes and reads canonical events."""

    @abstractmethod
    async def publish(self, event: PlatformEvent) -> None:
        """Persist/publish one canonical event."""

    @abstractmethod
    async def read(
        self,
        correlation_id: str,
        *,
        after_event_id: str | None = None,
    ) -> tuple[PlatformEvent, ...]:
        """Read events for one logical flow in provider-defined stable order."""


class AuthorizationProvider(ProviderContract):
    """Evaluates authorization without embedding a specific policy engine."""

    @abstractmethod
    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        """Return a normalized authorization decision."""


class NodeProvider(ProviderContract):
    """Discovers compute nodes participating in the platform."""

    @abstractmethod
    async def list_nodes(self, context: OperationContext) -> tuple[NodeDescriptor, ...]:
        """Return normalized node descriptors."""


class WorkerProvider(ProviderContract):
    """Discovers workers and dispatches canonical execution requests."""

    @abstractmethod
    async def list_workers(self, context: OperationContext) -> tuple[WorkerDescriptor, ...]:
        """Return normalized worker descriptors."""

    @abstractmethod
    async def dispatch(
        self,
        worker_id: str,
        request: ExecutionRequest,
    ) -> ExecutionHandle:
        """Dispatch one canonical run attempt to a worker."""
