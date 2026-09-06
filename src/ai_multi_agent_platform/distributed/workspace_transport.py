"""#35-backed concrete remote Workspace materialization for distributed Workers.

The transport adapter implements the existing #37 ``RemoteWorkspaceMaterializer``
contract. Canonical Workspace, Snapshot and File identity stays on the Control Plane;
Worker-local filesystem paths are deployment details and never cross the wire.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    ExecutionHandle,
    ExecutionSnapshot,
    LifecycleBackend,
    OperationControl,
    RetryMode,
)
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.data import DataAccessContext, FileProvider, FileRecord
from ai_multi_agent_platform.messaging import (
    MessageKind,
    MessageTransport,
    Subscription,
    TransportEnvelope,
)
from ai_multi_agent_platform.portability.file_codecs import snapshot_file
from ai_multi_agent_platform.security import redact_exception
from ai_multi_agent_platform.workspaces import (
    MaterializationOutcome,
    RemoteCleanupAcknowledgement,
    RemoteMaterializationReceipt,
    RemoteMaterializationRequest,
    RemoteMaterializationResult,
    RemoteWorkspaceMaterializer,
    Workspace,
    WorkspaceAccessMode,
    WorkspaceChange,
    WorkspaceChangeKind,
    WorkspaceProvider,
    validate_relative_path,
)

from .models import JobResultStatus, WorkerJobRequest, WorkerJobResult
from .registry import RegistryError
from .worker import LocalWorker, WorkerDispatcher

WORKSPACE_TRANSPORT_SCHEMA_VERSION = "1"
WORKSPACE_COMMAND_TOPIC_PREFIX = "distributed.worker.workspace.commands"
WORKSPACE_REPLY_TOPIC_PREFIX = "distributed.worker.workspace.replies"
DEFAULT_WORKSPACE_CHUNK_BYTES = 128 * 1024


def worker_workspace_command_topic(worker_id: str) -> str:
    if not worker_id.strip():
        raise ValueError("worker_id must not be blank")
    return f"{WORKSPACE_COMMAND_TOPIC_PREFIX}.{worker_id}"


class WorkspaceDataContextResolver(Protocol):
    """Resolve one authorized FileProvider context for a canonical Workspace."""

    def __call__(self, workspace: Workspace) -> DataAccessContext: ...


class WorkspaceLifecycleFactory(Protocol):
    """Build a lifecycle backend bound to one Worker-local execution workspace token."""

    def __call__(self, execution_workspace: str) -> LifecycleBackend: ...


@dataclass(frozen=True, slots=True)
class _ManifestEntry:
    relative_path: str
    file_id: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        validate_relative_path(self.relative_path)
        if not self.file_id.strip():
            raise ValueError("manifest file_id must not be blank")
        _validate_sha256(self.sha256)
        if self.size_bytes < 0:
            raise ValueError("manifest size_bytes must not be negative")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "relative_path": self.relative_path,
            "file_id": self.file_id,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_json(cls, value: object) -> _ManifestEntry:
        data = _mapping(value, "workspace manifest entry")
        return cls(
            relative_path=_required_string(data, "relative_path"),
            file_id=_required_string(data, "file_id"),
            sha256=_required_string(data, "sha256"),
            size_bytes=_required_integer(data, "size_bytes", minimum=0),
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


class TransportRemoteWorkspaceMaterializer(RemoteWorkspaceMaterializer):
    """Control-side concrete #37 materializer implemented through #35 transport."""

    def __init__(
        self,
        worker_id: str,
        transport: MessageTransport,
        workspaces: WorkspaceProvider,
        files: FileProvider,
        context_resolver: WorkspaceDataContextResolver,
        *,
        client_id: str = "distributed-workspace-control-plane",
        chunk_bytes: int = DEFAULT_WORKSPACE_CHUNK_BYTES,
        response_timeout_seconds: float = 30.0,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be blank")
        if not client_id.strip():
            raise ValueError("workspace transport client_id must not be blank")
        if chunk_bytes < 1024:
            raise ValueError("workspace transport chunk_bytes must be at least 1024")
        if response_timeout_seconds <= 0:
            raise ValueError("response_timeout_seconds must be greater than zero")
        self.worker_id = worker_id
        self.transport = transport
        self.workspaces = workspaces
        self.files = files
        self.context_resolver = context_resolver
        self.client_id = client_id
        self.chunk_bytes = chunk_bytes
        self.response_timeout_seconds = response_timeout_seconds
        self._results: dict[str, RemoteMaterializationResult] = {}

    async def materialize(
        self,
        request: RemoteMaterializationRequest,
    ) -> RemoteMaterializationReceipt:
        workspace = await self.workspaces.get_workspace(request.workspace_id)
        snapshot = await self.workspaces.get_snapshot(request.snapshot_id)
        if snapshot.workspace_id != workspace.id:
            raise RegistryError("remote Workspace snapshot belongs to another Workspace")
        if snapshot.content_checksum != request.expected_checksum:
            raise RegistryError("remote Workspace request checksum differs from canonical snapshot")
        context = self._context(workspace)
        manifest: list[_ManifestEntry] = []
        portable_files: dict[str, bytes] = {}
        for workspace_file in snapshot.files:
            portable = await snapshot_file(self.files, workspace_file.file_id, context)
            if portable.record.sha256 != workspace_file.sha256:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "canonical Workspace File checksum differs from snapshot manifest",
                    details={"file_id": workspace_file.file_id},
                )
            manifest.append(
                _ManifestEntry(
                    relative_path=workspace_file.relative_path,
                    file_id=workspace_file.file_id,
                    sha256=workspace_file.sha256,
                    size_bytes=len(portable.data),
                )
            )
            portable_files[workspace_file.relative_path] = portable.data

        prepared = await self._request(
            request,
            operation="prepare",
            payload={
                "request": _encode_request(request),
                "manifest": [entry.to_json() for entry in manifest],
                "chunk_bytes": self.chunk_bytes,
            },
            idempotency_suffix="prepare",
        )
        receipt_raw = prepared.get("receipt")
        if receipt_raw is not None:
            return _decode_receipt(receipt_raw)
        materialization_ref = _required_string(prepared, "materialization_ref")
        for entry in manifest:
            data = portable_files[entry.relative_path]
            total_chunks = max(1, (len(data) + self.chunk_bytes - 1) // self.chunk_bytes)
            for index in range(total_chunks):
                chunk = data[index * self.chunk_bytes : (index + 1) * self.chunk_bytes]
                await self._request(
                    request,
                    operation="put_chunk",
                    payload={
                        "materialization_ref": materialization_ref,
                        "relative_path": entry.relative_path,
                        "chunk_index": index,
                        "total_chunks": total_chunks,
                        "data_base64": base64.b64encode(chunk).decode("ascii"),
                    },
                    idempotency_suffix=(
                        f"chunk:{entry.file_id}:{index}:{hashlib.sha256(chunk).hexdigest()}"
                    ),
                )
        committed = await self._request(
            request,
            operation="commit",
            payload={"materialization_ref": materialization_ref},
            idempotency_suffix="commit",
        )
        return _decode_receipt(_required(committed, "receipt"))

    async def collect_result(
        self,
        receipt: RemoteMaterializationReceipt,
    ) -> RemoteMaterializationResult:
        existing = self._results.get(receipt.materialization_ref)
        if existing is not None:
            return existing
        workspace = await self.workspaces.get_workspace(receipt.workspace_id)
        context = self._context(workspace)
        manifest_reply = await self._request_for_receipt(
            receipt,
            operation="result_manifest",
            payload={"receipt": _encode_receipt(receipt)},
            idempotency_suffix="result-manifest",
        )
        content_checksum = _required_string(manifest_reply, "content_checksum")
        _validate_sha256(content_checksum)
        changes_raw = _array(_required(manifest_reply, "changes"), "changes")
        changes: list[WorkspaceChange] = []
        for raw in changes_raw:
            change = _mapping(raw, "remote Workspace change")
            relative_path = _required_string(change, "relative_path")
            kind = WorkspaceChangeKind(_required_string(change, "kind"))
            if kind is WorkspaceChangeKind.DELETED:
                changes.append(WorkspaceChange(relative_path=relative_path, kind=kind))
                continue
            sha256 = _required_string(change, "sha256")
            size_bytes = _required_integer(change, "size_bytes", minimum=0)
            data = bytearray()
            total_chunks = max(1, (size_bytes + self.chunk_bytes - 1) // self.chunk_bytes)
            for index in range(total_chunks):
                chunk_reply = await self._request_for_receipt(
                    receipt,
                    operation="result_chunk",
                    payload={
                        "receipt": _encode_receipt(receipt),
                        "relative_path": relative_path,
                        "chunk_index": index,
                        "chunk_bytes": self.chunk_bytes,
                    },
                    idempotency_suffix=f"result-chunk:{relative_path}:{index}",
                )
                data.extend(_required_base64(chunk_reply, "data_base64"))
            raw_data = bytes(data)
            if len(raw_data) != size_bytes or hashlib.sha256(raw_data).hexdigest() != sha256:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "remote Workspace result bytes failed checksum verification",
                )
            file_id = _deterministic_result_file_id(receipt, relative_path, sha256)
            record = await _create_or_reuse_file(
                self.files,
                file_id,
                raw_data,
                context,
                metadata={
                    "workspace_id": receipt.workspace_id,
                    "workspace_snapshot_id": receipt.snapshot_id,
                    "remote_materialization_ref": receipt.materialization_ref,
                    "relative_path": relative_path,
                },
            )
            changes.append(
                WorkspaceChange(
                    relative_path=relative_path,
                    kind=kind,
                    file_id=record.file_id,
                    sha256=record.sha256,
                )
            )
        result = RemoteMaterializationResult(
            workspace_id=receipt.workspace_id,
            snapshot_id=receipt.snapshot_id,
            materialization_ref=receipt.materialization_ref,
            content_checksum=content_checksum,
            changes=tuple(changes),
        )
        self._results[receipt.materialization_ref] = result
        return result

    async def cleanup(
        self,
        receipt: RemoteMaterializationReceipt,
        outcome: MaterializationOutcome,
    ) -> RemoteCleanupAcknowledgement:
        reply = await self._request_for_receipt(
            receipt,
            operation="cleanup",
            payload={
                "receipt": _encode_receipt(receipt),
                "outcome": outcome.value,
            },
            idempotency_suffix=f"cleanup:{outcome.value}",
        )
        return _decode_cleanup(_required(reply, "cleanup"))

    def _context(self, workspace: Workspace) -> DataAccessContext:
        context = self.context_resolver(workspace)
        if context.project_id != workspace.project_id:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "remote Workspace data context project does not match Workspace project",
            )
        return context

    async def _request(
        self,
        request: RemoteMaterializationRequest,
        *,
        operation: str,
        payload: dict[str, JsonValue],
        idempotency_suffix: str,
    ) -> Mapping[str, object]:
        return await self._send_request(
            correlation_id=f"workspace:{request.workspace_id}:{request.snapshot_id}",
            operation=operation,
            payload=payload,
            idempotency_key=(
                f"workspace:{self.worker_id}:{request.workspace_id}:{request.snapshot_id}:"
                f"{idempotency_suffix}"
            ),
        )

    async def _request_for_receipt(
        self,
        receipt: RemoteMaterializationReceipt,
        *,
        operation: str,
        payload: dict[str, JsonValue],
        idempotency_suffix: str,
    ) -> Mapping[str, object]:
        return await self._send_request(
            correlation_id=f"workspace:{receipt.workspace_id}:{receipt.snapshot_id}",
            operation=operation,
            payload=payload,
            idempotency_key=(
                f"workspace:{self.worker_id}:{receipt.materialization_ref}:{idempotency_suffix}"
            ),
        )

    async def _send_request(
        self,
        *,
        correlation_id: str,
        operation: str,
        payload: dict[str, JsonValue],
        idempotency_key: str,
    ) -> Mapping[str, object]:
        command_payload: dict[str, JsonValue] = {
            "worker_id": self.worker_id,
            "operation": operation,
            **payload,
        }
        command = TransportEnvelope(
            message_type=f"workspace.{operation}",
            kind=MessageKind.COMMAND,
            payload_schema_version=WORKSPACE_TRANSPORT_SCHEMA_VERSION,
            source_component=self.client_id,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            payload=command_payload,
        )
        reply_topic = f"{WORKSPACE_REPLY_TOPIC_PREFIX}.{command.message_id}"
        command_payload["reply_topic"] = reply_topic
        command = TransportEnvelope(
            message_id=command.message_id,
            message_type=command.message_type,
            kind=command.kind,
            payload_schema_version=command.payload_schema_version,
            source_component=command.source_component,
            correlation_id=command.correlation_id,
            idempotency_key=command.idempotency_key,
            payload=command_payload,
        )
        subscription = self.transport.subscribe(
            Subscription(
                topic=reply_topic,
                consumer_id=f"{self.client_id}:{command.message_id}",
                consumer_group=f"workspace-request:{command.message_id}",
            )
        )
        control = OperationControl(
            timeout_seconds=self.response_timeout_seconds,
            idempotency_key=idempotency_key,
            retry_mode=RetryMode.IDEMPOTENT,
        )
        try:
            await self.transport.publish(
                worker_workspace_command_topic(self.worker_id),
                command,
                control=control,
            )
            try:
                async with asyncio.timeout(self.response_timeout_seconds):
                    delivery = await anext(subscription)
            except TimeoutError as exc:
                raise RegistryError("remote Workspace transport response timed out") from exc
            await self.transport.ack(delivery)
            reply = delivery.envelope
            if reply.causation_id != command.message_id:
                raise RegistryError("remote Workspace reply causation mismatch")
            if reply.correlation_id != command.correlation_id:
                raise RegistryError("remote Workspace reply correlation mismatch")
            data = _mapping(reply.payload, "remote Workspace reply")
            if _required_string(data, "worker_id") != self.worker_id:
                raise RegistryError("remote Workspace reply came from another Worker")
            if reply.message_type == "workspace.error":
                raise RegistryError(_required_string(data, "message"))
            return data
        finally:
            await subscription.aclose()


class WorkerWorkspaceTransportEndpoint:
    """Worker-side consumer for concrete remote Workspace transfer operations."""

    def __init__(
        self,
        store: WorkerWorkspaceMaterializationStore,
        transport: MessageTransport,
        *,
        consumer_id: str | None = None,
    ) -> None:
        self.store = store
        self.transport = transport
        self.consumer_id = consumer_id or f"workspace-endpoint:{store.worker_id}"

    async def serve(self) -> None:
        subscription = self.transport.subscribe(
            Subscription(
                topic=worker_workspace_command_topic(self.store.worker_id),
                consumer_id=self.consumer_id,
                consumer_group=f"workspace-worker:{self.store.worker_id}",
            )
        )
        try:
            async for delivery in subscription:
                try:
                    await self._handle(delivery.envelope)
                except Exception:
                    await self.transport.nack(
                        delivery,
                        retry=True,
                        reason="workspace_transport_reply_publish_failed",
                    )
                else:
                    await self.transport.ack(delivery)
        finally:
            await subscription.aclose()

    async def _handle(self, command: TransportEnvelope) -> None:
        data = _mapping(command.payload, "remote Workspace command")
        reply_topic = _required_string(data, "reply_topic")
        if not reply_topic.startswith(f"{WORKSPACE_REPLY_TOPIC_PREFIX}."):
            raise RegistryError("remote Workspace reply topic is outside canonical prefix")
        if _required_string(data, "worker_id") != self.store.worker_id:
            await self._error(command, reply_topic, "Worker Workspace target mismatch")
            return
        operation = _required_string(data, "operation")
        try:
            payload = await self._dispatch(operation, data)
        except Exception as exc:
            await self._error(command, reply_topic, _safe_workspace_error(exc))
            return
        await self._reply(command, reply_topic, f"workspace.{operation}.accepted", payload)

    async def _dispatch(
        self,
        operation: str,
        data: Mapping[str, object],
    ) -> dict[str, JsonValue]:
        if operation == "prepare":
            request = _decode_request(_required(data, "request"))
            manifest = tuple(
                _ManifestEntry.from_json(item)
                for item in _array(_required(data, "manifest"), "manifest")
            )
            prepared = await self.store.prepare(
                request,
                manifest,
                chunk_bytes=_required_integer(data, "chunk_bytes", minimum=1024),
            )
            if isinstance(prepared, RemoteMaterializationReceipt):
                return {
                    "worker_id": self.store.worker_id,
                    "receipt": _encode_receipt(prepared),
                }
            return {
                "worker_id": self.store.worker_id,
                "materialization_ref": prepared,
            }
        if operation == "put_chunk":
            await self.store.put_chunk(
                _required_string(data, "materialization_ref"),
                _required_string(data, "relative_path"),
                chunk_index=_required_integer(data, "chunk_index", minimum=0),
                total_chunks=_required_integer(data, "total_chunks", minimum=1),
                data=_required_base64(data, "data_base64"),
            )
            return {"worker_id": self.store.worker_id, "stored": True}
        if operation == "commit":
            receipt = await self.store.commit(_required_string(data, "materialization_ref"))
            return {
                "worker_id": self.store.worker_id,
                "receipt": _encode_receipt(receipt),
            }
        if operation == "result_manifest":
            receipt = _decode_receipt(_required(data, "receipt"))
            content_checksum, changes = await self.store.result_manifest(receipt)
            return {
                "worker_id": self.store.worker_id,
                "content_checksum": content_checksum,
                "changes": [
                    {
                        "relative_path": change.relative_path,
                        "kind": change.kind.value,
                        "sha256": change.sha256,
                        "size_bytes": 0 if change.data is None else len(change.data),
                    }
                    for change in changes
                ],
            }
        if operation == "result_chunk":
            receipt = _decode_receipt(_required(data, "receipt"))
            chunk, total_chunks = await self.store.result_chunk(
                receipt,
                _required_string(data, "relative_path"),
                chunk_index=_required_integer(data, "chunk_index", minimum=0),
                chunk_bytes=_required_integer(data, "chunk_bytes", minimum=1024),
            )
            return {
                "worker_id": self.store.worker_id,
                "data_base64": base64.b64encode(chunk).decode("ascii"),
                "total_chunks": total_chunks,
            }
        if operation == "cleanup":
            receipt = _decode_receipt(_required(data, "receipt"))
            cleanup = await self.store.cleanup(
                receipt,
                MaterializationOutcome(_required_string(data, "outcome")),
            )
            return {
                "worker_id": self.store.worker_id,
                "cleanup": _encode_cleanup(cleanup),
            }
        raise RegistryError(f"unsupported remote Workspace operation: {operation}")

    async def _reply(
        self,
        command: TransportEnvelope,
        reply_topic: str,
        message_type: str,
        payload: dict[str, JsonValue],
    ) -> None:
        await self.transport.publish(
            reply_topic,
            TransportEnvelope(
                message_type=message_type,
                kind=MessageKind.SIGNAL,
                payload_schema_version=WORKSPACE_TRANSPORT_SCHEMA_VERSION,
                source_component=f"workspace-worker:{self.store.worker_id}",
                correlation_id=command.correlation_id,
                causation_id=command.message_id,
                payload=payload,
            ),
        )

    async def _error(
        self,
        command: TransportEnvelope,
        reply_topic: str,
        message: str,
    ) -> None:
        await self._reply(
            command,
            reply_topic,
            "workspace.error",
            {
                "worker_id": self.store.worker_id,
                "message": message,
                "retryable": False,
            },
        )


class WorkspaceBoundLocalWorker:
    """Bind canonical Worker jobs to an already-materialized Worker-local Workspace.

    Jobs without Workspace references can delegate to an ordinary fallback dispatcher.
    Workspace jobs receive a lifecycle backend built for the exact local execution token
    resolved from the Worker materialization store.
    """

    def __init__(
        self,
        worker_id: str,
        store: WorkerWorkspaceMaterializationStore,
        lifecycle_factory: WorkspaceLifecycleFactory,
        *,
        fallback: WorkerDispatcher | None = None,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be blank")
        if worker_id != store.worker_id:
            raise ValueError("Workspace store belongs to a different Worker")
        self._worker_id = worker_id
        self._store = store
        self._lifecycle_factory = lifecycle_factory
        self._fallback = fallback
        self._routes: dict[str, WorkerDispatcher] = {}
        self._requests: dict[str, WorkerJobRequest] = {}

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def dispatch(self, job: WorkerJobRequest) -> ExecutionHandle:
        existing = self._requests.get(job.worker_job_id)
        if existing is not None:
            if existing != job:
                raise RegistryError("duplicate worker_job_id carries a different Workspace job")
            return await self._routes[job.worker_job_id].dispatch(job)
        if job.workspace_ref is None and job.snapshot_ref is None:
            if self._fallback is None:
                raise RegistryError("Worker job has no materialized Workspace and no fallback")
            route = self._fallback
        elif job.workspace_ref is None or job.snapshot_ref is None:
            raise RegistryError("Workspace Worker jobs require both workspace_ref and snapshot_ref")
        else:
            execution_workspace = self._store.execution_workspace(
                job.workspace_ref,
                job.snapshot_ref,
            )
            route = LocalWorker(
                self.worker_id,
                self._lifecycle_factory(execution_workspace),
            )
        handle = await route.dispatch(job)
        self._routes[job.worker_job_id] = route
        self._requests[job.worker_job_id] = job
        return handle

    async def get(self, worker_job_id: str) -> ExecutionSnapshot:
        return await self._route(worker_job_id).get(worker_job_id)

    async def cancel(self, worker_job_id: str) -> ExecutionSnapshot:
        return await self._route(worker_job_id).cancel(worker_job_id)

    async def result(self, worker_job_id: str) -> WorkerJobResult | None:
        route = self._route(worker_job_id)
        if isinstance(route, LocalWorker):
            return await route.result(worker_job_id)
        result_method = getattr(route, "result", None)
        if result_method is None:
            snapshot = await route.get(worker_job_id)
            status = _terminal_result_status(snapshot.status.value)
            if status is None:
                return None
            return WorkerJobResult(
                worker_job_id=worker_job_id,
                worker_id=self.worker_id,
                status=status,
                execution=snapshot,
            )
        result = await result_method(worker_job_id)
        return cast(WorkerJobResult | None, result)

    def _route(self, worker_job_id: str) -> WorkerDispatcher:
        try:
            return self._routes[worker_job_id]
        except KeyError as exc:
            raise RegistryError(f"Worker job is unknown locally: {worker_job_id}") from exc


def _terminal_result_status(status: str) -> JobResultStatus | None:
    return {
        "succeeded": JobResultStatus.SUCCEEDED,
        "failed": JobResultStatus.FAILED,
        "cancelled": JobResultStatus.CANCELLED,
        "timed_out": JobResultStatus.TIMED_OUT,
    }.get(status)


async def _create_or_reuse_file(
    files: FileProvider,
    file_id: str,
    data: bytes,
    context: DataAccessContext,
    *,
    metadata: dict[str, JsonValue],
) -> FileRecord:
    try:
        existing = await files.get_file(file_id, context)
    except ContractError as exc:
        if exc.code is not ErrorCode.NOT_FOUND:
            raise
    else:
        if existing.sha256 != hashlib.sha256(data).hexdigest():
            raise ContractError(
                ErrorCode.CONFLICT,
                "deterministic remote Workspace result File ID has different bytes",
            )
        return existing
    return await files.create_file(
        data,
        context,
        file_id=file_id,
        metadata=metadata,
    )


def _deterministic_result_file_id(
    receipt: RemoteMaterializationReceipt,
    relative_path: str,
    sha256: str,
) -> str:
    identity = (
        f"remote-workspace:{receipt.worker_ref}:{receipt.workspace_id}:{receipt.snapshot_id}:"
        f"{receipt.materialization_ref}:{relative_path}:{sha256}"
    )
    return f"file_{uuid5(NAMESPACE_URL, identity)}"


def _materialization_ref(worker_id: str, request: RemoteMaterializationRequest) -> str:
    digest = hashlib.sha256(
        (
            f"{worker_id}\0{request.workspace_id}\0{request.snapshot_id}\0"
            f"{request.expected_checksum}\0{request.access_mode.value}"
        ).encode()
    ).hexdigest()
    return f"remote-materialization-{digest[:32]}"


def _receipt(
    worker_id: str,
    request: RemoteMaterializationRequest,
    materialization_ref: str,
    *,
    cache_hit: bool,
) -> RemoteMaterializationReceipt:
    return RemoteMaterializationReceipt(
        workspace_id=request.workspace_id,
        snapshot_id=request.snapshot_id,
        expected_checksum=request.expected_checksum,
        observed_checksum=request.expected_checksum,
        access_mode=request.access_mode,
        worker_ref=worker_id,
        materialization_ref=materialization_ref,
        cache_hit=cache_hit,
    )


def _canonical_snapshot_checksum(entries: tuple[_ManifestEntry, ...] | list[_ManifestEntry]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda item: item.relative_path):
        digest.update(entry.relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(entry.file_id.encode("ascii"))
        digest.update(b"\0")
        digest.update(entry.sha256.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _tree_content_checksum(files: Mapping[str, tuple[bytes, str]]) -> str:
    digest = hashlib.sha256()
    for relative_path, (_data, sha256) in sorted(files.items()):
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _entry_token(entry: _ManifestEntry) -> str:
    return hashlib.sha256(entry.relative_path.encode("utf-8")).hexdigest()


def _encode_request(request: RemoteMaterializationRequest) -> dict[str, JsonValue]:
    return {
        "workspace_id": request.workspace_id,
        "snapshot_id": request.snapshot_id,
        "expected_checksum": request.expected_checksum,
        "access_mode": request.access_mode.value,
        "cache_key": request.cache_key,
    }


def _decode_request(value: object) -> RemoteMaterializationRequest:
    data = _mapping(value, "RemoteMaterializationRequest")
    return RemoteMaterializationRequest(
        workspace_id=_required_string(data, "workspace_id"),
        snapshot_id=_required_string(data, "snapshot_id"),
        expected_checksum=_required_string(data, "expected_checksum"),
        access_mode=WorkspaceAccessMode(_required_string(data, "access_mode")),
        cache_key=_required_string(data, "cache_key"),
    )


def _encode_receipt(receipt: RemoteMaterializationReceipt) -> dict[str, JsonValue]:
    return {
        "workspace_id": receipt.workspace_id,
        "snapshot_id": receipt.snapshot_id,
        "expected_checksum": receipt.expected_checksum,
        "observed_checksum": receipt.observed_checksum,
        "access_mode": receipt.access_mode.value,
        "worker_ref": receipt.worker_ref,
        "materialization_ref": receipt.materialization_ref,
        "cache_hit": receipt.cache_hit,
        "acknowledged_at": receipt.acknowledged_at.isoformat(),
    }


def _decode_receipt(value: object) -> RemoteMaterializationReceipt:
    data = _mapping(value, "RemoteMaterializationReceipt")
    return RemoteMaterializationReceipt(
        workspace_id=_required_string(data, "workspace_id"),
        snapshot_id=_required_string(data, "snapshot_id"),
        expected_checksum=_required_string(data, "expected_checksum"),
        observed_checksum=_required_string(data, "observed_checksum"),
        access_mode=WorkspaceAccessMode(_required_string(data, "access_mode")),
        worker_ref=_required_string(data, "worker_ref"),
        materialization_ref=_required_string(data, "materialization_ref"),
        cache_hit=_boolean(data.get("cache_hit"), "cache_hit"),
        acknowledged_at=_datetime(_required(data, "acknowledged_at")),
    )


def _encode_cleanup(cleanup: RemoteCleanupAcknowledgement) -> dict[str, JsonValue]:
    return {
        "workspace_id": cleanup.workspace_id,
        "snapshot_id": cleanup.snapshot_id,
        "materialization_ref": cleanup.materialization_ref,
        "outcome": cleanup.outcome.value,
        "succeeded": cleanup.succeeded,
        "error_code": cleanup.error_code,
        "acknowledged_at": cleanup.acknowledged_at.isoformat(),
    }


def _decode_cleanup(value: object) -> RemoteCleanupAcknowledgement:
    data = _mapping(value, "RemoteCleanupAcknowledgement")
    return RemoteCleanupAcknowledgement(
        workspace_id=_required_string(data, "workspace_id"),
        snapshot_id=_required_string(data, "snapshot_id"),
        materialization_ref=_required_string(data, "materialization_ref"),
        outcome=MaterializationOutcome(_required_string(data, "outcome")),
        succeeded=_boolean(data.get("succeeded"), "succeeded"),
        error_code=_optional_string(data.get("error_code"), "error_code"),
        acknowledged_at=_datetime(_required(data, "acknowledged_at")),
    )


def _safe_workspace_error(error: Exception) -> str:
    if isinstance(error, (RegistryError, ContractError, ValueError)):
        return redact_exception(error)
    return "remote Workspace operation failed"


def _decode_base64(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RegistryError("remote Workspace payload contains invalid base64") from exc


def _required_base64(data: Mapping[str, object], name: str) -> bytes:
    value = _required(data, name)
    if not isinstance(value, str):
        raise RegistryError(f"remote Workspace field {name} must be a base64 string")
    return _decode_base64(value)


def _validate_sha256(value: str) -> None:
    if len(value) != 64:
        raise ValueError("sha256 must contain 64 hexadecimal characters")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError("sha256 must be hexadecimal") from exc


def _validate_opaque(value: str, label: str) -> None:
    if not value.strip() or "/" in value or "\\" in value or value in {".", ".."}:
        raise RegistryError(f"{label} must be an opaque reference")


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise RegistryError(f"{label} must be an object")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise RegistryError(f"{label} keys must be strings")
        result[key] = item
    return result


def _array(value: object, label: str) -> list[object]:
    if not isinstance(value, list | tuple):
        raise RegistryError(f"{label} must be an array")
    return list(value)


def _required(data: Mapping[str, object], name: str) -> object:
    if name not in data:
        raise RegistryError(f"required remote Workspace field is missing: {name}")
    return data[name]


def _required_string(data: Mapping[str, object], name: str) -> str:
    value = _required(data, name)
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"remote Workspace field {name} must be a non-blank string")
    return value


def _optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"remote Workspace field {name} must be a non-blank string or null")
    return value


def _required_integer(data: Mapping[str, object], name: str, *, minimum: int) -> int:
    value = _required(data, name)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise RegistryError(f"remote Workspace field {name} must be an integer >= {minimum}")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise RegistryError(f"remote Workspace field {name} must be boolean")
    return value


def _datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise RegistryError("remote Workspace timestamp must be a string")
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise RegistryError("remote Workspace timestamp is invalid") from exc
    if timestamp.tzinfo is None:
        raise RegistryError("remote Workspace timestamp must include timezone")
    return timestamp
