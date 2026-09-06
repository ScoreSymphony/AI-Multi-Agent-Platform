from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.distributed import RegistryError
from ai_multi_agent_platform.distributed.workspace_transport import (
    WorkerWorkspaceMaterializationStore,
    _ManifestEntry,
    _canonical_snapshot_checksum,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.workspaces import (
    MaterializationOutcome,
    RemoteMaterializationRequest,
    WorkspaceAccessMode,
)


def _transfer_fixture(
    *,
    data: bytes = b"worker-boundary-content",
    relative_path: str = "src/input.txt",
) -> tuple[RemoteMaterializationRequest, tuple[_ManifestEntry, ...], bytes]:
    digest = hashlib.sha256(data).hexdigest()
    manifest = (
        _ManifestEntry(
            relative_path=relative_path,
            file_id=new_id("file"),
            sha256=digest,
            size_bytes=len(data),
        ),
    )
    request = RemoteMaterializationRequest(
        workspace_id=new_id("workspace"),
        snapshot_id=new_id("workspace_snapshot"),
        expected_checksum=_canonical_snapshot_checksum(manifest),
        access_mode=WorkspaceAccessMode.READ_WRITE,
        cache_key="issue-433-hardening",
    )
    return request, manifest, data


async def _materialize(
    store: WorkerWorkspaceMaterializationStore,
    request: RemoteMaterializationRequest,
    manifest: tuple[_ManifestEntry, ...],
    data: bytes,
):
    prepared = await store.prepare(request, manifest, chunk_bytes=1024)
    assert isinstance(prepared, str)
    await store.put_chunk(
        prepared,
        manifest[0].relative_path,
        chunk_index=0,
        total_chunks=1,
        data=data,
    )
    return await store.commit(prepared)


def test_worker_rejects_manifest_checksum_tampering(tmp_path: Path) -> None:
    async def scenario() -> None:
        request, manifest, _data = _transfer_fixture()
        tampered = RemoteMaterializationRequest(
            workspace_id=request.workspace_id,
            snapshot_id=request.snapshot_id,
            expected_checksum="0" * 64,
            access_mode=request.access_mode,
            cache_key=request.cache_key,
        )
        store = WorkerWorkspaceMaterializationStore(new_id("worker"), tmp_path / "worker")

        with pytest.raises(RegistryError, match="manifest checksum does not match snapshot"):
            await store.prepare(tampered, manifest, chunk_bytes=1024)

    asyncio.run(scenario())


def test_worker_rejects_corrupted_content_before_commit(tmp_path: Path) -> None:
    async def scenario() -> None:
        request, manifest, data = _transfer_fixture()
        store = WorkerWorkspaceMaterializationStore(new_id("worker"), tmp_path / "worker")
        prepared = await store.prepare(request, manifest, chunk_bytes=1024)
        assert isinstance(prepared, str)
        corrupted = b"x" * len(data)
        await store.put_chunk(
            prepared,
            manifest[0].relative_path,
            chunk_index=0,
            total_chunks=1,
            data=corrupted,
        )

        with pytest.raises(RegistryError, match="checksum mismatch"):
            await store.commit(prepared)

    asyncio.run(scenario())


def test_worker_rejects_traversal_chunk_path(tmp_path: Path) -> None:
    async def scenario() -> None:
        request, manifest, data = _transfer_fixture()
        store = WorkerWorkspaceMaterializationStore(new_id("worker"), tmp_path / "worker")
        prepared = await store.prepare(request, manifest, chunk_bytes=1024)
        assert isinstance(prepared, str)

        with pytest.raises((ContractError, ValueError), match="relative|path|travers"):
            await store.put_chunk(
                prepared,
                "../escape.txt",
                chunk_index=0,
                total_chunks=1,
                data=data,
            )

    asyncio.run(scenario())


def test_worker_rejects_symlink_escape_during_result_scan(tmp_path: Path) -> None:
    async def scenario() -> None:
        request, manifest, data = _transfer_fixture()
        worker_root = tmp_path / "worker"
        store = WorkerWorkspaceMaterializationStore(new_id("worker"), worker_root)
        receipt = await _materialize(store, request, manifest, data)
        materialized_root = worker_root / request.workspace_id / request.snapshot_id
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"outside")
        link = materialized_root / "escape-link"
        try:
            link.symlink_to(outside)
        except OSError as exc:
            pytest.skip(f"symlinks unavailable in test environment: {exc}")

        with pytest.raises(ContractError, match="symlinks are not permitted"):
            await store.result_manifest(receipt)

    asyncio.run(scenario())


def test_duplicate_prepare_chunk_and_commit_are_idempotent_and_conflicts_are_rejected(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        request, manifest, data = _transfer_fixture()
        store = WorkerWorkspaceMaterializationStore(new_id("worker"), tmp_path / "worker")
        first = await store.prepare(request, manifest, chunk_bytes=1024)
        second = await store.prepare(request, manifest, chunk_bytes=1024)
        assert isinstance(first, str)
        assert first == second

        await store.put_chunk(
            first,
            manifest[0].relative_path,
            chunk_index=0,
            total_chunks=1,
            data=data,
        )
        await store.put_chunk(
            first,
            manifest[0].relative_path,
            chunk_index=0,
            total_chunks=1,
            data=data,
        )
        with pytest.raises(RegistryError, match="duplicate workspace chunk carries different bytes"):
            await store.put_chunk(
                first,
                manifest[0].relative_path,
                chunk_index=0,
                total_chunks=1,
                data=b"z" * len(data),
            )

        receipt = await store.commit(first)
        target = tmp_path / "worker" / request.workspace_id / request.snapshot_id / "src/input.txt"
        assert target.read_bytes() == data
        assert receipt.materialization_ref == first
        assert receipt.cache_hit is False

        repeated_commit = await store.commit(first)
        assert repeated_commit.materialization_ref == first
        assert repeated_commit.workspace_id == receipt.workspace_id
        assert repeated_commit.snapshot_id == receipt.snapshot_id
        assert repeated_commit.observed_checksum == receipt.observed_checksum
        assert repeated_commit.cache_hit is True
        assert target.read_bytes() == data

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "outcome",
    [MaterializationOutcome.FAILED, MaterializationOutcome.CANCELLED],
)
def test_cleanup_removes_materialization_for_non_success_outcomes(
    tmp_path: Path,
    outcome: MaterializationOutcome,
) -> None:
    async def scenario() -> None:
        request, manifest, data = _transfer_fixture()
        worker_root = tmp_path / f"worker-{outcome.value}"
        store = WorkerWorkspaceMaterializationStore(new_id("worker"), worker_root)
        receipt = await _materialize(store, request, manifest, data)

        acknowledgement = await store.cleanup(receipt, outcome)
        assert acknowledgement.succeeded is True
        assert acknowledgement.outcome is outcome
        assert not (worker_root / request.workspace_id / request.snapshot_id).exists()

        repeated = await store.cleanup(receipt, outcome)
        assert repeated.succeeded is True
        assert repeated.outcome is outcome

    asyncio.run(scenario())
