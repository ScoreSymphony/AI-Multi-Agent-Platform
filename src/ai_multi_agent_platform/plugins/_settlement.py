from __future__ import annotations

import asyncio
from collections.abc import Awaitable


async def settle_awaitable[T](operation: Awaitable[T]) -> tuple[T | None, BaseException | None]:
    """Let an owned plugin-lifecycle settlement finish despite repeated caller cancellation."""

    worker = asyncio.ensure_future(operation)
    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            continue
    try:
        return worker.result(), None
    # error-boundary: allow-broad-catch=cleanup report settlement failure to the primary owner
    except (Exception, asyncio.CancelledError) as exc:
        return None, exc
