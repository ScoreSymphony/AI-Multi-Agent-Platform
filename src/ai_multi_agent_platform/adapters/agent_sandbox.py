"""Optional Agent-Sandbox proof-of-concept behind the platform Executor contract.

The adapter deliberately contains no Agent-Sandbox or E2B runtime dependency. A
concrete client owns provider transport/lifecycle details while this module proves
that provider-private sandbox identities and controls can remain behind the
platform-owned execution boundary.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from time import monotonic
from typing import Protocol
from uuid import uuid4

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.execution.contracts import (
    ExecutionArtifact,
    ExecutionError,
    ExecutionErrorCategory,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    Executor,
    ExecutorDescriptor,
)

AGENT_SANDBOX_UPSTREAM_REPOSITORY = "https://github.com/agent-sandbox/agent-sandbox"
AGENT_SANDBOX_EVALUATED_REVISION = "d1b7ac007debcb1ba8de91c76afb49bee90d096a"
AGENT_SANDBOX_EVALUATED_LICENSE = "Apache-2.0"

_SAFE_PROVIDER_METADATA_KEYS = frozenset(
    {
        "image_digest",
        "provider_version",
        "runtime_class",
        "sandbox_state",
        "template_id",
        "transport",
    }
)


class AgentSandboxExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class AgentSandboxSecurityProfile:
    """Provider-private controls projected from an already-authorized platform request.

    This is intentionally not a canonical policy model. The future production
    client must derive it from platform-owned authorization/egress policy rather
    than accepting arbitrary agent input.
    """

    allow_internet_access: bool = False
    allow_out: tuple[str, ...] = ()
    deny_out: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentSandboxArtifact:
    relative_path: str
    media_type: str = "application/octet-stream"
    size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class AgentSandboxClientRequest:
    """Adapter-private request consumed by a concrete Agent-Sandbox transport."""

    request_ref: str
    task_id: str
    run_id: str
    correlation_id: str
    action: str
    workspace_path: str
    step_id: str | None = None
    arguments: dict[str, JsonValue] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float | None = None
    policy_context: dict[str, JsonValue] = field(default_factory=dict)
    expected_artifacts: tuple[str, ...] = ()
    security_profile: AgentSandboxSecurityProfile = field(
        default_factory=AgentSandboxSecurityProfile
    )


@dataclass(frozen=True, slots=True)
class AgentSandboxClientResult:
    """Provider result before translation into canonical execution evidence."""

    status: AgentSandboxExecutionStatus
    sandbox_id: str | None = None
    session_id: str | None = None
    snapshot_id: str | None = None
    result_code: int | None = None
    output: dict[str, JsonValue] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    artifacts: tuple[AgentSandboxArtifact, ...] = ()
    started_at: str | None = None
    finished_at: str | None = None
    duration_seconds: float | None = None
    resources: dict[str, JsonValue] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    metadata: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentSandboxHealth:
    healthy: bool
    capabilities: tuple[str, ...] = ()
    metadata: dict[str, JsonValue] = field(default_factory=dict)


class AgentSandboxClient(Protocol):
    """Minimal provider-private seam used by the evaluation adapter."""

    async def execute(self, request: AgentSandboxClientRequest) -> AgentSandboxClientResult: ...

    async def cancel(self, request_ref: str) -> None: ...

    async def health(self) -> AgentSandboxHealth: ...


_ERROR_MAP: dict[str, ExecutionErrorCategory] = {
    "invalid_request": ExecutionErrorCategory.INVALID_REQUEST,
    "unsupported_capability": ExecutionErrorCategory.UNSUPPORTED_CAPABILITY,
    "workspace_error": ExecutionErrorCategory.WORKSPACE_ERROR,
    "execution_failed": ExecutionErrorCategory.EXECUTION_FAILED,
    "timeout": ExecutionErrorCategory.TIMEOUT,
    "cancelled": ExecutionErrorCategory.CANCELLED,
    "internal": ExecutionErrorCategory.INTERNAL,
    "unavailable": ExecutionErrorCategory.INTERNAL,
    "sandbox_unavailable": ExecutionErrorCategory.INTERNAL,
}
_PROVIDER_INFRASTRUCTURE_ERROR_CODES = frozenset(
    {
        "internal",
        "unavailable",
        "sandbox_unavailable",
    }
)
_PROVIDER_FAILURE_MESSAGE = "Agent-Sandbox provider execution failed"


def _safe_provider_metadata(metadata: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {key: value for key, value in metadata.items() if key in _SAFE_PROVIDER_METADATA_KEYS}


def _safe_provider_error_code(error_code: str | None) -> str | None:
    if error_code is None or error_code not in _ERROR_MAP:
        return None
    return error_code


def _requires_provider_failure_redaction(backend: AgentSandboxClientResult) -> bool:
    if backend.status is AgentSandboxExecutionStatus.SUCCEEDED:
        return False
    safe_error_code = _safe_provider_error_code(backend.error_code)
    if backend.error_code is not None and safe_error_code is None:
        return True
    return safe_error_code in _PROVIDER_INFRASTRUCTURE_ERROR_CODES


class AgentSandboxExecutor(Executor):
    """Translate canonical execution into an optional Agent-Sandbox backend."""

    def __init__(
        self,
        client: AgentSandboxClient,
        workspace_root: str | Path,
        *,
        capabilities: tuple[str, ...],
        security_profile: AgentSandboxSecurityProfile | None = None,
        executor_id: str = "agent-sandbox",
    ) -> None:
        if not executor_id.strip():
            raise ValueError("executor_id must not be blank")
        self._client = client
        self._root = Path(workspace_root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._capabilities = tuple(dict.fromkeys(capabilities))
        self._security_profile = security_profile or AgentSandboxSecurityProfile()
        self._executor_id = executor_id

    @property
    def descriptor(self) -> ExecutorDescriptor:
        return ExecutorDescriptor(
            executor_id=self._executor_id,
            capabilities=self._capabilities,
            metadata={
                "adapter": "agent-sandbox",
                "canonical_lifecycle_owner": "platform",
                "evaluated_revision": AGENT_SANDBOX_EVALUATED_REVISION,
                "internet_access_default": self._security_profile.allow_internet_access,
            },
        )

    async def health(self) -> ExecutorDescriptor:
        try:
            health = await self._client.health()
        except Exception as exc:
            return ExecutorDescriptor(
                executor_id=self._executor_id,
                capabilities=self._capabilities,
                healthy=False,
                metadata={
                    **self.descriptor.metadata,
                    "health_error": "provider health check failed",
                    "health_exception_type": type(exc).__name__,
                },
            )
        capabilities = health.capabilities or self._capabilities
        return ExecutorDescriptor(
            executor_id=self._executor_id,
            capabilities=capabilities,
            healthy=health.healthy,
            metadata={
                **self.descriptor.metadata,
                **_safe_provider_metadata(health.metadata),
            },
        )

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        started_at = datetime.now(UTC).isoformat()
        started = monotonic()

        try:
            workspace = self._workspace(request.workspace)
        except ValueError as exc:
            return self._failure(
                request,
                started_at,
                started,
                ExecutionErrorCategory.WORKSPACE_ERROR,
                str(exc),
            )

        if request.action not in self._capabilities:
            return self._failure(
                request,
                started_at,
                started,
                ExecutionErrorCategory.UNSUPPORTED_CAPABILITY,
                f"unsupported action: {request.action}",
            )
        if request.cancellation is not None and request.cancellation.cancelled:
            return self._cancelled(request, started_at, started)
        if request.environment:
            return self._failure(
                request,
                started_at,
                started,
                ExecutionErrorCategory.INVALID_REQUEST,
                (
                    "direct environment projection to Agent-Sandbox is disabled until a "
                    "#34-safe environment/secret delivery path is proven"
                ),
            )

        backend_request = AgentSandboxClientRequest(
            request_ref=uuid4().hex,
            task_id=request.task_id,
            run_id=request.run_id,
            step_id=request.step_id,
            correlation_id=request.correlation_id,
            action=request.action,
            workspace_path=str(workspace),
            arguments=dict(request.arguments),
            environment={},
            timeout_seconds=request.timeout_seconds,
            policy_context={},
            expected_artifacts=request.expected_artifacts,
            security_profile=self._security_profile,
        )

        try:
            backend_result = await self._execute_backend(request, backend_request)
        except TimeoutError:
            await self._cancel_backend(backend_request.request_ref)
            return self._failure(
                request,
                started_at,
                started,
                ExecutionErrorCategory.TIMEOUT,
                "execution timed out",
                status=ExecutionStatus.TIMED_OUT,
            )
        except asyncio.CancelledError:
            await self._cancel_backend(backend_request.request_ref)
            return self._cancelled(request, started_at, started)
        except Exception:
            return self._failure(
                request,
                started_at,
                started,
                ExecutionErrorCategory.INTERNAL,
                _PROVIDER_FAILURE_MESSAGE,
                retryable=True,
            )

        return self._translate_result(request, backend_result, started_at, started, workspace)

    async def _execute_backend(
        self,
        request: ExecutionRequest,
        backend_request: AgentSandboxClientRequest,
    ) -> AgentSandboxClientResult:
        if request.cancellation is None:
            if request.timeout_seconds is None:
                return await self._client.execute(backend_request)
            return await asyncio.wait_for(
                self._client.execute(backend_request),
                request.timeout_seconds,
            )

        result_task = asyncio.create_task(self._client.execute(backend_request))
        cancel_task = asyncio.create_task(request.cancellation.wait())
        try:
            done, _ = await asyncio.wait(
                {result_task, cancel_task},
                timeout=request.timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise TimeoutError
            if cancel_task in done and request.cancellation.cancelled:
                raise asyncio.CancelledError
            return result_task.result()
        finally:
            for task in (result_task, cancel_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(result_task, cancel_task, return_exceptions=True)

    def _workspace(self, workspace: str) -> Path:
        candidate = (self._root / workspace).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise ValueError("workspace escapes configured workspace root")
        if not candidate.exists() or not candidate.is_dir():
            raise ValueError("workspace is missing or unavailable")
        return candidate

    async def _cancel_backend(self, request_ref: str) -> None:
        try:
            await self._client.cancel(request_ref)
        except Exception:
            return

    def _translate_result(
        self,
        request: ExecutionRequest,
        backend: AgentSandboxClientResult,
        started_at: str,
        started: float,
        workspace: Path,
    ) -> ExecutionResult:
        status = {
            AgentSandboxExecutionStatus.SUCCEEDED: ExecutionStatus.SUCCEEDED,
            AgentSandboxExecutionStatus.FAILED: ExecutionStatus.FAILED,
            AgentSandboxExecutionStatus.TIMED_OUT: ExecutionStatus.TIMED_OUT,
            AgentSandboxExecutionStatus.CANCELLED: ExecutionStatus.CANCELLED,
        }[backend.status]
        redact_provider_failure = _requires_provider_failure_redaction(backend)

        artifacts: list[ExecutionArtifact] = []
        if not redact_provider_failure:
            for artifact in backend.artifacts:
                if not artifact.relative_path.strip():
                    return self._failure(
                        request,
                        started_at,
                        started,
                        ExecutionErrorCategory.INTERNAL,
                        "Agent-Sandbox returned an empty artifact path",
                    )
                artifact_path = (workspace / artifact.relative_path).resolve()
                if artifact_path != workspace and workspace not in artifact_path.parents:
                    return self._failure(
                        request,
                        started_at,
                        started,
                        ExecutionErrorCategory.INTERNAL,
                        "Agent-Sandbox returned artifact evidence outside the execution workspace",
                    )
                artifacts.append(
                    ExecutionArtifact(
                        relative_path=artifact.relative_path,
                        media_type=artifact.media_type,
                        size_bytes=artifact.size_bytes,
                    )
                )

        safe_error_code = _safe_provider_error_code(backend.error_code)
        error: ExecutionError | None = None
        if status is not ExecutionStatus.SUCCEEDED:
            category = self._error_category(backend)
            message = (
                _PROVIDER_FAILURE_MESSAGE
                if redact_provider_failure
                else (
                    backend.error_message
                    or backend.stderr
                    or f"Agent-Sandbox execution {backend.status}"
                )
            )
            details: dict[str, JsonValue] = {}
            if safe_error_code is not None:
                details["agent_sandbox_error_code"] = safe_error_code
            error = ExecutionError(
                category=category,
                message=message,
                retryable=backend.retryable,
                details=details,
            )

        provider_metadata: dict[str, JsonValue] = {
            "backend_status": backend.status.value,
            **_safe_provider_metadata(backend.metadata),
        }
        if backend.sandbox_id is not None:
            provider_metadata["sandbox_id"] = backend.sandbox_id
        if backend.session_id is not None:
            provider_metadata["session_id"] = backend.session_id
        if backend.snapshot_id is not None:
            provider_metadata["snapshot_id"] = backend.snapshot_id
        if safe_error_code is not None:
            provider_metadata["error_code"] = safe_error_code

        return ExecutionResult(
            task_id=request.task_id,
            run_id=request.run_id,
            correlation_id=request.correlation_id,
            step_id=request.step_id,
            status=status,
            result_code=backend.result_code,
            output={} if redact_provider_failure else backend.output,
            stdout="" if redact_provider_failure else backend.stdout,
            stderr=_PROVIDER_FAILURE_MESSAGE if redact_provider_failure else backend.stderr,
            artifacts=tuple(artifacts),
            started_at=(
                started_at if redact_provider_failure else (backend.started_at or started_at)
            ),
            finished_at=(
                datetime.now(UTC).isoformat()
                if redact_provider_failure
                else (backend.finished_at or datetime.now(UTC).isoformat())
            ),
            duration_seconds=(
                monotonic() - started
                if redact_provider_failure
                else (
                    backend.duration_seconds
                    if backend.duration_seconds is not None
                    else monotonic() - started
                )
            ),
            resources={} if redact_provider_failure else backend.resources,
            error=error,
            adapter_metadata={"agent_sandbox": provider_metadata},
        )

    @staticmethod
    def _error_category(backend: AgentSandboxClientResult) -> ExecutionErrorCategory:
        if backend.status is AgentSandboxExecutionStatus.TIMED_OUT:
            return ExecutionErrorCategory.TIMEOUT
        if backend.status is AgentSandboxExecutionStatus.CANCELLED:
            return ExecutionErrorCategory.CANCELLED
        if backend.error_code is not None:
            return _ERROR_MAP.get(backend.error_code, ExecutionErrorCategory.INTERNAL)
        return ExecutionErrorCategory.EXECUTION_FAILED

    def _failure(
        self,
        request: ExecutionRequest,
        started_at: str,
        started: float,
        category: ExecutionErrorCategory,
        message: str,
        *,
        status: ExecutionStatus = ExecutionStatus.FAILED,
        code: int | None = None,
        retryable: bool = False,
    ) -> ExecutionResult:
        return ExecutionResult(
            task_id=request.task_id,
            run_id=request.run_id,
            correlation_id=request.correlation_id,
            step_id=request.step_id,
            status=status,
            result_code=code,
            stderr=message,
            error=ExecutionError(category=category, message=message, retryable=retryable),
            started_at=started_at,
            finished_at=datetime.now(UTC).isoformat(),
            duration_seconds=monotonic() - started,
            adapter_metadata={"agent_sandbox": {"adapter_failure": True}},
        )

    def _cancelled(
        self,
        request: ExecutionRequest,
        started_at: str,
        started: float,
    ) -> ExecutionResult:
        return self._failure(
            request,
            started_at,
            started,
            ExecutionErrorCategory.CANCELLED,
            "execution cancelled",
            status=ExecutionStatus.CANCELLED,
        )
