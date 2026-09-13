from __future__ import annotations

import json

import pytest

from ai_multi_agent_platform.adapters.mcp_stateless import _decode_response
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode


def _sse_event(payload: dict[str, object]) -> bytes:
    return f"event: message\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n".encode()


def test_stateless_sse_ignores_request_scoped_progress_and_returns_final_response() -> None:
    body = b"".join(
        (
            _sse_event(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/progress",
                    "params": {"progress": 0.5},
                }
            ),
            _sse_event(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"resultType": "task", "taskId": "task-1"},
                }
            ),
        )
    )

    response = _decode_response(200, body, content_type="text/event-stream; charset=utf-8")

    assert response.status == 200
    assert response.payload["id"] == 1
    assert response.payload["result"] == {"resultType": "task", "taskId": "task-1"}


def test_stateless_sse_requires_one_final_jsonrpc_response() -> None:
    body = _sse_event(
        {
            "jsonrpc": "2.0",
            "method": "notifications/progress",
            "params": {"progress": 0.5},
        }
    )

    with pytest.raises(ContractError) as exc_info:
        _decode_response(200, body, content_type="text/event-stream")

    assert exc_info.value.code is ErrorCode.INVALID_PROVIDER_RESPONSE
    assert "without a final JSON-RPC response" in str(exc_info.value)


def test_stateless_sse_rejects_multiple_final_responses() -> None:
    body = b"".join(
        (
            _sse_event({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}),
            _sse_event({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}),
        )
    )

    with pytest.raises(ContractError) as exc_info:
        _decode_response(200, body, content_type="text/event-stream")

    assert exc_info.value.code is ErrorCode.INVALID_PROVIDER_RESPONSE
    assert "multiple final JSON-RPC responses" in str(exc_info.value)
