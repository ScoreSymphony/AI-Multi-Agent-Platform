from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path
from typing import cast

import pytest

from ai_multi_agent_platform.connectors import (
    Connection,
    ExternalNativeReference,
    ExternalResourceReference,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.repositories import (
    AsyncSqliteRepositoryBindingCatalog,
    InMemoryRepositoryBindingCatalog,
    RepositoryBinding,
    RepositoryBindingCatalog,
    RepositoryBindingRecord,
    RepositoryCallContext,
    RepositoryConnection,
    RepositoryManagementService,
    RepositoryProvider,
    RepositoryReference,
    RepositoryRegistry,
    RepositoryVisibility,
    SqliteRepositoryBindingCatalog,
)
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)


def _record(
    *,
    connection_id: str | None = None,
    native_id: str = "fixture",
) -> RepositoryBindingRecord:
    resolved_connection_id = connection_id or new_id("connection")
    reference = RepositoryReference(
        external_resource=ExternalResourceReference(
            id=new_id("external_resource"),
            connection_id=resolved_connection_id,
            resource_type="repository",
            native_reference=ExternalNativeReference(namespace="test", native_id=native_id),
        ),
        default_branch="main",
        visibility=RepositoryVisibility.LOCAL,
    )
    return RepositoryBindingRecord(
        reference=reference,
        provider_id="local-git",
        local=True,
        adapter_configuration={"root": f"managed/{native_id}"},
    )


def test_in_memory_and_sqlite_catalogs_share_async_contract(tmp_path: Path) -> None:
    async def scenario() -> None:
        catalogs: tuple[RepositoryBindingCatalog, ...] = (
            InMemoryRepositoryBindingCatalog(),
            AsyncSqliteRepositoryBindingCatalog(
                SqliteRepositoryBindingCatalog(tmp_path / "bindings.sqlite3")
            ),
        )
        connection_id = new_id("connection")
        records = (
            _record(connection_id=connection_id, native_id="one"),
            _record(connection_id=connection_id, native_id="two"),
        )

        for catalog in catalogs:
            for record in records:
                assert await catalog.save(record) == record
            assert await catalog.get(records[0].repository_id) == records[0]
            assert await catalog.list(connection_id=connection_id) == tuple(
                sorted(records, key=lambda record: record.repository_id)
            )
            await catalog.delete(records[0].repository_id)
            with pytest.raises(ContractError) as missing:
                await catalog.get(records[0].repository_id)
            assert missing.value.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_sqlite_catalog_keeps_event_loop_responsive_and_connections_on_worker_thread(
    tmp_path: Path,
) -> None:
    class SlowCatalog(SqliteRepositoryBindingCatalog):
        def __init__(self, path: Path) -> None:
            self.connect_threads: list[int] = []
            self.operation_thread: int | None = None
            super().__init__(path)
            self.connect_threads.clear()

        def _connect(self) -> sqlite3.Connection:
            self.connect_threads.append(threading.get_ident())
            return super()._connect()

        def list(
            self,
            *,
            connection_id: str | None = None,
        ) -> tuple[RepositoryBindingRecord, ...]:
            self.operation_thread = threading.get_ident()
            time.sleep(0.08)
            return super().list(connection_id=connection_id)

    async def scenario() -> None:
        main_thread = threading.get_ident()
        synchronous = SlowCatalog(tmp_path / "responsive.sqlite3")
        catalog = AsyncSqliteRepositoryBindingCatalog(synchronous)
        heartbeat_count = 0

        operation = asyncio.create_task(catalog.list())
        while not operation.done():
            heartbeat_count += 1
            await asyncio.sleep(0.005)
        await operation

        assert heartbeat_count >= 5
        assert synchronous.operation_thread is not None
        assert synchronous.operation_thread != main_thread
        assert synchronous.connect_threads
        assert all(thread_id != main_thread for thread_id in synchronous.connect_threads)

    asyncio.run(scenario())


def test_sqlite_catalog_bounds_concurrent_worker_operations(tmp_path: Path) -> None:
    class MeasuringCatalog(SqliteRepositoryBindingCatalog):
        def __init__(self, path: Path) -> None:
            self._measurement_lock = threading.Lock()
            self.active = 0
            self.maximum_active = 0
            self.calls = 0
            super().__init__(path)

        def list(
            self,
            *,
            connection_id: str | None = None,
        ) -> tuple[RepositoryBindingRecord, ...]:
            with self._measurement_lock:
                self.active += 1
                self.calls += 1
                self.maximum_active = max(self.maximum_active, self.active)
            try:
                time.sleep(0.04)
                return super().list(connection_id=connection_id)
            finally:
                with self._measurement_lock:
                    self.active -= 1

    async def scenario() -> None:
        synchronous = MeasuringCatalog(tmp_path / "bounded.sqlite3")
        catalog = AsyncSqliteRepositoryBindingCatalog(synchronous, max_concurrency=2)
        await asyncio.gather(*(catalog.list() for _ in range(8)))
        assert synchronous.calls == 8
        assert synchronous.maximum_active <= 2

    asyncio.run(scenario())


def test_sqlite_catalog_defers_repeated_cancellation_until_write_settles(tmp_path: Path) -> None:
    class BlockingCatalog(SqliteRepositoryBindingCatalog):
        def __init__(self, path: Path) -> None:
            self.started = threading.Event()
            self.release = threading.Event()
            super().__init__(path)

        def save(self, record: RepositoryBindingRecord) -> RepositoryBindingRecord:
            self.started.set()
            if not self.release.wait(timeout=2):
                raise RuntimeError("test write was not released")
            return super().save(record)

    async def scenario() -> None:
        synchronous = BlockingCatalog(tmp_path / "cancel.sqlite3")
        catalog = AsyncSqliteRepositoryBindingCatalog(synchronous, max_concurrency=1)
        record = _record(native_id="cancelled-write")
        write = asyncio.create_task(catalog.save(record))
        assert await asyncio.to_thread(synchronous.started.wait, 1)

        write.cancel()
        write.cancel()
        await asyncio.sleep(0.02)
        assert not write.done()

        synchronous.release.set()
        with pytest.raises(asyncio.CancelledError):
            await write
        assert synchronous.get(record.repository_id) == record

    asyncio.run(scenario())


def test_sqlite_catalog_maps_busy_errors_to_retryable_transient_failure(tmp_path: Path) -> None:
    class LockedCatalog(SqliteRepositoryBindingCatalog):
        def list(
            self,
            *,
            connection_id: str | None = None,
        ) -> tuple[RepositoryBindingRecord, ...]:
            del connection_id
            try:
                raise sqlite3.OperationalError("database is locked")
            except sqlite3.OperationalError as exc:
                raise ContractError(ErrorCode.BACKEND_ERROR, "catalog read failed") from exc

    async def scenario() -> None:
        catalog = AsyncSqliteRepositoryBindingCatalog(LockedCatalog(tmp_path / "locked.sqlite3"))
        with pytest.raises(ContractError) as failure:
            await catalog.list()
        assert failure.value.code is ErrorCode.TRANSIENT_FAILURE
        assert failure.value.retryable is True

    asyncio.run(scenario())


def test_management_hides_binding_until_durable_save_succeeds(tmp_path: Path) -> None:
    class FailingCatalog(InMemoryRepositoryBindingCatalog):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def save(self, record: RepositoryBindingRecord) -> RepositoryBindingRecord:
            self.started.set()
            await self.release.wait()
            raise ContractError(ErrorCode.BACKEND_ERROR, "injected persistence failure")

    class Provider:
        provider_id = "local-git"

    async def scenario() -> None:
        owner_id = new_id("user")
        project_id = new_id("project")
        record = _record(native_id="pending")
        connection = RepositoryConnection(
            connection=Connection(
                id=record.connection_id,
                connector_type_id="local-git",
                connector_version="1.0",
                owner_type="user",
                owner_id=owner_id,
                display_name="Pending repository",
                project_id=project_id,
            ),
            provider_id="local-git",
            local=True,
        )
        binding = RepositoryBinding(
            connection,
            record.reference,
            cast(RepositoryProvider, Provider()),
        )
        registry = RepositoryRegistry()
        catalog = FailingCatalog()
        authorization = AuthorizationGate(
            LocalAuthorizationProvider(
                (
                    LocalPrincipalPolicy(
                        principal_ref=owner_id,
                        actor_types=frozenset({ActorType.HUMAN}),
                        allowed_actions=frozenset({AuthorizationAction.CREATE}),
                        resource_types=frozenset({ResourceType.GENERIC}),
                        project_ids=frozenset({project_id}),
                    ),
                )
            )
        )
        service = RepositoryManagementService(
            registry,
            catalog,
            authorization,
            managed_local_root=tmp_path / "managed",
        )
        context = RepositoryCallContext(
            operation=OperationContext(
                correlation_id="issue-892-pending-binding",
                owner_type="user",
                owner_id=owner_id,
                project_id=project_id,
            ),
            actor_ref=owner_id,
        )

        attach = asyncio.create_task(service.attach_binding(binding, context))
        await catalog.started.wait()
        with pytest.raises(ContractError) as pending:
            registry.resolve(record.repository_id)
        assert pending.value.code is ErrorCode.NOT_FOUND

        catalog.release.set()
        with pytest.raises(ContractError) as failure:
            await attach
        assert failure.value.code is ErrorCode.BACKEND_ERROR
        with pytest.raises(ContractError) as rolled_back:
            registry.resolve(record.repository_id)
        assert rolled_back.value.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())
