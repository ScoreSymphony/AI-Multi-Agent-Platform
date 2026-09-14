"""Compatibility bridge for awaitable Control Plane stream preparation."""

from __future__ import annotations

from typing import Any

from .http import HTTPRequest, HTTPResponse


async def prepare_stream_request(
    http: Any,
    request: HTTPRequest,
    *,
    request_id: str,
    correlation_id: str,
) -> HTTPRequest | HTTPResponse:
    """Prefer an awaitable stream preflight while preserving synchronous test seams."""

    prepare_async = getattr(http, "async_prepare_stream_request", None)
    if callable(prepare_async):
        return await prepare_async(
            request,
            request_id=request_id,
            correlation_id=correlation_id,
        )
    return http.prepare_stream_request(
        request,
        request_id=request_id,
        correlation_id=correlation_id,
    )


__all__ = ["prepare_stream_request"]
