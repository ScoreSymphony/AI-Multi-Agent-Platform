"""Read-only catalog adapter over the durable #872 SQLite composition store."""

from __future__ import annotations

import sqlite3

from .models import CodingBatch
from .sqlite_store import SqliteCodingBatchStore


class SqliteCodingBatchCatalog:
    """Enumerate persisted batches without creating a second orchestration store.

    `SqliteCodingBatchStore` remains the write/persistence authority. This adapter only reads the
    store's primary keys and resolves each payload through the store's public `get` method.
    """

    def __init__(self, store: SqliteCodingBatchStore) -> None:
        self._store = store

    def get(self, batch_id: str) -> CodingBatch | None:
        return self._store.get(batch_id)

    def list_batches(self) -> tuple[CodingBatch, ...]:
        with sqlite3.connect(self._store.path) as connection:
            rows = connection.execute(
                "SELECT batch_id FROM coding_batches ORDER BY rowid ASC"
            ).fetchall()
        batches: list[CodingBatch] = []
        for (batch_id,) in rows:
            batch = self._store.get(str(batch_id))
            if batch is None:
                raise RuntimeError("coding-batch catalog observed a missing persisted batch")
            batches.append(batch)
        return tuple(batches)


__all__ = ["SqliteCodingBatchCatalog"]
