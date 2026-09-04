from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import Event, RunStatus, TaskStatus, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository
from ai_multi_agent_platform.portability import (
    TASK_HISTORY_RESOURCE_TYPE,
    IdPolicy,
    ImportExecutor,
    ImportMutationRegistry,
    ImportPreviewService,
    InMemoryHistoricalTaskArchiveRepository,
    PackageProvenance,
    ResourceSerializerRegistry,
    TaskHistoryImportMutationHandler,
    build_package,
    register_task_history_portability_codec,
    snapshot_task_history,
)

_NOW = datetime(2026, 9, 4, 8, 0, tzinfo=UTC)


def _event(
    *,
    event_type: str,
    subject_type: str,
    subject_id: str,
    task_id: str,
    offset: int,
    payload: dict[str, object] | None = None,
    trace_id: str | None = None,
) -> Event:
    return Event(
        id=new_id("event"),
        event_type=event_type,
        subject_type=subject_type,
        subject_id=subject_id,
        correlation_id=task_id,
        occurred_at=_NOW + timedelta(seconds=offset),
        trace_id=trace_id,
        payload=payload or {},
    )


def _terminal_stream(task_id: str, run_id: str) -> tuple[Event, ...]:
    owner = {"owner_type": "user", "owner_id": "user-history"}
    return (
        _event(
            event_type="task.created",
            subject_type="task",
            subject_id=task_id,
            task_id=task_id,
            offset=0,
            payload={**owner, "title": "Archived task", "objective": "Portable history"},
        ),
        _event(
            event_type="task.ready",
            subject_type="task",
            subject_id=task_id,
            task_id=task_id,
            offset=1,
        ),
        _event(
            event_type="task.running",
            subject_type="task",
            subject_id=task_id,
            task_id=task_id,
            offset=2,
        ),
        _event(
            event_type="run.created",
            subject_type="run",
            subject_id=run_id,
            task_id=task_id,
            offset=3,
            payload={
                **owner,
                "task_id": task_id,
                "subject_type": "task",
                "subject_id": task_id,
                "attempt": 1,
            },
        ),
        _event(
            event_type="run.starting",
            subject_type="run",
            subject_id=run_id,
            task_id=task_id,
            offset=4,
        ),
        _event(
            event_type="run.running",
            subject_type="run",
            subject_id=run_id,
            task_id=task_id,
            offset=5,
            payload={"backend_ref": "opaque-executor-job-42"},
            trace_id="trace-live-only",
        ),
        _event(
            event_type="run.succeeded",
            subject_type="run",
            subject_id=run_id,
            task_id=task_id,
            offset=6,
            payload={"output": {"answer": "done"}},
            trace_id="trace-live-only",
        ),
        _event(
            event_type="task.succeeded",
            subject_type="task",
            subject_id=task_id,
            task_id=task_id,
            offset=7,
            trace_id="trace-live-only",
        ),
    )


def _source_history() -> tuple[InMemoryKernelRepository, str, str]:
    source = InMemoryKernelRepository()
    task_id = new_id("task")
    run_id = new_id("run")
    asyncio.run(
        source.commit(
            stream_id=task_id,
            expected_revision=0,
            events=_terminal_stream(task_id, run_id),
        )
    )
    return source, task_id, run_id


def test_task_history_snapshot_is_terminal_and_strips_live_execution_fields() -> None:
    source, task_id, run_id = _source_history()

    snapshot = asyncio.run(snapshot_task_history(source, task_id))

    assert snapshot.task.status is TaskStatus.SUCCEEDED
    assert snapshot.run_ids == (run_id,)
    assert snapshot.runs[0].run.status is RunStatus.SUCCEEDED
    assert snapshot.runs[0].run.trace_id is None
    assert snapshot.runs[0].run.worker_id is None
    assert snapshot.runs[0].output == {"answer": "done"}
    assert all(event.trace_id is None for event in snapshot.events)

    serializers = ResourceSerializerRegistry()
    register_task_history_portability_codec(serializers)
    resource = serializers.serialize(TASK_HISTORY_RESOURCE_TYPE, snapshot)

    assert resource.id_policy is IdPolicy.HISTORICAL_PRESERVE
    assert resource.payload["historical_only"] is True
    encoded_runs = resource.payload["runs"]
    assert isinstance(encoded_runs, list)
    assert "backend_ref" not in encoded_runs[0]


def test_task_history_import_remains_outside_live_kernel() -> None:
    source, task_id, _ = _source_history()
    snapshot = asyncio.run(snapshot_task_history(source, task_id))

    serializers = ResourceSerializerRegistry()
    register_task_history_portability_codec(serializers)
    resource = serializers.serialize(TASK_HISTORY_RESOURCE_TYPE, snapshot)
    package = build_package(
        source_platform_version="test",
        resources=(resource,),
        provenance=PackageProvenance(source="task-history-test"),
    )
    preview = ImportPreviewService(
        resource_exists=lambda _kind, _resource_id: False,
        dependency_available=lambda _requirement: True,
    ).preview(package)
    assert preview.ready is True

    archive = InMemoryHistoricalTaskArchiveRepository()
    mutations = ImportMutationRegistry()
    mutations.register(TaskHistoryImportMutationHandler(archive))
    live_kernel = InMemoryKernelRepository()

    result = asyncio.run(ImportExecutor(serializers, mutations).execute(package, preview))

    assert result.resources[0].target_id == task_id
    archived = asyncio.run(archive.get(task_id))
    assert archived.task.status is TaskStatus.SUCCEEDED
    assert archived.task.id == task_id
    assert archived.run_ids == snapshot.run_ids
    assert asyncio.run(live_kernel.list_stream_ids()) == ()


def test_active_task_history_export_is_rejected() -> None:
    source = InMemoryKernelRepository()
    task_id = new_id("task")
    created = _event(
        event_type="task.created",
        subject_type="task",
        subject_id=task_id,
        task_id=task_id,
        offset=0,
        payload={
            "owner_type": "user",
            "owner_id": "user-history",
            "title": "Still active",
            "objective": "Must not become portable history",
        },
    )
    asyncio.run(
        source.commit(
            stream_id=task_id,
            expected_revision=0,
            events=(created,),
        )
    )

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(snapshot_task_history(source, task_id))

    assert exc_info.value.code is ErrorCode.CONFLICT
    assert "cannot be exported" in exc_info.value.message


def test_task_history_with_non_terminal_run_is_rejected() -> None:
    source = InMemoryKernelRepository()
    task_id = new_id("task")
    run_id = new_id("run")
    events = _terminal_stream(task_id, run_id)
    without_run_terminal = tuple(event for event in events if event.event_type != "run.succeeded")
    asyncio.run(
        source.commit(
            stream_id=task_id,
            expected_revision=0,
            events=without_run_terminal,
        )
    )

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(snapshot_task_history(source, task_id))

    assert exc_info.value.code is ErrorCode.CONFLICT
    assert "non-terminal Run" in exc_info.value.message
