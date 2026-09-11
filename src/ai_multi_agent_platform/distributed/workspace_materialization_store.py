"""Worker-local filesystem materialization for distributed remote Workspaces.

Canonical Workspace, Snapshot and File identity remains outside this module. The store owns
only Worker-local transfer state, filesystem safety, cache/restart validation and cleanup;
local paths never become canonical transport identifiers.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.workspaces import (
    MaterializationOutcome,
    RemoteCleanupAcknowledgement,
    RemoteMaterializationReceipt,
    RemoteMaterializationRequest,
    WorkspaceAccessMode,
    WorkspaceChangeKind,
    validate_relative_path,
)

from .registry import RegistryError
from .workspace_transport_codec import (
    _array,
    _canonical_snapshot_checksum,
    _decode_request,
    _encode_request,
    _entry_token,
    _ManifestEntry,
    _mapping,
    _materialization_ref,
    _receipt,
    _required,
    _tree_content_checksum,
    _validate_opaque,
)


@dataclass(slots=True)
class _IncomingTransfer:
    request: RemoteMaterializationRequest
    materialization_ref: str
    manifest: tuple[_ManifestEntry, ...]
    chunk_bytes: int
    incoming_root: Path


@dataclass(frozen=True, slots=True)
class _RawWorkspaceChange:
    relative_path: str
    kind: WorkspaceChangeKind
    sha256: str | None = None
    data: bytes | None = None


class WorkerWorkspaceMaterializationStore:
    """Worker-local isolated materialization/cache state.

    The deterministic local path is ``root/workspace_id/snapshot_id``. This path is
    never serialized. A separate hidden state directory records only portable manifest
    metadata so an interrupted transfer can be restarted safely.
    """

    def __init__(self, worker_id: str, root: str | Path) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be blank")
        self.worker_id = worker_id
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._state_root = self.root / ".remote-workspace-state"
        self._incoming_root = self.root / ".remote-workspace-incoming"
        self._state_root.mkdir(parents=True, exist_ok=True)
        self._incoming_root.mkdir(parents=True, exist_ok=True)
        self._transfers: dict[str, _IncomingTransfer] = {}
        self._lock = asyncio.Lock()

    async def prepare(
        self,
        request: RemoteMaterializationRequest,
        manifest: tuple[_ManifestEntry, ...],
        *,
        chunk_bytes: int,
    ) -> RemoteMaterializationReceipt | str:
        if chunk_bytes < 1024:
            raise RegistryError("workspace transfer chunk_bytes must be at least 1024")
        observed = _canonical_snapshot_checksum(manifest)
        if observed != request.expected_checksum:
            raise RegistryError("workspace transfer manifest checksum does not match snapshot")
        materialization_ref = _materialization_ref(self.worker_id, request)
        final_root = self._final_root(request.workspace_id, request.snapshot_id)
        if await asyncio.to_thread(self._completed_matches, request, manifest, final_root):
            return _receipt(
                self.worker_id,
                request,
                materialization_ref,
                cache_hit=True,
            )

        incoming_root = self._incoming_root / materialization_ref
        async with self._lock:
            existing = self._transfers.get(materialization_ref)
            if existing is not None:
                if (
                    existing.request != request
                    or existing.manifest != manifest
                    or existing.chunk_bytes != chunk_bytes
                ):
                    raise RegistryError(
                        "duplicate materialization_ref carries different transfer metadata"
                    )
                return materialization_ref
            await asyncio.to_thread(self._reset_directory, incoming_root)
            transfer = _IncomingTransfer(
                request=request,
                materialization_ref=materialization_ref,
                manifest=manifest,
                chunk_bytes=chunk_bytes,
                incoming_root=incoming_root,
            )
            self._transfers[materialization_ref] = transfer
        return materialization_ref

    async def put_chunk(
        self,
        materialization_ref: str,
        relative_path: str,
        *,
        chunk_index: int,
        total_chunks: int,
        data: bytes,
    ) -> None:
        transfer = self._transfer(materialization_ref)
        entry = self._entry(transfer, relative_path)
        expected_chunks = max(
            1, (entry.size_bytes + transfer.chunk_bytes - 1) // transfer.chunk_bytes
        )
        if total_chunks != expected_chunks:
            raise RegistryError("workspace transfer chunk count does not match file size")
        if not 0 <= chunk_index < total_chunks:
            raise RegistryError("workspace transfer chunk index is out of range")
        if len(data) > transfer.chunk_bytes:
            raise RegistryError("workspace transfer chunk exceeds configured size")
        if chunk_index < total_chunks - 1 and len(data) != transfer.chunk_bytes:
            raise RegistryError("non-terminal workspace transfer chunk has invalid size")
        chunk_root = transfer.incoming_root / "chunks" / _entry_token(entry)
        chunk_root.mkdir(parents=True, exist_ok=True)
        destination = chunk_root / f"{chunk_index:08d}.chunk"
        if destination.exists():
            existing = await asyncio.to_thread(destination.read_bytes)
            if existing != data:
                raise RegistryError("duplicate workspace chunk carries different bytes")
            return
        await asyncio.to_thread(destination.write_bytes, data)

    async def commit(self, materialization_ref: str) -> RemoteMaterializationReceipt:
        try:
            transfer = self._transfer(materialization_ref)
        except RegistryError:
            return await asyncio.to_thread(self._completed_receipt, materialization_ref)
        files_root = transfer.incoming_root / "files"
        await asyncio.to_thread(self._reset_directory, files_root)
        for entry in transfer.manifest:
            destination = self._safe_target(files_root, entry.relative_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            total_chunks = max(
                1,
                (entry.size_bytes + transfer.chunk_bytes - 1) // transfer.chunk_bytes,
            )
            data = bytearray()
            chunk_root = transfer.incoming_root / "chunks" / _entry_token(entry)
            for index in range(total_chunks):
                chunk = chunk_root / f"{index:08d}.chunk"
                if not chunk.exists():
                    raise RegistryError(
                        f"workspace transfer is missing a chunk for {entry.relative_path}"
                    )
                data.extend(await asyncio.to_thread(chunk.read_bytes))
            raw = bytes(data)
            if len(raw) != entry.size_bytes:
                raise RegistryError(f"workspace transfer size mismatch for {entry.relative_path}")
            if hashlib.sha256(raw).hexdigest() != entry.sha256:
                raise RegistryError(
                    f"workspace transfer checksum mismatch for {entry.relative_path}"
                )
            await asyncio.to_thread(destination.write_bytes, raw)

        self._reject_symlinks(files_root)
        observed = _canonical_snapshot_checksum(transfer.manifest)
        if observed != transfer.request.expected_checksum:
            raise RegistryError("materialized Workspace snapshot checksum mismatch")
        final_root = self._final_root(
            transfer.request.workspace_id,
            transfer.request.snapshot_id,
        )
        await asyncio.to_thread(self._install_files, files_root, final_root)
        if transfer.request.access_mode is WorkspaceAccessMode.READ_ONLY:
            await asyncio.to_thread(self._make_read_only, final_root)
        await asyncio.to_thread(self._write_state, transfer)
        await asyncio.to_thread(shutil.rmtree, transfer.incoming_root, True)
        async with self._lock:
            self._transfers.pop(materialization_ref, None)
        return _receipt(
            self.worker_id,
            transfer.request,
            materialization_ref,
            cache_hit=False,
        )

    def execution_workspace(self, workspace_id: str, snapshot_id: str) -> str:
        final_root = self._final_root(workspace_id, snapshot_id)
        if not final_root.is_dir():
            raise RegistryError("Worker Workspace snapshot has not been materialized")
        self._reject_symlinks(final_root)
        return f"{workspace_id}/{snapshot_id}"

    async def result_manifest(
        self,
        receipt: RemoteMaterializationReceipt,
    ) -> tuple[str, tuple[_RawWorkspaceChange, ...]]:
        request, manifest = await asyncio.to_thread(self._read_state, receipt.materialization_ref)
        self._validate_receipt_binding(receipt, request)
        root = self._final_root(receipt.workspace_id, receipt.snapshot_id)
        current = await asyncio.to_thread(self._scan_files, root)
        base = {entry.relative_path: entry for entry in manifest}
        changed_paths = {
            path
            for path, (_, digest) in current.items()
            if path not in base or base[path].sha256 != digest
        }
        deleted_paths = set(base) - set(current)
        if receipt.access_mode is WorkspaceAccessMode.READ_ONLY and (
            changed_paths or deleted_paths
        ):
            raise ContractError(ErrorCode.FORBIDDEN, "read-only remote Workspace was modified")
        changes: list[_RawWorkspaceChange] = []
        for relative_path in sorted(changed_paths):
            data, digest = current[relative_path]
            changes.append(
                _RawWorkspaceChange(
                    relative_path=relative_path,
                    kind=(
                        WorkspaceChangeKind.CREATED
                        if relative_path not in base
                        else WorkspaceChangeKind.MODIFIED
                    ),
                    sha256=digest,
                    data=data,
                )
            )
        for relative_path in sorted(deleted_paths):
            changes.append(
                _RawWorkspaceChange(
                    relative_path=relative_path,
                    kind=WorkspaceChangeKind.DELETED,
                )
            )
        return _tree_content_checksum(current), tuple(changes)

    async def result_chunk(
        self,
        receipt: RemoteMaterializationReceipt,
        relative_path: str,
        *,
        chunk_index: int,
        chunk_bytes: int,
    ) -> tuple[bytes, int]:
        if chunk_bytes < 1024:
            raise RegistryError("workspace result chunk_bytes must be at least 1024")
        _request, _manifest = await asyncio.to_thread(self._read_state, receipt.materialization_ref)
        root = self._final_root(receipt.workspace_id, receipt.snapshot_id)
        target = self._safe_target(root, relative_path)
        if not target.is_file() or target.is_symlink():
            raise RegistryError("workspace result file is unavailable")
        data = await asyncio.to_thread(target.read_bytes)
        total_chunks = max(1, (len(data) + chunk_bytes - 1) // chunk_bytes)
        if not 0 <= chunk_index < total_chunks:
            raise RegistryError("workspace result chunk index is out of range")
        start = chunk_index * chunk_bytes
        return data[start : start + chunk_bytes], total_chunks

    async def cleanup(
        self,
        receipt: RemoteMaterializationReceipt,
        outcome: MaterializationOutcome,
    ) -> RemoteCleanupAcknowledgement:
        try:
            request, _manifest = await asyncio.to_thread(
                self._read_state, receipt.materialization_ref
            )
            self._validate_receipt_binding(receipt, request)
        except RegistryError as exc:
            if "state not found" not in str(exc):
                raise
        root = self._final_root(receipt.workspace_id, receipt.snapshot_id)
        try:
            await asyncio.to_thread(self._remove_materialization, root)
            state_path = self._state_path(receipt.materialization_ref)
            await asyncio.to_thread(state_path.unlink, missing_ok=True)
        except OSError:
            return RemoteCleanupAcknowledgement(
                workspace_id=receipt.workspace_id,
                snapshot_id=receipt.snapshot_id,
                materialization_ref=receipt.materialization_ref,
                outcome=outcome,
                succeeded=False,
                error_code="workspace_cleanup_failed",
            )
        return RemoteCleanupAcknowledgement(
            workspace_id=receipt.workspace_id,
            snapshot_id=receipt.snapshot_id,
            materialization_ref=receipt.materialization_ref,
            outcome=outcome,
            succeeded=True,
        )

    def _transfer(self, materialization_ref: str) -> _IncomingTransfer:
        transfer = self._transfers.get(materialization_ref)
        if transfer is None:
            raise RegistryError("unknown remote Workspace transfer")
        return transfer

    def _completed_receipt(self, materialization_ref: str) -> RemoteMaterializationReceipt:
        request, manifest = self._read_state(materialization_ref)
        if _materialization_ref(self.worker_id, request) != materialization_ref:
            raise RegistryError("completed remote Workspace materialization reference mismatch")
        final_root = self._final_root(request.workspace_id, request.snapshot_id)
        if not self._completed_matches(request, manifest, final_root):
            raise RegistryError("completed remote Workspace materialization failed validation")
        return _receipt(
            self.worker_id,
            request,
            materialization_ref,
            cache_hit=True,
        )

    @staticmethod
    def _entry(transfer: _IncomingTransfer, relative_path: str) -> _ManifestEntry:
        validate_relative_path(relative_path)
        for entry in transfer.manifest:
            if entry.relative_path == relative_path:
                return entry
        raise RegistryError("workspace transfer chunk references an unknown file")

    def _final_root(self, workspace_id: str, snapshot_id: str) -> Path:
        root = self.root / workspace_id / snapshot_id
        resolved = root.resolve(strict=False)
        if self.root != resolved and self.root not in resolved.parents:
            raise RegistryError("Worker Workspace path escapes configured local root")
        return resolved

    def _state_path(self, materialization_ref: str) -> Path:
        _validate_opaque(materialization_ref, "materialization_ref")
        return self._state_root / f"{materialization_ref}.json"

    def _write_state(self, transfer: _IncomingTransfer) -> None:
        document = {
            "request": _encode_request(transfer.request),
            "manifest": [entry.to_json() for entry in transfer.manifest],
        }
        path = self._state_path(transfer.materialization_ref)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
        temporary.replace(path)

    def _read_state(
        self, materialization_ref: str
    ) -> tuple[RemoteMaterializationRequest, tuple[_ManifestEntry, ...]]:
        path = self._state_path(materialization_ref)
        if not path.is_file():
            raise RegistryError("remote Workspace materialization state not found")
        try:
            raw: object = json.loads(path.read_text(encoding="utf-8"))
            data = _mapping(raw, "remote Workspace state")
            request = _decode_request(_required(data, "request"))
            manifest = tuple(
                _ManifestEntry.from_json(item)
                for item in _array(_required(data, "manifest"), "manifest")
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise RegistryError("invalid remote Workspace materialization state") from exc
        return request, manifest

    def _completed_matches(
        self,
        request: RemoteMaterializationRequest,
        manifest: tuple[_ManifestEntry, ...],
        final_root: Path,
    ) -> bool:
        materialization_ref = _materialization_ref(self.worker_id, request)
        try:
            stored_request, stored_manifest = self._read_state(materialization_ref)
            if stored_request != request or stored_manifest != manifest:
                return False
            current = self._scan_files(final_root)
        except (RegistryError, ContractError):
            return False
        if set(current) != {entry.relative_path for entry in manifest}:
            return False
        return all(current[entry.relative_path][1] == entry.sha256 for entry in manifest)

    @staticmethod
    def _validate_receipt_binding(
        receipt: RemoteMaterializationReceipt,
        request: RemoteMaterializationRequest,
    ) -> None:
        if (
            receipt.workspace_id != request.workspace_id
            or receipt.snapshot_id != request.snapshot_id
            or receipt.expected_checksum != request.expected_checksum
            or receipt.access_mode is not request.access_mode
        ):
            raise RegistryError("remote Workspace receipt does not match stored materialization")

    @staticmethod
    def _safe_target(root: Path, relative_path: str) -> Path:
        validate_relative_path(relative_path)
        target = (root / relative_path).resolve(strict=False)
        resolved_root = root.resolve(strict=False)
        if target == resolved_root or resolved_root not in target.parents:
            raise RegistryError("remote Workspace path escapes materialization root")
        return target

    @staticmethod
    def _reject_symlinks(root: Path) -> None:
        if root.is_symlink():
            raise ContractError(ErrorCode.FORBIDDEN, "remote Workspace root is a symlink")
        if not root.exists():
            return
        for current_root, dirnames, filenames in os.walk(root, followlinks=False):
            base = Path(current_root)
            for name in (*dirnames, *filenames):
                if (base / name).is_symlink():
                    raise ContractError(
                        ErrorCode.FORBIDDEN,
                        "remote Workspace symlinks are not permitted",
                    )

    @classmethod
    def _scan_files(cls, root: Path) -> dict[str, tuple[bytes, str]]:
        if not root.is_dir():
            raise RegistryError("remote Workspace materialization is missing")
        cls._reject_symlinks(root)
        files: dict[str, tuple[bytes, str]] = {}
        for current_root, _, filenames in os.walk(root, followlinks=False):
            base = Path(current_root)
            for name in filenames:
                path = base / name
                relative = path.relative_to(root).as_posix()
                validate_relative_path(relative)
                data = path.read_bytes()
                files[relative] = (data, hashlib.sha256(data).hexdigest())
        return files

    @classmethod
    def _install_files(cls, files_root: Path, final_root: Path) -> None:
        if final_root.exists():
            cls._make_writable(final_root)
            shutil.rmtree(final_root)
        final_root.parent.mkdir(parents=True, exist_ok=True)
        files_root.replace(final_root)
        cls._reject_symlinks(final_root)

    @staticmethod
    def _reset_directory(path: Path) -> None:
        if path.exists():
            WorkerWorkspaceMaterializationStore._make_writable(path)
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=False)

    @staticmethod
    def _remove_materialization(path: Path) -> None:
        if not path.exists():
            return
        WorkerWorkspaceMaterializationStore._make_writable(path)
        shutil.rmtree(path)
        parent = path.parent
        if parent.is_dir() and parent != path and not any(parent.iterdir()):
            parent.rmdir()

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


__all__ = ["WorkerWorkspaceMaterializationStore"]
