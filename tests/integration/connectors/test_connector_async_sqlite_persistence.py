from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_multi_agent_platform.connectors import (
    Connection,
    ConnectionStatus,
    ConnectorDefinition,
    ExternalNativeReference,
    ExternalResourceReference,
    InMemoryConnectorRepository,
    SqliteConnectorRepository,
    SyncCheckpoint,
    SyncStatus,
    connector_definition_id,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import HealthStatus
from ai_multi_agent_platform.domain import new_id


def _definition() -> ConnectorDefinition:
    return ConnectorDefinition(
        id=connector_definition_id("fixture.async-connector", "1.0"),
        connector_type_id="fixture.async-connector",
        name="Async SQLite fixture",
        version="1.0",
        supported_operations=("sync",),
        resource_types=("record",),
    )


def _connection(*, connection_id: str | None = None) -> Connection:
    now = datetime.now(UTC)
    return Connection(
        id=connection_id or new_id("connection"),
        connector_type_id="fixture.async-connector",
        connector_version="1.0",
        owner_type="user",
        owner_id="issue-892-user",
        display_name="Async SQLite connector",
        requested_scopes=("read",),
        granted_scopes=("read",),
        enabled=True,
        status=ConnectionStatus.READY,
        health=HealthStatus.HEALTHY,
        created_at=now,
        updated_at=now,
    )


def _resource(
    connection_id: str,
    native_id: str,
    *,
    resource_id: str | None = None,
) -> ExternalResourceReference:
    return ExternalResourceReference(
        id=resource_id or new_id("external_resource"),
        connection_id=connection_id,
        resource_type="record",
        native_reference=ExternalNativeReference(
            namespace="fixture.records",
            native_id=native_id,
        ),
        revision="1",
        provenance={"source": "fixture"},
        metadata={"label": native_id},
    )


def _checkpoint(connection_id: str) -> SyncCheckpoint:
    now = datetime.now(UTC)
    return SyncCheckpoint(
        connection_id=connection_id,
        stream="records",
        cursor="cursor-2",
        last_successful_sync=now,
        remote_revision="r2",
        status=SyncStatus.SUCCEEDED,
        dedupe_mapping={"alpha": "event-alpha"},
        updated_at=now,
    )


async def _wait_for_thread_event(event: threading.Event) -> None:
    for _ in range(200):
        if event.is_set():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("worker did not reach expected blocking point")


def test_connector_sqlite_runtime_keeps_event_loop_responsive_and_connections_in_workers(
    tmp_path: Path,
) -> None:
    main_thread = threading.get_ident()

    class SlowConnectorRepository(SqliteConnectorRepository):
        track_runtime = False
        connection_threads: list[int] = []

        def _connect(self) -> sqlite3.Connection:
            if self.track_runtime:
                self.connection_threads.append(threading.get_ident())
                time.sleep(0.12)
            return super()._connect()

    async def scenario() -> None:
        repository = SlowConnectorRepository(tmp_path / "responsive.sqlite3")
        repository.track_runtime = True
        heartbeat = 0

        async def ticker(task: asyncio.Task[tuple[Connection, ...]]) -> None:
            nonlocal heartbeat
            while not task.done():
                heartbeat += 1
                await asyncio.sleep(0.005)

        operation = asyncio.create_task(repository.list_connections())
        ticker_task = asyncio.create_task(ticker(operation))
        assert await operation == ()
        await ticker_task

        assert heartbeat >= 5
        assert repository.connection_threads
        assert all(thread_id != main_thread for thread_id in repository.connection_threads)
        assert repository.schema_version == 1

    asyncio.run(scenario())


def test_connector_sqlite_runtime_bounds_concurrent_worker_operations(tmp_path: Path) -> None:
    active = 0
    maximum_active = 0
    counter_lock = threading.Lock()

    class MeasuredConnectorRepository(SqliteConnectorRepository):
        measure_runtime = False

        def _connect(self) -> sqlite3.Connection:
            nonlocal active, maximum_active
            if not self.measure_runtime:
                return super()._connect()
            with counter_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                time.sleep(0.08)
                return super()._connect()
            finally:
                with counter_lock:
                    active -= 1

    async def scenario() -> None:
        repository = MeasuredConnectorRepository(
            tmp_path / "bounded.sqlite3",
            max_concurrency=2,
        )
        repository.measure_runtime = True
        await asyncio.gather(*(repository.list_connections() for _ in range(8)))

    asyncio.run(scenario())
    assert maximum_active == 2


def test_connector_sqlite_runtime_does_not_consume_shared_default_executor(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingConnectorRepository(SqliteConnectorRepository):
        block_runtime = False

        def _connect(self) -> sqlite3.Connection:
            if self.block_runtime:
                entered.set()
                if not release.wait(timeout=5):
                    raise AssertionError("test did not release Connector worker")
            return super()._connect()

    async def scenario() -> None:
        repository = BlockingConnectorRepository(
            tmp_path / "executor-isolation.sqlite3",
            max_concurrency=1,
        )
        repository.block_runtime = True
        blocked = asyncio.create_task(repository.list_connections())
        await _wait_for_thread_event(entered)

        unrelated = await asyncio.wait_for(asyncio.to_thread(lambda: "default-executor-ok"), 1)
        assert unrelated == "default-executor-ok"

        release.set()
        assert await blocked == ()

    asyncio.run(scenario())


def test_connector_sqlite_runtime_defers_repeated_cancellation_until_write_settles(
    tmp_path: Path,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    database = tmp_path / "cancel.sqlite3"

    class BlockingConnectorRepository(SqliteConnectorRepository):
        block_runtime = False

        def _connect(self) -> sqlite3.Connection:
            if self.block_runtime:
                entered.set()
                if not release.wait(timeout=5):
                    raise AssertionError("test did not release Connector write")
            return super()._connect()

    async def scenario() -> None:
        repository = BlockingConnectorRepository(database)
        connection = _connection()
        repository.block_runtime = True
        task = asyncio.create_task(repository.save_connection(connection))
        await _wait_for_thread_event(entered)

        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0.03)
        assert not task.done()

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

        restarted = SqliteConnectorRepository(database)
        assert await restarted.get_connection(connection.id) == connection

    asyncio.run(scenario())


def test_connector_sqlite_runtime_maps_busy_to_retryable_transient_failure(
    tmp_path: Path,
) -> None:
    class BusyConnectorRepository(SqliteConnectorRepository):
        fail_runtime = False

        def _connect(self) -> sqlite3.Connection:
            if self.fail_runtime:
                raise sqlite3.OperationalError("database is locked")
            return super()._connect()

    async def scenario() -> None:
        repository = BusyConnectorRepository(tmp_path / "busy.sqlite3")
        repository.fail_runtime = True
        with pytest.raises(ContractError) as failure:
            await repository.list_connections()
        assert failure.value.code is ErrorCode.TRANSIENT_FAILURE
        assert failure.value.retryable is True

    asyncio.run(scenario())


def test_connector_sqlite_runtime_rolls_back_failed_resource_rebuild(tmp_path: Path) -> None:
    database = tmp_path / "rollback.sqlite3"
    connection = _connection()
    alpha = _resource(connection.id, "alpha")
    beta = _resource(connection.id, "beta")

    async def seed() -> None:
        repository = SqliteConnectorRepository(database)
        await repository.save_connection(connection)
        await repository.save_external_resource(alpha)
        await repository.save_external_resource(beta)

    asyncio.run(seed())

    with sqlite3.connect(database) as raw:
        raw.execute(
            """
            CREATE TRIGGER reject_exploding_resource
            BEFORE INSERT ON external_resources
            WHEN NEW.native_id = 'explode'
            BEGIN
                SELECT RAISE(ABORT, 'forced replacement failure');
            END
            """
        )

    async def fail_and_verify() -> None:
        repository = SqliteConnectorRepository(database)
        with pytest.raises(ContractError) as failure:
            await repository.replace_external_resources(
                connection.id,
                (_resource(connection.id, "explode"),),
            )
        assert failure.value.code is ErrorCode.BACKEND_ERROR

        restarted = SqliteConnectorRepository(database)
        persisted = await restarted.list_external_resources(connection_id=connection.id)
        assert {item.id for item in persisted} == {alpha.id, beta.id}

    asyncio.run(fail_and_verify())


def test_connector_repository_contract_parity_and_restart_durability(tmp_path: Path) -> None:
    database = tmp_path / "parity.sqlite3"
    definition = _definition()
    connection = _connection()
    resource = _resource(connection.id, "alpha")
    checkpoint = _checkpoint(connection.id)

    async def exercise(
        repository: InMemoryConnectorRepository | SqliteConnectorRepository,
    ) -> tuple[
        tuple[ConnectorDefinition, ...],
        tuple[Connection, ...],
        tuple[ExternalResourceReference, ...],
        SyncCheckpoint | None,
    ]:
        await repository.save_definition(definition)
        await repository.save_connection(connection)
        await repository.save_external_resource(resource)
        await repository.save_checkpoint(checkpoint)
        return (
            await repository.list_definitions(),
            await repository.list_connections(),
            await repository.list_external_resources(connection_id=connection.id),
            await repository.get_checkpoint(connection.id, checkpoint.stream),
        )

    async def scenario() -> None:
        memory_snapshot = await exercise(InMemoryConnectorRepository())
        sqlite_snapshot = await exercise(SqliteConnectorRepository(database))
        assert sqlite_snapshot == memory_snapshot

        restarted = SqliteConnectorRepository(database)
        restart_snapshot = (
            await restarted.list_definitions(),
            await restarted.list_connections(),
            await restarted.list_external_resources(connection_id=connection.id),
            await restarted.get_checkpoint(connection.id, checkpoint.stream),
        )
        assert restart_snapshot == memory_snapshot

    asyncio.run(scenario())
