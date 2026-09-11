"""Measured runtime provenance for application-build lifecycle results."""

from __future__ import annotations

import platform
from dataclasses import replace

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.contracts import ExecutionStatus
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.data import DataAccessContext
from ai_multi_agent_platform.execution import ExecutionResult

from .execution import ApplicationBuildLifecycleBackend as _BaseApplicationBuildLifecycleBackend
from .models import ApplicationRelease, BuildTargetState
from .provenance import sanitize_runtime_provenance


class ApplicationBuildLifecycleBackend(_BaseApplicationBuildLifecycleBackend):
    """Attach measured executor/runtime identity to canonical application-build output."""

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

        raw: dict[str, JsonValue] = dict(self._executor.descriptor.metadata)  # noqa: SLF001
        raw["executor_id"] = self._executor.descriptor.executor_id  # noqa: SLF001
        raw["build_provider_version"] = __version__
        os_name = platform.system().strip().lower()
        architecture = platform.machine().strip()
        if os_name:
            raw["os"] = os_name
        if architecture:
            raw["architecture"] = architecture

        provenance = sanitize_runtime_provenance(raw)
        if not provenance:
            return captured
        enriched_build = dict(build)
        enriched_build["runtime_provenance"] = provenance
        output = dict(captured.output)
        output["application_build"] = enriched_build
        return replace(captured, output=output)
