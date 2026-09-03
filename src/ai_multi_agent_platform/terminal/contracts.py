"""Replaceable terminal/session adapter contracts.

The canonical service owns session identity, authorization and lifecycle. Adapters own
only the backend-specific stream/process handle required to implement a session.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from ai_multi_agent_platform.contracts.types import AdapterMetadata, JsonValue, OperationContext

from .models import (
    SessionContext,
    SessionMode,
    SessionStatus,
    SessionType,
    StreamChannel,
    TerminalCapabilities,
    TerminalDimensions,
)


@dataclass(frozen=True, slots=True)
class TerminalAdapterDescriptor:
    adapter_id: str
    capabilities: TerminalCapabilities
    supported_session_types: tuple[SessionType, ...]
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.adapter_id.strip():
            raise ValueError("terminal adapter_id must not be blank")
        if not self.supported_session_types:
            raise ValueError("terminal adapter must support at least one session type")


@dataclass(frozen=True, slots=True)
class SessionCreateRequest:
    session_type: SessionType
    context: SessionContext
    mode: SessionMode
    actor_ref: str
    operation: OperationContext
    adapter_id: str = "reference-terminal"
    dimensions: TerminalDimensions | None = None
    encoding: str = "utf-8"
    policy_classification: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.actor_ref.strip():
            raise ValueError("session actor_ref must not be blank")
        if not self.adapter_id.strip():
            raise ValueError("session adapter_id must not be blank")
        if not self.encoding.strip():
            raise ValueError("session encoding must not be blank")
        if self.operation.project_id is not None and self.operation.project_id != self.context.project_id:
            raise ValueError("operation project_id must match session context")
        if any(not label.strip() for label in self.policy_classification):
            raise ValueError("policy classifications must not contain blank values")


@dataclass(frozen=True, slots=True)
class AdapterSessionHandle:
    """Opaque backend handle that must never be serialized as canonical session identity."""

    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("adapter session handle must not be blank")


@dataclass(frozen=True, slots=True)
class AdapterStartResult:
    handle: AdapterSessionHandle
    status: SessionStatus
    capabilities: TerminalCapabilities
    metadata: tuple[AdapterMetadata, ...] = ()


@dataclass(frozen=True, slots=True)
class AdapterFrame:
    sequence: int
    channel: StreamChannel
    data: str
    final: bool = False

    def __post_init__(self) -> None:
        if self.sequence <= 0:
            raise ValueError("adapter frame sequence must be positive")


class TerminalSessionAdapter(ABC):
    """Backend-neutral adapter boundary for local, PTY, container or worker sessions."""

    @property
    @abstractmethod
    def descriptor(self) -> TerminalAdapterDescriptor: ...

    @abstractmethod
    async def start(self, request: SessionCreateRequest) -> AdapterStartResult: ...

    @abstractmethod
    async def read_frames(
        self,
        handle: AdapterSessionHandle,
        *,
        after_sequence: int = 0,
    ) -> tuple[AdapterFrame, ...]: ...

    @abstractmethod
    async def stream_frames(
        self,
        handle: AdapterSessionHandle,
        *,
        after_sequence: int = 0,
    ) -> AsyncIterator[AdapterFrame]: ...

    @abstractmethod
    async def send_input(self, handle: AdapterSessionHandle, data: str) -> None: ...

    @abstractmethod
    async def resize(
        self,
        handle: AdapterSessionHandle,
        dimensions: TerminalDimensions,
    ) -> None: ...

    @abstractmethod
    async def terminate(self, handle: AdapterSessionHandle, *, reason: str | None = None) -> None: ...

    @abstractmethod
    async def status(self, handle: AdapterSessionHandle) -> SessionStatus: ...
