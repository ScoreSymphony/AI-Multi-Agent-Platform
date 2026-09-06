"""Bridge the platform-owned Executor abstraction into the existing kernel lifecycle seam."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    ContractError,
    ErrorCode,
    ExecutionSnapshot,
    LifecycleBackend,
    OperationContext,
    ProviderDescriptor,
)
from ai_multi_agent_platform.contracts import (
    ExecutionHandle as KernelExecutionHandle,
)
from ai_multi_agent_platform.contracts import (
    ExecutionRequest as KernelExecutionRequest,
)
from ai_multi_agent_platform.contracts import (
    ExecutionStatus as KernelExecutionStatus,
)

from .contracts import ExecutionRequest, ExecutionResult, ExecutionStatus, Executor

WorkspaceResolver = Callable[[KernelExecutionRequest], Awaitable[str]]
TerminalResultObserver = Callable[[KernelExecutionRequest, ExecutionResult], Awaitable[None]]


class ExecutorLifecycleBackend(LifecycleBackend):
    """Lifecycle adapter proving the kernel can execute through a generic Executor.

    The optional workspace resolver keeps canonical Run/Workspace selection outside the
    Executor contract while allowing one lifecycle instance to route each Run to an opaque
    execution-workspace token. The optional terminal observer runs before a terminal snapshot
    becomes visible to the kernel and is retried when it fails, which lets platform-owned
    integrations persist artifacts/provenance without making the Executor lifecycle authority.
    """

    def __init__(
        self,
        executor: Executor,
        *,
        workspace: str,
        action: str = "echo",
        workspace_resolver: WorkspaceResolver | None = None,
        terminal_result_observer: TerminalResultObserver | None = None,
    ) -> None:
        self._executor = executor
        self._workspace = workspace
        self._action = action
        self._workspace_resolver = workspace_resolver
        self._terminal_result_observer = terminal_result_observer
        self._results: dict[str, ExecutionResult] = {}
        self._requests: dict[str, KernelExecutionRequest] = {}
        self._observed_terminal_runs: set[str] = set()

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id=f"executor:{self._executor.descriptor.executor_id}",
            provider_type="execution",
            supported_operations=("start", "get", "cancel"),
        )

    async def start(
        self,
        request: KernelExecutionRequest,
    ) -> KernelExecutionHandle:
        existing = self._results.get(request.run_id)
        if existing is None:
            workspace = self._workspace
            if self._workspace_resolver is not None:
                workspace = await self._workspace_resolver(request)
                if not workspace.strip():
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "execution workspace resolver returned a blank token",
                    )
            result = await self._executor.execute(
                ExecutionRequest(
                    task_id=(
                        request.subject_id
                        if request.subject_type == "task"
                        else request.context.correlation_id
                    ),
                    run_id=request.run_id,
                    step_id=request.subject_id if request.subject_type == "step" else None,
                    correlation_id=request.context.correlation_id,
                    action=self._action,
                    workspace=workspace,
                    arguments={"text": str(request.input.get("plan_ref", ""))},
                    timeout_seconds=request.context.control.timeout_seconds,
                )
            )
            self._results[request.run_id] = result
            self._requests[request.run_id] = request
        else:
            result = existing
            self._requests.setdefault(request.run_id, request)
        return KernelExecutionHandle(
            run_id=request.run_id,
            backend_ref=f"{self._executor.descriptor.executor_id}:{request.run_id}",
            adapter_metadata=self._adapter_metadata(result),
        )

    async def get(
        self,
        run_id: str,
        context: OperationContext,
    ) -> ExecutionSnapshot:
        del context
        result = self._results.get(run_id)
        if result is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"execution not found: {run_id}",
            )
        await self._observe_terminal_result(run_id, result)
        return ExecutionSnapshot(
            run_id=run_id,
            status=self._map_status(result.status),
            output={
                "result_code": result.result_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "output": result.output,
                "artifacts": [artifact.relative_path for artifact in result.artifacts],
            },
            adapter_metadata=self._adapter_metadata(result),
        )

    async def cancel(
        self,
        run_id: str,
        context: OperationContext,
    ) -> ExecutionSnapshot:
        del context
        result = self._results.get(run_id)
        if result is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"execution not found: {run_id}",
            )
        if result.status not in {
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMED_OUT,
        }:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"execution cannot be cancelled: {run_id}",
            )
        return await self.get(
            run_id,
            OperationContext(correlation_id=run_id),
        )

    async def _observe_terminal_result(
        self,
        run_id: str,
        result: ExecutionResult,
    ) -> None:
        observer = self._terminal_result_observer
        if observer is None or run_id in self._observed_terminal_runs:
            return
        if result.status not in {
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.TIMED_OUT,
        }:
            return
        request = self._requests.get(run_id)
        if request is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "terminal execution result has no originating lifecycle request",
                details={"run_id": run_id},
            )
        await observer(request, result)
        self._observed_terminal_runs.add(run_id)

    @staticmethod
    def ensure_workspace(root: str | Path, workspace: str) -> Path:
        path = Path(root) / workspace
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _adapter_metadata(result: ExecutionResult) -> tuple[AdapterMetadata, ...]:
        return tuple(
            AdapterMetadata(namespace=namespace, values=dict(values))
            for namespace, values in sorted(result.adapter_metadata.items())
        )

    @staticmethod
    def _map_status(status: ExecutionStatus) -> KernelExecutionStatus:
        return {
            ExecutionStatus.SUCCEEDED: KernelExecutionStatus.SUCCEEDED,
            ExecutionStatus.FAILED: KernelExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED: KernelExecutionStatus.CANCELLED,
            ExecutionStatus.TIMED_OUT: KernelExecutionStatus.TIMED_OUT,
        }[status]
