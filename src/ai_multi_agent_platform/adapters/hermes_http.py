"""HTTP transport boundary for the optional Hermes adapter."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib import error, request

from ai_multi_agent_platform.contracts.types import JsonValue


@dataclass(frozen=True, slots=True)
class HermesHttpResponse:
    status_code: int
    payload: JsonValue


class HermesHttpTransport(Protocol):
    async def request_json(
        self,
        method: str,
        url: str,
        *,
        payload: Mapping[str, JsonValue] | None,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HermesHttpResponse: ...


class UrllibHermesHttpTransport:
    """Dependency-free JSON transport for a separately deployed Hermes API server."""

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        payload: Mapping[str, JsonValue] | None,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HermesHttpResponse:
        try:
            return await asyncio.to_thread(
                self._request_json_sync,
                method,
                url,
                payload,
                headers,
                timeout_seconds,
            )
        except TimeoutError:
            raise
        except error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise TimeoutError(str(exc.reason)) from exc
            raise ConnectionError(str(exc.reason)) from exc

    @staticmethod
    def _request_json_sync(
        method: str,
        url: str,
        payload: Mapping[str, JsonValue] | None,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HermesHttpResponse:
        body = None
        request_headers = {"accept": "application/json", **dict(headers)}
        if payload is not None:
            body = json.dumps(dict(payload), separators=(",", ":")).encode("utf-8")
            request_headers["content-type"] = "application/json"
        http_request = request.Request(
            url,
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            with request.urlopen(http_request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                parsed: JsonValue = json.loads(raw) if raw else None
                return HermesHttpResponse(response.status, parsed)
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = raw
            return HermesHttpResponse(exc.code, parsed)
