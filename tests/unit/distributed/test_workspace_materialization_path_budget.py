from __future__ import annotations

import hashlib

from ai_multi_agent_platform.distributed.workspace_materialization_store import (
    _workspace_path_token,
)


def test_worker_local_materialization_token_preserves_path_budget() -> None:
    workspace_id = "workspace_00000000-0000-4000-8000-000000000240"
    snapshot_id = "workspace_snapshot_00000000-0000-4000-8000-000000000241"

    token = _workspace_path_token(workspace_id, snapshot_id)

    expected = hashlib.sha256(f"{workspace_id}\0{snapshot_id}".encode()).hexdigest()[:32]
    assert token == expected
    assert len(token) == 32
    assert "/" not in token
    assert "\\" not in token
    assert len(token) < len(workspace_id) + 1 + len(snapshot_id)


def test_worker_local_materialization_token_binds_workspace_and_snapshot() -> None:
    workspace_id = "workspace_00000000-0000-4000-8000-000000000240"
    snapshot_id = "workspace_snapshot_00000000-0000-4000-8000-000000000241"

    assert _workspace_path_token(workspace_id, snapshot_id) != _workspace_path_token(
        workspace_id,
        "workspace_snapshot_00000000-0000-4000-8000-000000000242",
    )
    assert _workspace_path_token(workspace_id, snapshot_id) != _workspace_path_token(
        "workspace_00000000-0000-4000-8000-000000000243",
        snapshot_id,
    )
