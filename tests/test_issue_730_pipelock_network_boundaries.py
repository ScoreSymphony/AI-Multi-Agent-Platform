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

PIPELOCK_TEST_BIN = os.getenv("PIPELOCK_730_BIN")
FIXTURES = Path(__file__).parent / "fixtures"
WEBSOCKET_CONFIG = FIXTURES / "pipelock_websocket_audit.yaml"
REDIRECT_SSRF_CONFIG = FIXTURES / "pipelock_redirect_ssrf_audit.yaml"
WEBSOCKET_FIXTURE = FIXTURES / "websocket_echo_server.py"
REDIRECT_FIXTURE = FIXTURES / "http_redirect_private_server.py"


def _reserve_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _wait_for_port(host: str, port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"process exited before {host}:{port} became ready")
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {host}:{port}")


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


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
        _wait_for_port("127.0.0.1", port, process)
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


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_generic_websocket_proxy_relays_clean_text_frames(tmp_path: Path) -> None:
    websockets = pytest.importorskip("websockets")
    echo_port = _reserve_port()
    proxy_port = _reserve_port()
    fixture_log = (tmp_path / "websocket-fixture.log").open("w", encoding="utf-8")
    fixture = subprocess.Popen(
        (
            sys.executable,
            str(WEBSOCKET_FIXTURE),
            "--host",
            "127.0.0.1",
            "--port",
            str(echo_port),
        ),
        stdout=fixture_log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_port("127.0.0.1", echo_port, fixture)
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
    finally:
        _stop(fixture)
        fixture_log.close()


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_fetch_redirect_to_private_target_is_blocked_before_target_access(
    tmp_path: Path,
) -> None:
    source_host = "127.0.0.2"
    target_host = "127.0.0.1"
    source_port = _reserve_port(source_host)
    target_port = _reserve_port(target_host)
    proxy_port = _reserve_port()
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
        _wait_for_port(source_host, source_port, fixture)
        _wait_for_port(target_host, target_port, fixture)

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
def test_fetch_blocks_link_local_metadata_target(tmp_path: Path) -> None:
    proxy_port = _reserve_port()
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
