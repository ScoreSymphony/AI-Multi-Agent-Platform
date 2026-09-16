"""HTTP transport and redirect-policy enforcement for the stdlib browser provider."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from email.message import Message
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext

from .models import BrowserNetworkPolicy, BrowserOperation
from .policy import BrowserNetworkPolicyHook
from .reference_page import SessionState


@dataclass(frozen=True, slots=True)
class FetchedResource:
    final_url: str
    status_code: int
    content_type: str | None
    charset: str
    data: bytes


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
        opener = build_opener(
            HTTPCookieProcessor(state.cookies),
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
            self._network_hook.check(final_url, operation, context)
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
