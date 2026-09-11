"""Build-command execution and lifecycle binding for application releases."""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import replace
from datetime import UTC, datetime
from math import ceil
from pathlib import Path, PurePosixPath
from time import monotonic
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.configuration import SecretAccessContext, SecretProvider
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    ExecutionHandle,
    ExecutionSnapshot,
    ExecutionStatus,
    HealthStatus,
    LifecycleBackend,
    OperationContext,
    ProviderDescriptor,
)
from ai_multi_agent_platform.contracts import ExecutionRequest as KernelExecutionRequest
from ai_multi_agent_platform.data import DataAccessContext, FileProvider
from ai_multi_agent_platform.execution import (
    CancellationToken,
    ExecutionArtifact,
    ExecutionError,
    ExecutionErrorCategory,
    ExecutionRequest,
    ExecutionResult,
    Executor,
    ExecutorDescriptor,
)
from ai_multi_agent_platform.security import redact_sensitive, redact_text
from ai_multi_agent_platform.workspaces import (
    MaterializationOutcome,
    RunWorkspaceBindingRepository,
    WorkspaceChangeKind,
    WorkspaceProvider,
    validate_relative_path,
)

from .contracts import ApplicationReleaseRepository
from .models import ApplicationRelease, BuildTargetState

APPLICATION_BUILD_ACTION = "application.build.command"
_MAX_CAPTURED_OUTPUT = 256 * 1024
_ENV_ALLOWLIST = (
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "VIRTUAL_ENV",
)
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_POLL_SECONDS = 0.05
_DEFAULT_SECRET_LIFETIME_SECONDS = 300


class ApplicationCommandExecutor(Executor):
    """Run one explicit argv build command in an isolated materialized Workspace."""

    def __init__(self, workspace_root: str | Path) -> None:
        self._root = Path(workspace_root).resolve()

    @property
    def descriptor(self) -> ExecutorDescriptor:
        return ExecutorDescriptor(
            executor_id="application-command",
            capabilities=(APPLICATION_BUILD_ACTION,),
            metadata={
                "shell": False,
                "workspace_root": str(self._root),
                "environment_allowlist": list(_ENV_ALLOWLIST),
            },
        )

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        started_at = datetime.now(UTC).isoformat()
        started = monotonic()
        if request.action != APPLICATION_BUILD_ACTION:
            return _failure(
                request,
                started_at,
                started,
                ExecutionErrorCategory.UNSUPPORTED_CAPABILITY,
                f"unsupported action: {request.action}",
            )
        if request.cancellation is not None and request.cancellation.cancelled:
            return _cancelled(request, started_at, started)
        sensitive_values = _sensitive_environment_values(request)
        try:
            workspace = self._workspace(request.workspace)
            command = _command(request.arguments.get("command"))
            source_path = _optional_relative_path(request.arguments.get("source_path"))
            output_path = _required_relative_path(
                request.arguments.get("output_path"),
                "output_path",
            )
            environment = _execution_environment(request.environment)
            cwd = workspace if source_path is None else _contained(workspace, source_path)
            if not cwd.is_dir():
                raise ValueError("build source_path does not exist as a directory")
            destination = _contained(workspace, output_path)
            result = await self._run_process(
                command,
                cwd=cwd,
                environment=environment,
                timeout_seconds=request.timeout_seconds,
                cancellation=request.cancellation,
            )
        except TimeoutError:
            return _failure(
                request,
                started_at,
                started,
                ExecutionErrorCategory.TIMEOUT,
                "application build timed out",
                status=ExecutionStatus.TIMED_OUT,
            )
        except asyncio.CancelledError:
            return _cancelled(request, started_at, started)
        except (OSError, ValueError) as exc:
            message = redact_text(str(exc), sensitive_values)
            return _failure(
                request,
                started_at,
                started,
                ExecutionErrorCategory.INVALID_REQUEST,
                message,
            )

        stdout, stderr, returncode = result
        stdout = redact_text(stdout, sensitive_values)
        stderr = redact_text(stderr, sensitive_values)
        if request.cancellation is not None and request.cancellation.cancelled:
            return _cancelled(request, started_at, started)
        if returncode != 0:
            return _failure(
                request,
                started_at,
                started,
                ExecutionErrorCategory.EXECUTION_FAILED,
                stderr or f"application build exited with code {returncode}",
                code=returncode,
                stdout=stdout,
            )
        if destination.is_symlink() or not destination.is_file():
            return _failure(
                request,
                started_at,
                started,
                ExecutionErrorCategory.EXECUTION_FAILED,
                f"application build did not produce expected output: {output_path}",
                code=returncode,
                stdout=stdout,
                stderr=stderr,
            )
        size_bytes = destination.stat().st_size
        return ExecutionResult(
            task_id=request.task_id,
            run_id=request.run_id,
            correlation_id=request.correlation_id,
            step_id=request.step_id,
            status=ExecutionStatus.SUCCEEDED,
            result_code=returncode,
            output={"output_path": output_path, "size_bytes": size_bytes},
            stdout=stdout,
            stderr=stderr,
            artifacts=(
                ExecutionArtifact(
                    relative_path=output_path,
                    media_type="application/octet-stream",
                    size_bytes=size_bytes,
                ),
            ),
            started_at=started_at,
            finished_at=datetime.now(UTC).isoformat(),
            duration_seconds=monotonic() - started,
        )

    def _workspace(self, token: str) -> Path:
        if not token.strip() or token in {".", ".."} or "/" in token or "\\" in token:
            raise ValueError("execution workspace must be an opaque local token")
        path = (self._root / token).resolve()
        if self._root not in path.parents:
            raise ValueError("execution workspace escapes configured root")
        if not path.is_dir():
            raise ValueError("execution workspace is missing or unavailable")
        return path

    @staticmethod
    async def _run_process(
        command: tuple[str, ...],
        *,
        cwd: Path,
        environment: dict[str, str],
        timeout_seconds: float | None,
        cancellation: CancellationToken | None,
    ) -> tuple[str, str, int]:
        process_environment = {
            name: value for name in _ENV_ALLOWLIST if (value := os.environ.get(name))
        }
        process_environment.update(environment)
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            env=process_environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        communication = asyncio.create_task(process.communicate())
        deadline = None if timeout_seconds is None else monotonic() + timeout_seconds
        try:
            while True:
                if cancellation is not None and cancellation.cancelled:
                    raise asyncio.CancelledError
                if communication.done():
                    stdout_bytes, stderr_bytes = communication.result()
                    break
                interval = _POLL_SECONDS
                if deadline is not None:
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise TimeoutError
                    interval = min(interval, remaining)
                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        asyncio.shield(communication),
                        timeout=interval,
                    )
                    break
                except TimeoutError:
                    continue
        except (TimeoutError, asyncio.CancelledError):
            if process.returncode is None:
                process.kill()
                await process.wait()
            communication.cancel()
            try:
                await communication
            except asyncio.CancelledError:
                pass
            raise
        return (
            _decode_output(stdout_bytes),
            _decode_output(stderr_bytes),
            process.returncode or 0,
        )


class ApplicationBuildLifecycleBackend(LifecycleBackend):
    """Resolve a canonical build Run to its immutable release specification and Workspace."""

    def __init__(
        self,
        releases: ApplicationReleaseRepository,
        workspaces: WorkspaceProvider,
        files: FileProvider,
        bindings: RunWorkspaceBindingRepository,
        executor: Executor,
        *,
        secret_provider: SecretProvider | None = None,
        secret_consumer_ref: str = "service:application-build-secrets",
        secret_purpose: str = "application_build",
    ) -> None:
        if not secret_consumer_ref.strip():
            raise ValueError("application build secret consumer must not be blank")
        if not secret_purpose.strip():
            raise ValueError("application build secret purpose must not be blank")
        self._releases = releases
        self._workspaces = workspaces
        self._files = files
        self._bindings = bindings
        self._executor = executor
        self._secret_provider = secret_provider
        self._secret_consumer_ref = secret_consumer_ref
        self._secret_purpose = secret_purpose
        self._results: dict[str, ExecutionResult] = {}
        self._cancellations: dict[str, CancellationToken] = {}
        self._finished: dict[str, asyncio.Event] = {}
        self._secret_deadlines: dict[str, datetime] = {}

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="application-build-lifecycle",
            provider_type="execution",
            supported_operations=("start", "get", "cancel"),
            health=HealthStatus.HEALTHY,
            available=True,
        )

    async def start(self, request: KernelExecutionRequest) -> ExecutionHandle:
        existing = self._results.get(request.run_id)
        if existing is None and request.run_id not in self._cancellations:
            release = await self._release_for_run(request.run_id)
            target = _target_for_run(release, request.run_id)
            self._validate_request(request, release, target)
            binding = await self._bindings.get(request.run_id)
            if binding is None:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "application build Run has no immutable Workspace binding",
                )
            if (
                binding.task_id != request.subject_id
                or binding.workspace_id != release.workspace_id
                or binding.workspace_snapshot_id != release.workspace_snapshot_id
                or binding.content_checksum != release.workspace_content_checksum
            ):
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "application build Run binding differs from release provenance",
                )
            timeout_seconds = _timeout_seconds(release, request.context)
            context = _data_context(request.context, binding.task_id, request.run_id)
            materialization = await self._workspaces.materialize(
                release.workspace_id,
                context,
                snapshot_id=release.workspace_snapshot_id,
                task_id=binding.task_id,
                run_id=request.run_id,
            )
            cancellation = CancellationToken()
            finished = asyncio.Event()
            self._cancellations[request.run_id] = cancellation
            self._finished[request.run_id] = finished
            outcome = MaterializationOutcome.FAILED
            started_at = datetime.now(UTC).isoformat()
            started = monotonic()
            try:
                try:
                    environment, secret_environment_keys = await self._build_environment(
                        release,
                        task_id=binding.task_id,
                        run_id=request.run_id,
                        timeout_seconds=timeout_seconds,
                    )
                    timeout_seconds = _lease_bound_timeout_seconds(
                        timeout_seconds,
                        self._secret_deadlines.pop(request.run_id, None),
                    )
                except ContractError as exc:
                    result = _contract_failure(
                        task_id=binding.task_id,
                        run_id=request.run_id,
                        correlation_id=request.context.correlation_id,
                        started_at=started_at,
                        started=started,
                        error=exc,
                    )
                else:
                    result = await self._executor.execute(
                        ExecutionRequest(
                            task_id=binding.task_id,
                            run_id=request.run_id,
                            correlation_id=request.context.correlation_id,
                            action=APPLICATION_BUILD_ACTION,
                            workspace=materialization.execution_workspace,
                            arguments={
                                "command": list(release.build_specification.command),
                                "source_path": release.build_specification.source_path,
                                "output_path": target.target.output_path,
                            },
                            environment=environment,
                            timeout_seconds=timeout_seconds,
                            cancellation=cancellation,
                            policy_context={
                                "sensitive_environment_keys": list(secret_environment_keys),
                            },
                        )
                    )
                    if result.status is ExecutionStatus.SUCCEEDED:
                        result = await self._capture_output(
                            release,
                            target,
                            materialization.id,
                            context,
                            result,
                        )
                        outcome = MaterializationOutcome.SUCCEEDED
                    elif result.status is ExecutionStatus.CANCELLED:
                        outcome = MaterializationOutcome.CANCELLED
                self._results[request.run_id] = result
            finally:
                self._secret_deadlines.pop(request.run_id, None)
                self._cancellations.pop(request.run_id, None)
                await self._workspaces.release_materialization(materialization.id, outcome)
                finished.set()
                self._finished.pop(request.run_id, None)
        return ExecutionHandle(
            run_id=request.run_id,
            backend_ref=f"application-build:{request.run_id}",
            adapter_metadata=(),
        )

    async def get(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        result = self._results.get(run_id)
        if result is not None:
            return _snapshot(result)
        if run_id in self._cancellations:
            return ExecutionSnapshot(
                run_id=run_id,
                status=ExecutionStatus.RUNNING,
                output={"state": "active-local-build"},
            )
        result = await self._lost_process_result(run_id, context)
        self._results[run_id] = result
        return _snapshot(result)

    async def cancel(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        result = self._results.get(run_id)
        if result is not None:
            return _snapshot(result)
        cancellation = self._cancellations.get(run_id)
        if cancellation is not None:
            finished = self._finished.get(run_id)
            cancellation.cancel()
            if finished is not None:
                await finished.wait()
            result = self._results.get(run_id)
            if result is not None:
                return _snapshot(result)
        result = await self._lost_process_result(run_id, context)
        self._results[run_id] = result
        return _snapshot(result)

    async def _lost_process_result(
        self,
        run_id: str,
        context: OperationContext,
    ) -> ExecutionResult:
        release = await self._release_for_run(run_id)
        target = _target_for_run(release, run_id)
        if target.task_id is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "application build target has no canonical Task identity",
            )
        now = monotonic()
        return _failure(
            ExecutionRequest(
                task_id=target.task_id,
                run_id=run_id,
                correlation_id=context.correlation_id,
                action=APPLICATION_BUILD_ACTION,
                workspace="unavailable",
            ),
            datetime.now(UTC).isoformat(),
            now,
            ExecutionErrorCategory.INTERNAL,
            "application build process state is unavailable; refusing implicit re-execution",
        )

    async def _build_environment(
        self,
        release: ApplicationRelease,
        *,
        task_id: str,
        run_id: str,
        timeout_seconds: float | None,
    ) -> tuple[dict[str, str], tuple[str, ...]]:
        specification = release.build_specification
        environment = dict(specification.environment)
        if not specification.secret_environment:
            return environment, ()
        if self._secret_provider is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "application build requires a SecretProvider for secret_environment",
            )
        secret_keys: list[str] = []
        earliest_expiry: datetime | None = None
        lifetime = _secret_lifetime_seconds(timeout_seconds)
        for name, reference in specification.secret_environment.items():
            if reference.scope != release.project_id:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "application build secret reference is outside the release project scope",
                )
            material = await self._secret_provider.resolve(
                reference,
                SecretAccessContext(
                    consumer_ref=self._secret_consumer_ref,
                    project_id=release.project_id,
                    workspace_id=release.workspace_id,
                    task_id=task_id,
                    run_id=run_id,
                    action=APPLICATION_BUILD_ACTION,
                    capability_ref=APPLICATION_BUILD_ACTION,
                    purpose=self._secret_purpose,
                    requested_lifetime_seconds=lifetime,
                ),
            )
            if material.expires_at <= datetime.now(UTC):
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "application build secret lease expired before execution",
                )
            if earliest_expiry is None or material.expires_at < earliest_expiry:
                earliest_expiry = material.expires_at
            environment[name] = material.reveal()
            secret_keys.append(name)
        if earliest_expiry is not None:
            self._secret_deadlines[run_id] = earliest_expiry
        return environment, tuple(secret_keys)

    async def _release_for_run(self, run_id: str) -> ApplicationRelease:
        release = await self._releases.find_run(run_id)
        if release is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"application release for build Run not found: {run_id}",
            )
        return release

    async def _capture_output(
        self,
        release: ApplicationRelease,
        target: BuildTargetState,
        materialization_id: str,
        context: DataAccessContext,
        result: ExecutionResult,
    ) -> ExecutionResult:
        changes = await self._workspaces.capture_changes(materialization_id, context)
        matches = [
            change
            for change in changes.changes
            if change.relative_path == target.target.output_path
            and change.kind in {WorkspaceChangeKind.CREATED, WorkspaceChangeKind.MODIFIED}
        ]
        if len(matches) != 1:
            return _failure(
                result_request(result),
                result.started_at or datetime.now(UTC).isoformat(),
                monotonic(),
                ExecutionErrorCategory.EXECUTION_FAILED,
                "application build output was not captured as one canonical changed File",
            )
        change = matches[0]
        if change.file_id is None or change.sha256 is None:
            return _failure(
                result_request(result),
                result.started_at or datetime.now(UTC).isoformat(),
                monotonic(),
                ExecutionErrorCategory.EXECUTION_FAILED,
                "application build output was not captured as one canonical changed File",
            )
        artifact_id = _artifact_id(result.run_id, target.target.target_id, change.sha256)
        await self._files.link_artifact(change.file_id, artifact_id, context)
        output = dict(result.output)
        output["application_build"] = {
            "release_id": release.release_id,
            "target_id": target.target.target_id,
            "artifact_id": artifact_id,
            "file_id": change.file_id,
            "filename": PurePosixPath(target.target.output_path).name,
            "media_type": "application/octet-stream",
            "sha256": change.sha256,
        }
        return replace(result, output=output)

    @staticmethod
    def _validate_request(
        request: KernelExecutionRequest,
        release: ApplicationRelease,
        target: BuildTargetState,
    ) -> None:
        if request.subject_type != "task" or request.subject_id != target.task_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "application build lifecycle requires the target's canonical Task Run",
            )
        if request.context.project_id != release.project_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "application build execution context is outside the release project",
            )


def _snapshot(result: ExecutionResult) -> ExecutionSnapshot:
    return ExecutionSnapshot(
        run_id=result.run_id,
        status=result.status,
        output={
            "result_code": result.result_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "output": result.output,
            "artifacts": [artifact.relative_path for artifact in result.artifacts],
        },
    )


def result_request(result: ExecutionResult) -> ExecutionRequest:
    """Create the minimum request identity needed to normalize a post-execution failure."""

    return ExecutionRequest(
        task_id=result.task_id,
        run_id=result.run_id,
        correlation_id=result.correlation_id,
        action=APPLICATION_BUILD_ACTION,
        workspace="captured",
    )


def _target_for_run(release: ApplicationRelease, run_id: str) -> BuildTargetState:
    matches = [target for target in release.targets if target.run_id == run_id]
    if len(matches) != 1:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "application build Run must belong to exactly one release target",
        )
    return matches[0]


def _artifact_id(run_id: str, target_id: str, sha256: str) -> str:
    return f"artifact_{uuid5(NAMESPACE_URL, f'application-build:{run_id}:{target_id}:{sha256}')}"


def _data_context(operation: OperationContext, task_id: str, run_id: str) -> DataAccessContext:
    actor_ref = (
        f"{operation.owner_type}:{operation.owner_id}"
        if operation.owner_type is not None and operation.owner_id is not None
        else "service:application-distribution"
    )
    return DataAccessContext(
        operation=operation,
        actor_ref=actor_ref,
        task_id=task_id,
        run_id=run_id,
        audit_metadata={"source": "application-build-lifecycle"},
    )


def _timeout_seconds(release: ApplicationRelease, context: OperationContext) -> float | None:
    configured = release.build_specification.resource_hints.get("timeout_seconds")
    if configured is None:
        return context.control.timeout_seconds
    if isinstance(configured, bool) or not isinstance(configured, int | float) or configured <= 0:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "build resource_hints.timeout_seconds must be a positive number",
        )
    return float(configured)


def _secret_lifetime_seconds(timeout_seconds: float | None) -> int:
    if timeout_seconds is None:
        return _DEFAULT_SECRET_LIFETIME_SECONDS
    return max(1, ceil(timeout_seconds))


def _lease_bound_timeout_seconds(
    timeout_seconds: float | None,
    secret_expires_at: datetime | None,
) -> float | None:
    if secret_expires_at is None:
        return timeout_seconds
    remaining = (secret_expires_at - datetime.now(UTC)).total_seconds()
    if remaining <= 0:
        raise ContractError(
            ErrorCode.FORBIDDEN,
            "application build secret lease expired before execution",
        )
    if timeout_seconds is None:
        return remaining
    return min(timeout_seconds, remaining)


def _command(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        raise ValueError("application build command must be a non-empty argv array")
    command = tuple(value)
    if any(not item.strip() for item in command):
        raise ValueError("application build command must not contain blank argv items")
    return command


def _execution_environment(values: dict[str, str]) -> dict[str, str]:
    environment: dict[str, str] = {}
    for name, value in values.items():
        if _ENVIRONMENT_NAME.fullmatch(name) is None:
            raise ValueError("application build environment contains an invalid variable name")
        if not isinstance(value, str):
            raise ValueError("application build environment values must be strings")
        environment[name] = value
    return environment


def _sensitive_environment_values(request: ExecutionRequest) -> tuple[str, ...]:
    raw_keys = request.policy_context.get("sensitive_environment_keys")
    configured: set[str] = set()
    if isinstance(raw_keys, list):
        for item in raw_keys:
            if isinstance(item, str):
                configured.add(item)
    for name, value in request.environment.items():
        if redact_sensitive({name: value}) != {name: value}:
            configured.add(name)
    return tuple(
        request.environment[name]
        for name in sorted(configured)
        if name in request.environment and request.environment[name]
    )


def _optional_relative_path(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("source_path must be a relative path or null")
    validate_relative_path(value)
    return value


def _required_relative_path(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a relative path")
    validate_relative_path(value)
    return value


def _contained(root: Path, relative_path: str) -> Path:
    target = (root / relative_path).resolve(strict=False)
    if target == root or root not in target.parents:
        raise ValueError("build path escapes the materialized Workspace")
    return target


def _decode_output(value: bytes) -> str:
    if len(value) > _MAX_CAPTURED_OUTPUT:
        value = value[-_MAX_CAPTURED_OUTPUT:]
    return value.decode("utf-8", errors="replace")


def _contract_failure(
    *,
    task_id: str,
    run_id: str,
    correlation_id: str,
    started_at: str,
    started: float,
    error: ContractError,
) -> ExecutionResult:
    message = redact_text(error.message)
    return ExecutionResult(
        task_id=task_id,
        run_id=run_id,
        correlation_id=correlation_id,
        status=ExecutionStatus.FAILED,
        output={
            "contract_error": {
                "code": error.code.value,
                "retryable": error.retryable,
            }
        },
        stderr=message,
        error=ExecutionError(
            category=ExecutionErrorCategory.EXECUTION_FAILED,
            message=message,
            retryable=error.retryable,
            details={"contract_error_code": error.code.value},
        ),
        started_at=started_at,
        finished_at=datetime.now(UTC).isoformat(),
        duration_seconds=max(0.0, monotonic() - started),
    )


def _cancelled(
    request: ExecutionRequest,
    started_at: str,
    started: float,
) -> ExecutionResult:
    return _failure(
        request,
        started_at,
        started,
        ExecutionErrorCategory.CANCELLED,
        "application build cancelled",
        status=ExecutionStatus.CANCELLED,
    )


def _failure(
    request: ExecutionRequest,
    started_at: str,
    started: float,
    category: ExecutionErrorCategory,
    message: str,
    *,
    status: ExecutionStatus = ExecutionStatus.FAILED,
    code: int | None = None,
    stdout: str = "",
    stderr: str | None = None,
) -> ExecutionResult:
    return ExecutionResult(
        task_id=request.task_id,
        run_id=request.run_id,
        correlation_id=request.correlation_id,
        step_id=request.step_id,
        status=status,
        result_code=code,
        stdout=stdout,
        stderr=stderr if stderr is not None else message,
        error=ExecutionError(category=category, message=message),
        started_at=started_at,
        finished_at=datetime.now(UTC).isoformat(),
        duration_seconds=max(0.0, monotonic() - started),
    )


__all__ = [
    "APPLICATION_BUILD_ACTION",
    "ApplicationBuildLifecycleBackend",
    "ApplicationCommandExecutor",
]
