"""Cancellation-resistant settlement helpers for Template transactions."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable


async def settle_awaitable[T](
    operation: Awaitable[T],
) -> tuple[T | None, BaseException | None]:
    """Finish one cleanup operation despite repeated caller cancellation.

    The caller remains responsible for re-raising its primary failure. This helper only ensures
    that a cancellation arriving while compensation is running cannot abandon the cleanup task.
    Cleanup failures are returned rather than raised so they cannot accidentally replace the
    primary failure before the transaction owner has applied its settlement policy.
    """

    worker: asyncio.Future[T] = asyncio.ensure_future(operation)
    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            continue
    try:
        return worker.result(), None
    # error-boundary: allow-broad-catch=cleanup secondary settlement failure
    except (Exception, asyncio.CancelledError) as exc:
        return None, exc
