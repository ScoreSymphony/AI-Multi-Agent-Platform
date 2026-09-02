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
    """Lists normalized capabilities across one registry/source."""

    @abstractmethod
    async def list_capabilities(
        self,
        context: OperationContext,
        *,
        kind: CapabilityKind | None = None,
    ) -> tuple[Capability, ...]:
        """Return capabilities, optionally filtered by broad kind."""


class Orchestrator(ProviderContract):
    """Turns canonical task intent into a provider-neutral plan proposal."""

    @abstractmethod
    async def plan(self, request: PlanRequest) -> PlanResponse:
        """Propose planning content without allocating canonical Plan/Step IDs.

        The platform core owns canonical Plan and Step identity. An orchestrator
        may suggest decomposition and coordination content, but it must not make
        its own workflow/job/session identifiers canonical platform identities.
        """


class LifecycleBackend(ProviderContract):
    """Executes and observes canonical Run attempts."""

    @abstractmethod
    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        """Start one canonical execution attempt.

        Repeating a request with the same canonical Run ID and idempotency key
        must not intentionally create a second execution attempt.
        """

    @abstractmethod
    async def get(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        """Read the current execution snapshot for a canonical Run ID."""

    @abstractmethod
    async def cancel(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        """Request cancellation of a canonical run.

        Cancellation is idempotent. Repeated cancellation returns the current
        terminal snapshot or another normalized snapshot for the same Run ID.
        """


class ModelProvider(ProviderContract):
    """Executes a model request against one model/runtime provider."""

    @abstractmethod
    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Generate a provider-neutral model response."""


class ModelRouter(ProviderContract):
    """Chooses a model provider/reference from neutral request requirements."""

    @abstractmethod
    async def select_provider(self, request: ModelRequest) -> ModelSelection:
        """Return a typed platform selection, never a provider SDK object."""


class ToolProvider(ProviderContract):
    """Invokes canonical Tools through native, MCP, HTTP or other adapters."""

    @abstractmethod
    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        """Execute one tool invocation using the canonical Tool reference."""


class MemoryProvider(ProviderContract):
    """Stores and retrieves memory entries without fixing a memory product."""

    @abstractmethod
    async def put(
        self,
        namespace: str,
        key: str,
        value: JsonValue,
        context: OperationContext,
        *,
        metadata: dict[str, JsonValue] | None = None,
    ) -> StoredObject:
        """Store or replace one memory entry and optional canonical metadata."""

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
        *,
        metadata: dict[str, JsonValue] | None = None,
    ) -> StoredObject:
        """Persist bytes and optional canonical metadata for an object reference."""

    @abstractmethod
    async def read(self, object_ref: str, context: OperationContext) -> bytes:
        """Read payload bytes by canonical/storage-neutral reference."""


class KnowledgeProvider(ProviderContract):
    """Indexes, searches and retrieves through a replaceable knowledge backend."""

    @abstractmethod
    async def index(
        self,
        source_ref: str,
        content: str,
        context: OperationContext,
    ) -> StoredObject:
        """Index canonical knowledge content under a stable source reference."""

    @abstractmethod
    async def query(self, request: KnowledgeQuery) -> tuple[KnowledgeHit, ...]:
        """Return normalized knowledge hits."""

    @abstractmethod
    async def get(self, source_ref: str, context: OperationContext) -> KnowledgeHit:
        """Retrieve one indexed knowledge source by canonical reference."""


class EventProvider(ProviderContract):
    """Persists and subscribes to the canonical domain Event type."""

    @abstractmethod
    async def publish(self, event: PlatformEvent) -> None:
        """Persist/publish one canonical domain Event unchanged."""

    @abstractmethod
    async def read(
        self,
        correlation_id: str,
        *,
        after_event_id: str | None = None,
        control: OperationControl | None = None,
    ) -> tuple[PlatformEvent, ...]:
        """Read events for one logical flow in provider-defined stable order."""

    @abstractmethod
    def subscribe(
        self,
        correlation_id: str,
        *,
        after_event_id: str | None = None,
        control: OperationControl | None = None,
    ) -> AsyncIterator[PlatformEvent]:
        """Yield canonical Events from a stable cursor until cancelled."""


class AuthorizationProvider(ProviderContract):
    """Evaluates authorization without embedding a specific policy engine."""

    @abstractmethod
    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        """Return a normalized authorization decision."""


class NodeProvider(ProviderContract):
    """Registers and discovers canonical compute nodes participating in the platform."""

    @abstractmethod
    async def register_node(
        self,
        node: NodeDescriptor,
        context: OperationContext,
    ) -> NodeDescriptor:
        """Register or refresh one canonical node descriptor."""

    @abstractmethod
    async def list_nodes(self, context: OperationContext) -> tuple[NodeDescriptor, ...]:
        """Return normalized node descriptors."""


class WorkerProvider(ProviderContract):
    """Registers/discovers workers and dispatches canonical execution requests."""

    @abstractmethod
    async def register_worker(
        self,
        worker: WorkerDescriptor,
        context: OperationContext,
    ) -> WorkerDescriptor:
        """Register or refresh one canonical worker descriptor."""

    @abstractmethod
    async def list_workers(self, context: OperationContext) -> tuple[WorkerDescriptor, ...]:
        """Return normalized worker descriptors."""

    @abstractmethod
    async def dispatch(
        self,
        worker_id: str,
        request: ExecutionRequest,
    ) -> ExecutionHandle:
        """Dispatch one canonical Run attempt to a canonical Worker."""
