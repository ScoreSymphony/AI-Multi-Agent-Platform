from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from ai_multi_agent_platform.adapters.mcp import MCPServerConfig
from ai_multi_agent_platform.adapters.mcp_sdk import build_mcp_provider
from ai_multi_agent_platform.capabilities import (
    CapabilityInvocation,
    CapabilityInvoker,
    CapabilityRegistry,
    InvocationTrace,
)
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.domain import new_id

PIPELOCK_TEST_BIN = os.getenv("PIPELOCK_730_BIN")
PIPELOCK_TEST_CONFIG = os.getenv("PIPELOCK_730_CONFIG")
FIXTURE_DIR = Path(__file__).parent / "fixtures"
HTTP_FIXTURE = FIXTURE_DIR / "mcp_streamable_http_server.py"
MCP_WEBSOCKET_FIXTURE = FIXTURE_DIR / "mcp_websocket_server.py"
GENERIC_WEBSOCKET_FIXTURE = FIXTURE_DIR / "websocket_echo_server.py"
REDIRECT_FIXTURE = FIXTURE_DIR / "http_redirect_private_server.py"
WEBSOCKET_CONFIG = FIXTURE_DIR / "pipelock_websocket_audit.yaml"
REDIRECT_SSRF_CONFIG = FIXTURE_DIR / "pipelock_redirect_ssrf_audit.yaml"


def _free_loopback_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((host, 0))
        return int(listener.getsockname()[1])


def _wait_for_tcp(
    port: int,
    process: subprocess.Popen[bytes] | subprocess.Popen[str],
    *,
    host: str = "127.0.0.1",
) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"fixture exited before listening on {host}:{port}")
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"fixture did not listen on {host}:{port}")


def _stop(process: subprocess.Popen[bytes] | subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


@contextmanager
def _running_fixture(path: Path) -> Iterator[int]:
    port = _free_loopback_port()
    process = subprocess.Popen(
        [sys.executable, str(path), "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_tcp(port, process)
        yield port
    finally:
        _stop(process)


@contextmanager
def _pipelock(
    *,
    config: Path,
    port: int,
    tmp_path: Path,
    log_name: str,
) -> Iterator[Path]:
    assert PIPELOCK_TEST_BIN is not None
    home = tmp_path / f"home-{log_name}"
    home.mkdir()
    log_path = tmp_path / f"{log_name}.log"
    log_handle = log_path.open("w", encoding="utf-8")
    env = dict(os.environ)
    env["HOME"] = str(home)
    process = subprocess.Popen(
        (
            PIPELOCK_TEST_BIN,
            "run",
            "--config",
            str(config),
            "--listen",
            f"127.0.0.1:{port}",
        ),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    try:
        _wait_for_tcp(port, process)
        yield log_path
    finally:
        _stop(process)
        log_handle.close()


def _direct_opener(*, follow_redirects: bool = True) -> urllib.request.OpenerDirector:
    handlers: list[urllib.request.BaseHandler] = [urllib.request.ProxyHandler({})]
    if not follow_redirects:

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(  # type: ignore[override]
                self,
                req: urllib.request.Request,
                fp: object,
                code: int,
                msg: str,
                headers: object,
                newurl: str,
            ) -> None:
                return None

        handlers.append(NoRedirect())
    return urllib.request.build_opener(*handlers)


def _fetch_through_pipelock(proxy_port: int, target: str) -> tuple[int, str]:
    query = urllib.parse.urlencode({"url": target})
    request_url = f"http://127.0.0.1:{proxy_port}/fetch?{query}"
    opener = _direct_opener()
    try:
        with opener.open(request_url, timeout=10) as response:
            return int(response.status), response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", errors="replace")


def _invocation(query: str) -> CapabilityInvocation:
    project_id = new_id("project")
    return CapabilityInvocation(
        invocation_id=f"mcp-pipelock-730-{query}",
        capability_id="tool.lookup",
        arguments={"query": query},
        context=OperationContext(
            correlation_id=f"mcp-pipelock-correlation-730-{query}",
            owner_type="user",
            owner_id="user-730",
            project_id=project_id,
        ),
        trace=InvocationTrace(
            correlation_id=f"mcp-pipelock-correlation-730-{query}",
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=new_id("agent"),
            project_id=project_id,
        ),
    )


async def _exercise_upstream(
    *,
    server_id: str,
    upstream: str,
    query: str,
    expected_transport: str,
) -> None:
    assert PIPELOCK_TEST_BIN is not None
    assert PIPELOCK_TEST_CONFIG is not None

    config = MCPServerConfig(
        server_id=server_id,
        command=(
            PIPELOCK_TEST_BIN,
            "mcp",
            "proxy",
            "--config",
            PIPELOCK_TEST_CONFIG,
            "--upstream",
            upstream,
        ),
        read_timeout_seconds=15,
        capability_id_overrides={"lookup": "tool.lookup"},
    )
    registry = CapabilityRegistry()
    await registry.register_provider(build_mcp_provider(config))

    result = await CapabilityInvoker(registry).invoke(_invocation(query))

    assert result.capability_id == "tool.lookup"
    assert result.provider_id == f"mcp:{server_id}"
    assert result.output == {"query": query, "transport": expected_transport}


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None or PIPELOCK_TEST_CONFIG is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_pipelock_mcp_streamable_http_upstream_uses_canonical_invocation_path() -> None:
    with _running_fixture(HTTP_FIXTURE) as port:
        asyncio.run(
            _exercise_upstream(
                server_id="pipelock-streamable-http",
                upstream=f"http://127.0.0.1:{port}/mcp",
                query="pipelock-streamable-http",
                expected_transport="streamable-http",
            )
        )


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None or PIPELOCK_TEST_CONFIG is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_pipelock_mcp_websocket_upstream_uses_canonical_invocation_path() -> None:
    with _running_fixture(MCP_WEBSOCKET_FIXTURE) as port:
        asyncio.run(
            _exercise_upstream(
                server_id="pipelock-websocket",
                upstream=f"ws://127.0.0.1:{port}/mcp",
                query="pipelock-websocket",
                expected_transport="websocket",
            )
        )


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_pipelock_generic_websocket_proxy_relays_clean_text_frames(tmp_path: Path) -> None:
    websockets = pytest.importorskip("websockets")
    with _running_fixture(GENERIC_WEBSOCKET_FIXTURE) as echo_port:
        proxy_port = _free_loopback_port()
        with _pipelock(
            config=WEBSOCKET_CONFIG,
            port=proxy_port,
            tmp_path=tmp_path,
            log_name="pipelock-websocket",
        ):

            async def scenario() -> None:
                target = f"ws://127.0.0.1:{echo_port}/echo"
                query = urllib.parse.urlencode({"url": target})
                proxy_url = f"ws://127.0.0.1:{proxy_port}/ws?{query}"
                async with websockets.connect(
                    proxy_url,
                    compression=None,
                    open_timeout=5,
                    close_timeout=2,
                ) as websocket:
                    await websocket.send("pipelock-generic-websocket")
                    assert await asyncio.wait_for(websocket.recv(), timeout=5) == (
                        "pipelock-generic-websocket"
                    )

            asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_pipelock_fetch_redirect_to_private_target_is_blocked_before_target_access(
    tmp_path: Path,
) -> None:
    source_host = "127.0.0.2"
    target_host = "127.0.0.1"
    source_port = _free_loopback_port(source_host)
    target_port = _free_loopback_port(target_host)
    proxy_port = _free_loopback_port()
    marker = tmp_path / "private-target-reached.txt"
    fixture_log = (tmp_path / "redirect-fixture.log").open("w", encoding="utf-8")
    fixture = subprocess.Popen(
        (
            sys.executable,
            str(REDIRECT_FIXTURE),
            "--source-host",
            source_host,
            "--source-port",
            str(source_port),
            "--target-host",
            target_host,
            "--target-port",
            str(target_port),
            "--marker",
            str(marker),
        ),
        stdout=fixture_log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    source_url = f"http://{source_host}:{source_port}/start"
    private_url = f"http://{target_host}:{target_port}/private"
    try:
        _wait_for_tcp(source_port, fixture, host=source_host)
        _wait_for_tcp(target_port, fixture, host=target_host)

        no_redirect = _direct_opener(follow_redirects=False)
        with pytest.raises(urllib.error.HTTPError) as direct:
            no_redirect.open(source_url, timeout=5)
        assert direct.value.code == 302
        assert direct.value.headers["Location"] == private_url
        assert not marker.exists()

        with _pipelock(
            config=REDIRECT_SSRF_CONFIG,
            port=proxy_port,
            tmp_path=tmp_path,
            log_name="pipelock-redirect-ssrf",
        ) as log_path:
            status, _body = _fetch_through_pipelock(proxy_port, source_url)

        assert status >= 400
        assert not marker.exists()
        log_text = log_path.read_text(encoding="utf-8").lower()
        assert "redirect" in log_text
        assert "ssrf" in log_text or "private" in log_text
    finally:
        _stop(fixture)
        fixture_log.close()


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_pipelock_fetch_blocks_link_local_metadata_target(tmp_path: Path) -> None:
    proxy_port = _free_loopback_port()
    metadata_url = "http://169.254.169.254/latest/meta-data/"
    with _pipelock(
        config=REDIRECT_SSRF_CONFIG,
        port=proxy_port,
        tmp_path=tmp_path,
        log_name="pipelock-metadata-ssrf",
    ) as log_path:
        status, _body = _fetch_through_pipelock(proxy_port, metadata_url)

    assert status >= 400
    log_text = log_path.read_text(encoding="utf-8").lower()
    assert "ssrf" in log_text or "link-local" in log_text or "169.254.169.254" in log_text
