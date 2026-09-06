"""Publish remote Worker Workspace changes through the canonical File/Artifact/Run seams."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.data import DataAccessContext, FileProvider
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.workspaces import RemoteMaterializationResult, Workspace, WorkspaceProvider

from .models import JobResultStatus, WorkerJobRequest, WorkerJobResult
from .registry import RegistryError
from .workspace import MaterializingWorkerDispatcher, WorkspaceDispatchEvidence

WorkspaceArtifactContextResolver = Callable[[Workspace], DataAccessContext]


class WorkspaceArtifactPublisher(Protocol):
    """Turn canonical remote Workspace File changes into canonical Artifact evidence."""

    async def publish(
        self,
        job: WorkerJobRequest,
        result: RemoteMaterializationResult,
    ) -> tuple[str, ...]: ...


class CanonicalWorkspaceArtifactPublisher:
    """Link Worker-produced canonical Files to deterministic Artifacts on the same Run.

    Remote materialization owns byte transfer and therefore returns canonical ``file_*`` IDs.
    This integration deliberately creates a distinct deterministic ``artifact_*`` identity for
    every non-deleted change, links it through ``FileProvider`` and attaches it to the parent
    canonical Run. File IDs are never reinterpreted as Artifact IDs.
    """

    def __init__(
        self,
        workspaces: WorkspaceProvider,
        files: FileProvider,
        kernel: PlatformKernel,
        context_resolver: WorkspaceArtifactContextResolver,
    ) -> None:
        self._workspaces = workspaces
        self._files = files
        self._kernel = kernel
        self._context_resolver = context_resolver

    async def publish(
        self,
        job: WorkerJobRequest,
        result: RemoteMaterializationResult,
    ) -> tuple[str, ...]:
        if job.execution.subject_type != "task":
            raise RegistryError("Worker Workspace artifact publication requires a Task subject")
        if job.workspace_ref != result.workspace_id or job.snapshot_ref != result.snapshot_id:
            raise RegistryError("Worker Workspace artifact result does not match the Worker Job")

        workspace = await self._workspaces.get_workspace(result.workspace_id)
        context = self._context_resolver(workspace)
        if context.project_id != workspace.project_id:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "Worker Workspace artifact context project does not match Workspace project",
            )
        context = replace(
            context,
            operation=job.execution.context,
            task_id=job.execution.subject_id,
            run_id=job.execution.run_id,
        )

        artifact_ids: list[str] = []
        for change in result.changes:
            if change.file_id is None:
                continue
            artifact_id = _artifact_id(job, result, change.relative_path, change.sha256 or "")
            await self._files.link_artifact(change.file_id, artifact_id, context)
            await self._kernel.attach_artifact(
                idempotency_key=f"worker-output:{job.worker_job_id}:{artifact_id}",
                task_id=job.execution.subject_id,
                run_id=job.execution.run_id,
                artifact_id=artifact_id,
                actor_ref=context.actor_ref,
                source="distributed-worker-workspace",
            )
            artifact_ids.append(artifact_id)
        return tuple(dict.fromkeys(artifact_ids))


class ArtifactPublishingWorkerDispatcher:
    """Decorate a materializing Worker so canonical Workspace artifacts reach Worker results."""

    def __init__(
        self,
        dispatcher: MaterializingWorkerDispatcher,
        publisher: WorkspaceArtifactPublisher,
    ) -> None:
        self._dispatcher = dispatcher
        self._publisher = publisher
        self._jobs: dict[str, WorkerJobRequest] = {}
        self._artifact_ids: dict[str, tuple[str, ...]] = {}

    @property
    def worker_id(self) -> str:
        return self._dispatcher.worker_id

    async def dispatch(self, job: WorkerJobRequest):
        existing = self._jobs.get(job.worker_job_id)
        if existing is not None and existing != job:
            raise RegistryError("duplicate worker_job_id carries a different artifact request")
        self._jobs[job.worker_job_id] = job
        return await self._dispatcher.dispatch(job)

    async def get(self, worker_job_id: str):
        return await self._dispatcher.get(worker_job_id)

    async def cancel(self, worker_job_id: str):
        return await self._dispatcher.cancel(worker_job_id)

    async def result(self, worker_job_id: str) -> WorkerJobResult | None:
        result = await self._dispatcher.result(worker_job_id)
        if result is None:
            return None
        artifact_ids = self._artifact_ids.get(worker_job_id, ())
        if result.status is JobResultStatus.SUCCEEDED and not artifact_ids:
            evidence = self._dispatcher.evidence(worker_job_id)
            if evidence.result is not None:
                job = self._job(worker_job_id)
                artifact_ids = await self._publisher.publish(job, evidence.result)
                self._artifact_ids[worker_job_id] = artifact_ids
        return replace(
            result,
            artifact_refs=tuple(dict.fromkeys((*result.artifact_refs, *artifact_ids))),
        )

    def evidence(self, worker_job_id: str) -> WorkspaceDispatchEvidence:
        evidence = self._dispatcher.evidence(worker_job_id)
        artifact_ids = self._artifact_ids.get(worker_job_id, ())
        if evidence.result is None or not artifact_ids:
            return evidence
        enriched = replace(
            evidence.result,
            artifact_ids=tuple(
                dict.fromkeys((*evidence.result.artifact_ids, *artifact_ids))
            ),
        )
        return replace(evidence, result=enriched)

    def _job(self, worker_job_id: str) -> WorkerJobRequest:
        try:
            return self._jobs[worker_job_id]
        except KeyError as exc:
            raise RegistryError(
                f"Worker artifact publication has no request: {worker_job_id}"
            ) from exc


def _artifact_id(
    job: WorkerJobRequest,
    result: RemoteMaterializationResult,
    relative_path: str,
    sha256: str,
) -> str:
    identity = json.dumps(
        (
            job.worker_job_id,
            result.materialization_ref,
            relative_path,
            sha256,
        ),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"artifact_{uuid5(NAMESPACE_URL, identity)}"


__all__ = [
    "ArtifactPublishingWorkerDispatcher",
    "CanonicalWorkspaceArtifactPublisher",
    "WorkspaceArtifactContextResolver",
    "WorkspaceArtifactPublisher",
]
