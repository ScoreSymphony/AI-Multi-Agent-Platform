from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane import AuthenticatedControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.distributed import (
    DistributedRegistry,
    DistributedRuntime,
    NodeRecord,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerRecord,
)
from ai_multi_agent_platform.distributed.worker_protocol import (
    WorkerProtocolService,
    WorkerRequestCredentials,
)
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    CredentialScope,
    InMemoryAuthenticationStore,
    LocalAuthenticationService,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
    ScryptPasswordHasher,
)
from ai_multi_agent_platform.security.async_authentication import (
    AsyncAuthenticationServiceAdapter,
    AuthenticationPersistenceOffload,
)
from ai_multi_agent_platform.security.sqlite_authentication import SqliteAuthenticationStore

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
PASSWORD = "correct horse battery staple"


def _hasher() -> ScryptPasswordHasher:
    return ScryptPasswordHasher(n=2**10, r=8, p=1, maxmem=8 * 1024 * 1024)


def _service(store: InMemoryAuthenticationStore | None = None) -> LocalAuthenticationService:
    return LocalAuthenticationService(store=store, password_hasher=_hasher())


async def _wait_for_event(event: threading.Event, *, timeout: float = 1.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while not event.is_set() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.005)
    return event.is_set()


class _SlowSQLiteAuthenticationStore(SqliteAuthenticationStore):
    def __init__(self, path: Path) -> None:
        self.delay_seconds = 0.0
        self.connection_threads: list[str] = []
        super().__init__(path)

    def _connect(self) -> sqlite3.Connection:
        self.connection_threads.append(threading.current_thread().name)
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        return super()._connect()


class _BlockingAuthentication(LocalAuthenticationService):
    def __init__(self, *, failure: BaseException | None = None) -> None:
        super().__init__(password_hasher=_hasher())
        self.started = threading.Event()
        self.release = threading.Event()
        self.block = False
        self.failure = failure

    def authenticate_bearer(
        self,
        token: str,
        *,
        now: datetime | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ):
        if self.block:
            self.started.set()
            self.release.wait(timeout=5)
        if self.failure is not None:
            raise self.failure
        return super().authenticate_bearer(
            token,
            now=now,
            request_id=request_id,
            correlation_id=correlation_id,
        )


class _EchoControlPlane:
    registered_collections: tuple[str, ...] = ()
    registered_commands: tuple[str, ...] = ()

    async def list_tasks(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        return {
            "items": [
                {
                    "id": "task_async_auth",
                    "type": "task",
                    "principal_ref": context.actor.principal_ref,
                }
            ],
            "next_cursor": None,
            "total": 1,
            "limit": query.limit,
        }


def _seed_bearer(service: LocalAuthenticationService):
    user = service.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    return service.create_personal_access_token(user.user_id, purpose="async-auth", now=NOW)


def test_sqlite_authentication_runs_on_owned_worker_and_keeps_event_loop_responsive(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = _SlowSQLiteAuthenticationStore(tmp_path / "authentication.sqlite3")
        service = _service(store)
        credential = _seed_bearer(service)
        store.connection_threads.clear()
        store.delay_seconds = 0.08
        adapter = AsyncAuthenticationServiceAdapter(service)

        authentication = asyncio.create_task(
            adapter.authenticate_bearer(
                credential.secret,
                request_id="request-auth-heartbeat",
                correlation_id="correlation-auth-heartbeat",
            )
        )
        heartbeat = 0
        while not authentication.done():
            heartbeat += 1
            await asyncio.sleep(0.005)

        actor = await authentication
        assert actor.identity.actor_id.startswith("user_")
        assert heartbeat >= 2
        assert store.connection_threads
        assert all(
            name.startswith("authentication-persistence") for name in store.connection_threads
        )

    asyncio.run(scenario())


def test_authentication_queue_is_bounded_before_executor_submission() -> None:
    async def scenario() -> None:
        service = _BlockingAuthentication()
        credential = _seed_bearer(service)
        service.block = True
        offload = AuthenticationPersistenceOffload(max_concurrency=1, max_pending=2)
        adapter = AsyncAuthenticationServiceAdapter(service, offload=offload)

        first = asyncio.create_task(adapter.authenticate_bearer(credential.secret))
        assert await _wait_for_event(service.started)
        second = asyncio.create_task(adapter.authenticate_bearer(credential.secret))
        await asyncio.sleep(0)

        with pytest.raises(ContractError) as captured:
            await adapter.authenticate_bearer(credential.secret)
        assert captured.value.code is ErrorCode.TRANSIENT_FAILURE
        assert captured.value.retryable is True

        service.release.set()
        await first
        await second

    asyncio.run(scenario())


def test_authentication_adapters_share_one_serialization_boundary() -> None:
    service = _service()
    first = AsyncAuthenticationServiceAdapter(service)
    second = AsyncAuthenticationServiceAdapter(service)
    assert first.offload is second.offload


def test_cancellation_waits_for_started_authentication_persistence_to_settle() -> None:
    async def scenario() -> None:
        service = _BlockingAuthentication()
        credential = _seed_bearer(service)
        service.block = True
        adapter = AsyncAuthenticationServiceAdapter(service)

        authentication = asyncio.create_task(adapter.authenticate_bearer(credential.secret))
        assert await _wait_for_event(service.started)
        authentication.cancel()
        await asyncio.sleep(0)
        assert not authentication.done()

        service.release.set()
        with pytest.raises(asyncio.CancelledError):
            await authentication
        stored = service.store.credentials[credential.credential_id]
        assert stored.last_used_at is not None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("failure", "code", "retryable"),
    [
        (sqlite3.OperationalError("database is locked"), ErrorCode.TRANSIENT_FAILURE, True),
        (sqlite3.DatabaseError("corrupt page"), ErrorCode.BACKEND_ERROR, False),
    ],
)
def test_sqlite_authentication_failures_map_to_canonical_errors(
    failure: BaseException,
    code: ErrorCode,
    retryable: bool,
) -> None:
    async def scenario() -> None:
        service = _BlockingAuthentication(failure=failure)
        credential = _seed_bearer(service)
        adapter = AsyncAuthenticationServiceAdapter(service)

        with pytest.raises(ContractError) as captured:
            await adapter.authenticate_bearer(credential.secret)
        assert captured.value.code is code
        assert captured.value.retryable is retryable

    asyncio.run(scenario())


def test_authentication_restart_preserves_async_session_and_credential_state(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "authentication.sqlite3"
        first = _service(SqliteAuthenticationStore(path))
        first_adapter = AsyncAuthenticationServiceAdapter(first)
        account = await first_adapter.bootstrap_first_admin("alice", PASSWORD)
        login = await first_adapter.login("alice", PASSWORD)
        credential = await first_adapter.create_personal_access_token(
            account.user_id,
            purpose="restart",
        )

        restarted = _service(SqliteAuthenticationStore(path))
        restarted_adapter = AsyncAuthenticationServiceAdapter(restarted)
        session_actor = await restarted_adapter.authenticate_session(login.session.token)
        bearer_actor = await restarted_adapter.authenticate_bearer(credential.secret)

        assert session_actor.identity.actor_id == account.user_id
        assert bearer_actor.identity.actor_id == account.user_id
        assert len(await restarted_adapter.list_sessions(account.user_id)) == 1
        assert len(await restarted_adapter.list_credentials(account.user_id)) == 1

    asyncio.run(scenario())


def test_in_memory_and_sqlite_async_authentication_contracts_match(tmp_path: Path) -> None:
    async def exercise(service: LocalAuthenticationService) -> tuple[object, ...]:
        adapter = AsyncAuthenticationServiceAdapter(service)
        account = await adapter.bootstrap_first_admin("alice", PASSWORD)
        login = await adapter.login("alice", PASSWORD)
        credential = await adapter.create_personal_access_token(account.user_id, purpose="parity")
        session_actor = await adapter.authenticate_session(login.session.token)
        bearer_actor = await adapter.authenticate_bearer(credential.secret)
        sessions = await adapter.list_sessions(account.user_id)
        credentials = await adapter.list_credentials(account.user_id)
        return (
            session_actor.identity.actor_type,
            bearer_actor.identity.actor_type,
            session_actor.identity.actor_id == account.user_id,
            bearer_actor.identity.actor_id == account.user_id,
            len(sessions),
            len(credentials),
            sessions[0].last_seen_at is not None,
            credentials[0].last_used_at is not None,
        )

    async def scenario() -> None:
        memory = await exercise(_service())
        sqlite = await exercise(_service(SqliteAuthenticationStore(tmp_path / "authentication.sqlite3")))
        assert memory == sqlite

    asyncio.run(scenario())


def test_authenticated_control_plane_offloads_bearer_write_through(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _SlowSQLiteAuthenticationStore(tmp_path / "authentication.sqlite3")
        service = _service(store)
        credential = _seed_bearer(service)
        http = AuthenticatedControlPlaneHTTP(_EchoControlPlane(), service, secure_cookie=False)
        store.connection_threads.clear()
        store.delay_seconds = 0.08

        response_task = asyncio.create_task(
            http.handle(
                HTTPRequest(
                    method="GET",
                    path="/api/v1/tasks",
                    headers={"Authorization": f"Bearer {credential.secret}"},
                )
            )
        )
        heartbeat = 0
        while not response_task.done():
            heartbeat += 1
            await asyncio.sleep(0.005)

        response = await response_task
        assert response.status == 200
        assert heartbeat >= 2
        assert store.connection_threads
        assert all(
            name.startswith("authentication-persistence") for name in store.connection_threads
        )

    asyncio.run(scenario())


def test_worker_protocol_offloads_sqlite_worker_authentication(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _SlowSQLiteAuthenticationStore(tmp_path / "authentication.sqlite3")
        authentication = _service(store)
        node = NodeRecord(
            node_id="node_async_auth",
            display_name="async-auth-node",
            resources=ResourceSnapshot(
                cpu_cores_total=4.0,
                cpu_cores_available=4.0,
                ram_total_bytes=16_000,
                ram_available_bytes=16_000,
                storage_total_bytes=100_000,
                storage_available_bytes=100_000,
            ),
            supported_runtimes=("python",),
        )
        worker = WorkerRecord(
            worker_id="worker_async_auth",
            node_id=node.node_id,
            supported_executors=("reference",),
            supported_runtimes=("python",),
            concurrency_limit=1,
        )
        scope = CredentialScope(
            actions=frozenset(
                {
                    AuthorizationAction.CREATE,
                    AuthorizationAction.MODIFY,
                    AuthorizationAction.DELETE,
                }
            ),
            resource_types=frozenset({ResourceType.NODE, ResourceType.WORKER}),
        )
        credential = authentication.create_worker_credential(
            worker.worker_id,
            scope=scope,
            now=NOW,
        )
        authorization = LocalAuthorizationProvider(
            (
                LocalPrincipalPolicy(
                    principal_ref=worker.worker_id,
                    actor_types=frozenset({ActorType.WORKER}),
                    allowed_actions=frozenset(
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
        service = WorkerProtocolService(
            DistributedRuntime(DistributedRegistry()),
            authentication=authentication,
            authorization=authorization,
        )
        store.connection_threads.clear()
        store.delay_seconds = 0.08

        registration = asyncio.create_task(
            service.register(
                RegistrationRequest(
                    node=node,
                    workers=(worker,),
                    service_identity_ref=worker.worker_id,
                ),
                WorkerRequestCredentials(
                    token=credential.secret,
                    nonce="async-auth-registration",
                    issued_at=NOW,
                ),
                now=NOW,
            )
        )
        heartbeat = 0
        while not registration.done():
            heartbeat += 1
            await asyncio.sleep(0.005)

        receipt = await registration
        assert receipt.reporter_worker_id == worker.worker_id
        assert heartbeat >= 2
        assert store.connection_threads
        assert all(
            name.startswith("authentication-persistence") for name in store.connection_threads
        )

    asyncio.run(scenario())
