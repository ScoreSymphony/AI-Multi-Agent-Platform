"""Gate-aware application distribution with durable build-runtime provenance."""

from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.contracts.types import JsonValue

from .gated_service import ApplicationDistributionService as _GateApplicationDistributionService
from .models import ApplicationRelease, BuildTargetState, BuildTargetStatus
from .provenance import sanitize_runtime_provenance


class ApplicationDistributionService(_GateApplicationDistributionService):
    """Persist safe measured build provenance before release-gate reconciliation."""

    async def request_build(
        self,
        release_id: str,
        *,
        target_id: str,
        idempotency_key: str,
        actor_ref: str,
        approval_id: str | None = None,
    ) -> ApplicationRelease:
        release = await super().request_build(
            release_id,
            target_id=target_id,
            idempotency_key=idempotency_key,
            actor_ref=actor_ref,
            approval_id=approval_id,
        )
        return await self._reconcile_build_runtime_provenance(release, target_id=target_id)

    async def _admit_execution_output(
        self,
        release: ApplicationRelease,
        state: BuildTargetState,
        output: dict[str, JsonValue],
        *,
        actor_ref: str,
        idempotency_key: str,
    ) -> ApplicationRelease:
        admitted = await super()._admit_execution_output(
            release,
            state,
            output,
            actor_ref=actor_ref,
            idempotency_key=idempotency_key,
        )
        return await self._persist_runtime_provenance(admitted, output)

    async def _reconcile_build_runtime_provenance(
        self,
        release: ApplicationRelease,
        *,
        target_id: str,
    ) -> ApplicationRelease:
        state = next(
            (item for item in release.targets if item.target.target_id == target_id),
            None,
        )
        if (
            state is None
            or state.status is not BuildTargetStatus.SUCCEEDED
            or state.task_id is None
            or state.run_id is None
        ):
            return release
        run = await self.kernel.get_run(state.task_id, state.run_id)
        return await self._persist_runtime_provenance(release, run.output)

    async def _persist_runtime_provenance(
        self,
        release: ApplicationRelease,
        output: dict[str, JsonValue],
    ) -> ApplicationRelease:
        build = output.get("application_build")
        if not isinstance(build, dict):
            return release
        artifact_id = build.get("artifact_id")
        runtime = build.get("runtime_provenance")
        if not isinstance(artifact_id, str) or not isinstance(runtime, dict):
            return release

        safe_runtime = sanitize_runtime_provenance(runtime)
        if not safe_runtime:
            return release
        artifact = next(
            (item for item in release.artifacts if item.artifact_id == artifact_id),
            None,
        )
        if artifact is None:
            return release
        metadata = dict(artifact.external_metadata)
        if all(metadata.get(key) == value for key, value in safe_runtime.items()):
            return release
        metadata.update(safe_runtime)
        artifacts = tuple(
            replace(item, external_metadata=metadata)
            if item.artifact_id == artifact_id
            else item
            for item in release.artifacts
        )
        updated = replace(
            release,
            artifacts=artifacts,
            revision=release.revision + 1,
        )
        return await self.repository.save(updated, expected_revision=release.revision)
