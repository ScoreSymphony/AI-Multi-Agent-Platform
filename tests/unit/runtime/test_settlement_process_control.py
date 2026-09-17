from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from ai_multi_agent_platform.evaluation.runner import _settle_awaitable as settle_evaluation
from ai_multi_agent_platform.plugins._settlement import settle_awaitable as settle_plugin
from ai_multi_agent_platform.templates._settlement import settle_awaitable as settle_template

Settlement = Callable[[Awaitable[None]], Awaitable[tuple[None, BaseException | None]]]


async def _raise(signal: BaseException) -> None:
    await asyncio.sleep(0)
    raise signal


@pytest.mark.parametrize("settle", (settle_evaluation, settle_plugin, settle_template))
@pytest.mark.parametrize("signal_type", (KeyboardInterrupt, SystemExit))
def test_settlement_helpers_propagate_process_control(
    settle: Settlement,
    signal_type: type[BaseException],
) -> None:
    async def scenario() -> None:
        with pytest.raises(signal_type):
            await settle(_raise(signal_type()))

    asyncio.run(scenario())


@pytest.mark.parametrize("settle", (settle_evaluation, settle_plugin, settle_template))
def test_settlement_helpers_return_child_cancellation_to_owner(settle: Settlement) -> None:
    async def scenario() -> None:
        result, error = await settle(_raise(asyncio.CancelledError()))
        assert result is None
        assert isinstance(error, asyncio.CancelledError)

    asyncio.run(scenario())
