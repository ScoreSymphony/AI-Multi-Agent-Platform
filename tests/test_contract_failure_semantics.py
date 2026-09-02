from __future__ import annotations

import asyncio

from ai_multi_agent_platform.contracts import ErrorCode, OperationContext, ToolInvocation
from ai_multi_agent_platform.testing import (
    FakeFailure,
    FakeToolProvider,
    assert_canonical_error,
)


def test_unsupported_tool_capability_fails_canonically() -> None:
    provider = FakeToolProvider(
        failure=FakeFailure(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "tool capability is not supported",
        )
    )
    invocation = ToolInvocation(
        invocation_id="invoke-unsupported",
        tool_ref="tool.unsupported",
        arguments={},
        context=OperationContext(correlation_id="task-unsupported"),
    )

    async def operation() -> object:
        return await provider.invoke(invocation)

    error = asyncio.run(
        assert_canonical_error(
            operation,
            expected_code=ErrorCode.UNSUPPORTED_CAPABILITY,
        )
    )

    assert error.retryable is False
    assert provider.calls == [invocation]
