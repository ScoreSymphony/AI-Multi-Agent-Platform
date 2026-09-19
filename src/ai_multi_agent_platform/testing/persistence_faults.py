"""Deterministic persistence/filesystem fault helpers for integration/conformance tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Any


class SqliteWriteLock:
    """Hold one SQLite writer lock until the context exits."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._connection: sqlite3.Connection | None = None

    def __enter__(self) -> SqliteWriteLock:
        connection = sqlite3.connect(self._path, timeout=0.1, isolation_level=None)
        connection.execute("BEGIN IMMEDIATE")
        self._connection = connection
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        connection = self._connection
        self._connection = None
        if connection is not None:
            connection.rollback()
            connection.close()


class FailOnceFilesystemOperation:
    """Wrap a filesystem callable and inject exactly one deterministic OSError."""

    def __init__(
        self,
        delegate: Callable[..., Any],
        *,
        error: OSError | None = None,
        fail_on_call: int = 1,
    ) -> None:
        if fail_on_call < 1:
            raise ValueError("fail_on_call must be >= 1")
        self._delegate = delegate
        self._error = error or OSError("injected filesystem failure")
        self._fail_on_call = fail_on_call
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        if self.calls == self._fail_on_call:
            raise self._error
        return self._delegate(*args, **kwargs)


__all__ = ["FailOnceFilesystemOperation", "SqliteWriteLock"]
