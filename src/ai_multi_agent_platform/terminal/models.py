"""Canonical terminal and execution-session value objects for issue #73."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum

from ai_multi_agent_platform.contracts.types import AdapterMetadata, JsonValue
from ai_multi_agent_platform.domain import new_id, validate_id


class SessionType(StrEnum):
    AGENT = "agent"
    WORKER = "worker"
    MANUAL = "manual"
    DEBUG = "debug"
    PROCESS = "process"
    LOG_STREAM = "log_stream"


class SessionMode(StrEnum):
    READ_ONLY = "read_only"
    INTERACTIVE = "interactive"


class SessionStatus(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LOST = "lost"


TERMINAL_SESSION_STATUSES = frozenset(
    {
        SessionStatus.COMPLETED,
        SessionStatus.FAILED,
        SessionStatus.CANCELLED,
        SessionStatus.LOST,
    }
)


class StreamChannel(StrEnum):
    STDOUT = "stdout"
    STDERR = "stderr"
    LOG = "log"
    SYSTEM = "system"


class AttachmentStatus(StrEnum):
    CONNECTED = "connected"
    DETACHED = "detached"


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class TerminalDimensions:
    columns: int = 80
    rows: int = 24

    def __post_init__(self) -> None:
        if self.columns <= 0 or self.rows <= 0:
            raise ValueError("terminal dimensions must be positive")
        if self.columns > 1000 or self.rows > 1000:
            raise ValueError("terminal dimensions exceed canonical limits")

    def to_json(self) -> dict[str, JsonValue]:
        return {"columns": self.columns, "rows": self.rows}


@dataclass(frozen=True, slots=True)
class TerminalCapabilities:
    interactive_input: bool = False
    resize: bool = False
    reconnect: bool = True
    terminate: bool = True
    pty: bool = False

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "interactive_input": self.interactive_input,
            "resize": self.resize,
            "reconnect": self.reconnect,
            "terminate": self.terminate,
            "pty": self.pty,
        }


@dataclass(frozen=True, slots=True)
class TerminalDiagnostic:
    """Explicitly public, non-secret diagnostic metadata safe for northbound clients."""

    namespace: str
    values: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.namespace.strip() or any(char.isspace() for char in self.namespace):
            raise ValueError(
                "terminal diagnostic namespace must be non-empty and contain no spaces"
            )

    def to_json(self) -> dict[str, JsonValue]:
        return {"namespace": self.namespace, "values": dict(self.values)}


@dataclass(frozen=True, slots=True)
class SessionContext:
    project_id: str
    workspace_id: str
    task_id: str | None = None
    run_id: str | None = None
    worker_id: str | None = None
    node_id: str | None = None

    def __post_init__(self) -> None:
        validate_id(self.project_id, "project")
        validate_id(self.workspace_id, "workspace")
        if self.task_id is not None:
            validate_id(self.task_id, "task")
        if self.run_id is not None:
            validate_id(self.run_id, "run")
            if self.task_id is None:
                raise ValueError("run-linked terminal context requires task_id")
        if self.worker_id is not None:
            validate_id(self.worker_id, "worker")
        if self.node_id is not None:
            validate_id(self.node_id, "node")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "project_id": self.project_id,
            "workspace_id": self.workspace_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "worker_id": self.worker_id,
            "node_id": self.node_id,
        }


@dataclass(frozen=True, slots=True)
class TerminalSession:
    session_type: SessionType
    context: SessionContext
    mode: SessionMode
    owner_actor_ref: str
    adapter_id: str
    capabilities: TerminalCapabilities
    id: str = field(default_factory=lambda: new_id("terminal_session"))
    status: SessionStatus = SessionStatus.STARTING
    started_at: datetime = field(default_factory=utc_now)
    ended_at: datetime | None = None
    encoding: str = "utf-8"
    dimensions: TerminalDimensions | None = None
    policy_classification: tuple[str, ...] = ()
    inactivity_timeout_seconds: int | None = None
    retain_transcript: bool = False
    adapter_metadata: tuple[AdapterMetadata, ...] = ()
    public_diagnostics: tuple[TerminalDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.id, "terminal_session")
        if not self.owner_actor_ref.strip():
            raise ValueError("owner_actor_ref must not be blank")
        if not self.adapter_id.strip():
            raise ValueError("adapter_id must not be blank")
        if not self.encoding.strip():
            raise ValueError("encoding must not be blank")
        if any(not label.strip() for label in self.policy_classification):
            raise ValueError("policy classifications must not contain blank values")
        if self.inactivity_timeout_seconds is not None and self.inactivity_timeout_seconds <= 0:
            raise ValueError("inactivity_timeout_seconds must be positive")
        if self.mode is SessionMode.INTERACTIVE and not self.capabilities.interactive_input:
            raise ValueError("interactive session requires interactive_input capability")
        if self.capabilities.pty and self.dimensions is None:
            raise ValueError("PTY-capable session requires terminal dimensions")
        if self.status in TERMINAL_SESSION_STATUSES and self.ended_at is None:
            raise ValueError("terminal session state requires ended_at")
        if self.status not in TERMINAL_SESSION_STATUSES and self.ended_at is not None:
            raise ValueError("non-terminal session must not have ended_at")

    def transition(
        self,
        status: SessionStatus,
        *,
        occurred_at: datetime | None = None,
    ) -> TerminalSession:
        if self.status in TERMINAL_SESSION_STATUSES:
            raise ValueError("terminal session has no outgoing status transition")
        allowed = {
            SessionStatus.STARTING: {
                SessionStatus.RUNNING,
                SessionStatus.COMPLETED,
                SessionStatus.FAILED,
                SessionStatus.CANCELLED,
                SessionStatus.LOST,
            },
            SessionStatus.RUNNING: TERMINAL_SESSION_STATUSES,
        }
        if status not in allowed[self.status]:
            raise ValueError(f"invalid session transition: {self.status.value} -> {status.value}")
        return replace(
            self,
            status=status,
            ended_at=(occurred_at or utc_now()) if status in TERMINAL_SESSION_STATUSES else None,
        )

    def with_dimensions(self, dimensions: TerminalDimensions) -> TerminalSession:
        if not self.capabilities.resize:
            raise ValueError("session does not support resize")
        return replace(self, dimensions=dimensions)

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "session_type": self.session_type.value,
            "project_id": self.context.project_id,
            "workspace_id": self.context.workspace_id,
            "context": self.context.to_json(),
            "mode": self.mode.value,
            "owner_actor_ref": self.owner_actor_ref,
            "adapter_id": self.adapter_id,
            "capabilities": self.capabilities.to_json(),
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at is not None else None,
            "encoding": self.encoding,
            "dimensions": self.dimensions.to_json() if self.dimensions is not None else None,
            "policy_classification": list(self.policy_classification),
            "inactivity_timeout_seconds": self.inactivity_timeout_seconds,
            "retain_transcript": self.retain_transcript,
            "diagnostics": [item.to_json() for item in self.public_diagnostics],
        }


@dataclass(frozen=True, slots=True)
class TerminalFrame:
    session_id: str
    sequence: int
    channel: StreamChannel
    data: str
    id: str = field(default_factory=lambda: new_id("terminal_frame"))
    occurred_at: datetime = field(default_factory=utc_now)
    final: bool = False

    def __post_init__(self) -> None:
        validate_id(self.id, "terminal_frame")
        validate_id(self.session_id, "terminal_session")
        if self.sequence <= 0:
            raise ValueError("terminal frame sequence must be positive")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "sequence": self.sequence,
            "channel": self.channel.value,
            "data": self.data,
            "occurred_at": self.occurred_at.isoformat(),
            "final": self.final,
        }


@dataclass(frozen=True, slots=True)
class SessionAttachment:
    session_id: str
    actor_ref: str
    id: str = field(default_factory=lambda: new_id("terminal_attachment"))
    status: AttachmentStatus = AttachmentStatus.CONNECTED
    connected_at: datetime = field(default_factory=utc_now)
    detached_at: datetime | None = None
    after_sequence: int = 0

    def __post_init__(self) -> None:
        validate_id(self.id, "terminal_attachment")
        validate_id(self.session_id, "terminal_session")
        if not self.actor_ref.strip():
            raise ValueError("attachment actor_ref must not be blank")
        if self.after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")
        if self.status is AttachmentStatus.DETACHED and self.detached_at is None:
            raise ValueError("detached attachment requires detached_at")
        if self.status is AttachmentStatus.CONNECTED and self.detached_at is not None:
            raise ValueError("connected attachment must not have detached_at")

    def detach(self) -> SessionAttachment:
        if self.status is AttachmentStatus.DETACHED:
            return self
        return replace(self, status=AttachmentStatus.DETACHED, detached_at=utc_now())

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "actor_ref": self.actor_ref,
            "status": self.status.value,
            "connected_at": self.connected_at.isoformat(),
            "detached_at": self.detached_at.isoformat() if self.detached_at is not None else None,
            "after_sequence": self.after_sequence,
        }
