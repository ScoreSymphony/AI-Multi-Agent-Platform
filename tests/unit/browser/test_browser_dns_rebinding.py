from __future__ import annotations

import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from unittest.mock import Mock

import pytest

from ai_multi_agent_platform.browser.models import (
    BrowserNetworkPolicy,
    BrowserOperation,
    BrowserSessionRef,
)
from ai_multi_agent_platform.browser.policy import (
    DefaultBrowserNetworkPolicyHook,
    resolve_browser_target,
)
from ai_multi_agent_platform.browser.reference_http import (
    _PinnedHTTPSConnection,
    _PinnedHTTPSHandler,
    ReferenceBrowserTransport,
)
from ai_multi_agent_platform.browser.reference_page import SessionState
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext, OperationControl
from ai_multi_agent_platform.domain import new_id


PUBLIC_V4 = "93.184.216.34"


def _context() -> OperationContext:
    return OperationContext(
        correlation_id="browser-rebinding",
        owner_type="user",
        owner_id="user-1",
        project_id=new_id("project"),
        control=OperationControl(),
    )


def _session(context: OperationContext) -> SessionState:
    return SessionState(ref=BrowserSessionRef.create(context))


def test_http_rebinding_is_blocked_before_socket_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter(((PUBLIC_V4,), ("127.0.0.1",)))
    dns_calls: list[str] = []
    connect_calls: list[tuple[str, int]] = []

    def fake_resolve(host: str) -> tuple[str, ...]:
        dns_calls.append(host)
        return next(answers)

    def fake_create_connection(address: tuple[str, int], *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        connect_calls.append(address)
        raise AssertionError("forbidden destination reached socket connect")

    monkeypatch.setattr(
        "ai_multi_agent_platform.browser.policy._resolve_addresses",
        fake_resolve,
    )
    monkeypatch.setattr(
        "ai_multi_agent_platform.browser.reference_http.socket.create_connection",
        fake_create_connection,
    )

    policy = BrowserNetworkPolicy(
        allowed_domains=("rebind.test",),
        allow_private_networks=False,
    )
    context = _context()
    transport = ReferenceBrowserTransport(
        network_policy=policy,
        network_hook=DefaultBrowserNetworkPolicyHook(policy),
        request_timeout_seconds=1.0,
        provider_id="browser.test",
    )

    with pytest.raises(ContractError) as caught:
        transport._fetch_sync(
            _session(context),
            "http://rebind.test/secret",
            BrowserOperation.NAVIGATE,
            context,
            "GET",
            None,
            {},
            1.0,
        )

    assert caught.value.code is ErrorCode.FORBIDDEN
    assert dns_calls == ["rebind.test", "rebind.test"]
    assert connect_calls == []


class _RedirectServer(ThreadingHTTPServer):
    requests_seen: list[tuple[str, str]]


class _RedirectHandler(BaseHTTPRequestHandler):
    server: _RedirectServer

    def do_GET(self) -> None:
        self.server.requests_seen.append((self.headers.get("Host", ""), self.path))
        if self.path == "/start":
            port = self.server.server_address[1]
            self.send_response(302)
            self.send_header("Location", f"http://redirect.test:{port}/secret")
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"PRIVATE")

    def log_message(self, format: str, *args: Any) -> None:
        del format, args


def test_redirect_hop_rebinding_is_blocked_before_second_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _RedirectServer(("127.0.0.1", 0), _RedirectHandler)
    server.requests_seen = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    real_create_connection = socket.create_connection
    pinned_connects: list[tuple[str, int]] = []
    answers = iter(
        (
            (PUBLIC_V4,),
            (PUBLIC_V4,),
            (PUBLIC_V4,),
            ("127.0.0.1",),
        )
    )

    def fake_resolve(host: str) -> tuple[str, ...]:
        del host
        return next(answers)

    def route_pinned_to_fixture(
        address: tuple[str, int],
        timeout: Any,
        source_address: tuple[str, int] | None,
    ) -> socket.socket:
        pinned_connects.append(address)
        return real_create_connection(
            ("127.0.0.1", server.server_address[1]),
            timeout,
            source_address,
        )

    monkeypatch.setattr(
        "ai_multi_agent_platform.browser.policy._resolve_addresses",
        fake_resolve,
    )
    monkeypatch.setattr(
        "ai_multi_agent_platform.browser.reference_http.socket.create_connection",
        route_pinned_to_fixture,
    )

    try:
        policy = BrowserNetworkPolicy(
            allowed_domains=("rebind.test", "redirect.test"),
            allow_private_networks=False,
        )
        context = _context()
        transport = ReferenceBrowserTransport(
            network_policy=policy,
            network_hook=DefaultBrowserNetworkPolicyHook(policy),
            request_timeout_seconds=1.0,
            provider_id="browser.test",
        )
        port = server.server_address[1]

        with pytest.raises(ContractError) as caught:
            transport._fetch_sync(
                _session(context),
                f"http://rebind.test:{port}/start",
                BrowserOperation.NAVIGATE,
                context,
                "GET",
                None,
                {},
                1.0,
            )

        assert caught.value.code is ErrorCode.FORBIDDEN
        assert pinned_connects == [(PUBLIC_V4, port)]
        assert server.requests_seen == [(f"rebind.test:{port}", "/start")]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def test_https_connection_pins_ip_and_preserves_hostname_for_sni(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_socket = Mock()
    wrapped_socket = Mock()
    tls_context = Mock()
    tls_context.wrap_socket.return_value = wrapped_socket
    destinations: list[tuple[str, int]] = []

    def fake_create_connection(
        address: tuple[str, int],
        timeout: Any,
        source_address: tuple[str, int] | None,
    ) -> Any:
        del timeout, source_address
        destinations.append(address)
        return raw_socket

    monkeypatch.setattr(
        "ai_multi_agent_platform.browser.reference_http.socket.create_connection",
        fake_create_connection,
    )

    connection = _PinnedHTTPSConnection(
        "secure.example",
        443,
        context=tls_context,
        pinned_addresses=(PUBLIC_V4,),
    )
    connection.connect()

    assert destinations == [(PUBLIC_V4, 443)]
    tls_context.wrap_socket.assert_called_once_with(raw_socket, server_hostname="secure.example")

    handler = _PinnedHTTPSHandler(lambda _url: (PUBLIC_V4,))
    assert handler._context.check_hostname is True
    assert handler._context.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1/", "127.0.0.1"),
        ("http://10.0.0.1/", "10.0.0.1"),
        ("http://169.254.169.254/", "169.254.169.254"),
        ("http://[fc00::1]/", "fc00::1"),
        ("http://[fe80::1]/", "fe80::1"),
    ],
)
def test_private_and_link_local_ipv4_ipv6_require_explicit_opt_in(
    url: str,
    expected: str,
) -> None:
    with pytest.raises(ContractError) as caught:
        resolve_browser_target(url, BrowserNetworkPolicy(allow_private_networks=False))
    assert caught.value.code is ErrorCode.FORBIDDEN

    assert resolve_browser_target(
        url,
        BrowserNetworkPolicy(allow_private_networks=True),
    ) == (expected,)


def test_mixed_public_and_private_dns_answer_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_resolve(host: str) -> tuple[str, ...]:
        del host
        return (PUBLIC_V4, "10.0.0.1")

    monkeypatch.setattr(
        "ai_multi_agent_platform.browser.policy._resolve_addresses",
        fake_resolve,
    )

    with pytest.raises(ContractError) as caught:
        resolve_browser_target(
            "http://ambiguous.test/",
            BrowserNetworkPolicy(allow_private_networks=False),
        )
    assert caught.value.code is ErrorCode.FORBIDDEN
