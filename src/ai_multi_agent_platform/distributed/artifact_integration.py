"""Promote remote Worker workspace changes into canonical Run Artifact evidence.

Remote workspace collection already creates canonical ``file_*`` records at the Control Plane.
This module deliberately reuses that boundary: it links those files to deterministic canonical
``artifact_*`` identities and attaches the Artifact evidence to the existing parent Task/Run.
No Worker-private path or transport identifier becomes canonical lifecycle state.
"""

from __future__ import annotations

import json
from dataclasses import replace
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.data import DataAccessContext, FileProvider
from ai_multi_agent_platform.domain import validate_id
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.workspaces import RemoteMaterializationResult

from .models import WorkerJobRequest

_WORKER_ARTIFACT_NAMESPACE = "ai-multi-agent-platform:worker-workspace-artifact"


class CanonicalWorkerArtifactIntegrator:
    """Link collected Worker files to canonical Artifacts on the existing parent Run."""

    def __init__(self, files: FileProvider, kernel: PlatformKernel) -> None:
        self._files = files
        self._kernel = kernel

    async def integrate(
        self,
        job: WorkerJobRequest,
        result: RemoteMaterializationResult,
    ) -> RemoteMaterializationResult:
        """Return the same materialization result enriched with canonical Artifact IDs.

        Integration is idempotent. Artifact identity is derived only from canonical Run/WorkerJob
        identity plus immutable collected-file evidence; provider paths and backend handles are not
        part of the identity. The FileProvider remains the content authority while the Kernel owns
        the Task/Run attachment.
        """

        task_id = job.execution.context.correlation_id
        run_id = job.execution.run_id
        validate_id(task_id, "task")
        validate_id(run_id, "run")
        if job.workspace_ref != result.workspace_id or job.snapshot_ref != result.snapshot_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "remote Worker result does not match the canonical Worker Job Workspace binding",
                details={"worker_job_id": job.worker_job_id},
            )

        context = DataAccessContext(
            operation=job.execution.context,
            actor_ref=_actor_ref(job),
            task_id=task_id,
            run_id=run_id,
        )
        artifact_ids = list(result.artifact_ids)
        for change in result.changes:
            if change.file_id is None:
                continue
            artifact_id = worker_change_artifact_id(job, result, change.relative_path, change.sha256)
            await self._files.link_artifact(change.file_id, artifact_id, context)
            await self._kernel.attach_artifact(
                idempotency_key=f"worker-output:{job.worker_job_id}:{artifact_id}",
                task_id=task_id,
                run_id=run_id,
                artifact_id=artifact_id,
                actor_ref=context.actor_ref,
                source="distributed-workspace-output",
            )
            artifact_ids.append(artifact_id)

        return replace(result, artifact_ids=tuple(dict.fromkeys(artifact_ids)))


def worker_change_artifact_id(
    job: WorkerJobRequest,
    result: RemoteMaterializationResult,
    relative_path: str,
    sha256: str | None,
) -> str:
    """Derive stable Artifact identity from canonical execution and immutable file evidence."""

    if not relative_path.strip():
        raise ValueError("worker artifact relative_path must not be blank")
    if sha256 is None:
        raise ValueError("worker artifact file change requires sha256 evidence")
    identity = json.dumps(
        [
            job.execution.run_id,
            job.worker_job_id,
            result.materialization_ref,
            relative_path,
            sha256,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"artifact_{uuid5(NAMESPACE_URL, f'{_WORKER_ARTIFACT_NAMESPACE}:{identity}')}"


def _actor_ref(job: WorkerJobRequest) -> str:
    if job.actor_ref is not None and job.actor_ref.strip():
        return job.actor_ref
    context = job.execution.context
    if context.owner_type is not None and context.owner_id is not None:
        return f"{context.owner_type}:{context.owner_id}"
    return "service:platform"


__all__ = ["CanonicalWorkerArtifactIntegrator", "worker_change_artifact_id"]
