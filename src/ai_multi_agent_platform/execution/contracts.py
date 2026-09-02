"""Canonical execution contracts owned by the platform."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import RunStatus

ExecutionStatus = RunStatus


class ExecutionErrorCategory(StrEnum):
    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    WORKSPACE_ERROR = "workspace_error"
    EXECUTION_FAILED = "execution_failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class ExecutionError:
    category: ExecutionErrorCategory
    message: str
    retryable: bool = False
    details: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExecutionArtifact:
    relative_path: str
    media_type: str = "application/octet-stream"
    size_bytes: int | None = None


class CancellationToken:
    """Portable in-process cancellation primitive for one execution attempt."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    task_id: str
    run_id: str
    correlation_id: str
    action: str
    workspace: str
    step_id: str | None = None
    arguments: dict[str, JsonValue] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float | None = None
    cancellation: CancellationToken | None = None
    policy_context: dict[str, JsonValue] = field(default_factory=dict)
    expected_artifacts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("task_id", "run_id", "correlation_id", "action", "workspace"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be blank")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    task_id: str
    run_id: str
    correlation_id: str
    status: ExecutionStatus
    step_id: str | None = None
    result_code: int | None = None
    output: dict[str, JsonValue] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    artifacts: tuple[ExecutionArtifact, ...] = ()
    started_at: str | None = None
    finished_at: str | None = None
    duration_seconds: float | None = None
    resources: dict[str, JsonValue] = field(default_factory=dict)
    error: ExecutionError | None = None
    adapter_metadata: dict[str, dict[str, JsonValue]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExecutorDescriptor:
    executor_id: str
    capabilities: tuple[str, ...]
    healthy: bool = True
    metadata: dict[str, JsonValue] = field(default_factory=dict)


class Executor(ABC):
    """Backend-neutral execution seam. Implementations must not own retry policy."""

    @property
    @abstractmethod
    def descriptor(self) -> ExecutorDescriptor:
        raise NotImplementedError

    @abstractmethod
    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        raise NotImplementedError

    async def health(self) -> ExecutorDescriptor:
        return self.descriptor

    async def cancel(self, token: CancellationToken) -> None:
        token.cancel()
