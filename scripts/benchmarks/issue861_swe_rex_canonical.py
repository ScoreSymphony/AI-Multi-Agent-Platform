#!/usr/bin/env python3
"""Exercise the canonical Executor boundary against a live SWE-ReX runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import posixpath
import sys
import tempfile
import traceback
from pathlib import Path, PurePosixPath
from typing import Any

from ai_multi_agent_platform.adapters.swe_rex import (
    SWE_REX_EVALUATED_REVISION,
    SWE_REX_EVALUATED_VERSION,
    SwerexArtifact,
    SwerexClientRequest,
    SwerexClientResult,
    SwerexExecutionStatus,
    SwerexExecutor,
    SwerexHealth,
)
from ai_multi_agent_platform.execution import (
    ExecutionErrorCategory,
    ExecutionRequest,
    ExecutionStatus,
)

CAPABILITIES = ("echo", "write_artifact", "fail", "sleep")


class _RuntimeClient:
    """Minimal concrete bridge from the PoC client seam to a live SWE-ReX runtime."""

    def __init__(
        self,
        runtime: Any,
        *,
        backend: str,
        provider_workspace: str,
        python_command: str,
        provider_id: str,
    ) -> None:
        self._runtime = runtime
        self._backend = backend
        self._provider_workspace = provider_workspace
        self._python_command = python_command
        self._provider_id = provider_id
        self.dispatch_count = 0
        self.cancelled_refs: list[str] = []

    async def execute(self, request: SwerexClientRequest) -> SwerexClientResult:
        from swerex.runtime.abstract import Command, ReadFileRequest, WriteFileRequest

        self.dispatch_count += 1
        try:
            if request.action == "echo":
                text = str(request.arguments.get("text", ""))
                response = await self._runtime.execute(
                    Command(
                        command=[
                            self._python_command,
                            "-c",
                            "import sys; sys.stdout.write(sys.argv[1])",
                            text,
                        ],
                        cwd=self._provider_workspace,
                        check=False,
                    )
                )
                return self._command_result(
                    response,
                    output={"text": text},
                )

            if request.action == "fail":
                message = str(request.arguments.get("message", "controlled failure"))
                code_value = request.arguments.get("code", 1)
                code = code_value if isinstance(code_value, int) else 1
                response = await self._runtime.execute(
                    Command(
                        command=[
                            self._python_command,
                            "-c",
                            (
                                "import sys; print(sys.argv[1], file=sys.stderr); "
                                "raise SystemExit(int(sys.argv[2]))"
                            ),
                            message,
                            str(code),
                        ],
                        cwd=self._provider_workspace,
                        check=False,
                    )
                )
                return self._command_result(
                    response,
                    output={},
                    failure_message=message,
                )

            if request.action == "sleep":
                seconds_value = request.arguments.get("seconds", 0.0)
                if not isinstance(seconds_value, (int, float)) or isinstance(
                    seconds_value,
                    bool,
                ):
                    return self._invalid("seconds must be a number")
                seconds = float(seconds_value)
                if seconds < 0:
                    return self._invalid("sleep seconds must not be negative")
                response = await self._runtime.execute(
                    Command(
                        command=[
                            self._python_command,
                            "-c",
                            "import sys,time; time.sleep(float(sys.argv[1]))",
                            str(seconds),
                        ],
                        cwd=self._provider_workspace,
                        timeout=request.timeout_seconds,
                        check=False,
                    )
                )
                return self._command_result(
                    response,
                    output={"slept_seconds": seconds},
                )

            if request.action == "write_artifact":
                relative_raw = str(request.arguments.get("path", "artifact.txt"))
                relative = self._relative_path(relative_raw)
                if relative is None:
                    return self._invalid("artifact path escapes execution workspace")
                content = str(request.arguments.get("content", ""))
                provider_path = posixpath.join(
                    self._provider_workspace,
                    relative.as_posix(),
                )
                await self._runtime.write_file(
                    WriteFileRequest(path=provider_path, content=content)
                )
                read_back = await self._runtime.read_file(
                    ReadFileRequest(path=provider_path, encoding="utf-8")
                )
                host_root = Path(request.workspace_path).resolve()
                host_path = (host_root / Path(*relative.parts)).resolve()
                if host_path != host_root and host_root not in host_path.parents:
                    return self._invalid("artifact collection escapes host workspace")
                host_path.parent.mkdir(parents=True, exist_ok=True)
                host_path.write_text(str(read_back.content), encoding="utf-8")
                return SwerexClientResult(
                    status=SwerexExecutionStatus.SUCCEEDED,
                    deployment_id=self._provider_id,
                    result_code=0,
                    output={"artifact": relative.as_posix()},
                    stdout=relative.as_posix(),
                    artifacts=(
                        SwerexArtifact(
                            relative_path=relative.as_posix(),
                            media_type="text/plain",
                            size_bytes=len(content.encode("utf-8")),
                        ),
                    ),
                    metadata={"runtime_transport": self._backend},
                )

            return SwerexClientResult(
                status=SwerexExecutionStatus.FAILED,
                deployment_id=self._provider_id,
                error_code="unsupported_capability",
                error_message=f"unsupported action: {request.action}",
            )
        except Exception as exc:
            if type(exc).__name__ in {"CommandTimeoutError", "TimeoutError"}:
                return SwerexClientResult(
                    status=SwerexExecutionStatus.TIMED_OUT,
                    deployment_id=self._provider_id,
                    error_code="timeout",
                    error_message=str(exc) or "provider command timed out",
                )
            return SwerexClientResult(
                status=SwerexExecutionStatus.FAILED,
                deployment_id=self._provider_id,
                error_code="execution_failed",
                error_message=f"{type(exc).__name__}: {exc}",
            )

    async def cancel(self, request_ref: str) -> None:
        self.cancelled_refs.append(request_ref)

    async def health(self) -> SwerexHealth:
        response = await self._runtime.is_alive(timeout=2.0)
        return SwerexHealth(
            healthy=bool(response),
            capabilities=CAPABILITIES,
            metadata={
                "runtime_transport": self._backend,
                "provider_version": SWE_REX_EVALUATED_VERSION,
            },
        )

    def _command_result(
        self,
        response: Any,
        *,
        output: dict[str, Any],
        failure_message: str | None = None,
    ) -> SwerexClientResult:
        exit_code = response.exit_code
        if exit_code in (0, None):
            return SwerexClientResult(
                status=SwerexExecutionStatus.SUCCEEDED,
                deployment_id=self._provider_id,
                result_code=exit_code,
                output=output,
                stdout=response.stdout,
                stderr=response.stderr,
                metadata={"runtime_transport": self._backend},
            )
        return SwerexClientResult(
            status=SwerexExecutionStatus.FAILED,
            deployment_id=self._provider_id,
            result_code=exit_code,
            stdout=response.stdout,
            stderr=response.stderr,
            error_code="execution_failed",
            error_message=failure_message or response.stderr or "command failed",
            metadata={"runtime_transport": self._backend},
        )

    def _invalid(self, message: str) -> SwerexClientResult:
        return SwerexClientResult(
            status=SwerexExecutionStatus.FAILED,
            deployment_id=self._provider_id,
            error_code="invalid_request",
            error_message=message,
        )

    @staticmethod
    def _relative_path(value: str) -> PurePosixPath | None:
        candidate = PurePosixPath(value.replace("\\", "/"))
        if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
            return None
        return candidate


async def _deployment(backend: str) -> Any:
    if backend == "local":
        from swerex.deployment.local import LocalDeployment

        deployment = LocalDeployment()
    else:
        from swerex.deployment.docker import DockerDeployment

        image = os.environ.get("ISSUE861_SWEREX_IMAGE")
        if not image:
            raise RuntimeError("ISSUE861_SWEREX_IMAGE is required for canonical Docker evidence")
        docker_args = ["--network=none"] if backend == "docker-network-none" else []
        deployment = DockerDeployment(
            image=image,
            pull="never",
            docker_args=docker_args,
            remove_container=True,
        )
    await deployment.start()
    return deployment


async def _run(backend: str) -> dict[str, Any]:
    import swerex

    evidence: dict[str, Any] = {
        "backend": backend,
        "host_platform": platform.platform(),
        "python": sys.version,
        "swerex_version": swerex.__version__,
        "expected_version": SWE_REX_EVALUATED_VERSION,
        "pinned_revision": SWE_REX_EVALUATED_REVISION,
    }
    deployment = await _deployment(backend)
    try:
        with tempfile.TemporaryDirectory(prefix="issue861-canonical-") as temp_dir:
            workspace_root = Path(temp_dir).resolve() / "workspaces"
            workspace = workspace_root / "run-1"
            workspace.mkdir(parents=True)

            if backend == "local":
                provider_workspace = str(workspace)
                python_command = sys.executable
                provider_id = "local-runtime"
            else:
                from swerex.runtime.abstract import UploadRequest

                provider_workspace = "/tmp/issue861-canonical"
                await deployment.runtime.upload(
                    UploadRequest(
                        source_path=str(workspace),
                        target_path=provider_workspace,
                    )
                )
                python_command = "python"
                provider_id = deployment.container_name or "docker-runtime"

            client = _RuntimeClient(
                deployment.runtime,
                backend=backend,
                provider_workspace=provider_workspace,
                python_command=python_command,
                provider_id=provider_id,
            )
            executor = SwerexExecutor(
                client,
                workspace_root,
                capabilities=CAPABILITIES,
                backend_kind="local" if backend == "local" else "docker",
                allow_unsandboxed_local=backend == "local",
            )

            health = await executor.health()
            echo = await executor.execute(
                ExecutionRequest(
                    task_id="task-live",
                    run_id="run-live",
                    correlation_id="corr-live",
                    action="echo",
                    workspace="run-1",
                    arguments={"text": "canonical-echo"},
                )
            )
            artifact = await executor.execute(
                ExecutionRequest(
                    task_id="task-live",
                    run_id="run-live",
                    correlation_id="corr-live",
                    action="write_artifact",
                    workspace="run-1",
                    arguments={
                        "path": "evidence/artifact.txt",
                        "content": "canonical-artifact",
                    },
                )
            )
            failure = await executor.execute(
                ExecutionRequest(
                    task_id="task-live",
                    run_id="run-live",
                    correlation_id="corr-live",
                    action="fail",
                    workspace="run-1",
                    arguments={"message": "canonical-failure", "code": 7},
                )
            )
            timeout = await executor.execute(
                ExecutionRequest(
                    task_id="task-live",
                    run_id="run-live",
                    correlation_id="corr-live",
                    action="sleep",
                    workspace="run-1",
                    arguments={"seconds": 1.0},
                    timeout_seconds=0.05,
                )
            )

            before_guarded_requests = client.dispatch_count
            environment = await executor.execute(
                ExecutionRequest(
                    task_id="task-live",
                    run_id="run-live",
                    correlation_id="corr-live",
                    action="echo",
                    workspace="run-1",
                    environment={"SYNTHETIC_SECRET": "must-not-dispatch"},
                )
            )
            traversal = await executor.execute(
                ExecutionRequest(
                    task_id="task-live",
                    run_id="run-live",
                    correlation_id="corr-live",
                    action="echo",
                    workspace="../outside",
                )
            )
            guards_dispatched = client.dispatch_count - before_guarded_requests
            collected_artifact = workspace / "evidence" / "artifact.txt"

            evidence["health"] = {
                "healthy": health.healthy,
                "capabilities": list(health.capabilities),
                "metadata": dict(health.metadata),
            }
            evidence["echo"] = {
                "status": echo.status.value,
                "canonical_ids_preserved": (
                    echo.task_id == "task-live"
                    and echo.run_id == "run-live"
                    and echo.correlation_id == "corr-live"
                ),
                "stdout": echo.stdout,
                "output": echo.output,
                "provider_metadata": echo.adapter_metadata.get("swe_rex", {}),
            }
            evidence["artifact"] = {
                "status": artifact.status.value,
                "paths": [item.relative_path for item in artifact.artifacts],
                "collected": collected_artifact.exists(),
                "content": (
                    collected_artifact.read_text(encoding="utf-8")
                    if collected_artifact.exists()
                    else None
                ),
            }
            evidence["failure"] = {
                "status": failure.status.value,
                "result_code": failure.result_code,
                "category": failure.error.category.value if failure.error else None,
            }
            evidence["timeout"] = {
                "status": timeout.status.value,
                "category": timeout.error.category.value if timeout.error else None,
                "provider_cancel_refs": list(client.cancelled_refs),
            }
            evidence["guards"] = {
                "environment_status": environment.status.value,
                "environment_category": (
                    environment.error.category.value if environment.error else None
                ),
                "traversal_status": traversal.status.value,
                "traversal_category": (traversal.error.category.value if traversal.error else None),
                "provider_dispatches": guards_dispatched,
            }

            evidence["canonical_adapter_passed"] = bool(
                health.healthy
                and echo.status is ExecutionStatus.SUCCEEDED
                and evidence["echo"]["canonical_ids_preserved"]
                and echo.output == {"text": "canonical-echo"}
                and artifact.status is ExecutionStatus.SUCCEEDED
                and evidence["artifact"]["collected"]
                and evidence["artifact"]["content"] == "canonical-artifact"
                and failure.status is ExecutionStatus.FAILED
                and failure.result_code == 7
                and failure.error is not None
                and failure.error.category is ExecutionErrorCategory.EXECUTION_FAILED
                and timeout.status is ExecutionStatus.TIMED_OUT
                and timeout.error is not None
                and timeout.error.category is ExecutionErrorCategory.TIMEOUT
                and environment.status is ExecutionStatus.FAILED
                and environment.error is not None
                and environment.error.category is ExecutionErrorCategory.INVALID_REQUEST
                and traversal.status is ExecutionStatus.FAILED
                and traversal.error is not None
                and traversal.error.category is ExecutionErrorCategory.WORKSPACE_ERROR
                and guards_dispatched == 0
            )
    finally:
        await deployment.stop()

    return evidence


async def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--backend",
        choices=("local", "docker", "docker-network-none"),
        required=True,
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        evidence = await _run(args.backend)
    except Exception as exc:
        evidence = {
            "backend": args.backend,
            "host_platform": platform.platform(),
            "python": sys.version,
            "pinned_revision": SWE_REX_EVALUATED_REVISION,
            "expected_version": SWE_REX_EVALUATED_VERSION,
            "canonical_adapter_passed": False,
            "fatal_error": {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc()[-4000:],
            },
        }

    rendered = json.dumps(evidence, indent=2, sort_keys=True)
    print(rendered)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if evidence.get("canonical_adapter_passed") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
