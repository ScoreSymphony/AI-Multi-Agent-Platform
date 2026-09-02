"""Reusable assertions/helpers for versioned Control Plane contract tests."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane import HTTPResponse


def api_headers(
    *,
    idempotency_key: str | None = None,
    request_id: str | None = None,
    correlation_id: str | None = None,
) -> dict[str, str]:
    headers = {
        "content-type": "application/json",
        "x-principal-ref": "user:contract-test",
    }
    if idempotency_key is not None:
        headers["idempotency-key"] = idempotency_key
    if request_id is not None:
        headers["x-request-id"] = request_id
    if correlation_id is not None:
        headers["x-correlation-id"] = correlation_id
    return headers


def assert_page(payload: JsonValue, *, total: int | None = None) -> list[JsonValue]:
    assert isinstance(payload, dict)
    assert isinstance(payload.get("items"), list)
    assert isinstance(payload.get("limit"), int)
    assert isinstance(payload.get("total"), int)
    assert "next_cursor" in payload
    if total is not None:
        assert payload["total"] == total
    items = payload["items"]
    assert isinstance(items, list)
    return items


def assert_error_envelope(response: HTTPResponse, *, code: str, status: int) -> None:
    assert response.status == status
    assert isinstance(response.body, dict)
    assert response.body["code"] == code
    assert isinstance(response.body["message"], str)
    assert isinstance(response.body["request_id"], str)
    assert isinstance(response.body["correlation_id"], str)
    assert isinstance(response.body["retryable"], bool)
