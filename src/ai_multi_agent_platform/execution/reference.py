"""Deterministic, safety-first executor for tests and local development."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from .contracts import (
    ExecutionArtifact,
    ExecutionError,
    ExecutionErrorCategory,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    Executor,
    ExecutorDescriptor,
    JsonValue,
)


@dataclass(frozen=True, slots=True)
class _ActionResult:
    output: dict[str, JsonValue]
    stdout: str = ""
    stderr: str = ""
    artifacts: tuple[ExecutionArtifact, ...] = ()


class _ControlledFailure(Exception):
    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


class ReferenceExecutor(Executor):
    """Executes only a small allow-listed set of deterministic platform actions."""

    _CAPABILITIES = ("echo", "write_artifact", "fail", "sleep")

    def __init__(self, workspace_root: str | Path) -> None:
        self._root = Path(workspace_root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def descriptor(self) -> ExecutorDescriptor:
        return ExecutorDescriptor(
            executor_id="reference",
            capabilities=self._CAPABILITIES,
            metadata={"workspace_root": str(self._root), "arbitrary_commands": False},
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

        if request.action not in self._CAPABILITIES:
            return self._failure(
                request,
                started_at,
                started,
                ExecutionErrorCategory.UNSUPPORTED_CAPABILITY,
                f"unsupported action: {request.action}",
            )
        if request.cancellation is not None and request.cancellation.cancelled:
            return self._cancelled(request, started_at, started)

        try:
            operation = self._run_action(request, workspace)
            action_result = (
                await operation
                if request.timeout_seconds is None
                else await asyncio.wait_for(
                    operation,
                    timeout=request.timeout_seconds,
                )
            )
        except TimeoutError:
            return self._failure(
                request,
                started_at,
                started,
                ExecutionErrorCategory.TIMEOUT,
                "execution timed out",
                status=ExecutionStatus.TIMED_OUT,
            )
        except asyncio.CancelledError:
            return self._cancelled(request, started_at, started)
        except _ControlledFailure as exc:
            return self._failure(
                request,
                started_at,
                started,
                ExecutionErrorCategory.EXECUTION_FAILED,
                str(exc),
                code=exc.code,
            )
        except ValueError as exc:
            return self._failure(
                request,
                started_at,
                started,
                ExecutionErrorCategory.INVALID_REQUEST,
                str(exc),
            )
        except Exception as exc:
            return self._failure(
                request,
                started_at,
                started,
                ExecutionErrorCategory.INTERNAL,
                str(exc),
            )

        if request.cancellation is not None and request.cancellation.cancelled:
            return self._cancelled(request, started_at, started)
        return ExecutionResult(
            task_id=request.task_id,
            run_id=request.run_id,
            correlation_id=request.correlation_id,
            step_id=request.step_id,
            status=ExecutionStatus.SUCCEEDED,
            result_code=0,
            output=action_result.output,
            stdout=action_result.stdout,
            stderr=action_result.stderr,
            artifacts=action_result.artifacts,
            started_at=started_at,
            finished_at=datetime.now(UTC).isoformat(),
            duration_seconds=monotonic() - started,
        )

    def _workspace(self, workspace: str) -> Path:
        candidate = (self._root / workspace).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise ValueError("workspace escapes configured workspace root")
        if not candidate.exists() or not candidate.is_dir():
            raise ValueError("workspace is missing or unavailable")
        return candidate

    async def _run_action(
        self,
        request: ExecutionRequest,
        workspace: Path,
    ) -> _ActionResult:
        if request.action == "echo":
            text = str(request.arguments.get("text", ""))
            return _ActionResult(output={"text": text}, stdout=text)
        if request.action == "write_artifact":
            relative = str(request.arguments.get("path", "artifact.txt"))
            content = str(request.arguments.get("content", ""))
            destination = (workspace / relative).resolve()
            if destination != workspace and workspace not in destination.parents:
                raise ValueError("artifact path escapes execution workspace")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
            artifact = ExecutionArtifact(
                relative_path=relative,
                media_type="text/plain",
                size_bytes=len(content.encode("utf-8")),
            )
            return _ActionResult(
                output={"artifact": relative},
                stdout=relative,
                artifacts=(artifact,),
            )
        if request.action == "fail":
            message = str(request.arguments.get("message", "controlled failure"))
            code = self._integer_argument(request, "code", default=1)
            raise _ControlledFailure(message, code)
        if request.action == "sleep":
            seconds = self._number_argument(request, "seconds", default=0.0)
            if seconds < 0:
                raise ValueError("sleep seconds must not be negative")
            await self._sleep_with_cancellation(seconds, request)
            return _ActionResult(output={"slept_seconds": seconds})
        raise AssertionError("capability validation bug")

    @staticmethod
    def _integer_argument(
        request: ExecutionRequest,
        name: str,
        *,
        default: int,
    ) -> int:
        value = request.arguments.get(name, default)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{name} must be an integer")
        return value

    @staticmethod
    def _number_argument(
        request: ExecutionRequest,
        name: str,
        *,
        default: float,
    ) -> float:
        value = request.arguments.get(name, default)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{name} must be a number")
        return float(value)

    async def _sleep_with_cancellation(
        self,
        seconds: float,
        request: ExecutionRequest,
    ) -> None:
        sleep_task = asyncio.create_task(asyncio.sleep(seconds))
        if request.cancellation is None:
            await sleep_task
            return
        cancel_task = asyncio.create_task(request.cancellation.wait())
        done, pending = await asyncio.wait(
            {sleep_task, cancel_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if cancel_task in done:
            raise asyncio.CancelledError

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
    ) -> ExecutionResult:
        return ExecutionResult(
            task_id=request.task_id,
            run_id=request.run_id,
            correlation_id=request.correlation_id,
            step_id=request.step_id,
            status=status,
            result_code=code,
            stderr=message,
            error=ExecutionError(category=category, message=message),
            started_at=started_at,
            finished_at=datetime.now(UTC).isoformat(),
            duration_seconds=monotonic() - started,
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
