from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path

from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.deployment.advanced_profiles import load_advanced_deployment_profile
from ai_multi_agent_platform.deployment.distributed_admin import DistributedWorkerAdmin
from ai_multi_agent_platform.security import LocalAuthenticationService, ScryptPasswordHasher
from ai_multi_agent_platform.security.sqlite_authentication import SqliteAuthenticationStore
from ai_multi_agent_platform.security.sqlite_authorization import SqliteLocalAuthorizationProvider

_PROFILE = Path("deploy/distributed/profiles/multi-local-workers.json")


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


class _SlowSQLiteAuthorizationProvider(SqliteLocalAuthorizationProvider):
    def __init__(self, path: Path) -> None:
        self.delay_seconds = 0.0
        self.connection_threads: list[str] = []
        super().__init__(path)

    def _connect(self) -> sqlite3.Connection:
        self.connection_threads.append(threading.current_thread().name)
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        return super()._connect()


def _authentication(store: SqliteAuthenticationStore) -> LocalAuthenticationService:
    return LocalAuthenticationService(
        store=store,
        password_hasher=ScryptPasswordHasher(n=2**10, r=8, p=1, maxmem=8 * 1024 * 1024),
    )


def test_distributed_worker_admin_offloads_policy_and_credential_sqlite(tmp_path: Path) -> None:
    async def scenario() -> None:
        profile = load_advanced_deployment_profile(_PROFILE)
        reporter_id = profile.nodes[0].reporter_worker_id
        assert reporter_id is not None

        authentication_store = _SlowSQLiteAuthenticationStore(tmp_path / "authentication.sqlite3")
        authentication = _authentication(authentication_store)
        authorization = _SlowSQLiteAuthorizationProvider(tmp_path / "authorization.sqlite3")
        admin = DistributedWorkerAdmin(profile, authentication, authorization)
        context = RequestContext(
            request_id="request-async-worker-admin",
            correlation_id="correlation-async-worker-admin",
        )

        authentication_store.connection_threads.clear()
        authorization.connection_threads.clear()
        authentication_store.delay_seconds = 0.08
        authorization.delay_seconds = 0.08

        provision = asyncio.create_task(admin.provision(context, reporter_id, {}))
        heartbeat = 0
        while not provision.done():
            heartbeat += 1
            await asyncio.sleep(0.005)

        result = await provision
        assert result["state"] == "provisioned"
        assert heartbeat >= 4
        assert authorization.connection_threads
        assert all(
            name.startswith("authorization-policy-persistence")
            for name in authorization.connection_threads
        )
        assert authentication_store.connection_threads
        assert all(
            name.startswith("authentication-persistence")
            for name in authentication_store.connection_threads
        )

    asyncio.run(scenario())


def test_distributed_worker_admin_rotation_uses_authentication_offload(tmp_path: Path) -> None:
    async def scenario() -> None:
        profile = load_advanced_deployment_profile(_PROFILE)
        reporter_id = profile.nodes[0].reporter_worker_id
        assert reporter_id is not None

        authentication_store = _SlowSQLiteAuthenticationStore(tmp_path / "authentication.sqlite3")
        authentication = _authentication(authentication_store)
        authorization = _SlowSQLiteAuthorizationProvider(tmp_path / "authorization.sqlite3")
        admin = DistributedWorkerAdmin(profile, authentication, authorization)
        context = RequestContext(
            request_id="request-async-worker-rotation",
            correlation_id="correlation-async-worker-rotation",
        )
        provisioned = await admin.provision(context, reporter_id, {})
        credential_id = provisioned["credential_id"]
        assert isinstance(credential_id, str)

        authentication_store.connection_threads.clear()
        authentication_store.delay_seconds = 0.08
        rotation = asyncio.create_task(
            admin.rotate(context, reporter_id, {"credential_id": credential_id})
        )
        heartbeat = 0
        while not rotation.done():
            heartbeat += 1
            await asyncio.sleep(0.005)

        result = await rotation
        assert result["state"] == "rotated"
        assert result["previous_credential_id"] == credential_id
        assert heartbeat >= 2
        assert authentication_store.connection_threads
        assert all(
            name.startswith("authentication-persistence")
            for name in authentication_store.connection_threads
        )

    asyncio.run(scenario())
