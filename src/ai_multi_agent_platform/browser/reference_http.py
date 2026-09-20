"""HTTP transport and redirect-policy enforcement for the stdlib browser provider."""

from __future__ import annotations

import asyncio
import http.client
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from email.message import Message
from functools import partial
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPCookieProcessor,
    HTTPHandler,
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext

from .models import BrowserNetworkPolicy, BrowserOperation
from .policy import BrowserNetworkPolicyHook, resolve_browser_target
from .reference_page import SessionState

_DEFAULT_TIMEOUT: Any = socket._GLOBAL_DEFAULT_TIMEOUT  # type: ignore[attr-defined]


@dataclass(frozen=True, slots=True)
class FetchedResource:
    final_url: str
    status_code: int
    content_type: str | None
    charset: str
    data: bytes


def _connect_to_pinned_address(
    pinned_addresses: tuple[str, ...],
    destination: tuple[str, int],
    timeout: Any = _DEFAULT_TIMEOUT,
    source_address: tuple[str, int] | None = None,
) -> socket.socket:
    _host, port = destination
    last_error: OSError | None = None
    for pinned_address in pinned_addresses:
        try:
            return socket.create_connection(
                (pinned_address, port),
                timeout,
                source_address,
            )
        except OSError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise OSError("pinned browser connection has no validated destination")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(
        self,
        host: str,
        port: int | None = None,
        *,
        timeout: Any = _DEFAULT_TIMEOUT,
        source_address: tuple[str, int] | None = None,
        blocksize: int = 8192,
        pinned_addresses: tuple[str, ...],
    ) -> None:
        if not pinned_addresses:
            raise ValueError("pinned browser connection requires at least one address")
        super().__init__(
            host,
            port,
            timeout=timeout,
            source_address=source_address,
            blocksize=blocksize,
        )
        self._create_connection = partial(_connect_to_pinned_address, pinned_addresses)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        host: str,
        port: int | None = None,
        *,
        timeout: Any = _DEFAULT_TIMEOUT,
        source_address: tuple[str, int] | None = None,
        context: Any = None,
        blocksize: int = 8192,
        pinned_addresses: tuple[str, ...],
    ) -> None:
        if not pinned_addresses:
            raise ValueError("pinned browser connection requires at least one address")
        super().__init__(
            host,
            port,
            timeout=timeout,
            source_address=source_address,
            context=context,
            blocksize=blocksize,
        )
        self._create_connection = partial(_connect_to_pinned_address, pinned_addresses)


class _PinnedHTTPHandler(HTTPHandler):
    def __init__(self, resolver: Callable[[str], tuple[str, ...]]) -> None:
        super().__init__()
        self._resolver = resolver

    def http_open(self, req: Request) -> Any:
        connection = partial(
            _PinnedHTTPConnection,
            pinned_addresses=self._resolver(req.full_url),
        )
        return self.do_open(connection, req)


class _PinnedHTTPSHandler(HTTPSHandler):
    def __init__(self, resolver: Callable[[str], tuple[str, ...]]) -> None:
        self._tls_context = ssl.create_default_context()
        super().__init__(context=self._tls_context)
        self._resolver = resolver

    def https_open(self, req: Request) -> Any:
        connection = partial(
            _PinnedHTTPSConnection,
            pinned_addresses=self._resolver(req.full_url),
        )
        return self.do_open(connection, req, context=self._tls_context)


class _PolicyRedirectHandler(HTTPRedirectHandler):
    def __init__(
        self,
        hook: BrowserNetworkPolicyHook,
        operation: BrowserOperation,
        context: OperationContext,
    ) -> None:
        super().__init__()
        self._hook = hook
        self._operation = operation
        self._context = context

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        self._hook.check(newurl, self._operation, self._context)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class ReferenceBrowserTransport:
    """Own stdlib HTTP I/O, bounded responses and network-policy checks."""

    def __init__(
        self,
        *,
        network_policy: BrowserNetworkPolicy,
        network_hook: BrowserNetworkPolicyHook,
        request_timeout_seconds: float,
        provider_id: str,
    ) -> None:
        self._network_policy = network_policy
        self._network_hook = network_hook
        self._request_timeout_seconds = request_timeout_seconds
        self._provider_id = provider_id

    async def fetch(
        self,
        state: SessionState,
        url: str,
        operation: BrowserOperation,
        context: OperationContext,
        *,
        method: str = "GET",
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> FetchedResource:
        timeout = context.control.timeout_seconds or self._request_timeout_seconds
        return await asyncio.to_thread(
            self._fetch_sync,
            state,
            url,
            operation,
            context,
            method,
            data,
            headers or {},
            timeout,
        )

    def _fetch_sync(
        self,
        state: SessionState,
        url: str,
        operation: BrowserOperation,
        context: OperationContext,
        method: str,
        data: bytes | None,
        headers: dict[str, str],
        timeout: float,
    ) -> FetchedResource:
        self._network_hook.check(url, operation, context)

        def resolve_for_connection(request_url: str) -> tuple[str, ...]:
            return resolve_browser_target(request_url, self._network_policy)

        opener = build_opener(
            ProxyHandler({}),
            HTTPCookieProcessor(state.cookies),
            _PinnedHTTPHandler(resolve_for_connection),
            _PinnedHTTPSHandler(resolve_for_connection),
            _PolicyRedirectHandler(self._network_hook, operation, context),
        )
        request = Request(url=url, data=data, headers=headers, method=method)
        response: Any
        try:
            response = opener.open(request, timeout=timeout)
        except HTTPError as exc:
            response = exc
        except TimeoutError as exc:
            raise self._timeout_error() from exc
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise self._timeout_error() from exc
            raise self._unavailable_error() from exc
        except OSError as exc:
            raise self._unavailable_error() from exc

        try:
            final_url = str(response.geturl())
            data_bytes = response.read(self._network_policy.max_response_bytes + 1)
            if len(data_bytes) > self._network_policy.max_response_bytes:
                raise ContractError(
                    ErrorCode.INPUT_TOO_LARGE,
                    "browser response exceeds configured maximum size",
                    provider_id=self._provider_id,
                )
            response_headers = cast(Message, response.headers)
            content_type = response_headers.get_content_type() if response_headers else None
            charset = response_headers.get_content_charset() if response_headers else None
            return FetchedResource(
                final_url=final_url,
                status_code=int(response.getcode()),
                content_type=content_type,
                charset=charset or "utf-8",
                data=data_bytes,
            )
        finally:
            response.close()

    def _timeout_error(self) -> ContractError:
        return ContractError(
            ErrorCode.TIMEOUT,
            "reference browser network request timed out",
            provider_id=self._provider_id,
            retryable=True,
        )

    def _unavailable_error(self) -> ContractError:
        return ContractError(
            ErrorCode.UNAVAILABLE,
            "reference browser network request failed",
            provider_id=self._provider_id,
            retryable=True,
        )
