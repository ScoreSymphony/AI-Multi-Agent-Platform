from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.distributed import (
    DistributedRegistry,
    DistributedRuntime,
    Heartbeat,
    NodeRecord,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerRecord,
)
from ai_multi_agent_platform.distributed.worker_protocol import (
    WorkerHeartbeatRequest,
    WorkerProtocolAuthorizationError,
    WorkerProtocolError,
    WorkerProtocolService,
    WorkerRequestCredentials,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import (
    ActorType,
    AuthenticationError,
    AuthenticationFailure,
    AuthorizationAction,
    CredentialScope,
    LocalAuthenticationService,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
    ScryptPasswordHasher,
)

NOW = datetime(2026, 9, 3, 22, 0, tzinfo=UTC)


def _node() -> NodeRecord:
    return NodeRecord(
        node_id=new_id("node"),
        display_name="remote-node",
        resources=ResourceSnapshot(
            cpu_cores_total=16.0,
            cpu_cores_available=16.0,
            ram_total_bytes=64_000,
            ram_available_bytes=64_000,
            storage_total_bytes=500_000,
            storage_available_bytes=500_000,
        ),
        trust_level="reported-admin",
        draining=True,
        maintenance=True,
        supported_runtimes=("python",),
    )


def _worker(node: NodeRecord) -> WorkerRecord:
    return WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        supported_executors=("reference",),
        supported_runtimes=("python",),
        concurrency_limit=2,
        draining=True,
    )


def _authentication() -> LocalAuthenticationService:
    return LocalAuthenticationService(
        password_hasher=ScryptPasswordHasher(n=2**10, r=8, p=1, maxmem=8 * 1024 * 1024)
    )


def _security(
    reporter_id: str,
    *,
    scope_actions: frozenset[AuthorizationAction] | None = None,
    policy_actions: frozenset[AuthorizationAction] | None = None,
) -> tuple[LocalAuthenticationService, LocalAuthorizationProvider, str]:
    authentication = _authentication()
    scope = CredentialScope(
        actions=scope_actions
        or frozenset(
            {
                AuthorizationAction.CREATE,
                AuthorizationAction.MODIFY,
                AuthorizationAction.DELETE,
            }
        ),
        resource_types=frozenset({ResourceType.NODE, ResourceType.WORKER}),
    )
    credential = authentication.create_worker_credential(
        reporter_id,
        scope=scope,
        now=NOW,
    )
    authorization = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref=reporter_id,
                actor_types=frozenset({ActorType.WORKER}),
                allowed_actions=policy_actions
                or frozenset(
                    {
                        AuthorizationAction.CREATE,
                        AuthorizationAction.MODIFY,
                        AuthorizationAction.DELETE,
                    }
                ),
                resource_types=frozenset({ResourceType.NODE, ResourceType.WORKER}),
            ),
        )
    )
    return authentication, authorization, credential.secret


def _credentials(secret: str, nonce: str, *, when: datetime = NOW) -> WorkerRequestCredentials:
    return WorkerRequestCredentials(
        token=secret,
        nonce=nonce,
        issued_at=when,
        tls_peer_ref="spiffe://example/node-reporter",
        request_id=f"request-{nonce}",
        correlation_id=f"correlation-{nonce}",
    )


def _service(
    node: NodeRecord,
    reporter: WorkerRecord,
    *,
    scope_actions: frozenset[AuthorizationAction] | None = None,
    policy_actions: frozenset[AuthorizationAction] | None = None,
) -> tuple[WorkerProtocolService, DistributedRuntime, str]:
    authentication, authorization, secret = _security(
        reporter.worker_id,
        scope_actions=scope_actions,
        policy_actions=policy_actions,
    )
    runtime = DistributedRuntime(DistributedRegistry())
    service = WorkerProtocolService(
        runtime,
        authentication=authentication,
        authorization=authorization,
    )
    return service, runtime, secret


def _registration(
    node: NodeRecord,
    reporter: WorkerRecord,
    *workers: WorkerRecord,
) -> RegistrationRequest:
    return RegistrationRequest(
        node=node,
        workers=(reporter, *workers),
        service_identity_ref=reporter.worker_id,
    )


def test_remote_registration_binds_reporter_and_cannot_self_grant_control_state() -> None:
    node = _node()
    reporter = _worker(node)
    sibling = _worker(node)
    service, runtime, secret = _service(node, reporter)

    async def scenario() -> None:
        receipt = await service.register(
            _registration(node, reporter, sibling),
            _credentials(secret, "register-1"),
            now=NOW,
        )

        registered_node = runtime.registry.get_node(node.node_id)
        registered_reporter = runtime.registry.get_worker(reporter.worker_id)
        assert receipt.reporter_worker_id == reporter.worker_id
        assert set(receipt.worker_ids) == {reporter.worker_id, sibling.worker_id}
        assert registered_node.trust_level == "untrusted"
        assert registered_node.draining is False
        assert registered_node.maintenance is False
        assert registered_reporter.draining is False

    asyncio.run(scenario())


def test_reregistration_and_heartbeat_preserve_admin_drain_and_trust_state() -> None:
    node = _node()
    reporter = _worker(node)
    sibling = _worker(node)
    service, runtime, secret = _service(node, reporter)

    async def scenario() -> None:
        await service.register(
            _registration(node, reporter, sibling),
            _credentials(secret, "register-initial"),
            now=NOW,
        )
        runtime.set_node_draining(node.node_id, draining=True)
        runtime.set_node_maintenance(node.node_id, maintenance=True)
        runtime.set_worker_draining(reporter.worker_id, draining=True)
        runtime.set_worker_draining(sibling.worker_id, draining=True)

        malicious_node = replace(
            node,
            trust_level="reported-root",
            draining=False,
            maintenance=False,
        )
        await service.register(
            _registration(
                malicious_node,
                replace(reporter, draining=False),
                replace(sibling, draining=False),
            ),
            _credentials(secret, "register-again", when=NOW + timedelta(seconds=1)),
            now=NOW + timedelta(seconds=1),
        )

        after_registration = runtime.registry.get_node(node.node_id)
        assert after_registration.trust_level == "untrusted"
        assert after_registration.draining is True
        assert after_registration.maintenance is True
        assert runtime.registry.get_worker(reporter.worker_id).draining is True
        assert runtime.registry.get_worker(sibling.worker_id).draining is True

        await service.heartbeat(
            WorkerHeartbeatRequest(
                heartbeat=Heartbeat(
                    node_id=node.node_id,
                    sequence=1,
                    observed_at=NOW + timedelta(seconds=2),
                    workers=(
                        replace(reporter, draining=False),
                        replace(sibling, draining=False),
                    ),
                ),
                service_identity_ref=reporter.worker_id,
            ),
            _credentials(secret, "heartbeat-1", when=NOW + timedelta(seconds=2)),
            now=NOW + timedelta(seconds=2),
        )

        assert runtime.registry.get_node(node.node_id).maintenance is True
        assert runtime.registry.get_worker(reporter.worker_id).draining is True
        assert runtime.registry.get_worker(sibling.worker_id).draining is True

    asyncio.run(scenario())


def test_registration_rejects_wrong_worker_identity_and_incomplete_snapshot() -> None:
    node = _node()
    reporter = _worker(node)
    sibling = _worker(node)
    service, runtime, secret = _service(node, reporter)

    async def scenario() -> None:
        await service.register(
            _registration(node, reporter, sibling),
            _credentials(secret, "register-complete"),
            now=NOW,
        )

        with pytest.raises(WorkerProtocolError, match="every known Worker"):
            await service.register(
                _registration(node, reporter),
                _credentials(secret, "register-incomplete", when=NOW + timedelta(seconds=1)),
                now=NOW + timedelta(seconds=1),
            )
        assert runtime.registry.get_worker(sibling.worker_id).draining is False

        other_authentication, other_authorization, other_secret = _security(sibling.worker_id)
        other_service = WorkerProtocolService(
            runtime,
            authentication=other_authentication,
            authorization=other_authorization,
        )
        with pytest.raises(WorkerProtocolError, match="does not match"):
            await other_service.register(
                _registration(node, reporter, sibling),
                _credentials(other_secret, "wrong-reporter", when=NOW + timedelta(seconds=2)),
                now=NOW + timedelta(seconds=2),
            )

    asyncio.run(scenario())


def test_worker_request_replay_is_rejected_by_authentication_boundary() -> None:
    node = _node()
    reporter = _worker(node)
    service, _, secret = _service(node, reporter)
    credentials = _credentials(secret, "one-time-request")

    async def scenario() -> None:
        await service.register(_registration(node, reporter), credentials, now=NOW)
        with pytest.raises(AuthenticationError) as replay:
            await service.register(_registration(node, reporter), credentials, now=NOW)
        assert replay.value.failure is AuthenticationFailure.REPLAY_REJECTED

    asyncio.run(scenario())


def test_credential_scope_and_authorization_both_constrain_registration() -> None:
    node = _node()
    reporter = _worker(node)

    scope_service, scope_runtime, scope_secret = _service(
        node,
        reporter,
        scope_actions=frozenset({AuthorizationAction.MODIFY}),
    )
    policy_service, policy_runtime, policy_secret = _service(
        node,
        reporter,
        policy_actions=frozenset({AuthorizationAction.MODIFY}),
    )

    async def scenario() -> None:
        with pytest.raises(WorkerProtocolAuthorizationError, match="credential scope"):
            await scope_service.register(
                _registration(node, reporter),
                _credentials(scope_secret, "scope-denied"),
                now=NOW,
            )
        assert scope_runtime.registry.list_nodes() == ()

        with pytest.raises(WorkerProtocolAuthorizationError, match="not granted"):
            await policy_service.register(
                _registration(node, reporter),
                _credentials(policy_secret, "policy-denied"),
                now=NOW,
            )
        assert policy_runtime.registry.list_nodes() == ()

    asyncio.run(scenario())


def test_authenticated_heartbeat_cannot_inject_unknown_worker_or_omit_known_worker() -> None:
    node = _node()
    reporter = _worker(node)
    sibling = _worker(node)
    unknown = _worker(node)
    service, runtime, secret = _service(node, reporter)

    async def scenario() -> None:
        await service.register(
            _registration(node, reporter, sibling),
            _credentials(secret, "heartbeat-setup"),
            now=NOW,
        )

        with pytest.raises(WorkerProtocolError, match="complete registered Worker snapshot"):
            await service.heartbeat(
                WorkerHeartbeatRequest(
                    heartbeat=Heartbeat(
                        node_id=node.node_id,
                        sequence=1,
                        workers=(reporter,),
                    ),
                    service_identity_ref=reporter.worker_id,
                ),
                _credentials(secret, "heartbeat-omitted", when=NOW + timedelta(seconds=1)),
                now=NOW + timedelta(seconds=1),
            )

        with pytest.raises(WorkerProtocolError, match="complete registered Worker snapshot"):
            await service.heartbeat(
                WorkerHeartbeatRequest(
                    heartbeat=Heartbeat(
                        node_id=node.node_id,
                        sequence=1,
                        workers=(reporter, sibling, unknown),
                    ),
                    service_identity_ref=reporter.worker_id,
                ),
                _credentials(secret, "heartbeat-injected", when=NOW + timedelta(seconds=2)),
                now=NOW + timedelta(seconds=2),
            )
        with pytest.raises(RegistryError):
            runtime.registry.get_worker(unknown.worker_id)

    asyncio.run(scenario())
