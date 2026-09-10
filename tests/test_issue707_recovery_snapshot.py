from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.contracts import AuthorizationProvider
from ai_multi_agent_platform.data import LocalFileProvider
from ai_multi_agent_platform.deployment.distributed_control_plane import (
    DeploymentWorkerProtocolService,
)
from ai_multi_agent_platform.deployment.worker_presence import WorkerPresenceEndpoint
from ai_multi_agent_platform.distributed import (
    DistributedRegistry,
    DistributedRuntime,
    JsonDistributedStateStore,
    NodeRecord,
    RegistrationRequest,
    WorkerRecord,
    WorkerStatus,
)
from ai_multi_agent_platform.distributed.worker_protocol import WorkerRequestAuthenticator
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.messaging import InProcessMessageTransport
from ai_multi_agent_platform.workspaces.reference import LocalWorkspaceProvider


def test_recovery_presence_subset_preserves_unreachable_sibling_drain_state(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        node_id = new_id("node")
        reachable_worker_id = new_id("worker")
        offline_worker_id = new_id("worker")
        node = NodeRecord(node_id=node_id, display_name="issue-707-recovery-snapshot")
        workers = (
            WorkerRecord(worker_id=reachable_worker_id, node_id=node_id, draining=False),
            WorkerRecord(worker_id=offline_worker_id, node_id=node_id, draining=False),
        )
        state_path = tmp_path / "distributed-runtime-state.json"
        first = DistributedRuntime(
            DistributedRegistry(),
            state_store=JsonDistributedStateStore(state_path),
        )
        first.register(RegistrationRequest(node=node, workers=workers))

        restarted = DistributedRuntime(DistributedRegistry())
        assert restarted.configure_state_store(JsonDistributedStateStore(state_path)) is True
        assert restarted.registry.get_worker(reachable_worker_id).status is WorkerStatus.OFFLINE
        assert restarted.registry.get_worker(offline_worker_id).status is WorkerStatus.OFFLINE

        transport = InProcessMessageTransport(provider_id="issue-707-recovery-snapshot")
        presence_task = asyncio.create_task(
            WorkerPresenceEndpoint(reachable_worker_id, transport).serve()
        )
        await asyncio.sleep(0)
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite3")
        workspaces = LocalWorkspaceProvider(tmp_path / "workspaces", files)
        service = DeploymentWorkerProtocolService(
            restarted,
            authentication=cast(WorkerRequestAuthenticator, object()),
            authorization=cast(AuthorizationProvider, object()),
            transport=transport,
            workspaces=workspaces,
            files=files,
            context_resolver=lambda _workspace: (_ for _ in ()).throw(
                AssertionError("workspace context must not be used")
            ),
            presence_timeout_seconds=0.05,
        )
        try:
            assert await service.restore_reachable_persisted_workers() == (
                reachable_worker_id,
            )
            reachable = restarted.registry.get_worker(reachable_worker_id)
            offline = restarted.registry.get_worker(offline_worker_id)
            assert reachable.status is WorkerStatus.DEGRADED
            assert reachable.draining is False
            assert offline.status is WorkerStatus.OFFLINE
            assert offline.draining is False
        finally:
            presence_task.cancel()
            with suppress(asyncio.CancelledError):
                await presence_task
            await transport.close(graceful=False)

    asyncio.run(scenario())
