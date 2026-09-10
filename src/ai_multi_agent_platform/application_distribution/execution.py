"""Build-command execution and lifecycle binding for application releases."""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from time import monotonic
from uuid import NAMESPACE_URL, uuid5

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
    ExecutionArtifact,
    ExecutionError,
    ExecutionErrorCategory,
    ExecutionRequest,
    ExecutionResult,
    Executor,
    ExecutorDescriptor,
)
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
        try:
            workspace = self._workspace(request.workspace)
            command = _command(request.arguments.get("command"))
            source_path = _optional_relative_path(request.arguments.get("source_path"))
            output_path = _required_relative_path(
                request.arguments.get("output_path"),
                "output_path",
            )
            cwd = workspace if source_path is None else _contained(workspace, source_path)
            if not cwd.is_dir():
                raise ValueError("build source_path does not exist as a directory")
            destination = _contained(workspace, output_path)
            result = await self._run_process(
                command,
                cwd=cwd,
                timeout_seconds=request.timeout_seconds,
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
        except (OSError, ValueError) as exc:
            return _failure(
                request,
                started_at,
                started,
                ExecutionErrorCategory.INVALID_REQUEST,
                str(exc),
            )

        stdout, stderr, returncode = result
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
        timeout_seconds: float | None,
    ) -> tuple[str, str, int]:
        environment = {name: value for name in _ENV_ALLOWLIST if (value := os.environ.get(name))}
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            if timeout_seconds is None:
                stdout_bytes, stderr_bytes = await process.communicate()
            else:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout_seconds,
                )
        except TimeoutError:
            process.kill()
            await process.wait()
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
    ) -> None:
        self._releases = releases
        self._workspaces = workspaces
        self._files = files
        self._bindings = bindings
        self._executor = executor
        self._results: dict[str, ExecutionResult] = {}

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
        if existing is None:
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
            context = _data_context(request.context, binding.task_id, request.run_id)
            materialization = await self._workspaces.materialize(
                release.workspace_id,
                context,
                snapshot_id=release.workspace_snapshot_id,
                task_id=binding.task_id,
                run_id=request.run_id,
            )
            outcome = MaterializationOutcome.FAILED
            try:
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
                        timeout_seconds=_timeout_seconds(release, request.context),
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
                await self._workspaces.release_materialization(materialization.id, outcome)
        return ExecutionHandle(
            run_id=request.run_id,
            backend_ref=f"application-build:{request.run_id}",
            adapter_metadata=(),
        )

    async def get(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        result = self._results.get(run_id)
        if result is None:
            release = await self._release_for_run(run_id)
            target = _target_for_run(release, run_id)
            if target.task_id is None:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "application build target has no canonical Task identity",
                )
            await self.start(
                KernelExecutionRequest(
                    run_id=run_id,
                    subject_type="task",
                    subject_id=target.task_id,
                    context=context,
                )
            )
            result = self._results.get(run_id)
        if result is None:
            raise ContractError(ErrorCode.NOT_FOUND, f"application build not found: {run_id}")
        return ExecutionSnapshot(
            run_id=run_id,
            status=result.status,
            output={
                "result_code": result.result_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "output": result.output,
                "artifacts": [artifact.relative_path for artifact in result.artifacts],
            },
        )

    async def cancel(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        return await self.get(run_id, context)

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
        if len(matches) != 1 or matches[0].file_id is None or matches[0].sha256 is None:
            return _failure(
                result_request(result),
                result.started_at or datetime.now(UTC).isoformat(),
                monotonic(),
                ExecutionErrorCategory.EXECUTION_FAILED,
                "application build output was not captured as one canonical changed File",
            )
        change = matches[0]
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


def _command(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        raise ValueError("application build command must be a non-empty argv array")
    command = tuple(value)
    if any(not item.strip() for item in command):
        raise ValueError("application build command must not contain blank argv items")
    return command


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
