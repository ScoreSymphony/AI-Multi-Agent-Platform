from __future__ import annotations

from pathlib import Path
from typing import Any

from pytest import MonkeyPatch

from ai_multi_agent_platform.backup import service


class _Cursor:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row


class _FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        # sqlite3.Connection context management commits/rolls back but does not close.
        return None

    def backup(self, _target: _FakeConnection) -> None:
        return None

    def execute(self, statement: str, *_args: object) -> _Cursor:
        if statement == "PRAGMA integrity_check":
            return _Cursor(("ok",))
        if statement == "PRAGMA foreign_key_check":
            return _Cursor(None)
        if statement == "PRAGMA journal_mode":
            return _Cursor(("wal",))
        if statement == "PRAGMA wal_checkpoint(TRUNCATE)":
            return _Cursor((0, 0, 0))
        raise AssertionError(f"unexpected SQL in lifecycle regression test: {statement}")

    def close(self) -> None:
        self.closed = True


def test_sqlite_snapshot_closes_connections_before_sidecar_cleanup(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_connection = _FakeConnection()
    destination_connection = _FakeConnection()
    connections = iter((source_connection, destination_connection))
    cleanup_observations: list[tuple[bool, bool]] = []

    def connect(*_args: object, **_kwargs: object) -> _FakeConnection:
        return next(connections)

    def remove_sidecars(_path: Path) -> None:
        cleanup_observations.append((source_connection.closed, destination_connection.closed))

    monkeypatch.setattr(service.sqlite3, "connect", connect)
    monkeypatch.setattr(service, "_remove_sqlite_sidecars", remove_sidecars)

    service._sqlite_snapshot(tmp_path / "source.sqlite3", tmp_path / "snapshot.sqlite3")

    assert cleanup_observations == [(True, True)]
