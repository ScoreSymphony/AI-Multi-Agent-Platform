#!/usr/bin/env python3
"""Reproducible #730 Pipelock performance and classification evidence harness."""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import math
import os
import platform
import socket
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, TextIO

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures"
MCP_STDIO_FIXTURE = FIXTURE_DIR / "mcp_stdio_server.py"
WEBSOCKET_FIXTURE = FIXTURE_DIR / "websocket_echo_server.py"
CORPUS_PATH = FIXTURE_DIR / "pipelock_adversarial_cases.json"
HTTP_SENTINEL = b"issue-730-benchmark-ok"


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipelock-bin", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--environment-label", required=True)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--mcp-iterations", type=int, default=5)
    return parser.parse_args()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_tcp(port: int, process: subprocess.Popen[str], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"process exited before listening on 127.0.0.1:{port}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.02)
    raise TimeoutError(f"process did not listen on 127.0.0.1:{port}")


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


class _BenchmarkHTTPHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        body = HTTP_SENTINEL
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextlib.contextmanager
def _http_target() -> Iterator[int]:
    with ThreadingHTTPServer(("127.0.0.1", 0), _BenchmarkHTTPHandler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield int(server.server_address[1])
        finally:
            server.shutdown()
            thread.join(timeout=5)


@contextlib.contextmanager
def _websocket_target() -> Iterator[int]:
    port = _free_port()
    process = subprocess.Popen(
        (sys.executable, str(WEBSOCKET_FIXTURE), "--port", str(port)),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        _wait_for_tcp(port, process)
        yield port
    finally:
        _stop(process)


@contextlib.contextmanager
def _pipelock(
    *,
    binary: Path,
    config: Path,
    workdir: Path,
) -> Iterator[tuple[subprocess.Popen[str], int, Path, Path, float]]:
    port = _free_port()
    home = workdir / "pipelock-home"
    home.mkdir(parents=True, exist_ok=True)
    log_path = workdir / "pipelock.log"
    log_handle: TextIO = log_path.open("w", encoding="utf-8")
    env = dict(os.environ)
    env["HOME"] = str(home)
    started = time.perf_counter()
    process = subprocess.Popen(
        (
            str(binary),
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
        startup_ms = (time.perf_counter() - started) * 1000.0
        yield process, port, log_path, home, startup_ms
    finally:
        _stop(process)
        log_handle.close()


def _open_url(url: str, timeout: float = 10.0) -> bytes:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        return exc.read()


def _mediated_fetch_url(proxy_port: int, target: str) -> str:
    query = urllib.parse.urlencode({"url": target})
    return f"http://127.0.0.1:{proxy_port}/fetch?{query}"


def _measure_sync(call: Any, *, warmups: int, iterations: int) -> list[float]:
    for _ in range(warmups):
        call()
    values: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        call()
        values.append((time.perf_counter_ns() - started) / 1_000_000.0)
    return values


def _latency_stats(values: Sequence[float]) -> dict[str, float]:
    if not values:
        raise ValueError("latency series must not be empty")
    ordered = sorted(values)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "count": float(len(ordered)),
        "min_ms": min(ordered),
        "mean_ms": statistics.fmean(ordered),
        "p50_ms": statistics.median(ordered),
        "p95_ms": ordered[p95_index],
        "max_ms": max(ordered),
    }


def _overhead(direct: dict[str, float], mediated: dict[str, float]) -> dict[str, float | None]:
    delta = mediated["mean_ms"] - direct["mean_ms"]
    baseline = direct["mean_ms"]
    return {
        "mean_delta_ms": delta,
        "mean_overhead_percent": (delta / baseline * 100.0) if baseline > 0 else None,
    }


def _read_proc_cpu_seconds(pid: int) -> float:
    fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
    ticks = int(fields[13]) + int(fields[14])
    clock_ticks = int(os.sysconf("SC_CLK_TCK"))
    return ticks / clock_ticks


def _read_proc_rss_bytes(pid: int) -> int:
    for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
        if line.startswith("VmRSS:"):
            kib = int(line.split()[1])
            return kib * 1024
    raise RuntimeError(f"VmRSS missing for pid {pid}")


def _tree_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def _rss_monitor(pid: int, stop_event: threading.Event, samples: list[int]) -> None:
    while not stop_event.wait(0.02):
        try:
            samples.append(_read_proc_rss_bytes(pid))
        except FileNotFoundError:
            return


async def _websocket_roundtrip(url: str, payload: str) -> None:
    import websockets

    async with websockets.connect(
        url,
        compression=None,
        open_timeout=5,
        close_timeout=2,
    ) as websocket:
        await websocket.send(payload)
        echoed = await asyncio.wait_for(websocket.recv(), timeout=5)
        if echoed != payload:
            raise AssertionError("websocket echo payload changed")


async def _measure_websocket(url: str, *, warmups: int, iterations: int) -> list[float]:
    payload = "issue-730-websocket-benchmark"
    for _ in range(warmups):
        await _websocket_roundtrip(url, payload)
    values: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        await _websocket_roundtrip(url, payload)
        values.append((time.perf_counter_ns() - started) / 1_000_000.0)
    return values


async def _measure_mcp_command(
    command: tuple[str, ...],
    *,
    iterations: int,
) -> list[float]:
    from ai_multi_agent_platform.adapters.mcp import MCPServerConfig
    from ai_multi_agent_platform.adapters.mcp_sdk import build_mcp_provider
    from ai_multi_agent_platform.capabilities import (
        CapabilityInvocation,
        CapabilityInvoker,
        CapabilityRegistry,
        InvocationTrace,
    )
    from ai_multi_agent_platform.contracts import OperationContext
    from ai_multi_agent_platform.domain import new_id

    config = MCPServerConfig(
        server_id="issue-730-benchmark-" + str(abs(hash(command))),
        command=command,
        read_timeout_seconds=15,
        capability_id_overrides={"lookup": "tool.lookup"},
    )
    registry = CapabilityRegistry()
    await registry.register_provider(build_mcp_provider(config))
    invoker = CapabilityInvoker(registry)
    project_id = new_id("project")
    values: list[float] = []

    for index in range(iterations):
        correlation_id = new_id("correlation")
        invocation = CapabilityInvocation(
            invocation_id=new_id("invocation"),
            capability_id="tool.lookup",
            arguments={"query": f"benchmark-{index}"},
            context=OperationContext(
                correlation_id=correlation_id,
                owner_type="user",
                owner_id="issue-730-benchmark",
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
        started = time.perf_counter_ns()
        result = await invoker.invoke(invocation)
        values.append((time.perf_counter_ns() - started) / 1_000_000.0)
        if not isinstance(result.output, dict) or result.output.get("transport") != "stdio":
            raise AssertionError("unexpected MCP benchmark response")
    return values


def _transform_corpus_case(case: dict[str, Any]) -> str:
    payload = str(case["payload"])
    if case["transform"] == "plain":
        return payload
    if case["transform"] == "base64":
        return base64.b64encode(payload.encode("utf-8")).decode("ascii")
    raise ValueError(f"unsupported benchmark corpus transform: {case['transform']}")


def _classification_measurement(
    *,
    proxy_port: int,
    target_port: int,
    log_path: Path,
) -> dict[str, Any]:
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    malicious = [
        case
        for case in corpus
        if case.get("status") == "live"
        and case.get("surface") == "http_fetch_url"
        and case.get("category") in {"secret_dlp", "encoding_variant"}
    ]
    benign = [
        ("benign-plain", "ordinary-project-reference"),
        ("benign-date", "release-2026-09-11"),
        ("benign-numeric", "ticket-730-12345"),
        ("benign-path", "docs-upstream-pipelock"),
    ]
    cases: list[tuple[str, bool, str]] = [
        (str(case["id"]), True, _transform_corpus_case(case)) for case in malicious
    ]
    cases.extend((case_id, False, value) for case_id, value in benign)

    tp = fp = tn = fn = 0
    observations: list[dict[str, object]] = []
    for case_id, expected_detection, value in cases:
        before = log_path.stat().st_size if log_path.exists() else 0
        target = f"http://127.0.0.1:{target_port}/classify?" + urllib.parse.urlencode(
            {"value": value}
        )
        _open_url(_mediated_fetch_url(proxy_port, target))
        time.sleep(0.05)
        segment = log_path.read_bytes()[before:].decode("utf-8", errors="replace").lower()
        observed_detection = "dlp" in segment
        if expected_detection and observed_detection:
            tp += 1
        elif expected_detection and not observed_detection:
            fn += 1
        elif not expected_detection and observed_detection:
            fp += 1
        else:
            tn += 1
        observations.append(
            {
                "case_id": case_id,
                "expected_detection": expected_detection,
                "observed_detection": observed_detection,
            }
        )

    return {
        "scope": "maintained live HTTP DLP corpus cases plus deterministic benign controls",
        "true_positive": tp,
        "false_positive": fp,
        "true_negative": tn,
        "false_negative": fn,
        "observations": observations,
    }


def _environment(label: str) -> dict[str, object]:
    return {
        "label": label,
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
    }


def _main() -> int:
    args = _args()
    if args.iterations < 2 or args.mcp_iterations < 1 or args.warmups < 0:
        raise SystemExit("invalid benchmark iteration count")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    workdir = args.output.parent / "issue730-pipelock-benchmark-work"
    workdir.mkdir(parents=True, exist_ok=True)

    with _http_target() as http_port, _websocket_target() as websocket_port:
        direct_http = f"http://127.0.0.1:{http_port}/ok"
        direct_ws = f"ws://127.0.0.1:{websocket_port}/echo"
        direct_http_values = _measure_sync(
            lambda: _assert_http_ok(_open_url(direct_http)),
            warmups=args.warmups,
            iterations=args.iterations,
        )
        direct_ws_values = asyncio.run(
            _measure_websocket(
                direct_ws,
                warmups=args.warmups,
                iterations=args.iterations,
            )
        )

        with _pipelock(
            binary=args.pipelock_bin.resolve(),
            config=args.config.resolve(),
            workdir=workdir,
        ) as (process, proxy_port, log_path, home, startup_ms):
            rss_samples = [_read_proc_rss_bytes(process.pid)]
            stop_monitor = threading.Event()
            monitor = threading.Thread(
                target=_rss_monitor,
                args=(process.pid, stop_monitor, rss_samples),
                daemon=True,
            )
            monitor.start()
            cpu_before = _read_proc_cpu_seconds(process.pid)
            disk_before = _tree_size(home) + _tree_size(log_path)
            mediated_http = _mediated_fetch_url(proxy_port, direct_http)
            mediated_ws = f"ws://127.0.0.1:{proxy_port}/ws?" + urllib.parse.urlencode(
                {"url": direct_ws}
            )
            mediated_http_values = _measure_sync(
                lambda: _assert_http_ok(_open_url(mediated_http)),
                warmups=args.warmups,
                iterations=args.iterations,
            )
            mediated_ws_values = asyncio.run(
                _measure_websocket(
                    mediated_ws,
                    warmups=args.warmups,
                    iterations=args.iterations,
                )
            )
            classification = _classification_measurement(
                proxy_port=proxy_port,
                target_port=http_port,
                log_path=log_path,
            )
            cpu_after = _read_proc_cpu_seconds(process.pid)
            disk_after = _tree_size(home) + _tree_size(log_path)
            stop_monitor.set()
            monitor.join(timeout=2)
            rss_samples.append(_read_proc_rss_bytes(process.pid))

        direct_mcp_command = (sys.executable, str(MCP_STDIO_FIXTURE))
        mediated_mcp_command = (
            str(args.pipelock_bin.resolve()),
            "mcp",
            "proxy",
            "--config",
            str(args.config.resolve()),
            "--",
            *direct_mcp_command,
        )
        direct_mcp_values = asyncio.run(
            _measure_mcp_command(direct_mcp_command, iterations=args.mcp_iterations)
        )
        mediated_mcp_values = asyncio.run(
            _measure_mcp_command(mediated_mcp_command, iterations=args.mcp_iterations)
        )

    http_direct_stats = _latency_stats(direct_http_values)
    http_mediated_stats = _latency_stats(mediated_http_values)
    ws_direct_stats = _latency_stats(direct_ws_values)
    ws_mediated_stats = _latency_stats(mediated_ws_values)
    mcp_direct_stats = _latency_stats(direct_mcp_values)
    mcp_mediated_stats = _latency_stats(mediated_mcp_values)
    result = {
        "schema_version": 1,
        "issue": 730,
        "pipelock_revision": "f7d1816f1a5ad63d501b0c48f36066f836f59022",
        "environment": _environment(args.environment_label),
        "measurement_scope": {
            "hosted_runner_is_vps_evidence": False,
            "notes": (
                "The same harness is intended for ordinary VPS execution. A GitHub-hosted run is "
                "reference evidence only and must not be relabeled as VPS evidence."
            ),
        },
        "latency": {
            "http": {
                "direct": http_direct_stats,
                "mediated": http_mediated_stats,
                "overhead": _overhead(http_direct_stats, http_mediated_stats),
            },
            "websocket_roundtrip": {
                "direct": ws_direct_stats,
                "mediated": ws_mediated_stats,
                "overhead": _overhead(ws_direct_stats, ws_mediated_stats),
            },
            "mcp_stdio_call_total": {
                "direct": mcp_direct_stats,
                "mediated": mcp_mediated_stats,
                "overhead": _overhead(mcp_direct_stats, mcp_mediated_stats),
                "includes_process_startup_and_handshake_per_call": True,
            },
        },
        "operability": {
            "pipelock_run_startup_ms": startup_ms,
            "pipelock_cpu_seconds_during_http_ws_and_classification": cpu_after - cpu_before,
            "pipelock_rss_start_bytes": rss_samples[0],
            "pipelock_rss_peak_observed_bytes": max(rss_samples),
            "pipelock_rss_end_bytes": rss_samples[-1],
            "pipelock_home_and_stdout_startup_bytes": disk_before,
            "pipelock_home_and_stdout_growth_bytes": disk_after - disk_before,
            "pipelock_home_and_stdout_end_bytes": disk_after,
        },
        "classification": classification,
    }
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _assert_http_ok(body: bytes) -> None:
    if HTTP_SENTINEL not in body:
        raise AssertionError("unexpected HTTP benchmark response")


if __name__ == "__main__":
    raise SystemExit(_main())
