from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.workspaces import (
    MaterializationOutcome,
    RemoteCleanupAcknowledgement,
    RemoteMaterializationReceipt,
    RemoteMaterializationRequest,
    RemoteMaterializationResult,
    RemoteWorkspaceMaterializer,
    WorkspaceAccessMode,
    WorkspaceChange,
    WorkspaceChangeKind,
)


class FakeRemoteMaterializer(RemoteWorkspaceMaterializer):
    async def materialize(
        self,
        request: RemoteMaterializationRequest,
    ) -> RemoteMaterializationReceipt:
        return RemoteMaterializationReceipt(
            workspace_id=request.workspace_id,
            snapshot_id=request.snapshot_id,
            expected_checksum=request.expected_checksum,
            observed_checksum=request.expected_checksum,
            access_mode=request.access_mode,
            worker_ref="worker-test",
            materialization_ref="remote-materialization-test",
        )

    async def collect_result(
        self,
        receipt: RemoteMaterializationReceipt,
    ) -> RemoteMaterializationResult:
        return RemoteMaterializationResult(
            workspace_id=receipt.workspace_id,
            snapshot_id=receipt.snapshot_id,
            materialization_ref=receipt.materialization_ref,
            content_checksum=receipt.observed_checksum,
        )

    async def cleanup(
        self,
        receipt: RemoteMaterializationReceipt,
        outcome: MaterializationOutcome,
    ) -> RemoteCleanupAcknowledgement:
        return RemoteCleanupAcknowledgement(
            workspace_id=receipt.workspace_id,
            snapshot_id=receipt.snapshot_id,
            materialization_ref=receipt.materialization_ref,
            outcome=outcome,
            succeeded=True,
        )


def _request() -> RemoteMaterializationRequest:
    return RemoteMaterializationRequest(
        workspace_id=new_id("workspace"),
        snapshot_id=new_id("workspace_snapshot"),
        expected_checksum="a" * 64,
        access_mode=WorkspaceAccessMode.READ_WRITE,
        cache_key="snapshot-cache-key",
    )


def test_remote_materialization_contract_round_trip_is_path_independent() -> None:
    async def scenario() -> None:
        transport = FakeRemoteMaterializer()
        request = _request()
        receipt = await transport.materialize(request)
        result = await transport.collect_result(receipt)
        cleanup = await transport.cleanup(receipt, MaterializationOutcome.SUCCEEDED)

        assert receipt.workspace_id == request.workspace_id
        assert receipt.snapshot_id == request.snapshot_id
        assert receipt.observed_checksum == request.expected_checksum
        assert "/" not in receipt.materialization_ref
        assert result.content_checksum == request.expected_checksum
        assert cleanup.succeeded is True
        assert cleanup.outcome is MaterializationOutcome.SUCCEEDED

    asyncio.run(scenario())


def test_remote_receipt_rejects_snapshot_checksum_mismatch_and_host_paths() -> None:
    request = _request()
    with pytest.raises(ValueError, match="checksum"):
        RemoteMaterializationReceipt(
            workspace_id=request.workspace_id,
            snapshot_id=request.snapshot_id,
            expected_checksum="a" * 64,
            observed_checksum="b" * 64,
            access_mode=request.access_mode,
            worker_ref="worker-test",
            materialization_ref="remote-materialization-test",
        )
    with pytest.raises(ValueError, match="filesystem path"):
        RemoteMaterializationReceipt(
            workspace_id=request.workspace_id,
            snapshot_id=request.snapshot_id,
            expected_checksum="a" * 64,
            observed_checksum="a" * 64,
            access_mode=request.access_mode,
            worker_ref="worker-test",
            materialization_ref="/tmp/worker-path",
        )


def test_remote_result_returns_canonical_change_and_artifact_references() -> None:
    request = _request()
    change = WorkspaceChange(
        relative_path="out/result.txt",
        kind=WorkspaceChangeKind.CREATED,
        file_id=new_id("file"),
        sha256="c" * 64,
    )
    artifact_id = new_id("artifact")
    result = RemoteMaterializationResult(
        workspace_id=request.workspace_id,
        snapshot_id=request.snapshot_id,
        materialization_ref="remote-result",
        content_checksum=request.expected_checksum,
        changes=(change,),
        artifact_ids=(artifact_id,),
    )

    assert result.changes[0].file_id == change.file_id
    assert result.artifact_ids == (artifact_id,)


def test_failed_remote_cleanup_is_explicit_and_observable() -> None:
    request = _request()
    failed = RemoteCleanupAcknowledgement(
        workspace_id=request.workspace_id,
        snapshot_id=request.snapshot_id,
        materialization_ref="remote-result",
        outcome=MaterializationOutcome.FAILED,
        succeeded=False,
        error_code="worker_cleanup_failed",
    )
    assert failed.error_code == "worker_cleanup_failed"

    with pytest.raises(ValueError, match="requires an error_code"):
        RemoteCleanupAcknowledgement(
            workspace_id=request.workspace_id,
            snapshot_id=request.snapshot_id,
            materialization_ref="remote-result",
            outcome=MaterializationOutcome.CANCELLED,
            succeeded=False,
        )
