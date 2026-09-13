from __future__ import annotations

import asyncio
import importlib
import multiprocessing
import os
from collections.abc import Iterator

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import Event, OwnerRef, TaskStatus, new_id
from ai_multi_agent_platform.kernel import (
    CommandRecord,
    PlatformKernel,
    PostgresKernelRepository,
)
from ai_multi_agent_platform.testing import FakeOrchestrator
from ai_multi_agent_platform.testing.fakes import FakeLifecycleBackend

pytestmark = pytest.mark.integration


def _dsn() -> str:
    value = os.getenv("AI_PLATFORM_TEST_HA_POSTGRES_DSN")
    if value is None or not value.strip():
        pytest.skip("AI_PLATFORM_TEST_HA_POSTGRES_DSN is required for PostgreSQL HA persistence")
    return value


def _reset_tables(dsn: str) -> None:
    psycopg = importlib.import_module("psycopg")
    connection = psycopg.connect(dsn)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "TRUNCATE ai_map_kernel_events, ai_map_kernel_commands, ai_map_kernel_streams"
            )
        connection.commit()
    finally:
        connection.close()


@pytest.fixture(autouse=True)
def clean_kernel_tables() -> Iterator[None]:
    dsn = _dsn()
    repository = PostgresKernelRepository(dsn)
    asyncio.run(repository.initialize())
    _reset_tables(dsn)
    yield
    _reset_tables(dsn)


def _kernel(repository: PostgresKernelRepository) -> PlatformKernel:
    return PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )


def _event(stream_id: str, *, event_id: str | None = None) -> Event:
    return Event(
        id=event_id or new_id("event"),
        event_type="task.tested",
        subject_type="task",
        subject_id=stream_id,
        correlation_id=stream_id,
        owner_ref=OwnerRef(type="user", id="ha-test"),
        payload={"source": "postgres-ha-test"},
    )


@pytest.mark.asyncio
async def test_independent_repositories_share_canonical_kernel_state_and_idempotency() -> None:
    dsn = _dsn()
    first_repository = PostgresKernelRepository(dsn)
    second_repository = PostgresKernelRepository(dsn)
    await first_repository.initialize()
    await second_repository.initialize()
    first = _kernel(first_repository)
    second = _kernel(second_repository)

    created = await first.create_task(
        idempotency_key="cross-process-create",
        title="Canonical task",
        objective="Prove shared PostgreSQL state",
        owner_type="user",
        owner_id="ha-test",
    )
    replayed = await second.create_task(
        idempotency_key="cross-process-create",
        title="Ignored replay payload",
        objective="Ignored replay payload",
        owner_type="user",
        owner_id="ha-test",
    )

    assert replayed.task_id == created.task_id
    assert await second.history(created.task_id) == await first.history(created.task_id)

    ready = await first.ready_task(
        idempotency_key="cross-process-ready",
        task_id=created.task_id,
    )
    observed = await second.get_task(created.task_id)
    assert ready.status is TaskStatus.READY
    assert observed == ready
    assert created.task_id in await second_repository.list_stream_ids()


@pytest.mark.asyncio
async def test_concurrent_first_stream_commit_has_one_optimistic_revision_winner() -> None:
    dsn = _dsn()
    first = PostgresKernelRepository(dsn)
    second = PostgresKernelRepository(dsn)
    stream_id = new_id("task")

    results = await asyncio.gather(
        first.commit(stream_id=stream_id, expected_revision=0, events=(_event(stream_id),)),
        second.commit(stream_id=stream_id, expected_revision=0, events=(_event(stream_id),)),
        return_exceptions=True,
    )

    applied = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, Exception)]
    assert len(applied) == 1
    assert applied[0].applied is True
    assert applied[0].revision == 1
    assert len(failures) == 1
    assert isinstance(failures[0], ContractError)
    assert failures[0].code is ErrorCode.CONFLICT
    assert failures[0].retryable is True
    assert await first.revision(stream_id) == 1
    assert len(await first.read_events(stream_id)) == 1


@pytest.mark.asyncio
async def test_concurrent_duplicate_command_returns_the_canonical_winner() -> None:
    dsn = _dsn()
    first = PostgresKernelRepository(dsn)
    second = PostgresKernelRepository(dsn)
    first_stream = new_id("task")
    second_stream = new_id("task")
    first_event = _event(first_stream)
    second_event = _event(second_stream)
    first_command = CommandRecord(
        scope="task.create",
        idempotency_key="shared-key",
        operation="create_task",
        stream_id=first_stream,
        result_id=first_stream,
        event_id=first_event.id,
    )
    second_command = CommandRecord(
        scope="task.create",
        idempotency_key="shared-key",
        operation="create_task",
        stream_id=second_stream,
        result_id=second_stream,
        event_id=second_event.id,
    )

    results = await asyncio.gather(
        first.commit(
            stream_id=first_stream,
            expected_revision=0,
            events=(first_event,),
            command=first_command,
        ),
        second.commit(
            stream_id=second_stream,
            expected_revision=0,
            events=(second_event,),
            command=second_command,
        ),
    )

    winner = next(result for result in results if result.applied)
    replay = next(result for result in results if not result.applied)
    assert winner.command is not None
    assert replay.command == winner.command
    assert replay.revision == 1
    assert await first.list_stream_ids() == (winner.command.stream_id,)


def _child_append(dsn: str, stream_id: str, queue: multiprocessing.Queue[object]) -> None:
    async def run() -> None:
        repository = PostgresKernelRepository(dsn)
        event = _event(stream_id)
        result = await repository.commit(
            stream_id=stream_id,
            expected_revision=1,
            events=(event,),
        )
        queue.put((result.revision, event.id))

    asyncio.run(run())


def test_separate_process_can_append_to_parent_kernel_stream() -> None:
    dsn = _dsn()
    repository = PostgresKernelRepository(dsn)
    stream_id = new_id("task")
    first_event = _event(stream_id)
    asyncio.run(
        repository.commit(
            stream_id=stream_id,
            expected_revision=0,
            events=(first_event,),
        )
    )

    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=_child_append, args=(dsn, stream_id, queue))
    process.start()
    process.join(timeout=20)

    assert process.exitcode == 0
    revision, child_event_id = queue.get(timeout=5)
    assert revision == 2
    history = asyncio.run(repository.read_events(stream_id))
    assert tuple(event.id for event in history) == (first_event.id, child_event_id)


def test_existing_incompatible_schema_metadata_fails_closed() -> None:
    dsn = _dsn()
    psycopg = importlib.import_module("psycopg")
    connection = psycopg.connect(dsn)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ai_map_ha_schema_versions
                SET schema_version = 999, migration_revision = 'incompatible-test'
                WHERE component = 'kernel'
                """
            )
        connection.commit()
    finally:
        connection.close()

    try:
        with pytest.raises(ContractError) as raised:
            asyncio.run(PostgresKernelRepository(dsn).initialize())
        assert raised.value.code is ErrorCode.INVALID_CONFIGURATION
    finally:
        connection = psycopg.connect(dsn)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE ai_map_ha_schema_versions
                    SET schema_version = 1, migration_revision = 'kernel-v1'
                    WHERE component = 'kernel'
                    """
                )
            connection.commit()
        finally:
            connection.close()
