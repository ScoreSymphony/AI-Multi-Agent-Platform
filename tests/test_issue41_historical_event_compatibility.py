from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.domain import Event, new_id
from ai_multi_agent_platform.kernel import SqliteKernelRepository
from ai_multi_agent_platform.upgrade import (
    SUPPORTED_HISTORICAL_EVENT_SCHEMA_VERSIONS,
    FormatTranslatorRegistry,
    JsonMigrationHistoryStore,
    MigrationRegistry,
    PreflightRequest,
    UpgradePreflight,
    current_release_versions,
)


def test_supported_historical_event_versions_remain_interpretable(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "db").mkdir(parents=True)
    current = current_release_versions()
    preflight = UpgradePreflight(
        MigrationRegistry(),
        JsonMigrationHistoryStore.for_data_dir(data_dir),
        portable_translators=FormatTranslatorRegistry(current.portable_format),
        template_translators=FormatTranslatorRegistry(current.template_schema),
    )

    report = preflight.run(
        PreflightRequest(
            data_dir=data_dir,
            current=current,
            target=current,
            historical_event_schema_versions=SUPPORTED_HISTORICAL_EVENT_SCHEMA_VERSIONS,
        )
    )

    assert report.ok
    check = next(
        item for item in report.checks if item.code == "history.event_schema.supported"
    )
    assert check.details["versions"] == sorted(SUPPORTED_HISTORICAL_EVENT_SCHEMA_VERSIONS)


def test_supported_historical_events_round_trip_through_kernel_store(tmp_path: Path) -> None:
    repository = SqliteKernelRepository(tmp_path / "kernel.sqlite3")

    async def exercise() -> None:
        for schema_version in sorted(SUPPORTED_HISTORICAL_EVENT_SCHEMA_VERSIONS):
            stream_id = new_id("task")
            event = Event(
                event_type="task.historical_fixture",
                subject_type="task",
                subject_id=stream_id,
                correlation_id=stream_id,
                schema_version=schema_version,
                payload={"fixture": schema_version},
            )
            result = await repository.commit(
                stream_id=stream_id,
                expected_revision=0,
                events=(event,),
            )
            assert result.applied

            restored = await repository.read_events(stream_id)
            assert len(restored) == 1
            assert restored[0].id == event.id
            assert restored[0].schema_version == schema_version
            assert restored[0].payload == {"fixture": schema_version}

    asyncio.run(exercise())
