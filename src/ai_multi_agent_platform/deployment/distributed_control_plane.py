"""Control-Plane composition for deployable #240 remote Workers.

The deployment layer binds authenticated #14 registration to the already-existing #35 Worker
transport and #37 Workspace materializer.  The canonical distributed runtime remains the sole
scheduler/ownership authority.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from ai_multi_agent_platform.contracts import AuthorizationProvider, OperationContext
from ai_multi_agent_platform.data import DataAccessContext, FileProvider
from ai_multi_agent_platform.distributed import DistributedRuntime
from ai_multi_agent_platform.distributed.models import RegistrationRequest
from ai_multi_agent_platform.distributed.transport import TransportWorkerDispatcher
from ai_multi_agent_platform.distributed.worker_protocol import (
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
)
from ai_multi_agent_platform.messaging import MessageTransport
from ai_multi_agent_platform.workspaces import Workspace, WorkspaceProvider

WorkspaceContextResolver = Callable[[Workspace], DataAccessContext]


class DeploymentWorkerProtocolService(WorkerProtocolService):
    """Attach authenticated registered Workers to the canonical distributed runtime.

    Registration remains owned by ``WorkerProtocolService``.  This subclass adds only the
    deployment consequence: once canonical Worker records exist, each one receives the standard
    transport dispatcher wrapped by the standard remote Workspace materializer.
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
        initial_trust_level: str = "untrusted",
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
        self._attached: set[str] = set()

    async def register(
        self,
        request: RegistrationRequest,
        credentials: WorkerRequestCredentials,
        *,
        now: datetime | None = None,
    ) -> WorkerProtocolReceipt:
        receipt = await super().register(request, credentials, now=now)
        for worker_id in receipt.worker_ids:
            self._attach(worker_id)
        return receipt

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
        dispatcher = MaterializingWorkerDispatcher(
            transport_dispatcher,
            materializer,
            WorkspaceJobMaterializationResolver(self._workspaces),
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
    )
    app = WorkerProtocolASGI(
        WorkerProtocolHTTP(service),
        downstream=downstream,
    )
    return app, service
