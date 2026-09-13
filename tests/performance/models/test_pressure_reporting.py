from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.contracts import AdapterMetadata
from ai_multi_agent_platform.deployment.distributed_worker import (
    DistributedWorkerProcess,
    DistributedWorkerProcessConfig,
)
from ai_multi_agent_platform.distributed import (
    DistributedRegistry,
    DistributedRuntime,
    HostPressureSnapshot,
    NodeRecord,
    PressureKind,
    PressureSignal,
    PressureState,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerProtocolService,
    WorkerRecord,
    WorkerRequestCredentials,
)
from ai_multi_agent_platform.distributed.pressure_reporting import (
    PRESSURE_PROVENANCE_NAMESPACE,
    PRESSURE_REPORT_NAMESPACE,
    RegistryPressureSnapshotProvider,
    attach_pressure_report,
    authenticate_pressure_report,
    pressure_report_metadata,
)
from ai_multi_agent_platform.distributed.worker_protocol_http import WorkerProtocolHTTPClient
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.messaging import InProcessMessageTransport
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    CredentialScope,
    LocalAuthenticationService,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
    ScryptPasswordHasher,
)

NOW = datetime(2026, 9, 7, 3, 30, tzinfo=UTC)


class _PressureProvider:
    def __init__(self, snapshot: HostPressureSnapshot) -> None:
        self.snapshot = snapshot
        self.calls: list[str] = []

    def snapshot_for_node(self, node_id: str) -> HostPressureSnapshot:
        self.calls.append(node_id)
        return self.snapshot


def _snapshot(observed_at: datetime = NOW) -> HostPressureSnapshot:
    return HostPressureSnapshot(
        state=PressureState.ELEVATED,
        observed_at=observed_at,
        signals=(
            PressureSignal(
                PressureKind.MEMORY,
                PressureState.ELEVATED,
                12.5,
                "percent_stall_avg10",
            ),
        ),
        source_ref="linux:provider-private-source",
        trusted=False,
        provider_metadata=(
            AdapterMetadata(
                "linux.host_pressure",
                {"private_path": "/sys/fs/cgroup/private"},
            ),
        ),
    )


def _node() -> NodeRecord:
    return NodeRecord(
        node_id=new_id("node"),
        display_name="remote-pressure-node",
        resources=ResourceSnapshot(
            cpu_cores_total=8,
            cpu_cores_available=8,
            ram_total_bytes=16_000,
            ram_available_bytes=16_000,
            storage_total_bytes=100_000,
            storage_available_bytes=100_000,
        ),
    )


def _security(
    reporter_id: str,
) -> tuple[LocalAuthenticationService, LocalAuthorizationProvider, str]:
    authentication = LocalAuthenticationService(
        password_hasher=ScryptPasswordHasher(n=2**10, r=8, p=1, maxmem=8 * 1024 * 1024)
    )
    actions = frozenset({AuthorizationAction.CREATE, AuthorizationAction.MODIFY})
    resource_types = frozenset({ResourceType.NODE, ResourceType.WORKER})
    credential = authentication.create_worker_credential(
        reporter_id,
        scope=CredentialScope(actions=actions, resource_types=resource_types),
        now=NOW,
    )
    authorization = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref=reporter_id,
                actor_types=frozenset({ActorType.WORKER}),
                allowed_actions=actions,
                resource_types=resource_types,
            ),
        )
    )
    return authentication, authorization, credential.secret


def _credentials(secret: str, nonce: str, when: datetime = NOW) -> WorkerRequestCredentials:
    return WorkerRequestCredentials(
        token=secret,
        nonce=nonce,
        issued_at=when,
        tls_peer_ref="spiffe://example/pressure-reporter",
        request_id=f"pressure-{nonce}",
        correlation_id=f"pressure-{nonce}",
    )


def test_worker_pressure_report_contains_only_portable_evidence() -> None:
    metadata = pressure_report_metadata(_snapshot())

    assert metadata.namespace == PRESSURE_REPORT_NAMESPACE
    assert metadata.values["state"] == "elevated"
    assert metadata.values["observed_at"] == NOW.isoformat()
    serialized = repr(metadata.values)
    assert "provider-private-source" not in serialized
    assert "/sys/fs/cgroup/private" not in serialized
    assert "linux.host_pressure" not in serialized
    assert "trusted" not in serialized


def test_attach_pressure_report_replaces_old_report_and_remote_provenance_claim() -> None:
    node = _node()
    worker = WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        adapter_metadata=(
            AdapterMetadata(PRESSURE_REPORT_NAMESPACE, {"state": "critical"}),
            AdapterMetadata(PRESSURE_PROVENANCE_NAMESPACE, {"authentication": "forged"}),
            AdapterMetadata("worker.other", {"preserve": True}),
        ),
    )

    reported = attach_pressure_report(worker, _snapshot())

    namespaces = [item.namespace for item in reported.adapter_metadata]
    assert namespaces.count(PRESSURE_REPORT_NAMESPACE) == 1
    assert PRESSURE_PROVENANCE_NAMESPACE not in namespaces
    assert "worker.other" in namespaces


def test_distributed_worker_samples_provider_and_places_report_only_on_reporter(
    tmp_path: Path,
) -> None:
    node = _node()
    reporter = WorkerRecord(worker_id=new_id("worker"), node_id=node.node_id)
    sibling = WorkerRecord(worker_id=new_id("worker"), node_id=node.node_id)
    registration = RegistrationRequest(
        node=node,
        workers=(reporter, sibling),
        service_identity_ref=reporter.worker_id,
    )
    provider = _PressureProvider(_snapshot())
    process = DistributedWorkerProcess(
        DistributedWorkerProcessConfig(
            registration=registration,
            worker_id=reporter.worker_id,
            workspace_root=tmp_path / "worker",
        ),
        protocol=cast(WorkerProtocolHTTPClient, object()),
        transport=InProcessMessageTransport(),
        pressure_provider=provider,
    )

    request = process._heartbeat_request()

    reported = next(
        worker for worker in request.heartbeat.workers if worker.worker_id == reporter.worker_id
    )
    untouched = next(
        worker for worker in request.heartbeat.workers if worker.worker_id == sibling.worker_id
    )
    assert provider.calls == [node.node_id]
    assert any(item.namespace == PRESSURE_REPORT_NAMESPACE for item in reported.adapter_metadata)
    assert not any(
        item.namespace == PRESSURE_REPORT_NAMESPACE for item in untouched.adapter_metadata
    )
    assert request.heartbeat.sequence == 1
    serialized = repr(reported.adapter_metadata)
    assert "provider-private-source" not in serialized
    assert "/sys/fs/cgroup/private" not in serialized


def test_registry_provider_ignores_remote_report_without_service_owned_provenance() -> None:
    registry = DistributedRegistry()
    node = _node()
    worker = WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        adapter_metadata=(pressure_report_metadata(_snapshot()),),
    )
    registry.register(RegistrationRequest(node=node, workers=(worker,)), now=NOW)

    assert RegistryPressureSnapshotProvider(registry).snapshot_for_node(node.node_id) is None


def test_service_owned_provenance_overwrites_forgery_and_drops_sibling_report() -> None:
    node = _node()
    reporter = WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        adapter_metadata=(
            pressure_report_metadata(_snapshot()),
            AdapterMetadata(
                PRESSURE_PROVENANCE_NAMESPACE,
                {
                    "node_id": node.node_id,
                    "reporter_worker_id": "worker_forged",
                    "accepted_at": (NOW + timedelta(days=365)).isoformat(),
                    "authentication": "worker_protocol",
                },
            ),
        ),
    )
    sibling = WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        adapter_metadata=(pressure_report_metadata(_snapshot()),),
    )
    authentication, authorization, secret = _security(reporter.worker_id)
    runtime = DistributedRuntime(DistributedRegistry())
    service = WorkerProtocolService(
        runtime,
        authentication=authentication,
        authorization=authorization,
    )

    async def scenario() -> None:
        await service.register(
            RegistrationRequest(
                node=node,
                workers=(reporter, sibling),
                service_identity_ref=reporter.worker_id,
            ),
            _credentials(secret, "register"),
            now=NOW,
        )

    asyncio.run(scenario())

    safe_reporter = runtime.registry.get_worker(reporter.worker_id)
    safe_sibling = runtime.registry.get_worker(sibling.worker_id)
    provenance = next(
        item
        for item in safe_reporter.adapter_metadata
        if item.namespace == PRESSURE_PROVENANCE_NAMESPACE
    )
    assert provenance.values["reporter_worker_id"] == reporter.worker_id
    assert provenance.values["accepted_at"] == NOW.isoformat()
    assert not any(
        item.namespace in {PRESSURE_REPORT_NAMESPACE, PRESSURE_PROVENANCE_NAMESPACE}
        for item in safe_sibling.adapter_metadata
    )

    resolved = RegistryPressureSnapshotProvider(runtime.registry).snapshot_for_node(node.node_id)
    assert resolved is not None
    assert resolved.trusted is True
    assert resolved.source_ref == f"worker:{reporter.worker_id}"
    assert resolved.state is PressureState.ELEVATED
    assert resolved.observed_at == NOW
    assert resolved.signal(PressureKind.MEMORY) == PressureSignal(
        PressureKind.MEMORY,
        PressureState.ELEVATED,
        12.5,
        "percent_stall_avg10",
    )


def test_authenticated_future_dated_report_is_not_treated_as_fresh_pressure() -> None:
    node = _node()
    reporter = WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        adapter_metadata=(pressure_report_metadata(_snapshot(NOW + timedelta(minutes=1))),),
    )
    authentication, authorization, secret = _security(reporter.worker_id)
    runtime = DistributedRuntime(DistributedRegistry())
    service = WorkerProtocolService(
        runtime,
        authentication=authentication,
        authorization=authorization,
    )

    async def scenario() -> None:
        await service.register(
            RegistrationRequest(
                node=node,
                workers=(reporter,),
                service_identity_ref=reporter.worker_id,
            ),
            _credentials(secret, "future-register"),
            now=NOW,
        )

    asyncio.run(scenario())

    assert (
        RegistryPressureSnapshotProvider(runtime.registry).snapshot_for_node(node.node_id) is None
    )


def test_authenticate_pressure_report_keeps_report_untrusted_until_protocol_acceptance() -> None:
    registry = DistributedRegistry()
    node = _node()
    worker = WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        adapter_metadata=(pressure_report_metadata(_snapshot()),),
    )

    authenticated = authenticate_pressure_report(
        worker,
        node_id=node.node_id,
        reporter_worker_id=worker.worker_id,
        accepted_at=NOW,
    )
    registry.register(RegistrationRequest(node=node, workers=(authenticated,)), now=NOW)
    resolved = RegistryPressureSnapshotProvider(registry).snapshot_for_node(node.node_id)

    assert resolved is not None
    assert resolved.trusted is True
