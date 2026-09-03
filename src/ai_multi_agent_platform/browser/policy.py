"""Browser-specific network policy hooks and SSRF protections."""

from __future__ import annotations

import ipaddress
import socket
from typing import Protocol
from urllib.parse import urlsplit

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext

from .models import BrowserNetworkPolicy, BrowserOperation


class BrowserNetworkPolicyHook(Protocol):
    """Replaceable policy hook evaluated before every outbound browser request."""

    def check(
        self,
        url: str,
        operation: BrowserOperation,
        context: OperationContext,
    ) -> None: ...


class DefaultBrowserNetworkPolicyHook:
    """Fail-closed reference policy with domain and private-network controls."""

    def __init__(self, policy: BrowserNetworkPolicy | None = None) -> None:
        self.policy = policy or BrowserNetworkPolicy()

    def check(
        self,
        url: str,
        operation: BrowserOperation,
        context: OperationContext,
    ) -> None:
        del operation, context
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"}:
            raise _blocked("browser network policy allows only http/https URLs")
        if scheme == "http" and not self.policy.allow_http:
            raise _blocked("HTTP is disabled by browser network policy")
        if scheme == "https" and not self.policy.allow_https:
            raise _blocked("HTTPS is disabled by browser network policy")
        if parsed.username is not None or parsed.password is not None:
            raise _blocked("credentials embedded in browser URLs are forbidden")

        host = (parsed.hostname or "").rstrip(".").lower()
        if not host:
            raise ContractError(ErrorCode.INVALID_REQUEST, "browser URL requires a hostname")
        if any(_matches_domain(host, denied) for denied in self.policy.denied_domains):
            raise _blocked(f"browser domain is denied by network policy: {host}")
        if self.policy.allowed_domains and not any(
            _matches_domain(host, allowed) for allowed in self.policy.allowed_domains
        ):
            raise _blocked(f"browser domain is outside the configured allowlist: {host}")

        if self.policy.allow_private_networks:
            return
        if host == "localhost" or host.endswith(".localhost"):
            raise _blocked("browser access to localhost is blocked by network policy")
        for address in _resolve_addresses(host):
            if _is_non_public(address):
                raise _blocked(
                    f"browser target resolves to a non-public network address: {address}"
                )


def _matches_domain(host: str, configured: str) -> bool:
    domain = configured.strip().rstrip(".").lower()
    return host == domain or host.endswith(f".{domain}")


def _resolve_addresses(host: str) -> tuple[str, ...]:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            results = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                f"browser target hostname could not be resolved: {host}",
                retryable=True,
            ) from exc
        return tuple(sorted({str(result[4][0]) for result in results}))
    return (str(address),)


def _is_non_public(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or address.is_multicast
    )


def _blocked(message: str) -> ContractError:
    return ContractError(ErrorCode.FORBIDDEN, message)
