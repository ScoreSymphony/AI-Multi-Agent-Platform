"""Optional SWE-ReX proof-of-concept behind the platform Executor contract.

The adapter intentionally has no runtime dependency on SWE-ReX. A concrete client
owns provider transport/deployment details while this module proves that provider
identities and execution mechanics can remain behind the platform-owned boundary.
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

SWE_REX_UPSTREAM_REPOSITORY = "https://github.com/SWE-agent/SWE-ReX"
SWE_REX_EVALUATED_REVISION = "5c995c365dfb1fd5bc56fda688be5d8538f9931f"
SWE_REX_EVALUATED_VERSION = "1.4.0"
SWE_REX_EVALUATED_LICENSE = "MIT"

_SAFE_PROVIDER_METADATA_KEYS = frozenset(
    {
        "image",
        "platform",
        "provider_version",
        "runtime_transport",
    }
)


class SwerexExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class SwerexArtifact:
    relative_path: str
    media_type: str = "application/octet-stream"
    size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class SwerexClientRequest:
    """Adapter-private request consumed by a concrete SWE-ReX transport."""

    request_ref: str
    task_id: str
    run_id: str
    correlation_id: str
    action: str
    workspace_path: str
    step_id: str | None = None
    arguments: dict[str, JsonValue] = field(default_factory=dict)
    timeout_seconds: float | None = None
    expected_artifacts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SwerexClientResult:
    """Provider result before translation into canonical execution evidence."""

    status: SwerexExecutionStatus
    deployment_id: str | None = None
    runtime_id: str | None = None
    session_id: str | None = None
    result_code: int | None = None
    output: dict[str, JsonValue] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    artifacts: tuple[SwerexArtifact, ...] = ()
    started_at: str | None = None
    finished_at: str | None = None
    duration_seconds: float | None = None
    resources: dict[str, JsonValue] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    metadata: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SwerexHealth:
    healthy: bool
    capabilities: tuple[str, ...] = ()
    metadata: dict[str, JsonValue] = field(default_factory=dict)


class SwerexClient(Protocol):
    """Minimal provider-private seam used by the evaluation adapter."""

    async def execute(self, request: SwerexClientRequest) -> SwerexClientResult: ...

    async def cancel(self, request_ref: str) -> None: ...

    async def health(self) -> SwerexHealth: ...


_ERROR_MAP: dict[str, ExecutionErrorCategory] = {
    "invalid_request": ExecutionErrorCategory.INVALID_REQUEST,
    "unsupported_capability": ExecutionErrorCategory.UNSUPPORTED_CAPABILITY,
    "workspace_error": ExecutionErrorCategory.WORKSPACE_ERROR,
    "execution_failed": ExecutionErrorCategory.EXECUTION_FAILED,
    "timeout": ExecutionErrorCategory.TIMEOUT,
    "cancelled": ExecutionErrorCategory.CANCELLED,
    "internal": ExecutionErrorCategory.INTERNAL,
    "unavailable": ExecutionErrorCategory.INTERNAL,
    "runtime_unavailable": ExecutionErrorCategory.INTERNAL,
}


def _safe_provider_metadata(metadata: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {
        key: value
        for key, value in metadata.items()
        if key in _SAFE_PROVIDER_METADATA_KEYS
    }


class SwerexExecutor(Executor):
    """Translate canonical execution into an optional SWE-ReX backend."""

    def __init__(
        self,
        client: SwerexClient,
        workspace_root: str | Path,
        *,
        capabilities: tuple[str, ...],
        backend_kind: str = "docker",
        allow_unsandboxed_local: bool = False,
        executor_id: str = "swe-rex",
    ) -> None:
        if not executor_id.strip():
            raise ValueError("executor_id must not be blank")
        normalized_backend_kind = backend_kind.strip().casefold()
        if not normalized_backend_kind:
            raise ValueError("backend_kind must not be blank")
        if normalized_backend_kind == "local" and not allow_unsandboxed_local:
            raise ValueError(
                "SWE-ReX LocalDeployment executes directly on the host; "
                "explicit allow_unsandboxed_local=True is required"
            )
        self._client = client
        self._root = Path(workspace_root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._capabilities = tuple(dict.fromkeys(capabilities))
        self._backend_kind = normalized_backend_kind
        self._allow_unsandboxed_local = allow_unsandboxed_local
        self._executor_id = executor_id

    @property
    def descriptor(self) -> ExecutorDescriptor:
        return ExecutorDescriptor(
            executor_id=self._executor_id,
            capabilities=self._capabilities,
            metadata={
                "adapter": "swe-rex",
                "backend_kind": self._backend_kind,
                "canonical_lifecycle_owner": "platform",
                "evaluated_revision": SWE_REX_EVALUATED_REVISION,
                "evaluated_version": SWE_REX_EVALUATED_VERSION,
                "isolation_evidence": "backend-specific-required",
                "unsandboxed_local_opt_in": self._allow_unsandboxed_local,
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
                    "health_error_type": type(exc).__name__,
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
                    "direct environment projection to SWE-ReX is disabled until a "
                    "#34-safe scoped environment/secret delivery path is proven"
                ),
            )

        backend_request = SwerexClientRequest(
            request_ref=uuid4().hex,
            task_id=request.task_id,
            run_id=request.run_id,
            step_id=request.step_id,
            correlation_id=request.correlation_id,
            action=request.action,
            workspace_path=str(workspace),
            arguments=dict(request.arguments),
            timeout_seconds=request.timeout_seconds,
            expected_artifacts=request.expected_artifacts,
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
        except Exception as exc:
            return self._failure(
                request,
                started_at,
                started,
                ExecutionErrorCategory.INTERNAL,
                f"SWE-ReX client error: {type(exc).__name__}",
                retryable=True,
            )

        return self._translate_result(request, backend_result, started_at, started, workspace)

    async def _execute_backend(
        self,
        request: ExecutionRequest,
        backend_request: SwerexClientRequest,
    ) -> SwerexClientResult:
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
        backend: SwerexClientResult,
        started_at: str,
        started: float,
        workspace: Path,
    ) -> ExecutionResult:
        status = {
            SwerexExecutionStatus.SUCCEEDED: ExecutionStatus.SUCCEEDED,
            SwerexExecutionStatus.FAILED: ExecutionStatus.FAILED,
            SwerexExecutionStatus.TIMED_OUT: ExecutionStatus.TIMED_OUT,
            SwerexExecutionStatus.CANCELLED: ExecutionStatus.CANCELLED,
        }[backend.status]

        artifacts: list[ExecutionArtifact] = []
        for artifact in backend.artifacts:
            if not artifact.relative_path.strip():
                return self._failure(
                    request,
                    started_at,
                    started,
                    ExecutionErrorCategory.INTERNAL,
                    "SWE-ReX returned an empty artifact path",
                )
            artifact_path = (workspace / artifact.relative_path).resolve()
            if artifact_path != workspace and workspace not in artifact_path.parents:
                return self._failure(
                    request,
                    started_at,
                    started,
                    ExecutionErrorCategory.INTERNAL,
                    "SWE-ReX returned artifact evidence outside the execution workspace",
                )
            if not artifact_path.exists() or not artifact_path.is_file():
                return self._failure(
                    request,
                    started_at,
                    started,
                    ExecutionErrorCategory.INTERNAL,
                    "SWE-ReX returned artifact evidence before canonical collection",
                )
            artifacts.append(
                ExecutionArtifact(
                    relative_path=artifact.relative_path,
                    media_type=artifact.media_type,
                    size_bytes=artifact_path.stat().st_size,
                )
            )

        error: ExecutionError | None = None
        if status is not ExecutionStatus.SUCCEEDED:
            category = self._error_category(backend)
            message = (
                backend.error_message
                or backend.stderr
                or f"SWE-ReX execution {backend.status}"
            )
            details: dict[str, JsonValue] = {}
            if backend.error_code is not None:
                details["swe_rex_error_code"] = backend.error_code
            error = ExecutionError(
                category=category,
                message=message,
                retryable=backend.retryable,
                details=details,
            )

        provider_metadata: dict[str, JsonValue] = {
            "backend_status": backend.status.value,
            "backend_kind": self._backend_kind,
            **_safe_provider_metadata(backend.metadata),
        }
        if backend.deployment_id is not None:
            provider_metadata["deployment_id"] = backend.deployment_id
        if backend.runtime_id is not None:
            provider_metadata["runtime_id"] = backend.runtime_id
        if backend.session_id is not None:
            provider_metadata["session_id"] = backend.session_id
        if backend.error_code is not None:
            provider_metadata["error_code"] = backend.error_code

        return ExecutionResult(
            task_id=request.task_id,
            run_id=request.run_id,
            correlation_id=request.correlation_id,
            step_id=request.step_id,
            status=status,
            result_code=backend.result_code,
            output=backend.output,
            stdout=backend.stdout,
            stderr=backend.stderr,
            artifacts=tuple(artifacts),
            started_at=backend.started_at or started_at,
            finished_at=backend.finished_at or datetime.now(UTC).isoformat(),
            duration_seconds=(
                backend.duration_seconds
                if backend.duration_seconds is not None
                else monotonic() - started
            ),
            resources=backend.resources,
            error=error,
            adapter_metadata={"swe_rex": provider_metadata},
        )

    @staticmethod
    def _error_category(backend: SwerexClientResult) -> ExecutionErrorCategory:
        if backend.status is SwerexExecutionStatus.TIMED_OUT:
            return ExecutionErrorCategory.TIMEOUT
        if backend.status is SwerexExecutionStatus.CANCELLED:
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
            adapter_metadata={"swe_rex": {"adapter_failure": True}},
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
