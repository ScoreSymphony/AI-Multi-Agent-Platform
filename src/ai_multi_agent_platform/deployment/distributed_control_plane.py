"""Control-Plane composition for deployable #240 remote Workers.

The deployment layer binds authenticated #14 registration to the already-existing #35 Worker
transport and #37 Workspace materializer. The canonical distributed runtime remains the sole
scheduler/ownership authority.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime

from ai_multi_agent_platform.capabilities import CapabilityRegistry
from ai_multi_agent_platform.contracts import AuthorizationProvider, OperationContext
from ai_multi_agent_platform.data import DataAccessContext, FileProvider
from ai_multi_agent_platform.distributed import (
    WORKSPACE_ARTIFACT_CAPABILITY_ID,
    ArtifactPublishingWorkerDispatcher,
    CanonicalWorkspaceArtifactPublisher,
    DistributedExecutorArtifactProvider,
    DistributedRuntime,
    NodeStatus,
    WorkerStatus,
)
from ai_multi_agent_platform.distributed.models import RegistrationRequest, WorkerRecord
from ai_multi_agent_platform.distributed.registry import RegistryError
from ai_multi_agent_platform.distributed.transport import TransportWorkerDispatcher
from ai_multi_agent_platform.distributed.worker import WorkerDispatcher
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
from ai_multi_agent_platform.workspaces import (
    RunWorkspaceBindingRepository,
    Workspace,
    WorkspaceProvider,
)

from .worker_presence import TransportWorkerPresenceProbe

WorkspaceContextResolver = WorkspaceDataContextResolver


class DeploymentWorkerProtocolService(WorkerProtocolService):
    """Attach authenticated registered Workers to the canonical distributed runtime.

    Registration remains owned by ``WorkerProtocolService``. This subclass adds deployment
    consequences only: each canonical Worker receives the standard transport dispatcher. Shipped
    multi-process composition additionally enables #35 presence enforcement so a live Node
    reporter cannot keep a dead sibling schedulable by repeating a static profile snapshot.

    When the deployment supplies its canonical Capability registry and Run Workspace bindings,
    eligible reference Workers that advertise ``tool.workspace.write_artifact`` also receive one
    worker-pinned ``DistributedExecutorArtifactProvider``. Provider publication follows the same
    authoritative Worker snapshot: unhealthy/offline/draining/incompatible/deregistered Workers
    are removed from capability discovery instead of leaving a stale, apparently usable tool.
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
        capabilities: CapabilityRegistry | None = None,
        workspace_bindings: RunWorkspaceBindingRepository | None = None,
        initial_trust_level: str = "untrusted",
        presence_timeout_seconds: float | None = None,
    ) -> None:
        super().__init__(
            runtime,
            authentication=authentication,
            authorization=authorization,
            initial_trust_level=initial_trust_level,
        )
        if (capabilities is None) != (workspace_bindings is None):
            raise ValueError(
                "distributed artifact capability publication requires both the capability "
                "registry and Run Workspace bindings"
            )
        self._transport = transport
        self._workspaces = workspaces
        self._files = files
        self._context_resolver = context_resolver
        self._kernel = kernel
        self._capabilities = capabilities
        self._workspace_bindings = workspace_bindings
        self._attached: set[str] = set()
        self._artifact_provider_ids: dict[str, str] = {}
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
            await self._attach(worker_id)
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
        receipt = await super().heartbeat(safe_request, credentials, now=now)
        for worker_id in receipt.worker_ids:
            await self._sync_artifact_provider(worker_id)
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
        self._remove_artifact_provider(worker_id)
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

    async def _attach(self, worker_id: str) -> None:
        if worker_id not in self._attached:
            transport_dispatcher = TransportWorkerDispatcher(worker_id, self._transport)
            materializer = TransportRemoteWorkspaceMaterializer(
                worker_id,
                self._transport,
                self._workspaces,
                self._files,
                self._context_resolver,
            )
            materializing = MaterializingWorkerDispatcher(
                transport_dispatcher,
                materializer,
                WorkspaceJobMaterializationResolver(self._workspaces),
            )
            dispatcher: WorkerDispatcher = materializing
            if self._kernel is not None:
                dispatcher = ArtifactPublishingWorkerDispatcher(
                    materializing,
                    CanonicalWorkspaceArtifactPublisher(
                        self._workspaces,
                        self._files,
                        self._kernel,
                        self._context_resolver,
                    ),
                )
            self.runtime.attach_worker(dispatcher)
            self._attached.add(worker_id)
        await self._sync_artifact_provider(worker_id)

    async def _sync_artifact_provider(self, worker_id: str) -> None:
        if self._capabilities is None or self._workspace_bindings is None:
            return
        provider_id = self._artifact_provider_ids.get(worker_id)
        try:
            worker = self.runtime.registry.get_worker(worker_id)
            node = self.runtime.registry.get_node(worker.node_id)
        except RegistryError:
            self._remove_artifact_provider(worker_id)
            return

        eligible = (
            worker.status is WorkerStatus.HEALTHY
            and not worker.draining
            and node.status is NodeStatus.ONLINE
            and not node.draining
            and not node.maintenance
            and "reference" in worker.supported_executors
            and WORKSPACE_ARTIFACT_CAPABILITY_ID in worker.capability_refs
        )
        if not eligible:
            self._remove_artifact_provider(worker_id)
            return
        if provider_id is not None:
            return

        provider_id = _artifact_provider_id(worker_id)
        registered_ids = {
            descriptor.provider_id for descriptor in self._capabilities.inventory_providers()
        }
        if provider_id not in registered_ids:
            await self._capabilities.register_provider(
                DistributedExecutorArtifactProvider(
                    self.runtime,
                    worker_id=worker_id,
                    workspace_bindings=self._workspace_bindings,
                    provider_id=provider_id,
                )
            )
        self._artifact_provider_ids[worker_id] = provider_id

    def _remove_artifact_provider(self, worker_id: str) -> None:
        if self._capabilities is None:
            return
        provider_id = self._artifact_provider_ids.pop(worker_id, None)
        if provider_id is None:
            provider_id = _artifact_provider_id(worker_id)
        registered_ids = {
            descriptor.provider_id for descriptor in self._capabilities.inventory_providers()
        }
        if provider_id in registered_ids:
            self._capabilities.unregister_provider(provider_id)


def _artifact_provider_id(worker_id: str) -> str:
    return f"distributed.executor.reference-artifact.{worker_id}"


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
    capabilities: CapabilityRegistry | None = None,
    workspace_bindings: RunWorkspaceBindingRepository | None = None,
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
        capabilities=capabilities,
        workspace_bindings=workspace_bindings,
        presence_timeout_seconds=1.0,
    )
    app = WorkerProtocolASGI(
        WorkerProtocolHTTP(service),
        downstream=downstream,
    )
    return app, service
