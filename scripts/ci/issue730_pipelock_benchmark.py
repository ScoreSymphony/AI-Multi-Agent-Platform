#!/usr/bin/env python3
"""Reproducible #730 Pipelock Core performance/operability benchmark.

The harness intentionally measures the pinned Core candidate as an optional mediation layer. It does
not make policy decisions and it does not claim production containment. The same script is suitable
for GitHub-hosted smoke evidence and an ordinary Linux VPS run; the report records the execution
environment so those result classes cannot be confused.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

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

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures"
WEBSOCKET_FIXTURE = FIXTURE_DIR / "websocket_echo_server.py"
MCP_HTTP_FIXTURE = FIXTURE_DIR / "mcp_streamable_http_server.py"
DEFAULT_PROXY_CONFIG = FIXTURE_DIR / "pipelock_websocket_audit.yaml"


@dataclass(frozen=True, slots=True)
class TimingSummary:
    count: int
    mean_ms: float
    median_ms: float
    p95_ms: float
    min_ms: float
    max_ms: float

    def to_dict(self) -> dict[str, float | int]:
        return {
            "count": self.count,
            "mean_ms": self.mean_ms,
            "median_ms": self.median_ms,
            "p95_ms": self.p95_ms,
            "min_ms": self.min_ms,
            "max_ms": self.max_ms,
        }


class _HTTPHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        body = b'{"ok":true,"transport":"http"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _round(value: float) -> float:
    return round(value, 3)


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ValueError("at least one value is required")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _summary(samples_ms: Sequence[float]) -> TimingSummary:
    if not samples_ms:
        raise ValueError("at least one timing sample is required")
    return TimingSummary(
        count=len(samples_ms),
        mean_ms=_round(statistics.fmean(samples_ms)),
        median_ms=_round(statistics.median(samples_ms)),
        p95_ms=_round(_percentile(samples_ms, 0.95)),
        min_ms=_round(min(samples_ms)),
        max_ms=_round(max(samples_ms)),
    )


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_tcp(
    port: int,
    process: subprocess.Popen[str] | None = None,
    *,
    timeout_seconds: float = 10.0,
) -> float:
    started = time.perf_counter()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"process exited before listening on 127.0.0.1:{port}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return (time.perf_counter() - started) * 1000.0
        except OSError:
            time.sleep(0.02)
    raise TimeoutError(f"listener did not become ready on 127.0.0.1:{port}")


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
def _http_target() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}/data"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def _fixture(path: Path) -> Iterator[int]:
    port = _free_loopback_port()
    process = subprocess.Popen(
        (sys.executable, str(path), "--port", str(port)),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        _wait_for_tcp(port, process)
        yield port
    finally:
        _stop(process)


def _directory_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except FileNotFoundError:
                continue
    return total


def _read_rss_kib(pid: int) -> int | None:
    try:
        text = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError):
        return None
    for line in text.splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    return None


def _read_cpu_seconds(pid: int) -> float | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
        ticks = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
    except (FileNotFoundError, PermissionError, ValueError):
        return None
    return (int(fields[13]) + int(fields[14])) / float(ticks)


class _ProcessSampler:
    def __init__(self, pid: int) -> None:
        self._pid = pid
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.max_rss_kib: int | None = None
        self.cpu_start_seconds = _read_cpu_seconds(pid)
        self.cpu_end_seconds: float | None = None

    def _run(self) -> None:
        while not self._stop_event.wait(0.02):
            rss = _read_rss_kib(self._pid)
            if rss is not None:
                self.max_rss_kib = max(self.max_rss_kib or 0, rss)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=2)
        rss = _read_rss_kib(self._pid)
        if rss is not None:
            self.max_rss_kib = max(self.max_rss_kib or 0, rss)
        self.cpu_end_seconds = _read_cpu_seconds(self._pid)

    @property
    def cpu_seconds(self) -> float | None:
        if self.cpu_start_seconds is None or self.cpu_end_seconds is None:
            return None
        return _round(max(0.0, self.cpu_end_seconds - self.cpu_start_seconds))


@dataclass(slots=True)
class RunningPipelock:
    process: subprocess.Popen[str]
    port: int
    startup_ms: float
    home: Path
    log_path: Path
    sampler: _ProcessSampler


@contextmanager
def _pipelock_run(
    pipelock_bin: Path,
    config: Path,
    workdir: Path,
) -> Iterator[RunningPipelock]:
    port = _free_loopback_port()
    home = workdir / "pipelock-home"
    home.mkdir()
    log_path = workdir / "pipelock-run.log"
    log_handle = log_path.open("w", encoding="utf-8")
    env = dict(os.environ)
    env["HOME"] = str(home)
    process = subprocess.Popen(
        (
            str(pipelock_bin),
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
        startup_ms = _wait_for_tcp(port, process)
        sampler = _ProcessSampler(process.pid)
        sampler.start()
        running = RunningPipelock(
            process=process,
            port=port,
            startup_ms=_round(startup_ms),
            home=home,
            log_path=log_path,
            sampler=sampler,
        )
        yield running
    finally:
        if "sampler" in locals():
            sampler.stop()
        _stop(process)
        log_handle.close()


def _measure_sync(action: Callable[[], None], *, iterations: int, warmup: int) -> TimingSummary:
    for _ in range(warmup):
        action()
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        action()
        samples.append((time.perf_counter_ns() - started) / 1_000_000.0)
    return _summary(samples)


async def _measure_async(
    action: Callable[[], Any],
    *,
    iterations: int,
    warmup: int,
) -> TimingSummary:
    for _ in range(warmup):
        await action()
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        await action()
        samples.append((time.perf_counter_ns() - started) / 1_000_000.0)
    return _summary(samples)


def _http_get(url: str) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=10) as response:
        body = response.read()
        if int(response.status) != 200 or b'"ok":true' not in body:
            raise RuntimeError(f"unexpected HTTP benchmark response from {url}")


def _fetch_url(proxy_port: int, target: str) -> str:
    return (
        f"http://127.0.0.1:{proxy_port}/fetch?"
        + urllib.parse.urlencode({"url": target})
    )


async def _websocket_roundtrip(url: str, message: str) -> None:
    import websockets

    async with websockets.connect(
        url,
        compression=None,
        open_timeout=5,
        close_timeout=2,
    ) as websocket:
        await websocket.send(message)
        response = await asyncio.wait_for(websocket.recv(), timeout=5)
        if response != message:
            raise RuntimeError("unexpected WebSocket benchmark echo")


def _mcp_invocation(index: int) -> CapabilityInvocation:
    project_id = new_id("project")
    correlation_id = f"pipelock-perf-730-{index}"
    return CapabilityInvocation(
        invocation_id=f"pipelock-perf-invocation-730-{index}",
        capability_id="tool.lookup",
        arguments={"query": f"benchmark-{index}"},
        context=OperationContext(
            correlation_id=correlation_id,
            owner_type="user",
            owner_id="user-730",
            project_id=project_id,
        ),
        trace=InvocationTrace(
            correlation_id=correlation_id,
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=new_id("agent"),
            project_id=project_id,
        ),
    )


async def _mcp_benchmark(
    config: MCPServerConfig,
    *,
    iterations: int,
    warmup: int,
) -> TimingSummary:
    registry = CapabilityRegistry()
    await registry.register_provider(build_mcp_provider(config))
    invoker = CapabilityInvoker(registry)
    counter = 0

    async def action() -> None:
        nonlocal counter
        counter += 1
        result = await invoker.invoke(_mcp_invocation(counter))
        output = result.output
        if not isinstance(output, dict) or output.get("transport") != "streamable-http":
            raise RuntimeError("unexpected MCP benchmark response")

    return await _measure_async(action, iterations=iterations, warmup=warmup)


def _overhead(direct: TimingSummary, mediated: TimingSummary) -> dict[str, float]:
    median_delta = mediated.median_ms - direct.median_ms
    p95_delta = mediated.p95_ms - direct.p95_ms
    median_percent = 0.0 if direct.median_ms == 0 else (median_delta / direct.median_ms) * 100.0
    return {
        "median_ms": _round(median_delta),
        "median_percent": _round(median_percent),
        "p95_ms": _round(p95_delta),
    }


def _environment() -> dict[str, object]:
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "github_actions": os.getenv("GITHUB_ACTIONS") == "true",
        "github_runner_name": os.getenv("RUNNER_NAME"),
        "github_runner_environment": os.getenv("RUNNER_ENVIRONMENT"),
        "platform_commit": os.getenv("GITHUB_SHA"),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipelock-bin", type=Path, required=True)
    parser.add_argument("--mcp-config", type=Path, required=True)
    parser.add_argument("--proxy-config", type=Path, default=DEFAULT_PROXY_CONFIG)
    parser.add_argument("--iterations", type=int, default=12)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations must be at least 1")
    if args.warmup < 0:
        parser.error("--warmup must not be negative")
    return args


def main() -> int:
    args = _parse_args()
    pipelock_bin = args.pipelock_bin.resolve()
    mcp_config = args.mcp_config.resolve()
    proxy_config = args.proxy_config.resolve()
    if not pipelock_bin.is_file():
        raise SystemExit(f"missing Pipelock binary: {pipelock_bin}")
    if not mcp_config.is_file():
        raise SystemExit(f"missing Pipelock MCP config: {mcp_config}")
    if not proxy_config.is_file():
        raise SystemExit(f"missing Pipelock proxy config: {proxy_config}")

    with tempfile.TemporaryDirectory(prefix="pipelock-730-perf-") as temporary:
        workdir = Path(temporary)
        with _http_target() as http_target:
            with _fixture(WEBSOCKET_FIXTURE) as websocket_port:
                with _fixture(MCP_HTTP_FIXTURE) as mcp_port:
                    direct_http = _measure_sync(
                        lambda: _http_get(http_target),
                        iterations=args.iterations,
                        warmup=args.warmup,
                    )
                    direct_ws_url = f"ws://127.0.0.1:{websocket_port}/echo"
                    direct_websocket = asyncio.run(
                        _measure_async(
                            lambda: _websocket_roundtrip(direct_ws_url, "pipelock-perf-730"),
                            iterations=args.iterations,
                            warmup=args.warmup,
                        )
                    )
                    direct_mcp = asyncio.run(
                        _mcp_benchmark(
                            MCPServerConfig(
                                server_id="pipelock-perf-direct",
                                endpoint=f"http://127.0.0.1:{mcp_port}/mcp",
                                read_timeout_seconds=15,
                                capability_id_overrides={"lookup": "tool.lookup"},
                            ),
                            iterations=args.iterations,
                            warmup=args.warmup,
                        )
                    )

                    with _pipelock_run(pipelock_bin, proxy_config, workdir) as running:
                        mediated_http = _measure_sync(
                            lambda: _http_get(_fetch_url(running.port, http_target)),
                            iterations=args.iterations,
                            warmup=args.warmup,
                        )
                        target_ws = f"ws://127.0.0.1:{websocket_port}/echo"
                        mediated_ws_url = (
                            f"ws://127.0.0.1:{running.port}/ws?"
                            + urllib.parse.urlencode({"url": target_ws})
                        )
                        mediated_websocket = asyncio.run(
                            _measure_async(
                                lambda: _websocket_roundtrip(
                                    mediated_ws_url,
                                    "pipelock-perf-730",
                                ),
                                iterations=args.iterations,
                                warmup=args.warmup,
                            )
                        )

                        mediated_mcp = asyncio.run(
                            _mcp_benchmark(
                                MCPServerConfig(
                                    server_id="pipelock-perf-mediated",
                                    command=(
                                        str(pipelock_bin),
                                        "mcp",
                                        "proxy",
                                        "--config",
                                        str(mcp_config),
                                        "--upstream",
                                        f"http://127.0.0.1:{mcp_port}/mcp",
                                    ),
                                    read_timeout_seconds=15,
                                    capability_id_overrides={"lookup": "tool.lookup"},
                                ),
                                iterations=args.iterations,
                                warmup=args.warmup,
                            )
                        )

                    report = {
                        "schema_version": 1,
                        "issue": 730,
                        "candidate": {
                            "binary": str(pipelock_bin),
                            "mcp_config": str(mcp_config),
                            "proxy_config": str(proxy_config),
                        },
                        "environment": _environment(),
                        "parameters": {
                            "iterations": args.iterations,
                            "warmup": args.warmup,
                        },
                        "http": {
                            "direct": direct_http.to_dict(),
                            "mediated": mediated_http.to_dict(),
                            "overhead": _overhead(direct_http, mediated_http),
                        },
                        "websocket": {
                            "direct": direct_websocket.to_dict(),
                            "mediated": mediated_websocket.to_dict(),
                            "overhead": _overhead(direct_websocket, mediated_websocket),
                        },
                        "mcp_streamable_http": {
                            "direct": direct_mcp.to_dict(),
                            "mediated": mediated_mcp.to_dict(),
                            "overhead": _overhead(direct_mcp, mediated_mcp),
                        },
                        "resources": {
                            "scope": "persistent pipelock run proxy during HTTP/WebSocket workload",
                            "startup_ms": running.startup_ms,
                            "cpu_seconds": running.sampler.cpu_seconds,
                            "max_rss_kib": running.sampler.max_rss_kib,
                            "log_bytes": running.log_path.stat().st_size,
                            "home_bytes": _directory_bytes(running.home),
                        },
                        "correctness": {
                            "clean_http_allowed": True,
                            "clean_websocket_allowed": True,
                            "clean_mcp_allowed": True,
                            "false_positive_clean_cases": 0,
                            "false_negative_measurement": (
                                "maintained adversarial corpus is evaluated by dedicated #730 workflows; "
                                "this latency harness does not relabel those security cases"
                            ),
                        },
                        "interpretation": {
                            "hosted_runner_is_not_vps_evidence": os.getenv("GITHUB_ACTIONS") == "true",
                            "mcp_resource_scope": (
                                "MCP proxy subprocess resources are not included in persistent run-proxy RSS/CPU"
                            ),
                        },
                    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
