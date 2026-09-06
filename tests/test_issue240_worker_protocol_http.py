from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from ai_multi_agent_platform.distributed import (
    DistributedRegistry,
    DistributedRuntime,
    Heartbeat,
    NodeRecord,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerHeartbeatRequest,
    WorkerProtocolService,
    WorkerRecord,
)
from ai_multi_agent_platform.distributed.worker_protocol_http import (
    WORKER_PROTOCOL_HTTP_PREFIX,
    WorkerProtocolASGI,
    WorkerProtocolCodec,
    WorkerProtocolHTTP,
    WorkerProtocolHTTPClient,
    WorkerProtocolHTTPRequest,
)
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

CODEC_TIME = datetime(2026, 9, 6, 0, 0, tzinfo=UTC)
NODE_ID = "node_00000000-0000-4000-8000-000000000240"
WORKER_ID = "worker_00000000-0000-4000-8000-000000000240"


def _registration() -> RegistrationRequest:
    node = NodeRecord(
        node_id=NODE_ID,
        display_name="Remote Worker test node",
        resources=ResourceSnapshot(
            cpu_cores_total=8,
            cpu_cores_available=8,
            ram_total_bytes=16 * 1024**3,
            ram_available_bytes=16 * 1024**3,
        ),
        supported_runtimes=("python",),
        capability_refs=("execution:general",),
    )
    worker = WorkerRecord(
        worker_id=WORKER_ID,
        node_id=NODE_ID,
        supported_executors=("reference",),
        supported_runtimes=("python",),
        capability_refs=("execution:general",),
    )
    return RegistrationRequest(
        node=node,
        workers=(worker,),
        service_identity_ref=WORKER_ID,
    )


def _service() -> tuple[WorkerProtocolHTTP, DistributedRuntime, str]:
    authentication = LocalAuthenticationService(
        password_hasher=ScryptPasswordHasher(n=2**10, r=8, p=1, maxmem=8 * 1024 * 1024)
    )
    actions = frozenset(
        {
            AuthorizationAction.CREATE,
            AuthorizationAction.MODIFY,
            AuthorizationAction.DELETE,
        }
    )
    resource_types = frozenset({ResourceType.NODE, ResourceType.WORKER})
    credential = authentication.create_worker_credential(
        WORKER_ID,
        scope=CredentialScope(actions=actions, resource_types=resource_types),
        now=datetime.now(UTC),
    )
    authorization = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref=WORKER_ID,
                actor_types=frozenset({ActorType.WORKER}),
                allowed_actions=actions,
                resource_types=resource_types,
            ),
        )
    )
    runtime = DistributedRuntime(DistributedRegistry())
    service = WorkerProtocolService(
        runtime,
        authentication=authentication,
        authorization=authorization,
    )
    return WorkerProtocolHTTP(service), runtime, credential.secret


def _headers(secret: str, nonce: str, *, when: datetime | None = None) -> dict[str, str]:
    issued_at = when or datetime.now(UTC)
    return {
        "authorization": f"Bearer {secret}",
        "x-worker-nonce": nonce,
        "x-worker-issued-at": issued_at.isoformat(),
        "x-request-id": f"issue240-{nonce}",
        "x-correlation-id": f"issue240-{nonce}",
    }


def _assert_worker_report(decoded: WorkerRecord, source: WorkerRecord) -> None:
    """Compare Worker-owned report fields, excluding Control-Plane liveness timestamps."""

    assert decoded.worker_id == source.worker_id
    assert decoded.node_id == source.node_id
    assert decoded.worker_type == source.worker_type
    assert decoded.supported_executors == source.supported_executors
    assert decoded.capability_refs == source.capability_refs
    assert decoded.supported_runtimes == source.supported_runtimes
    assert decoded.model_refs == source.model_refs
    assert decoded.concurrency_limit == source.concurrency_limit
    assert decoded.active_jobs == source.active_jobs
    assert decoded.status is source.status
    assert decoded.protocol_version == source.protocol_version
    assert decoded.worker_version == source.worker_version
    assert decoded.locality_refs == source.locality_refs
    assert decoded.adapter_metadata == source.adapter_metadata


def test_worker_protocol_codec_preserves_registration_and_heartbeat_reports() -> None:
    registration = _registration()
    encoded_registration = WorkerProtocolCodec.encode_registration(registration)
    decoded_registration = WorkerProtocolCodec.decode_registration(encoded_registration)
    assert decoded_registration.node.node_id == registration.node.node_id
    assert decoded_registration.node.resources == registration.node.resources
    _assert_worker_report(decoded_registration.workers[0], registration.workers[0])
    assert decoded_registration.service_identity_ref == WORKER_ID
    serialized_registration = repr(encoded_registration)
    assert "registered_at" not in serialized_registration
    assert "last_heartbeat_at" not in serialized_registration

    heartbeat = WorkerHeartbeatRequest(
        heartbeat=Heartbeat(
            node_id=NODE_ID,
            observed_at=CODEC_TIME,
            sequence=7,
            resources=registration.node.resources,
            workers=registration.workers,
        ),
        service_identity_ref=WORKER_ID,
    )
    encoded_heartbeat = WorkerProtocolCodec.encode_heartbeat(heartbeat)
    decoded_heartbeat = WorkerProtocolCodec.decode_heartbeat(encoded_heartbeat)
    assert decoded_heartbeat.service_identity_ref == heartbeat.service_identity_ref
    assert decoded_heartbeat.heartbeat.node_id == heartbeat.heartbeat.node_id
    assert decoded_heartbeat.heartbeat.observed_at == heartbeat.heartbeat.observed_at
    assert decoded_heartbeat.heartbeat.sequence == heartbeat.heartbeat.sequence
    assert decoded_heartbeat.heartbeat.resources == heartbeat.heartbeat.resources
    assert decoded_heartbeat.heartbeat.node_status == heartbeat.heartbeat.node_status
    assert decoded_heartbeat.heartbeat.protocol_version == heartbeat.heartbeat.protocol_version
    _assert_worker_report(
        decoded_heartbeat.heartbeat.workers[0], heartbeat.heartbeat.workers[0]
    )
    serialized_heartbeat = repr(encoded_heartbeat)
    assert "registered_at" not in serialized_heartbeat
    assert "last_heartbeat_at" not in serialized_heartbeat


def test_private_http_surface_registers_heartbeats_and_rejects_replay() -> None:
    http, runtime, secret = _service()
    registration = _registration()

    async def scenario() -> None:
        registered = await http.handle(
            WorkerProtocolHTTPRequest(
                method="POST",
                path=f"{WORKER_PROTOCOL_HTTP_PREFIX}/register",
                headers=_headers(secret, "register"),
                body=WorkerProtocolCodec.encode_registration(registration),
            )
        )
        assert registered.status == 200
        assert runtime.registry.get_node(NODE_ID).node_id == NODE_ID
        assert runtime.registry.get_worker(WORKER_ID).worker_id == WORKER_ID
        assert secret not in repr(registered.body)

        heartbeat = WorkerHeartbeatRequest(
            heartbeat=Heartbeat(
                node_id=NODE_ID,
                observed_at=CODEC_TIME,
                sequence=1,
                resources=registration.node.resources,
                workers=registration.workers,
            ),
            service_identity_ref=WORKER_ID,
        )
        heartbeat_request = WorkerProtocolHTTPRequest(
            method="POST",
            path=f"{WORKER_PROTOCOL_HTTP_PREFIX}/heartbeat",
            headers=_headers(secret, "heartbeat"),
            body=WorkerProtocolCodec.encode_heartbeat(heartbeat),
        )
        before_heartbeat = datetime.now(UTC)
        accepted = await http.handle(heartbeat_request)
        after_heartbeat = datetime.now(UTC)
        assert accepted.status == 200
        observed = runtime.registry.get_worker(WORKER_ID).last_heartbeat_at
        assert before_heartbeat <= observed <= after_heartbeat
        assert observed != CODEC_TIME

        replay = await http.handle(heartbeat_request)
        assert replay.status == 401
        assert secret not in repr(replay.body)

    asyncio.run(scenario())


def test_worker_protocol_credentials_are_transport_metadata_not_payload_fields() -> None:
    http, _runtime, secret = _service()
    body = WorkerProtocolCodec.encode_registration(_registration())
    serialized = repr(body)
    assert secret not in serialized
    assert "authorization" not in serialized.casefold()
    assert "x-worker-nonce" not in serialized.casefold()

    async def scenario() -> None:
        missing_auth = await http.handle(
            WorkerProtocolHTTPRequest(
                method="POST",
                path=f"{WORKER_PROTOCOL_HTTP_PREFIX}/register",
                headers={},
                body=body,
            )
        )
        assert missing_auth.status == 401

    asyncio.run(scenario())


def test_worker_protocol_asgi_delegates_non_worker_routes_and_preserves_lifespan() -> None:
    http, _runtime, _secret = _service()
    delegated: list[str] = []

    async def downstream(
        scope: dict[str, Any],
        receive: Any,
        send: Any,
    ) -> None:
        del receive, send
        delegated.append(str(scope.get("type")))

    app = WorkerProtocolASGI(http, downstream=downstream)

    async def scenario() -> None:
        async def receive() -> dict[str, Any]:
            return {"type": "lifespan.startup"}

        async def send(_message: dict[str, Any]) -> None:
            return None

        await app({"type": "lifespan"}, receive, send)
        await app({"type": "http", "path": "/api/v1/health"}, receive, send)

    asyncio.run(scenario())
    assert delegated == ["lifespan", "http"]


def test_worker_protocol_client_requires_https_for_non_loopback_control_plane() -> None:
    def credential() -> str:
        return "runtime-only-test-credential"

    try:
        WorkerProtocolHTTPClient(
            "http://192.0.2.10:8000",
            credential_provider=credential,
        )
    except ValueError as exc:
        assert "require HTTPS" in str(exc)
    else:
        raise AssertionError("non-loopback Worker protocol client accepted plaintext HTTP")
