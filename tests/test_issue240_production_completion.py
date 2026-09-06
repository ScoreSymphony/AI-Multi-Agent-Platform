from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from ai_multi_agent_platform.adapters.distributed_control_plane_app import (
    build_distributed_control_plane_deployment,
)
from ai_multi_agent_platform.cli.compute import add_compute_parsers
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.data import LocalFileProvider
from ai_multi_agent_platform.deployment.advanced_profiles import load_advanced_deployment_profile
from ai_multi_agent_platform.deployment.config import SingleNodeConfig
from ai_multi_agent_platform.deployment.distributed_admin import DistributedWorkerAdmin
from ai_multi_agent_platform.deployment.distributed_control_plane import (
    DeploymentWorkerProtocolService,
)
from ai_multi_agent_platform.deployment.worker_presence import WorkerPresenceEndpoint
from ai_multi_agent_platform.distributed import (
    DistributedRegistry,
    DistributedRuntime,
    Heartbeat,
    NodeRecord,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerRecord,
    WorkerStatus,
)
from ai_multi_agent_platform.distributed.worker_protocol import (
    WorkerHeartbeatRequest,
    WorkerRequestCredentials,
)
from ai_multi_agent_platform.messaging import InProcessMessageTransport
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
from ai_multi_agent_platform.security.sqlite_authorization import SqliteLocalAuthorizationProvider
from ai_multi_agent_platform.workspaces.reference import LocalWorkspaceProvider

_PROFILE = Path("deploy/distributed/profiles/multi-local-workers.json")


def _fast_authentication() -> LocalAuthenticationService:
    return LocalAuthenticationService(
        password_hasher=ScryptPasswordHasher(n=2**10, r=8, p=1, maxmem=8 * 1024 * 1024)
    )


def _two_worker_registration() -> RegistrationRequest:
    node_id = "node_00000000-0000-4000-8000-000000000901"
    reporter_id = "worker_00000000-0000-4000-8000-000000000902"
    sibling_id = "worker_00000000-0000-4000-8000-000000000903"
    resources = ResourceSnapshot(
        cpu_cores_total=4,
        cpu_cores_available=4,
        ram_total_bytes=8_000_000,
        ram_available_bytes=8_000_000,
        storage_total_bytes=50_000_000,
        storage_available_bytes=50_000_000,
    )
    workers = tuple(
        WorkerRecord(
            worker_id=worker_id,
            node_id=node_id,
            supported_executors=("reference",),
            supported_runtimes=("python",),
            capability_refs=("execution:general",),
        )
        for worker_id in (reporter_id, sibling_id)
    )
    return RegistrationRequest(
        node=NodeRecord(
            node_id=node_id,
            display_name="issue-240-presence-node",
            resources=resources,
            supported_runtimes=("python",),
            capability_refs=("execution:general",),
        ),
        workers=workers,
        service_identity_ref=reporter_id,
    )


def _worker_security(registration: RegistrationRequest):
    reporter_id = registration.service_identity_ref
    assert reporter_id is not None
    authentication = _fast_authentication()
    actions = frozenset(
        {
            AuthorizationAction.CREATE,
            AuthorizationAction.MODIFY,
            AuthorizationAction.DELETE,
        }
    )
    resource_types = frozenset({ResourceType.NODE, ResourceType.WORKER})
    credential = authentication.create_worker_credential(
        reporter_id,
        scope=CredentialScope(actions=actions, resource_types=resource_types),
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


def _credentials(secret: str, nonce: str) -> WorkerRequestCredentials:
    return WorkerRequestCredentials(
        token=secret,
        nonce=nonce,
        issued_at=datetime.now(UTC),
        request_id=nonce,
        correlation_id=nonce,
    )


def test_profile_bound_admin_provisions_and_rotates_reporter_credential(tmp_path: Path) -> None:
    profile = load_advanced_deployment_profile(_PROFILE)
    node = profile.nodes[0]
    reporter_id = node.reporter_worker_id
    assert reporter_id is not None
    authentication = _fast_authentication()
    authorization = SqliteLocalAuthorizationProvider(tmp_path / "authorization.sqlite3")
    admin = DistributedWorkerAdmin(profile, authentication, authorization)
    context = RequestContext(request_id="issue240-provision", correlation_id="issue240-provision")

    async def scenario() -> None:
        provisioned = await admin.provision(context, reporter_id, {})
        assert provisioned["state"] == "provisioned"
        assert provisioned["secret_display"] == "one_time"
        secret = provisioned["secret"]
        credential_id = provisioned["credential_id"]
        assert isinstance(secret, str)
        assert isinstance(credential_id, str)
        assert authorization.has_policy(reporter_id)

        stored = authentication.list_credentials(reporter_id)[0]
        scope = authentication.credential_scope(stored.credential_id)
        assert scope.resource_ids == frozenset(
            {node.node.node_id, *(worker.worker_id for worker in node.workers)}
        )
        actor = authentication.authenticate_worker_request(
            secret,
            nonce="issue240-provision-auth",
            issued_at=datetime.now(UTC),
        )
        assert actor.identity.actor_id == reporter_id
        assert actor.identity.actor_type is ActorType.WORKER

        repeated = await admin.provision(context, reporter_id, {})
        assert repeated["state"] == "already_provisioned"
        assert "secret" not in repeated
        assert credential_id in repeated["active_credential_ids"]

        rotated = await admin.rotate(
            context,
            reporter_id,
            {"credential_id": credential_id},
        )
        replacement_secret = rotated["secret"]
        assert isinstance(replacement_secret, str)
        assert rotated["previous_credential_id"] == credential_id
        assert rotated["credential_id"] != credential_id
        try:
            authentication.authenticate_bearer(secret)
        except AuthenticationError as exc:
            assert exc.failure is AuthenticationFailure.CREDENTIAL_REVOKED
        else:
            raise AssertionError("rotated Worker credential remained valid")
        replacement_actor = authentication.authenticate_bearer(replacement_secret)
        assert replacement_actor.identity.actor_id == reporter_id

    asyncio.run(scenario())


def test_reporter_heartbeat_cannot_keep_dead_sibling_worker_healthy(tmp_path: Path) -> None:
    async def scenario() -> None:
        registration = _two_worker_registration()
        reporter, sibling = registration.workers
        authentication, authorization, secret = _worker_security(registration)
        transport = InProcessMessageTransport(provider_id="issue-240-worker-presence")
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite3")
        workspaces = LocalWorkspaceProvider(tmp_path / "workspaces", files)
        runtime = DistributedRuntime(DistributedRegistry())
        service = DeploymentWorkerProtocolService(
            runtime,
            authentication=authentication,
            authorization=authorization,
            transport=transport,
            workspaces=workspaces,
            files=files,
            context_resolver=lambda _workspace: None,  # type: ignore[arg-type]
            presence_timeout_seconds=0.05,
        )
        reporter_endpoint = WorkerPresenceEndpoint(reporter.worker_id, transport)
        sibling_endpoint = WorkerPresenceEndpoint(sibling.worker_id, transport)
        reporter_task = asyncio.create_task(reporter_endpoint.serve())
        sibling_task = asyncio.create_task(sibling_endpoint.serve())
        await asyncio.sleep(0)
        try:
            await service.register(
                registration,
                _credentials(secret, "issue240-presence-register"),
            )
            assert runtime.registry.get_worker(reporter.worker_id).status is WorkerStatus.HEALTHY
            assert runtime.registry.get_worker(sibling.worker_id).status is WorkerStatus.HEALTHY

            sibling_task.cancel()
            with suppress(asyncio.CancelledError):
                await sibling_task
            await service.heartbeat(
                WorkerHeartbeatRequest(
                    heartbeat=Heartbeat(
                        node_id=registration.node.node_id,
                        sequence=1,
                        resources=registration.node.resources,
                        workers=registration.workers,
                    ),
                    service_identity_ref=reporter.worker_id,
                ),
                _credentials(secret, "issue240-presence-loss"),
            )
            assert runtime.registry.get_worker(reporter.worker_id).status is WorkerStatus.HEALTHY
            assert runtime.registry.get_worker(sibling.worker_id).status is WorkerStatus.OFFLINE

            sibling_task = asyncio.create_task(sibling_endpoint.serve())
            await asyncio.sleep(0)
            await service.heartbeat(
                WorkerHeartbeatRequest(
                    heartbeat=Heartbeat(
                        node_id=registration.node.node_id,
                        sequence=2,
                        resources=registration.node.resources,
                        workers=registration.workers,
                    ),
                    service_identity_ref=reporter.worker_id,
                ),
                _credentials(secret, "issue240-presence-restart"),
            )
            assert runtime.registry.get_worker(sibling.worker_id).status is WorkerStatus.HEALTHY
        finally:
            reporter_task.cancel()
            sibling_task.cancel()
            await asyncio.gather(reporter_task, sibling_task, return_exceptions=True)
            await transport.close(graceful=False)

    asyncio.run(scenario())


def test_shipped_distributed_server_registers_compute_and_worker_admin(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("PLATFORM_MESSAGE_BROKER_PORT", "8765")
    deployment = build_distributed_control_plane_deployment(
        SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False),
        profile_path=str(_PROFILE),
    )
    resources = set(deployment.control_plane.registered_collections)
    commands = set(deployment.control_plane.registered_commands)
    assert {"nodes", "workers", "worker-jobs"}.issubset(resources)
    assert {
        "node.drain",
        "worker.drain",
        "worker.provision",
        "worker.rotate-credential",
    }.issubset(commands)


def test_platform_worker_cli_exposes_provision_and_rotation_commands() -> None:
    parser = argparse.ArgumentParser()
    areas = parser.add_subparsers(dest="area", required=True)
    add_compute_parsers(areas)
    worker_id = "worker_00000000-0000-4000-8000-000000000902"

    provision = parser.parse_args(
        [
            "worker",
            "provision",
            worker_id,
            "--secret-file",
            "worker.token",
            "--idempotency-key",
            "provision-1",
        ]
    )
    assert provision.area == "worker"
    assert provision.command == "provision"
    assert provision.worker_id == worker_id
    assert provision.secret_file == "worker.token"

    rotate = parser.parse_args(
        [
            "worker",
            "rotate-credential",
            worker_id,
            "--credential-id",
            "credential_00000000-0000-4000-8000-000000000904",
            "--secret-file",
            "worker.token",
            "--idempotency-key",
            "rotate-1",
        ]
    )
    assert rotate.command == "rotate-credential"
    assert rotate.worker_id == worker_id
    assert rotate.secret_file == "worker.token"
