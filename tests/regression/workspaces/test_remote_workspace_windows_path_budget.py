from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from ai_multi_agent_platform.distributed.workspace_transport import (
    WorkerWorkspaceMaterializationStore,
    _canonical_snapshot_checksum,
    _ManifestEntry,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.workspaces import RemoteMaterializationRequest, WorkspaceAccessMode

CLASSIC_WINDOWS_PATH_LIMIT = 260
OBSERVED_WORKER_ROOT_CHARS = 154


def test_worker_staging_preserves_classic_windows_path_budget(tmp_path: Path) -> None:
    async def scenario() -> None:
        root_prefix = str(tmp_path.resolve())
        padding_length = max(1, OBSERVED_WORKER_ROOT_CHARS - len(root_prefix) - 1)
        worker_root = tmp_path / ("w" * padding_length)
        assert len(str(worker_root.resolve())) >= OBSERVED_WORKER_ROOT_CHARS

        data = b"x" * 1024
        entry = _ManifestEntry(
            relative_path="payload.bin",
            file_id=new_id("file"),
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
        )
        manifest = (entry,)
        request = RemoteMaterializationRequest(
            workspace_id=new_id("workspace"),
            snapshot_id=new_id("workspace_snapshot"),
            expected_checksum=_canonical_snapshot_checksum(manifest),
            access_mode=WorkspaceAccessMode.READ_WRITE,
            cache_key="windows-path-budget",
        )
        store = WorkerWorkspaceMaterializationStore(new_id("worker"), worker_root)

        prepared = await store.prepare(request, manifest, chunk_bytes=1024)
        assert isinstance(prepared, str)
        await store.put_chunk(
            prepared,
            entry.relative_path,
            chunk_index=0,
            total_chunks=1,
            data=data,
        )

        incoming_root = worker_root / ".remote-workspace-incoming"
        staged_files = tuple(path for path in incoming_root.rglob("*") if path.is_file())
        assert len(staged_files) == 1
        assert len(str(staged_files[0])) < CLASSIC_WINDOWS_PATH_LIMIT

        receipt = await store.commit(prepared)
        assert receipt.materialization_ref == prepared
        execution_workspace = store.execution_workspace(request.workspace_id, request.snapshot_id)
        assert (worker_root / execution_workspace / entry.relative_path).read_bytes() == data

    asyncio.run(scenario())
