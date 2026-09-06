"""Control-Plane composition for deployable #240 remote Workers.

The deployment layer binds authenticated #14 registration to the already-existing #35 Worker
transport and #37 Workspace materializer. The canonical distributed runtime remains the sole
scheduler/ownership authority.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime

from ai_multi_agent_platform.contracts import AuthorizationProvider, OperationContext
from ai_multi_agent_platform.data import DataAccessContext, FileProvider
from ai_multi_agent_platform.distributed import (
    CanonicalWorkerArtifactIntegrator,
    DistributedRuntime,
    WorkerStatus,
)
from ai_multi_agent_platform.distributed.models import RegistrationRequest, WorkerRecord
from ai_multi_agent_platform.distributed.transport import TransportWorkerDispatcher
from ai_multi_agent_platform.distributed.worker_protocol import (
    WorkerHeartbeatRequest,
    WorkerProtocolReceipt,
    WorkerProtocolService,
    WorkerRequestAuthenticator,
    WorkerRequestCredentials,
)
from ai_multi_agent_platform.distributed.worker_protocol_http import (
    ASGIApp,
    WorkerProtocolASGI,
    WorkerProtocolHTTP,
)
from ai_multi_agent_platform.distributed.workspace import (
    MaterializingWorkerDispatcher,
    WorkspaceJobMaterializationResolver,
)
from ai_multi_agent_platform.distributed.workspace_transport import (
    TransportRemoteWorkspaceMaterializer,
    WorkspaceDataContextResolver,
)
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.messaging import MessageTransport
from ai_multi_agent_platform.workspaces import Workspace, WorkspaceProvider

from .worker_presence import TransportWorkerPresenceProbe

WorkspaceContextResolver = WorkspaceDataContextResolver


class DeploymentWorkerProtocolService(WorkerProtocolService):
    """Attach authenticated registered Workers to the canonical distributed runtime.

    Registration remains owned by ``WorkerProtocolService``. This subclass adds deployment
    consequences only: each canonical Worker receives the standard transport dispatcher. Shipped
    multi-process composition additionally enables #35 presence enforcement so a live Node
    reporter cannot keep a dead sibling schedulable by repeating a static profile snapshot.
    """

    def __init__(
        self,
        runtime: DistributedRuntime,
        *,
        authentication: WorkerRequestAuthenticator,
        authorization: AuthorizationProvider,
        transport: MessageTransport,
        workspaces: WorkspaceProvider,
        files: FileProvider,
        context_resolver: WorkspaceContextResolver,
        kernel: PlatformKernel | None = None,
        initial_trust_level: str = "untrusted",
        presence_timeout_seconds: float | None = None,
    ) -> None:
        super().__init__(
            runtime,
            authentication=authentication,
            authorization=authorization,
            initial_trust_level=initial_trust_level,
        )
        self._transport = transport
        self._workspaces = workspaces
        self._files = files
        self._context_resolver = context_resolver
        self._kernel = kernel
        self._attached: set[str] = set()
        self._presence = (
            None
            if presence_timeout_seconds is None
            else TransportWorkerPresenceProbe(
                transport,
                timeout_seconds=presence_timeout_seconds,
            )
        )

    async def register(
        self,
        request: RegistrationRequest,
        credentials: WorkerRequestCredentials,
        *,
        now: datetime | None = None,
    ) -> WorkerProtocolReceipt:
        presence_workers = await self._presence_workers(request.workers)
        receipt = await super().register(
            replace(request, workers=presence_workers),
            credentials,
            now=now,
        )
        for worker_id in receipt.worker_ids:
            self._attach(worker_id)
        return receipt

    async def heartbeat(
        self,
        request: WorkerHeartbeatRequest,
        credentials: WorkerRequestCredentials,
        *,
        now: datetime | None = None,
    ) -> WorkerProtocolReceipt:
        presence_workers = await self._presence_workers(request.heartbeat.workers)
        safe_request = replace(
            request,
            heartbeat=replace(request.heartbeat, workers=presence_workers),
        )
        return await super().heartbeat(safe_request, credentials, now=now)

    async def deregister_worker(
        self,
        worker_id: str,
        node_id: str,
        credentials: WorkerRequestCredentials,
        *,
        now: datetime | None = None,
    ) -> None:
        await super().deregister_worker(
            worker_id,
            node_id,
            credentials,
            now=now,
        )
        self._attached.discard(worker_id)

    async def _presence_workers(
        self,
        workers: tuple[WorkerRecord, ...],
    ) -> tuple[WorkerRecord, ...]:
        if self._presence is None:
            return workers
        reachable = await asyncio.gather(
            *(self._presence.reachable(worker.worker_id) for worker in workers)
        )
        return tuple(
            worker if is_reachable else replace(worker, status=WorkerStatus.OFFLINE)
            for worker, is_reachable in zip(workers, reachable, strict=True)
        )

    def _attach(self, worker_id: str) -> None:
        if worker_id in self._attached:
            return
        transport_dispatcher = TransportWorkerDispatcher(worker_id, self._transport)
        materializer = TransportRemoteWorkspaceMaterializer(
            worker_id,
            self._transport,
            self._workspaces,
            self._files,
            self._context_resolver,
        )
        result_integrator = (
            None
            if self._kernel is None
            else CanonicalWorkerArtifactIntegrator(self._files, self._kernel)
        )
        dispatcher = MaterializingWorkerDispatcher(
            transport_dispatcher,
            materializer,
            WorkspaceJobMaterializationResolver(self._workspaces),
            result_integrator=result_integrator,
        )
        self.runtime.attach_worker(dispatcher)
        self._attached.add(worker_id)


def platform_workspace_context(workspace: Workspace) -> DataAccessContext:
    """Build the platform-service data context for canonical remote Workspace transfer."""

    return DataAccessContext(
        operation=OperationContext(
            correlation_id=f"remote-workspace:{workspace.id}",
            project_id=workspace.project_id,
        ),
        actor_ref="service:platform",
    )


def build_worker_protocol_app(
    *,
    downstream: ASGIApp,
    runtime: DistributedRuntime,
    authentication: WorkerRequestAuthenticator,
    authorization: AuthorizationProvider,
    transport: MessageTransport,
    workspaces: WorkspaceProvider,
    files: FileProvider,
    context_resolver: WorkspaceContextResolver = platform_workspace_context,
    kernel: PlatformKernel | None = None,
) -> tuple[WorkerProtocolASGI, DeploymentWorkerProtocolService]:
    """Wrap the real Control-Plane ASGI app with the authenticated Worker protocol surface."""

    service = DeploymentWorkerProtocolService(
        runtime,
        authentication=authentication,
        authorization=authorization,
        transport=transport,
        workspaces=workspaces,
        files=files,
        context_resolver=context_resolver,
        kernel=kernel,
        presence_timeout_seconds=1.0,
    )
    app = WorkerProtocolASGI(
        WorkerProtocolHTTP(service),
        downstream=downstream,
    )
    return app, service
