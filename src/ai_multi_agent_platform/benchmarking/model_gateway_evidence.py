"""Secret-safe evidence manifest helpers for model-gateway evaluations."""

from __future__ import annotations

import os
import platform
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .model_gateway_evaluation import ModelGatewayBenchmarkReport


@dataclass(frozen=True, slots=True)
class ModelGatewayTargetEvidence:
    """Reproducibility metadata for one direct or gateway benchmark target.

    Deliberately excludes URLs, headers and credentials. ``native_model`` records the
    configured request-side model identifier only so an evidence artifact can prove
    which path was exercised without changing canonical platform model identity.
    """

    component: str
    component_version: str
    component_revision: str
    native_model: str
    deployment_label: str

    def __post_init__(self) -> None:
        for field_name, value in (
            ("component", self.component),
            ("component_version", self.component_version),
            ("component_revision", self.component_revision),
            ("native_model", self.native_model),
            ("deployment_label", self.deployment_label),
        ):
            if not value.strip():
                raise ValueError(f"{field_name} must not be empty")


def build_model_gateway_evidence(
    report: ModelGatewayBenchmarkReport,
    *,
    target_evidence: Mapping[str, ModelGatewayTargetEvidence],
) -> dict[str, Any]:
    """Attach host and target metadata required to reproduce a gateway benchmark.

    Evidence metadata must exist for every target in the report and for no unknown
    targets. The fixed dataclass schema prevents accidental inclusion of API keys,
    authorization headers or endpoint URLs in the machine-readable artifact.
    """

    expected_targets = set(report.targets)
    supplied_targets = set(target_evidence)
    if supplied_targets != expected_targets:
        missing = sorted(expected_targets - supplied_targets)
        unknown = sorted(supplied_targets - expected_targets)
        details: list[str] = []
        if missing:
            details.append(f"missing targets: {', '.join(missing)}")
        if unknown:
            details.append(f"unknown targets: {', '.join(unknown)}")
        raise ValueError("target evidence must match benchmark targets (" + "; ".join(details) + ")")

    payload = report.to_dict()
    payload["environment"] = _environment_metadata()
    payload["target_evidence"] = {
        name: asdict(target_evidence[name]) for name in sorted(target_evidence)
    }
    return payload


def _environment_metadata() -> dict[str, object]:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "cpu_model": _cpu_model(),
        "memory_total_bytes": _memory_total_bytes(),
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "python_major_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
    }


def _cpu_model() -> str | None:
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        try:
            for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
                key, separator, value = line.partition(":")
                if separator and key.strip().casefold() in {"model name", "hardware"}:
                    normalized = value.strip()
                    if normalized:
                        return normalized
        except OSError:
            pass
    processor = platform.processor().strip()
    return processor or None


def _memory_total_bytes() -> int | None:
    meminfo = Path("/proc/meminfo")
    if not meminfo.is_file():
        return None
    try:
        for line in meminfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.startswith("MemTotal:"):
                continue
            fields = line.split()
            if len(fields) >= 2:
                return int(fields[1]) * 1024
    except (OSError, ValueError):
        return None
    return None
