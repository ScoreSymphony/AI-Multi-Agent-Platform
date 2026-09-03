"""Deterministic local workspace implementation backed by canonical FileProvider objects."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
from dataclasses import replace
from pathlib import Path

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.data import DataAccessContext, FileProvider
from ai_multi_agent_platform.domain import OwnerRef, new_id, validate_id

from .contracts import WorkspaceProvider
from .models import (
    CleanupReport,
    MaterializationOutcome,
    Workspace,
    WorkspaceAccessMode,
    WorkspaceChange,
    WorkspaceChangeKind,
    WorkspaceChangeSet,
    WorkspaceFile,
    WorkspaceMaterialization,
    WorkspaceRetention,
    WorkspaceSnapshot,
    WorkspaceSourceRef,
    WorkspaceStatus,
    WorkspaceType,
    utc_now,
    validate_relative_path,
)


class LocalWorkspaceProvider(WorkspaceProvider):
    """Local reference implementation with isolated per-run materialization directories.

    Workspace and snapshot identity stays canonical and independent from local paths. The
    filesystem under ``root`` is only a materialization cache/execution boundary; durable
    bytes are returned to the configured canonical FileProvider.
    """

    def __init__(self, root: str | Path, files: FileProvider) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._files = files
        self._workspaces: dict[str, Workspace] = {}
        self._snapshots: dict[str, WorkspaceSnapshot] = {}
        self._heads: dict[str, str] = {}
        self._materializations: dict[str, WorkspaceMaterialization] = {}
        self._materialization_paths: dict[str, Path] = {}
        self._lock = asyncio.Lock()

    @property
    def materialization_root(self) -> Path:
        """Local-only root for wiring the reference executor; never a canonical identity."""

        return self._root

    async def create_workspace(
        self,
        *,
        project_id: str,
        owner_ref: OwnerRef,
        workspace_type: WorkspaceType,
        context: DataAccessContext,
        access_mode: WorkspaceAccessMode = WorkspaceAccessMode.READ_WRITE,
        retention: WorkspaceRetention = WorkspaceRetention.PERSISTENT,
        source_refs: tuple[WorkspaceSourceRef, ...] = (),
        files: tuple[WorkspaceFile, ...] = (),
        workspace_id: str | None = None,
    ) -> Workspace:
        validate_id(project_id, "project")
        if context.project_id != project_id:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "workspace project must match the data access context project",
            )
        canonical_id = workspace_id or new_id("workspace")
        validate_id(canonical_id, "workspace")
        await self._verify_files(files, context)
        workspace = Workspace(
            id=canonical_id,
            project_id=project_id,
            owner_ref=owner_ref,
            workspace_type=workspace_type,
            access_mode=access_mode,
            retention=retention,
            source_refs=source_refs,
        )
        snapshot = self._snapshot_for(workspace, files, parent_snapshot_id=None)
        workspace = replace(workspace, base_snapshot_id=snapshot.id)
        async with self._lock:
            if canonical_id in self._workspaces:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    f"workspace already exists: {canonical_id}",
                )
            self._workspaces[canonical_id] = workspace
            self._snapshots[snapshot.id] = snapshot
            self._heads[canonical_id] = snapshot.id
        return workspace

    async def get_workspace(self, workspace_id: str) -> Workspace:
        validate_id(workspace_id, "workspace")
        async with self._lock:
            workspace = self._workspaces.get(workspace_id)
        if workspace is None or workspace.status is WorkspaceStatus.DELETED:
            raise ContractError(ErrorCode.NOT_FOUND, f"workspace not found: {workspace_id}")
        return workspace

    async def list_workspaces(self, *, project_id: str | None = None) -> tuple[Workspace, ...]:
        if project_id is not None:
            validate_id(project_id, "project")
        async with self._lock:
            values = tuple(self._workspaces.values())
        return tuple(
            workspace
            for workspace in values
            if workspace.status is not WorkspaceStatus.DELETED
            and (project_id is None or workspace.project_id == project_id)
        )

    async def get_snapshot(self, snapshot_id: str) -> WorkspaceSnapshot:
        validate_id(snapshot_id, "workspace_snapshot")
        async with self._lock:
            snapshot = self._snapshots.get(snapshot_id)
        if snapshot is None:
            raise ContractError(ErrorCode.NOT_FOUND, f"workspace snapshot not found: {snapshot_id}")
        return snapshot

    async def create_snapshot(self, workspace_id: str) -> WorkspaceSnapshot:
        workspace = await self.get_workspace(workspace_id)
        async with self._lock:
            head_id = self._heads[workspace_id]
            head = self._snapshots[head_id]
            snapshot = self._snapshot_for(
                workspace,
                head.files,
                parent_snapshot_id=head.id,
            )
            self._snapshots[snapshot.id] = snapshot
            self._heads[workspace_id] = snapshot.id
            self._workspaces[workspace_id] = replace(
                workspace,
                base_snapshot_id=snapshot.id,
                updated_at=utc_now(),
            )
        return snapshot

    async def materialize(
        self,
        workspace_id: str,
        context: DataAccessContext,
        *,
        snapshot_id: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
    ) -> WorkspaceMaterialization:
        workspace = await self.get_workspace(workspace_id)
        if context.project_id != workspace.project_id:
            raise ContractError(ErrorCode.FORBIDDEN, "workspace belongs to another project")
        selected_snapshot_id = snapshot_id or workspace.base_snapshot_id
        if selected_snapshot_id is None:
            raise ContractError(ErrorCode.CONTRACT_VIOLATION, "workspace has no base snapshot")
        snapshot = await self.get_snapshot(selected_snapshot_id)
        if snapshot.workspace_id != workspace.id:
            raise ContractError(ErrorCode.INVALID_REQUEST, "snapshot belongs to another workspace")
        if snapshot.content_checksum != self._snapshot_checksum(snapshot.files):
            raise ContractError(ErrorCode.CONTRACT_VIOLATION, "workspace snapshot checksum mismatch")

        materialization = WorkspaceMaterialization(
            workspace_id=workspace.id,
            snapshot_id=snapshot.id,
            base_revision=workspace.revision,
            access_mode=workspace.access_mode,
            task_id=task_id,
            run_id=run_id,
        )
        local_root = self._root / materialization.execution_workspace
        try:
            local_root.mkdir(parents=False, exist_ok=False)
            for entry in snapshot.files:
                destination = self._safe_target(local_root, entry.relative_path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                data = await self._read_file(entry.file_id, context)
                digest = hashlib.sha256(data).hexdigest()
                if digest != entry.sha256:
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        f"source checksum mismatch for workspace file: {entry.relative_path}",
                    )
                destination.write_bytes(data)
            self._reject_symlinks(local_root)
            if materialization.access_mode is WorkspaceAccessMode.READ_ONLY:
                self._make_read_only(local_root)
        except Exception:
            self._make_writable(local_root)
            shutil.rmtree(local_root, ignore_errors=True)
            raise

        async with self._lock:
            self._materializations[materialization.id] = materialization
            self._materialization_paths[materialization.id] = local_root
            self._workspaces[workspace.id] = replace(
                workspace,
                last_used_at=utc_now(),
                active_task_ids=_with_optional(workspace.active_task_ids, task_id),
                active_run_ids=_with_optional(workspace.active_run_ids, run_id),
            )
        return materialization

    def local_path(self, materialization_id: str) -> Path:
        """Return a local implementation detail for executor wiring/tests only."""

        validate_id(materialization_id, "materialization")
        path = self._materialization_paths.get(materialization_id)
        if path is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"workspace materialization not found: {materialization_id}",
            )
        return path

    async def capture_changes(
        self,
        materialization_id: str,
        context: DataAccessContext,
    ) -> WorkspaceChangeSet:
        materialization = await self._get_materialization(materialization_id)
        snapshot = await self.get_snapshot(materialization.snapshot_id)
        root = self.local_path(materialization_id)
        if context.project_id != (await self.get_workspace(materialization.workspace_id)).project_id:
            raise ContractError(ErrorCode.FORBIDDEN, "workspace belongs to another project")
        current = self._scan_files(root)
        base = {entry.relative_path: entry for entry in snapshot.files}
        changed_paths = {
            path
            for path, (_, digest) in current.items()
            if path not in base or base[path].sha256 != digest
        }
        deleted_paths = set(base) - set(current)
        if materialization.access_mode is WorkspaceAccessMode.READ_ONLY:
            if changed_paths or deleted_paths:
                raise ContractError(ErrorCode.FORBIDDEN, "read-only workspace was modified")
            return WorkspaceChangeSet(
                workspace_id=materialization.workspace_id,
                snapshot_id=materialization.snapshot_id,
                materialization_id=materialization.id,
                base_revision=materialization.base_revision,
                changes=(),
            )

        changes: list[WorkspaceChange] = []
        for relative_path in sorted(changed_paths):
            data, digest = current[relative_path]
            metadata: dict[str, JsonValue] = {
                "workspace_id": materialization.workspace_id,
                "workspace_snapshot_id": materialization.snapshot_id,
                "relative_path": relative_path,
            }
            record = await self._files.create_file(data, context, metadata=metadata)
            changes.append(
                WorkspaceChange(
                    relative_path=relative_path,
                    kind=(
                        WorkspaceChangeKind.CREATED
                        if relative_path not in base
                        else WorkspaceChangeKind.MODIFIED
                    ),
                    file_id=record.file_id,
                    sha256=digest,
                )
            )
        for relative_path in sorted(deleted_paths):
            changes.append(
                WorkspaceChange(
                    relative_path=relative_path,
                    kind=WorkspaceChangeKind.DELETED,
                )
            )
        return WorkspaceChangeSet(
            workspace_id=materialization.workspace_id,
            snapshot_id=materialization.snapshot_id,
            materialization_id=materialization.id,
            base_revision=materialization.base_revision,
            changes=tuple(changes),
        )

    async def commit_changes(
        self,
        materialization_id: str,
        context: DataAccessContext,
        *,
        expected_revision: int,
    ) -> WorkspaceSnapshot:
        materialization = await self._get_materialization(materialization_id)
        workspace = await self.get_workspace(materialization.workspace_id)
        if expected_revision != workspace.revision or materialization.base_revision != workspace.revision:
            raise ContractError(ErrorCode.CONFLICT, "stale workspace revision")
        change_set = await self.capture_changes(materialization_id, context)
        async with self._lock:
            current = self._workspaces[workspace.id]
            if expected_revision != current.revision or materialization.base_revision != current.revision:
                raise ContractError(ErrorCode.CONFLICT, "stale workspace revision")
            base_snapshot = self._snapshots[materialization.snapshot_id]
            manifest = {entry.relative_path: entry for entry in base_snapshot.files}
            for change in change_set.changes:
                if change.kind is WorkspaceChangeKind.DELETED:
                    manifest.pop(change.relative_path, None)
                    continue
                if change.file_id is None or change.sha256 is None:
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "workspace change is missing canonical file evidence",
                    )
                manifest[change.relative_path] = WorkspaceFile(
                    relative_path=change.relative_path,
                    file_id=change.file_id,
                    sha256=change.sha256,
                )
            next_workspace = replace(
                current,
                revision=current.revision + 1,
                updated_at=utc_now(),
                last_used_at=utc_now(),
            )
            snapshot = self._snapshot_for(
                next_workspace,
                tuple(manifest[path] for path in sorted(manifest)),
                parent_snapshot_id=base_snapshot.id,
            )
            next_workspace = replace(next_workspace, base_snapshot_id=snapshot.id)
            self._snapshots[snapshot.id] = snapshot
            self._heads[current.id] = snapshot.id
            self._workspaces[current.id] = next_workspace
        return snapshot

    async def release_materialization(
        self,
        materialization_id: str,
        outcome: MaterializationOutcome,
    ) -> None:
        del outcome
        materialization = await self._get_materialization(materialization_id)
        path = self.local_path(materialization_id)
        try:
            self._make_writable(path)
            shutil.rmtree(path)
        except OSError as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                f"failed to clean workspace materialization: {materialization_id}",
            ) from exc
        async with self._lock:
            self._materializations.pop(materialization_id, None)
            self._materialization_paths.pop(materialization_id, None)
            workspace = self._workspaces[materialization.workspace_id]
            remaining = tuple(self._materializations.values())
            task_ids = tuple(
                task_id
                for task_id in workspace.active_task_ids
                if any(item.task_id == task_id for item in remaining)
            )
            run_ids = tuple(
                run_id
                for run_id in workspace.active_run_ids
                if any(item.run_id == run_id for item in remaining)
            )
            self._workspaces[workspace.id] = replace(
                workspace,
                active_task_ids=task_ids,
                active_run_ids=run_ids,
                last_used_at=utc_now(),
            )

    async def cleanup(self) -> CleanupReport:
        async with self._lock:
            known = dict(self._materialization_paths)
        removed: list[str] = []
        missing: list[str] = []
        failed: list[str] = []
        for materialization_id, path in known.items():
            if not path.exists():
                missing.append(materialization_id)
        for path in self._root.iterdir():
            if not path.name.startswith("materialization_"):
                continue
            if path.name in known:
                continue
            try:
                self._make_writable(path)
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
                validate_id(path.name, "materialization")
                removed.append(path.name)
            except (OSError, ValueError):
                try:
                    validate_id(path.name, "materialization")
                except ValueError:
                    continue
                failed.append(path.name)
        return CleanupReport(
            removed_materialization_ids=tuple(sorted(removed)),
            missing_materialization_ids=tuple(sorted(missing)),
            failed_materialization_ids=tuple(sorted(failed)),
        )

    async def _get_materialization(self, materialization_id: str) -> WorkspaceMaterialization:
        validate_id(materialization_id, "materialization")
        async with self._lock:
            materialization = self._materializations.get(materialization_id)
        if materialization is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"workspace materialization not found: {materialization_id}",
            )
        return materialization

    async def _verify_files(
        self,
        files: tuple[WorkspaceFile, ...],
        context: DataAccessContext,
    ) -> None:
        paths: set[str] = set()
        for entry in files:
            if entry.relative_path in paths:
                raise ContractError(ErrorCode.INVALID_REQUEST, "duplicate workspace file path")
            paths.add(entry.relative_path)
            record = await self._files.get_file(entry.file_id, context)
            if record.sha256 != entry.sha256:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    f"workspace file checksum mismatch: {entry.relative_path}",
                )

    async def _read_file(self, file_id: str, context: DataAccessContext) -> bytes:
        chunks: list[bytes] = []
        async for chunk in self._files.stream_file(file_id, context):
            chunks.append(chunk)
        return b"".join(chunks)

    def _snapshot_for(
        self,
        workspace: Workspace,
        files: tuple[WorkspaceFile, ...],
        *,
        parent_snapshot_id: str | None,
    ) -> WorkspaceSnapshot:
        return WorkspaceSnapshot(
            workspace_id=workspace.id,
            revision=workspace.revision,
            files=tuple(sorted(files, key=lambda entry: entry.relative_path)),
            content_checksum=self._snapshot_checksum(files),
            source_refs=workspace.source_refs,
            parent_snapshot_id=parent_snapshot_id,
        )

    @staticmethod
    def _snapshot_checksum(files: tuple[WorkspaceFile, ...]) -> str:
        digest = hashlib.sha256()
        for entry in sorted(files, key=lambda item: item.relative_path):
            digest.update(entry.relative_path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(entry.file_id.encode("ascii"))
            digest.update(b"\0")
            digest.update(entry.sha256.encode("ascii"))
            digest.update(b"\n")
        return digest.hexdigest()

    @staticmethod
    def _safe_target(root: Path, relative_path: str) -> Path:
        validate_relative_path(relative_path)
        target = (root / relative_path).resolve(strict=False)
        resolved_root = root.resolve()
        if target == resolved_root or resolved_root not in target.parents:
            raise ContractError(ErrorCode.INVALID_REQUEST, "workspace path escapes materialization")
        return target

    @staticmethod
    def _reject_symlinks(root: Path) -> None:
        if root.is_symlink():
            raise ContractError(ErrorCode.FORBIDDEN, "workspace materialization root is a symlink")
        for current_root, dirnames, filenames in os.walk(root, followlinks=False):
            base = Path(current_root)
            for name in (*dirnames, *filenames):
                if (base / name).is_symlink():
                    raise ContractError(ErrorCode.FORBIDDEN, "workspace symlinks are not permitted")

    @classmethod
    def _scan_files(cls, root: Path) -> dict[str, tuple[bytes, str]]:
        if not root.exists() or not root.is_dir():
            raise ContractError(ErrorCode.NOT_FOUND, "workspace materialization is missing")
        cls._reject_symlinks(root)
        files: dict[str, tuple[bytes, str]] = {}
        for current_root, _, filenames in os.walk(root, followlinks=False):
            base = Path(current_root)
            for name in filenames:
                path = base / name
                relative = path.relative_to(root).as_posix()
                validate_relative_path(relative)
                try:
                    data = path.read_bytes()
                except OSError as exc:
                    raise ContractError(
                        ErrorCode.BACKEND_ERROR,
                        f"failed to read materialized workspace file: {relative}",
                    ) from exc
                files[relative] = (data, hashlib.sha256(data).hexdigest())
        return files

    @staticmethod
    def _make_read_only(root: Path) -> None:
        for current_root, dirnames, filenames in os.walk(root, topdown=False):
            base = Path(current_root)
            for name in filenames:
                (base / name).chmod(0o444)
            for name in dirnames:
                (base / name).chmod(0o555)
        root.chmod(0o555)

    @staticmethod
    def _make_writable(root: Path) -> None:
        if not root.exists() or root.is_symlink():
            return
        for current_root, dirnames, filenames in os.walk(root, topdown=False, followlinks=False):
            base = Path(current_root)
            for name in filenames:
                path = base / name
                if not path.is_symlink():
                    path.chmod(0o600)
            for name in dirnames:
                path = base / name
                if not path.is_symlink():
                    path.chmod(0o700)
        root.chmod(0o700)


def _with_optional(values: tuple[str, ...], value: str | None) -> tuple[str, ...]:
    if value is None or value in values:
        return values
    return (*values, value)
