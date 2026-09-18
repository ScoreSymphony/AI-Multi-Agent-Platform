from __future__ import annotations

import asyncio
import shutil
import sqlite3
from collections import namedtuple
from pathlib import Path

import ai_multi_agent_platform.deployment.persistence_health as persistence_health
from ai_multi_agent_platform.backup.inventory import SINGLE_NODE_DURABLE_STORES
from ai_multi_agent_platform.contracts import HealthStatus
from ai_multi_agent_platform.deployment.config import SingleNodeConfig
from ai_multi_agent_platform.deployment.persistence_health import (
    SingleNodePersistenceHealthProvider,
)
from ai_multi_agent_platform.observability import InMemoryExporter, Telemetry
from ai_multi_agent_platform.testing import FailOnceFilesystemOperation

_DiskUsage = namedtuple("_DiskUsage", "total used free")


def _seed_required_stores(config: SingleNodeConfig) -> None:
    config.prepare_directories()
    for spec in SINGLE_NODE_DURABLE_STORES:
        if not spec.required:
            continue
        path = config.data_dir / spec.path
        path.parent.mkdir(parents=True, exist_ok=True)
        if spec.kind == "sqlite":
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS persistence_health_fixture (id INTEGER PRIMARY KEY)"
                )
        else:
            path.write_text("{}\n", encoding="utf-8")


def _codes(provider: SingleNodePersistenceHealthProvider) -> set[str]:
    return {str(item["code"]) for item in provider.health_diagnostics}


def test_persistence_health_accepts_complete_writable_single_node_state(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "data", secure_cookie=False)
        _seed_required_stores(config)
        provider = SingleNodePersistenceHealthProvider(config, minimum_free_bytes=0)

        assert await provider.health() is HealthStatus.HEALTHY
        assert provider.health_diagnostics == ()
        assert provider.descriptor.resources["free_space_state"] == "healthy"

    asyncio.run(scenario())


def test_persistence_health_fails_closed_when_data_root_disappears(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "data", secure_cookie=False)
        _seed_required_stores(config)
        shutil.rmtree(config.data_dir)

        provider = SingleNodePersistenceHealthProvider(config, minimum_free_bytes=0)

        assert await provider.health() is HealthStatus.UNAVAILABLE
        assert "persistence_path_unavailable" in _codes(provider)

    asyncio.run(scenario())


def test_persistence_health_detects_atomic_write_or_rename_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "data", secure_cookie=False)
        _seed_required_stores(config)
        failure = FailOnceFilesystemOperation(persistence_health.os.replace)
        monkeypatch.setattr(persistence_health.os, "replace", failure)
        provider = SingleNodePersistenceHealthProvider(config, minimum_free_bytes=0)

        assert await provider.health() is HealthStatus.UNAVAILABLE
        assert "persistence_path_unwritable" in _codes(provider)
        assert failure.calls >= 1

    asyncio.run(scenario())


def test_persistence_health_blocks_critical_space_and_degrades_low_space(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "data", secure_cookie=False)
        _seed_required_stores(config)
        provider = SingleNodePersistenceHealthProvider(
            config,
            minimum_free_bytes=64,
            warning_free_bytes=256,
        )

        monkeypatch.setattr(
            persistence_health.shutil,
            "disk_usage",
            lambda _path: _DiskUsage(1024, 1023, 1),
        )
        assert await provider.health() is HealthStatus.UNAVAILABLE
        assert "free_space_critical" in _codes(provider)

        monkeypatch.setattr(
            persistence_health.shutil,
            "disk_usage",
            lambda _path: _DiskUsage(1024, 900, 124),
        )
        assert await provider.health() is HealthStatus.DEGRADED
        assert "free_space_low" in _codes(provider)

    asyncio.run(scenario())


def test_persistence_health_rejects_missing_or_corrupt_required_store(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "data", secure_cookie=False)
        _seed_required_stores(config)
        kernel = config.database_dir / "kernel.sqlite3"
        kernel.unlink()

        provider = SingleNodePersistenceHealthProvider(config, minimum_free_bytes=0)
        assert await provider.health() is HealthStatus.UNAVAILABLE
        assert "required_store_missing" in _codes(provider)

        kernel.write_bytes(b"not-a-sqlite-database")
        assert await provider.health() is HealthStatus.UNAVAILABLE
        assert "sqlite_integrity_failed" in _codes(provider)

    asyncio.run(scenario())


def test_optional_corrupt_store_degrades_without_claiming_required_outage(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "data", secure_cookie=False)
        _seed_required_stores(config)
        (config.database_dir / "applications.sqlite3").write_bytes(b"broken")

        provider = SingleNodePersistenceHealthProvider(config, minimum_free_bytes=0)

        assert await provider.health() is HealthStatus.DEGRADED
        assert "sqlite_integrity_failed" in _codes(provider)

    asyncio.run(scenario())


def test_persistence_health_emits_unavailable_and_recovery_transitions(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "data", secure_cookie=False)
        _seed_required_stores(config)
        missing = config.database_dir / "kernel.sqlite3"
        missing.unlink()
        exporter = InMemoryExporter()
        provider = SingleNodePersistenceHealthProvider(
            config,
            telemetry=Telemetry(exporter),
            minimum_free_bytes=0,
        )

        assert await provider.health() is HealthStatus.UNAVAILABLE
        with sqlite3.connect(missing) as connection:
            connection.execute("CREATE TABLE recovered (id INTEGER PRIMARY KEY)")
        assert await provider.health() is HealthStatus.HEALTHY

        names = [record.event_name for record in exporter.logs]
        assert "persistence.unavailable" in names
        assert "persistence.recovered" in names

    asyncio.run(scenario())
