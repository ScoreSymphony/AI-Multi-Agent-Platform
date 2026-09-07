"""Deployable Worker-process composition for advanced distributed profiles.

This module composes existing #14 Worker contracts, #35 MessageTransport, #36 Worker
credentials and #37 remote Workspace materialization. It deliberately owns no canonical
Task/Run/Node/Worker/Workspace identity of its own.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import ssl
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ai_multi_agent_platform.distributed import (
    Heartbeat,
    LinuxHostPressureProvider,
    LocalWorker,
    NodeStatus,
    PressureSnapshotProvider,
    RegistrationRequest,
    WorkerRecord,
)
from ai_multi_agent_platform.distributed.pressure_reporting import attach_pressure_report
from ai_multi_agent_platform.distributed.transport import WorkerTransportEndpoint
from ai_multi_agent_platform.distributed.worker_protocol import WorkerHeartbeatRequest
from ai_multi_agent_platform.distributed.worker_protocol_http import (
    WorkerProtocolHTTPClient,
    WorkerProtocolHTTPClientError,
)
from ai_multi_agent_platform.distributed.workspace_transport import (
    WorkerWorkspaceMaterializationStore,
    WorkerWorkspaceTransportEndpoint,
    WorkspaceBoundLocalWorker,
    WorkspaceLifecycleFactory,
)
from ai_multi_agent_platform.execution import ExecutorLifecycleBackend, ReferenceExecutor
from ai_multi_agent_platform.messaging import MessageTransport, TcpMessageTransport

from .advanced_profiles import DeploymentNode, load_advanced_deployment_profile
from .worker_presence import WorkerPresenceEndpoint

_DEFAULT_HEARTBEAT_SECONDS = 5.0
_REFERENCE_WORKSPACE = "reference"


@dataclass(frozen=True, slots=True)
class DistributedWorkerProcessConfig:
    """Runtime-only configuration for one independently running canonical Worker."""

    registration: RegistrationRequest
    worker_id: str
    workspace_root: Path
    heartbeat_interval_seconds: float = _DEFAULT_HEARTBEAT_SECONDS
    reporting: bool = True

    def __post_init__(self) -> None:
        worker_ids = {worker.worker_id for worker in self.registration.workers}
        if self.worker_id not in worker_ids:
            raise ValueError("distributed Worker process worker_id is not in registration snapshot")
        if self.reporting:
            if self.registration.service_identity_ref != self.worker_id:
                raise ValueError(
                    "reporting Worker must match RegistrationRequest.service_identity_ref"
                )
        if self.heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be greater than zero")
        if not self.workspace_root.is_absolute():
            raise ValueError("Worker workspace_root must be absolute")

    @property
    def worker(self) -> WorkerRecord:
        return next(
            worker for worker in self.registration.workers if worker.worker_id == self.worker_id
        )


class DistributedWorkerProcess:
    """Long-lived Worker process with registration, heartbeat, execution and Workspace endpoints."""

    def __init__(
        self,
        config: DistributedWorkerProcessConfig,
        *,
        protocol: WorkerProtocolHTTPClient | None,
        transport: MessageTransport,
        lifecycle_factory: WorkspaceLifecycleFactory | None = None,
        pressure_provider: PressureSnapshotProvider | None = None,
    ) -> None:
        if config.reporting and protocol is None:
            raise ValueError("a reporting Worker process requires a Worker protocol client")
        self.config = config
        self.protocol = protocol
        self.transport = transport
        self.pressure_provider = pressure_provider
        self._stop = asyncio.Event()
        self._sequence = 0

        config.workspace_root.mkdir(parents=True, exist_ok=True)
        self.store = WorkerWorkspaceMaterializationStore(config.worker_id, config.workspace_root)
        executor = ReferenceExecutor(config.workspace_root)
        ExecutorLifecycleBackend.ensure_workspace(config.workspace_root, _REFERENCE_WORKSPACE)
        self._lifecycle_factory: WorkspaceLifecycleFactory = lifecycle_factory or (
            lambda execution_workspace: ExecutorLifecycleBackend(
                executor,
                workspace=execution_workspace,
            )
        )
        fallback = LocalWorker(
            config.worker_id,
            ExecutorLifecycleBackend(executor, workspace=_REFERENCE_WORKSPACE),
        )
        self.worker = WorkspaceBoundLocalWorker(
            config.worker_id,
            self.store,
            self._lifecycle_factory,
            fallback=fallback,
        )
        self.workspace_endpoint = WorkerWorkspaceTransportEndpoint(self.store, transport)
        self.worker_endpoint = WorkerTransportEndpoint(self.worker, transport)
        self.presence_endpoint = WorkerPresenceEndpoint(config.worker_id, transport)

    async def run(self) -> None:
        """Serve until ``stop`` is requested or the hosting task is cancelled.

        Execution, Workspace and presence endpoints start before reporter registration. This makes
        registration truthful: a Worker is not projected healthy before its #35 endpoint can
        answer. A reporting Worker deregisters best-effort on graceful stop; abrupt sibling loss
        is detected by the reporter's transport presence probes on the next Node heartbeat.
        """

        registered = False
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(self.workspace_endpoint.serve())
                group.create_task(self.worker_endpoint.serve())
                group.create_task(self.presence_endpoint.serve())
                # Let subscription coroutines establish their transport consumers before the
                # Control Plane probes reachability during registration.
                await asyncio.sleep(0)
                if self.config.reporting:
                    await self._register()
                    registered = True
                    group.create_task(self._heartbeat_loop())
                await self._stop.wait()
                raise _WorkerStop()
        except* _WorkerStop:
            pass
        finally:
            if self.config.reporting and registered:
                await self._deregister_best_effort()

    def stop(self) -> None:
        self._stop.set()

    async def _register(self) -> None:
        protocol = self._required_protocol()
        await protocol.register(self._registration_request())

    def _registration_request(self) -> RegistrationRequest:
        return replace(
            self.config.registration,
            workers=self._workers_with_pressure_report(),
        )

    def _heartbeat_request(self) -> WorkerHeartbeatRequest:
        self._sequence += 1
        return WorkerHeartbeatRequest(
            heartbeat=Heartbeat(
                node_id=self.config.registration.node.node_id,
                sequence=self._sequence,
                resources=self.config.registration.node.resources,
                node_status=NodeStatus.ONLINE,
                workers=self._workers_with_pressure_report(),
            ),
            service_identity_ref=self.config.worker_id,
        )

    def _workers_with_pressure_report(self) -> tuple[WorkerRecord, ...]:
        provider = self.pressure_provider
        if not self.config.reporting or provider is None:
            return self.config.registration.workers
        node_id = self.config.registration.node.node_id
        snapshot = provider.snapshot_for_node(node_id)
        if snapshot is None:
            return self.config.registration.workers
        return tuple(
            attach_pressure_report(worker, snapshot)
            if worker.worker_id == self.config.worker_id
            else worker
            for worker in self.config.registration.workers
        )

    async def _heartbeat_loop(self) -> None:
        protocol = self._required_protocol()
        while not self._stop.is_set():
            heartbeat = self._heartbeat_request()
            try:
                await protocol.heartbeat(heartbeat)
            except WorkerProtocolHTTPClientError as exc:
                if exc.retryable:
                    # A Worker-protocol outage is not evidence that Control-Plane state vanished.
                    # Keep retrying; #35 presence evidence independently bounds sibling liveness.
                    pass
                elif exc.status == 400:
                    # A restarted Control Plane may have lost volatile Node/Worker registration.
                    # Re-registration is safe with the same canonical identities and restores the
                    # snapshot before subsequent heartbeat attempts.
                    await self._register()
                else:
                    raise
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.config.heartbeat_interval_seconds,
                )
            except TimeoutError:
                continue

    async def _deregister_best_effort(self) -> None:
        protocol = self._required_protocol()
        try:
            await protocol.deregister_worker(
                self.config.worker_id,
                self.config.registration.node.node_id,
            )
        except (WorkerProtocolHTTPClientError, OSError):
            # Loss of the Control Plane during shutdown must not prevent the Worker process from
            # terminating. The Control Plane will expire the last heartbeat and reconcile.
            return

    def _required_protocol(self) -> WorkerProtocolHTTPClient:
        if self.protocol is None:
            raise RuntimeError("Worker protocol client is unavailable")
        return self.protocol


class _WorkerStop(Exception):
    """TaskGroup-local sentinel used to cancel endpoint loops on an explicit stop."""


def build_worker_process_from_deployment_node(
    node: DeploymentNode,
    *,
    worker_id: str,
    protocol: WorkerProtocolHTTPClient | None,
    transport: MessageTransport,
    heartbeat_interval_seconds: float = _DEFAULT_HEARTBEAT_SECONDS,
    pressure_provider: PressureSnapshotProvider | None = None,
) -> DistributedWorkerProcess:
    """Compose one process from a validated #240 deployment node.

    Exactly the declared reporter performs registration/heartbeat. Additional Worker processes
    for the same Node run execution/Workspace/presence endpoints with ``reporting=False`` while
    the reporter owns the complete Node heartbeat snapshot. Local and remote reporters use the
    same authenticated Worker-protocol contract; ``connection_mode`` is locality metadata only.

    The profile's ``workspace_root`` is a host-level parent. Every independently running Worker
    receives a private child root named by its canonical ``worker_id`` so sibling processes cannot
    replace or clean up one another's materialized Workspace trees.
    """

    worker_ids = {worker.worker_id for worker in node.workers}
    if worker_id not in worker_ids:
        raise ValueError(f"Worker {worker_id!r} is not declared on deployment Node")
    reporting = node.reporter_worker_id == worker_id
    registration = RegistrationRequest(
        node=node.node,
        workers=node.workers,
        service_identity_ref=node.reporter_worker_id,
    )
    return DistributedWorkerProcess(
        DistributedWorkerProcessConfig(
            registration=registration,
            worker_id=worker_id,
            workspace_root=Path(str(node.binding.workspace_root)) / worker_id,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            reporting=reporting,
        ),
        protocol=protocol,
        transport=transport,
        pressure_provider=pressure_provider if reporting else None,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="platform-worker",
        description="Run one Worker process from an advanced distributed deployment profile.",
    )
    parser.add_argument("--profile", required=True, help="Validated #240 deployment profile JSON")
    parser.add_argument("--host-ref", required=True, help="Deployment host_ref to run")
    parser.add_argument("--worker-id", required=True, help="Canonical worker_* identity to serve")
    parser.add_argument("--control-plane-url", required=True)
    parser.add_argument("--broker-host", required=True)
    parser.add_argument("--broker-port", required=True, type=int)
    parser.add_argument(
        "--worker-token-env",
        default="PLATFORM_WORKER_TOKEN",
        help="Environment variable containing the #36 Worker bearer credential",
    )
    parser.add_argument(
        "--transport-auth-env",
        default="PLATFORM_TRANSPORT_AUTH_KEY",
        help="Environment variable containing the #35 TCP HMAC key (optional with mTLS/loopback)",
    )
    parser.add_argument("--ca-file", default=None)
    parser.add_argument("--client-cert", default=None)
    parser.add_argument("--client-key", default=None)
    parser.add_argument("--server-hostname", default=None)
    parser.add_argument("--heartbeat-seconds", type=float, default=_DEFAULT_HEARTBEAT_SECONDS)
    parser.add_argument(
        "--host-pressure",
        action="store_true",
        help="enable read-only Linux host-pressure reporting for the authenticated reporter",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        profile = load_advanced_deployment_profile(str(args.profile))
        node = next(item for item in profile.nodes if item.binding.host_ref == str(args.host_ref))
    except StopIteration:
        print(f"deployment host_ref is not present in profile: {args.host_ref}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"invalid distributed Worker profile: {exc}", file=sys.stderr)
        return 2

    token = os.environ.get(str(args.worker_token_env), "")
    reporting = node.reporter_worker_id == str(args.worker_id)
    if reporting and not token:
        print(
            f"reporting Worker credential environment variable is empty: {args.worker_token_env}",
            file=sys.stderr,
        )
        return 2
    transport_key = os.environ.get(str(args.transport_auth_env)) or None

    try:
        ssl_context = _client_ssl_context(
            ca_file=args.ca_file,
            client_cert=args.client_cert,
            client_key=args.client_key,
        )
        protocol = (
            WorkerProtocolHTTPClient(
                str(args.control_plane_url),
                credential_provider=lambda: token,
                ssl_context=ssl_context,
            )
            if reporting
            else None
        )
        transport = TcpMessageTransport(
            str(args.broker_host),
            int(args.broker_port),
            ssl_context=ssl_context,
            server_hostname=args.server_hostname,
            authentication_key=transport_key,
            provider_id=f"worker:{args.worker_id}",
        )
        pressure_provider = (
            LinuxHostPressureProvider()
            if bool(args.host_pressure) and reporting and sys.platform.startswith("linux")
            else None
        )
        worker = build_worker_process_from_deployment_node(
            node,
            worker_id=str(args.worker_id),
            protocol=protocol,
            transport=transport,
            heartbeat_interval_seconds=float(args.heartbeat_seconds),
            pressure_provider=pressure_provider,
        )
    except (OSError, ValueError) as exc:
        print(f"cannot compose distributed Worker: {exc}", file=sys.stderr)
        return 2

    async def serve() -> None:
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, worker.stop)
            except (NotImplementedError, RuntimeError):
                pass
        try:
            await worker.run()
        finally:
            await transport.close(graceful=True)

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        return 130
    except (WorkerProtocolHTTPClientError, OSError, RuntimeError) as exc:
        print(f"distributed Worker stopped with an error: {exc}", file=sys.stderr)
        return 3
    return 0


def _client_ssl_context(
    *,
    ca_file: str | None,
    client_cert: str | None,
    client_key: str | None,
) -> ssl.SSLContext | None:
    if ca_file is None and client_cert is None and client_key is None:
        return None
    if client_cert is None and client_key is not None:
        raise ValueError("--client-key requires --client-cert")
    if client_cert is not None and client_key is None:
        raise ValueError("--client-cert requires --client-key")
    context = ssl.create_default_context(cafile=ca_file)
    if client_cert is not None and client_key is not None:
        context.load_cert_chain(client_cert, client_key)
    return context


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
