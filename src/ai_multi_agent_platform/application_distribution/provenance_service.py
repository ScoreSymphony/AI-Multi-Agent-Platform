"""Gate-aware application distribution with durable build-runtime provenance."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .contracts import PublicationResult
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

    async def _apply_publication(
        self,
        release: ApplicationRelease,
        result: PublicationResult,
    ) -> ApplicationRelease:
        """Keep canonical build provenance while adding namespaced publisher metadata."""

        canonical_metadata = {
            artifact.artifact_id: artifact.external_metadata for artifact in release.artifacts
        }
        merged_result = replace(
            result,
            artifacts=tuple(
                replace(
                    published,
                    external_metadata=_merge_external_metadata(
                        canonical_metadata.get(published.artifact_id, {}),
                        published.external_metadata,
                    ),
                )
                for published in result.artifacts
            ),
        )
        return await super()._apply_publication(release, merged_result)

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
        output: Mapping[str, JsonValue],
    ) -> ApplicationRelease:
        # Canonical Event payloads may freeze nested JSON objects behind MappingProxyType.
        # Treat the provider-neutral JSON contract as Mapping rather than requiring mutable dicts.
        nested = output.get("output")
        if not isinstance(nested, Mapping):
            return release
        build = nested.get("application_build")
        if not isinstance(build, Mapping):
            return release
        artifact_id = build.get("artifact_id")
        runtime = build.get("runtime_provenance")
        if not isinstance(runtime, Mapping):
            runtime = nested.get("runtime_provenance")
        if not isinstance(artifact_id, str) or not isinstance(runtime, Mapping):
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
            replace(item, external_metadata=metadata) if item.artifact_id == artifact_id else item
            for item in release.artifacts
        )
        updated = replace(
            release,
            artifacts=artifacts,
            revision=release.revision + 1,
        )
        return await self.repository.save(updated, expected_revision=release.revision)


def _merge_external_metadata(
    canonical: Mapping[str, JsonValue],
    published: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    merged = dict(canonical)
    for key, value in published.items():
        if key in merged and merged[key] != value:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "publisher external metadata conflicts with canonical build provenance",
                details={"metadata_key": key},
            )
        merged[key] = value
    return merged
