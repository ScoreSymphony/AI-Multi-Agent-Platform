"""Optional NVIDIA OpenShell proof-of-concept behind the canonical Executor contract.

The adapter deliberately contains no OpenShell runtime dependency. A concrete client
owns OpenShell transport and sandbox lifecycle details while this module proves that
provider-private sandbox, gateway, runtime and policy identities can remain behind the
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

OPENSHELL_UPSTREAM_REPOSITORY = "https://github.com/NVIDIA/OpenShell"
OPENSHELL_EVALUATED_REVISION = "5b9daab9351b1e053f9a5e0ce4c899f5d3f674b0"
OPENSHELL_EVALUATED_LICENSE = "Apache-2.0"
OPENSHELL_REVIEWED_RUNTIME_PROFILE = "docker"

_SAFE_PROVIDER_METADATA_KEYS = frozenset(
    {
        "compute_driver",
        "image_digest",
        "openshell_version",
        "policy_revision",
        "runtime_profile",
        "sandbox_state",
        "transport",
    }
)


class _ExecutionCancellationRequested(Exception):
    """Internal signal for canonical request-token cancellation, not task cancellation."""


class OpenShellExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class OpenShellPolicyProjection:
    """Provider-private enforcement projection derived from platform-owned policy.

    The fields model only the adapter seam. They are not a canonical authorization,
    egress, secret or capability policy model and must not be populated directly from
    agent-controlled input.
    """

    default_deny_egress: bool = True
    allowed_hosts: tuple[str, ...] = ()
    allowed_http: tuple[str, ...] = ()
    provider_bindings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OpenShellArtifact:
    relative_path: str
    media_type: str = "application/octet-stream"
    size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class OpenShellClientRequest:
    """Adapter-private request consumed by a concrete OpenShell transport."""

    request_ref: str
    task_id: str
    run_id: str
    correlation_id: str
    action: str
    workspace_path: str
    runtime_profile: str
    step_id: str | None = None
    arguments: dict[str, JsonValue] = field(default_factory=dict)
    timeout_seconds: float | None = None
    expected_artifacts: tuple[str, ...] = ()
    policy_projection: OpenShellPolicyProjection = field(default_factory=OpenShellPolicyProjection)


@dataclass(frozen=True, slots=True)
class OpenShellClientResult:
    """Provider result before translation into canonical execution evidence."""

    status: OpenShellExecutionStatus
    sandbox_id: str | None = None
    gateway_id: str | None = None
    runtime_id: str | None = None
    result_code: int | None = None
    output: dict[str, JsonValue] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    artifacts: tuple[OpenShellArtifact, ...] = ()
    started_at: str | None = None
    finished_at: str | None = None
    duration_seconds: float | None = None
    resources: dict[str, JsonValue] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    metadata: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OpenShellHealth:
    healthy: bool
    capabilities: tuple[str, ...] = ()
    metadata: dict[str, JsonValue] = field(default_factory=dict)


class OpenShellClient(Protocol):
    """Minimal provider-private seam used by the evaluation adapter."""

    async def execute(self, request: OpenShellClientRequest) -> OpenShellClientResult: ...

    async def cancel(self, request_ref: str) -> None: ...

    async def health(self) -> OpenShellHealth: ...


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
    "gateway_unavailable": ExecutionErrorCategory.INTERNAL,
    "policy_unavailable": ExecutionErrorCategory.INTERNAL,
}
_PROVIDER_INFRASTRUCTURE_ERROR_CODES = frozenset(
    {"internal", "unavailable", "sandbox_unavailable", "gateway_unavailable", "policy_unavailable"}
)
_PROVIDER_FAILURE_MESSAGE = "OpenShell provider execution failed"


def _safe_provider_metadata(metadata: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {key: value for key, value in metadata.items() if key in _SAFE_PROVIDER_METADATA_KEYS}


def _safe_provider_error_code(error_code: str | None) -> str | None:
    if error_code is None or error_code not in _ERROR_MAP:
        return None
    return error_code


def _requires_provider_failure_redaction(backend: OpenShellClientResult) -> bool:
    if backend.status is OpenShellExecutionStatus.SUCCEEDED:
        return False
    safe_error_code = _safe_provider_error_code(backend.error_code)
    if backend.error_code is not None and safe_error_code is None:
        return True
    return safe_error_code in _PROVIDER_INFRASTRUCTURE_ERROR_CODES


class OpenShellExecutor(Executor):
    """Translate canonical execution into an optional OpenShell backend."""

    def __init__(
        self,
        client: OpenShellClient,
        workspace_root: str | Path,
        *,
        capabilities: tuple[str, ...],
        runtime_profile: str = OPENSHELL_REVIEWED_RUNTIME_PROFILE,
        policy_projection: OpenShellPolicyProjection | None = None,
        cancel_timeout_seconds: float = 1.0,
        executor_id: str = "openshell",
    ) -> None:
        if not executor_id.strip():
            raise ValueError("executor_id must not be blank")
        if not runtime_profile.strip():
            raise ValueError("runtime_profile must not be blank")
        if cancel_timeout_seconds <= 0:
            raise ValueError("cancel_timeout_seconds must be greater than zero")
        self._client = client
        self._root = Path(workspace_root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._capabilities = tuple(dict.fromkeys(capabilities))
        self._runtime_profile = runtime_profile
        self._policy_projection = policy_projection or OpenShellPolicyProjection()
        self._cancel_timeout_seconds = cancel_timeout_seconds
        self._executor_id = executor_id

    @property
    def descriptor(self) -> ExecutorDescriptor:
        return ExecutorDescriptor(
            executor_id=self._executor_id,
            capabilities=self._capabilities,
            metadata={
                "adapter": "openshell",
                "canonical_lifecycle_owner": "platform",
                "canonical_policy_owner": "platform",
                "evaluated_revision": OPENSHELL_EVALUATED_REVISION,
                "runtime_profile": self._runtime_profile,
                "default_deny_egress": self._policy_projection.default_deny_egress,
            },
        )

    async def health(self) -> ExecutorDescriptor:
        try:
            health = await self._client.health()
        # error-boundary: allow-broad-catch=boundary external provider health boundary
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
                    "direct environment projection to OpenShell is disabled until a safe scoped "
                    "provider/credential binding path is proven"
                ),
            )

        backend_request = OpenShellClientRequest(
            request_ref=uuid4().hex,
            task_id=request.task_id,
            run_id=request.run_id,
            step_id=request.step_id,
            correlation_id=request.correlation_id,
            action=request.action,
            workspace_path=str(workspace),
            runtime_profile=self._runtime_profile,
            arguments=dict(request.arguments),
            timeout_seconds=request.timeout_seconds,
            expected_artifacts=request.expected_artifacts,
            policy_projection=self._policy_projection,
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
                retryable=True,
            )
        except _ExecutionCancellationRequested:
            await self._cancel_backend(backend_request.request_ref)
            return self._cancelled(request, started_at, started)
        except asyncio.CancelledError:
            await self._cancel_backend(backend_request.request_ref)
            raise
        # error-boundary: allow-broad-catch=translation external execution provider boundary
        except Exception:
            return self._failure(
                request,
                started_at,
                started,
                ExecutionErrorCategory.INTERNAL,
                _PROVIDER_FAILURE_MESSAGE,
                retryable=False,
            )

        return self._translate_result(request, backend_result, started_at, started, workspace)

    async def _execute_backend(
        self,
        request: ExecutionRequest,
        backend_request: OpenShellClientRequest,
    ) -> OpenShellClientResult:
        if request.cancellation is None:
            if request.timeout_seconds is None:
                return await self._client.execute(backend_request)
            return await asyncio.wait_for(
                self._client.execute(backend_request), request.timeout_seconds
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
                raise _ExecutionCancellationRequested
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
        cancel_task = asyncio.create_task(self._client.cancel(request_ref))
        try:
            done, _ = await asyncio.wait(
                {cancel_task},
                timeout=self._cancel_timeout_seconds,
                return_when=asyncio.ALL_COMPLETED,
            )
            if not done:
                cancel_task.cancel()
                return
            cancel_task.result()
        except asyncio.CancelledError:
            cancel_task.cancel()
            raise
        # error-boundary: allow-broad-catch=cleanup best-effort provider cancellation
        except Exception:
            return

    def _translate_result(
        self,
        request: ExecutionRequest,
        backend: OpenShellClientResult,
        started_at: str,
        started: float,
        workspace: Path,
    ) -> ExecutionResult:
        status = {
            OpenShellExecutionStatus.SUCCEEDED: ExecutionStatus.SUCCEEDED,
            OpenShellExecutionStatus.FAILED: ExecutionStatus.FAILED,
            OpenShellExecutionStatus.TIMED_OUT: ExecutionStatus.TIMED_OUT,
            OpenShellExecutionStatus.CANCELLED: ExecutionStatus.CANCELLED,
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
                        "OpenShell returned an empty artifact path",
                    )
                artifact_path = (workspace / artifact.relative_path).resolve()
                if artifact_path != workspace and workspace not in artifact_path.parents:
                    return self._failure(
                        request,
                        started_at,
                        started,
                        ExecutionErrorCategory.INTERNAL,
                        "OpenShell returned artifact evidence outside the execution workspace",
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
            message = (
                _PROVIDER_FAILURE_MESSAGE
                if redact_provider_failure
                else backend.error_message
                or backend.stderr
                or f"OpenShell execution {backend.status}"
            )
            details: dict[str, JsonValue] = {}
            if safe_error_code is not None:
                details["openshell_error_code"] = safe_error_code
            error = ExecutionError(
                category=self._error_category(backend),
                message=message,
                retryable=backend.retryable,
                details=details,
            )

        provider_metadata: dict[str, JsonValue] = {
            "backend_status": backend.status.value,
            "runtime_profile": self._runtime_profile,
            **_safe_provider_metadata(backend.metadata),
        }
        if backend.sandbox_id is not None:
            provider_metadata["sandbox_id"] = backend.sandbox_id
        if backend.gateway_id is not None:
            provider_metadata["gateway_id"] = backend.gateway_id
        if backend.runtime_id is not None:
            provider_metadata["runtime_id"] = backend.runtime_id
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
            adapter_metadata={"openshell": provider_metadata},
        )

    @staticmethod
    def _error_category(backend: OpenShellClientResult) -> ExecutionErrorCategory:
        if backend.status is OpenShellExecutionStatus.TIMED_OUT:
            return ExecutionErrorCategory.TIMEOUT
        if backend.status is OpenShellExecutionStatus.CANCELLED:
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
        retryable: bool = False,
    ) -> ExecutionResult:
        return ExecutionResult(
            task_id=request.task_id,
            run_id=request.run_id,
            correlation_id=request.correlation_id,
            step_id=request.step_id,
            status=status,
            started_at=started_at,
            finished_at=datetime.now(UTC).isoformat(),
            duration_seconds=monotonic() - started,
            error=ExecutionError(category=category, message=message, retryable=retryable),
            adapter_metadata={"openshell": {"runtime_profile": self._runtime_profile}},
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
