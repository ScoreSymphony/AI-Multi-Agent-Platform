"""Executor-private Workspace materialization for local Application processes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.data import DataAccessContext
from ai_multi_agent_platform.workspaces import (
    MaterializationOutcome,
    WorkspaceAccessMode,
    WorkspaceProvider,
)

from .models import (
    ApplicationInstallRequest,
    ApplicationInstance,
    ApplicationManifest,
    ApplicationService,
    ApplicationVolumeKind,
)
from .runtime import ApplicationPreparationError, ApplicationRuntimeError

_WORKSPACE_MOUNT_TARGET = "."
_WORKSPACE_ACTOR_REF = "service:application-runtime"


@dataclass(frozen=True, slots=True)
class LocalApplicationWorkspaceExecution:
    """Backend-private execution handle for one materialized canonical Workspace."""

    volume_name: str
    workspace_id: str
    materialization_id: str
    path: Path
    context: DataAccessContext
    base_revision: int
    access_mode: WorkspaceAccessMode
    mounted_service_ids: frozenset[str]


class LocalApplicationWorkspaceBinder:
    """Bind one canonical Workspace to PROCESS service working directories."""

    def __init__(
        self,
        provider: WorkspaceProvider,
        local_path: Callable[[str], Path],
    ) -> None:
        self._provider = provider
        self._local_path = local_path

    def validate_request(self, request: ApplicationInstallRequest) -> None:
        workspace_volumes = tuple(
            volume
            for volume in request.manifest.volumes
            if volume.kind is ApplicationVolumeKind.WORKSPACE
        )
        unsupported = tuple(
            volume
            for volume in request.manifest.volumes
            if volume.kind is not ApplicationVolumeKind.WORKSPACE
        )
        if unsupported:
            raise ApplicationPreparationError(
                "local process runtime only supports Workspace volumes; "
                f"unsupported volumes: {[volume.name for volume in unsupported]!r}"
            )
        if len(workspace_volumes) > 1:
            raise ApplicationPreparationError(
                "local process runtime supports at most one Workspace volume"
            )
        if not workspace_volumes:
            return

        volume = workspace_volumes[0]
        binding = next(
            (item for item in request.volume_bindings if item.volume_name == volume.name),
            None,
        )
        mounted = False
        for service in request.manifest.services:
            for mount in service.mounts:
                if mount.volume_name != volume.name:
                    continue
                mounted = True
                if mount.target != _WORKSPACE_MOUNT_TARGET:
                    raise ApplicationPreparationError(
                        "local process runtime can expose a Workspace only as the process "
                        "working directory using mount target '.'"
                    )
        if mounted and binding is None:
            raise ApplicationPreparationError(
                f"mounted Workspace volume has no binding: {volume.name}"
            )

    async def materialize(
        self,
        manifest: ApplicationManifest,
        instance: ApplicationInstance,
    ) -> LocalApplicationWorkspaceExecution | None:
        workspace_volumes = tuple(
            volume for volume in manifest.volumes if volume.kind is ApplicationVolumeKind.WORKSPACE
        )
        if not workspace_volumes:
            return None
        volume = workspace_volumes[0]
        mounted_service_ids = frozenset(
            service.service_id
            for service in manifest.services
            if any(mount.volume_name == volume.name for mount in service.mounts)
        )
        if not mounted_service_ids:
            return None
        binding = next(
            (item for item in instance.volume_bindings if item.volume_name == volume.name),
            None,
        )
        if binding is None:
            raise ApplicationRuntimeError(
                f"application Workspace volume is not bound: {volume.name}"
            )

        try:
            workspace = await self._provider.get_workspace(binding.source_ref)
        except ContractError as exc:
            raise ApplicationRuntimeError("application Workspace lookup failed") from exc

        requested_read_only = binding.read_only or any(
            mount.read_only
            for service in manifest.services
            for mount in service.mounts
            if mount.volume_name == volume.name
        )
        if requested_read_only and workspace.access_mode is WorkspaceAccessMode.READ_WRITE:
            raise ApplicationRuntimeError(
                "local process runtime cannot enforce a read-only projection of a "
                "read-write Workspace"
            )

        context = DataAccessContext(
            operation=OperationContext(
                correlation_id=f"application:{instance.instance_id}",
                owner_type="application_instance",
                owner_id=instance.instance_id,
                project_id=workspace.project_id,
            ),
            actor_ref=_WORKSPACE_ACTOR_REF,
        )
        try:
            materialization = await self._provider.materialize(workspace.id, context)
        except ContractError as exc:
            raise ApplicationRuntimeError("application Workspace materialization failed") from exc
        try:
            path = self._local_path(materialization.id)
        except ContractError as exc:
            await self._release_after_failure(materialization.id)
            raise ApplicationRuntimeError(
                "application Workspace local path resolution failed"
            ) from exc
        if not path.is_dir():
            await self._release_after_failure(materialization.id)
            raise ApplicationRuntimeError(
                "application Workspace materialization did not produce a local directory"
            )
        return LocalApplicationWorkspaceExecution(
            volume_name=volume.name,
            workspace_id=workspace.id,
            materialization_id=materialization.id,
            path=path,
            context=context,
            base_revision=materialization.base_revision,
            access_mode=materialization.access_mode,
            mounted_service_ids=mounted_service_ids,
        )

    def service_cwd(
        self,
        execution: LocalApplicationWorkspaceExecution | None,
        service: ApplicationService,
    ) -> Path | None:
        if execution is None or service.service_id not in execution.mounted_service_ids:
            return None
        return execution.path

    async def release(
        self,
        execution: LocalApplicationWorkspaceExecution | None,
        *,
        outcome: MaterializationOutcome,
        commit: bool,
    ) -> None:
        if execution is None:
            return
        if commit and execution.access_mode is WorkspaceAccessMode.READ_WRITE:
            try:
                await self._provider.commit_changes(
                    execution.materialization_id,
                    execution.context,
                    expected_revision=execution.base_revision,
                )
            except ContractError as exc:
                await self._release_after_failure(execution.materialization_id)
                raise ApplicationRuntimeError("application Workspace commit failed") from exc
        try:
            await self._provider.release_materialization(
                execution.materialization_id,
                outcome,
            )
        except ContractError as exc:
            raise ApplicationRuntimeError("application Workspace release failed") from exc

    async def _release_after_failure(self, materialization_id: str) -> None:
        try:
            await self._provider.release_materialization(
                materialization_id,
                MaterializationOutcome.FAILED,
            )
        except ContractError as exc:
            raise ApplicationRuntimeError(
                "application Workspace cleanup failed"
            ) from exc
