"""Measured runtime provenance for application-build execution and lifecycle results."""

from __future__ import annotations

import platform
from dataclasses import replace

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.contracts import ExecutionStatus
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.data import DataAccessContext
from ai_multi_agent_platform.execution import ExecutionRequest, ExecutionResult

from .execution import ApplicationBuildLifecycleBackend as _BaseApplicationBuildLifecycleBackend
from .execution import ApplicationCommandExecutor as _BaseApplicationCommandExecutor
from .models import ApplicationRelease, BuildTargetState
from .provenance import sanitize_runtime_provenance


class ApplicationCommandExecutor(_BaseApplicationCommandExecutor):
    """Reference build executor that reports only runtime facts it can actually measure."""

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        result = await super().execute(request)
        if result.status is not ExecutionStatus.SUCCEEDED:
            return result
        provenance = _measured_runtime_provenance(
            executor_id=self.descriptor.executor_id,
            descriptor_metadata=self.descriptor.metadata,
        )
        if not provenance:
            return result
        output = dict(result.output)
        output["runtime_provenance"] = provenance
        return replace(result, output=output)


class ApplicationBuildLifecycleBackend(_BaseApplicationBuildLifecycleBackend):
    """Attach executor/provider identity to canonical application-build output when available."""

    async def _capture_output(
        self,
        release: ApplicationRelease,
        target: BuildTargetState,
        materialization_id: str,
        context: DataAccessContext,
        result: ExecutionResult,
    ) -> ExecutionResult:
        captured = await super()._capture_output(
            release,
            target,
            materialization_id,
            context,
            result,
        )
        if captured.status is not ExecutionStatus.SUCCEEDED:
            return captured
        build = captured.output.get("application_build")
        if not isinstance(build, dict):
            return captured

        runtime = captured.output.get("runtime_provenance")
        if isinstance(runtime, dict):
            provenance = sanitize_runtime_provenance(runtime)
        else:
            provenance = _measured_runtime_provenance(
                executor_id=self._executor.descriptor.executor_id,  # noqa: SLF001
                descriptor_metadata=self._executor.descriptor.metadata,  # noqa: SLF001
            )
        if not provenance:
            return captured
        enriched_build = dict(build)
        enriched_build["runtime_provenance"] = provenance
        output = dict(captured.output)
        output["application_build"] = enriched_build
        return replace(captured, output=output)


def _measured_runtime_provenance(
    *,
    executor_id: str,
    descriptor_metadata: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    raw: dict[str, JsonValue] = dict(descriptor_metadata)
    raw["executor_id"] = executor_id
    raw["executor_version"] = __version__
    raw["runtime_id"] = "python"
    raw["runtime_version"] = platform.python_version()
    raw["build_provider_version"] = __version__
    os_name = platform.system().strip().lower()
    architecture = platform.machine().strip()
    if os_name:
        raw["os"] = os_name
    if architecture:
        raw["architecture"] = architecture
    return sanitize_runtime_provenance(raw)
