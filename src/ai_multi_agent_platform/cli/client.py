"""Dependency-free client for the versioned canonical Control Plane API."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from ai_multi_agent_platform.contracts.types import JsonValue


class TransportError(RuntimeError):
    """Raised when the Control Plane cannot be reached or decoded safely."""


@dataclass(frozen=True, slots=True)
class APIClientError(RuntimeError):
    """Canonical HTTP/API failure returned by the Control Plane."""

    status: int
    code: str
    category: str
    message: str
    retryable: bool
    request_id: str | None = None
    correlation_id: str | None = None
    details: JsonValue = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


@dataclass(frozen=True, slots=True)
class RawResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]


class HTTPTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RawResponse: ...


class UrllibTransport:
    """Small stdlib HTTP transport so the base CLI has no extra dependency."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RawResponse:
        request = Request(url=url, data=body, headers=dict(headers), method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                return RawResponse(
                    status=response.status,
                    body=response.read(),
                    headers={key.casefold(): value for key, value in response.headers.items()},
                )
        except HTTPError as exc:
            headers_map = (
                {key.casefold(): value for key, value in exc.headers.items()}
                if exc.headers is not None
                else {}
            )
            return RawResponse(status=exc.code, body=exc.read(), headers=headers_map)
        except (URLError, TimeoutError, OSError) as exc:
            raise TransportError(f"Control Plane request failed: {exc}") from exc


@dataclass(frozen=True, slots=True)
class ClientResponse:
    status: int
    body: JsonValue
    request_id: str | None
    correlation_id: str | None
    api_version: str | None


@dataclass(frozen=True, slots=True)
class ClientOptions:
    endpoint: str
    timeout: float = 10.0
    retries: int = 2
    principal_ref: str | None = None
    owner_type: str | None = None
    owner_id: str | None = None

    def __post_init__(self) -> None:
        if self.timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if self.retries < 0 or self.retries > 10:
            raise ValueError("retries must be between 0 and 10")


class ControlPlaneClient:
    """Canonical `/api/v1` client; never accesses platform backends directly."""

    def __init__(
        self,
        options: ClientOptions,
        *,
        transport: HTTPTransport | None = None,
    ) -> None:
        self.options = options
        self._transport = transport or UrllibTransport()

    def get(
        self,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        raise_for_status: bool = True,
    ) -> ClientResponse:
        return self.request("GET", path, query=query, raise_for_status=raise_for_status)

    def post(
        self,
        path: str,
        *,
        body: Mapping[str, JsonValue] | None = None,
        idempotency_key: str | None = None,
        raise_for_status: bool = True,
    ) -> ClientResponse:
        headers = {"idempotency-key": idempotency_key or f"cli_{uuid4()}"}
        return self.request(
            "POST",
            path,
            body=body,
            headers=headers,
            raise_for_status=raise_for_status,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        body: Mapping[str, JsonValue] | None = None,
        headers: Mapping[str, str] | None = None,
        raise_for_status: bool = True,
    ) -> ClientResponse:
        normalized_method = method.upper()
        request_id = f"request_{uuid4()}"
        correlation_id = f"corr_{uuid4()}"
        request_headers = {
            "accept": "application/json",
            "x-request-id": request_id,
            "x-correlation-id": correlation_id,
        }
        if self.options.principal_ref is not None:
            request_headers["x-principal-ref"] = self.options.principal_ref
        if self.options.owner_type is not None:
            request_headers["x-owner-type"] = self.options.owner_type
        if self.options.owner_id is not None:
            request_headers["x-owner-id"] = self.options.owner_id
        if headers is not None:
            request_headers.update(headers)
        raw_body: bytes | None = None
        if body is not None:
            request_headers["content-type"] = "application/json"
            raw_body = json.dumps(body, separators=(",", ":")).encode("utf-8")

        attempts = self.options.retries + 1 if normalized_method == "GET" else 1
        response: RawResponse | None = None
        last_transport_error: TransportError | None = None
        for attempt in range(attempts):
            try:
                response = self._transport.request(
                    normalized_method,
                    self._url(path, query),
                    headers=request_headers,
                    body=raw_body,
                    timeout=self.options.timeout,
                )
                last_transport_error = None
            except TransportError as exc:
                last_transport_error = exc
                if attempt + 1 >= attempts:
                    raise
                _backoff(attempt)
                continue
            if response.status not in {502, 503, 504} or attempt + 1 >= attempts:
                break
            _backoff(attempt)

        if response is None:
            if last_transport_error is not None:
                raise last_transport_error
            raise TransportError("Control Plane request produced no response")
        decoded = _decode_json(response.body)
        normalized_headers = {key.casefold(): value for key, value in response.headers.items()}
        client_response = ClientResponse(
            status=response.status,
            body=decoded,
            request_id=normalized_headers.get("x-request-id") or request_id,
            correlation_id=normalized_headers.get("x-correlation-id") or correlation_id,
            api_version=normalized_headers.get("x-api-version"),
        )
        if raise_for_status and response.status >= 400:
            raise _api_error(client_response)
        return client_response

    def _url(self, path: str, query: Mapping[str, str] | None) -> str:
        relative = path if path.startswith("/") else f"/{path}"
        url = f"{self.options.endpoint.rstrip('/')}/api/v1{relative}"
        if query:
            url = f"{url}?{urlencode(query)}"
        return url


def _decode_json(body: bytes) -> JsonValue:
    if not body:
        return None
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransportError("Control Plane returned invalid JSON") from exc
    return cast(JsonValue, decoded)


def _api_error(response: ClientResponse) -> APIClientError:
    body = response.body
    if isinstance(body, dict):
        code_value = body.get("code")
        category_value = body.get("category")
        message_value = body.get("message")
        retryable_value = body.get("retryable")
        details = body.get("details")
        request_value = body.get("request_id")
        correlation_value = body.get("correlation_id")
        return APIClientError(
            status=response.status,
            code=code_value if isinstance(code_value, str) else "api_error",
            category=category_value if isinstance(category_value, str) else "api",
            message=message_value if isinstance(message_value, str) else f"HTTP {response.status}",
            retryable=retryable_value if isinstance(retryable_value, bool) else False,
            request_id=request_value if isinstance(request_value, str) else response.request_id,
            correlation_id=(
                correlation_value if isinstance(correlation_value, str) else response.correlation_id
            ),
            details=details,
        )
    return APIClientError(
        status=response.status,
        code="api_error",
        category="api",
        message=f"HTTP {response.status}",
        retryable=False,
        request_id=response.request_id,
        correlation_id=response.correlation_id,
    )


def _backoff(attempt: int) -> None:
    time.sleep(min(0.1 * (2**attempt), 0.5))
