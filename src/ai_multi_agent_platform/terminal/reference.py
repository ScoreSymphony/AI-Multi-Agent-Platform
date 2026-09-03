"""Deterministic safety-first terminal adapter for tests and local development."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from uuid import uuid4

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import AdapterMetadata

from .contracts import (
    AdapterFrame,
    AdapterSessionHandle,
    AdapterStartResult,
    SessionCreateRequest,
    TerminalAdapterDescriptor,
    TerminalSessionAdapter,
)
from .models import (
    TERMINAL_SESSION_STATUSES,
    SessionMode,
    SessionStatus,
    SessionType,
    StreamChannel,
    TerminalCapabilities,
    TerminalDimensions,
)


@dataclass(slots=True)
class _ReferenceState:
    mode: SessionMode
    status: SessionStatus = SessionStatus.RUNNING
    dimensions: TerminalDimensions | None = None
    frames: list[AdapterFrame] = field(default_factory=list)
    next_sequence: int = 1

    def append(self, channel: StreamChannel, data: str, *, final: bool = False) -> None:
        self.frames.append(
            AdapterFrame(
                sequence=self.next_sequence,
                channel=channel,
                data=data,
                final=final,
            )
        )
        self.next_sequence += 1


class ReferenceTerminalAdapter(TerminalSessionAdapter):
    """Reference implementation that never exposes an arbitrary host shell.

    Read-only sessions emit deterministic log output. Interactive sessions only echo
    submitted input. A future PTY/container/worker adapter may implement the same
    contract without changing canonical session identity or Control Plane semantics.
    """

    _CAPABILITIES = TerminalCapabilities(
        interactive_input=True,
        resize=False,
        reconnect=True,
        terminate=True,
        pty=False,
    )

    def __init__(self, *, poll_interval_seconds: float = 0.01) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self._poll_interval_seconds = poll_interval_seconds
        self._states: dict[str, _ReferenceState] = {}

    @property
    def descriptor(self) -> TerminalAdapterDescriptor:
        return TerminalAdapterDescriptor(
            adapter_id="reference-terminal",
            capabilities=self._CAPABILITIES,
            supported_session_types=(
                SessionType.MANUAL,
                SessionType.DEBUG,
                SessionType.PROCESS,
                SessionType.LOG_STREAM,
            ),
            metadata={"arbitrary_host_shell": False, "pty": False, "deterministic": True},
        )

    async def start(self, request: SessionCreateRequest) -> AdapterStartResult:
        if request.session_type not in self.descriptor.supported_session_types:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"reference terminal does not support {request.session_type.value!r} sessions",
                provider_id=self.descriptor.adapter_id,
            )
        if request.mode is SessionMode.INTERACTIVE and request.session_type is SessionType.LOG_STREAM:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "log-stream sessions are read-only in the reference adapter",
                provider_id=self.descriptor.adapter_id,
            )
        handle = AdapterSessionHandle(f"reference-session-{uuid4()}")
        state = _ReferenceState(mode=request.mode, dimensions=request.dimensions)
        if request.mode is SessionMode.READ_ONLY:
            state.append(StreamChannel.LOG, "reference session attached\n")
        else:
            state.append(StreamChannel.SYSTEM, "reference interactive session ready\n")
        self._states[handle.value] = state
        capabilities = TerminalCapabilities(
            interactive_input=request.mode is SessionMode.INTERACTIVE,
            resize=False,
            reconnect=True,
            terminate=True,
            pty=False,
        )
        return AdapterStartResult(
            handle=handle,
            status=SessionStatus.RUNNING,
            capabilities=capabilities,
            metadata=(
                AdapterMetadata(
                    namespace="reference-terminal",
                    values={"arbitrary_host_shell": False, "deterministic": True},
                ),
            ),
        )

    async def read_frames(
        self,
        handle: AdapterSessionHandle,
        *,
        after_sequence: int = 0,
    ) -> tuple[AdapterFrame, ...]:
        if after_sequence < 0:
            raise ContractError(ErrorCode.INVALID_REQUEST, "after_sequence must be non-negative")
        state = self._state(handle)
        return tuple(frame for frame in state.frames if frame.sequence > after_sequence)

    async def stream_frames(
        self,
        handle: AdapterSessionHandle,
        *,
        after_sequence: int = 0,
    ) -> AsyncIterator[AdapterFrame]:
        if after_sequence < 0:
            raise ContractError(ErrorCode.INVALID_REQUEST, "after_sequence must be non-negative")
        cursor = after_sequence
        while True:
            state = self._state(handle)
            pending = [frame for frame in state.frames if frame.sequence > cursor]
            for frame in pending:
                cursor = frame.sequence
                yield frame
            if state.status in TERMINAL_SESSION_STATUSES and not pending:
                return
            await asyncio.sleep(self._poll_interval_seconds)

    async def send_input(self, handle: AdapterSessionHandle, data: str) -> None:
        state = self._state(handle)
        if state.status in TERMINAL_SESSION_STATUSES:
            raise ContractError(ErrorCode.CONFLICT, "terminal session is no longer running")
        if state.mode is not SessionMode.INTERACTIVE:
            raise ContractError(ErrorCode.FORBIDDEN, "terminal session is read-only")
        state.append(StreamChannel.STDOUT, data)

    async def resize(
        self,
        handle: AdapterSessionHandle,
        dimensions: TerminalDimensions,
    ) -> None:
        self._state(handle)
        del dimensions
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "reference terminal does not provide a PTY resize capability",
            provider_id=self.descriptor.adapter_id,
        )

    async def terminate(self, handle: AdapterSessionHandle, *, reason: str | None = None) -> None:
        state = self._state(handle)
        if state.status in TERMINAL_SESSION_STATUSES:
            return
        state.status = SessionStatus.CANCELLED
        message = "session terminated"
        if reason is not None and reason.strip():
            message = f"{message}: {reason.strip()}"
        state.append(StreamChannel.SYSTEM, f"{message}\n", final=True)

    async def status(self, handle: AdapterSessionHandle) -> SessionStatus:
        return self._state(handle).status

    def complete(self, handle: AdapterSessionHandle) -> None:
        """Deterministic test/development hook for backend-driven completion."""

        state = self._state(handle)
        if state.status in TERMINAL_SESSION_STATUSES:
            return
        state.status = SessionStatus.COMPLETED
        state.append(StreamChannel.SYSTEM, "session completed\n", final=True)

    def lose(self, handle: AdapterSessionHandle, reason: str = "backend session lost") -> None:
        """Deterministic hook that models later worker/node loss."""

        state = self._state(handle)
        if state.status in TERMINAL_SESSION_STATUSES:
            return
        state.status = SessionStatus.LOST
        state.append(StreamChannel.SYSTEM, f"{reason}\n", final=True)

    def _state(self, handle: AdapterSessionHandle) -> _ReferenceState:
        try:
            return self._states[handle.value]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                "terminal adapter session handle was not found",
                provider_id=self.descriptor.adapter_id,
            ) from exc
