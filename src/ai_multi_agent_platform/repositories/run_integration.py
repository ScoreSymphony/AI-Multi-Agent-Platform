"""Bridge repository-backed Workspace snapshots and Run artifact/provenance evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.data import DataAccessContext, FileProvider
from ai_multi_agent_platform.domain import validate_id
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.workspaces import (
    WorkspaceChangeSet,
    WorkspaceProvider,
    WorkspaceSnapshot,
    WorkspaceSourceKind,
)

from .models import RepositoryRunProvenance, utc_now, validate_git_revision
from .service import RepositoryProvenanceStore, RepositoryRegistry


@dataclass(frozen=True, slots=True)
class RepositoryRunArtifactBundle:
    """Canonical Workspace change evidence returned for one repository-backed Run."""

    run_id: str
    change_set: WorkspaceChangeSet
    artifact_ids: tuple[str, ...]
    manifest_artifact_id: str | None = None

    def __post_init__(self) -> None:
        validate_id(self.run_id, "run")
        for artifact_id in self.artifact_ids:
            validate_id(artifact_id, "artifact")
        if self.manifest_artifact_id is not None:
            validate_id(self.manifest_artifact_id, "artifact")
            if self.manifest_artifact_id not in self.artifact_ids:
                raise ValueError("manifest artifact must be included in artifact_ids")


class RepositoryRunIntegration:
    """Record exact repository inputs and return Workspace changes as canonical artifacts."""

    def __init__(
        self,
        repositories: RepositoryRegistry,
        provenance: RepositoryProvenanceStore,
        workspaces: WorkspaceProvider,
        files: FileProvider,
        kernel: PlatformKernel,
    ) -> None:
        self._repositories = repositories
        self._provenance = provenance
        self._workspaces = workspaces
        self._files = files
        self._kernel = kernel

    def record_input_snapshot(
        self,
        *,
        run_id: str,
        snapshot: WorkspaceSnapshot,
        actor_ref: str,
        task_id: str | None = None,
    ) -> tuple[RepositoryRunProvenance, ...]:
        """Persist immutable repository inputs from the exact Workspace snapshot bound to a Run."""

        validate_id(run_id, "run")
        if task_id is not None:
            validate_id(task_id, "task")
        if not actor_ref.strip():
            raise ValueError("repository run actor_ref must not be blank")

        recorded: list[RepositoryRunProvenance] = []
        for source_ref in snapshot.source_refs:
            if source_ref.kind is not WorkspaceSourceKind.REPOSITORY:
                continue
            if source_ref.revision is None:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "repository Workspace source must be resolved to an immutable revision "
                    "before Run binding",
                    details={"repository_id": source_ref.ref, "snapshot_id": snapshot.id},
                )
            binding = self._repositories.resolve(source_ref.ref)
            requested = source_ref.metadata.get("requested_revision")
            branch_ref = requested if isinstance(requested, str) and requested.strip() else None
            provenance = RepositoryRunProvenance(
                run_id=run_id,
                task_id=task_id,
                repository_id=binding.reference.id,
                input_revision=source_ref.revision,
                branch_ref=branch_ref,
                actor_ref=actor_ref,
                provider_resource_ids=(binding.reference.id,),
            )
            self._provenance.upsert(provenance)
            recorded.append(provenance)
        return tuple(recorded)

    async def capture_workspace_changes(
        self,
        *,
        run_id: str,
        task_id: str,
        materialization_id: str,
        actor_ref: str,
        context: DataAccessContext,
        output_revisions: Mapping[str, str] | None = None,
    ) -> RepositoryRunArtifactBundle:
        """Capture changed files, create canonical artifacts, and enrich Run provenance."""

        validate_id(run_id, "run")
        validate_id(task_id, "task")
        if not actor_ref.strip():
            raise ValueError("repository run actor_ref must not be blank")
        change_set = await self._workspaces.capture_changes(materialization_id, context)
        artifact_ids: list[str] = []

        for change in change_set.changes:
            if change.file_id is None:
                continue
            artifact_id = _deterministic_id(
                "artifact",
                run_id,
                materialization_id,
                change.relative_path,
                change.kind.value,
                change.sha256 or "",
            )
            await self._files.link_artifact(change.file_id, artifact_id, context)
            await self._kernel.attach_artifact(
                idempotency_key=f"repository-output:{materialization_id}:{artifact_id}",
                task_id=task_id,
                run_id=run_id,
                artifact_id=artifact_id,
                actor_ref=actor_ref,
                source="repository-run-integration",
            )
            artifact_ids.append(artifact_id)

        manifest_artifact_id: str | None = None
        if change_set.changes:
            manifest_artifact_id = await self._create_change_manifest(
                run_id=run_id,
                task_id=task_id,
                materialization_id=materialization_id,
                actor_ref=actor_ref,
                context=context,
                change_set=change_set,
            )
            artifact_ids.append(manifest_artifact_id)

        revisions = dict(output_revisions or {})
        for repository_id, revision in revisions.items():
            validate_git_revision(revision)
            if self._provenance.get(run_id, repository_id) is None:
                raise ContractError(
                    ErrorCode.NOT_FOUND,
                    "cannot record repository output revision without Run input provenance",
                    details={"run_id": run_id, "repository_id": repository_id},
                )

        for current in self._provenance.for_run(run_id):
            merged_artifacts = tuple(dict.fromkeys((*current.diff_artifact_ids, *artifact_ids)))
            output_revision = revisions.get(current.repository_id, current.output_revision)
            self._provenance.upsert(
                replace(
                    current,
                    output_revision=output_revision,
                    diff_artifact_ids=merged_artifacts,
                    actor_ref=actor_ref,
                    recorded_at=utc_now(),
                )
            )

        return RepositoryRunArtifactBundle(
            run_id=run_id,
            change_set=change_set,
            artifact_ids=tuple(artifact_ids),
            manifest_artifact_id=manifest_artifact_id,
        )

    def record_output_revision(
        self,
        *,
        run_id: str,
        repository_id: str,
        output_revision: str,
        actor_ref: str | None = None,
        artifact_ids: tuple[str, ...] = (),
    ) -> RepositoryRunProvenance:
        """Record a commit created after execution without implying that push occurred."""

        validate_id(run_id, "run")
        validate_git_revision(output_revision)
        current = self._provenance.get(run_id, repository_id)
        if current is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                "cannot record repository output revision without Run input provenance",
                details={"run_id": run_id, "repository_id": repository_id},
            )
        for artifact_id in artifact_ids:
            validate_id(artifact_id, "artifact")
        updated = replace(
            current,
            output_revision=output_revision,
            actor_ref=actor_ref or current.actor_ref,
            diff_artifact_ids=tuple(dict.fromkeys((*current.diff_artifact_ids, *artifact_ids))),
            recorded_at=utc_now(),
        )
        self._provenance.upsert(updated)
        return updated

    async def _create_change_manifest(
        self,
        *,
        run_id: str,
        task_id: str,
        materialization_id: str,
        actor_ref: str,
        context: DataAccessContext,
        change_set: WorkspaceChangeSet,
    ) -> str:
        artifact_id = _deterministic_id(
            "artifact",
            run_id,
            materialization_id,
            "repository-change-manifest",
        )
        file_id = _deterministic_id(
            "file",
            run_id,
            materialization_id,
            "repository-change-manifest",
        )
        manifest = {
            "schema_version": "1.0",
            "run_id": run_id,
            "task_id": task_id,
            "workspace_id": change_set.workspace_id,
            "workspace_snapshot_id": change_set.snapshot_id,
            "materialization_id": materialization_id,
            "base_revision": change_set.base_revision,
            "changes": [
                {
                    "path": change.relative_path,
                    "kind": change.kind.value,
                    "file_id": change.file_id,
                    "sha256": change.sha256,
                }
                for change in change_set.changes
            ],
        }
        payload = json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            record = await self._files.get_file(file_id, context)
        except ContractError as exc:
            if exc.code is not ErrorCode.NOT_FOUND:
                raise
            record = await self._files.create_file(
                payload,
                context,
                file_id=file_id,
                content_type="application/json",
                metadata={
                    "source_kind": "repository_run_changes",
                    "run_id": run_id,
                    "task_id": task_id,
                    "materialization_id": materialization_id,
                },
            )
        await self._files.link_artifact(record.file_id, artifact_id, context)
        await self._kernel.attach_artifact(
            idempotency_key=f"repository-output:{materialization_id}:{artifact_id}",
            task_id=task_id,
            run_id=run_id,
            artifact_id=artifact_id,
            actor_ref=actor_ref,
            source="repository-run-integration",
        )
        return artifact_id


def _deterministic_id(prefix: str, *parts: str) -> str:
    identity = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return f"{prefix}_{uuid5(NAMESPACE_URL, identity)}"
