#!/usr/bin/env python3
"""Summarize an #862 ordinary-VPS storage evidence bundle.

The summarizer is deliberately offline: it reads only the capture files already
produced by ``run_issue862_storage_vps_capture.sh`` and emits deterministic JSON
and Markdown summaries. It does not turn GitHub-runner observations into VPS
evidence and it does not infer missing measurements.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path
from typing import Any

_BACKENDS = ("rustfs", "garage", "seaweedfs")
_BYTE_UNITS = {
    "B": 1,
    "kB": 1000,
    "KB": 1000,
    "KiB": 1024,
    "MB": 1000**2,
    "MiB": 1024**2,
    "GB": 1000**3,
    "GiB": 1024**3,
}


def _read_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected JSON object: {path}")
    return raw


def _parse_bytes(value: str) -> int:
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)\s*", value)
    if match is None:
        raise ValueError(f"unsupported byte value: {value!r}")
    amount = float(match.group(1))
    unit = match.group(2)
    multiplier = _BYTE_UNITS.get(unit)
    if multiplier is None:
        raise ValueError(f"unsupported byte unit: {unit}")
    return round(amount * multiplier)


def _parse_percent(value: str) -> float:
    return float(value.strip().removesuffix("%"))


def _load_stats_lines(path: Path) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        if not isinstance(raw, dict):
            raise ValueError(f"expected Docker stats object in {path}")
        samples.append(raw)
    if not samples:
        raise ValueError(f"no Docker stats samples in {path}")
    return samples


def _docker_sample(raw: dict[str, Any]) -> dict[str, float | int | None]:
    memory_value = raw.get("MemUsage")
    cpu_value = raw.get("CPUPerc")
    pids_value = raw.get("PIDs")
    if not isinstance(memory_value, str) or "/" not in memory_value:
        raise ValueError("Docker stats sample is missing MemUsage")
    if not isinstance(cpu_value, str):
        raise ValueError("Docker stats sample is missing CPUPerc")
    memory_used = _parse_bytes(memory_value.split("/", 1)[0].strip())
    pids = int(pids_value) if pids_value not in {None, ""} else None
    return {
        "cpu_percent": _parse_percent(cpu_value),
        "memory_used_bytes": memory_used,
        "pids": pids,
    }


def _disk_usage_bytes(path: Path) -> int:
    token = path.read_text(encoding="utf-8").strip().split(maxsplit=1)[0]
    return int(token)


def _summarize_backend(root: Path, backend: str) -> dict[str, Any]:
    workload = _read_json(root / f"{backend}-workload.json")
    idle_raw = _read_json(root / f"{backend}-idle-docker-stats.json")
    active_raw = _load_stats_lines(root / f"{backend}-active-docker-stats.jsonl")
    idle = _docker_sample(idle_raw)
    active = [_docker_sample(sample) for sample in active_raw]
    cpu = [float(sample["cpu_percent"]) for sample in active]
    memory = [int(sample["memory_used_bytes"]) for sample in active]
    pids = [int(sample["pids"]) for sample in active if sample["pids"] is not None]

    timings = workload.get("timings_seconds")
    if not isinstance(timings, dict):
        raise ValueError(f"missing workload timings for {backend}")

    return {
        "workload_status": "pass" if workload.get("operations") else workload.get("status"),
        "timings_seconds": timings,
        "idle": idle,
        "active_samples": len(active),
        "active_cpu_percent": {
            "median": statistics.median(cpu),
            "max": max(cpu),
        },
        "active_memory_used_bytes": {
            "median": round(statistics.median(memory)),
            "max": max(memory),
        },
        "active_pids": {
            "median": statistics.median(pids) if pids else None,
            "max": max(pids) if pids else None,
        },
        "post_cleanup_data_root_bytes": _disk_usage_bytes(root / f"{backend}-disk-usage.txt"),
    }


def _parse_time_file(path: Path) -> dict[str, int | str | None]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if ": " not in line:
            continue
        key, value = line.strip().split(": ", 1)
        values[key] = value
    max_rss = values.get("Maximum resident set size (kbytes)")
    return {
        "elapsed_wall_clock": values.get("Elapsed (wall clock) time (h:mm:ss or m:ss)"),
        "maximum_resident_set_bytes": int(max_rss) * 1024 if max_rss is not None else None,
    }


def _summarize_local(root: Path) -> dict[str, Any]:
    workload = _read_json(root / "local-filesystem-workload.json")
    timings = workload.get("timings_seconds")
    if not isinstance(timings, dict):
        raise ValueError("local filesystem workload is missing timings")
    return {
        "workload_status": workload.get("status"),
        "timings_seconds": timings,
        "process_resources": _parse_time_file(root / "local-filesystem-time.txt"),
        "payload_disk_bytes_before_cleanup": workload.get("disk_bytes_before_cleanup"),
        "post_cleanup_root_bytes": _disk_usage_bytes(root / "local-filesystem-disk-usage.txt"),
        "resident_storage_daemon": False,
    }


def build_summary(root: Path) -> dict[str, Any]:
    manifest = _read_json(root / "storage-vps-evidence-manifest.json")
    if manifest.get("issue") != 862 or manifest.get("evidence_class") != "ordinary-vps-reference":
        raise ValueError("capture manifest is not #862 ordinary-VPS evidence")

    return {
        "schema_version": 1,
        "issue": 862,
        "evidence_class": "ordinary-vps-reference-summary",
        "source_manifest": manifest,
        "local_filesystem": _summarize_local(root),
        "object_store_backends": {
            backend: _summarize_backend(root, backend) for backend in _BACKENDS
        },
        "interpretation_guardrails": [
            "container CPU/memory describe the resident object-store service only",
            "local filesystem process RSS is not directly equivalent to resident daemon memory",
            (
                "post-cleanup data-root bytes are operational residual footprint, "
                "not storage amplification"
            ),
            (
                "classification decisions must retain raw evidence and must not infer "
                "missing measurements"
            ),
        ],
    }


def _format_mib(value: int | float | None) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) / (1024 * 1024):.1f} MiB"


def render_markdown(summary: dict[str, Any]) -> str:
    local = summary["local_filesystem"]
    backends = summary["object_store_backends"]
    lines = [
        "# Issue #862 VPS storage summary",
        "",
        (
            "| Backend | Peak active RAM | Median active RAM | Peak CPU sample | "
            "Residual data-root | Concurrent round-trip |"
        ),
        "|---|---:|---:|---:|---:|---:|",
        (
            "| local filesystem | "
            f"{_format_mib(local['process_resources']['maximum_resident_set_bytes'])} | "
            "n/a | n/a | "
            f"{_format_mib(local['post_cleanup_root_bytes'])} | "
            f"{float(local['timings_seconds']['concurrent_round_trip_seconds']):.4f}s |"
        ),
    ]
    for backend in _BACKENDS:
        item = backends[backend]
        lines.append(
            f"| {backend} | "
            f"{_format_mib(item['active_memory_used_bytes']['max'])} | "
            f"{_format_mib(item['active_memory_used_bytes']['median'])} | "
            f"{float(item['active_cpu_percent']['max']):.2f}% | "
            f"{_format_mib(item['post_cleanup_data_root_bytes'])} | "
            f"{float(item['timings_seconds']['concurrent_round_trip_seconds']):.4f}s |"
        )
    lines.extend(
        [
            "",
            "The table is a compact view of the raw capture, not a replacement for it. "
            "Local filesystem process RSS and container daemon memory are different "
            "resource shapes, and residual data-root size is not a storage-amplification "
            "measurement.",
            "",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture_dir", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = args.capture_dir.resolve()
    summary = build_summary(root)
    (root / "storage-vps-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "storage-vps-summary.md").write_text(render_markdown(summary), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
