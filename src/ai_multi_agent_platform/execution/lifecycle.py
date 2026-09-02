"""Bridge the platform-owned Executor abstraction into the existing kernel lifecycle seam."""

from __future__ import annotations

from pathlib import Path

from ai_multi_agent_platform.contracts import (
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


class ExecutorLifecycleBackend(LifecycleBackend):
    """Lifecycle adapter proving the kernel can execute through a generic Executor."""

    def __init__(
        self,
        executor: Executor,
        *,
        workspace: str,
        action: str = "echo",
    ) -> None:
        self._executor = executor
        self._workspace = workspace
        self._action = action
        self._results: dict[str, ExecutionResult] = {}

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
            result = await self._executor.execute(
                ExecutionRequest(
                    task_id=request.subject_id,
                    run_id=request.run_id,
                    correlation_id=request.context.correlation_id,
                    action=self._action,
                    workspace=self._workspace,
                    arguments={"text": str(request.input.get("plan_ref", ""))},
                    timeout_seconds=request.context.control.timeout_seconds,
                )
            )
            self._results[request.run_id] = result
        return KernelExecutionHandle(
            run_id=request.run_id,
            backend_ref=f"{self._executor.descriptor.executor_id}:{request.run_id}",
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

    @staticmethod
    def ensure_workspace(root: str | Path, workspace: str) -> Path:
        path = Path(root) / workspace
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _map_status(status: ExecutionStatus) -> KernelExecutionStatus:
        return {
            ExecutionStatus.SUCCEEDED: KernelExecutionStatus.SUCCEEDED,
            ExecutionStatus.FAILED: KernelExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED: KernelExecutionStatus.CANCELLED,
            ExecutionStatus.TIMED_OUT: KernelExecutionStatus.TIMED_OUT,
        }[status]
